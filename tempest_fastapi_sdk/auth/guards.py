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
* raises the canonical SDK exception on failure
  (:class:`UnauthorizedException` / :class:`ForbiddenException`,
  mapped to HTTP 401 / 403 by ``register_exception_handlers``), and
* returns the user **narrowed to non-None and to the concrete
  subclass** on success, so the caller keeps the precise type and
  drops the ``| None`` from the rest of the function.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, TypeVar

from tempest_fastapi_sdk.exceptions import (
    ForbiddenException,
    UnauthorizedException,
)

if TYPE_CHECKING:
    from tempest_fastapi_sdk.db.user_model import BaseUserModel

UserT = TypeVar("UserT", bound="BaseUserModel")


def require_authenticated(user: UserT | None) -> UserT:
    """Assert that a user is authenticated (non-``None``).

    Args:
        user (UserT | None): The user resolved from the request —
            typically the output of a ``soft=True`` authenticated-user
            dependency, which yields ``None`` when no valid token was
            sent.

    Returns:
        UserT: The same user, narrowed to non-``None`` (and to its
        concrete subclass).

    Raises:
        UnauthorizedException: When ``user`` is ``None`` (HTTP 401).
    """
    if user is None:
        raise UnauthorizedException(message="Authentication required")
    return user


def require_active(user: UserT | None) -> UserT:
    """Assert that a user is authenticated **and** active.

    Args:
        user (UserT | None): The user resolved from the request.

    Returns:
        UserT: The authenticated, active user.

    Raises:
        UnauthorizedException: When ``user`` is ``None`` (HTTP 401).
        ForbiddenException: When ``user.is_active`` is falsy (HTTP 403).
    """
    authenticated = require_authenticated(user)
    if not authenticated.is_active:
        raise ForbiddenException(message="User account is inactive")
    return authenticated


def require_admin(user: UserT | None) -> UserT:
    """Assert that a user is authenticated **and** an administrator.

    Args:
        user (UserT | None): The user resolved from the request.

    Returns:
        UserT: The authenticated admin user.

    Raises:
        UnauthorizedException: When ``user`` is ``None`` (HTTP 401).
        ForbiddenException: When ``user.is_admin`` is falsy (HTTP 403).
    """
    authenticated = require_authenticated(user)
    if not authenticated.is_admin:
        raise ForbiddenException(message="Admin privileges required")
    return authenticated
