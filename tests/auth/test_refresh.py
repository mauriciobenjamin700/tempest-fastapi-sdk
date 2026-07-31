"""Tests for the refresh-token flow on ``UserAuthService`` + bundled router."""

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
    ACCESS_TOKEN_TYPE,
    MFA_TOKEN_TYPE,
    REFRESH_TOKEN_TYPE,
    BaseModel,
    BaseUserModel,
    UserAuthService,
    make_auth_router,
    make_jwt_user_dependency,
    make_user_token_model,
    register_exception_handlers,
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


class TestIssuedTokenTypes:
    """Every token the service mints declares what it is.

    All three are signed with the same secret, so a route guard that only
    reads ``sub`` would treat any of them as a session. The ``typ`` claim is
    what keeps the refresh token and the MFA-pending token out.
    """

    async def test_access_token_declares_access(
        self,
        session: AsyncSession,
    ) -> None:
        service = _service()
        user = await _make_user(service, session, email="typ-access@a.com")
        access, _refresh = service.issue_jwt_pair(user)
        assert service.jwt.decode(access)["typ"] == ACCESS_TOKEN_TYPE

    async def test_refresh_token_declares_refresh(
        self,
        session: AsyncSession,
    ) -> None:
        service = _service()
        user = await _make_user(service, session, email="typ-refresh@a.com")
        _access, refresh = service.issue_jwt_pair(user)
        claims = service.jwt.decode(refresh)
        assert claims["typ"] == REFRESH_TOKEN_TYPE
        assert claims["refresh"] is True

    async def test_mfa_token_declares_mfa(
        self,
        session: AsyncSession,
    ) -> None:
        service = _service()
        user = await _make_user(service, session, email="typ-mfa@a.com")
        claims = service.jwt.decode(service.issue_mfa_token(user))
        assert claims["typ"] == MFA_TOKEN_TYPE
        assert claims["purpose"] == "mfa_pending"

    async def test_issue_token_pair_declares_both(
        self,
        session: AsyncSession,
    ) -> None:
        service = _service()
        user = await _make_user(service, session, email="typ-pair@a.com")
        access, refresh = await service.issue_token_pair(session, user)
        assert service.jwt.decode(access)["typ"] == ACCESS_TOKEN_TYPE
        assert service.jwt.decode(refresh)["typ"] == REFRESH_TOKEN_TYPE

    async def test_refresh_flow_accepts_a_typ_only_token(
        self,
        session: AsyncSession,
    ) -> None:
        """A token carrying ``typ`` but not the legacy ``refresh`` flag works."""
        service = _service()
        user = await _make_user(service, session, email="typ-only@a.com")
        token = service.jwt.encode(
            {"sub": str(user.id), "typ": REFRESH_TOKEN_TYPE},
        )

        out_user, access, refresh = await service.refresh_tokens(
            session, refresh_token=token
        )

        assert out_user.id == user.id
        assert access and refresh

    async def test_mfa_token_cannot_authorize_a_route(
        self,
        session: AsyncSession,
    ) -> None:
        """Step one of a two-step login must not hand out a usable session."""
        service = _service()
        user = await _make_user(service, session, email="mfa-replay@a.com")
        mfa_token = service.issue_mfa_token(user)

        current_user = make_jwt_user_dependency(
            service.jwt,
            lambda subject: _load_by_id(session, subject),
        )
        app = FastAPI()
        register_exception_handlers(app)

        @app.get("/me")
        async def me(loaded: Any = Depends(current_user)) -> dict[str, str]:
            return {"id": str(loaded.id)}

        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://t") as client:
            denied = await client.get(
                "/me", headers={"Authorization": f"Bearer {mfa_token}"}
            )
            allowed = await client.get(
                "/me",
                headers={
                    "Authorization": f"Bearer {service._encode_access(user)}",
                },
            )

        assert denied.status_code == 401
        assert allowed.status_code == 200
        assert allowed.json() == {"id": str(user.id)}


async def _load_by_id(session: AsyncSession, subject: str) -> Any:
    """Resolve a user id string to its row on the given session."""
    from uuid import UUID

    return await session.get(_RefreshUser, UUID(subject))
