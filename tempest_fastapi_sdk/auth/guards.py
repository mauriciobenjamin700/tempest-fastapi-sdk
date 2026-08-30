"""Imperative authorization guards for an already-loaded user.

These complement the route-level dependency factories in
:mod:`tempest_fastapi_sdk.api.dependencies.auth` (which gate on JWT
claims *before* the handler runs). The guards here run *inside* a
service / controller, where you already hold the domain user object
and want to assert an invariant before continuing:

    >>> user = require_admin(current)  # current came from a dependency
    >>> # `user` is now narrowed to a non-None admin user

Each guard:

* accepts ``UserT | None`` (the typical output of a ``soft=True``
  authenticated-user dependency),
* raises an :class:`~tempest_fastapi_sdk.AppException` on failure
  (:class:`UnauthorizedException` / :class:`ForbiddenException` by
  default, mapped to HTTP 401 / 403 by ``register_exception_handlers``),
  and
* returns the user **narrowed to non-None and to the concrete
  subclass** on success, so the caller keeps the precise type and
  drops the ``| None`` from the rest of the function.

## Why the exception is a parameter

The SDK treats ``code`` as the contract, not the message:
``register_exception_handlers`` serializes ``{"detail", "code",
"details"}`` precisely so a client can branch on ``code``, and
:class:`~tempest_fastapi_sdk.AppException` warns with
``GenericCodeWarning`` when a subclass inherits a generic one. A project
that took that seriously declares ``UserIsNotAdminError(code=
"USER_IS_NOT_ADMIN")`` — and until v0.274.0 could not adopt
:func:`require_admin`, because doing so would have answered ``FORBIDDEN``
on every admin route and broken its clients. The same went for the
message, which is English here while the rest of the auth flow ships
pt-BR by default (:mod:`tempest_fastapi_sdk.auth.locale`).

So every guard takes ``exception=`` (its own refusal) and
``unauthenticated=`` (the ``None`` case it delegates), and
:func:`make_flag_guard` builds a guard for any boolean column — the
``is_producer`` / ``is_staff`` flags the SDK does not model and the
project was hand-writing anyway. Defaults are unchanged, so nothing that
worked before behaves differently.
"""

from __future__ import annotations

from collections.abc import Callable
from typing import TYPE_CHECKING, Protocol, TypeAlias, TypeVar

from tempest_fastapi_sdk.exceptions import (
    AppException,
    ForbiddenException,
    UnauthorizedException,
)

if TYPE_CHECKING:
    from tempest_fastapi_sdk.db.user_model import BaseUserModel

UserT = TypeVar("UserT", bound="BaseUserModel")

SubjectT = TypeVar("SubjectT")
"""Whatever a dependency resolved a caller to, user model or not.

:func:`require_authenticated` reads no attribute off its argument — it
only rejects ``None`` — and the SDK hands out subjects that are not user
models: ``FirebaseAuth.get_optional_identity`` yields a
``FirebaseIdentity``, and the Firebase recipe guards it with this
function. Bound to ``BaseUserModel``, that documented call resolved
``UserT`` to ``None`` and failed under mypy.

Unbound means literally unbound: ``require_authenticated("")`` type-checks
and hands back a ``str``. That is the honest reading of a function whose
whole body is ``if user is None: raise``, and the checking is bought back
where the subject *is* known — :meth:`UserAuthService.require_authenticated`
keeps the ``UserT`` bound. The siblings below keep it too, because they read
``is_active`` / ``is_admin``.
"""

GuardException: TypeAlias = Callable[[], AppException]
"""Type of the ``exception=`` seam: a zero-argument factory.

A factory rather than an instance, because an exception carries a
traceback once raised and a module-level singleton would accumulate one
across requests. ``exception=MyError`` works directly whenever the class
takes no required arguments, which is the shape
:func:`~tempest_fastapi_sdk.not_found_exception` and its siblings
produce.
"""


class FlagGuard(Protocol):
    """The callable :func:`make_flag_guard` returns.

    Generic in the user type per call, so a guard built once still
    narrows ``UserModel | None`` to ``UserModel`` at each call site
    rather than degrading to the base class.
    """

    def __call__(self, user: UserT | None, /) -> UserT:
        """Assert the flag on ``user`` and return it narrowed.

        Args:
            user (UserT | None): The user resolved from the request.

        Returns:
            UserT: The same user, narrowed to non-``None``.
        """
        ...


