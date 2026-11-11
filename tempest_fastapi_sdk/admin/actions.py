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
back on the list view. An action that creates a credential returns it in
``AdminActionResult.secrets`` (:class:`AdminSecret`), and the panel shows
it once on the action's own response instead of redirecting.
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable, Sequence
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from pydantic import BaseModel as PydanticModel
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
class AdminSecret:
    """A value the operator must see exactly once, such as a new API key.

    The ``value`` is left out of ``repr`` so a logged or printed result
    does not carry it.

    Attributes:
        label (str): What the value is (``"API key for ana@example.com"``).
        value (str): The secret itself, in plaintext.
    """

    label: str
    value: str = field(repr=False)


@dataclass(frozen=True)
class AdminActionResult:
    """The outcome of a custom action.

    Without ``secrets`` the message is flashed on the list view, carried
    by the redirect's query string. With ``secrets`` the panel answers the
    action's own ``POST`` with a page that shows the message and each
    secret once — no redirect, so no value reaches a URL, the browser
    history, an access log or a ``Referer`` — sent with
    ``Cache-Control: no-store`` and ``Referrer-Policy: no-referrer``. The
    request's database session is committed before that page renders, so
    a secret on screen is never one whose row was rolled back.

    Attributes:
        message (str): Human-readable result shown to the operator.
        category (str): Banner style — ``"success"`` (default),
            ``"error"`` or ``"warning"``.
        secrets (Sequence[AdminSecret]): Values to reveal once. Any
            sequence is accepted (a list included) and stored as a tuple.
    """

    message: str
    category: str = "success"
    secrets: Sequence[AdminSecret] = ()

    def __post_init__(self) -> None:
        """Store ``secrets`` as a tuple, so the frozen result stays hashable."""
        object.__setattr__(self, "secrets", tuple(self.secrets))


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


CreateHandlerFunc = Callable[
    [Any, AdminActionContext], Awaitable["AdminActionResult | None"]
]
"""An async ``(data, ctx)`` creating a record from a validated schema.

``data`` is an instance of the handler's schema; ``ctx`` is the same
:class:`AdminActionContext` a custom action gets, with ``ids`` empty.
"""


@dataclass(frozen=True)
class AdminCreateHandler:
    """A create flow driven by a schema and a service, not by the columns.

    Some rows are born from a service rather than typed in: an API key is a
    generated secret stored as a hash and shown in plaintext once, so the
    column form (``key_hash``, ``key_prefix`` typed by hand) is wrong for
    it. Pass this as ``AdminModel(create_handler=...)`` and the list's
    "+ New" button opens a form generated from :attr:`schema`; on submit
    the panel validates it and calls :attr:`handler`.

    The handler returns the same :class:`AdminActionResult` a custom action
    does. With ``secrets``, the database session is committed and the
    values are shown once on the ``POST``'s own response; reloading that
    page does not call the handler again (a single-use nonce in the form).
    Without them, the panel redirects to the list and flashes the message.
    An :class:`~tempest_fastapi_sdk.AppException` raised by the handler
    re-renders the form with its message and what was typed.

    Attributes:
        schema (type[PydanticModel]): The Pydantic model the form is
            generated from and the submission is validated against. A field
            whose name is in the admin's ``autocomplete_fields`` renders as
            the same foreign-key search box.
        handler (CreateHandlerFunc): The async function that creates the
            record.
        label (str | None): Text of the "+ New" button and the form
            heading; ``None`` uses ``New <verbose name>``.
    """

    schema: type[PydanticModel]
    handler: CreateHandlerFunc
    label: str | None = None


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
    "AdminCreateHandler",
    "CreateHandlerFunc",
    "admin_action",
    "resolve_admin_action",
]
