"""Tests for the refresh-token flow on ``UserAuthService`` + bundled router."""

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
    ForbiddenException,
    InvalidTokenException,
)
from tempest_fastapi_sdk.settings.mixins import AuthSettings, JWTSettings


class _RefreshUser(BaseUserModel):
    __tablename__ = "refresh_test_users"


_RefreshUserToken = make_user_token_model(
    user_table="refresh_test_users",
    tablename="refresh_test_user_tokens",
    class_name="_RefreshUserToken",
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


def _service() -> UserAuthService:
    auth = AuthSettings(AUTH_AUTO_ACTIVATE=True)
    jwt = JWTSettings(JWT_SECRET="x" * 32)
    return UserAuthService(
        user_model=_RefreshUser,
        token_model=_RefreshUserToken,  # type: ignore[arg-type]
        auth_settings=auth,
        jwt_settings=jwt,
        email=None,
    )


async def _make_user(
    service: UserAuthService,
    session: AsyncSession,
    *,
    email: str = "refresh@a.com",
    password: str = "strong-pass-12-chars",
) -> Any:
    user, _ = await service.signup(session, email=email, password=password)
    await session.commit()
    return user


def _client(service: UserAuthService, session: AsyncSession) -> AsyncClient:
    async def _factory() -> AsyncIterator[AsyncSession]:
        yield session

    app = FastAPI()
    app.include_router(make_auth_router(service, session_factory=_factory))
    return AsyncClient(transport=ASGITransport(app=app), base_url="http://t")


class TestRefreshService:
    async def test_refresh_tokens_returns_fresh_pair(
        self,
        session: AsyncSession,
    ) -> None:
        service = _service()
        user = await _make_user(service, session)
        _access, refresh = service.issue_jwt_pair(user)

        out_user, new_access, new_refresh = await service.refresh_tokens(
            session, refresh_token=refresh
        )

        assert out_user.id == user.id
        assert new_access
        assert new_refresh
        # The new pair decodes and the new refresh still carries the claim.
        assert service.jwt.decode(new_refresh)["refresh"] is True
        assert service.jwt.decode(new_access)["sub"] == str(user.id)

    async def test_access_token_is_rejected(
        self,
        session: AsyncSession,
    ) -> None:
        service = _service()
        user = await _make_user(service, session, email="access-replay@a.com")
        access, _refresh = service.issue_jwt_pair(user)

        with pytest.raises(InvalidTokenException):
            await service.refresh_tokens(session, refresh_token=access)

    async def test_garbage_token_is_rejected(
        self,
        session: AsyncSession,
    ) -> None:
        service = _service()
        with pytest.raises(InvalidTokenException):
            await service.refresh_tokens(session, refresh_token="not-a-jwt")

    async def test_inactive_user_is_forbidden(
        self,
        session: AsyncSession,
    ) -> None:
        service = _service()
        user = await _make_user(service, session, email="inactive@a.com")
        _access, refresh = service.issue_jwt_pair(user)
        user.is_active = False
        await session.commit()

        with pytest.raises(ForbiddenException):
            await service.refresh_tokens(session, refresh_token=refresh)


class TestRefreshRouter:
    async def test_login_then_refresh_returns_new_pair(
        self,
        session: AsyncSession,
    ) -> None:
        service = _service()
        await _make_user(service, session, email="router-refresh@a.com")

        async with _client(service, session) as c:
            login = await c.post(
                "/auth/login",
                json={
                    "email": "router-refresh@a.com",
                    "password": "strong-pass-12-chars",
                },
            )
            assert login.status_code == 200, login.text
            refresh_token = login.json()["refresh_token"]
            assert refresh_token

            r = await c.post(
                "/auth/refresh",
                json={"refresh_token": refresh_token},
            )

        assert r.status_code == 200, r.text
        body = r.json()
        assert body["access_token"]
        assert body["refresh_token"]
        assert body["mfa_required"] is False

    async def test_refresh_rejects_access_token(
        self,
        session: AsyncSession,
    ) -> None:
        service = _service()
        user = await _make_user(service, session, email="router-replay@a.com")
        access, _refresh = service.issue_jwt_pair(user)

        async with _client(service, session) as c:
            r = await c.post("/auth/refresh", json={"refresh_token": access})

        assert r.status_code == 401, r.text

    async def test_refresh_rejects_garbage(
        self,
        session: AsyncSession,
    ) -> None:
        service = _service()
        await _make_user(service, session, email="router-garbage@a.com")

        async with _client(service, session) as c:
            r = await c.post("/auth/refresh", json={"refresh_token": "nope"})

        assert r.status_code == 401, r.text
