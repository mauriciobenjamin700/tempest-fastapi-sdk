"""Auth flows whose e-mail the SMTP server does not take.

The incident behind this file: ``SMTP_USE_SSL=true`` on port ``587``.
Every send failed the TLS handshake (``[SSL: WRONG_VERSION_NUMBER]``) and
``POST /auth/signup`` answered a bare ``500`` with the traceback logged as
unhandled. The mailer here reproduces that over a real socket: implicit
TLS against a plaintext SMTP greeting.

Two answers, chosen per flow:

* **503 ``EMAIL_UNAVAILABLE``** where the response already depends on the
  account (signup answers ``409`` for a taken address), or the caller is
  authenticated (e-mail change, e-mail verification), or has proven the
  account is theirs (e-mail recovery after the password matched). The
  writes are rolled back.
* **202, unchanged** where the flow answers the same for a known and an
  unknown address (activation re-send, password reset). A ``503`` only for
  registered addresses would be an enumeration oracle, so the failure is
  logged and nothing is stored — the previous link keeps working.
"""

from __future__ import annotations

import logging
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
    UserAuthService,
    make_auth_router,
    make_user_token_model,
    register_exception_handlers,
)
from tempest_fastapi_sdk.db.user_token_model import UserTokenPurpose
from tempest_fastapi_sdk.settings.mixins import AuthSettings, JWTSettings
from tests.utils.smtp_server import TinySMTP


class _SfUser(BaseUserModel):
    __tablename__ = "sf_test_users"


_SfUserToken = make_user_token_model(
    user_table="sf_test_users",
    tablename="sf_test_user_tokens",
    class_name="_SfUserToken",
)

PASSWORD: str = "strong-pass-12-chars"
"""Password every seeded account uses."""


@pytest.fixture
async def broken_mailer() -> AsyncIterator[EmailUtils]:
    """Yield a mailer whose every send fails the TLS handshake.

    Implicit TLS (``use_tls=True``, what ``SMTP_USE_SSL=true`` maps to)
    against a server that greets in plaintext, as Gmail does on ``587``.

    Yields:
        EmailUtils: The failing mailer.
    """
    server = TinySMTP()
    port = await server.start()
    yield EmailUtils(
        "127.0.0.1",
        port,
        from_addr="noreply@acme.com.br",
        use_tls=True,
        timeout=5.0,
    )
    await server.stop()


