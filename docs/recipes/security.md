# Segurança

Primitivos defensivos: rate-limit por falha (login/OTP), tokens opacos de uso único, serviço de arquivos estáticos endurecido com headers de segurança, e o resolvedor de IP do cliente resistente a spoofing.

## Throttling de força bruta


`AttemptThrottle` conta tentativas falhas por chave (tipicamente `<endpoint>:<identificador>` — e-mail de login, alvo de reset de senha, etc.) e ou libera uma tentativa, trava quem chama por um cooldown, ou sinaliza "recue" quando o limite é cruzado. Dois backends vêm: `MemoryThrottleBackend` (default — local ao processo, perfeito para testes e serviços de processo único) e `RedisThrottleBackend` (deploys multi-processo / multi-host).

```python
from tempest_fastapi_sdk import AttemptThrottle, ThrottleStatus, TooManyRequestsException
from tempest_fastapi_sdk.utils.throttle import RedisThrottleBackend

throttle = AttemptThrottle(
    backend=RedisThrottleBackend(redis_manager=cache),
    max_attempts=5,
    window_seconds=300,           # rolling window
    lock_seconds=900,             # cooldown applied when threshold trips
)


async def login(email: str, password: str) -> User:
    status = await throttle.check(f"login:{email}")
    if status is ThrottleStatus.LOCKED:
        raise TooManyRequestsException(message="Too many attempts; try again later.")

    user = await users_repo.get_by_email(email)
    if not password_utils.verify(password, user.password_hash):
        await throttle.record_failure(f"login:{email}")
        raise UnauthorizedException(message="Invalid credentials.")

    await throttle.reset(f"login:{email}")
    return user
```

`check()` retorna um enum `ThrottleStatus` (`ALLOWED` / `LOCKED`); inspecionar `.attempts_left` e `.retry_after_seconds` no resultado permite exibir payloads de erro amigáveis. Combine com `TooManyRequestsException` para que o exception handler do SDK emita o envelope canônico `{detail, code, details}` com HTTP 429 e um header `Retry-After`.


## Tokens opacos


`generate_opaque_token` produz um token URL-safe de alta entropia (default 32 bytes / 256 bits via `secrets.token_urlsafe`); `hash_opaque_token` o armazena como um digest HMAC-SHA-256 para que uma linha de banco vazada seja inútil por si só; `verify_opaque_token` faz comparação em tempo constante. Use-os para links de reset de senha, confirmação de e-mail, API keys, IDs de sessão opacos — qualquer coisa onde o segredo emitido nunca é inspecionado pelo destinatário.

```python
from tempest_fastapi_sdk import (
    generate_opaque_token,
    hash_opaque_token,
    verify_opaque_token,
)
from src.core.settings import settings


def issue_reset_token(user_id: UUID) -> str:
    plain = generate_opaque_token()
    digest = hash_opaque_token(plain, secret=settings.OPAQUE_TOKEN_PEPPER)
    await reset_tokens_repo.add(
        PasswordResetToken(user_id=user_id, digest=digest, expires_at=...),
    )
    return plain  # send this in the email — never store it


async def consume_reset_token(plain: str, user_id: UUID) -> bool:
    record = await reset_tokens_repo.get_or_none({"user_id": user_id})
    if record is None or record.is_expired:
        return False
    return verify_opaque_token(
        plain,
        record.digest,
        secret=settings.OPAQUE_TOKEN_PEPPER,
    )
```

`secret=` é opcional — passar o mesmo pepper em `hash_*` / `verify_*` adiciona um segredo a nível de serviço para que a coluna de digest sozinha não possa ser quebrada por força bruta. Defaults: 32 bytes de entropia, HMAC-SHA-256, comparação em tempo constante. Sobrescreva `nbytes=` para chaves mais longas (API keys / refresh tokens).


## Arquivos estáticos endurecidos + helpers de cookie


`HardenedStaticFiles` estende o `StaticFiles` do Starlette com três defaults de nível de produção: resolve o caminho servido contra uma base livre de symlinks, recusa qualquer caminho que escape dessa base (defesa em profundidade contra path-traversal), e anexa um conjunto configurável de headers de segurança (`DEFAULT_STATIC_SECURITY_HEADERS` — `X-Content-Type-Options: nosniff`, `Referrer-Policy: same-origin`, `Cross-Origin-Resource-Policy: same-site`).

```python
from fastapi import FastAPI

from tempest_fastapi_sdk import DEFAULT_STATIC_SECURITY_HEADERS, HardenedStaticFiles

app = FastAPI()
app.mount(
    "/static",
    HardenedStaticFiles(
        directory="public/",
        # Override or extend the defaults — merging is the caller's job.
        security_headers={
            **DEFAULT_STATIC_SECURITY_HEADERS,
            "Cache-Control": "public, max-age=86400, immutable",
        },
    ),
    name="static",
)
```

Para fluxos de sessão baseados em cookie, `set_cookie` / `clear_cookie` escrevem headers que já incluem o combo seguro (`HttpOnly`, `Secure`, `SameSite=Lax`) para que quem chama só escolha os bits dos quais quer desviar. `SameSite` é um `BaseStrEnum` (`SameSite.LAX` / `STRICT` / `NONE`) — usar `SameSite.NONE` força `Secure=True` para honrar a exigência do navegador.

```python
from fastapi import Response

from tempest_fastapi_sdk import SameSite, clear_cookie, set_cookie


def login(response: Response, token: str) -> None:
    set_cookie(
        response,
        key="session",
        value=token,
        max_age=3600,
        same_site=SameSite.LAX,         # default
        # secure=True,                  # auto-enabled for SameSite.NONE
        # http_only=True,               # default
        path="/",
    )


def logout(response: Response) -> None:
    clear_cookie(response, key="session", path="/")
```


## Extração do IP do cliente


`get_client_ip(request)` e `get_client_ip_from_scope(scope)` retornam o IP real do cliente por trás de uma cadeia arbitrária de proxies. Eles inspecionam `Forwarded` (RFC 7239) primeiro, caem para `X-Forwarded-For` honrando uma allowlist explícita `trusted_proxies`, e por fim usam o endereço de socket cru — nunca confiando ingenuamente em um header de entrada.

```python
from fastapi import Request

from tempest_fastapi_sdk import get_client_ip


@router.post("/login")
async def login(request: Request, payload: LoginIn) -> LoginOut:
    ip = get_client_ip(
        request,
        trusted_proxies={"10.0.0.0/8", "192.168.0.0/16"},
    )
    await throttle.check(f"login:{ip}")
    ...
```

Use `get_client_ip_from_scope(scope)` de middleware ou handlers de websocket onde só o scope ASGI está ao alcance. Ambos os helpers normalizam colchetes de IPv6 e se recusam a retornar endereços privados quando `accept_private=False` é passado — útil quando só tráfego público deveria popular logs de auditoria.
