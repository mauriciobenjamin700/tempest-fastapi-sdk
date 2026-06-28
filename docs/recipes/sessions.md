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
6. **[Segurança](#seguranca)** — anti-fixation rotation, hash-at-rest, anti-enumeração, CSRF.
7. **[Trade-offs e quando NÃO usar](#trade-offs)** — multi-replica, mobile, edge.

---

## Setup mínimo

Quatro objetos compõem o fluxo. Mount uma vez no `app.py`:

```python
# src/api/app.py
from fastapi import FastAPI

from tempest_fastapi_sdk import (
    AsyncDatabaseManager,
    RedisSessionStore,
    SessionAuth,
    SessionMiddleware,
    SessionSettings,
    make_session_router,
    register_exception_handlers,
)
from tempest_fastapi_sdk.cache import AsyncRedisManager

from src.core.settings import settings
from src.db.models import UserModel

db = AsyncDatabaseManager(settings.DATABASE_URL)
cache = AsyncRedisManager(settings.REDIS_URL)
session_settings = SessionSettings()

session_store = RedisSessionStore(cache.client, prefix=f"{settings.APP_NAME}:")
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

Pronto. O usuário faz `POST /auth/session/login` com email+senha; o SDK seta o cookie HttpOnly+Secure; toda request subsequente que carrega o cookie tem `request.state.session` populado.

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

---

## Stores

### `MemorySessionStore` — dev/testes

```python
from tempest_fastapi_sdk import MemorySessionStore

session_store = MemorySessionStore()
```

State no dict do processo. **Não escala** — restart do uvicorn limpa tudo, uma réplica não vê sessões da outra. Use em testes e localdev.

### `RedisSessionStore` — produção

```python
from tempest_fastapi_sdk import RedisSessionStore
from tempest_fastapi_sdk.cache import AsyncRedisManager

cache = AsyncRedisManager(settings.REDIS_URL)
session_store = RedisSessionStore(cache.client, prefix="myapp:")
```

Schema interno:

- `myapp:sess:<sha256-hex>` — JSON da `Session`, TTL = `expires_at - now`
- `myapp:user:<user-uuid>` — Redis SET de hashes das sessões do user (índice pra `list_by_user` / `delete_by_user`)

TTL é gerenciado pelo Redis automaticamente — sem janitor process.

**Requer `[cache]` extra** (`redis` async client).

### Customizado

Qualquer classe que implemente o protocol `SessionStore` (5 métodos async) plugga out-of-the-box — DynamoDB, Postgres table, Memcached, etc.

---

## Middleware

`SessionMiddleware` roda **antes** dos routers, lê o cookie, resolve via store, popula `request.state.session`:

```python
@router.get("/profile")
async def profile(session: Session = Depends(make_session_dependency(required=True))):
    return {"user_id": str(session.user_id), "data": session.data}
```

**`required=True`** (default): sem cookie → `UnauthorizedException` → resposta `401` no envelope SDK.

**`required=False`**: handler aceita ambos — `session` é `Session | None`. Use em endpoints públicos que adaptam conteúdo pra logged-in users.

Acesso direto (sem dependency):

```python
@router.get("/anything")
async def handler(request: Request) -> dict:
    s: Session | None = request.state.session
    return {"authenticated": s is not None}
```

---

## Segurança

- **Hash at rest**: cookie carrega plaintext de 32 bytes URL-safe; store guarda só SHA-256. Vazamento da tabela `sessions` **não** dá login.
- **Session-fixation prevention**: `SESSION_ROTATE_ON_LOGIN=True` (default) — login bem-sucedido sempre mint id novo, mesmo que o browser já tivesse um. Fecha o vetor "atacante planta cookie conhecido antes do login".
- **CSRF nativo via SameSite**: `SESSION_COOKIE_SAMESITE=lax` (default) bloqueia POST cross-site. Combine com [`CSRFMiddleware`](security.md) pra GET-state-changing endpoints e form-submission.
- **HttpOnly + Secure**: `SESSION_COOKIE_HTTPONLY=True` + `SESSION_COOKIE_SECURE=True` por default. JavaScript não lê (anti-XSS); browser não envia em HTTP.
- **Sliding TTL com floor**: `SESSION_SLIDING=True` (default) refresh a cada hit, mas `created_at` permanece — você pode forçar logout absoluto após N dias via job que limpa rows com `created_at < now - 30d`.
- **Anti-enumeração**: `/auth/session/login` rejeita email-errado e senha-errada com o **mesmo** `UnauthorizedException` + mesmo timing approximado (bcrypt sempre roda).
- **Revogação instantânea**: `revoke_all(user_id)` no password-change / suspeita de compromisso → logout em todos os dispositivos no próximo request.

---

## Trade-offs

**Quando NÃO usar:**

- **API pública pra mobile** — apps nativos não dão atenção a cookies; bearer JWT no header `Authorization` continua melhor.
- **Microservices stateless** — cada réplica decode JWT sem hit em DB. Sessions exige Redis compartilhado.
- **Edge/CDN auth** — Cloudflare Workers etc. validam JWT no edge sem chegar no origin. Session exige roundtrip ao backend.

**Quando combinar JWT + Session:**

Possível. SPA web usa cookie de sessão; mobile do mesmo backend usa `UserAuthService.login` → JWT. Os dois flows coexistem sem conflito — `UserAuthService` e `SessionAuth` falam com o mesmo `UserModel`, diferem só no pós-verify (mint JWT vs mint Session).

## Próximos passos

- **[Auth flow »](auth-flow.md)** — fluxo JWT bundled (signup / activate / reset). Sessions cobre só login/logout.
- **[Segurança »](security.md)** — `CSRFMiddleware` pra blindar POST contra ataques cross-site mesmo com SameSite=lax.
- **[Cache »](cache.md)** — `AsyncRedisManager` que alimenta o `RedisSessionStore`.
