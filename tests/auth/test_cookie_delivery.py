"""Tests for the token-delivery modes (bearer / cookie / both).

Covers ``make_auth_router``'s ``AUTH_TOKEN_DELIVERY`` behaviour: bearer
(body only), cookie (HttpOnly cookies + body omits tokens) and both
(bearer at ``/auth/*`` plus a parallel cookie set at ``/auth/cookie/*``),
including cookie-based refresh, logout and the dependency reading the
access token from the cookie.

Cookies are created with ``AUTH_COOKIE_SECURE=False`` so httpx's cookie
jar sends them back over the test's plain-``http`` base URL — a ``Secure``
cookie would be dropped, exactly as a real browser drops it over HTTP.
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from typing import Any

import pytest
from fastapi import Depends, FastAPI
from httpx import ASGITransport, AsyncClient
from sqlalchemy.ext.asyncio import (
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)

from tempest_fastapi_sdk import (
    AsyncDatabaseManager,
    BaseModel,
    BaseUserModel,
    TokenDelivery,
    UserAuthService,
    make_auth_router,
    make_user_token_model,
)
from tempest_fastapi_sdk.settings.mixins import AuthSettings, JWTSettings


class _CookieUser(BaseUserModel):
    __tablename__ = "cookie_test_users"


_CookieUserToken = make_user_token_model(
    user_table="cookie_test_users",
    tablename="cookie_test_user_tokens",
    class_name="_CookieUserToken",
)


class _DepUser(BaseUserModel):
    __tablename__ = "cookie_dep_users"


_DepUserToken = make_user_token_model(
    user_table="cookie_dep_users",
    tablename="cookie_dep_user_tokens",
    class_name="_DepUserToken",
)

_PASSWORD = "strong-pass-12-chars"


@pytest.fixture
async def session() -> AsyncIterator[AsyncSession]:
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as conn:
        await conn.run_sync(BaseModel.metadata.create_all)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    async with factory() as s:
        yield s
    await engine.dispose()


def _service(delivery: TokenDelivery) -> UserAuthService:
    auth = AuthSettings(
        AUTH_AUTO_ACTIVATE=True,
        AUTH_TOKEN_DELIVERY=delivery,
        AUTH_COOKIE_SECURE=False,
    )
    jwt = JWTSettings(JWT_SECRET="x" * 32)
    return UserAuthService(
        user_model=_CookieUser,
        token_model=_CookieUserToken,  # type: ignore[arg-type]
        auth_settings=auth,
        jwt_settings=jwt,
        email=None,
    )


async def _make_user(
    service: UserAuthService,
    session: AsyncSession,
    *,
    email: str,
) -> Any:
    user, _ = await service.signup(session, email=email, password=_PASSWORD)
    await session.commit()
    return user


def _client(service: UserAuthService, session: AsyncSession) -> AsyncClient:
    async def _factory() -> AsyncIterator[AsyncSession]:
        yield session

    app = FastAPI()
    app.include_router(make_auth_router(service, session_factory=_factory))
    return AsyncClient(transport=ASGITransport(app=app), base_url="http://t")


class TestBearerMode:
    async def test_login_returns_tokens_in_body_no_cookies(
        self,
        session: AsyncSession,
    ) -> None:
        service = _service("bearer")
        await _make_user(service, session, email="bearer@a.com")

        async with _client(service, session) as c:
            r = await c.post(
                "/auth/login",
                json={"email": "bearer@a.com", "password": _PASSWORD},
            )

        assert r.status_code == 200, r.text
        body = r.json()
        assert body["access_token"]
        assert body["refresh_token"]
        assert "access_token" not in r.cookies
        assert "refresh_token" not in r.cookies

    async def test_cookie_endpoints_absent(
        self,
        session: AsyncSession,
    ) -> None:
        service = _service("bearer")
        await _make_user(service, session, email="bearer-absent@a.com")

        async with _client(service, session) as c:
            r = await c.post(
                "/auth/cookie/login",
                json={"email": "bearer-absent@a.com", "password": _PASSWORD},
            )

        assert r.status_code == 404, r.text


class TestCookieMode:
    async def test_login_sets_cookies_and_omits_body_tokens(
        self,
        session: AsyncSession,
    ) -> None:
        service = _service("cookie")
        await _make_user(service, session, email="cookie@a.com")

        async with _client(service, session) as c:
            r = await c.post(
                "/auth/login",
                json={"email": "cookie@a.com", "password": _PASSWORD},
            )

        assert r.status_code == 200, r.text
        body = r.json()
        assert body["access_token"] is None
        assert body["refresh_token"] is None
        assert r.cookies.get("access_token")
        assert r.cookies.get("refresh_token")

    async def test_refresh_reads_cookie_and_rotates(
        self,
        session: AsyncSession,
    ) -> None:
        service = _service("cookie")
        await _make_user(service, session, email="cookie-refresh@a.com")

        async with _client(service, session) as c:
            login = await c.post(
                "/auth/login",
                json={"email": "cookie-refresh@a.com", "password": _PASSWORD},
            )
            assert login.status_code == 200, login.text

            # No body needed — the refresh cookie rides along automatically.
            r = await c.post("/auth/refresh")

        assert r.status_code == 200, r.text
        # A fresh pair is set back as cookies and decodes to the same user.
        new_access = r.cookies.get("access_token")
        new_refresh = r.cookies.get("refresh_token")
        assert new_access
        assert new_refresh
        assert service.jwt.decode(new_refresh)["refresh"] is True
        assert service.jwt.decode(new_access)["email"] == "cookie-refresh@a.com"

    async def test_refresh_without_cookie_is_unauthorized(
        self,
        session: AsyncSession,
    ) -> None:
        service = _service("cookie")
        await _make_user(service, session, email="cookie-norefresh@a.com")

        async with _client(service, session) as c:
            r = await c.post("/auth/refresh")

        assert r.status_code == 401, r.text

    async def test_dependency_reads_access_cookie(
        self,
        session: AsyncSession,
    ) -> None:
        service = _service("cookie")
        await _make_user(service, session, email="cookie-dep@a.com")

        async with _client(service, session) as c:
            await c.post(
                "/auth/login",
                json={"email": "cookie-dep@a.com", "password": _PASSWORD},
            )
            # password-change is guarded by current_user_dep; the access
            # cookie alone (no Authorization header) must authenticate it.
            r = await c.post(
                "/auth/password-change",
                json={
                    "current_password": _PASSWORD,
                    "new_password": "another-strong-pass-12",
                },
            )

        assert r.status_code == 204, r.text

    async def test_logout_clears_cookies(
        self,
        session: AsyncSession,
    ) -> None:
        service = _service("cookie")
        await _make_user(service, session, email="cookie-logout@a.com")

        async with _client(service, session) as c:
            await c.post(
                "/auth/login",
                json={"email": "cookie-logout@a.com", "password": _PASSWORD},
            )
            assert c.cookies.get("access_token")

            logout = await c.post("/auth/logout")
            assert logout.status_code == 204, logout.text

            # After logout the jar dropped the cookies, so a guarded
            # endpoint now rejects the request.
            r = await c.post(
                "/auth/password-change",
                json={
                    "current_password": _PASSWORD,
                    "new_password": "another-strong-pass-12",
                },
            )

        assert r.status_code == 401, r.text


class TestBothMode:
    async def test_bearer_path_returns_body_tokens(
        self,
        session: AsyncSession,
    ) -> None:
        service = _service("both")
        await _make_user(service, session, email="both-bearer@a.com")

        async with _client(service, session) as c:
            r = await c.post(
                "/auth/login",
                json={"email": "both-bearer@a.com", "password": _PASSWORD},
            )

        assert r.status_code == 200, r.text
        body = r.json()
        assert body["access_token"]
        assert body["refresh_token"]
        assert "access_token" not in r.cookies

    async def test_cookie_path_sets_cookies(
        self,
        session: AsyncSession,
    ) -> None:
        service = _service("both")
        await _make_user(service, session, email="both-cookie@a.com")

        async with _client(service, session) as c:
            r = await c.post(
                "/auth/cookie/login",
                json={"email": "both-cookie@a.com", "password": _PASSWORD},
            )

        assert r.status_code == 200, r.text
        body = r.json()
        assert body["access_token"] is None
        assert r.cookies.get("access_token")
        assert r.cookies.get("refresh_token")

    async def test_cookie_refresh_path_rotates(
        self,
        session: AsyncSession,
    ) -> None:
        service = _service("both")
        await _make_user(service, session, email="both-refresh@a.com")

        async with _client(service, session) as c:
            await c.post(
                "/auth/cookie/login",
                json={"email": "both-refresh@a.com", "password": _PASSWORD},
            )
            r = await c.post("/auth/cookie/refresh")

        assert r.status_code == 200, r.text
        assert r.cookies.get("access_token")


class TestCurrentUserDependencyCookie:
    """service.current_user_dependency() must honour cookie delivery.

    A business route guarded by the dependency should authenticate off
    the access cookie the bundled login set — with no Authorization
    header — when AUTH_TOKEN_DELIVERY is cookie/both.
    """

    async def test_protected_route_reads_access_cookie(self) -> None:
        db = AsyncDatabaseManager("sqlite+aiosqlite:///:memory:")
        await db.connect()
        await db.create_tables()
        try:
            service = UserAuthService(
                user_model=_DepUser,
                token_model=_DepUserToken,  # type: ignore[arg-type]
                auth_settings=AuthSettings(
                    AUTH_AUTO_ACTIVATE=True,
                    AUTH_TOKEN_DELIVERY="both",
                    AUTH_COOKIE_SECURE=False,
                ),
                jwt_settings=JWTSettings(JWT_SECRET="x" * 32),
                email=None,
                db=db,
            )
            async with db.get_session_context() as s:
                await service.signup(s, email="dep@a.com", password=_PASSWORD)
                await s.commit()

            app = FastAPI()
            app.include_router(
                make_auth_router(service, session_factory=db.session_dependency)
            )
            current_user = service.current_user_dependency()

            @app.get("/me")
            async def me(user: _DepUser = Depends(current_user)) -> dict[str, str]:
                return {"email": user.email}

            transport = ASGITransport(app=app)
            async with AsyncClient(transport=transport, base_url="http://t") as c:
                # No cookie yet → guarded route rejects.
                anon = await c.get("/me")
                assert anon.status_code == 401, anon.text

                await c.post(
                    "/auth/cookie/login",
                    json={"email": "dep@a.com", "password": _PASSWORD},
                )
                # Access cookie alone (no Authorization header) authenticates.
                ok = await c.get("/me")

            assert ok.status_code == 200, ok.text
            assert ok.json()["email"] == "dep@a.com"
        finally:
            await db.drop_tables()
            await db.disconnect()
