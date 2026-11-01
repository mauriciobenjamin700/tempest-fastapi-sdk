"""A sign-up cannot write the bookkeeping columns the SDK models keep.

``signup(fields=...)`` set any mapped column outside
``SIGNUP_PROTECTED_FIELDS``. That left ``last_login_at``, the timestamps,
``SoftDeleteMixin.deleted_at`` (a row born deleted hides itself from every
query) and ``AuditMixin.created_by`` / ``updated_by`` (a sign-up claiming
another principal authored it) open to whatever the body carried.
"""

from __future__ import annotations

import uuid
from collections.abc import AsyncIterator
from datetime import UTC, datetime
from typing import Any

import pytest
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from tempest_fastapi_sdk import (
    SIGNUP_PROTECTED_FIELDS,
    AuditMixin,
    BaseModel,
    BaseUserModel,
    SoftDeleteMixin,
    UserAuthService,
    make_user_token_model,
)
from tempest_fastapi_sdk.settings.mixins import AuthSettings, JWTSettings

PASSWORD: str = "Str0ng-pass-12!"
BOOKKEEPING: tuple[str, ...] = (
    "last_login_at",
    "created_at",
    "updated_at",
    "deleted_at",
    "created_by",
    "updated_by",
)


class _BookkeepingUser(SoftDeleteMixin, AuditMixin, BaseUserModel):
    """A user with soft delete and authorship columns."""

    __tablename__ = "signup_bookkeeping_users"


_BookkeepingUserToken = make_user_token_model(
    user_table="signup_bookkeeping_users",
    tablename="signup_bookkeeping_user_tokens",
    class_name="_BookkeepingUserToken",
)


@pytest.fixture
async def session() -> AsyncIterator[AsyncSession]:
    """Yield one session over a fresh in-memory database.

    Yields:
        AsyncSession: The session.
    """
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as conn:
        await conn.run_sync(BaseModel.metadata.create_all)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    async with factory() as active:
        yield active
    await engine.dispose()


def _service() -> UserAuthService:
    """Build a service that activates accounts immediately.

    Returns:
        UserAuthService: Configured over the bookkeeping model.
    """
    return UserAuthService(
        user_model=_BookkeepingUser,
        token_model=_BookkeepingUserToken,
        auth_settings=AuthSettings(_env_file=None, AUTH_AUTO_ACTIVATE=True),
        jwt_settings=JWTSettings(_env_file=None, JWT_SECRET="x" * 32),
    )


def _value(column: str) -> Any:
    """Return a plausible value for one of the guarded columns.

    Args:
        column (str): The column name.

    Returns:
        Any: A value the column would accept.
    """
    if column in {"created_by", "updated_by"}:
        return str(uuid.uuid4())
    return datetime.now(UTC)


class TestProtectedSet:
    @pytest.mark.parametrize("column", BOOKKEEPING)
    def test_the_constant_names_the_column(self, column: str) -> None:
        assert column in SIGNUP_PROTECTED_FIELDS

    def test_every_column_exists_on_the_model(self) -> None:
        assert set(BOOKKEEPING) <= set(_BookkeepingUser.__table__.columns.keys())

    def test_a_project_column_from_a_mixin_stays_settable(self) -> None:
        assert "name" not in SIGNUP_PROTECTED_FIELDS
        assert "locale" not in SIGNUP_PROTECTED_FIELDS


class TestServiceRefuses:
    @pytest.mark.parametrize("column", BOOKKEEPING)
    async def test_the_column_is_refused_before_any_write(
        self,
        session: AsyncSession,
        column: str,
    ) -> None:
        with pytest.raises(ValueError, match="protected"):
            await _service().signup(
                session,
                email="ana@example.com",
                password=PASSWORD,
                fields={column: _value(column)},
            )
        count = await session.execute(
            select(func.count()).select_from(_BookkeepingUser)
        )
        assert count.scalar_one() == 0
