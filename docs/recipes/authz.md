# Permissões object-level

Autorização baseada em **objeto** — "esse usuário pode editar **esse**
pedido?" — em vez de só "esse token tem a capability `orders:write`?".

!!! info "Autenticação x autorização"
    `tempest_fastapi_sdk.auth` cuida de **quem é** o usuário (login, JWT,
    sessão). Este módulo, `tempest_fastapi_sdk.authz`, cuida do **que ele
    pode fazer** — e leva o objeto em conta. Os dois se complementam.

## O problema

O guard estático (`make_permission_dependency`) responde uma pergunta
por token:

```python
# "o token carrega a permission orders:write?"
Depends(make_permission_dependency(tokens, ["orders:write"]))
```

Mas a decisão real quase sempre depende da **linha**: o dono pode
apagar o próprio pedido, um moderador pode apagar qualquer um, os
demais não podem apagar nenhum. Isso o token não sabe — só o objeto
sabe.

## A solução em 3 passos

### 1. Registre uma regra

Uma regra é um predicado `(user, obj) -> bool`. Decore com
`@permission(...)`:

```python
from tempest_fastapi_sdk.authz import permission

from src.db.models import OrderModel, UserModel


@permission("order.delete")
def only_owner_can_delete(user: UserModel, order: OrderModel) -> bool:
    """Só o dono apaga o próprio pedido."""
    return order.owner_id == user.id
```

### 2. Pergunte ao registry

```python
from tempest_fastapi_sdk.authz import has_perm

allowed: bool = await has_perm(current_user, "order.delete", obj=order)
if allowed:
    await repository.delete(order.id)
```

Ou levante `ForbiddenException` direto quando negado:

```python
from tempest_fastapi_sdk.authz import check_permission

await check_permission(current_user, "order.delete", obj=order)
# segue o fluxo se passou; 403 se não
```

### 3. (Opcional) Proteja a rota

`make_permission_checker` monta uma dependency FastAPI que resolve o
usuário e o objeto e chama `check_permission` por você:

```python
from uuid import UUID

from fastapi import Depends

from tempest_fastapi_sdk.authz import make_permission_checker

from src.api.dependencies import get_current_user, get_order_or_404

require_delete = make_permission_checker(
    "order.delete",
    get_user=get_current_user,
    get_object=get_order_or_404,   # dependency que devolve o OrderModel
)


@router.delete("/orders/{order_id}", dependencies=[Depends(require_delete)])
async def delete_order(order_id: UUID) -> None:
    """Chega aqui só quem passou no check de object-level."""
    ...
```

Omita `get_object` para um check **model-level** (`obj=None`) — útil em
`POST /orders` (criar), onde ainda não existe objeto.

## Como a decisão é tomada

`has_perm(user, perm, obj)` resolve nesta ordem:

1. `user` é `None` → **negado**.
2. **Superusuário** — `is_superuser(user)` (por padrão lê
   `user.is_admin`) → **permitido**, sempre.
3. Existem regras registradas para `perm`?
   - com `obj` → permitido se **qualquer** regra devolver verdadeiro.
   - sem `obj` → permitido se o conjunto estático tiver `perm` **ou**
     qualquer regra (chamada com `obj=None`) devolver verdadeiro.
4. Sem regra para `perm` → cai no **conjunto estático** de permissões
   (`permission_resolver(user)`); uma capability "de mesa" vale para
   todos os objetos.

!!! tip "Wildcards"
    Uma regra pode ser registrada num padrão: `order.*` cobre
    `order.delete`, `order.update`, …; `*` cobre tudo. Você pode ter uma
    regra ampla (`order.*` → moderador) e uma específica
    (`order.delete` → dono) convivendo — vale se **qualquer** uma
    liberar.

## Handlers async

O predicado pode ser `async` — útil quando a decisão precisa do banco:

```python
@permission("project.invite")
async def is_project_member(user: UserModel, project: ProjectModel) -> bool:
    return await membership_repo.exists(
        {"project_id": project.id, "user_id": user.id}
    )
```

## Ajustando bypass e fallback

Por padrão o superusuário é `user.is_admin` e o conjunto estático vem de
`user.permissions`. Ambos são injetáveis — monte seu próprio registry:

```python
from tempest_fastapi_sdk.authz import PermissionRegistry


async def perms_from_roles(user: UserModel) -> set[str]:
    """Deriva as permissions das roles do usuário (async, do banco)."""
    return await role_repo.permissions_for(user.id)


registry = PermissionRegistry(
    is_superuser=lambda u: u.is_admin or "root" in u.roles,
    permission_resolver=perms_from_roles,
)


@permission("order.delete", registry=registry)
def rule(user: UserModel, order: OrderModel) -> bool:
    return order.owner_id == user.id


allowed = await has_perm(user, "order.delete", obj=order, registry=registry)
```

## O call site `user.has_perm(...)`

Herde `PermissionMixin` no seu modelo de usuário para o atalho:

```python
from tempest_fastapi_sdk import BaseUserModel
from tempest_fastapi_sdk.authz import PermissionMixin


class UserModel(BaseUserModel, PermissionMixin):
    __tablename__ = "users"


# em qualquer lugar:
if await user.has_perm("order.delete", obj=order):
    ...
```

O mixin delega ao registry global (`default_registry`).

## Recap

- **Regra** = predicado `(user, obj) -> bool`, registrada com
  `@permission("recurso.acao")`.
- `has_perm` devolve bool; `check_permission` levanta `ForbiddenException`.
- `make_permission_checker` protege a rota (com ou sem objeto).
- Superusuário e conjunto estático são injetáveis via `PermissionRegistry`.
- Regras batem por string exata ou wildcard (`order.*`, `*`).
- `PermissionMixin` dá o `await user.has_perm(...)`.
