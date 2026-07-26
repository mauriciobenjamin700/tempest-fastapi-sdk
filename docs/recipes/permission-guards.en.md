# Permission guards (`@requires`)

You already hold the user — it came from a dependency, or from a service
parameter — and you want to assert an invariant before the function body runs:
"must be active", "must own the order", "must be an admin".

`@requires` does that with plain functions: no framework, no registry, no magic
strings. 🚀

## The problem

Without a decorator, the check becomes noise at the top of every function:

```python
async def delete_order(order_id: UUID, user: UserModel | None) -> None:
    if user is None:
        raise UnauthorizedException(message="Authentication required")
    if not user.is_active:
        raise ForbiddenException(message="User account is inactive")
    if order_id not in user.owned_orders:
        raise ForbiddenException(message="Not the order owner")
    ...
```

Three problems: repetition across every route, authorization mixed into business
logic, and nothing stops someone from writing `if not allowed: return None`
instead of raising — the route answers 200 on a denial.

## The solution in 2 steps

### 1. Write the guard

A guard is an ordinary function: it **takes the user**, **returns the user** (or
`None`) and **denies by raising** an `AppException`.

```python
from tempest_fastapi_sdk import ForbiddenException

from src.db.models import UserModel


def order_owner(user: UserModel) -> UserModel:
    """Assert the user owns the order under edit.

    Args:
        user (UserModel): The authenticated user.

    Returns:
        UserModel: The same user.

    Raises:
        NotOrderOwnerException: When the user does not own the order.
    """
    if not user.owns_current_order:
        raise NotOrderOwnerException()
    return user
```

!!! warning "A guard denies by raising, never by returning `False`"
    `return False` denies **nothing** — `@requires` ignores the value and warns
    with `GuardContractWarning`, and `tempest permissions` reports
    `guard-returns-bool` as an error. The reason is error standardization:
    raising an `AppException` gets you the HTTP status, the `code` and the
    `{detail, code, details}` envelope for free from the SDK handlers.

### 2. Decorate the function

```python
from uuid import UUID

from fastapi import Depends
from tempest_fastapi_sdk import error_responses, requires
from tempest_fastapi_sdk.auth import require_active

from src.api.dependencies import get_current_user
from src.api.guards import order_owner
from src.core.exceptions import NotOrderOwnerException
from src.db.models import UserModel


@router.delete(
    "/orders/{order_id}",
    responses=error_responses(NotOrderOwnerException),
)
@requires(require_active, order_owner)
async def delete_order(
    order_id: UUID,
    user: UserModel = Depends(get_current_user),
) -> None:
    """Delete an order the caller owns.

    Args:
        order_id (UUID): The order to delete.
        user (UserModel): The authenticated, active, owning user.
    """
    await controller.delete(order_id)
```

That is it. Guards run left to right before the body; the body runs only if all
of them pass.

!!! tip "Decorator order"
    `@requires` goes **below** the route decorator. The router must register the
    already-guarded function.

## Where the user comes from

`@requires` finds the user parameter **by annotation**: the one whose type is a
`BaseModel` / `BaseUserModel` subclass. Nothing to configure in the common case.

When the signature holds more than one user, point at the right one:

```python
@requires(can_ban_users, user_param="target")
async def ban_user(
    actor: UserModel = Depends(get_current_user),
    target: UserModel = Depends(get_target_user),
) -> None:
    """Ban the target user.

    Args:
        actor (UserModel): The moderator performing the ban.
        target (UserModel): The user being banned.
    """
    ...
```

??? info "Technical details — annotation resolution"
    The order is: an explicit `user_param=`; then the single parameter whose
    annotation resolves to a user model; then — only when no annotation resolved
    to one — the single parameter whose name is in `USER_PARAM_NAMES`
    (`user`, `current_user`, `actor`, `requester`, `principal`) or whose textual
    annotation mentions `User`. That last step exists because
    `from __future__ import annotations` plus a `TYPE_CHECKING` import leaves the
    annotation impossible to evaluate at decoration time.

    When nothing resolves — or two candidates tie — the import fails with
    `TempestPermissionError`. Better an application that refuses to start than
    one running a check that never fires.

## The guard's return narrows the type

A guard that returns the user replaces the user seen by the next guard **and by
the function body**. That is how the SDK guards
(`require_authenticated` / `require_active` / `require_admin`) turn
`UserT | None` into `UserT`:

```python
@requires(require_active)
async def me(user: UserModel | None = Depends(get_current_user_soft)) -> UserModel:
    """Return the authenticated user.

    Args:
        user (UserModel | None): Filled by the soft dependency; guaranteed
            non-None inside the body.

    Returns:
        UserModel: The active user.
    """
    return user
```

Returning `None` is allowed and means "I did not touch the user".

## Works at any layer

Nothing here depends on FastAPI. The same decorator works on a controller or a
service, sync or `async`:

```python
from tempest_fastapi_sdk import requires

from src.api.guards import order_owner
from src.db.models import UserModel


class OrderService:
    """Business logic for orders."""

    @requires(order_owner)
    async def delete(self, order_id: UUID, user: UserModel) -> None:
        """Delete an order the caller owns.

        Args:
            order_id (UUID): The order to delete.
            user (UserModel): The owning user.
        """
        await self.repository.delete(order_id)
```

