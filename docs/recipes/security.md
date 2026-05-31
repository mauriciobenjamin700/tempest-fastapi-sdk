# Segurança {#security}

Primitivos defensivos: rate-limit por falha (login/OTP), tokens opacos de uso único, entrega de arquivos estáticos endurecida com cabeçalhos de segurança e o resolvedor de IP do cliente resistente a spoofing.

## Throttling de força bruta {#brute-force-throttling}


`AttemptThrottle` conta tentativas que falharam por chave (tipicamente `<endpoint>:<identifier>` — email de login, alvo de redefinição de senha, etc.) e ou libera uma tentativa, bloqueia o chamador por um cooldown, ou sinaliza "recue" assim que o limite é ultrapassado. Dois backends são fornecidos: `MemoryThrottleBackend` (padrão — local ao processo, perfeito para testes e serviços de processo único) e `RedisThrottleBackend` (deployments multi-processo / multi-host).

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

`check()` retorna um enum `ThrottleStatus` (`ALLOWED` / `LOCKED`); inspecionar `.attempts_left` e `.retry_after_seconds` no resultado permite expor payloads de erro amigáveis. Combine com `TooManyRequestsException` para que o handler de exceções do SDK emita o envelope canônico `{detail, code, details}` com HTTP 429 e um cabeçalho `Retry-After`.


## Tokens opacos {#opaque-tokens}


`generate_opaque_token` produz um token URL-safe de alta entropia (padrão 32 bytes / 256 bits via `secrets.token_urlsafe`); `hash_opaque_token` o armazena como um digest HMAC-SHA-256, de modo que uma linha de banco vazada é inútil por si só; `verify_opaque_token` realiza comparação em tempo constante. Use-os para links de redefinição de senha, confirmação de email, API keys, IDs de sessão opacos — qualquer coisa em que o segredo emitido nunca seja inspecionado pelo destinatário.

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

`secret=` é opcional — passar o mesmo pepper entre `hash_*` / `verify_*` adiciona um segredo de escopo do serviço, de modo que a coluna de digest sozinha não possa sofrer força bruta. Padrões: 32 bytes de entropia, HMAC-SHA-256, comparação em tempo constante. Sobrescreva `nbytes=` para chaves mais longas (API keys / refresh tokens).


## Arquivos estáticos endurecidos + helpers de cookie {#hardened-static-files-cookie-helpers}


`HardenedStaticFiles` estende o `StaticFiles` do Starlette com três padrões de nível de produção: resolve o caminho servido contra uma base livre de symlinks, recusa qualquer caminho que escape dessa base (defesa em profundidade contra path-traversal) e anexa um conjunto configurável de cabeçalhos de segurança (`DEFAULT_STATIC_SECURITY_HEADERS` — `X-Content-Type-Options: nosniff`, `Referrer-Policy: same-origin`, `Cross-Origin-Resource-Policy: same-site`).

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

Para fluxos de sessão baseados em cookie, `set_cookie` / `clear_cookie` escrevem cabeçalhos que já incluem a combinação segura (`HttpOnly`, `Secure`, `SameSite=Lax`), de modo que o chamador só precisa escolher os trechos dos quais deseja se desviar. `SameSite` é um `BaseStrEnum` (`SameSite.LAX` / `STRICT` / `NONE`) — usar `SameSite.NONE` força `Secure=True` para respeitar o requisito do navegador.

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


## Extração de IP do cliente {#client-ip-extraction}


`get_client_ip(request)` e `get_client_ip_from_scope(scope)` retornam o IP real do cliente por trás de uma cadeia arbitrária de proxies. Eles inspecionam `Forwarded` (RFC 7239) primeiro, recorrem a `X-Forwarded-For` respeitando uma allowlist explícita de `trusted_proxies` e, por fim, usam o endereço de socket bruto — nunca confiando ingenuamente em um cabeçalho de entrada.

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

Use `get_client_ip_from_scope(scope)` a partir de middleware ou handlers de websocket onde apenas o scope ASGI está ao alcance. Ambos os helpers normalizam os colchetes de IPv6 e se recusam a retornar endereços privados quando `accept_private=False` é passado — útil quando apenas tráfego público deve popular logs de auditoria.
