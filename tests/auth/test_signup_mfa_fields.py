"""A sign-up cannot write the second-factor columns or a declared secret (#663).

``signup(fields=...)`` and the ``signup_schema`` of ``make_auth_router`` set
extra columns on the new row. The :class:`~tempest_fastapi_sdk.MFAMixin`
columns are owned by the enrollment flow, which proves the authenticator
holds the secret before ``totp_enabled_at`` turns the factor on; a body that
names them must not reach the row. A column the project lists in
``__secret_columns__`` is a credential too, and is refused the same way.
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from datetime import UTC, datetime
from typing import Any, ClassVar

import pytest
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient
from pydantic import Field
from sqlalchemy import String, func, select
from sqlalchemy.ext.asyncio import (
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)
from sqlalchemy.orm import Mapped, mapped_column

from tempest_fastapi_sdk import (
    SIGNUP_PROTECTED_FIELDS,
    BaseModel,
    BaseUserModel,
    MFAMixin,
    SignupSchema,
    UserAuthService,
    make_auth_router,
    make_user_token_model,
    register_exception_handlers,
)
from tempest_fastapi_sdk.settings.mixins import AuthSettings, JWTSettings

PASSWORD: str = "Str0ng-pass-12!"
SECRET: str = "JBSWY3DPEHPK3PXPJBSWY3DP"
MFA_COLUMNS: tuple[str, ...] = ("totp_secret", "totp_enabled_at", "totp_last_step")


class _MfaSignupUser(MFAMixin, BaseUserModel):
    """A user with the TOTP columns and a project-declared secret column."""

    __tablename__ = "signup_mfa_fields_users"
    __secret_columns__: ClassVar[frozenset[str]] = frozenset({"api_key_digest"})

    api_key_digest: Mapped[str | None] = mapped_column(String(64), nullable=True)


_MfaSignupUserToken = make_user_token_model(
    user_table="signup_mfa_fields_users",
    tablename="signup_mfa_fields_user_tokens",
    class_name="_MfaSignupUserToken",
)


class _MfaSignupSchema(SignupSchema):
    """Signup body that names every second-factor column.

    Attributes:
        totp_secret (str | None): Shares the TOTP secret column's name.
        totp_enabled_at (datetime | None): Shares the enrollment timestamp.
        totp_last_step (int | None): Shares the replay counter.
        api_key_digest (str | None): Shares the declared secret column.
    """

    totp_secret: str | None = Field(default=None)
    totp_enabled_at: datetime | None = Field(default=None)
    totp_last_step: int | None = Field(default=None)
    api_key_digest: str | None = Field(default=None)


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
        UserAuthService: Configured over the MFA model.
    """
    return UserAuthService(
        user_model=_MfaSignupUser,
        token_model=_MfaSignupUserToken,  # type: ignore[arg-type]
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
    return {
        "totp_secret": SECRET,
        "totp_enabled_at": datetime.now(UTC),
        "totp_last_step": 1,
        "api_key_digest": "f" * 64,
    }[column]


class TestProtectedSet:
    """The constant and the per-model set cover the factor columns."""

    @pytest.mark.parametrize("column", MFA_COLUMNS)
    def test_the_constant_names_every_mfa_column(self, column: str) -> None:
        assert column in SIGNUP_PROTECTED_FIELDS

    def test_every_mixin_column_is_covered(self) -> None:
        mixin_columns = {
            name for name in vars(MFAMixin) if name in _MfaSignupUser.__table__.columns
        }
        assert mixin_columns == set(MFA_COLUMNS)
        assert mixin_columns <= _service().signup_protected_fields()

    def test_a_declared_secret_column_is_protected(self) -> None:
        service = _service()
        assert "api_key_digest" in service.signup_protected_fields()
        assert "api_key_digest" not in service.signup_column_names()


class TestServiceRefuses:
    """``signup(fields=...)`` raises before writing anything."""

    @pytest.mark.parametrize("column", [*MFA_COLUMNS, "api_key_digest"])
    async def test_the_column_is_refused(
        self, session: AsyncSession, column: str
    ) -> None:
        with pytest.raises(ValueError, match="protected"):
            await _service().signup(
                session,
                email="ana@example.com",
                password=PASSWORD,
                fields={column: _value(column)},
            )
        count = await session.execute(select(func.count()).select_from(_MfaSignupUser))
        assert count.scalar_one() == 0


class TestRouterDrops:
    """A ``signup_schema`` field named after a factor column never lands."""

    async def test_the_account_is_born_without_a_factor(
        self, session: AsyncSession
    ) -> None:
        async def _factory() -> AsyncIterator[AsyncSession]:
            yield session

        app = FastAPI()
        register_exception_handlers(app)
        app.include_router(
            make_auth_router(
                _service(),
                session_factory=_factory,
                signup_schema=_MfaSignupSchema,
            )
        )
        async with AsyncClient(
            transport=ASGITransport(app=app), base_url="http://t"
        ) as client:
            response = await client.post(
                "/auth/signup",
                json={
                    "email": "ana@example.com",
                    "password": PASSWORD,
                    "totp_secret": SECRET,
                    "totp_enabled_at": datetime.now(UTC).isoformat(),
                    "totp_last_step": 1,
                    "api_key_digest": "f" * 64,
                },
            )

        assert response.status_code == 201, response.text
        user = (await session.execute(select(_MfaSignupUser))).scalar_one()
        assert user.totp_secret is None
        assert user.totp_enabled_at is None
        assert user.totp_last_step is None
        assert user.api_key_digest is None
