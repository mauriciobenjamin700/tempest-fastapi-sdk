"""Production never hands an e-mailed link to the caller in the response.

``UserAuthService(email=None)`` — the constructor default — returns the
activation, reset, e-mail change and recovery links in the response body.
That is the development mode the recipes describe. Before this check it
also held under ``ENV=production``, where the production guard is meant to
refuse development-only values: an anonymous ``POST
/auth/password-reset/request`` answered ``202`` with a working
``reset_url`` for any registered address. Now every flow carrying a link
answers ``503 INTEGRATION_DISABLED`` there, before reading or writing
anything, and a service with a mailer wired keeps working.
"""

from __future__ import annotations

from collections.abc import AsyncIterator

import pytest
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)

from tempest_fastapi_sdk import (
    AuthSettings,
    BaseAppSettings,
    BaseModel,
    BaseUserModel,
    EnvironmentSettings,
    IntegrationDisabledException,
    JWTSettings,
    UserAuthService,
    make_auth_router,
    make_user_token_model,
    register_exception_handlers,
)
from tempest_fastapi_sdk.settings.mixins import AppEnvironment
from tempest_fastapi_sdk.testing.fakes.email import FakeEmailUtils


class _PlUser(BaseUserModel):
    __tablename__ = "pl_test_users"


_PlUserToken = make_user_token_model(
    user_table="pl_test_users",
    tablename="pl_test_user_tokens",
    class_name="_PlUserToken",
)

PASSWORD: str = "strong-pass-12-chars"

LINKS: dict[str, str] = {
    "AUTH_ACTIVATION_URL_TEMPLATE": "https://app.acme.com.br/activate?token={token}",
    "AUTH_PASSWORD_RESET_URL_TEMPLATE": "https://app.acme.com.br/reset?token={token}",
    "AUTH_EMAIL_CHANGE_URL_TEMPLATE": "https://app.acme.com.br/email?token={token}",
    "AUTH_EMAIL_VERIFICATION_URL_TEMPLATE": "https://app.acme.com.br/verify?token={token}",
}
"""Templates the production guard accepts."""


class _Settings(EnvironmentSettings, AuthSettings, BaseAppSettings):
    """Auth composed with the environment, as a deployed service has it."""


def build_settings(env: AppEnvironment) -> _Settings:
    """Build settings that pass the production guard.

    Args:
        env (AppEnvironment): The ``ENV`` value.

    Returns:
        _Settings: The composed settings.
    """
    return _Settings(_env_file=None, ENV=env, **LINKS)  # type: ignore[call-arg, arg-type]


def build_service(
    *,
    env: AppEnvironment = "production",
    mailer: FakeEmailUtils | None = None,
) -> UserAuthService:
    """Build a service over the test models.

    Args:
        env (AppEnvironment): The ``ENV`` the settings report.
        mailer (FakeEmailUtils | None): The mailer; ``None`` is the
            constructor default.

    Returns:
        UserAuthService: The configured service.
    """
    return UserAuthService(
        user_model=_PlUser,
        token_model=_PlUserToken,  # type: ignore[arg-type]
        auth_settings=build_settings(env),
        jwt_settings=JWTSettings(JWT_SECRET="x" * 32),
        email=mailer,  # type: ignore[arg-type]
    )


@pytest.fixture
async def engine() -> AsyncIterator[AsyncEngine]:
    """Yield an engine over a fresh in-memory schema."""
    built = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with built.begin() as conn:
        await conn.run_sync(BaseModel.metadata.create_all)
    yield built
    await built.dispose()


@pytest.fixture
async def session(engine: AsyncEngine) -> AsyncIterator[AsyncSession]:
    """Yield a session over the test engine."""
    factory = async_sessionmaker(engine, expire_on_commit=False)
    async with factory() as opened:
        yield opened


async def seed_user(session: AsyncSession, *, active: bool = True) -> BaseUserModel:
    """Insert ``ana@acme.com`` through a development service.

    Args:
        session (AsyncSession): The test session.
        active (bool): Whether the account is active.

    Returns:
        BaseUserModel: The committed user.
    """
    seeder = UserAuthService(
        user_model=_PlUser,
        token_model=_PlUserToken,  # type: ignore[arg-type]
        auth_settings=AuthSettings(AUTH_AUTO_ACTIVATE=True),
        jwt_settings=JWTSettings(JWT_SECRET="x" * 32),
    )
    user, _ = await seeder.signup(session, email="ana@acme.com", password=PASSWORD)
    user.is_active = active
    await session.commit()
    return user


async def count_tokens(engine: AsyncEngine) -> int:
    """Count the committed single-use token rows.

    Args:
        engine (AsyncEngine): The test engine.

    Returns:
        int: How many rows exist.
    """
    factory = async_sessionmaker(engine, expire_on_commit=False)
    async with factory() as opened:
        result = await opened.execute(select(func.count()).select_from(_PlUserToken))
        return int(result.scalar_one())


