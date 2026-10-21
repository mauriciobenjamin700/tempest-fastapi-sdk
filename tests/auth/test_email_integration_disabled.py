"""Auth flows that need an e-mail, with the e-mail integration switched off.

``EMAIL_ENABLED=false`` builds ``EmailUtils(enabled=False)``. Before the
switch, the only way to run without SMTP was to wire no mailer at all,
which returns the link in the response body — fine in development, a hole
in production. With the switch, every flow whose link would travel by
e-mail answers ``503 INTEGRATION_DISABLED`` **before** writing anything:
an account whose activation link can never arrive is never created, and a
reset request never mints a token nobody receives.
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
    BaseModel,
    BaseUserModel,
    EmailUtils,
    IntegrationDisabledException,
    UserAuthService,
    make_auth_router,
    make_user_token_model,
    register_exception_handlers,
)
from tempest_fastapi_sdk.settings.mixins import AuthSettings, JWTSettings


class _EdUser(BaseUserModel):
    __tablename__ = "ed_test_users"


_EdUserToken = make_user_token_model(
    user_table="ed_test_users",
    tablename="ed_test_user_tokens",
    class_name="_EdUserToken",
)

PASSWORD: str = "strong-pass-12-chars"


def disabled_mailer() -> EmailUtils:
    """Build the mailer ``email_kwargs()`` yields under ``EMAIL_ENABLED=false``.

    Returns:
        EmailUtils: A mailer built with ``enabled=False``.
    """
    return EmailUtils("localhost", 1025, from_addr="noreply@localhost", enabled=False)


def build_service(
    *,
    auto_activate: bool = False,
    return_token: bool = False,
    notify_old: bool = True,
    mailer: EmailUtils | None = None,
) -> UserAuthService:
    """Build a service over the test models.

    Args:
        auto_activate (bool): ``AUTH_AUTO_ACTIVATE``.
        return_token (bool): ``AUTH_RETURN_TOKEN_IN_RESPONSE``.
        notify_old (bool): ``AUTH_EMAIL_CHANGE_NOTIFY_OLD``.
        mailer (EmailUtils | None): The mailer; disabled by default.

    Returns:
        UserAuthService: The configured service.
    """
    return UserAuthService(
        user_model=_EdUser,
        token_model=_EdUserToken,  # type: ignore[arg-type]
        auth_settings=AuthSettings(
            AUTH_AUTO_ACTIVATE=auto_activate,
            AUTH_RETURN_TOKEN_IN_RESPONSE=return_token,
            AUTH_EMAIL_CHANGE_NOTIFY_OLD=notify_old,
        ),
        jwt_settings=JWTSettings(JWT_SECRET="x" * 32),
        email=mailer if mailer is not None else disabled_mailer(),
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


async def count_rows(engine: AsyncEngine, model: type[BaseModel]) -> int:
    """Count the committed rows of ``model``.

    Args:
        engine (AsyncEngine): The test engine.
        model (type[BaseModel]): The table to count.

    Returns:
        int: How many rows exist.
    """
    factory = async_sessionmaker(engine, expire_on_commit=False)
    async with factory() as opened:
        result = await opened.execute(select(func.count()).select_from(model))
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


async def seed_active_user(session: AsyncSession, email: str) -> BaseUserModel:
    """Insert an active user through a mailer-less, auto-activating service.

    Args:
        session (AsyncSession): The test session.
        email (str): The account address.

    Returns:
        BaseUserModel: The committed user.
    """
    seeder = UserAuthService(
        user_model=_EdUser,
        token_model=_EdUserToken,  # type: ignore[arg-type]
        auth_settings=AuthSettings(AUTH_AUTO_ACTIVATE=True),
        jwt_settings=JWTSettings(JWT_SECRET="x" * 32),
    )
    user, _ = await seeder.signup(session, email=email, password=PASSWORD)
    await session.commit()
    return user


class TestRoutes:
    """What the client sees, and what the database keeps."""

    async def test_signup_answers_503_and_creates_no_account(
        self, engine: AsyncEngine
    ) -> None:
        async with build_client(engine, build_service()) as client:
            response = await client.post(
                "/auth/signup",
                json={"email": "ana@acme.com", "password": PASSWORD},
            )
        assert response.status_code == 503
        body = response.json()
        assert body["code"] == "INTEGRATION_DISABLED"
        assert body["details"] == {"integration": "email"}
        assert await count_rows(engine, _EdUser) == 0
        assert await count_rows(engine, _EdUserToken) == 0

    async def test_auto_activated_signup_needs_no_mail(
        self, engine: AsyncEngine
    ) -> None:
        """Born active means no link, so the switch does not apply."""
        service = build_service(auto_activate=True)
        async with build_client(engine, service) as client:
            response = await client.post(
                "/auth/signup",
                json={"email": "ana@acme.com", "password": PASSWORD},
            )
        assert response.status_code == 201
        assert await count_rows(engine, _EdUser) == 1

    async def test_link_in_the_response_needs_no_mail(
        self, engine: AsyncEngine
    ) -> None:
        """``AUTH_RETURN_TOKEN_IN_RESPONSE`` hands the link over the wire."""
        service = build_service(return_token=True)
        async with build_client(engine, service) as client:
            response = await client.post(
                "/auth/signup",
                json={"email": "ana@acme.com", "password": PASSWORD},
            )
        assert response.status_code == 201
        assert response.json()["activation_url"]

    @pytest.mark.parametrize("address", ["ana@acme.com", "ghost@acme.com"])
    async def test_password_reset_answers_503_for_any_address(
        self, engine: AsyncEngine, session: AsyncSession, address: str
    ) -> None:
        """Known and unknown addresses answer alike: nothing to enumerate."""
        await seed_active_user(session, "ana@acme.com")
        async with build_client(engine, build_service()) as client:
            response = await client.post(
                "/auth/password-reset/request", json={"email": address}
            )
        assert response.status_code == 503
        assert response.json()["code"] == "INTEGRATION_DISABLED"
        assert await count_rows(engine, _EdUserToken) == 0

    async def test_activation_resend_answers_503(self, engine: AsyncEngine) -> None:
        async with build_client(engine, build_service()) as client:
            response = await client.post(
                "/auth/activation/request", json={"email": "ana@acme.com"}
            )
        assert response.status_code == 503

    async def test_login_is_untouched(
        self, engine: AsyncEngine, session: AsyncSession
    ) -> None:
        """Only flows that send mail are refused."""
        await seed_active_user(session, "ana@acme.com")
        async with build_client(engine, build_service()) as client:
            response = await client.post(
                "/auth/login",
                json={"email": "ana@acme.com", "password": PASSWORD},
            )
        assert response.status_code == 200


class TestServiceFlows:
    """The bearer-only and confirm flows, straight on the service."""

    async def test_email_change_request_is_refused(
        self, session: AsyncSession, engine: AsyncEngine
    ) -> None:
        user = await seed_active_user(session, "ana@acme.com")
        with pytest.raises(IntegrationDisabledException):
            await build_service().request_email_change(
                session,
                user=user,
                current_password=PASSWORD,
                new_email="new@acme.com",
            )
        assert await count_rows(engine, _EdUserToken) == 0

    async def test_email_verification_request_is_refused(
        self, session: AsyncSession
    ) -> None:
        user = await seed_active_user(session, "ana@acme.com")
        with pytest.raises(IntegrationDisabledException):
            await build_service().request_email_verification(session, user=user)

    async def test_email_recovery_is_refused_before_the_lookup(
        self, session: AsyncSession
    ) -> None:
        """An unknown address is refused too: the 503 reveals nothing."""
        with pytest.raises(IntegrationDisabledException):
            await build_service().request_email_recovery(
                session,
                email="ghost@acme.com",
                new_email="new@acme.com",
                current_password=PASSWORD,
            )

    async def test_confirm_change_keeps_the_token_when_the_notice_cannot_go(
        self, session: AsyncSession
    ) -> None:
        """The security notice is not covered by the return-token flag."""
        user = await seed_active_user(session, "ana@acme.com")
        issuer = build_service(return_token=True)
        bundle = await issuer.request_email_change(
            session, user=user, current_password=PASSWORD, new_email="new@acme.com"
        )
        await session.commit()
        assert bundle is not None
        with pytest.raises(IntegrationDisabledException):
            await issuer.confirm_email_change(session, token=bundle.token)
        unchanged = await session.get(_EdUser, user.id)
        assert unchanged is not None
        assert unchanged.email == "ana@acme.com"
        applied = await build_service(notify_old=False).confirm_email_change(
            session, token=bundle.token
        )
        assert applied.email == "new@acme.com"

    def test_enabled_mailer_is_never_refused(self) -> None:
        service = build_service(
            mailer=EmailUtils("smtp.acme.com", 587, from_addr="noreply@acme.com")
        )
        service.require_mail_delivery()
        service.require_mail_delivery(carries_token=False)

    def test_mailer_double_without_the_attribute_counts_as_enabled(self) -> None:
        """Test doubles written before the flag keep working."""
        service = build_service()
        service.email = object()  # type: ignore[assignment]
        service.require_mail_delivery()
