---
description: "Use para proteger rotas por token, papel e permissão, com o `JWTUtils` criado no `create_app`."
---

# Dependências JWT bearer, usuário atual e role

Use para proteger rotas por token, papel e permissão, com o `JWTUtils` criado no `create_app`.

Parte da receita [Camada HTTP](../http.md).

Quatro factories de dependência vivem em `tempest_fastapi_sdk.api.dependencies.auth` — escolha o nível de abstração que você precisa.

| Factory | O que você ganha |
| --- | --- |
| `make_token_dependency(secret)` | Valida o header de segredo compartilhado `X-Token` (tempo constante). |
| `make_bearer_token_dependency(tokens, soft=False)` | Decodifica `Authorization: Bearer <jwt>` e retorna o dict de claims. |
| `make_jwt_user_dependency(tokens, user_loader, soft=False, subject_claim="sub")` | Decodifica o bearer JWT, aguarda `user_loader(subject)`, retorna o usuário carregado. |
| `make_role_dependency(tokens, ["admin"], require_all=False, roles_claim="roles")` / `make_permission_dependency(tokens, ["users:write"], require_all=True, permissions_claim="permissions")` | Decodifica o bearer JWT e controla a rota por roles / permissões. |

!!! info "Fora de rota: `require_x_token`"
    A checagem do segredo compartilhado também existe em forma imperativa —
    `require_x_token(secret, token)` levanta `UnauthorizedException` quando não
    casa (e é no-op com `secret=""`, o modo dev). Serve onde não há `Depends`
    pra pendurar: um consumidor de fila que recebe o token no payload, um
    handler de WebSocket, um script. Em rota, prefira
    `make_token_dependency(secret)` — o Swagger mostra o header.

    ```python
    from tempest_fastapi_sdk import require_x_token

    from src.core.settings import settings


    def handle_job(payload: dict[str, str]) -> None:
        """Reject the job unless it carries the shared secret."""
        require_x_token(settings.TOKEN_SECRET, payload.get("token", ""))
    ```

!!! tip "Usa o flow bundled? Pule o `load_user`"
    Se você monta auth com `UserAuthService` + `make_auth_router`, não precisa escrever `load_user` nem instanciar um `JWTUtils` aqui — chame `auth_service.current_user_dependency()` (e `.current_user_dependency(soft=True)`), que reusa o `JWTUtils` interno do service. Veja a [receita de auth »](../auth-flow/current-user.md). O exemplo abaixo é a montagem manual, pra quando você **não** usa o service.

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

`soft=True` retorna `None` em vez de levantar em tokens ausentes/inválidos — útil para endpoints que funcionam tanto autenticados quanto anônimos. `subject_claim` é `"sub"` por padrão, mas pode ser qualquer claim custom (`"user_id"`, `"uid"`, ...). As dependências de role aceitam uma string ou uma lista de strings no claim do JWT; `require_all=True` exige cada role/permissão listada, `False` (default para roles, sobrescrito para permissões) exige qualquer uma.


## `JWTUtils` criado no `create_app`

Com `get_settings()` e `create_app(settings)`, o `JWTUtils` só existe dentro
do `create_app` — e a dependency de módulo (`CurrentUser = Annotated[...]`) é
declarada antes. Passe uma função que recebe o `Request` no lugar do helper:
ela é chamada a cada request. Vale para `make_jwt_user_dependency`,
`make_bearer_token_dependency`, `make_role_dependency` e
`make_permission_dependency`.

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

Medido em `tests/api/test_lazy_dependencies.py`: dois apps no mesmo processo,
com segredos diferentes e a **mesma** `CurrentUser`, cada um recusa (`401`) o
token do outro.
