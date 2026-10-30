"""Granular per-model / per-action access control for the admin.

Out of the box every user who can log into the admin (``is_admin``) can
do everything the :class:`AdminModel` flags allow. To restrict a
principal to a subset of models or actions — a "support" role that may
view orders but not delete them, an "editor" who may touch content
models only — pass an :data:`AdminAccessPolicy` to
:func:`~tempest_fastapi_sdk.admin.router.make_admin_router`.

The policy is asked ``(principal, admin, action)`` for every model
action; a falsy answer yields ``403`` (and hides the model from the
dashboard/nav for ``VIEW``). It composes with — does not replace — the
``AdminModel.can_create`` / ``can_edit`` / ``can_delete`` flags: both
must allow an action.

```python
from tempest_fastapi_sdk.admin import AdminPermission


def policy(user: UserModel, admin: AdminModel, action: AdminPermission) -> bool:
    if user.role == "superadmin":
        return True
    if user.role == "support":
        return action is AdminPermission.VIEW
    return False


router = make_admin_router(site, ..., access_policy=policy)
```

The policy also covers what a model surface renders of *another* model:
an inline table on a parent's detail view needs ``VIEW`` on the child
(an inline whose child has no registered admin is asked about a
throwaway ``AdminModel(child)``), an editable inline's add row needs
``CREATE`` and its delete box ``DELETE``, and a foreign-key select,
list filter or autocomplete needs ``VIEW`` on the referenced admin.

The panels that are not about one model — the SQL console, the task
panel and the logs page — are an :class:`AdminSurface`, decided by an
:data:`AdminSurfacePolicy` passed as ``surface_policy``. Once an
``access_policy`` is configured they are **denied** to everyone unless a
``surface_policy`` says otherwise, because a principal the model policy
restricts would otherwise read any table through the console:

```python
from tempest_fastapi_sdk.admin import AdminSurface


def surfaces(user: UserModel, surface: AdminSurface) -> bool:
    return user.role == "superadmin"


router = make_admin_router(
    site, ..., access_policy=policy, surface_policy=surfaces
)
```
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from typing import TYPE_CHECKING, Any

from tempest_fastapi_sdk.core.enums import BaseStrEnum

if TYPE_CHECKING:
    from tempest_fastapi_sdk.admin.config import AdminModel


class AdminPermission(BaseStrEnum):
    """An admin action gated by an :data:`AdminAccessPolicy`."""

    VIEW = "view"
    CREATE = "create"
    EDIT = "edit"
    DELETE = "delete"


class AdminSurface(BaseStrEnum):
    """An admin panel that is not about one model.

    Gated by an :data:`AdminSurfacePolicy` instead of the per-model
    :data:`AdminAccessPolicy`, since there is no :class:`AdminModel` to
    ask about.
    """

    SQL = "sql"
    TASKS = "tasks"
    LOGS = "logs"


#: A policy deciding whether ``principal`` may perform ``action`` on the
#: model behind ``admin``. Sync or ``async``; return truthy to allow.
AdminAccessPolicy = Callable[
    [Any, "AdminModel[Any]", AdminPermission],
    bool | Awaitable[bool],
]


#: A policy deciding whether ``principal`` may open ``surface`` (the SQL
#: console, the task panel or the logs page). Sync or ``async``; return
#: truthy to allow.
AdminSurfacePolicy = Callable[
    [Any, AdminSurface],
    bool | Awaitable[bool],
]


__all__: list[str] = [
    "AdminAccessPolicy",
    "AdminPermission",
    "AdminSurface",
    "AdminSurfacePolicy",
]
