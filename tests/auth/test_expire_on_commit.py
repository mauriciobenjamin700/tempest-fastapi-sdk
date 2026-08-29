"""The four success pages under the session default nobody changes.

``async_sessionmaker`` defaults to ``expire_on_commit=True``. Under it, the
commit that consumes the token expires the identity map, and the page that
renders next reads an expired column — I/O outside the greenlet, which async
answers with ``MissingGreenlet`` and the user sees a 500 on a flow that
already succeeded.

Every other fixture in this suite builds the factory with
``expire_on_commit=False`` (so does the SDK's own ``AsyncDatabaseManager``),
which is exactly why nothing caught it. This module builds the factory the
way the SQLAlchemy default does.

The ``refresh`` calls in the setup are the *test's* problem, not the SDK's:
these tests reuse one session across setup and request, so a row loaded
before a commit is expired by the time the setup calls the next service
method. A real request loads its user through a dependency, inside the
request, so it never sees that. What is under test starts at the HTTP call.
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


class _ExpiringUser(BaseUserModel):
    __tablename__ = "auth_expiring_users"


_ExpiringUserToken = make_user_token_model(
    user_table="auth_expiring_users",
    tablename="auth_expiring_user_tokens",
    class_name="_ExpiringUserToken",
)


@pytest.fixture
async def expiring_session() -> AsyncIterator[AsyncSession]:
    """Yield a session from a factory using the SQLAlchemy default.

    Yields:
        AsyncSession: A session whose commits expire every loaded row.
    """
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as conn:
        await conn.run_sync(BaseModel.metadata.create_all)
    factory = async_sessionmaker(engine, expire_on_commit=True)
    async with factory() as session:
        yield session
    await engine.dispose()


def _service() -> UserAuthService:
    """Build a service with the backend HTML pages mounted.

    Returns:
        UserAuthService: Wired against the module's user model, handing the
        token back in the response so the tests can drive the pages.
    """
    return UserAuthService(
        user_model=_ExpiringUser,
        token_model=_ExpiringUserToken,  # type: ignore[arg-type]
        auth_settings=AuthSettings(
            AUTH_AUTO_ACTIVATE=False,
            AUTH_RETURN_TOKEN_IN_RESPONSE=True,
            AUTH_BACKEND_LINKS=True,
            AUTH_DEFAULT_LOCALE="en-US",
        ),
        jwt_settings=JWTSettings(JWT_SECRET="x" * 32),
    )


def _app(service: UserAuthService, session: AsyncSession) -> FastAPI:
    """Mount the auth router over one session.

    Args:
        service (UserAuthService): The service under test.
        session (AsyncSession): The session every request reuses.

    Returns:
        FastAPI: An app carrying the auth router.
    """

    async def _factory() -> AsyncIterator[AsyncSession]:
        yield session

    app = FastAPI()
    app.include_router(make_auth_router(service, session_factory=_factory))
    return app


class TestPagesSurviveAnExpiringCommit:
    async def test_activation_page(self, expiring_session: AsyncSession) -> None:
        """The page the report opened: 500 before, 200 now."""
        service = _service()
        _user, activation = await service.signup(
            expiring_session, email="act@x.com", password="strong-pass-12"
        )
        await expiring_session.commit()
        assert activation is not None

        async with AsyncClient(
            transport=ASGITransport(app=_app(service, expiring_session)),
            base_url="http://t",
        ) as client:
            response = await client.get(f"/auth/activate/{activation.token}")
        assert response.status_code == 200, response.text
        assert "act@x.com" in response.text

    async def test_password_reset_submit_page(
        self, expiring_session: AsyncSession
    ) -> None:
        """The only reset route that commits before rendering."""
        service = _service()
        user, _activation = await service.signup(
            expiring_session, email="reset@x.com", password="strong-pass-12"
        )
        user.is_active = True
        await expiring_session.commit()
        reset = await service.request_password_reset(
            expiring_session, email="reset@x.com"
        )
        await expiring_session.commit()
        assert reset is not None

        async with AsyncClient(
            transport=ASGITransport(app=_app(service, expiring_session)),
            base_url="http://t",
        ) as client:
            response = await client.post(
                f"/auth/password-reset/{reset.token}",
                data={
                    "new_password": "another-strong-1",
                    "confirm_password": "another-strong-1",
                },
            )
        assert response.status_code == 200, response.text

    async def test_email_change_page(self, expiring_session: AsyncSession) -> None:
        """Confirming a staged address commits, then renders the row."""
        service = _service()
        user, _activation = await service.signup(
            expiring_session, email="chg@x.com", password="strong-pass-12"
        )
        user.is_active = True
        await expiring_session.commit()
        await expiring_session.refresh(user)
        change = await service.request_email_change(
            expiring_session,
            user=user,
            current_password="strong-pass-12",
            new_email="chg-new@x.com",
        )
        await expiring_session.commit()
        assert change is not None

        async with AsyncClient(
            transport=ASGITransport(app=_app(service, expiring_session)),
            base_url="http://t",
        ) as client:
            response = await client.get(f"/auth/email-change/{change.token}")
        assert response.status_code == 200, response.text
        assert "chg-new@x.com" in response.text

    async def test_email_verification_page(
        self, expiring_session: AsyncSession
    ) -> None:
        """Re-verification marks the row active, then renders it."""
        service = _service()
        user, _activation = await service.signup(
            expiring_session, email="ver@x.com", password="strong-pass-12"
        )
        await expiring_session.commit()
        await expiring_session.refresh(user)
        verification = await service.request_email_verification(
            expiring_session, user=user
        )
        await expiring_session.commit()
        assert verification is not None

        async with AsyncClient(
            transport=ASGITransport(app=_app(service, expiring_session)),
            base_url="http://t",
        ) as client:
            response = await client.get(f"/auth/email-verify/{verification.token}")
        assert response.status_code == 200, response.text
        assert "ver@x.com" in response.text
