---
description: "Use to guard routes by token, role and permission, with the `JWTUtils` built in `create_app`."
---

# JWT bearer, current user and role dependencies

Use to guard routes by token, role and permission, with the `JWTUtils` built in `create_app`.

Part of the [HTTP layer](../http.md) recipe.

Four dependency factories live in `tempest_fastapi_sdk.api.dependencies.auth` — pick the level of abstraction you need.

| Factory | What you get |
| --- | --- |
| `make_token_dependency(secret)` | Validate the `X-Token` shared-secret header (constant time). |
| `make_bearer_token_dependency(tokens, soft=False)` | Decode `Authorization: Bearer <jwt>` and return the claims dict. |
| `make_jwt_user_dependency(tokens, user_loader, soft=False, subject_claim="sub")` | Decode the bearer JWT, await `user_loader(subject)`, return the loaded user. |
| `make_role_dependency(tokens, ["admin"], require_all=False, roles_claim="roles")` / `make_permission_dependency(tokens, ["users:write"], require_all=True, permissions_claim="permissions")` | Decode the bearer JWT and gate the route on roles / permissions. |

!!! info "Outside a route: `require_x_token`"
    The shared-secret check also exists in imperative form —
    `require_x_token(secret, token)` raises `UnauthorizedException` on a
    mismatch (and is a no-op with `secret=""`, the dev mode). Use it where there
    is no `Depends` to hang off: a queue consumer receiving the token in the
    payload, a WebSocket handler, a script. On a route prefer
    `make_token_dependency(secret)` — Swagger then shows the header.

    ```python
    from tempest_fastapi_sdk import require_x_token

    from src.core.settings import settings


    def handle_job(payload: dict[str, str]) -> None:
        """Reject the job unless it carries the shared secret."""
        require_x_token(settings.TOKEN_SECRET, payload.get("token", ""))
    ```

!!! tip "Using the bundled flow? Skip `load_user`"
    If you wire auth with `UserAuthService` + `make_auth_router`, you don't write `load_user` or instantiate a `JWTUtils` here — call `auth_service.current_user_dependency()` (and `.current_user_dependency(soft=True)`), which reuses the service's internal `JWTUtils`. See the [auth recipe »](../auth-flow/current-user.md). The example below is the manual wiring, for when you're **not** using the service.

```python
# src/api/dependencies/auth.py
from uuid import UUID

from tempest_fastapi_sdk import (
    JWTUtils,
    make_bearer_token_dependency,
    make_jwt_user_dependency,
    make_permission_dependency,
    make_role_dependency,
)

from src.api.app import db
from src.core.settings import settings
from src.db.models import UserModel
from src.db.repositories import UserRepository


tokens = JWTUtils(
    secret=settings.JWT_SECRET,
    algorithm=settings.JWT_ALGORITHM,
)


async def load_user(subject: str) -> UserModel:
    """Resolve the JWT subject (a UUID string) to a persisted user."""
    async with db.get_session_context() as session:
        repo = UserRepository(session)
        return await repo.get_by_id(UUID(subject))


require_bearer = make_bearer_token_dependency(tokens)
get_current_user = make_jwt_user_dependency(tokens, load_user)
get_current_user_or_none = make_jwt_user_dependency(tokens, load_user, soft=True)

require_admin = make_role_dependency(tokens, ["admin"])
require_users_write = make_permission_dependency(tokens, ["users:write"])
```

```python
# src/api/routers/users.py

from uuid import UUID

from fastapi import APIRouter, Depends

from src.api.dependencies.auth import (
    get_current_user,
    require_admin,
    require_users_write,
)
from src.db.models import UserModel
from src.schemas import UserResponseSchema


router = APIRouter(prefix="/users", tags=["users"])


@router.get("/me")
async def me(current: UserModel = Depends(get_current_user)) -> UserResponseSchema:
    return UserResponseSchema.model_validate(current)


@router.delete("/{user_id}", dependencies=[Depends(require_admin)])
async def delete_user(user_id: UUID) -> None:
    ...


@router.patch(
    "/{user_id}/permissions",
    dependencies=[Depends(require_users_write)],
)
async def update_perms(user_id: UUID) -> None:
    ...
```

`soft=True` returns `None` instead of raising on missing/invalid tokens — useful for endpoints that work both authenticated and anonymous. `subject_claim` defaults to `"sub"` but can be any custom claim (`"user_id"`, `"uid"`, ...). Role dependencies accept either a string or a list of strings on the JWT claim; `require_all=True` requires every listed role/permission, `False` (default for roles, override for permissions) requires any.


## `JWTUtils` built in `create_app`

With `get_settings()` and `create_app(settings)`, the `JWTUtils` only exists
inside `create_app` — and the module-level dependency
(`CurrentUser = Annotated[...]`) is declared before it. Pass a function taking
the `Request` instead of the helper: it is called on every request. It works
for `make_jwt_user_dependency`, `make_bearer_token_dependency`,
`make_role_dependency` and `make_permission_dependency`.

```python
from typing import Annotated

from fastapi import Depends, FastAPI, Request

from tempest_fastapi_sdk import JWTUtils, make_jwt_user_dependency


async def load_user(subject: str) -> dict[str, str]:
    """Load the user the token names."""
    return {"id": subject}


def jwt_of(request: Request) -> JWTUtils:
    """Read the helper create_app put on app.state."""
    jwt: JWTUtils = request.app.state.jwt
    return jwt


CurrentUser = Annotated[
    dict[str, str],
    Depends(make_jwt_user_dependency(jwt_of, user_loader=load_user, strict=True)),
]


def create_app(secret: str) -> FastAPI:
    """Build the app; the JWT helper only exists from here on."""
    app = FastAPI()
    app.state.jwt = JWTUtils(secret)

    @app.get("/me")
    async def me(user: CurrentUser) -> dict[str, str]:
        """Return the authenticated user."""
        return user

    return app
```

Measured in `tests/api/test_lazy_dependencies.py`: two apps in one process,
with different secrets and the **same** `CurrentUser`, each refuses (`401`)
the other's token.
