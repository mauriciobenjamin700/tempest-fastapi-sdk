"""Tests for tempest_fastapi_sdk.BaseUserModel."""

from sqlalchemy.ext.asyncio import AsyncSession

from tempest_fastapi_sdk import BaseUserModel


class UserModel(BaseUserModel):
    __tablename__ = "admin_test_users"


class TestNormalizeEmail:
    def test_lowercases_and_strips(self) -> None:
        assert BaseUserModel.normalize_email("  Foo@Bar.COM ") == "foo@bar.com"


class TestPasswordHelpers:
    def test_set_and_check_password_round_trip(self) -> None:
        user = UserModel(email="a@b.com", hashed_password="", is_admin=True)
        user.set_password("hunter2")
        assert user.hashed_password
        assert user.check_password("hunter2") is True
        assert user.check_password("wrong") is False

    def test_check_password_empty_hash_returns_false(self) -> None:
        user = UserModel(email="a@b.com", hashed_password="", is_admin=False)
        assert user.check_password("anything") is False


class TestPersistence:
    async def test_round_trip(self, session: AsyncSession) -> None:
        user = UserModel(email="a@b.com", hashed_password="", is_admin=True)
        user.set_password("pw")
        session.add(user)
        await session.commit()
        await session.refresh(user)
        assert user.id is not None
        assert user.is_admin is True
        assert user.is_active is True
        assert user.last_login_at is None
