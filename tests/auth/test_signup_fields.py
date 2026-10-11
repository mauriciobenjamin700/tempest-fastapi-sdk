"""``signup(fields=...)`` — product columns set before the insert.

``on_signup`` runs after the ``flush``, so it can only fill a nullable
column: a ``NOT NULL`` phone without a default made the insert itself
fail, and the only way around it was to rewrite ``signup`` whole. These
tests pin the three halves of the fix: the service sets the columns
before the insert, the router forwards the ``signup_schema`` fields that
are columns, and a unique violation on one of them answers ``409``
instead of escaping as an ``IntegrityError``.
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from typing import Any

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
    SignupSchema,
    UserAuthService,
    make_auth_router,
    make_user_token_model,
    register_exception_handlers,
)
from tempest_fastapi_sdk.exceptions import ConflictException
from tempest_fastapi_sdk.settings.mixins import AuthSettings, JWTSettings

PASSWORD: str = "Str0ng-pass-12!"


class _PhoneUser(BaseUserModel):
    """A user whose phone is required and unique, like the transport's."""

    __tablename__ = "signup_fields_users"

    phone: Mapped[str] = mapped_column(String(20), unique=True)


_PhoneUserToken = make_user_token_model(
    user_table="signup_fields_users",
    tablename="signup_fields_user_tokens",
    class_name="_PhoneUserToken",
)


