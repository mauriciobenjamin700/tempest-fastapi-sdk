# Sessões server-side

Desde v0.34.0 o SDK fornece o ciclo completo de autenticação baseada em **sessões server-side** — alternativa ao fluxo JWT do `UserAuthService`. O cookie carrega apenas um id opaco; estado real (user_id, TTL, metadata do cliente, payload da app) vive num **`SessionStore`** plugável (Memory pra dev/testes, Redis pra produção).

## JWT vs sessões server-side

| Aspecto | JWT (`UserAuthService`) | Sessions (`SessionAuth`) |
|---|---|---|
| Estado | stateless (no cliente) | stateful (no Redis/Memory) |
| Cookie size | ~500 B – 1 KB (JWT) | 64 B (opaque id) |
| Revogação | espera token expirar (~1h típico) | **instantânea** (delete da row) |
| Logout global | precisa de blocklist ou rotacionar JWT_SECRET | `revoke_all(user_id)` num call |
| CSRF | precisa de header bearer custom | cookie HttpOnly + double-submit token nativo |
| Multi-device UI ("logado em 3 lugares") | sem state → impossível direto | `list_sessions(user_id)` trivial |
| Multi-replica | trivial (verify-only) | exige Redis (ou sticky) |
| Latência por request | nenhuma DB (decode CPU) | 1 hit Redis (~0.5ms LAN) |

**Use sessions quando:** SaaS B2C, painel admin, fluxo SSR (HTMX/Django-like), revogação instantânea é requisito, UI de "dispositivos ativos" é feature.

**Use JWT quando:** APIs públicas consumidas por mobile/SPA, microservices stateless, escala alta sem dependência de Redis.

## Conteúdo da receita

