"""Tests for ``UserAuthService`` + ``make_auth_router``."""

from __future__ import annotations

from collections.abc import AsyncIterator
from typing import Any

import pytest
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient
from sqlalchemy.ext.asyncio import (
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)

from tempest_fastapi_sdk import (
    BaseModel,
    BaseUserModel,
    UserAuthService,
    make_auth_router,
    make_user_token_model,
)
from tempest_fastapi_sdk.exceptions import (
    ConflictException,
    InvalidTokenException,
    UnauthorizedException,
    ValidationException,
)
from tempest_fastapi_sdk.settings.mixins import AuthSettings, JWTSettings


class _TestUser(BaseUserModel):
    __tablename__ = "auth_test_users"


_TestUserToken = make_user_token_model(
    user_table="auth_test_users",
    tablename="auth_test_user_tokens",
    class_name="_TestUserToken",
)


@pytest.fixture
async def session() -> AsyncIterator[AsyncSession]:
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as conn:
        await conn.run_sync(BaseModel.metadata.create_all)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    async with factory() as s:
        yield s
    await engine.dispose()


def _service(
    *,
    auto_activate: bool = False,
    return_token: bool = True,
    email: Any = None,
) -> UserAuthService:
    auth = AuthSettings(
        AUTH_AUTO_ACTIVATE=auto_activate,
        AUTH_RETURN_TOKEN_IN_RESPONSE=return_token,
    )
    jwt = JWTSettings(JWT_SECRET="x" * 32)
    return UserAuthService(
        user_model=_TestUser,
        token_model=_TestUserToken,  # type: ignore[arg-type]
        auth_settings=auth,
        jwt_settings=jwt,
        email=email,
    )


class TestSignup:
    async def test_creates_user_with_hashed_password(
        self,
        session: AsyncSession,
    ) -> None:
        service = _service()
        user, activation = await service.signup(
            session,
            email="ANA@example.com",
            password="strong-pass-12-chars",
        )
        assert user.email == "ana@example.com"  # normalized lowercase
        assert user.hashed_password != "strong-pass-12-chars"
        assert user.is_active is False
        assert activation is not None
        assert "token=" in activation.url

    async def test_auto_activate_returns_no_token(
        self,
        session: AsyncSession,
    ) -> None:
        service = _service(auto_activate=True)
        user, activation = await service.signup(
            session,
            email="a@b.com",
            password="strong-pass-12-chars",
        )
        assert user.is_active is True
        assert activation is None

    async def test_duplicate_email_rejected(
        self,
        session: AsyncSession,
    ) -> None:
        service = _service()
        await service.signup(
            session,
            email="dup@b.com",
            password="strong-pass-12-chars",
        )
        await session.commit()
        with pytest.raises(ConflictException):
            await service.signup(
                session,
                email="dup@b.com",
                password="strong-pass-12-chars",
            )

    async def test_short_password_rejected(
        self,
        session: AsyncSession,
    ) -> None:
        service = _service()
        with pytest.raises(ValidationException):
            await service.signup(
                session,
                email="a@b.com",
                password="short",
            )


class TestActivate:
    async def test_consumes_token_and_activates_user(
        self,
        session: AsyncSession,
    ) -> None:
        service = _service()
        _user, activation = await service.signup(
            session,
            email="a@b.com",
            password="strong-pass-12-chars",
        )
        assert activation is not None

        activated = await service.activate(session, token=activation.token)
        assert activated.is_active is True

    async def test_reused_token_rejected(
        self,
        session: AsyncSession,
    ) -> None:
        service = _service()
        _user, activation = await service.signup(
            session,
            email="a@b.com",
            password="strong-pass-12-chars",
        )
        assert activation is not None

        await service.activate(session, token=activation.token)
        with pytest.raises(InvalidTokenException):
            await service.activate(session, token=activation.token)

    async def test_unknown_token_rejected(
        self,
        session: AsyncSession,
    ) -> None:
        service = _service()
        with pytest.raises(InvalidTokenException):
            await service.activate(session, token="nope-never-issued")


