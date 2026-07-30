"""Regression tests: the auth router's user must be attached to the route session.

``make_auth_router`` used to build its authenticated-user dependency without a
``session_dependency``, so the loader opened a private session, fetched the user
and returned it after that session closed. Every authenticated route of the
router then held a **detached** instance: mutating it and calling
``session.flush()`` wrote nothing (the instance is absent from that session's
identity map) and the following ``session.refresh(user)`` raised
``InvalidRequestError: Instance is not persistent within this Session``.
``POST /auth/password-change`` failed exactly that way in production — 500 on the
response *and* the old password still valid.

The existing router tests could not catch it: they wire a
``session_factory`` that ``yield``s one shared ``AsyncSession`` object, so the
loader's "private" session and the route's session were literally the same
object. These tests use a factory that opens a **new** session per call, which is
what ``AsyncDatabaseManager.session_dependency`` does in a real app.
"""

from __future__ import annotations

from collections.abc import AsyncIterator

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
from tempest_fastapi_sdk.settings.mixins import AuthSettings, JWTSettings


class _SharingUser(BaseUserModel):
    __tablename__ = "session_sharing_users"


_SharingUserToken = make_user_token_model(
    user_table="session_sharing_users",
    tablename="session_sharing_user_tokens",
    class_name="_SharingUserToken",
)

CURRENT_PASSWORD = "strong-pass-12-chars"
NEW_PASSWORD = "brand-new-pass-12"


class RequestScopedSessions:
    """A session factory that opens a fresh session per dependency resolution.

    Mirrors ``AsyncDatabaseManager.session_dependency``: each call yields a new
    ``AsyncSession`` and closes it when the request ends. The shared-session wiring
    is only observable against this — a factory yielding one long-lived session
    hides the bug, which is why the original tests passed while production broke.
    """

    def __init__(self, factory: async_sessionmaker[AsyncSession]) -> None:
        self.factory = factory
        self.opened = 0

    async def __call__(self) -> AsyncIterator[AsyncSession]:
        """Yield a brand-new session and commit it on a clean exit."""
        self.opened += 1
        async with self.factory() as session:
            yield session
            await session.commit()


@pytest.fixture
async def sessions() -> AsyncIterator[RequestScopedSessions]:
    """Provide a per-request session factory over a shared in-memory database.

    A ``StaticPool``-free file-less SQLite database would give each connection its
    own empty schema, so the engine is created once and reused across sessions via
    the default pool, which keeps a single in-memory connection alive.
    """
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as conn:
        await conn.run_sync(BaseModel.metadata.create_all)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    yield RequestScopedSessions(factory)
    await engine.dispose()


def _service() -> UserAuthService:
    """Build an auth service over the local user model, auto-activating signups."""
    return UserAuthService(
        user_model=_SharingUser,
        token_model=_SharingUserToken,  # type: ignore[arg-type]
        auth_settings=AuthSettings(
            AUTH_AUTO_ACTIVATE=True,
            AUTH_RETURN_TOKEN_IN_RESPONSE=True,
        ),
        jwt_settings=JWTSettings(JWT_SECRET="x" * 32),
    )


async def _seed_user(
    service: UserAuthService, sessions: RequestScopedSessions, email: str
) -> str:
    """Create an active user and return an access token for them."""
    async with sessions.factory() as session:
        user, _ = await service.signup(session, email=email, password=CURRENT_PASSWORD)
        await session.commit()
        access, _ = service.issue_jwt_pair(user)
    return access


def _client(service: UserAuthService, sessions: RequestScopedSessions) -> AsyncClient:
    """Mount the auth router on the per-request session factory."""
    app = FastAPI()
    app.include_router(make_auth_router(service, session_factory=sessions))
    return AsyncClient(transport=ASGITransport(app=app), base_url="http://t")


class TestPasswordChangePersists:
    """``POST /auth/password-change`` must answer 204 and actually rotate."""

    async def test_returns_204_instead_of_500(
        self, sessions: RequestScopedSessions
    ) -> None:
        """The detached instance used to blow up on ``session.refresh``."""
        service = _service()
        access = await _seed_user(service, sessions, "pwd-204@b.com")

        async with _client(service, sessions) as client:
            response = await client.post(
                "/auth/password-change",
                json={
                    "current_password": CURRENT_PASSWORD,
                    "new_password": NEW_PASSWORD,
                },
                headers={"Authorization": f"Bearer {access}"},
            )

        assert response.status_code == 204, response.text

    async def test_new_password_is_persisted(
        self, sessions: RequestScopedSessions
    ) -> None:
        """The write must survive the request, not vanish on a foreign flush."""
        service = _service()
        access = await _seed_user(service, sessions, "pwd-persist@b.com")

        async with _client(service, sessions) as client:
            await client.post(
                "/auth/password-change",
                json={
                    "current_password": CURRENT_PASSWORD,
                    "new_password": NEW_PASSWORD,
                },
                headers={"Authorization": f"Bearer {access}"},
            )

        async with sessions.factory() as session:
            await service.login(
                session, email="pwd-persist@b.com", password=NEW_PASSWORD
            )

    async def test_old_password_stops_working(
        self, sessions: RequestScopedSessions
    ) -> None:
        """The rotation is real: the previous password no longer authenticates.

        Asserted separately from the new password because a silently-skipped
        write leaves *both* passing the login check for the old value — the
        symptom users reported as "password change does nothing".
        """
        service = _service()
        access = await _seed_user(service, sessions, "pwd-old@b.com")

        async with _client(service, sessions) as client:
            await client.post(
                "/auth/password-change",
                json={
                    "current_password": CURRENT_PASSWORD,
                    "new_password": NEW_PASSWORD,
                },
                headers={"Authorization": f"Bearer {access}"},
            )

        async with sessions.factory() as session:
            with pytest.raises(Exception) as raised:
                await service.login(
                    session, email="pwd-old@b.com", password=CURRENT_PASSWORD
                )
            assert raised.value.__class__.__name__ == "UnauthorizedException"

    async def test_wrong_current_password_is_rejected(
        self, sessions: RequestScopedSessions
    ) -> None:
        """The re-auth check still guards the rotation."""
        service = _service()
        access = await _seed_user(service, sessions, "pwd-wrong@b.com")

        async with _client(service, sessions) as client:
            response = await client.post(
                "/auth/password-change",
                json={
                    "current_password": "not-the-current-one",
                    "new_password": NEW_PASSWORD,
                },
                headers={"Authorization": f"Bearer {access}"},
            )

        assert response.status_code == 401, response.text
        async with sessions.factory() as session:
            await service.login(
                session, email="pwd-wrong@b.com", password=CURRENT_PASSWORD
            )


class TestUserIsAttached:
    """The dependency and the route body must resolve to one session."""

    async def test_loader_reuses_the_route_session(
        self, sessions: RequestScopedSessions
    ) -> None:
        """One request opens one session, not one per dependency.

        FastAPI caches a sub-dependency by its callable, so sharing the session
        depends on the router passing the *same* callable the routes use. Counting
        the sessions a single request opens is what proves the wiring — a second
        session would mean the user is detached again.
        """
        service = _service()
        access = await _seed_user(service, sessions, "attached@b.com")
        before = sessions.opened

        async with _client(service, sessions) as client:
            response = await client.post(
                "/auth/password-change",
                json={
                    "current_password": CURRENT_PASSWORD,
                    "new_password": NEW_PASSWORD,
                },
                headers={"Authorization": f"Bearer {access}"},
            )

        assert response.status_code == 204, response.text
        assert sessions.opened - before == 1
