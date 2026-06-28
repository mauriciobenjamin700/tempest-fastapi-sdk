# Segurança

Primitivos defensivos: rate-limit por falha (login/OTP), tokens opacos single-use, serviço de arquivos estáticos endurecido com headers de segurança, helpers de cookie HttpOnly/Secure/SameSite, e resolvedor de IP do cliente atrás de proxies confiáveis.

## Throttling de força bruta

`AttemptThrottle` conta tentativas falhas por chave (tipicamente `<endpoint>:<identificador>` — e-mail de login, alvo de reset de senha, IP, etc.). Quando o limite é cruzado, `raise_if_blocked` levanta `TooManyRequestsException` direto; ou você lê `status`/`hit` e decide o que fazer.

O construtor recebe um `backend` (qualquer objeto que case com o `Protocol` `ThrottleBackend` — `redis.asyncio.Redis` funciona out-of-the-box) + `max_attempts` + `window_seconds`. Sem backend "in-memory" bundled — use o cliente Redis do `AsyncRedisManager` ou um fake nos testes.

```python
from tempest_fastapi_sdk import (
    AttemptThrottle,
    TooManyRequestsException,
    UnauthorizedException,
)
from tempest_fastapi_sdk.cache import AsyncRedisManager

from src.core.settings import settings

cache = AsyncRedisManager(settings.REDIS_URL)
# `cache.client` is `redis.asyncio.Redis` — matches the ThrottleBackend Protocol
throttle = AttemptThrottle(
    cache.client,
    max_attempts=5,
    window_seconds=300,         # janela fixa; também é o TTL no primeiro fail
    namespace="login",          # prefixo de key — multiplos throttles podem coexistir
    fail_open=True,             # outage do Redis = libera, não trava todo mundo
)


async def login(email: str, password: str) -> User:
    key = f"login:{email}"
    await throttle.raise_if_blocked(key)            # 429 se já estourou

    user = await users_repo.get_or_none({"email": email})
    if user is None or not password_utils.verify(password, user.hashed_password):
        await throttle.hit(key)                     # +1 failure, aplica TTL
        raise UnauthorizedException(message="Invalid credentials.")

    await throttle.reset(key)                       # zera contagem no sucesso
    return user
```

`throttle.status(key)` (peek sem incrementar) e `throttle.hit(key)` (incrementar) retornam `ThrottleStatus` — um `dataclass` frozen com:

- `attempts: int` — falhas registradas na janela atual.
- `blocked: bool` — `True` quando `attempts >= max_attempts`.
- `retry_after_seconds: int` — segundos até a janela resetar (`0` quando não bloqueado).

Use os campos pra montar payloads de erro amigáveis. `raise_if_blocked` já cria a `TooManyRequestsException` com `Retry-After` no header — não precisa lê-los manualmente.

