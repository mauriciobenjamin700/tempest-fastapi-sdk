"""Custom admin actions — user-defined row/bulk operations.

The admin ships three hardcoded bulk operations (activate / deactivate /
delete). Anything else — "send welcome email", "mark as shipped",
"recalculate totals" — is a *custom action*: an async function decorated
with :func:`admin_action` and registered on an
:class:`~tempest_fastapi_sdk.admin.AdminModel` via ``actions=[...]``.

    @admin_action(label="Send welcome email")
    async def send_welcome(ctx: AdminActionContext) -> AdminActionResult:
        users = await ctx.repository.list(filters={"id": ctx.ids})
        for user in users:
            await mailer.send_welcome(user.email)
        return AdminActionResult(f"Sent {len(users)} welcome emails.")

    site.register(AdminModel(model=UserModel, actions=[send_welcome]))

The decorated function receives an :class:`AdminActionContext` (the
selected row ids, a repository, the request and the admin session) and
returns an optional :class:`AdminActionResult` whose message is flashed
back on the list view.
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from sqlalchemy.ext.asyncio import AsyncSession
    from starlette.requests import Request

    from tempest_fastapi_sdk.admin.session import AdminSession
    from tempest_fastapi_sdk.db.repository import BaseRepository


@dataclass(frozen=True)
class AdminActionContext:
    """Everything a custom action handler needs to do its work.

    Attributes:
        ids (list[Any]): Identity values of the rows the user selected.
        repository (BaseRepository[Any]): Repository bound to the
            request DB session for this admin's model.
        db_session (AsyncSession): The request DB session (for work
            beyond the repository).
        request (Request): The inbound request.
        session (AdminSession): The authenticated admin session.
        principal (Any): The resolved admin user row.
    """

    ids: list[Any]
    repository: BaseRepository[Any]
    db_session: AsyncSession
    request: Request
    session: AdminSession
    principal: Any


@dataclass(frozen=True)
class AdminActionResult:
    """The outcome of a custom action, flashed on the list view.

    Attributes:
        message (str): Human-readable result shown to the operator.
        category (str): Banner style — ``"success"`` (default),
            ``"error"`` or ``"warning"``.
    """

    message: str
    category: str = "success"


ActionHandler = Callable[[AdminActionContext], Awaitable["AdminActionResult | None"]]


@dataclass(frozen=True)
class AdminAction:
    """A registered custom action: metadata + handler.

    Attributes:
        name (str): Stable identifier (form value); unique per model.
        label (str): Text shown in the bulk-action dropdown.
        handler (ActionHandler): The async function to run.
        dangerous (bool): When ``True``, the UI marks it as destructive
            (a stronger confirm prompt).
    """

    name: str
    label: str
    handler: ActionHandler
    dangerous: bool = field(default=False)


def admin_action(
    *,
    label: str,
    name: str | None = None,
    dangerous: bool = False,
) -> Callable[[ActionHandler], ActionHandler]:
    """Mark an async function as a custom admin action.

    The decorated function is registered by passing it to
    ``AdminModel(actions=[...])``; the decorator only attaches metadata,
    so the function stays directly callable (and unit-testable) on its
    own.

    Args:
        label (str): Dropdown label shown to the operator.
        name (str | None): Stable identifier (the submitted form value).
            Defaults to the function's ``__name__``.
        dangerous (bool): Flag a destructive action for a stronger UI
            confirm.

    Returns:
        Callable[[ActionHandler], ActionHandler]: The decorator.
    """

    def decorator(func: ActionHandler) -> ActionHandler:
        func.__admin_action__ = AdminAction(  # type: ignore[attr-defined]
            name=name or func.__name__,
            label=label,
            handler=func,
            dangerous=dangerous,
        )
        return func

    return decorator


def resolve_admin_action(func: ActionHandler) -> AdminAction:
    """Return the :class:`AdminAction` attached by :func:`admin_action`.

    Args:
        func (ActionHandler): A function passed to ``AdminModel(actions=)``.

    Returns:
        AdminAction: The attached metadata + handler.

    Raises:
        TypeError: When ``func`` was not decorated with
            :func:`admin_action`.
    """
    action = getattr(func, "__admin_action__", None)
    if not isinstance(action, AdminAction):
        raise TypeError(
            f"{getattr(func, '__name__', func)!r} is not an @admin_action — "
            "decorate it with @admin_action(label=...) before registering.",
        )
    return action


__all__: list[str] = [
    "AdminAction",
    "AdminActionContext",
    "AdminActionResult",
    "admin_action",
    "resolve_admin_action",
]
