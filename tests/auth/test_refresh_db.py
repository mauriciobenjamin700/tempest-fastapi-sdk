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


class TestReuseRevocationSurvivesTheUnwind:
    """The revocation has to outlive the exception that follows it.

    ``_lookup_refresh_record`` used to ``flush()`` the family revocation and
    raise. In a FastAPI request the exception travels out through the session
    dependency's teardown, the unit of work is rolled back, and the
    revocation goes with it: the replay is refused with 401 while every
    descendant token keeps working.

    ``test_reuse_revokes_whole_family`` above cannot see that. It commits
    **after** the ``pytest.raises`` block, on the same session, which is the
    one thing a real request never does. These cases put a session boundary
    where the request has one.
    """

    @pytest.fixture
    def factory(self, tmp_path: Any) -> Any:
        """Build a session factory over a file-backed SQLite database.

        Args:
            tmp_path (Any): pytest's per-test temporary directory.

        Returns:
            Any: An ``async_sessionmaker`` whose sessions see each other's
            commits — which ``sqlite+aiosqlite:///:memory:`` does not give,
            since every connection there opens its own empty database.
        """
        engine = create_async_engine(f"sqlite+aiosqlite:///{tmp_path / 'auth.db'}")
        return async_sessionmaker(engine, expire_on_commit=False)

    async def _seed_replayed_family(self, factory: Any) -> tuple[Any, str, str]:
        """Create a user, rotate once, and return the replayable pair.

        Args:
            factory (Any): The session factory.

        Returns:
            tuple[Any, str, str]: The service, the consumed token, and the
            live descendant token.
        """
        service = _service()
        async with factory() as setup:
            connection = await setup.connection()
            await connection.run_sync(BaseModel.metadata.create_all)
            await setup.commit()

        async with factory() as session:
            user = await _make_user(
                session=session, service=service, email="unwind@a.com"
            )
            _access, consumed = await service.issue_token_pair(session, user)
            await session.commit()

        async with factory() as session:
            _user, _access2, descendant = await service.refresh_tokens(
                session, refresh_token=consumed
            )
            await session.commit()

        return service, consumed, descendant

    async def test_revocation_persists_across_the_session_boundary(
        self,
        factory: Any,
    ) -> None:
        """Every row in the family comes back revoked, from a fresh session."""
        service, consumed, _descendant = await self._seed_replayed_family(factory)

        with pytest.raises(InvalidTokenException):
            async with factory() as request_session:
                await service.refresh_tokens(request_session, refresh_token=consumed)

        async with factory() as check:
            rows = (await check.execute(select(_RefreshDBRefreshToken))).scalars().all()
            assert rows
            assert all(row.revoked_at is not None for row in rows)

    async def test_the_descendant_is_refused_after_the_unwind(
        self,
        factory: Any,
    ) -> None:
        """The point of revoking the family, checked where it matters."""
        service, consumed, descendant = await self._seed_replayed_family(factory)

        with pytest.raises(InvalidTokenException):
            async with factory() as request_session:
                await service.refresh_tokens(request_session, refresh_token=consumed)

        with pytest.raises(InvalidTokenException):
            async with factory() as later:
                await service.refresh_tokens(later, refresh_token=descendant)

    async def test_other_staged_writes_are_not_committed(
        self,
        factory: Any,
    ) -> None:
        """The security decision does not drag the request's work with it.

        Committing the caller's session wholesale would persist whatever
        else the request had staged, as a side effect of refusing it. The
        revocation rolls that back first, so only the revocation lands.
        """
        service, consumed, _descendant = await self._seed_replayed_family(factory)

        with pytest.raises(InvalidTokenException):
            async with factory() as request_session:
                request_session.add(
                    _RefreshDBUser(
                        email="staged@a.com",
                        hashed_password="not-a-real-hash",
                    )
                )
                await request_session.flush()
                await service.refresh_tokens(request_session, refresh_token=consumed)

        async with factory() as check:
            staged = await check.execute(
                select(_RefreshDBUser).where(_RefreshDBUser.email == "staged@a.com")
            )
            assert staged.scalar_one_or_none() is None
            rows = (await check.execute(select(_RefreshDBRefreshToken))).scalars().all()
            assert all(row.revoked_at is not None for row in rows)


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
