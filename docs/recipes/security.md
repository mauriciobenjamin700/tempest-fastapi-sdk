# Segurança

Primitivos defensivos: rate-limit por falha (login/OTP), tokens opacos single-use, serviço de arquivos estáticos endurecido com headers de segurança, helpers de cookie HttpOnly/Secure/SameSite, e resolvedor de IP do cliente atrás de proxies confiáveis.

## Throttling de força bruta

`AttemptThrottle` conta tentativas falhas por chave (tipicamente `<endpoint>:<identificador>` — e-mail de login, alvo de reset de senha, IP, etc.). Quando o limite é cruzado, `raise_if_blocked` levanta `TooManyRequestsException` direto; ou você lê `status`/`hit` e decide o que fazer.

O construtor recebe um `backend` (qualquer objeto que case com o `Protocol` `ThrottleBackend` — `redis.asyncio.Redis` funciona out-of-the-box) + `max_attempts` + `window_seconds`. Sem backend "in-memory" bundled — use o cliente Redis do `AsyncRedisManager` ou um fake nos testes.

```python
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine
from tempest_fastapi_sdk import (
    AttemptThrottle,
    PasswordUtils,
    TooManyRequestsException,
    UnauthorizedException,
)
from tempest_fastapi_sdk.cache import AsyncRedisManager

from src.core.settings import settings
from src.db.models import User
from src.db.repositories import UserRepository

# Num serviço, a sessão real vem de `db.get_session_context()`; aqui, do SQLite.
session = AsyncSession(create_async_engine("sqlite+aiosqlite:///:memory:"))

password_utils = PasswordUtils()

users_repo = UserRepository(session)


cache = AsyncRedisManager(settings.REDIS_URL)
throttle: AttemptThrottle


async def on_startup() -> None:
    """Connect Redis and build the throttle at application startup.

    `cache.client` raises RuntimeError until `connect()` runs, so the
    manager must be connected before the throttle is built. Wire this
    to your app lifespan (`FastAPI(lifespan=...)`).
    """
    global throttle
    await (
        cache.connect()
    )  # obrigatório — `cache.client` levanta RuntimeError até conectar
    # `cache.client` is `redis.asyncio.Redis` — matches the ThrottleBackend Protocol
    throttle = AttemptThrottle(
        cache.client,
        max_attempts=5,
        window_seconds=300,  # janela fixa; também é o TTL no primeiro fail
        namespace="login",  # prefixo de key — multiplos throttles podem coexistir
        fail_open=True,  # outage do Redis = libera, não trava todo mundo
    )


async def login(email: str, password: str) -> User:
    key = f"login:{email}"
    await throttle.raise_if_blocked(key)  # 429 se já estourou

    user = await users_repo.get_or_none({"email": email})
    if user is None or not password_utils.verify(password, user.hashed_password):
        await throttle.hit(key)  # +1 failure, aplica TTL
        raise UnauthorizedException(message="Invalid credentials.")

    await throttle.reset(key)  # zera contagem no sucesso
    return user
```

`throttle.status(key)` (peek sem incrementar) e `throttle.hit(key)` (incrementar) retornam `ThrottleStatus` — um `dataclass` frozen com:

- `attempts: int` — falhas registradas na janela atual.
- `blocked: bool` — `True` quando `attempts >= max_attempts`.
- `retry_after_seconds: int` — segundos até a janela resetar (`0` quando não bloqueado).

Use os campos pra montar payloads de erro amigáveis. `raise_if_blocked` já cria a `TooManyRequestsException` com `Retry-After` no header — não precisa lê-los manualmente.

!!! note "Conecte o `AsyncRedisManager` no startup"
    `cache.client` levanta `RuntimeError` enquanto `connect()` não for chamado. Conecte o manager no startup da aplicação (via `FastAPI(lifespan=...)` ou `on_startup`) antes de acessar `cache.client` — e chame `cache.disconnect()` no shutdown.

