"""The password reset revokes the sessions it leaves behind (#534).

Rotating a credential ends the sessions opened with the old one, as
``mfa_confirm`` and ``tempest user set-password`` already do. A reset that
leaves every refresh token exchangeable is not a reset: whoever stole the
password keeps the session even after the victim changes it. The bundled
``/auth/password-reset/confirm`` route keeps the default; ``revoke_sessions=
False`` is the explicit opt-out for a caller that ends the sessions its own
way.
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from urllib.parse import parse_qs, urlparse

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
    make_user_refresh_token_model,
    make_user_token_model,
    register_exception_handlers,
)
from tempest_fastapi_sdk.exceptions import UnauthorizedException
from tempest_fastapi_sdk.settings.mixins import AuthSettings, JWTSettings

PASSWORD: str = "strong-pass-12-chars"
NEW_PASSWORD: str = "brand-new-pass-12-chars"


class _RevokeUser(BaseUserModel):
    __tablename__ = "reset_revoke_test_users"


_RevokeUserToken = make_user_token_model(
    user_table="reset_revoke_test_users",
    tablename="reset_revoke_test_user_tokens",
    class_name="_RevokeUserToken",
)
_RevokeRefreshToken = make_user_refresh_token_model(
    user_table="reset_revoke_test_users",
    tablename="reset_revoke_test_refresh_tokens",
    class_name="_RevokeRefreshToken",
)


class _LegacyResetService(UserAuthService):
    """Override of ``confirm_password_reset`` written before the kwarg.

    The signature the subscriber had before ``revoke_sessions`` existed:
    no such parameter, and it delegates to ``super()`` the way every
    override does. The router must keep calling it.
    """

    async def confirm_password_reset(
        self,
        session: AsyncSession,
        *,
        token: str,
        new_password: str,
    ) -> BaseUserModel:
        """Delegate to the base implementation unchanged.

        Args:
            session (AsyncSession): Active SQLAlchemy session.
            token (str): Plaintext reset token.
            new_password (str): Plaintext replacement password.

        Returns:
            BaseUserModel: The user whose password was rotated.
        """
        return await super().confirm_password_reset(
            session, token=token, new_password=new_password
        )


@pytest.fixture
async def session() -> AsyncIterator[AsyncSession]:
    """Yield a session over a fresh in-memory database.

    Yields:
        AsyncSession: The session every request of a test shares.
    """
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as conn:
        await conn.run_sync(BaseModel.metadata.create_all)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    async with factory() as s:
        yield s
    await engine.dispose()


def _service(
    *,
    db_refresh: bool = True,
    service_class: type[UserAuthService] = UserAuthService,
) -> UserAuthService:
    """Build an auth service whose reset links come back in the body.

    Args:
        db_refresh (bool): Wire the DB-backed refresh tokens. ``False``
            keeps the stateless JWT refresh token, which has no row to
            revoke.
        service_class (type[UserAuthService]): The class to instantiate.

    Returns:
        UserAuthService: The configured service.
    """
    return service_class(
        user_model=_RevokeUser,
        token_model=_RevokeUserToken,  # type: ignore[arg-type]
        auth_settings=AuthSettings(
            _env_file=None,
            AUTH_AUTO_ACTIVATE=True,
            AUTH_RETURN_TOKEN_IN_RESPONSE=True,
        ),
        jwt_settings=JWTSettings(_env_file=None, JWT_SECRET="x" * 32),
        email=None,
        refresh_token_model=_RevokeRefreshToken if db_refresh else None,  # type: ignore[arg-type]
    )


def _client(service: UserAuthService, session: AsyncSession) -> AsyncClient:
    """Mount the auth router on a throwaway app.

    Args:
        service (UserAuthService): The configured service.
        session (AsyncSession): The session every request shares.

    Returns:
        AsyncClient: A client bound to the app over ASGI.
    """

    async def _factory() -> AsyncIterator[AsyncSession]:
        yield session

    app = FastAPI()
    register_exception_handlers(app)
    app.include_router(make_auth_router(service, session_factory=_factory))
    return AsyncClient(transport=ASGITransport(app=app), base_url="http://t")


async def _make_session(
    service: UserAuthService, session: AsyncSession, email: str
) -> str:
    """Create an active account and return its refresh token.

    Args:
        service (UserAuthService): The configured service.
        session (AsyncSession): The session to write through.
        email (str): The account address.

    Returns:
        str: The refresh token minted at signup.
    """
    user, _ = await service.signup(session, email=email, password=PASSWORD)
    _, refresh = await service.issue_token_pair(session, user)
    await session.commit()
    return refresh


async def _reset_token(
    service: UserAuthService, session: AsyncSession, email: str
) -> str:
    """Issue a reset token and return its plaintext.

    Args:
        service (UserAuthService): The configured service.
        session (AsyncSession): The session to write through.
        email (str): The account address.

    Returns:
        str: The plaintext single-use reset token.
    """
    token = await service.request_password_reset(session, email=email)
    await session.commit()
    assert token is not None
    return token.token


class TestResetRevokesSessions:
    async def test_the_pre_reset_refresh_token_is_refused(
        self, session: AsyncSession
    ) -> None:
        """The probe from the issue: a session opened before the reset dies."""
        service = _service()
        async with _client(service, session) as client:
            await client.post(
                "/auth/signup",
                json={"email": "revoke@a.com", "password": PASSWORD},
            )
            login = await client.post(
                "/auth/login",
                json={"email": "revoke@a.com", "password": PASSWORD},
            )
            stale_refresh = login.json()["refresh_token"]
            requested = await client.post(
                "/auth/password-reset/request",
                json={"email": "revoke@a.com"},
            )
            token = parse_qs(urlparse(requested.json()["reset_url"]).query)["token"][0]

            confirmation = await client.post(
                "/auth/password-reset/confirm",
                json={"token": token, "new_password": NEW_PASSWORD},
            )
            assert confirmation.status_code == 200, confirmation.text
            assert confirmation.json()["access_token"]

            replay = await client.post(
                "/auth/refresh",
                json={"refresh_token": stale_refresh},
            )
            relogin = await client.post(
                "/auth/login",
                json={"email": "revoke@a.com", "password": NEW_PASSWORD},
            )
        assert replay.status_code == 401, replay.text
        assert relogin.status_code == 200, relogin.text

    async def test_a_legacy_override_still_delegates_to_the_default(
        self, session: AsyncSession
    ) -> None:
        """An override without the kwarg keeps working — and still revokes."""
        service = _service(service_class=_LegacyResetService)
        async with _client(service, session) as client:
            await client.post(
                "/auth/signup",
                json={"email": "legacy-revoke@a.com", "password": PASSWORD},
            )
            login = await client.post(
                "/auth/login",
                json={"email": "legacy-revoke@a.com", "password": PASSWORD},
            )
            stale_refresh = login.json()["refresh_token"]
            requested = await client.post(
                "/auth/password-reset/request",
                json={"email": "legacy-revoke@a.com"},
            )
            token = parse_qs(urlparse(requested.json()["reset_url"]).query)["token"][0]

            confirmation = await client.post(
                "/auth/password-reset/confirm",
                json={"token": token, "new_password": NEW_PASSWORD},
            )
            assert confirmation.status_code == 200, confirmation.text
            replay = await client.post(
                "/auth/refresh",
                json={"refresh_token": stale_refresh},
            )
        assert replay.status_code == 401, replay.text


class TestResetOptOut:
    async def test_revoke_sessions_false_keeps_the_session(
        self, session: AsyncSession
    ) -> None:
        """The explicit opt-out leaves the pre-reset refresh token alive."""
        service = _service()
        refresh = await _make_session(service, session, "keep@a.com")
        token = await _reset_token(service, session, "keep@a.com")

        await service.confirm_password_reset(
            session,
            token=token,
            new_password=NEW_PASSWORD,
            revoke_sessions=False,
        )
        await session.commit()

        user, access, _rotated = await service.refresh_tokens(
            session, refresh_token=refresh
        )
        assert user.email == "keep@a.com"
        assert access

    async def test_the_default_revokes_the_session(self, session: AsyncSession) -> None:
        """Without the opt-out, the same call kills the refresh token."""
        service = _service()
        refresh = await _make_session(service, session, "kill@a.com")
        token = await _reset_token(service, session, "kill@a.com")

        await service.confirm_password_reset(
            session,
            token=token,
            new_password=NEW_PASSWORD,
        )
        await session.commit()

        with pytest.raises(UnauthorizedException):
            await service.refresh_tokens(session, refresh_token=refresh)

    async def test_stateless_refresh_has_no_row_to_revoke(
        self, session: AsyncSession
    ) -> None:
        """With no ``refresh_token_model`` the JWT keeps working until expiry."""
        service = _service(db_refresh=False)
        refresh = await _make_session(service, session, "stateless@a.com")
        token = await _reset_token(service, session, "stateless@a.com")

        await service.confirm_password_reset(
            session,
            token=token,
            new_password=NEW_PASSWORD,
        )
        await session.commit()

        user, access, _rotated = await service.refresh_tokens(
            session, refresh_token=refresh
        )
        assert user.email == "stateless@a.com"
        assert access