`async` guards may only decorate `async` functions — otherwise the coroutine
would never be awaited, so the import fails with `TempestPermissionError`.

## The linter catches your mistakes

Two layers, because each sees what the other cannot.

### At import time — `TempestPermissionError`

The decorator validates while the module is imported. The application **does not
start** with:

| Situation | Message |
| --- | --- |
| `@requires()` with no guard | `needs at least one guard` |
| non-callable guard | `is not callable` |
| guard with 2 required params | `takes 2 required params, expected 1 (user)` |
| `async` guard on a sync function | `is async but ... is not` |
| no user parameter | `no parameter annotated with a user model` |
| two user parameters | `several parameters are user models` |

### At call time — `GuardContractWarning`

What only shows up while running: a guard raising `ValueError` (the API would
answer 500 with no `code`) and a guard returning `False` (the denial would be
ignored). `@requires` **warns** and lets the original exception propagate — it
must not change the outcome of a call it is only observing.

!!! tip "Turn it into an error in tests"
    Run the suite with
    `-W error::tempest_fastapi_sdk.authz.GuardContractWarning` (or
    `filterwarnings = ["error"]` in `pyproject.toml`) and a guard outside the
    contract fails the test instead of becoming a log line.

### In CI — `tempest permissions`

What neither layer reaches: a guard whose `raise` sits behind an `if` no test
exercises, or a guard that is never imported. The command reads the contract off
the source with `ast`, without importing the application:

```bash
tempest permissions                    # informative report (exit 0)
tempest permissions --check            # exit 1 on any error (CI gate)
tempest permissions --check --strict   # fail on warnings too
tempest permissions --path src --path libs
```

```text
src/api/routers/orders.py:41  delete_order
  error: guard-foreign-exception: guard 'order_owner' raises ValueError, which is
    not an AppException subclass; the API layer answers it as HTTP 500 without an
    error code
  warning: guard-missing-annotation: guard 'order_owner' has no return annotation
2 finding(s), 1 error(s).
```

The codes it reports:

| Code | Severity | What it means |
| --- | --- | --- |
| `no-guards` | error | `@requires()` with no guard: everything passes |
| `user-param-missing` | error | no parameter is a user model |
| `user-param-ambiguous` | error | several candidates, no `user_param=` |
| `guard-arity` | error | the guard does not take exactly one parameter |
| `guard-async-in-sync` | error | `async` guard on a sync function |
| `guard-returns-bool` | error | predicate-style guard: its `False` is ignored |
| `guard-foreign-exception` | error | raises outside the `AppException` hierarchy |
| `guard-never-denies` | warning | nothing in the call graph raises |
| `guard-missing-annotation` | warning | parameter or return unannotated |
| `guard-return-type` | warning | return is neither the user, `None`, nor their union |
| `guard-unresolved` | warning | the guard is a lambda, lives outside the scanned paths, or its name maps to several definitions |

!!! note "Reports instead of guessing"
    A guard whose name exists in two modules becomes `guard-unresolved`, not a
    check against the wrong definition. Same policy as `openapi-errors`:
    over-reporting is acceptable, a confident wrong verdict is not.

## OpenAPI error-docs integration

A guard denies by raising, so its exception is as reachable as that of any
function the body calls. `tempest openapi-errors` reads the `@requires` guards
when building the reachable set:

```bash
tempest openapi-errors --check
```

```text
src/api/routers/orders.py:41  DELETE /orders/{order_id}
  undocumented: NotOrderOwnerException
```

`--fix` writes `responses=error_responses(NotOrderOwnerException)` into the
route, like any other exception in the flow. Details in the
[OpenAPI errors »](openapi-errors.md) recipe.

## Auditing a route's guards

```python
from tempest_fastapi_sdk import declared_guards, guarded_user_param

assert declared_guards(delete_order) == (require_active, order_owner)
assert guarded_user_param(delete_order) == "user"
```

Handy in a test asserting that every write route carries at least one guard.

## `@requires` vs. the other authorization tools

| Tool | Question it answers | Where it lives |
| --- | --- | --- |
| `make_permission_dependency` | "does the token carry `orders:write`?" | route dependency, before the handler |
| `has_perm` / `make_permission_checker` | "may this user act on this object?" | registry of `(user, obj) -> bool` rules |
| `@requires` | "does this user pass these invariants?" | any function that already receives the user |

The three compose: a guard may call `check_permission(user, "order.delete",
obj=order)` and pull the whole registry inside `@requires`.

## Recap

- A **guard** is `(user) -> user | None` that denies by raising an
  `AppException`.
- `@requires(g1, g2)` runs the guards in order, below the route decorator.
- The user parameter comes from the annotation; `user_param=` breaks ties.
- A non-`None` return replaces the user — that is how the type narrows.
- Works on routers, controllers and services, sync or `async`.
- Misuse: `TempestPermissionError` at import, `GuardContractWarning` at call
  time, `tempest permissions --check` in CI.
- Guard exceptions reach `error_responses(...)` through
  `tempest openapi-errors`.
