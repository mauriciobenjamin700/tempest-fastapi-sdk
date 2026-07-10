"""FastAPI route guard for object-level permissions."""

from __future__ import annotations

from collections.abc import Callable, Coroutine
from typing import Any

from fastapi import Depends

from tempest_fastapi_sdk.authz.permissions import (
    PermissionRegistry,
    default_registry,
)


def make_permission_checker(
    permission: str,
    *,
    get_user: Callable[..., Any],
    get_object: Callable[..., Any] | None = None,
    registry: PermissionRegistry | None = None,
    forbidden_message: str | None = None,
) -> Callable[..., Coroutine[Any, Any, None]]:
    """Build a FastAPI dependency enforcing an object-level permission.

    The returned dependency resolves the current user via ``get_user``
    and, when ``get_object`` is given, the target object via
    ``get_object`` (both ordinary FastAPI dependencies — they may
    themselves ``Depends`` on the path params, session, etc.). It then
    calls :meth:`PermissionRegistry.check_permission`, raising
    :class:`~tempest_fastapi_sdk.exceptions.forbidden.ForbiddenException`
    when the permission is denied.

    Omit ``get_object`` for a model-level guard (``obj=None``).

    Example:
        ```python
        require_delete = make_permission_checker(
            "order.delete",
            get_user=get_current_user,
            get_object=get_order_from_path,
        )

        @router.delete("/orders/{order_id}", dependencies=[Depends(require_delete)])
        async def delete_order(order_id: UUID) -> None: ...
        ```

    Args:
        permission (str): The permission string to enforce, e.g.
            ``"order.delete"``.
        get_user (Callable[..., Any]): FastAPI dependency returning the
            current user (or ``None``).
        get_object (Callable[..., Any] | None): FastAPI dependency
            returning the target object; ``None`` for a model-level
            check.
        registry (PermissionRegistry | None): Registry to consult;
            ``None`` uses the process-wide default.
        forbidden_message (str | None): Override for the forbidden
            message.

    Returns:
        Callable[..., Coroutine[Any, Any, None]]: An async FastAPI
        dependency raising ``ForbiddenException`` on denial, ``None`` on
        success.
    """
    reg = registry or default_registry

    if get_object is None:

        async def _check_model(
            user: Any = Depends(get_user),
        ) -> None:
            await reg.check_permission(
                user, permission, None, message=forbidden_message
            )

        return _check_model

    async def _check_object(
        user: Any = Depends(get_user),
        obj: Any = Depends(get_object),
    ) -> None:
        await reg.check_permission(user, permission, obj, message=forbidden_message)

    return _check_object


__all__: list[str] = [
    "make_permission_checker",
]
