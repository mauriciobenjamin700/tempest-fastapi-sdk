# Object-level permissions

**Object**-based authorization — "may this user edit **this** order?" —
rather than only "does this token carry the `orders:write` capability?".

!!! info "Authentication vs authorization"
    `tempest_fastapi_sdk.auth` handles **who** the user is (login, JWT,
    session). This module, `tempest_fastapi_sdk.authz`, handles **what
    they may do** — and takes the object into account. The two
    complement each other.

## The problem

The static guard (`make_permission_dependency`) answers one question
per token:

```python
# "does the token carry the orders:write permission?"
Depends(make_permission_dependency(tokens, ["orders:write"]))
```

But the real decision almost always depends on the **row**: the owner
may delete their own order, a moderator may delete any, everyone else
may delete none. The token can't know that — only the object does.

## The solution in 3 steps

### 1. Register a rule

A rule is a predicate `(user, obj) -> bool`. Decorate it with
`@permission(...)`:

```python
from tempest_fastapi_sdk.authz import permission

from src.db.models import OrderModel, UserModel


@permission("order.delete")
def only_owner_can_delete(user: UserModel, order: OrderModel) -> bool:
    """Only the owner deletes their own order."""
    return order.owner_id == user.id
```

### 2. Ask the registry

```python
from tempest_fastapi_sdk.authz import has_perm

allowed: bool = await has_perm(current_user, "order.delete", obj=order)
if allowed:
    await repository.delete(order.id)
```

Or raise `ForbiddenException` directly on denial:

```python
from tempest_fastapi_sdk.authz import check_permission

await check_permission(current_user, "order.delete", obj=order)
# flow continues if allowed; 403 otherwise
```

### 3. (Optional) Guard the route

`make_permission_checker` builds a FastAPI dependency that resolves the
user and the object and calls `check_permission` for you:

```python
from uuid import UUID

from fastapi import Depends

from tempest_fastapi_sdk.authz import make_permission_checker

from src.api.dependencies import get_current_user, get_order_or_404

require_delete = make_permission_checker(
    "order.delete",
    get_user=get_current_user,
    get_object=get_order_or_404,   # dependency returning the OrderModel
)


@router.delete("/orders/{order_id}", dependencies=[Depends(require_delete)])
async def delete_order(order_id: UUID) -> None:
    """Reached only by callers who passed the object-level check."""
    ...
```

Omit `get_object` for a **model-level** check (`obj=None`) — handy on
`POST /orders` (create), where no object exists yet.

## How the decision is made

`has_perm(user, perm, obj)` resolves in this order:

1. `user` is `None` → **denied**.
2. **Superuser** — `is_superuser(user)` (default: reads
   `user.is_admin`) → **allowed**, always.
3. Are there rules registered for `perm`?
   - with `obj` → allowed when **any** rule returns truthy.
   - without `obj` → allowed when the static set holds `perm` **or** any
     rule (called with `obj=None`) returns truthy.
4. No rule for `perm` → fall back to the **static** permission set
   (`permission_resolver(user)`); a blanket capability applies to every
   object.

!!! tip "Wildcards"
    A rule can be registered on a pattern: `order.*` covers
    `order.delete`, `order.update`, …; `*` covers everything. A broad
    rule (`order.*` → moderator) and a specific one (`order.delete` →
    owner) can coexist — access is granted if **any** of them allows it.

## Async handlers

The predicate can be `async` — useful when the decision needs the
database:

```python
@permission("project.invite")
async def is_project_member(user: UserModel, project: ProjectModel) -> bool:
    return await membership_repo.exists(
        {"project_id": project.id, "user_id": user.id}
    )
```

## Tuning bypass and fallback

By default the superuser is `user.is_admin` and the static set comes
from `user.permissions`. Both are injectable — build your own registry:

```python
from tempest_fastapi_sdk.authz import PermissionRegistry


async def perms_from_roles(user: UserModel) -> set[str]:
    """Derive permissions from the user's roles (async, from the DB)."""
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

## The `user.has_perm(...)` call site

Inherit `PermissionMixin` on your user model for the shortcut:

```python
from tempest_fastapi_sdk import BaseUserModel
from tempest_fastapi_sdk.authz import PermissionMixin


class UserModel(BaseUserModel, PermissionMixin):
    __tablename__ = "users"


# anywhere:
if await user.has_perm("order.delete", obj=order):
    ...
```

The mixin delegates to the global registry (`default_registry`).

## Recap

- A **rule** is a `(user, obj) -> bool` predicate, registered with
  `@permission("resource.action")`.
- `has_perm` returns a bool; `check_permission` raises
  `ForbiddenException`.
- `make_permission_checker` guards the route (with or without an object).
- Superuser and static set are injectable via `PermissionRegistry`.
- Rules match by exact string or wildcard (`order.*`, `*`).
- `PermissionMixin` gives you `await user.has_perm(...)`.