!!! warning "`AttemptThrottle` não tem backend bundled in-memory"
    Pra testes sem Redis, use um fake/double via [fakeredis](https://github.com/cunla/fakeredis-py) (`pip install fakeredis`) que satisfaz a interface `ThrottleBackend` (métodos `get`, `incr`, `expire`, `ttl`, `delete`) e expõe um Redis funcional 100% em memória.

## Tokens opacos single-use

`generate_opaque_token()` produz `(plaintext, token_hash)` em uma chamada — `plaintext` é uma string URL-safe (default 32 bytes ≈ 43 chars), `token_hash` é o digest SHA-256 hex em lowercase (64 chars). Você guarda **só o hash** no banco; o `plaintext` sai pelo e-mail/SMS uma única vez. Use pra password reset, confirmação de e-mail, API keys, IDs de sessão opacos — qualquer coisa onde o segredo emitido nunca volta a ser inspecionado.

!!! info "Sem pepper, sem HMAC"
    O hash é SHA-256 puro (`hashlib.sha256(plain).hexdigest()`) por design: tokens opacos têm 256 bits de entropia (já fora de alcance de força bruta), então pepper extra não adiciona segurança prática. Pra credenciais com baixa entropia (senha humana), use `PasswordUtils.hash` (bcrypt) — não os helpers desta seção.

```python
from uuid import UUID

from tempest_fastapi_sdk import (
    generate_opaque_token,
    hash_opaque_token,
    verify_opaque_token,
)


async def issue_reset_token(user_id: UUID) -> str:
    plaintext, token_hash = generate_opaque_token()
    await reset_tokens_repo.add(
        PasswordResetToken(
            user_id=user_id,
            token_hash=token_hash,
            expires_at=utcnow() + timedelta(hours=1),
        ),
    )
    return plaintext   # mostre uma vez — never store


async def consume_reset_token(plaintext: str, user_id: UUID) -> bool:
    record = await reset_tokens_repo.get_or_none(
        {"user_id": user_id, "used_at": None},
    )
    if record is None or record.expires_at < utcnow():
        return False
    if not verify_opaque_token(plaintext, record.token_hash):
        return False
    record.used_at = utcnow()
    await reset_tokens_repo.update(record)
    return True
```

!!! tip "Pra fluxo completo, use `UserAuthService`"
    Signup + activation + login + password reset prontos com tokens opacos one-shot, TTL, anti-enumeração e e-mail Jinja2 bundled estão em [`auth-flow.md`](auth-flow.md). Use estes helpers diretos só quando você precisa de um fluxo customizado fora do `UserAuthService`.

## Arquivos estáticos endurecidos

`HardenedStaticFiles` estende `starlette.staticfiles.StaticFiles` carimbando headers anti-XSS em toda resposta — defesa em profundidade contra um arquivo malicioso que tenha caído no diretório (bypass de upload-validation, ação manual de operador) sendo servido como uma primitiva de stored-XSS.

`DEFAULT_STATIC_SECURITY_HEADERS` aplica:

- `X-Content-Type-Options: nosniff` — navegador não chuta o MIME por bytes.
- `Content-Security-Policy: default-src 'none'; sandbox` — script embutido não executa; sandbox bloqueia formulários e navegação top-level.
- `Cross-Origin-Resource-Policy: same-site` — limita leitura cross-origin.

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

## Cookies de sessão

`set_cookie` / `clear_cookie` escrevem cookies com defaults seguros (`HttpOnly=True`, `Secure=True`, `samesite="lax"`). `SameSite` é um **type alias** `Literal["lax", "strict", "none"]` — passe a string literal, não um enum.

```python
from fastapi import Response

from tempest_fastapi_sdk import clear_cookie, set_cookie


def login(response: Response, token: str) -> None:
    set_cookie(
        response,
        "session",                 # name (posicional)
        token,                     # value (posicional)
        max_age=3600,
        samesite="lax",            # "lax" (default), "strict" ou "none"
        # secure=True,             # default — você precisa setar False só pra HTTP local
        # http_only=True,          # default
        path="/",
    )


def logout(response: Response) -> None:
    clear_cookie(response, "session", path="/")
```

!!! warning "`SameSite=\"none\"` exige `Secure=True`"
    Quando o navegador vê `SameSite=None` sem `Secure`, ele rejeita o cookie. O SDK **não** força `secure=True` automaticamente — passe explicitamente `samesite="none", secure=True` em cenários cross-site (iframe widget, OAuth callback de domínio diferente).

## Extração do IP do cliente

`get_client_ip(request)` e `get_client_ip_from_scope(scope)` retornam o IP real do cliente atrás de proxies. Por design simples: a função aceita **um** nome de header confiável (`trusted_header=`) que sua infraestrutura sabe que só o edge proxy pode setar (típico: `"x-real-ip"` num Nginx, `"x-forwarded-for"` num ALB com cabeçalhos sanitizados). Sem `trusted_header=`, a função usa o peer address direto.

```python
from fastapi import Request

from tempest_fastapi_sdk import get_client_ip


@router.post("/login")
async def login(request: Request, payload: LoginIn) -> LoginOut:
    # Atrás de Nginx que sobrescreve X-Real-IP com o peer real:
    ip = get_client_ip(request, trusted_header="x-real-ip")
    await throttle.raise_if_blocked(f"login:{ip}")
    ...
```

!!! warning "Configure no edge proxy, não no Python"
    A defesa contra spoofing de `X-Forwarded-For` precisa acontecer no proxy (Nginx, ALB, CloudFront) — o proxy **sobrescreve** o header com o peer real antes do request bater no FastAPI. O SDK só lê o header que você confia. Se você expõe a app direto na internet, **não** passe `trusted_header=` — use o peer address.

Use `get_client_ip_from_scope(scope, trusted_header=...)` em middleware ou handlers de WebSocket onde só o scope ASGI está ao alcance.