def build_service(
    mailer: EmailUtils | None,
    *,
    notify_old: bool = True,
) -> UserAuthService:
    """Build a service over the test models.

    Args:
        mailer (EmailUtils | None): The mailer; ``None`` surfaces links
            in the response, which is how the tests seed tokens.
        notify_old (bool): ``AUTH_EMAIL_CHANGE_NOTIFY_OLD``.

    Returns:
        UserAuthService: The configured service.
    """
    return UserAuthService(
        user_model=_SfUser,
        token_model=_SfUserToken,  # type: ignore[arg-type]
        auth_settings=AuthSettings(
            AUTH_EMAIL_CHANGE_NOTIFY_OLD=notify_old,
            AUTH_EMAIL_RECOVERY_ENABLED=True,
        ),
        jwt_settings=JWTSettings(JWT_SECRET="x" * 32),
        email=mailer,
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
    """Count the committed rows of ``model``, from a fresh session.

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


async def unspent_tokens(engine: AsyncEngine, purpose: UserTokenPurpose) -> int:
    """Count the committed, unspent tokens of ``purpose``.

    Args:
        engine (AsyncEngine): The test engine.
        purpose (UserTokenPurpose): The flow.

    Returns:
        int: Rows with ``used_at`` still empty.
    """
    factory = async_sessionmaker(engine, expire_on_commit=False)
    async with factory() as opened:
        result = await opened.execute(
            select(func.count())
            .select_from(_SfUserToken)
            .where(
                _SfUserToken.purpose == purpose.value,
                _SfUserToken.used_at.is_(None),
            )
        )
        return int(result.scalar_one())


async def stored_email(engine: AsyncEngine, user_id: object) -> str:
    """Read the committed e-mail of a user, from a fresh session.

    Args:
        engine (AsyncEngine): The test engine.
        user_id (object): The user's primary key.

    Returns:
        str: The address in the database.
    """
    factory = async_sessionmaker(engine, expire_on_commit=False)
    async with factory() as opened:
        user = await opened.get(_SfUser, user_id)
        assert user is not None
        return str(user.email)


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


async def seed_user(
    session: AsyncSession, email: str, *, active: bool = True
) -> BaseUserModel:
    """Insert a user without sending any mail.

    Args:
        session (AsyncSession): The test session.
        email (str): The account address.
        active (bool): ``False`` leaves the account pending activation,
            with one unspent activation token.

    Returns:
        BaseUserModel: The committed user.
    """
    seeder = UserAuthService(
        user_model=_SfUser,
        token_model=_SfUserToken,  # type: ignore[arg-type]
        auth_settings=AuthSettings(AUTH_AUTO_ACTIVATE=active),
        jwt_settings=JWTSettings(JWT_SECRET="x" * 32),
    )
    user, _ = await seeder.signup(session, email=email, password=PASSWORD)
    await session.commit()
    return user


async def bearer(client: AsyncClient, email: str) -> dict[str, str]:
    """Log in and return the ``Authorization`` header.

    Args:
        client (AsyncClient): The app client.
        email (str): The seeded account.

    Returns:
        dict[str, str]: The bearer header.
    """
    login = await client.post(
        "/auth/login", json={"email": email, "password": PASSWORD}
    )
    assert login.status_code == 200
    return {"Authorization": f"Bearer {login.json()['access_token']}"}


class TestAnswered503:
    """Flows that already depend on the account answer ``503`` and roll back."""

    async def test_signup_answers_503_and_creates_no_account(
        self, engine: AsyncEngine, broken_mailer: EmailUtils
    ) -> None:
        """The incident: a clean ``503`` instead of an unhandled ``500``."""
        async with build_client(engine, build_service(broken_mailer)) as client:
            response = await client.post(
                "/auth/signup",
                json={"email": "ana@acme.com", "password": PASSWORD},
            )
        assert response.status_code == 503
        body = response.json()
        assert body["code"] == "EMAIL_UNAVAILABLE"
        assert body["details"] == {}
        assert await count_rows(engine, _SfUser) == 0
        assert await count_rows(engine, _SfUserToken) == 0

    async def test_signup_can_be_retried_once_mail_works(
        self, engine: AsyncEngine, broken_mailer: EmailUtils
    ) -> None:
        """Nothing was kept, so the same address is not ``409`` afterwards."""
        payload = {"email": "ana@acme.com", "password": PASSWORD}
        async with build_client(engine, build_service(broken_mailer)) as client:
            assert (await client.post("/auth/signup", json=payload)).status_code == 503
        async with build_client(engine, build_service(None)) as client:
            assert (await client.post("/auth/signup", json=payload)).status_code == 201

    async def test_email_change_request_answers_503(
        self, engine: AsyncEngine, session: AsyncSession, broken_mailer: EmailUtils
    ) -> None:
        await seed_user(session, "ana@acme.com")
        async with build_client(engine, build_service(broken_mailer)) as client:
            response = await client.post(
                "/auth/email-change/request",
                json={"current_password": PASSWORD, "new_email": "new@acme.com"},
                headers=await bearer(client, "ana@acme.com"),
            )
        assert response.status_code == 503
        assert response.json()["code"] == "EMAIL_UNAVAILABLE"
        assert await unspent_tokens(engine, UserTokenPurpose.EMAIL_CHANGE) == 0

    async def test_email_change_confirm_keeps_the_old_address_and_the_link(
        self, engine: AsyncEngine, session: AsyncSession, broken_mailer: EmailUtils
    ) -> None:
        """The change never lands without the notice the setting promises."""
        user = await seed_user(session, "ana@acme.com")
        bundle = await build_service(None).request_email_change(
            session, user=user, current_password=PASSWORD, new_email="new@acme.com"
        )
        await session.commit()
        assert bundle is not None

        async with build_client(engine, build_service(broken_mailer)) as client:
            response = await client.post(
                "/auth/email-change/confirm", json={"token": bundle.token}
            )
        assert response.status_code == 503
        assert response.json()["code"] == "EMAIL_UNAVAILABLE"
        assert await stored_email(engine, user.id) == "ana@acme.com"
        assert await unspent_tokens(engine, UserTokenPurpose.EMAIL_CHANGE) == 1

        async with build_client(engine, build_service(None)) as client:
            retried = await client.post(
                "/auth/email-change/confirm", json={"token": bundle.token}
            )
        assert retried.status_code == 200
        assert await stored_email(engine, user.id) == "new@acme.com"

    async def test_email_verify_request_answers_503(
        self, engine: AsyncEngine, session: AsyncSession, broken_mailer: EmailUtils
    ) -> None:
        await seed_user(session, "ana@acme.com")
        async with build_client(engine, build_service(broken_mailer)) as client:
            response = await client.post(
                "/auth/email-verify/request",
                headers=await bearer(client, "ana@acme.com"),
            )
        assert response.status_code == 503
        assert response.json()["code"] == "EMAIL_UNAVAILABLE"
        assert await unspent_tokens(engine, UserTokenPurpose.EMAIL_VERIFICATION) == 0

    async def test_email_recovery_answers_503_only_after_the_password(
        self, engine: AsyncEngine, session: AsyncSession, broken_mailer: EmailUtils
    ) -> None:
        """The soft failures stay ``202``; the ``503`` needs the password."""
        await seed_user(session, "ana@acme.com")
        body = {"email": "ana@acme.com", "new_email": "new@acme.com"}
        async with build_client(engine, build_service(broken_mailer)) as client:
            wrong = await client.post(
                "/auth/email-recovery/request",
                json={**body, "current_password": "not-the-password-1"},
            )
            stranger = await client.post(
                "/auth/email-recovery/request",
                json={**body, "email": "ghost@acme.com", "current_password": PASSWORD},
            )
            proven = await client.post(
                "/auth/email-recovery/request",
                json={**body, "current_password": PASSWORD},
            )
        assert wrong.status_code == 202
        assert stranger.status_code == 202
        assert wrong.json() == stranger.json()
        assert proven.status_code == 503
        assert proven.json()["code"] == "EMAIL_UNAVAILABLE"
        assert await unspent_tokens(engine, UserTokenPurpose.EMAIL_CHANGE) == 0


class TestUniform202:
    """Flows that answer alike for any address keep answering alike."""

    @pytest.mark.parametrize("address", ["ana@acme.com", "ghost@acme.com"])
    async def test_password_reset_answers_like_an_unknown_address(
        self,
        engine: AsyncEngine,
        session: AsyncSession,
        broken_mailer: EmailUtils,
        address: str,
    ) -> None:
        """Known (send fails) and unknown (no send) give the same body."""
        await seed_user(session, "ana@acme.com")
        async with build_client(engine, build_service(broken_mailer)) as client:
            response = await client.post(
                "/auth/password-reset/request", json={"email": address}
            )
        assert response.status_code == 202
        assert response.json() == {
            "message": "If the email matches an account, a reset link was sent.",
            "reset_url": None,
        }
        assert await count_rows(engine, _SfUserToken) == 0

    async def test_password_reset_failure_keeps_the_previous_link(
        self, engine: AsyncEngine, session: AsyncSession, broken_mailer: EmailUtils
    ) -> None:
        """Nothing is stored, so the pending link is not superseded."""
        await seed_user(session, "ana@acme.com")
        earlier = await build_service(None).request_password_reset(
            session, email="ana@acme.com"
        )
        await session.commit()
        assert earlier is not None

        async with build_client(engine, build_service(broken_mailer)) as client:
            response = await client.post(
                "/auth/password-reset/request", json={"email": "ana@acme.com"}
            )
        assert response.status_code == 202
        assert await unspent_tokens(engine, UserTokenPurpose.PASSWORD_RESET) == 1

    async def test_password_reset_failure_is_logged_without_the_address(
        self,
        engine: AsyncEngine,
        session: AsyncSession,
        broken_mailer: EmailUtils,
        caplog: pytest.LogCaptureFixture,
    ) -> None:
        await seed_user(session, "ana@acme.com")
        caplog.set_level(logging.ERROR)
        async with build_client(engine, build_service(broken_mailer)) as client:
            await client.post(
                "/auth/password-reset/request", json={"email": "ana@acme.com"}
            )
        flows = [
            record
            for record in caplog.records
            if record.name == "tempest_fastapi_sdk.auth.service"
        ]
        assert [record.levelno for record in flows] == [logging.ERROR]
        assert flows[0].__dict__["auth_flow"] == "password_reset"
        assert "ana@acme.com" not in caplog.text

    @pytest.mark.parametrize("address", ["pending@acme.com", "ghost@acme.com"])
    async def test_activation_resend_answers_like_an_unknown_address(
        self,
        engine: AsyncEngine,
        session: AsyncSession,
        broken_mailer: EmailUtils,
        address: str,
    ) -> None:
        """The pending account's first link survives the failed re-send."""
        await seed_user(session, "pending@acme.com", active=False)
        async with build_client(engine, build_service(broken_mailer)) as client:
            response = await client.post(
                "/auth/activation/request", json={"email": address}
            )
        assert response.status_code == 202
        assert response.json() == {
            "message": "If the email matches a pending account, a link was sent.",
            "activation_url": None,
        }
        assert await count_rows(engine, _SfUserToken) == 1
        assert await unspent_tokens(engine, UserTokenPurpose.ACTIVATION) == 1
