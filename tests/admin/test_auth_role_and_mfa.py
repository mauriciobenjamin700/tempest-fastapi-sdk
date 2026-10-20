"""``UserModelAuthBackend``: authorize by role, and require MFA enrollment."""

from __future__ import annotations

import pytest
from sqlalchemy import String
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import Mapped, mapped_column

from tempest_fastapi_sdk import (
    AdminAuthError,
    BaseUserModel,
    MFAMixin,
    UserModelAuthBackend,
)
from tempest_fastapi_sdk.utils.datetime import utcnow


class RoleUser(MFAMixin, BaseUserModel):
    """A user authorized by a role column, not by ``is_admin``."""

    __tablename__ = "admin_role_mfa_users"

    role: Mapped[str] = mapped_column(String(16), default="MEMBER", nullable=False)


class StaffAuthBackend(UserModelAuthBackend):
    """Admin access for ``role == "STAFF"``."""

    def is_authorized(self, user: BaseUserModel) -> bool:
        """Authorize staff only.

        Args:
            user (BaseUserModel): The loaded account.

        Returns:
            bool: ``True`` for staff.
        """
        return isinstance(user, RoleUser) and user.role == "STAFF"


async def _add(
    session: AsyncSession,
    email: str,
    *,
    role: str = "MEMBER",
    is_admin: bool = False,
    mfa: bool = False,
) -> RoleUser:
    """Insert one user with password ``pw``.

    Args:
        session (AsyncSession): The session.
        email (str): The address.
        role (str): The role column.
        is_admin (bool): The ``is_admin`` flag.
        mfa (bool): Whether MFA is enrolled.

    Returns:
        RoleUser: The stored user.
    """
    user = RoleUser(email=email, hashed_password="", is_admin=is_admin, role=role)
    user.set_password("pw")
    if mfa:
        user.totp_secret = "JBSWY3DPEHPK3PXP"
        user.totp_enabled_at = utcnow()
    session.add(user)
    await session.commit()
    await session.refresh(user)
    return user


class TestIsAuthorized:
    async def test_default_is_the_is_admin_flag(self, session: AsyncSession) -> None:
        admin = await _add(session, "a@example.com", is_admin=True)
        member = await _add(session, "m@example.com")
        backend = UserModelAuthBackend(RoleUser)
        assert backend.is_authorized(admin) is True
        assert backend.is_authorized(member) is False

    async def test_override_lets_the_role_in(self, session: AsyncSession) -> None:
        staff = await _add(session, "s@example.com", role="STAFF")
        backend = StaffAuthBackend(RoleUser)
        user = await backend.authenticate(
            session, identifier="s@example.com", password="pw"
        )
        assert user.id == staff.id
        assert await backend.load_principal(session, str(staff.id)) is not None

    async def test_override_refuses_is_admin_without_the_role(
        self, session: AsyncSession
    ) -> None:
        admin = await _add(session, "a@example.com", is_admin=True)
        backend = StaffAuthBackend(RoleUser)
        with pytest.raises(AdminAuthError, match="not authorized"):
            await backend.authenticate(
                session, identifier="a@example.com", password="pw"
            )
        assert await backend.load_principal(session, str(admin.id)) is None

    async def test_wrong_password_still_reads_invalid_credentials(
        self, session: AsyncSession
    ) -> None:
        """The override must not move the authorization check before the password."""
        await _add(session, "m@example.com")
        backend = StaffAuthBackend(RoleUser)
        with pytest.raises(AdminAuthError, match="Invalid credentials"):
            await backend.authenticate(
                session, identifier="m@example.com", password="no"
            )

    async def test_role_revoked_after_login_ends_the_session(
        self, session: AsyncSession
    ) -> None:
        staff = await _add(session, "s@example.com", role="STAFF")
        backend = StaffAuthBackend(RoleUser)
        await backend.authenticate(session, identifier="s@example.com", password="pw")
        staff.role = "MEMBER"
        await session.commit()
        assert await backend.load_principal(session, str(staff.id)) is None


class TestRequireMfa:
    async def test_unenrolled_account_is_refused_at_login(
        self, session: AsyncSession
    ) -> None:
        await _add(session, "a@example.com", is_admin=True)
        backend = UserModelAuthBackend(RoleUser, require_mfa=True)
        with pytest.raises(AdminAuthError, match="MFA enrollment required for /admin"):
            await backend.authenticate(
                session, identifier="a@example.com", password="pw"
            )

    async def test_wrong_password_is_checked_first(self, session: AsyncSession) -> None:
        await _add(session, "a@example.com", is_admin=True)
        backend = UserModelAuthBackend(RoleUser, require_mfa=True)
        with pytest.raises(AdminAuthError, match="Invalid credentials"):
            await backend.authenticate(
                session, identifier="a@example.com", password="no"
            )

    async def test_unauthorized_account_reads_not_authorized(
        self, session: AsyncSession
    ) -> None:
        await _add(session, "m@example.com")
        backend = UserModelAuthBackend(RoleUser, require_mfa=True)
        with pytest.raises(AdminAuthError, match="not authorized"):
            await backend.authenticate(
                session, identifier="m@example.com", password="pw"
            )

    async def test_enrolled_account_logs_in(self, session: AsyncSession) -> None:
        admin = await _add(session, "a@example.com", is_admin=True, mfa=True)
        backend = UserModelAuthBackend(RoleUser, require_mfa=True)
        user = await backend.authenticate(
            session, identifier="a@example.com", password="pw"
        )
        assert user.id == admin.id
        assert await backend.load_principal(session, str(admin.id)) is not None

    async def test_mfa_disabled_after_login_ends_the_session(
        self, session: AsyncSession
    ) -> None:
        admin = await _add(session, "a@example.com", is_admin=True, mfa=True)
        backend = UserModelAuthBackend(RoleUser, require_mfa=True)
        await backend.authenticate(session, identifier="a@example.com", password="pw")
        admin.totp_secret = None
        admin.totp_enabled_at = None
        await session.commit()
        assert await backend.load_principal(session, str(admin.id)) is None

    async def test_off_by_default(self, session: AsyncSession) -> None:
        admin = await _add(session, "a@example.com", is_admin=True)
        backend = UserModelAuthBackend(RoleUser)
        assert backend.require_mfa is False
        await backend.authenticate(session, identifier="a@example.com", password="pw")
        assert await backend.load_principal(session, str(admin.id)) is not None

    async def test_role_and_mfa_together(self, session: AsyncSession) -> None:
        await _add(session, "s@example.com", role="STAFF")
        backend = StaffAuthBackend(RoleUser, require_mfa=True)
        with pytest.raises(AdminAuthError, match="MFA enrollment required"):
            await backend.authenticate(
                session, identifier="s@example.com", password="pw"
            )