def require_authenticated(
    user: SubjectT | None,
    *,
    exception: GuardException | None = None,
) -> SubjectT:
    """Assert that a subject is authenticated (non-``None``).

    Args:
        user (SubjectT | None): The subject resolved from the request —
            typically the output of a ``soft=True`` authenticated-user
            dependency, which yields ``None`` when no valid token was
            sent. A user model, a provider identity, anything a
            dependency returns.
        exception (GuardException | None): Factory for the
            refusal. ``None`` (default) raises
            :class:`UnauthorizedException` with the SDK's English
            message and the generic ``UNAUTHORIZED`` code. Pass your own
            class to keep your service's ``code`` and language.

    Returns:
        SubjectT: The same subject, narrowed to non-``None`` (and to its
        concrete subclass).

    Raises:
        AppException: When ``user`` is ``None`` — whatever ``exception``
            builds, or :class:`UnauthorizedException` (HTTP 401).
    """
    if user is None:
        raise (
            exception()
            if exception is not None
            else UnauthorizedException(message="Authentication required")
        )
    return user


def make_flag_guard(
    attribute: str,
    *,
    exception: GuardException,
    unauthenticated: GuardException | None = None,
) -> FlagGuard:
    """Build a guard asserting that a boolean column on the user is truthy.

    The generalization of :func:`require_active` / :func:`require_admin`
    to the flags the SDK does not model — ``is_producer``, ``is_staff``,
    ``is_verified`` — which a project was writing by hand anyway, and
    with its own exception, which is the part that could not be reused
    before.

    ```python
    from tempest_fastapi_sdk import make_flag_guard

    from src.core.exceptions import UserIsNotProducerError

    require_producer = make_flag_guard(
        "is_producer",
        exception=UserIsNotProducerError,
    )
    ```

    The attribute is read with ``getattr(user, attribute, False)``, so a
    user model that does not declare the column is refused rather than
    raising ``AttributeError`` — a missing column and a ``False`` one
    both mean "this user does not have it".

    Args:
        attribute (str): Name of the boolean attribute to assert.
        exception (GuardException): Factory for the refusal
            when the flag is falsy. Required: a guard whose whole point
            is the project's own ``code`` should not have a generic
            default to fall back to silently.
        unauthenticated (GuardException | None): Factory for
            the ``user is None`` case. ``None`` (default) delegates to
            :func:`require_authenticated`'s own default.

    Returns:
        FlagGuard: A guard taking ``UserT | None`` and returning
        ``UserT``.
    """

    def _guard(user: UserT | None) -> UserT:
        """Assert ``attribute`` on ``user``.

        Args:
            user (UserT | None): The user resolved from the request.

        Returns:
            UserT: The authenticated user carrying the flag.

        Raises:
            AppException: When the user is absent or the flag is falsy.
        """
        authenticated = require_authenticated(user, exception=unauthenticated)
        if not getattr(authenticated, attribute, False):
            raise exception()
        return authenticated

    return _guard


def require_active(
    user: UserT | None,
    *,
    exception: GuardException | None = None,
    unauthenticated: GuardException | None = None,
) -> UserT:
    """Assert that a user is authenticated **and** active.

    Args:
        user (UserT | None): The user resolved from the request.
        exception (GuardException | None): Factory for the
            refusal when ``is_active`` is falsy. ``None`` (default)
            raises :class:`ForbiddenException` with the generic
            ``FORBIDDEN`` code.
        unauthenticated (GuardException | None): Factory for
            the ``user is None`` case. ``None`` (default) delegates to
            :func:`require_authenticated`.

    Returns:
        UserT: The authenticated, active user.

    Raises:
        AppException: When ``user`` is ``None`` (HTTP 401 by default) or
            ``user.is_active`` is falsy (HTTP 403 by default).
    """
    authenticated = require_authenticated(user, exception=unauthenticated)
    if not authenticated.is_active:
        raise (
            exception()
            if exception is not None
            else ForbiddenException(message="User account is inactive")
        )
    return authenticated


def require_admin(
    user: UserT | None,
    *,
    exception: GuardException | None = None,
    unauthenticated: GuardException | None = None,
) -> UserT:
    """Assert that a user is authenticated **and** an administrator.

    Args:
        user (UserT | None): The user resolved from the request.
        exception (GuardException | None): Factory for the
            refusal when ``is_admin`` is falsy. ``None`` (default)
            raises :class:`ForbiddenException` with the generic
            ``FORBIDDEN`` code.
        unauthenticated (GuardException | None): Factory for
            the ``user is None`` case. ``None`` (default) delegates to
            :func:`require_authenticated`.

    Returns:
        UserT: The authenticated admin user.

    Raises:
        AppException: When ``user`` is ``None`` (HTTP 401 by default) or
            ``user.is_admin`` is falsy (HTTP 403 by default).
    """
    authenticated = require_authenticated(user, exception=unauthenticated)
    if not authenticated.is_admin:
        raise (
            exception()
            if exception is not None
            else ForbiddenException(message="Admin privileges required")
        )
    return authenticated


__all__: list[str] = [
    "FlagGuard",
    "GuardException",
    "make_flag_guard",
    "require_active",
    "require_admin",
    "require_authenticated",
]
