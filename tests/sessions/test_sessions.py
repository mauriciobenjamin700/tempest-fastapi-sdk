"""Tests for the server-side session module."""

from __future__ import annotations

from collections.abc import AsyncIterator
from datetime import timedelta
from uuid import uuid4

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
    MemorySessionStore,
    Session,
    SessionAuth,
    SessionMiddleware,
    SessionSettings,
    make_session_router,
)
from tempest_fastapi_sdk.utils.datetime import utcnow
from tempest_fastapi_sdk.utils.password import PasswordUtils


class _SessionTestUser(BaseUserModel):
    __tablename__ = "session_test_users"


@pytest.fixture
async def session() -> AsyncIterator[AsyncSession]:
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as conn:
        await conn.run_sync(BaseModel.metadata.create_all)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    async with factory() as s:
        yield s
    await engine.dispose()


def _settings(**overrides: object) -> SessionSettings:
    defaults: dict[str, object] = {
        "SESSION_TTL_SECONDS": 60,
        "SESSION_COOKIE_SECURE": False,  # tests run over HTTP
        "SESSION_SLIDING": True,
        "SESSION_ROTATE_ON_LOGIN": True,
    }
    defaults.update(overrides)
    return SessionSettings(**defaults)  # type: ignore[arg-type]


# ---------------------------------------------------------------------------
# MemorySessionStore
# ---------------------------------------------------------------------------


def _make_session(*, user_id: object | None = None, ttl: int = 60) -> Session:
    now = utcnow()
    return Session(
        session_id="a" * 64,
        user_id=user_id or uuid4(),
        created_at=now,
        expires_at=now + timedelta(seconds=ttl),
        last_seen_at=now,
        ip="127.0.0.1",
        user_agent="pytest",
        data={},
    )


class TestMemorySessionStore:
    async def test_set_then_get_returns_session(self) -> None:
        store = MemorySessionStore()
        s = _make_session()
        await store.set(s)
        loaded = await store.get(s.session_id)
        assert loaded is not None
        assert loaded.session_id == s.session_id

    async def test_get_returns_none_when_expired(self) -> None:
        store = MemorySessionStore()
        s = _make_session(ttl=-10)
        await store.set(s)
        loaded = await store.get(s.session_id)
        assert loaded is None

    async def test_delete_is_idempotent(self) -> None:
        store = MemorySessionStore()
        s = _make_session()
        await store.set(s)
        await store.delete(s.session_id)
        await store.delete(s.session_id)
        assert await store.get(s.session_id) is None

    async def test_delete_by_user_removes_every_session(self) -> None:
        store = MemorySessionStore()
        uid = uuid4()
        s1 = _make_session(user_id=uid)
        s2 = _make_session(user_id=uid)
        s2.session_id = "b" * 64
        await store.set(s1)
        await store.set(s2)
        count = await store.delete_by_user(uid)
        assert count == 2
        assert await store.list_by_user(uid) == []

    async def test_list_by_user_returns_oldest_first(self) -> None:
        store = MemorySessionStore()
        uid = uuid4()
        s1 = _make_session(user_id=uid)
        s2 = _make_session(user_id=uid)
        s2.session_id = "b" * 64
        s2.created_at = s1.created_at + timedelta(seconds=1)
        await store.set(s1)
        await store.set(s2)
        listed = await store.list_by_user(uid)
        assert [s.session_id for s in listed] == [s1.session_id, s2.session_id]


# ---------------------------------------------------------------------------
# SessionAuth
# ---------------------------------------------------------------------------


async def _make_active_user(
    session: AsyncSession,
    *,
    email: str = "ana@example.com",
    password: str = "strong-pass-12-chars",
) -> _SessionTestUser:
    passwords = PasswordUtils()
    user = _SessionTestUser(
        email=email,
        is_active=True,
    )
    user.hashed_password = passwords.hash(password)
    session.add(user)
    await session.flush()
    await session.refresh(user)
    return user


def _auth(store: MemorySessionStore | None = None, **overrides: object) -> SessionAuth:
    return SessionAuth(
        user_model=_SessionTestUser,
        store=store or MemorySessionStore(),
        settings=_settings(**overrides),
    )