class TestLogin:
    async def test_valid_credentials_returns_user(
        self,
        session: AsyncSession,
    ) -> None:
        service = _service(auto_activate=True)
        await service.signup(
            session,
            email="a@b.com",
            password="strong-pass-12-chars",
        )
        await session.commit()
        user = await service.login(
            session,
            email="a@b.com",
            password="strong-pass-12-chars",
        )
        assert user.email == "a@b.com"
        assert user.last_login_at is not None

    async def test_wrong_password_rejected(
        self,
        session: AsyncSession,
    ) -> None:
        service = _service(auto_activate=True)
        await service.signup(
            session,
            email="a@b.com",
            password="strong-pass-12-chars",
        )
        with pytest.raises(UnauthorizedException):
            await service.login(
                session,
                email="a@b.com",
                password="wrong-pass-12-chars",
            )

    async def test_inactive_user_rejected(
        self,
        session: AsyncSession,
    ) -> None:
        service = _service()
        await service.signup(
            session,
            email="a@b.com",
            password="strong-pass-12-chars",
        )
        with pytest.raises(UnauthorizedException):
            await service.login(
                session,
                email="a@b.com",
                password="strong-pass-12-chars",
            )


class TestPasswordReset:
    async def test_request_reset_returns_token_when_user_exists(
        self,
        session: AsyncSession,
    ) -> None:
        service = _service(auto_activate=True)
        await service.signup(
            session,
            email="a@b.com",
            password="strong-pass-12-chars",
        )
        token = await service.request_password_reset(session, email="a@b.com")
        assert token is not None
        assert "token=" in token.url

    async def test_request_reset_returns_none_for_unknown_email(
        self,
        session: AsyncSession,
    ) -> None:
        service = _service()
        token = await service.request_password_reset(
            session,
            email="ghost@nowhere.example",
        )
        assert token is None

    async def test_confirm_reset_rotates_password(
        self,
        session: AsyncSession,
    ) -> None:
        service = _service(auto_activate=True)
        await service.signup(
            session,
            email="a@b.com",
            password="strong-pass-12-chars",
        )
        token = await service.request_password_reset(session, email="a@b.com")
        assert token is not None

        await service.confirm_password_reset(
            session,
            token=token.token,
            new_password="brand-new-pass-12",
        )
        # Old password no longer works
        with pytest.raises(UnauthorizedException):
            await service.login(
                session,
                email="a@b.com",
                password="strong-pass-12-chars",
            )
        # New password works
        await service.login(
            session,
            email="a@b.com",
            password="brand-new-pass-12",
        )


class TestRouter:
    async def test_signup_endpoint_returns_activation_url(
        self,
        session: AsyncSession,
    ) -> None:
        service = _service()

        async def _factory() -> AsyncIterator[AsyncSession]:
            yield session

        app = FastAPI()
        app.include_router(make_auth_router(service, session_factory=_factory))

        async with AsyncClient(
            transport=ASGITransport(app=app), base_url="http://t"
        ) as c:
            r = await c.post(
                "/auth/signup",
                json={
                    "email": "router@a.com",
                    "password": "strong-pass-12-chars",
                },
            )
        assert r.status_code == 201, r.text
        body = r.json()
        assert body["activation_required"] is True
        assert "token=" in body["activation_url"]
        assert body["access_token"] is None

    async def test_signup_endpoint_auto_activate_returns_tokens(
        self,
        session: AsyncSession,
    ) -> None:
        service = _service(auto_activate=True)

        async def _factory() -> AsyncIterator[AsyncSession]:
            yield session

        app = FastAPI()
        app.include_router(make_auth_router(service, session_factory=_factory))

        async with AsyncClient(
            transport=ASGITransport(app=app), base_url="http://t"
        ) as c:
            r = await c.post(
                "/auth/signup",
                json={
                    "email": "auto@a.com",
                    "password": "strong-pass-12-chars",
                },
            )
        assert r.status_code == 201, r.text
        body = r.json()
        assert body["activation_required"] is False
        assert body["access_token"]
        assert body["refresh_token"]

    async def test_password_reset_request_never_leaks(
        self,
        session: AsyncSession,
    ) -> None:
        service = _service()

        async def _factory() -> AsyncIterator[AsyncSession]:
            yield session

        app = FastAPI()
        app.include_router(make_auth_router(service, session_factory=_factory))

        async with AsyncClient(
            transport=ASGITransport(app=app), base_url="http://t"
        ) as c:
            r = await c.post(
                "/auth/password-reset/request",
                json={"email": "ghost@nowhere.example"},
            )
        assert r.status_code == 202
        body = r.json()
        # Generic 202 always — no leak of "user not found".
        assert "matches an account" in body["message"]
        assert body["reset_url"] is None