!!! warning "`AttemptThrottle` não tem backend bundled in-memory"
    Pra testes sem Redis, use um fake/double via [fakeredis](https://github.com/cunla/fakeredis-py) (`pip install fakeredis`) que satisfaz a interface `ThrottleBackend` (métodos `get`, `incr`, `expire`, `ttl`, `delete`) e expõe um Redis funcional 100% em memória.

## Tipos de token JWT (`typ`)

Um serviço com o fluxo de auth bundled emite três JWTs **com o mesmo segredo**: o access token, o refresh token e o token intermediário que liga os dois passos de um login com MFA. Assinatura válida, portanto, não diz nada sobre *qual* deles chegou — e um guard de rota que só lê `sub` aceitaria os três.

O `typ` é o que separa. `UserAuthService` estampa um em tudo que emite:

| Token | `typ` | Onde vale |
| --- | --- | --- |
| Access | `ACCESS_TOKEN_TYPE` (`"access"`) | Qualquer rota autenticada |
| Refresh | `REFRESH_TOKEN_TYPE` (`"refresh"`) | Só `POST /auth/refresh` |
| MFA pendente | `MFA_TOKEN_TYPE` (`"mfa"`) | Só `POST /auth/mfa/verify` |

`make_bearer_token_dependency` e `make_jwt_user_dependency` aceitam **só** `access` por padrão:

```python
from tempest_fastapi_sdk import (
    ACCESS_TOKEN_TYPE,
    REFRESH_TOKEN_TYPE,
    JWTUtils,
    make_bearer_token_dependency,
)

tokens = JWTUtils(secret="…" * 8)

# Padrão: access-only. Um refresh ou um MFA-pendente devolve 401.
require_claims = make_bearer_token_dependency(tokens)

# Rota que de propósito recebe outro tipo (ex.: um endpoint de rotação):
require_refresh = make_bearer_token_dependency(
    tokens,
    accepted_typ=(REFRESH_TOKEN_TYPE,),
)
```

!!! danger "Por que isso importa"
    O `mfa_token` é devolvido pelo `/login` a um cliente que provou **só a senha**. Sem a checagem de tipo ele serve como bearer em qualquer rota autenticada — o segundo fator vira decoração. O mesmo vale pro refresh token: ele tem vida longa de propósito, e aceitá-lo como access anula o motivo de o access token ser curto.

!!! note "Token sem `typ` continua valendo"
    Quem assina JWT direto com `JWTUtils.encode()` não precisa mudar nada: um token sem `typ` é aceito, senão atualizar o SDK derrubaria toda sessão ativa. Os dois marcadores antigos que o SDK já estampava — `refresh: True` e `purpose: "mfa_pending"` — são reconhecidos e **rejeitados** como access. Use `token_type_allowed()` se precisar da mesma decisão fora de uma dependency.

## Tokens opacos single-use

`generate_opaque_token()` produz `(plaintext, token_hash)` em uma chamada — `plaintext` é uma string URL-safe (default 32 bytes ≈ 43 chars), `token_hash` é o digest SHA-256 hex em lowercase (64 chars). Você guarda **só o hash** no banco; o `plaintext` sai pelo e-mail/SMS uma única vez. Use pra password reset, confirmação de e-mail, API keys, IDs de sessão opacos — qualquer coisa onde o segredo emitido nunca volta a ser inspecionado.

!!! info "Sem pepper, sem HMAC"
    O hash é SHA-256 puro (`hashlib.sha256(plain).hexdigest()`) por design: tokens opacos têm 256 bits de entropia (já fora de alcance de força bruta), então pepper extra não adiciona segurança prática. Pra credenciais com baixa entropia (senha humana), use `PasswordUtils.hash` (bcrypt) — não os helpers desta seção.

```python
from datetime import timedelta
from uuid import UUID

from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine
from tempest_fastapi_sdk import (
    generate_opaque_token,
    hash_opaque_token,
    verify_opaque_token,
)
from tempest_fastapi_sdk.utils import utcnow

from src.db.models import PasswordResetToken
from src.db.repositories import UserTokenRepository

# Num serviço, a sessão real vem de `db.get_session_context()`; aqui, do SQLite.
session = AsyncSession(create_async_engine("sqlite+aiosqlite:///:memory:"))

reset_tokens_repo = UserTokenRepository(session)


async def issue_reset_token(user_id: UUID) -> str:
    plaintext, token_hash = generate_opaque_token()
    await reset_tokens_repo.add(
        PasswordResetToken(
            user_id=user_id,
            token_hash=token_hash,
            expires_at=utcnow() + timedelta(hours=1),
        ),
    )
    return plaintext  # mostre uma vez — never store


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

## CSRF em fluxo com cookie (`CSRFMiddleware`)

Sessão por cookie tem um problema que bearer token não tem: o browser reenvia o
cookie **sozinho**, mesmo numa request disparada por outro site. `SameSite=lax`
barra a maior parte disso, mas não POST de formulário em subdomínio nem cliente
antigo. `CSRFMiddleware` fecha com double-submit:

```python
# src/api/app.py
from fastapi import FastAPI
from tempest_fastapi_sdk import CSRFMiddleware

app: FastAPI = FastAPI()

app.add_middleware(
    CSRFMiddleware,
    exclude_paths=("/api/", "/webhooks/"),
)
```

Em `POST`/`PUT`/`PATCH`/`DELETE` a request precisa carregar **as duas** coisas
com o mesmo valor: o cookie `csrf_token` e o header `X-CSRF-Token`. Faltando ou
divergindo, a resposta é `403` no envelope canônico do SDK. `GET`/`HEAD`/
`OPTIONS` passam sempre. Os defaults estão em `CSRF_COOKIE_NAME` e
`CSRF_HEADER_NAME`; troque com `cookie_name=`/`header_name=`.

!!! info "Por que excluir `/api/`"
    Rota autenticada por `Authorization: Bearer` **não** é vulnerável a CSRF —
    o browser não anexa o header sozinho. Exigir token ali só quebraria cliente
    mobile. Webhook assinado (`WebhookSignatureVerifier`) idem: a autenticação
    já é a assinatura. O match de `exclude_paths` é por prefixo (`startswith`).

Pra emitir o token, monte `make_csrf_token_dependency()` na rota que renderiza a
página — ela **grava o cookie** quando falta e devolve o valor pro template. As
duas metades são necessárias: o double-submit compara cookie e header, então uma
dependency que só devolvesse o valor deixaria o cookie ausente e o `POST`
seguinte — justamente aquele pra que a página foi renderizada — cairia com 403.

```python
# src/api/routers/pages.py
from fastapi import APIRouter, Depends
from tempest_fastapi_sdk import make_csrf_token_dependency

router: APIRouter = APIRouter()
csrf_token = make_csrf_token_dependency()


@router.get("/login")
async def login_page(token: str = Depends(csrf_token)) -> dict[str, str]:
    """Render the login shell carrying the CSRF token."""
    return {"csrf_token": token}
```

!!! info "Esse cookie não é `HttpOnly`, de propósito"
    O cliente precisa **ler** o valor pra ecoar no header `X-CSRF-Token` — é o
    mecanismo inteiro do double-submit. `HttpOnly` quebraria isso. É seguro
    enquanto o cookie carregar só o token CSRF; nunca coloque outra coisa nele.
    Ele sai com `Secure` e `SameSite=Lax` por padrão: use
    `make_csrf_token_dependency(secure=False)` só num dev server em HTTP puro,
    e `samesite="none"` (com `secure=True`) quando o frontend está em outra
    origem.

O cliente reenvia esse valor no header a cada request de escrita:

```javascript
await fetch("/auth/login", {
  method: "POST",
  credentials: "include",
  headers: { "X-CSRF-Token": token, "Content-Type": "application/json" },
  body: JSON.stringify({ email, password }),
});
```

`generate_csrf_token(n_bytes=32)` está exposto pra quem quer emitir o token fora
de uma dependency (um handler SSR, por exemplo).

!!! warning "CSRF só importa se a credencial viaja automática"
    Use quando a sessão está em cookie — `AUTH_TOKEN_DELIVERY=cookie`/`both`
    ([receita de auth](auth-flow.md)) ou sessão server-side
    ([receita de sessões](sessions.md)). Serviço 100% bearer não precisa do
    middleware.

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
from fastapi import APIRouter, Request

from tempest_fastapi_sdk import AttemptThrottle, get_client_ip

from src.schemas import LoginIn, LoginOut

# Built in the app lifespan: `cache.client` raises until `connect()` runs.
throttle: AttemptThrottle | None = None

router = APIRouter()


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

## Recap

- `AttemptThrottle` conta falha por chave (`login:<email>`, `reset:<ip>`) e
  bloqueia a chave, não o serviço — força bruta fica caro sem punir quem
  digitou errado uma vez.
- O claim `typ` separa os três JWTs que compartilham o mesmo segredo: access,
  refresh e o intermediário do MFA. Sem ele, um refresh passaria como access.
- `generate_opaque_token()` devolve `(plaintext, token_hash)` numa chamada: o
  plaintext vai no e-mail, o hash vai no banco, e vazamento de tabela não vira
  login.
- `HardenedStaticFiles` carimba header de segurança em toda resposta e recusa
  caminho que escapa da base — defesa em profundidade contra travessia.
- `CSRFMiddleware` cobre o que bearer token não precisa e cookie precisa: o
  browser reenviando credencial em request que o seu serviço não iniciou.
- `set_cookie` / `clear_cookie` já vêm com `HttpOnly`, `Secure` e `SameSite`
  seguros, e `get_client_ip` resolve o IP real atrás de proxy.
