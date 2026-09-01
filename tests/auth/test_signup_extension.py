"""``signup_schema`` + ``on_signup`` — signup for an account that is not
just email, password and name.

Every product's account carries something at birth the SDK cannot know
about: a role flag, a phone, a tax document. Before these two seams the
only way to write those was to leave ``POST /auth/signup`` unmounted and
hand-roll a local route, which then had to re-derive the password
policy, the duplicate-email conflict, the activation branch and the
token pair — four things the bundled route already gets right, drifting
from it one release at a time.

The load-bearing test here is
:meth:`TestTheHookSharesTheInsertTransaction.test_a_raising_hook_leaves_no_account`.
A hook that runs *after* the commit would be a second transaction, so a
rejected domain field would leave behind an account with an email, a
password and none of the fields that make it usable — reachable by
password reset, invisible to the product. Running before the commit is
what makes signup atomic.
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from typing import Any

import pytest
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient
from pydantic import Field
from sqlalchemy import Boolean, String, select
from sqlalchemy.ext.asyncio import (
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)
from sqlalchemy.orm import Mapped, mapped_column

from tempest_fastapi_sdk import (
    BaseModel,
    BaseUserModel,
    SignupSchema,
    UserAuthService,
    make_auth_router,
    make_user_token_model,
    register_exception_handlers,
)
from tempest_fastapi_sdk.settings.mixins import AuthSettings, JWTSettings

_PASSWORD = "Str0ng-pass-12!"


class _ProfileUser(BaseUserModel):
    __tablename__ = "signup_ext_users"

    phone: Mapped[str | None] = mapped_column(String(20), nullable=True)
    is_producer: Mapped[bool] = mapped_column(Boolean, default=False)


_ProfileUserToken = make_user_token_model(
    user_table="signup_ext_users",
    tablename="signup_ext_user_tokens",
    class_name="_ProfileUserToken",
)


class _ProfileSignupSchema(SignupSchema):
    """What an AloFans-shaped account carries at birth.

    Attributes:
        phone (str | None): Contact number, optional at signup.
        is_producer (bool): Whether the account is a producer.
    """

    phone: str | None = Field(default=None, max_length=20)
    is_producer: bool = Field(default=False)


class _NotASignupSchema(BaseModel):  # type: ignore[misc]
    """Deliberately unrelated to :class:`SignupSchema`."""


@pytest.fixture
async def session() -> AsyncIterator[AsyncSession]:
    """Yield one session over a fresh in-memory database."""
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
        UserAuthService: Configured over the local profile model.
    """
    return UserAuthService(
        user_model=_ProfileUser,
        token_model=_ProfileUserToken,  # type: ignore[arg-type]
        auth_settings=AuthSettings(
            _env_file=None,
            AUTH_AUTO_ACTIVATE=True,
            AUTH_PASSWORD_MIN_LENGTH=8,
        ),
        jwt_settings=JWTSettings(_env_file=None, JWT_SECRET="x" * 32),
    )