class _PhoneSignupSchema(SignupSchema):
    """Signup body carrying a column, a non-column, and a protected name.

    Attributes:
        phone (str): Required phone, a ``NOT NULL`` unique column.
        accept_terms (bool): Not a column; reaches only ``on_signup``.
        is_admin (bool): Shares a protected column's name; never applied.
    """

    phone: str = Field(max_length=20)
    accept_terms: bool = Field(default=False)
    is_admin: bool = Field(default=False)


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
        UserAuthService: Configured over the phone model.
    """
    return UserAuthService(
        user_model=_PhoneUser,
        token_model=_PhoneUserToken,  # type: ignore[arg-type]
        auth_settings=AuthSettings(_env_file=None, AUTH_AUTO_ACTIVATE=True),
        jwt_settings=JWTSettings(_env_file=None, JWT_SECRET="x" * 32),
    )


async def _count(session: AsyncSession) -> int:
    """Return how many users exist.

    Args:
        session (AsyncSession): The session.

    Returns:
        int: The row count.
    """
    result = await session.execute(select(func.count()).select_from(_PhoneUser))
    return int(result.scalar_one())


class TestServiceFields:
    """``UserAuthService.signup(fields=...)``."""

    async def test_not_null_column_is_filled_before_the_insert(
        self, session: AsyncSession
    ) -> None:
        """The phone lands on the row; no hook involved."""
        user, _ = await _service().signup(
            session,
            email="ana@example.com",
            password=PASSWORD,
            fields={"phone": "+5586999990000"},
        )
        await session.commit()

        assert isinstance(user, _PhoneUser)
        assert user.phone == "+5586999990000"

    async def test_missing_not_null_column_names_it(
        self, session: AsyncSession
    ) -> None:
        """Without ``fields`` the error names ``phone``, not a bare IntegrityError."""
        with pytest.raises(ValueError, match="'phone' is NOT NULL"):
            await _service().signup(session, email="ana@example.com", password=PASSWORD)

    async def test_duplicate_unique_column_is_a_conflict(
        self, session: AsyncSession
    ) -> None:
        """A second account with the same phone is a 409, with the column."""
        service = _service()
        await service.signup(
            session,
            email="ana@example.com",
            password=PASSWORD,
            fields={"phone": "+5586999990000"},
        )
        await session.commit()

        with pytest.raises(ConflictException) as caught:
            await service.signup(
                session,
                email="bia@example.com",
                password=PASSWORD,
                fields={"phone": "+5586999990000"},
            )

        assert caught.value.field == "phone"
        assert caught.value.details["columns"] == ["phone"]

    @pytest.mark.parametrize("key", sorted(SIGNUP_PROTECTED_FIELDS))
    async def test_protected_column_is_refused(
        self, session: AsyncSession, key: str
    ) -> None:
        """Protected keys are refused before anything is written."""
        with pytest.raises(ValueError, match="protected"):
            await _service().signup(
                session,
                email="ana@example.com",
                password=PASSWORD,
                fields={"phone": "+5586999990000", key: True},
            )
        assert await _count(session) == 0

    async def test_unknown_key_is_refused(self, session: AsyncSession) -> None:
        """A key that is not a column is a wiring defect, raised up front."""
        with pytest.raises(ValueError, match="not columns"):
            await _service().signup(
                session,
                email="ana@example.com",
                password=PASSWORD,
                fields={"phone": "+5586999990000", "nickname": "ana"},
            )
        assert await _count(session) == 0


def _app(session: AsyncSession, **router_kwargs: Any) -> FastAPI:
    """Mount the auth router over ``session``.

    Args:
        session (AsyncSession): The session every request shares.
        **router_kwargs (Any): Forwarded to ``make_auth_router``.

    Returns:
        FastAPI: The application under test.
    """

    async def _factory() -> AsyncIterator[AsyncSession]:
        yield session

    app = FastAPI()
    register_exception_handlers(app)
    app.include_router(
        make_auth_router(_service(), session_factory=_factory, **router_kwargs)
    )
    return app


def _client(app: FastAPI) -> AsyncClient:
    """Bind a client to ``app`` over ASGI.

    Args:
        app (FastAPI): The application under test.

    Returns:
        AsyncClient: The test client.
    """
    return AsyncClient(transport=ASGITransport(app=app), base_url="http://t")


class TestRouterForwardsColumns:
    """``make_auth_router(signup_schema=...)`` fills the columns itself."""

    async def test_schema_column_is_written_without_a_hook(
        self, session: AsyncSession
    ) -> None:
        """``phone`` reaches the row; ``accept_terms`` (not a column) is skipped."""
        app = _app(session, signup_schema=_PhoneSignupSchema)
        async with _client(app) as client:
            response = await client.post(
                "/auth/signup",
                json={
                    "email": "ana@example.com",
                    "password": PASSWORD,
                    "phone": "+5586999990000",
                    "accept_terms": True,
                },
            )

        assert response.status_code == 201
        user = (await session.execute(select(_PhoneUser))).scalar_one()
        assert user.phone == "+5586999990000"

    async def test_protected_name_in_the_schema_never_reaches_the_row(
        self, session: AsyncSession
    ) -> None:
        """A body with ``is_admin: true`` creates a non-admin account."""
        app = _app(session, signup_schema=_PhoneSignupSchema)
        async with _client(app) as client:
            response = await client.post(
                "/auth/signup",
                json={
                    "email": "ana@example.com",
                    "password": PASSWORD,
                    "phone": "+5586999990000",
                    "is_admin": True,
                },
            )

        assert response.status_code == 201
        user = (await session.execute(select(_PhoneUser))).scalar_one()
        assert user.is_admin is False

    async def test_duplicate_phone_answers_409_with_the_column(
        self, session: AsyncSession
    ) -> None:
        """The second signup with the same phone is a 409 naming ``phone``."""
        app = _app(session, signup_schema=_PhoneSignupSchema)
        body = {"password": PASSWORD, "phone": "+5586999990000"}
        async with _client(app) as client:
            first = await client.post(
                "/auth/signup", json={**body, "email": "ana@example.com"}
            )
            second = await client.post(
                "/auth/signup", json={**body, "email": "bia@example.com"}
            )

        assert first.status_code == 201
        assert second.status_code == 409
        payload = second.json()
        assert payload["code"] == "CONFLICT"
        assert payload["details"]["columns"] == ["phone"]


class _NicknameUser(BaseUserModel):
    """A user with a nullable product column an ``on_signup`` hook fills."""

    __tablename__ = "signup_legacy_users"

    nickname: Mapped[str | None] = mapped_column(String(40), nullable=True)


_NicknameUserToken = make_user_token_model(
    user_table="signup_legacy_users",
    tablename="signup_legacy_user_tokens",
    class_name="_NicknameUserToken",
)


class _NicknameSignupSchema(SignupSchema):
    """Signup body whose extra field is also a column.

    Attributes:
        nickname (str | None): A nullable column of the user model.
    """

    nickname: str | None = Field(default=None, max_length=40)


class _LegacySignupService(UserAuthService):
    """A service that overrides ``signup`` with the pre-0.305.0 signature.

    Services written before ``fields=`` existed override ``signup`` with
    ``email``, ``password`` and ``name`` only. The router must keep calling
    them the way it did then.
    """

    async def signup(  # type: ignore[override]
        self,
        session: AsyncSession,
        *,
        email: str,
        password: str,
        name: str | None = None,
    ) -> tuple[BaseUserModel, Any]:
        """Delegate to the base service without ``fields``.

        Args:
            session (AsyncSession): The session.
            email (str): Account email.
            password (str): Plaintext password.
            name (str | None): Display name.

        Returns:
            tuple[BaseUserModel, Any]: The user and the activation token.
        """
        return await super().signup(session, email=email, password=password, name=name)


def _legacy_service() -> _LegacySignupService:
    """Build the legacy service over the nickname model.

    Returns:
        _LegacySignupService: Activates accounts immediately.
    """
    return _LegacySignupService(
        user_model=_NicknameUser,
        token_model=_NicknameUserToken,  # type: ignore[arg-type]
        auth_settings=AuthSettings(_env_file=None, AUTH_AUTO_ACTIVATE=True),
        jwt_settings=JWTSettings(_env_file=None, JWT_SECRET="x" * 32),
    )


def _legacy_app(session: AsyncSession, **router_kwargs: Any) -> FastAPI:
    """Mount the auth router over the legacy service.

    Args:
        session (AsyncSession): The session every request shares.
        **router_kwargs (Any): Forwarded to ``make_auth_router``.

    Returns:
        FastAPI: The application under test.
    """

    async def _factory() -> AsyncIterator[AsyncSession]:
        yield session

    app = FastAPI()
    register_exception_handlers(app)
    app.include_router(
        make_auth_router(_legacy_service(), session_factory=_factory, **router_kwargs)
    )
    return app


class TestOverrideWithoutFields:
    """A ``signup`` override that predates ``fields=`` keeps working.

    Since 0.305.0 the router passed ``fields={}`` on every signup, so a
    service overriding ``signup`` with the old signature answered every
    ``POST /auth/signup`` with a 500 ``TypeError`` — seen in production on
    2026-10-11 when a service jumped from before 0.305.0 to 0.312.0.
    """

    async def test_plain_signup_reaches_the_override(
        self, session: AsyncSession
    ) -> None:
        """No extra field: the override is called without ``fields``."""
        app = _legacy_app(session)
        async with _client(app) as client:
            response = await client.post(
                "/auth/signup",
                json={"email": "ana@example.com", "password": PASSWORD},
            )

        assert response.status_code == 201

    async def test_extra_columns_fall_back_to_on_signup(
        self, session: AsyncSession
    ) -> None:
        """A column in the schema is left to ``on_signup``, as before 0.305.0."""

        async def fill_nickname(
            hook_session: AsyncSession, user: Any, payload: Any
        ) -> None:
            """Write the nickname the way pre-0.305.0 services did.

            Args:
                hook_session (AsyncSession): The request session.
                user (Any): The created user.
                payload (Any): The validated signup body.
            """
            user.nickname = payload.nickname

        app = _legacy_app(
            session, signup_schema=_NicknameSignupSchema, on_signup=fill_nickname
        )
        async with _client(app) as client:
            response = await client.post(
                "/auth/signup",
                json={
                    "email": "ana@example.com",
                    "password": PASSWORD,
                    "nickname": "ana",
                },
            )

        assert response.status_code == 201
        user = (await session.execute(select(_NicknameUser))).scalar_one()
        assert user.nickname == "ana"

    def test_the_skipped_columns_are_named_at_router_build(
        self, session: AsyncSession, caplog: pytest.LogCaptureFixture
    ) -> None:
        """Mounting the router says which columns fall back to ``on_signup``."""
        with caplog.at_level("WARNING", logger="tempest_fastapi_sdk.auth.router"):
            _legacy_app(session, signup_schema=_NicknameSignupSchema)

        assert "_LegacySignupService.signup() does not accept 'fields'" in caplog.text
        assert "['nickname']" in caplog.text
