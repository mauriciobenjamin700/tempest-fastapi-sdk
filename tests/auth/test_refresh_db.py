"""Tests for the DB-backed (opaque) refresh-token flow.

Exercises rotation, single-use enforcement, reuse detection
(family revoke), expiry, and the ``POST /auth/logout`` endpoint
that is mounted only when ``refresh_token_model`` is wired.
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from typing import Any

import pytest
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient
from sqlalchemy import func, select
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
    make_user_refresh_token_model,
    make_user_token_model,
)
from tempest_fastapi_sdk.exceptions import (
    ForbiddenException,
    InvalidTokenException,
)
from tempest_fastapi_sdk.settings.mixins import AuthSettings, JWTSettings


class _RefreshDBUser(BaseUserModel):
    __tablename__ = "refresh_db_users"


_RefreshDBUserToken = make_user_token_model(
    user_table="refresh_db_users",
    tablename="refresh_db_user_tokens",
    class_name="_RefreshDBUserToken",
)

_RefreshDBRefreshToken = make_user_refresh_token_model(
    user_table="refresh_db_users",
    tablename="refresh_db_refresh_tokens",
    class_name="_RefreshDBRefreshToken",
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
        user_model=_RefreshDBUser,
        token_model=_RefreshDBUserToken,  # type: ignore[arg-type]
        auth_settings=auth,
        jwt_settings=jwt,
        email=None,
        refresh_token_model=_RefreshDBRefreshToken,  # type: ignore[arg-type]
    )


async def _make_user(
    service: UserAuthService,
    session: AsyncSession,
    *,
    email: str = "refresh-db@a.com",
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


async def _row_count(session: AsyncSession) -> int:
    result = await session.execute(
        select(func.count()).select_from(_RefreshDBRefreshToken)
    )
    return int(result.scalar_one())


class TestRefreshDBService:
    async def test_issue_token_pair_persists_opaque_token(
        self,
        session: AsyncSession,
    ) -> None:
        service = _service()
        user = await _make_user(session=session, service=service)

        access, refresh = await service.issue_token_pair(session, user)
        await session.commit()

        assert access
        assert refresh
        # The opaque refresh token is NOT a JWT — it cannot be decoded.
        with pytest.raises(InvalidTokenException):
            service.jwt.decode(refresh)
        assert await _row_count(session) == 1

    async def test_rotation_marks_old_used_and_mints_new(
        self,
        session: AsyncSession,
    ) -> None:
        service = _service()
        user = await _make_user(session=session, service=service, email="rotate@a.com")
        _access, refresh = await service.issue_token_pair(session, user)
        await session.commit()

        out_user, new_access, new_refresh = await service.refresh_tokens(
            session, refresh_token=refresh
        )
        await session.commit()

        assert out_user.id == user.id
        assert new_access
        assert new_refresh != refresh
        # Two rows now: the rotated (used) one + the fresh one.
        assert await _row_count(session) == 2

    async def test_old_token_is_single_use(
        self,
        session: AsyncSession,
    ) -> None:
        service = _service()
        user = await _make_user(
            session=session, service=service, email="single-use@a.com"
        )
        _access, refresh = await service.issue_token_pair(session, user)
        await session.commit()

        await service.refresh_tokens(session, refresh_token=refresh)
        await session.commit()

        # Replaying the now-rotated token is reuse → rejected.
        with pytest.raises(InvalidTokenException):
            await service.refresh_tokens(session, refresh_token=refresh)

    async def test_reuse_revokes_whole_family(
        self,
        session: AsyncSession,
    ) -> None:
        service = _service()
        user = await _make_user(session=session, service=service, email="reuse@a.com")
        _access, refresh1 = await service.issue_token_pair(session, user)
        await session.commit()

        # Rotate once → refresh2 is the live descendant of the family.
        _user, _access2, refresh2 = await service.refresh_tokens(
            session, refresh_token=refresh1
        )
        await session.commit()

        # Replay the OLD token → reuse detected → family revoked.
        with pytest.raises(InvalidTokenException):
            await service.refresh_tokens(session, refresh_token=refresh1)
        await session.commit()

        # The still-valid descendant is now dead too (family killed).
        with pytest.raises(InvalidTokenException):
            await service.refresh_tokens(session, refresh_token=refresh2)

    async def test_unknown_token_rejected(
        self,
        session: AsyncSession,
    ) -> None:
        service = _service()
        with pytest.raises(InvalidTokenException):
            await service.refresh_tokens(session, refresh_token="nope-not-real")

    async def test_inactive_user_forbidden(
        self,
        session: AsyncSession,
    ) -> None:
        service = _service()
        user = await _make_user(
            session=session, service=service, email="inactive-db@a.com"
        )
        _access, refresh = await service.issue_token_pair(session, user)
        await session.commit()
        user.is_active = False
        await session.commit()

        with pytest.raises(ForbiddenException):
            await service.refresh_tokens(session, refresh_token=refresh)

    async def test_revoke_family_blocks_refresh(
        self,
        session: AsyncSession,
    ) -> None:
        service = _service()
        user = await _make_user(
            session=session, service=service, email="revoke-family@a.com"
        )
        _access, refresh = await service.issue_token_pair(session, user)
        await session.commit()

        await service.revoke_refresh_token(session, refresh_token=refresh)
        await session.commit()

        with pytest.raises(InvalidTokenException):
            await service.refresh_tokens(session, refresh_token=refresh)

    async def test_revoke_all_sessions(
        self,
        session: AsyncSession,
    ) -> None:
        service = _service()
        user = await _make_user(
            session=session, service=service, email="revoke-all@a.com"
        )
        _a, refresh_a = await service.issue_token_pair(session, user)
        _b, refresh_b = await service.issue_token_pair(session, user)
        await session.commit()

        # Distinct families (two independent logins).
        await service.revoke_refresh_token(
            session, refresh_token=refresh_a, all_sessions=True
        )
        await session.commit()

        for token in (refresh_a, refresh_b):
            with pytest.raises(InvalidTokenException):
                await service.refresh_tokens(session, refresh_token=token)

    async def test_revoke_unknown_token_is_noop(
        self,
        session: AsyncSession,
    ) -> None:
        service = _service()
        # Must not raise — logout stays idempotent.
        await service.revoke_refresh_token(session, refresh_token="ghost")
        await session.commit()


class TestRefreshDBRouter:
    async def test_login_refresh_logout_cycle(
        self,
        session: AsyncSession,
    ) -> None:
        service = _service()
        await _make_user(session=session, service=service, email="router-db@a.com")

        async with _client(service, session) as c:
            login = await c.post(
                "/auth/login",
                json={"email": "router-db@a.com", "password": "strong-pass-12-chars"},
            )
            assert login.status_code == 200, login.text
            refresh1 = login.json()["refresh_token"]
            assert refresh1

            r = await c.post("/auth/refresh", json={"refresh_token": refresh1})
            assert r.status_code == 200, r.text
            refresh2 = r.json()["refresh_token"]
            assert refresh2 != refresh1

            # Reusing the rotated token → 401.
            replay = await c.post("/auth/refresh", json={"refresh_token": refresh1})
            assert replay.status_code == 401, replay.text

            # Family killed → the descendant is dead too.
            dead = await c.post("/auth/refresh", json={"refresh_token": refresh2})
            assert dead.status_code == 401, dead.text

    async def test_logout_revokes_session(
        self,
        session: AsyncSession,
    ) -> None:
        service = _service()
        await _make_user(session=session, service=service, email="router-logout@a.com")

        async with _client(service, session) as c:
            login = await c.post(
                "/auth/login",
                json={
                    "email": "router-logout@a.com",
                    "password": "strong-pass-12-chars",
                },
            )
            refresh = login.json()["refresh_token"]

            logout = await c.post("/auth/logout", json={"refresh_token": refresh})
            assert logout.status_code == 204, logout.text

            after = await c.post("/auth/refresh", json={"refresh_token": refresh})
            assert after.status_code == 401, after.text


class TestStatelessLogoutAbsent:
    """Without ``refresh_token_model`` the /logout route is not mounted."""

    async def test_logout_not_mounted_in_stateless_mode(
        self,
        session: AsyncSession,
    ) -> None:
        auth = AuthSettings(AUTH_AUTO_ACTIVATE=True)
        jwt = JWTSettings(JWT_SECRET="x" * 32)
        service = UserAuthService(
            user_model=_RefreshDBUser,
            token_model=_RefreshDBUserToken,  # type: ignore[arg-type]
            auth_settings=auth,
            jwt_settings=jwt,
            email=None,
        )
        async with _client(service, session) as c:
            r = await c.post("/auth/logout", json={"refresh_token": "x"})
        assert r.status_code == 404, r.text
