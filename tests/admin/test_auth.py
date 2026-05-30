"""Tests for tempest_fastapi_sdk.admin.auth."""

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from tempest_fastapi_sdk import (
    AdminAuthError,
    BaseUserModel,
    UserModelAuthBackend,
)
from tempest_fastapi_sdk.db.model import BaseModel


class AdminUser(BaseUserModel):
    __tablename__ = "admin_auth_test_users"


class NotAUser(BaseModel):
    __tablename__ = "admin_auth_test_dummy"


@pytest.fixture
async def admin_user(session: AsyncSession) -> AdminUser:
    user = AdminUser(email="root@example.com", hashed_password="", is_admin=True)
    user.set_password("hunter2")
    session.add(user)
    await session.commit()
    await session.refresh(user)
    return user


class TestUserModelAuthBackend:
    def test_rejects_non_user_model(self) -> None:
        with pytest.raises(TypeError):
            UserModelAuthBackend(NotAUser)  # type: ignore[arg-type]

    async def test_authenticate_success(
        self, session: AsyncSession, admin_user: AdminUser
    ) -> None:
        backend = UserModelAuthBackend(AdminUser)
        user = await backend.authenticate(
            session, identifier="ROOT@example.com", password="hunter2"
        )
        assert user.id == admin_user.id
        assert user.last_login_at is not None

    async def test_authenticate_wrong_password(
        self, session: AsyncSession, admin_user: AdminUser
    ) -> None:
        backend = UserModelAuthBackend(AdminUser)
        with pytest.raises(AdminAuthError):
            await backend.authenticate(
                session, identifier="root@example.com", password="WRONG"
            )

    async def test_authenticate_unknown_user(self, session: AsyncSession) -> None:
        backend = UserModelAuthBackend(AdminUser)
        with pytest.raises(AdminAuthError):
            await backend.authenticate(
                session, identifier="ghost@example.com", password="x"
            )

    async def test_non_admin_rejected(self, session: AsyncSession) -> None:
        user = AdminUser(email="user@example.com", hashed_password="", is_admin=False)
        user.set_password("pw")
        session.add(user)
        await session.commit()
        backend = UserModelAuthBackend(AdminUser)
        with pytest.raises(AdminAuthError):
            await backend.authenticate(
                session, identifier="user@example.com", password="pw"
            )

    async def test_inactive_user_rejected(self, session: AsyncSession) -> None:
        user = AdminUser(
            email="inactive@example.com",
            hashed_password="",
            is_admin=True,
            is_active=False,
        )
        user.set_password("pw")
        session.add(user)
        await session.commit()
        backend = UserModelAuthBackend(AdminUser)
        with pytest.raises(AdminAuthError):
            await backend.authenticate(
                session, identifier="inactive@example.com", password="pw"
            )

    async def test_load_principal_returns_user(
        self, session: AsyncSession, admin_user: AdminUser
    ) -> None:
        backend = UserModelAuthBackend(AdminUser)
        loaded = await backend.load_principal(session, str(admin_user.id))
        assert loaded is not None
        assert loaded.id == admin_user.id

    async def test_load_principal_invalid_id(self, session: AsyncSession) -> None:
        backend = UserModelAuthBackend(AdminUser)
        assert await backend.load_principal(session, "not-a-uuid") is None

    async def test_load_principal_missing(self, session: AsyncSession) -> None:
        backend = UserModelAuthBackend(AdminUser)
        from uuid import uuid4

        assert await backend.load_principal(session, str(uuid4())) is None

    def test_principal_id_and_display_name(self) -> None:
        backend = UserModelAuthBackend(AdminUser)
        user = AdminUser(email="x@y.com", hashed_password="", is_admin=True)
        from uuid import uuid4

        user.id = uuid4()
        assert backend.principal_id(user) == str(user.id)
        assert backend.display_name(user) == "x@y.com"