def build_client(engine: AsyncEngine, service: UserAuthService) -> AsyncClient:
    """Mount the auth router, with the SDK handlers, over ``engine``.

    Args:
        engine (AsyncEngine): The test engine.
        service (UserAuthService): The service to mount.

    Returns:
        AsyncClient: Driving the app over ASGI.
    """
    factory = async_sessionmaker(engine, expire_on_commit=False)

    async def session_dependency() -> AsyncIterator[AsyncSession]:
        async with factory() as opened:
            yield opened

    app = FastAPI()
    register_exception_handlers(app)
    app.include_router(make_auth_router(service, session_factory=session_dependency))
    return AsyncClient(transport=ASGITransport(app=app), base_url="http://test")


class TestAnonymousRoutes:
    """What an anonymous caller gets in production without a mailer."""

    @pytest.mark.parametrize("address", ["ana@acme.com", "ghost@acme.com"])
    async def test_password_reset_is_refused_and_mints_nothing(
        self, engine: AsyncEngine, session: AsyncSession, address: str
    ) -> None:
        await seed_user(session)
        async with build_client(engine, build_service()) as client:
            response = await client.post(
                "/auth/password-reset/request", json={"email": address}
            )
        assert response.status_code == 503
        assert response.json()["code"] == "INTEGRATION_DISABLED"
        assert "reset_url" not in response.json()
        assert await count_tokens(engine) == 0

    async def test_activation_resend_is_refused(
        self, engine: AsyncEngine, session: AsyncSession
    ) -> None:
        await seed_user(session, active=False)
        async with build_client(engine, build_service()) as client:
            response = await client.post(
                "/auth/activation/request", json={"email": "ana@acme.com"}
            )
        assert response.status_code == 503
        assert await count_tokens(engine) == 0

    async def test_signup_is_refused(self, engine: AsyncEngine) -> None:
        async with build_client(engine, build_service()) as client:
            response = await client.post(
                "/auth/signup", json={"email": "bia@acme.com", "password": PASSWORD}
            )
        assert response.status_code == 503

    async def test_with_a_mailer_the_link_stays_in_the_mail(
        self, engine: AsyncEngine, session: AsyncSession
    ) -> None:
        """Control: the same production settings, mailer wired."""
        await seed_user(session)
        mailer = FakeEmailUtils()
        async with build_client(engine, build_service(mailer=mailer)) as client:
            response = await client.post(
                "/auth/password-reset/request", json={"email": "ana@acme.com"}
            )
        assert response.status_code == 202
        assert response.json()["reset_url"] is None
        assert len(mailer.outbox) == 1

    async def test_development_still_surfaces_the_link(
        self, engine: AsyncEngine, session: AsyncSession
    ) -> None:
        """The documented development mode is unchanged."""
        await seed_user(session)
        async with build_client(engine, build_service(env="development")) as client:
            response = await client.post(
                "/auth/password-reset/request", json={"email": "ana@acme.com"}
            )
        assert response.status_code == 202
        assert response.json()["reset_url"].startswith("https://app.acme.com.br/")


class TestServiceFlows:
    """The bearer and recovery flows, straight on the service."""

    async def test_email_recovery_is_refused(self, session: AsyncSession) -> None:
        await seed_user(session)
        with pytest.raises(IntegrationDisabledException):
            await build_service().request_email_recovery(
                session,
                email="ana@acme.com",
                new_email="new@acme.com",
                current_password=PASSWORD,
            )

    async def test_email_change_request_is_refused(self, session: AsyncSession) -> None:
        user = await seed_user(session)
        with pytest.raises(IntegrationDisabledException):
            await build_service().request_email_change(
                session,
                user=user,
                current_password=PASSWORD,
                new_email="new@acme.com",
            )

    async def test_email_verification_request_is_refused(
        self, session: AsyncSession
    ) -> None:
        user = await seed_user(session)
        with pytest.raises(IntegrationDisabledException):
            await build_service().request_email_verification(session, user=user)

    def test_a_flow_without_a_link_is_not_refused(self) -> None:
        """The notice to the old address carries no link to surface."""
        build_service().require_mail_delivery(carries_token=False)

    def test_return_token_flag_is_refused_at_runtime_too(self) -> None:
        """Settings that skipped the boot guard are still caught per request."""
        service = build_service(mailer=FakeEmailUtils())
        service.auth_settings = _Settings.model_construct(
            ENV="production",
            AUTH_RETURN_TOKEN_IN_RESPONSE=True,
        )
        with pytest.raises(IntegrationDisabledException):
            service.require_mail_delivery()
