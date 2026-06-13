"""Tests for the imperative authorization guards."""

from __future__ import annotations

import pytest

from tempest_fastapi_sdk import (
    BaseUserModel,
    UserAuthService,
    require_active,
    require_admin,
    require_authenticated,
)
from tempest_fastapi_sdk.exceptions import (
    ForbiddenException,
    UnauthorizedException,
)


class _GuardUser(BaseUserModel):
    __tablename__ = "guard_test_users"


def _user(*, is_active: bool = True, is_admin: bool = False) -> _GuardUser:
    """Build a transient user with explicit flags (no DB needed)."""
    user = _GuardUser(email="guard@a.com", hashed_password="x")
    user.is_active = is_active
    user.is_admin = is_admin
    return user


class TestRequireAuthenticated:
    def test_none_raises_unauthorized(self) -> None:
        with pytest.raises(UnauthorizedException):
            require_authenticated(None)

    def test_returns_same_user(self) -> None:
        user = _user()
        assert require_authenticated(user) is user


class TestRequireActive:
    def test_none_raises_unauthorized(self) -> None:
        with pytest.raises(UnauthorizedException):
            require_active(None)

    def test_inactive_raises_forbidden(self) -> None:
        with pytest.raises(ForbiddenException):
            require_active(_user(is_active=False))

    def test_active_returns_user(self) -> None:
        user = _user(is_active=True)
        assert require_active(user) is user


class TestRequireAdmin:
    def test_none_raises_unauthorized(self) -> None:
        with pytest.raises(UnauthorizedException):
            require_admin(None)

    def test_non_admin_raises_forbidden(self) -> None:
        with pytest.raises(ForbiddenException):
            require_admin(_user(is_admin=False))

    def test_admin_returns_user(self) -> None:
        user = _user(is_admin=True)
        assert require_admin(user) is user


class TestServiceStaticMirror:
    def test_static_methods_delegate(self) -> None:
        admin = _user(is_admin=True)
        assert UserAuthService.require_authenticated(admin) is admin
        assert UserAuthService.require_active(admin) is admin
        assert UserAuthService.require_admin(admin) is admin
        with pytest.raises(UnauthorizedException):
            UserAuthService.require_authenticated(None)
        with pytest.raises(ForbiddenException):
            UserAuthService.require_admin(_user(is_admin=False))