class TestSessionAuth:
    async def test_authenticate_rejects_wrong_password(
        self,
        session: AsyncSession,
    ) -> None:
        await _make_active_user(session)
        await session.commit()
        from tempest_fastapi_sdk.exceptions import UnauthorizedException

        with pytest.raises(UnauthorizedException):
            await _auth().authenticate(
                session,
                email="ana@example.com",
                password="wrong-pass-12-chars",
            )

    async def test_login_mints_session_and_returns_plaintext(
        self,
        session: AsyncSession,
    ) -> None:
        store = MemorySessionStore()
        service = _auth(store=store)
        user = await _make_active_user(session)
        s, plaintext = await service.login(user_id=user.id)
        assert plaintext
        assert s.user_id == user.id
        listed = await store.list_by_user(user.id)
        assert len(listed) == 1

    async def test_resolve_slides_ttl(
        self,
        session: AsyncSession,
    ) -> None:
        store = MemorySessionStore()
        service = _auth(store=store, SESSION_TTL_SECONDS=3600)
        user = await _make_active_user(session)
        original, plaintext = await service.login(user_id=user.id)
        resolved = await service.resolve(plaintext)
        assert resolved is not None
        assert resolved.expires_at >= original.expires_at

    async def test_resolve_returns_none_for_unknown_cookie(self) -> None:
        store = MemorySessionStore()
        service = _auth(store=store)
        assert await service.resolve("garbage") is None

    async def test_login_rotates_previous_session(
        self,
        session: AsyncSession,
    ) -> None:
        store = MemorySessionStore()
        service = _auth(store=store)
        user = await _make_active_user(session)
        _first, plain1 = await service.login(user_id=user.id)
        _second, plain2 = await service.login(
            user_id=user.id,
            previous_session_id=plain1,
        )
        # First session should be revoked.
        assert await service.resolve(plain1) is None
        assert await service.resolve(plain2) is not None

    async def test_revoke_all_clears_user_sessions(
        self,
        session: AsyncSession,
    ) -> None:
        store = MemorySessionStore()
        service = _auth(store=store)
        user = await _make_active_user(session)
        await service.login(user_id=user.id)
        await service.login(user_id=user.id)
        count = await service.revoke_all(user.id)
        assert count == 2
        assert await store.list_by_user(user.id) == []


# ---------------------------------------------------------------------------
# Router integration
# ---------------------------------------------------------------------------


def _build_app(
    *,
    auth: SessionAuth,
    session_factory_callable: object,
) -> FastAPI:
    app = FastAPI()
    app.add_middleware(
        SessionMiddleware,
        session_auth=auth,
        settings=auth.settings,
    )
    app.include_router(
        make_session_router(
            auth,
            session_factory=session_factory_callable,  # type: ignore[arg-type]
        )
    )
    return app


class TestSessionRouter:
    async def test_login_sets_cookie_and_returns_user_id(
        self,
        session: AsyncSession,
    ) -> None:
        user = await _make_active_user(session)
        await session.commit()

        async def _factory() -> AsyncIterator[AsyncSession]:
            yield session

        auth = _auth()
        app = _build_app(auth=auth, session_factory_callable=_factory)
        async with AsyncClient(
            transport=ASGITransport(app=app), base_url="http://t"
        ) as c:
            r = await c.post(
                "/auth/session/login",
                json={
                    "email": "ana@example.com",
                    "password": "strong-pass-12-chars",
                },
            )
        assert r.status_code == 200, r.text
        body = r.json()
        assert body["user_id"] == str(user.id)
        assert "tempest_session" in r.headers.get("set-cookie", "")

    async def test_me_returns_session_after_login(
        self,
        session: AsyncSession,
    ) -> None:
        user = await _make_active_user(session)
        await session.commit()

        async def _factory() -> AsyncIterator[AsyncSession]:
            yield session

        auth = _auth()
        app = _build_app(auth=auth, session_factory_callable=_factory)
        async with AsyncClient(
            transport=ASGITransport(app=app), base_url="http://t"
        ) as c:
            await c.post(
                "/auth/session/login",
                json={
                    "email": "ana@example.com",
                    "password": "strong-pass-12-chars",
                },
            )
            r = await c.get("/auth/session/me")
        assert r.status_code == 200, r.text
        assert r.json()["user_id"] == str(user.id)

    async def test_me_returns_401_without_cookie(
        self,
        session: AsyncSession,
    ) -> None:
        async def _factory() -> AsyncIterator[AsyncSession]:
            yield session

        auth = _auth()
        app = _build_app(auth=auth, session_factory_callable=_factory)
        from tempest_fastapi_sdk import register_exception_handlers

        register_exception_handlers(app)
        async with AsyncClient(
            transport=ASGITransport(app=app), base_url="http://t"
        ) as c:
            r = await c.get("/auth/session/me")
        assert r.status_code == 401

    async def test_logout_clears_cookie_and_revokes_session(
        self,
        session: AsyncSession,
    ) -> None:
        await _make_active_user(session)
        await session.commit()

        async def _factory() -> AsyncIterator[AsyncSession]:
            yield session

        store = MemorySessionStore()
        auth = _auth(store=store)
        app = _build_app(auth=auth, session_factory_callable=_factory)
        async with AsyncClient(
            transport=ASGITransport(app=app), base_url="http://t"
        ) as c:
            await c.post(
                "/auth/session/login",
                json={
                    "email": "ana@example.com",
                    "password": "strong-pass-12-chars",
                },
            )
            r = await c.post("/auth/session/logout")
        assert r.status_code == 204
        # Cookie cleared (Max-Age=0)
        assert "max-age=0" in r.headers.get("set-cookie", "").lower()

    async def test_list_includes_current_session(
        self,
        session: AsyncSession,
    ) -> None:
        await _make_active_user(session)
        await session.commit()

        async def _factory() -> AsyncIterator[AsyncSession]:
            yield session

        auth = _auth()
        app = _build_app(auth=auth, session_factory_callable=_factory)
        async with AsyncClient(
            transport=ASGITransport(app=app), base_url="http://t"
        ) as c:
            await c.post(
                "/auth/session/login",
                json={
                    "email": "ana@example.com",
                    "password": "strong-pass-12-chars",
                },
            )
            r = await c.get("/auth/session/list")
        assert r.status_code == 200, r.text
        rows = r.json()
        assert len(rows) == 1
        assert rows[0]["is_current"] is True
