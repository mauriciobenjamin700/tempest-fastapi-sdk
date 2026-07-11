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


#: A policy deciding whether ``principal`` may perform ``action`` on the
#: model behind ``admin``. Sync or ``async``; return truthy to allow.
AdminAccessPolicy = Callable[
    [Any, "AdminModel[Any]", AdminPermission],
    bool | Awaitable[bool],
]


__all__: list[str] = [
    "AdminAccessPolicy",
    "AdminPermission",
]
