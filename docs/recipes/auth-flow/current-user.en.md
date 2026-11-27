# Get the `current_user` from the request

Use to inject the authenticated user into your routes, with optional auth (`soft=True`) and guards inside the service.

Part of the [Bundled auth flow (signup / activate / login / reset)](../auth-flow.md) recipe.

`make_auth_router` **issues** the JWT pair (login/activate return `access_token` + `refresh_token`). But what next? When the frontend sends `Authorization: Bearer <access_token>` to **your own** routes, you need a dependency that decodes the token and resolves the user.

Since v0.49.0, `UserAuthService` builds that dependency for you — `current_user_dependency()`. It:

1. Looks the token up in three places, in **header → cookie → query string** order (first hit wins): `Authorization: Bearer <jwt>` via `HTTPBearer`, then the cookie (`cookie_name`), and finally the query parameter (`query_param`).
2. Decodes and verifies the JWT with **the same `JWTUtils` the service signs with** — no second secret to keep in sync.
3. Pulls the `sub` (user id) from the payload, opens a session from `db=`, and returns the persisted `UserModel`.
4. Refuses a deactivated account with `403` *(v0.305.0+)* — see below.
5. Answers `401` when the `sub` matches no account — an account deleted after the token was issued *(v0.344.0+; it used to be `404`, which a front end does not read as "session over")*. With `soft=True`, yields `None`.

## 1. Declare the dependency once

The service already has `user_model`, `JWTUtils` and the session — so you don't write `load_user` by hand. Group both variants in `src/api/dependencies/auth.py`:

```python
# src/api/dependencies/auth.py
from src.api.dependencies.resources import auth_service

get_current_user = auth_service.current_user_dependency()
get_current_user_or_none = auth_service.current_user_dependency(soft=True)
```