def _app(
    session: AsyncSession,
    **router_kwargs: Any,
) -> FastAPI:
    """Mount the auth router with ``router_kwargs`` on a throwaway app.

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


async def _write_profile(
    session: AsyncSession,
    user: _ProfileUser,
    payload: _ProfileSignupSchema,
) -> None:
    """Copy the extra signup fields onto the freshly created row.

    Args:
        session (AsyncSession): The transaction the row was inserted in.
        user (_ProfileUser): The instance to write onto.
        payload (_ProfileSignupSchema): The validated body.
    """
    del session
    user.phone = payload.phone
    user.is_producer = payload.is_producer


class TestTheExtendedBodyIsTheAdvertisedContract:
    """A subclass reaches OpenAPI, not just the handler."""

    async def test_extra_fields_are_in_the_request_schema(
        self, session: AsyncSession
    ) -> None:
        app = _app(session, signup_schema=_ProfileSignupSchema)

        schema = app.openapi()
        ref = schema["paths"]["/auth/signup"]["post"]["requestBody"]["content"][
            "application/json"
        ]["schema"]["$ref"]
        name = ref.rsplit("/", 1)[-1]

        assert name == "_ProfileSignupSchema"
        properties = schema["components"]["schemas"][name]["properties"]
        assert {"email", "password", "name", "phone", "is_producer"} <= set(properties)

    async def test_the_default_body_is_untouched(self, session: AsyncSession) -> None:
        app = _app(session)

        schema = app.openapi()
        ref = schema["paths"]["/auth/signup"]["post"]["requestBody"]["content"][
            "application/json"
        ]["schema"]["$ref"]

        assert ref.rsplit("/", 1)[-1] == "SignupSchema"

    async def test_a_body_that_is_not_a_signup_schema_is_refused(
        self, session: AsyncSession
    ) -> None:
        with pytest.raises(RuntimeError, match="must subclass SignupSchema"):
            _app(session, signup_schema=_NotASignupSchema)


class TestTheHookWritesTheProductColumns:
    """The fields the subclass added end up on the row."""

    async def test_extra_columns_are_persisted(self, session: AsyncSession) -> None:
        app = _app(
            session,
            signup_schema=_ProfileSignupSchema,
            on_signup=_write_profile,
        )

        async with _client(app) as client:
            response = await client.post(
                "/auth/signup",
                json={
                    "email": "ana@example.com",
                    "password": _PASSWORD,
                    "name": "Ana",
                    "phone": "5511999999999",
                    "is_producer": True,
                },
            )

        assert response.status_code == 201
        body = response.json()
        assert body["activation_required"] is False
        assert body["access_token"]
        row = (
            await session.execute(
                select(_ProfileUser).where(_ProfileUser.email == "ana@example.com")
            )
        ).scalar_one()
        assert row.phone == "5511999999999"
        assert row.is_producer is True

    async def test_without_a_hook_the_extra_fields_are_simply_not_written(
        self, session: AsyncSession
    ) -> None:
        app = _app(session, signup_schema=_ProfileSignupSchema)

        async with _client(app) as client:
            response = await client.post(
                "/auth/signup",
                json={
                    "email": "bruno@example.com",
                    "password": _PASSWORD,
                    "is_producer": True,
                },
            )

        assert response.status_code == 201
        row = (
            await session.execute(
                select(_ProfileUser).where(_ProfileUser.email == "bruno@example.com")
            )
        ).scalar_one()
        assert row.is_producer is False


class TestTheHookSharesTheInsertTransaction:
    """Signup stays atomic: no account survives a rejected field."""

    async def test_a_raising_hook_leaves_no_account(
        self, session: AsyncSession
    ) -> None:
        async def _reject(
            session: AsyncSession,
            user: BaseUserModel,
            payload: SignupSchema,
        ) -> None:
            """Refuse every signup.

            Args:
                session (AsyncSession): Unused.
                user (BaseUserModel): Unused.
                payload (SignupSchema): Unused.

            Raises:
                ValueError: Always.
            """
            del session, user, payload
            raise ValueError("domain field rejected")

        app = _app(
            session,
            signup_schema=_ProfileSignupSchema,
            on_signup=_reject,
        )

        async with _client(app) as client:
            with pytest.raises(ValueError, match="domain field rejected"):
                await client.post(
                    "/auth/signup",
                    json={
                        "email": "carla@example.com",
                        "password": _PASSWORD,
                    },
                )

        await session.rollback()
        found = (
            await session.execute(
                select(_ProfileUser).where(_ProfileUser.email == "carla@example.com")
            )
        ).scalar_one_or_none()
        assert found is None

    async def test_the_hook_runs_before_the_commit(self, session: AsyncSession) -> None:
        seen: list[bool] = []

        async def _record(
            session: AsyncSession,
            user: BaseUserModel,
            payload: SignupSchema,
        ) -> None:
            """Record whether the row is still uncommitted.

            Args:
                session (AsyncSession): The transaction under test.
                user (BaseUserModel): Unused.
                payload (SignupSchema): Unused.
            """
            del user, payload
            seen.append(session.in_transaction())

        app = _app(session, on_signup=_record)

        async with _client(app) as client:
            response = await client.post(
                "/auth/signup",
                json={"email": "dora@example.com", "password": _PASSWORD},
            )

        assert response.status_code == 201
        assert seen == [True]