1. **[Setup mínimo](#setup-minimo)** — wire de 4 objetos (`SessionStore`, `SessionAuth`, `SessionMiddleware`, `make_session_router`).
2. **[Endpoints bundled](#endpoints)** — login / logout / me / list / revoke.
3. **[Settings (`SessionSettings`)](#settings)** — flags + defaults.
4. **[Stores](#stores)** — `MemorySessionStore` vs `RedisSessionStore`.
5. **[Como o middleware injeta a sessão](#middleware)** — `request.state.session` + dependency.
6. **[Login sem tabela de usuário](#login-sem-tabela-de-usuario)** — credencial fixa, rota HTML com redirect, sem middleware.
7. **[Segurança](#seguranca)** — anti-fixation rotation, hash-at-rest, anti-enumeração, CSRF.
8. **[Trade-offs e quando NÃO usar](#trade-offs)** — multi-replica, mobile, edge.

---

## Setup mínimo

Quatro objetos compõem o fluxo. Mount uma vez no `app.py`:

```python
# src/api/app.py
from fastapi import FastAPI

from redis.asyncio import Redis

from tempest_fastapi_sdk import (
    AsyncDatabaseManager,
    RedisSessionStore,
    SessionAuth,
    SessionMiddleware,
    SessionSettings,
    make_session_router,
    register_exception_handlers,
)

from src.core.settings import settings
from src.db.models import UserModel

db = AsyncDatabaseManager(settings.DATABASE_URL)
session_settings = SessionSettings()

session_store = RedisSessionStore(
    Redis.from_url(settings.REDIS_URL, decode_responses=True),
    prefix=f"{settings.APP_NAME}:",
)
session_auth = SessionAuth(
    user_model=UserModel,
    store=session_store,
    settings=session_settings,
)


def create_app() -> FastAPI:
    app = FastAPI(title="my-app")
    register_exception_handlers(app)

    # Order matters: middleware ANTES dos routers.
    app.add_middleware(
        SessionMiddleware,
        session_auth=session_auth,
        settings=session_settings,
    )

    app.include_router(
        make_session_router(
            session_auth,
            session_factory=db.session_dependency,
        )
    )
    return app


app = create_app()
```

!!! note "Por que `Redis.from_url` aqui, e não `AsyncRedisManager`?"
    Este client alimenta um **middleware** (`SessionMiddleware`), montado no
    `create_app` (síncrono), antes de qualquer lifespan async rodar.
    `Redis.from_url()` é **lazy** — constrói sem abrir conexão, então serve nesse
    ponto.

    Se o serviço já tem um `AsyncRedisManager`, prefira `cache.client_proxy`
    (v0.256.0) a abrir um client solto: é um handle estável, construível antes
    do `connect()` e válido através de reconexão, e mantém o `disconnect()` e o
    `health_check()` do manager. O que não serve aqui é `cache.client`, que
    levanta `RuntimeError` antes do lifespan. Todos precisam do extra `[cache]`
    (o pacote `redis`).

Pronto. O usuário faz `POST /auth/session/login` com email+senha; o SDK seta o cookie HttpOnly+Secure; toda request subsequente que carrega o cookie tem `request.state.session` populado.

### O que cada objeto faz

1. **`SessionStore`** (`RedisSessionStore` / `MemorySessionStore`) — a camada de persistência. Guarda o estado real da sessão indexado pelo hash SHA-256 do id opaco. É o único objeto que fala com o Redis.
2. **`SessionAuth`** — a camada de lógica. Verifica credenciais contra o `UserModel`, cria (mint), rotaciona e revoga sessões via o `store`. Não sabe nada de HTTP.
3. **`SessionMiddleware`** — a ponte HTTP → sessão. A cada request lê o cookie, resolve via `SessionAuth`/`store` e popula `request.state.session` **antes** de qualquer router rodar. Sem ele, `make_session_dependency()` não encontra sessão nenhuma e responde `401` mesmo com cookie válido — a menos que você passe `session_auth=` para a dependency, que então resolve o cookie sozinha ([sem middleware](#login-sem-tabela-de-usuario)).
4. **`make_session_router`** — expõe os cinco endpoints bundled (`login` / `logout` / `me` / `list` / `{id}`). Recebe o mesmo `session_auth` e uma `session_factory` pra abrir a sessão de DB no login.

!!! warning "Ordem importa: `add_middleware` ANTES de `include_router`"
    O `SessionMiddleware` precisa rodar em toda request pra popular `request.state.session`. Registre-o com `app.add_middleware(...)` **antes** de montar os routers via `app.include_router(...)`. Se inverter, os handlers que dependem de `request.state.session` (ou de `make_session_dependency`) encontram o atributo ausente e quebram. Mantenha o wire na ordem exata do exemplo acima.

---

## Endpoints

Cinco endpoints bundled cobrindo o ciclo todo:

| Método | Path | Body / Output | Comportamento |
|---|---|---|---|
| POST | `/auth/session/login` | `SessionLoginSchema` → `SessionResponseSchema` | Verifica bcrypt. Mint nova sessão. Seta `Set-Cookie: tempest_session=<id>; HttpOnly; Secure; SameSite=Lax`. Se já havia cookie, **rotaciona** (anti-fixation). |
| POST | `/auth/session/logout` | — → `204 No Content` | Revoga a sessão atual + limpa cookie. Idempotente. |
| GET | `/auth/session/me` | — → `Session` | Retorna a sessão atual (`user_id`, timestamps, ip, user_agent, data). `401` quando sem cookie. |
| GET | `/auth/session/list` | — → `list[SessionSummarySchema]` | Lista todas as sessões ativas do usuário (UI "dispositivos ativos"). Marca a atual com `is_current=True`. |
| DELETE | `/auth/session/{id}` | — → `204 No Content` | Revoga uma sessão específica pelo public id (32 chars do hash). Se for a própria, limpa o cookie. |

---

## Settings

Mixe `SessionSettings` na sua `Settings`:

```python
from tempest_fastapi_sdk import BaseAppSettings, SessionSettings


class Settings(SessionSettings, BaseAppSettings):
    pass
```

```bash
# .env
SESSION_TTL_SECONDS=86400              # 24h (default)
SESSION_SLIDING=true                   # refresh expires_at a cada hit (default)
SESSION_COOKIE_NAME=tempest_session
SESSION_COOKIE_DOMAIN=                 # None = exato host
SESSION_COOKIE_PATH=/
SESSION_COOKIE_SECURE=true             # HTTPS only — false só pra dev HTTP
SESSION_COOKIE_HTTPONLY=true           # JavaScript não lê — sempre true
SESSION_COOKIE_SAMESITE=lax            # lax / strict / none
SESSION_ROTATE_ON_LOGIN=true           # anti-fixation
```

`SESSION_COOKIE_SAMESITE` é `Literal["lax", "strict", "none"]` (alias `SessionCookieSameSite`). Valor fora disso — `Strict`, `LAX`, `lax;` — derruba a construção do `Settings()` com `literal_error`, no boot, e não na primeira resposta. Espaço em volta (`lax `) é aparado, como antes.

Para escrever e apagar o cookie, use os dois mappers em vez de repetir sete settings no `set_cookie` e três no `delete_cookie`:

```python
from fastapi import Response

from tempest_fastapi_sdk import BaseAppSettings, SessionSettings


class Settings(SessionSettings, BaseAppSettings):
    pass


settings = Settings()


def start_session(response: Response, plaintext: str) -> None:
    response.set_cookie(value=plaintext, **settings.session_cookie_kwargs())


def end_session(response: Response) -> None:
    response.delete_cookie(**settings.session_cookie_delete_kwargs())
```

`session_cookie_kwargs()` leva o nome do cookie como `key`, então o call site só passa o id. `session_cookie_delete_kwargs()` repete `path`, `domain`, `secure`, `httponly` e `samesite` do mesmo lugar — o browser só apaga o cookie quando o `Set-Cookie` de remoção casa `path` e `domain` com o que o criou. Os dois devolvem `TypedDict` (`SessionCookieKwargs` / `SessionCookieDeleteKwargs`), então o mypy confere o splat contra a assinatura do Starlette. O `make_session_router` usa os mesmos mappers.

!!! danger "`SESSION_COOKIE_SECURE=false` é só pra dev HTTP"
    O default é `true`: o browser só envia o cookie sobre HTTPS. Setar `false` faz o cookie de sessão trafegar em texto claro sobre HTTP — qualquer intermediário na rede captura o id e sequestra a sessão. Use `false` **exclusivamente** em localdev sem TLS; nunca em staging ou produção. O mesmo vale pra deixar `SESSION_COOKIE_HTTPONLY=true` (default) — desligar expõe o cookie a XSS.

---

## Stores

### `MemorySessionStore` — dev/testes

```python
from tempest_fastapi_sdk import MemorySessionStore

session_store = MemorySessionStore()
```

State no dict do processo. **Não escala** — restart do uvicorn limpa tudo, uma réplica não vê sessões da outra. Use em testes e localdev.

!!! warning "`MemorySessionStore` não sobrevive a restart nem escala horizontalmente"
    O estado vive num dict em memória do processo. Todo restart/redeploy do uvicorn desloga todo mundo, e com mais de uma réplica cada worker enxerga só as próprias sessões (o cookie emitido por uma bate `401` na outra). É estritamente pra testes e localdev — em produção use sempre `RedisSessionStore`.

### `RedisSessionStore` — produção

```python
from redis.asyncio import Redis

from tempest_fastapi_sdk import RedisSessionStore

from src.core.settings import settings


session_store = RedisSessionStore(
    Redis.from_url(settings.REDIS_URL, decode_responses=True),
    prefix="myapp:",
)
```

Schema interno:

- `myapp:sess:<sha256-hex>` — JSON da `Session`, TTL = `expires_at - now`
- `myapp:user:<user-uuid>` — Redis SET de hashes das sessões do user (índice pra `list_by_user` / `delete_by_user`)

TTL é gerenciado pelo Redis automaticamente — sem janitor process.

!!! note "`RedisSessionStore` exige o extra `[cache]`"
    O `RedisSessionStore` depende do client async `redis`, que só é instalado com o extra `[cache]`. Como ele alimenta um middleware, receba um `Redis.from_url(...)` (lazy) ou o `AsyncRedisManager.client_proxy` — nunca o `cache.client`, que levanta antes do lifespan. Instale com `uv add "tempest-fastapi-sdk[cache]"` (some `auth` etc. conforme o serviço). O `MemorySessionStore` não precisa de extra nenhum.

### Customizado

Qualquer classe que implemente o protocol `SessionStore` (5 métodos async) plugga out-of-the-box — DynamoDB, Postgres table, Memcached, etc.

---

## Middleware

`SessionMiddleware` roda **antes** dos routers, lê o cookie, resolve via store, popula `request.state.session`:

```python
from fastapi import APIRouter, Depends

from tempest_fastapi_sdk import Session, make_session_dependency

router = APIRouter()


@router.get("/profile")
async def profile(session: Session = Depends(make_session_dependency(required=True))):
    return {"user_id": str(session.user_id), "data": session.data}
```

**`required=True`** (default): sem cookie → `UnauthorizedException` → resposta `401` no envelope SDK.

**`required=False`**: handler aceita ambos — `session` é `Session | None`. Use em endpoints públicos que adaptam conteúdo pra logged-in users.

Acesso direto (sem dependency):

```python
from fastapi import APIRouter, Request

from tempest_fastapi_sdk import Session

router = APIRouter()


@router.get("/anything")
async def handler(request: Request) -> dict:
    s: Session | None = request.state.session
    return {"authenticated": s is not None}
```

---

## Login sem tabela de usuário

`SessionAuth(user_model=...)` procura o e-mail numa tabela e verifica o hash. Um painel administrativo com **uma** credencial root vinda do ambiente não tem tabela para apontar. Para esse caso, `SessionAuth.from_credentials(username, password, ...)` monta o serviço sobre um `StaticCredentialAuthenticator`, e `make_session_dependency(session_auth=..., on_missing=redirect_to("/login"))` protege a rota HTML sem middleware nenhum.

O exemplo inteiro — form de login, página protegida e logout:

```python
from fastapi import Depends, FastAPI, Form, Request, status
from fastapi.responses import HTMLResponse, RedirectResponse, Response
from pydantic import SecretStr

from tempest_fastapi_sdk import (
    BaseAppSettings,
    MemorySessionStore,
    Session,
    SessionAuth,
    SessionSettings,
    UnauthorizedException,
    make_session_dependency,
    redirect_to,
    register_exception_handlers,
)


class Settings(SessionSettings, BaseAppSettings):
    ADMIN_USERNAME: str
    ADMIN_PASSWORD: SecretStr


settings = Settings()
session_auth = SessionAuth.from_credentials(
    settings.ADMIN_USERNAME,
    settings.ADMIN_PASSWORD,
    store=MemorySessionStore(),
    settings=settings,
)
require_admin = make_session_dependency(
    session_auth=session_auth,
    on_missing=redirect_to("/login"),
)

app = FastAPI()
register_exception_handlers(app)


@app.get("/login", response_class=HTMLResponse)
async def login_page() -> str:
    return (
        '<form method="post" action="/login">'
        '<input name="username"> <input name="password" type="password">'
        "<button>Entrar</button></form>"
    )


@app.post("/login")
async def login(
    request: Request,
    username: str = Form(),
    password: str = Form(),
) -> Response:
    try:
        _session, plaintext = await session_auth.login_with_credentials(
            username,
            password,
            previous_session_id=request.cookies.get(settings.SESSION_COOKIE_NAME),
        )
    except UnauthorizedException:
        return HTMLResponse("Credenciais inválidas", status_code=401)
    response = RedirectResponse("/admin", status_code=status.HTTP_303_SEE_OTHER)
    response.set_cookie(value=plaintext, **settings.session_cookie_kwargs())
    return response


@app.get("/admin", response_class=HTMLResponse)
async def admin(session: Session = Depends(require_admin)) -> str:
    return f"<h1>Painel</h1><p>Sessão expira em {session.expires_at:%H:%M}</p>"


@app.post("/logout")
async def logout(request: Request) -> Response:
    cookie = request.cookies.get(settings.SESSION_COOKIE_NAME)
    if cookie:
        await session_auth.revoke(cookie)
    response = RedirectResponse("/login", status_code=status.HTTP_303_SEE_OTHER)
    response.delete_cookie(**settings.session_cookie_delete_kwargs())
    return response
```

Com `ADMIN_USERNAME=root`, `ADMIN_PASSWORD=s3cret` e `SESSION_COOKIE_SECURE=false` no ambiente, dirigido por `httpx.AsyncClient`:

```text
GET /admin sem cookie: 303 /login
POST /login errado: 401 Credenciais inválidas
POST /login certo: 303 /admin
  Set-Cookie: tempest_session=63XC...; HttpOnly; Max-Age=86400; Path=/; SameSite=lax
GET /admin com cookie: 200 <h1>Painel</h1><p>Sessão expira em 16:55</p>
POST /logout: 303 /login
  Set-Cookie: tempest_session=""; expires=...; HttpOnly; Max-Age=0; Path=/; SameSite=lax
GET /admin com cookie revogado: 303 /login
```

### Pedaço por pedaço

1. **`SessionAuth.from_credentials(...)`** — aceita exatamente um par. As duas metades são sempre comparadas, cada uma com `hmac.compare_digest` sobre o SHA-256 do valor, e os resultados são combinados com `&`, não com `and`: usuário errado custa o mesmo que senha errada, e a mensagem (`invalid username or password`) é a mesma nos dois casos. Aceita `SecretStr`, então o campo do `Settings` entra direto. Credencial vazia levanta `ValueError` na construção — uma env var vazia não vira "aceita form vazio". Não precisa do extra `[auth]`: a comparação é só stdlib, e o `PasswordUtils` (bcrypt) que o modo `user_model=` usa nunca é construído aqui.
2. **O dono da sessão** é `uuid5(STATIC_CREDENTIAL_NAMESPACE, username)` por padrão: estável entre restarts e réplicas, então `session_auth.revoke_all(...)` alcança todas as sessões do root. Passe `user_id=` para escolher outro.
3. **`login_with_credentials(...)`** — verifica e abre a sessão numa chamada. Credencial errada levanta `UnauthorizedException` sem criar sessão e sem revogar a anterior; a rota decide o que renderizar.
4. **`make_session_dependency(session_auth=..., on_missing=redirect_to("/login"))`** — lê o cookie e resolve a sessão ela mesma (com o TTL deslizante, como o middleware). Sem sessão, levanta `HTTPException(303, Location=/login)`; o handler do FastAPI e o `register_exception_handlers` do SDK mantêm o header, e o browser segue. Sem `on_missing`, a resposta continua `401`.
5. **Logout** — `revoke(cookie)` apaga a sessão no store (o mesmo cookie depois disso redireciona para o login) e `delete_cookie(**settings.session_cookie_delete_kwargs())` apaga o cookie no browser.

!!! tip "Outro backend de credencial"
    `from_credentials` é um atalho. Qualquer objeto com `async def authenticate(self, username: str, password: str, /) -> UUID` que levante `UnauthorizedException` na recusa satisfaz o protocolo `SessionAuthenticator` — LDAP, uma API upstream — e entra em `SessionAuth(authenticator=..., store=..., settings=...)`. Passe `user_model=` **ou** `authenticator=`, nunca os dois. O `make_session_router` (login JSON por e-mail) exige `user_model=` e recusa na montagem um serviço montado com `authenticator=`.

!!! warning "`SessionMiddleware` é `BaseHTTPMiddleware` e fica no caminho de toda resposta"
    O middleware envolve o app inteiro, e o `BaseHTTPMiddleware` do Starlette repassa cada pedaço do corpo da resposta por um stream em memória. Medido aqui (Starlette 1.6.0, chamada ASGI direta com `send` vazio, mediana de 7 execuções): um `StreamingResponse` de 256 MiB em pedaços de 64 KiB levou **~4 ms** sem middleware e **~64 ms** com `SessionMiddleware` — cerca de 15 µs por pedaço, pagos por **toda** rota, inclusive o upload/download que nunca lê a sessão. Num serviço que faz streaming de arquivo e usa sessão em três rotas, prefira a dependency com `session_auth=`: só as rotas que a declaram pagam a resolução. Se o middleware estiver montado mesmo assim, a dependency reaproveita a sessão que ele resolveu em vez de resolver de novo.

---

## Segurança

- **Hash at rest**: cookie carrega plaintext de 32 bytes URL-safe; store guarda só SHA-256. Vazamento da tabela `sessions` **não** dá login.
- **Session-fixation prevention**: `SESSION_ROTATE_ON_LOGIN=True` (default) — login bem-sucedido sempre mint id novo, mesmo que o browser já tivesse um. Fecha o vetor "atacante planta cookie conhecido antes do login".
- **CSRF nativo via SameSite**: `SESSION_COOKIE_SAMESITE=lax` (default) bloqueia POST cross-site. Combine com [`CSRFMiddleware`](security.md) pra GET-state-changing endpoints e form-submission.
- **HttpOnly + Secure**: `SESSION_COOKIE_HTTPONLY=True` + `SESSION_COOKIE_SECURE=True` por default. JavaScript não lê (anti-XSS); browser não envia em HTTP.
- **Sliding TTL com floor**: `SESSION_SLIDING=True` (default) refresh a cada hit, mas `created_at` permanece — você pode forçar logout absoluto após N dias via job que limpa rows com `created_at < now - 30d`.
- **Anti-enumeração (parcial — leia o timing)**: `/auth/session/login` rejeita e-mail inexistente e senha errada com o **mesmo** `UnauthorizedException` e a mesma mensagem, então a *resposta* não distingue os dois casos. O **tempo** distingue: `authenticate()` levanta assim que a query não acha o usuário, antes de chamar o verificador de senha, então a tentativa contra conta inexistente não paga o custo do bcrypt — medido nesta máquina, ~153 ms por verificação. Quem cronometra a resposta separa "conta não existe" de "senha errada". Se enumeração por timing está no seu modelo de ameaça, limite a taxa de tentativas por IP/identificador antes do endpoint; o corpo da resposta sozinho não fecha esse canal.
- **Revogação instantânea**: `revoke_all(user_id)` no password-change / suspeita de compromisso → logout em todos os dispositivos no próximo request.

---

## Trade-offs

**Quando NÃO usar:**

- **API pública pra mobile** — apps nativos não dão atenção a cookies; bearer JWT no header `Authorization` continua melhor.
- **Microservices stateless** — cada réplica decode JWT sem hit em DB. Sessions exige Redis compartilhado.
- **Edge/CDN auth** — Cloudflare Workers etc. validam JWT no edge sem chegar no origin. Session exige roundtrip ao backend.

**Quando combinar JWT + Session:**

Possível. SPA web usa cookie de sessão; mobile do mesmo backend usa `UserAuthService.login` → JWT. Os dois flows coexistem sem conflito — `UserAuthService` e `SessionAuth` falam com o mesmo `UserModel`, diferem só no pós-verify (mint JWT vs mint Session).

## Recap

- Sessão server-side é a alternativa ao JWT quando revogação instantânea é
  requisito: o cookie carrega só um id opaco, e o estado vive no store.
- Quatro objetos compõem o fluxo — `SessionStore`, `SessionAuth`,
  `SessionMiddleware` e `make_session_router` —, montados uma vez no `app.py`.
- Cinco endpoints bundled cobrem o ciclo inteiro, e o middleware popula
  `request.state.session` antes de qualquer router.
- O cookie leva plaintext; o store guarda só SHA-256. Vazar a tabela de
  sessões **não** dá login em ninguém.
- Sem tabela de usuário, `SessionAuth.from_credentials(...)` compara as duas
  metades da credencial em tempo constante, e
  `make_session_dependency(session_auth=..., on_missing=redirect_to("/login"))`
  protege rota HTML com `303`, sem middleware.
- `settings.session_cookie_kwargs()` / `session_cookie_delete_kwargs()`
  escrevem e apagam o cookie com os mesmos atributos.
- `MemorySessionStore` serve dev e teste; troque o store, não o resto do
  wiring, para ir a Redis ou banco.

## Próximos passos

- **[Auth flow »](auth-flow.md)** — fluxo JWT bundled (signup / activate / reset). Sessions cobre só login/logout.
- **[Segurança »](security.md)** — `CSRFMiddleware` pra blindar POST contra ataques cross-site mesmo com SameSite=lax.
- **[Cache »](cache.md)** — `AsyncRedisManager` e o `client_proxy`, que é o handle certo para store de middleware como o `RedisSessionStore`.
