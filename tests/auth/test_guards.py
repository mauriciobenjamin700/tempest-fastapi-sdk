"""Tests for the imperative authorization guards.

The seam half of this module is the point. A project that took the SDK's
own advice — ``code`` is the contract, ``GenericCodeWarning`` scolds a
subclass that inherits a generic one — declares its own
``UserIsNotAdminError`` and could not adopt ``require_admin`` at all,
because adopting it would have answered ``FORBIDDEN`` on every admin
route and broken its clients. So the tests below assert the ``code`` on
the wire, not just the class: catching ``ForbiddenException`` passes
either way, which is exactly why the defect survived.
"""

from __future__ import annotations

import pytest
from sqlalchemy import Boolean
from sqlalchemy.orm import Mapped, mapped_column

from tempest_fastapi_sdk import (
    BaseUserModel,
    UserAuthService,
    make_flag_guard,
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


class _UserIsNotAdminError(ForbiddenException):
    """What a project following the SDK's own error advice declares."""

    message: str = "Apenas administradores podem acessar este recurso"
    code: str = "USER_IS_NOT_ADMIN"


class _UserIsNotProducerError(ForbiddenException):
    """A flag the SDK does not model, which every project hand-wrote."""

    message: str = "Apenas produtores podem acessar este recurso"
    code: str = "USER_IS_NOT_PRODUCER"


class _NotSignedInError(UnauthorizedException):
    """The 401 half of the same problem."""

    message: str = "Faça login para continuar"
    code: str = "NOT_SIGNED_IN"


class _FlagUser(BaseUserModel):
    __tablename__ = "guard_flag_users"

    is_producer: Mapped[bool] = mapped_column(Boolean, default=False)


class TestTheExceptionSeam:
    """Each guard's refusal is replaceable, and the default is unchanged."""

    def test_default_admin_refusal_keeps_the_generic_code(self) -> None:
        """The behaviour every existing consumer already depends on."""
        with pytest.raises(ForbiddenException) as caught:
            require_admin(_user(is_admin=False))
        assert caught.value.code == "FORBIDDEN"

    def test_admin_refusal_can_carry_the_project_code(self) -> None:
        with pytest.raises(ForbiddenException) as caught:
            require_admin(_user(is_admin=False), exception=_UserIsNotAdminError)
        assert caught.value.code == "USER_IS_NOT_ADMIN"
        assert caught.value.status_code == 403

    def test_active_refusal_can_carry_the_project_code(self) -> None:
        with pytest.raises(ForbiddenException) as caught:
            require_active(_user(is_active=False), exception=_UserIsNotAdminError)
        assert caught.value.code == "USER_IS_NOT_ADMIN"

    def test_the_401_half_is_replaceable_too(self) -> None:
        """Swapping only the 403 would leave the other half generic."""
        with pytest.raises(UnauthorizedException) as caught:
            require_authenticated(None, exception=_NotSignedInError)
        assert caught.value.code == "NOT_SIGNED_IN"

    def test_admin_delegates_the_401_to_unauthenticated(self) -> None:
        with pytest.raises(UnauthorizedException) as caught:
            require_admin(
                None,
                exception=_UserIsNotAdminError,
                unauthenticated=_NotSignedInError,
            )
        assert caught.value.code == "NOT_SIGNED_IN"

    def test_the_message_travels_with_the_class(self) -> None:
        """Half the problem was the English message, not just the code."""
        with pytest.raises(ForbiddenException) as caught:
            require_admin(_user(is_admin=False), exception=_UserIsNotAdminError)
        assert "administradores" in caught.value.message

    def test_the_service_mirror_takes_the_seam(self) -> None:
        """The static mirrors are a second front door on the same defect."""
        with pytest.raises(ForbiddenException) as caught:
            UserAuthService.require_admin(
                _user(is_admin=False), exception=_UserIsNotAdminError
            )
        assert caught.value.code == "USER_IS_NOT_ADMIN"


class TestMakeFlagGuard:
    """The flags the SDK does not model, which projects wrote by hand."""

    def test_truthy_flag_returns_the_user_narrowed(self) -> None:
        require_producer = make_flag_guard(
            "is_producer", exception=_UserIsNotProducerError
        )
        user = _FlagUser(email="p@a.com", hashed_password="x")
        user.is_producer = True
        assert require_producer(user) is user

    def test_falsy_flag_raises_the_given_exception(self) -> None:
        require_producer = make_flag_guard(
            "is_producer", exception=_UserIsNotProducerError
        )
        user = _FlagUser(email="p@a.com", hashed_password="x")
        user.is_producer = False
        with pytest.raises(ForbiddenException) as caught:
            require_producer(user)
        assert caught.value.code == "USER_IS_NOT_PRODUCER"

    def test_a_model_without_the_column_is_refused_not_crashed(self) -> None:
        """A missing column and a False one both mean "does not have it".

        Reading with a bare attribute access would make this an
        ``AttributeError`` — a 500 where a 403 belongs.
        """
        require_producer = make_flag_guard(
            "is_producer", exception=_UserIsNotProducerError
        )
        with pytest.raises(ForbiddenException):
            require_producer(_user())

    def test_none_delegates_to_require_authenticated(self) -> None:
        require_producer = make_flag_guard(
            "is_producer",
            exception=_UserIsNotProducerError,
            unauthenticated=_NotSignedInError,
        )
        with pytest.raises(UnauthorizedException) as caught:
            require_producer(None)
        assert caught.value.code == "NOT_SIGNED_IN"

    def test_it_reproduces_require_admin(self) -> None:
        """The factory is the generalization, so it must cover the originals."""
        guard = make_flag_guard("is_admin", exception=_UserIsNotAdminError)
        admin = _user(is_admin=True)
        assert guard(admin) is admin
        with pytest.raises(ForbiddenException):
            guard(_user(is_admin=False))