!!! info "Requires `db=` on `UserAuthService`"
    `current_user_dependency` needs `db=` (the `AsyncDatabaseManager` from [Minimum setup](setup.md#minimum-setup)) — without it, it raises `RuntimeError`. Because it reuses the internal `self.jwt`, the token is verified with the **same** secret that signed it — the divergent-`JWT_SECRET` footgun is gone.

    The user is loaded on the **request-scoped** session (`db.session_dependency` by default) — the very one your repositories use. So the instance comes back *attached* and you can mutate / `refresh` it without hitting `InvalidRequestError: Instance is not persistent within this Session`. If your repositories depend on a different session callable (a project-local `get_session`, say), pass **that exact callable** as `session_dependency=` — FastAPI caches a sub-dependency by callable, so a distinct wrapper opens a second session and detaches the user again.

    The same guarantee holds for `make_auth_router`'s own authenticated routes (`/auth/password-change`, `/auth/mfa/*`): they load the user on the request session as of **0.171.1**. Before that the router opened a private session and handed back an already-*detached* instance, so the write was silently dropped and the following `refresh` blew up — `/auth/password-change` answered 500 while keeping the old password.

!!! warning "A deactivated account does not pass, even with a valid token (v0.305.0+)"
    `login` already refuses an inactive account, but a token issued
    **before** the deactivation stays valid until it expires — the whole
    access TTL. So the dependency checks `is_active` after loading the user
    and answers `403` `ACCOUNT_INACTIVE` (the same `AuthExceptions.account_inactive` login uses); up to 0.304.0 it returned the row and every
    route had to remember `require_active`. The authenticated routes of
    `make_auth_router` (`/auth/me`, password and email change, MFA, passkeys)
    run the same check.

    - `soft=True` yields `None` for an inactive account, as for an invalid
      token.
    - `inactive_exception=YourException` swaps the `403` for your class.
    - `require_active=False` restores the old behavior — for the route that
      must serve an inactive account (reactivation, say).

    Measured in `tests/auth/test_current_user_active.py`: token issued,
    account deactivated, request with the token → `403`; with
    `require_active=False` → `200`.

!!! tip "The `401` message in your language"
    A missing or invalid token, or one whose account no longer exists,
    answers `401` `UNAUTHORIZED` with an English `detail`
    (`"Authorization token is missing or invalid"`, the
    `MISSING_TOKEN_MESSAGE` constant). A service answering in another
    language passes its own phrase — that is what the app shows the user:

    ```python
    from tempest_fastapi_sdk import UserAuthService

    def build_current_user(auth_service: UserAuthService):
        return auth_service.current_user_dependency(
            unauthorized_message="Sua sessão expirou. Entre de novo.",
        )
    ```

    The `code` stays `UNAUTHORIZED`: the front end branches on the `code`,
    the phrase is only the text. Measured in
    `tests/auth/test_current_user_active.py`.

!!! tip "Not header-only: cookie and query string count too"
    The dependency tries **header → cookie → query string** and stops at the first hit, so the single line above serves both bearer clients and cookie clients.

    - **Cookie — automatic.** With `AUTH_TOKEN_DELIVERY` set to `"cookie"` or `"both"`, `cookie_name` is **auto-derived** from `AUTH_ACCESS_COOKIE_NAME` — the same cookie the bundled `/auth/login` set. Zero extra wiring: a route guarded by this dependency already accepts that cookie. A bearer-only delivery mode leaves it `None` (header only). Pass `cookie_name="..."` to force a name.
    - **Query string — opt-in.** Never auto-derived. It exists for clients that can send neither a header **nor** a cookie. For **SSE**, don't turn this on by hand: `auth_service.sse_router(broker)` trades the session for a stream ticket that is valid on no other route. Raw `query_param` is for the rest:

    ```python
    # src/api/dependencies/auth.py
    from src.api.dependencies.resources import auth_service

    get_current_user = auth_service.current_user_dependency(
        cookie_name="access_token",
        query_param="access_token",
    )
    ```

    Full SSE walkthrough in the [SSE recipe »](../sse.md#authentication).

!!! warning "A token in the URL leaks"
    Query strings land in access logs, browser history and the `Referer` header. Enable `query_param` only over TLS, only with a **short-lived access token** (never a refresh token), and scrub the value from your log format. When the client shares the API's origin, prefer a session cookie (`withCredentials`).

??? note "No `UserAuthService`? Build the dependency by hand"
    If your service doesn't use the bundled flow, the `make_jwt_user_dependency` primitive accepts any `JWTUtils` + a one-argument async `user_loader`:

    ```python
    from uuid import UUID

    from tempest_fastapi_sdk import JWTUtils, make_jwt_user_dependency

    from src.api.dependencies.resources import db
    from src.core.settings import settings
    from src.db.models import UserModel
    from src.db.repositories import UserRepository

    tokens: JWTUtils = JWTUtils(
        secret=settings.JWT_SECRET,
        algorithm=settings.JWT_ALGORITHM,
    )


    async def load_user(subject: str) -> UserModel:
        """Resolve the JWT subject (a UUID string) to the persisted user."""
        async with db.get_session_context() as session:
            repo: UserRepository = UserRepository(session)
            return await repo.get_by_id(UUID(subject))


    get_current_user = make_jwt_user_dependency(tokens, load_user)
    get_current_user_or_none = make_jwt_user_dependency(tokens, load_user, soft=True)
    ```

    Heads up: here `tokens` **must** use the same `JWT_SECRET` / `JWT_ALGORITHM` as login, otherwise every valid token is rejected.

## 2. Inject it on the route with `Depends`

```python
# src/api/routers/users.py
from fastapi import APIRouter, Depends

from src.api.dependencies.auth import get_current_user
from src.db.models import UserModel
from src.schemas import UserResponseSchema

router: APIRouter = APIRouter(prefix="/users", tags=["users"])


@router.get("/profile")
async def profile(current: UserModel = Depends(get_current_user)) -> UserResponseSchema:
    """Return the user who owns the request's bearer token."""
    return UserResponseSchema.model_validate(current)
```

`current` **is** the `UserModel` the service resolved — typed, persisted, ready to use. Missing or invalid token → `401 UnauthorizedException` before the route body runs.

!!! tip "For a plain `/me`, write nothing"
    The bundled router has mounted `GET /auth/me` since v0.198.0, with
    `AuthUserSchema` (the columns `BaseUserModel` guarantees) as the
    response. Write your own only when the route does something **beyond**
    returning the account — aggregating counters, joining a profile from
    another table. If it is just one extra column on the same table,
    subclass the schema instead of rewriting the route:

    ```python
    from tempest_fastapi_sdk import AuthUserSchema, make_auth_router


    class UserResponseSchema(AuthUserSchema):
        name: str | None = None


    app.include_router(
        make_auth_router(
            auth_service,
            session_factory=get_db,
            me_response_model=UserResponseSchema,
        )
    )
    ```

!!! note "Your `session_factory` has to guarantee nothing"

    The HTML pages render after the commit that consumes the token, and
    SQLAlchemy's `async_sessionmaker` defaults to `expire_on_commit=True` —
    which made the page read an expired column and answer **500**
    (`MissingGreenlet`) through v0.265.0. The router now reloads the row
    **only when it expired**, checking `inspect(user).expired`, which never
    touches the database. With `AsyncDatabaseManager`'s factory
    (`expire_on_commit=False`) there is no extra query at all.

## 3. Optional auth — `soft=True`

For routes that work both authenticated **and** anonymous (e.g. a public feed that personalizes when logged in), use the `soft` variant — it returns `None` instead of raising:

```python
from fastapi import APIRouter, Depends

from src.api.dependencies.auth import get_current_user_or_none
from src.db.models import UserModel
from src.schemas import PostResponseSchema
from src.services import FeedService

feed_service = FeedService()

router = APIRouter()


@router.get("/feed")
async def feed(
    current: UserModel | None = Depends(get_current_user_or_none),
) -> list[PostResponseSchema]:
    """Public feed; personalizes the ranking when a user is logged in."""
    if current is None:
        return await feed_service.public()
    return await feed_service.personalized(current.id)
```

!!! tip "Role and permission are the next step"
    When the route needs a **role** (`admin`) or **permission** (`users:write`) and not just "logged in", swap for `make_role_dependency` / `make_permission_dependency`. See the [HTTP recipe »](../http.en.md) — same `JWTUtils`, same `Depends` pattern.

## 4. Imperative guards — checks inside the service / controller

The dependencies above gate the **route** (before the handler runs). But what about when you already hold the user deeper in the stack (service, controller) and just want to **assert** a condition before continuing? Since v0.50.0 the SDK ships three ready-made guards — no rewriting `if user is None: raise ...` in every service:

```python
from tempest_fastapi_sdk import (
    require_active,
    require_admin,
    require_authenticated,
)
```

| Guard | Raises when | HTTP status |
|-------|-------------|-------------|
| `require_authenticated(user)` | `user is None` | 401 `UnauthorizedException` |
| `require_active(user)` | `None`, or `not user.is_active` | 401 / 403 `ForbiddenException` |
| `require_admin(user)` | `None`, or `not user.is_admin` | 401 / 403 `ForbiddenException` |

The detail that matters: each one **returns the user already narrowed** — non-`None`, with the concrete type preserved — so the rest of the function stops seeing `| None`:

```python
from tempest_fastapi_sdk import require_admin

from src.db.models import UserModel


class ReportService:
    async def delete_all(self, current: UserModel | None) -> None:
        """Only an admin may purge reports."""
        admin: UserModel = require_admin(current)  # 401/403, or returns typed
        await self.repository.purge(by=admin.id)   # `admin` is no longer `| None`
```

It pairs directly with `current_user_dependency(soft=True)`: the route passes `UserModel | None`, and the guard decides in the service.

!!! tip "Already have `auth_service`? Use the static mirrors"
    The same guards exist as static methods on `UserAuthService` — `auth_service.require_admin(current)` — for when you already inject the service and don't want an extra import. Same semantics, same exception.
