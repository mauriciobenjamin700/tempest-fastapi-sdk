"""Enter the application as one of its users, from the admin.

The admin does not know how the service signs its users in, so the flow is
split: the service hands ``AdminModel(impersonate=handler)`` a function that
turns ``(operator, target)`` into the URL that opens the app as ``target``,
and the admin owns everything around it — the ``IMPERSONATE`` permission,
the confirmation page, the refusal of a target that is itself an admin
account, the record of who entered as whom, and the redirect.

:func:`make_jwt_impersonation_handler` is the handler for a service whose
users carry a :class:`~tempest_fastapi_sdk.JWTUtils` access token: it signs
a short-lived token for the target, with the operator in the RFC 8693
``act`` claim, and places it in a URL template the service provides.
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from datetime import timedelta
from typing import TYPE_CHECKING, Any, Final

from tempest_fastapi_sdk.utils.token_types import ACCESS_TOKEN_TYPE, ACT_CLAIM

if TYPE_CHECKING:
    from tempest_fastapi_sdk.utils.jwt import JWTUtils

AdminImpersonateHandler = Callable[[Any, Any], "str | Awaitable[str]"]
"""``(operator, account) -> url``: where the browser goes to act as ``account``.

``operator`` is the signed-in admin principal and ``account`` what
``AdminModel(impersonate_account=...)`` resolved from the row (the row itself
by default) — the identity the admin already checked is not an admin
account. Sync or async. A handler that enters as anything **other** than
``account`` bypasses that check and must refuse admin accounts itself. The
URL usually carries a credential, so the admin never logs it."""

AdminImpersonateAccount = Callable[[Any], "Any | Awaitable[Any]"]
"""``(row) -> account``: the account a row of the model logs in as.

For ``impersonate=`` on a profile model, ``lambda profile: profile.user``. A
sync resolver runs inside ``AsyncSession.run_sync``, so it may read a lazy
relationship; an async one must load what it reads itself (eager loading,
``AsyncAttrs.awaitable_attrs``). ``None``, an account without ``id``, or one
outside the ``row_scope`` of its own registered admin answers ``404``."""

ADMIN_IMPERSONATION_TTL: Final[timedelta] = timedelta(minutes=15)
"""Default lifetime of the token :func:`make_jwt_impersonation_handler` signs."""

ADMIN_IMPERSONATION_EVENT: Final[str] = "admin.impersonate"
"""Event name of the audit entry an impersonation writes.

Stored in the ``event`` column when the audit table mixes in
:class:`~tempest_fastapi_sdk.db.audit.AuditRequestMixin`, and under
``context["event"]`` otherwise."""

ADMIN_IMPERSONATION_FAILED_EVENT: Final[str] = "admin.impersonate.failed"
"""Event name of the entry written when the handler fails after the attempt.

The attempt (:data:`ADMIN_IMPERSONATION_EVENT`) is recorded before the
handler runs; this second entry, with the reason in ``context["reason"]``,
says no credential was handed over."""

IMPERSONATION_TOKEN_PLACEHOLDER: Final[str] = "{token}"
"""Marker :func:`make_jwt_impersonation_handler` replaces with the token."""


def make_jwt_impersonation_handler(
    jwt: JWTUtils,
    *,
    redirect_url: str,
    ttl: timedelta = ADMIN_IMPERSONATION_TTL,
) -> AdminImpersonateHandler:
    """Build an impersonation handler that signs a short access token.

    The token carries ``sub`` (the ``id`` of the account the admin resolved
    and checked — see ``AdminModel(impersonate_account=...)``), ``typ`` set to
    :data:`~tempest_fastapi_sdk.ACCESS_TOKEN_TYPE` — so
    :func:`~tempest_fastapi_sdk.make_bearer_token_dependency` and
    :func:`~tempest_fastapi_sdk.make_jwt_user_dependency` accept it like any
    access token signed with the same secret — the target's ``email`` when
    it has one, and ``act = {"sub": <operator id>}``, the RFC 8693 actor
    claim, so the app can tell an impersonated session from the user's own
    and log the operator behind each request
    (:func:`~tempest_fastapi_sdk.token_actor` reads it back, so a sensitive
    route can refuse an impersonated session). ``iat`` / ``exp`` come from
    :meth:`JWTUtils.encode` with ``ttl``. The token is otherwise an ordinary
    access token: every route that accepts the user's own token accepts it.

    Args:
        jwt (JWTUtils): The helper the app verifies its access tokens with.
        redirect_url (str): Where the browser lands, with
            :data:`IMPERSONATION_TOKEN_PLACEHOLDER` (``{token}``) where the
            token goes, e.g. ``"https://app.example.com/impersonate#token={token}"``.
            A fragment (``#``) keeps the token out of the app server's access
            log and out of the ``Referer`` the landing page sends.
        ttl (timedelta): Token lifetime; defaults to
            :data:`ADMIN_IMPERSONATION_TTL` (15 minutes).

    Returns:
        AdminImpersonateHandler: The handler to pass as
        ``AdminModel(impersonate=...)``.

    Raises:
        ValueError: When ``redirect_url`` has no ``{token}`` placeholder, or
            ``ttl`` is not positive.
    """
    if IMPERSONATION_TOKEN_PLACEHOLDER not in redirect_url:
        raise ValueError(
            "make_jwt_impersonation_handler `redirect_url` must contain "
            f"{IMPERSONATION_TOKEN_PLACEHOLDER} where the token goes",
        )
    if ttl <= timedelta(0):
        raise ValueError("make_jwt_impersonation_handler `ttl` must be positive")

    def handler(operator: Any, target: Any) -> str:
        """Sign the account's token and place it in the redirect URL.

        Args:
            operator (Any): The signed-in admin; its ``id`` goes in ``act``.
            target (Any): The account the admin resolved and checked; its
                ``id`` is the ``sub``.

        Returns:
            str: ``redirect_url`` with the token in place.
        """
        claims: dict[str, Any] = {
            "sub": str(target.id),
            "typ": ACCESS_TOKEN_TYPE,
            ACT_CLAIM: {"sub": str(operator.id)},
        }
        email = getattr(target, "email", None)
        if email:
            claims["email"] = str(email)
        token = jwt.encode(claims, ttl=ttl)
        return redirect_url.replace(IMPERSONATION_TOKEN_PLACEHOLDER, token)

    return handler


__all__: list[str] = [
    "ADMIN_IMPERSONATION_EVENT",
    "ADMIN_IMPERSONATION_FAILED_EVENT",
    "ADMIN_IMPERSONATION_TTL",
    "IMPERSONATION_TOKEN_PLACEHOLDER",
    "AdminImpersonateAccount",
    "AdminImpersonateHandler",
    "make_jwt_impersonation_handler",
]
