"""Email recovery spends the login and MFA budgets it checks credentials for.

``POST /auth/email-recovery/request`` proves identity with the account
password and, with MFA enrolled, a TOTP or recovery code — the same
credentials ``/auth/login`` and ``/auth/mfa/verify`` check behind a
failure budget. The route used to check them with no budget at all, and
every failure answered the same ``202``: unlimited guesses, even after the
login and MFA doors already answered ``429``. Now it reserves the same
throttle instances before each check, and a spent budget is one more
silent ``202`` that issues no link.
"""

from __future__ import annotations

import logging
from collections.abc import AsyncIterator
from typing import Any

import pyotp
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
    MFAMixin,
    UserAuthService,
    make_auth_router,
    make_user_recovery_code_model,
    make_user_token_model,
)
from tempest_fastapi_sdk.auth.schemas import EmailChangeToken
from tempest_fastapi_sdk.settings.mixins import AuthSettings, JWTSettings
from tempest_fastapi_sdk.utils.datetime import utcnow
from tempest_fastapi_sdk.utils.throttle import AttemptThrottle, InMemoryThrottleBackend

PASSWORD: str = "strong-pass-12-chars"
ATTEMPTS: int = 8
"""Wrong guesses sent — more than the default budget of five."""


class _ErUser(MFAMixin, BaseUserModel):
    __tablename__ = "er_throttle_users"


_ErUserToken = make_user_token_model(
    user_table="er_throttle_users",
    tablename="er_throttle_user_tokens",
    class_name="_ErUserToken",
)
_ErRecoveryCode = make_user_recovery_code_model(
    user_table="er_throttle_users",
    tablename="er_throttle_recovery_codes",
    class_name="_ErRecoveryCode",
)


class _LegacyRecoveryService(UserAuthService):
    """Override written before the throttle keywords existed."""

    async def request_email_recovery(
        self,
        session: AsyncSession,
        *,
        email: str,
        new_email: str,
        current_password: str,
        mfa_code: str | None = None,
        recovery_code_model: Any = None,
    ) -> EmailChangeToken | None:
        """Delegate to the base implementation unchanged.

        Args:
            session (AsyncSession): Active SQLAlchemy session.
            email (str): The account's current email.
            new_email (str): The recovery address.
            current_password (str): The account password.
            mfa_code (str | None): The second factor.
            recovery_code_model (Any): The recovery-code model.

        Returns:
            EmailChangeToken | None: What the base returns.
        """
        return await super().request_email_recovery(
            session,
            email=email,
            new_email=new_email,
            current_password=current_password,
            mfa_code=mfa_code,
            recovery_code_model=recovery_code_model,
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
    async with factory() as opened:
        yield opened
    await engine.dispose()


def _service(service_class: type[UserAuthService] = UserAuthService) -> UserAuthService:
    """Build a service with recovery and MFA on, links in the body.

    Args:
        service_class (type[UserAuthService]): The class to instantiate.

    Returns:
        UserAuthService: The configured service.
    """
    return service_class(
        user_model=_ErUser,
        token_model=_ErUserToken,  # type: ignore[arg-type]
        auth_settings=AuthSettings(
            _env_file=None,
            AUTH_AUTO_ACTIVATE=True,
            AUTH_RETURN_TOKEN_IN_RESPONSE=True,
            AUTH_EMAIL_RECOVERY_ENABLED=True,
            AUTH_MFA_ENABLED=True,
        ),
        jwt_settings=JWTSettings(_env_file=None, JWT_SECRET="x" * 32),
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
    app.include_router(
        make_auth_router(
            service, session_factory=_factory, recovery_code_model=_ErRecoveryCode
        )
    )
    return AsyncClient(transport=ASGITransport(app=app), base_url="http://t")


async def _user(
    service: UserAuthService, session: AsyncSession, *, mfa: bool = False
) -> str | None:
    """Create ``old@acme.com``, optionally with TOTP enrolled.

    Args:
        service (UserAuthService): The configured service.
        session (AsyncSession): The session to write through.
        mfa (bool): Enroll TOTP.

    Returns:
        str | None: The TOTP secret when enrolled.
    """
    user, _ = await service.signup(session, email="old@acme.com", password=PASSWORD)
    secret: str | None = None
    if mfa:
        secret = pyotp.random_base32()
        user.totp_secret = secret
        user.totp_enabled_at = utcnow()
    await session.commit()
    return secret


async def _recover(
    client: AsyncClient,
    *,
    password: str = PASSWORD,
    code: str | None = None,
    new_email: str = "new@acme.net",
) -> str | None:
    """Post a recovery request and return the confirmation link.

    Args:
        client (AsyncClient): The client.
        password (str): The password offered.
        code (str | None): The second factor offered.
        new_email (str): The recovery address.

    Returns:
        str | None: ``confirm_url`` from the ``202``.
    """
    body: dict[str, Any] = {
        "email": "old@acme.com",
        "new_email": new_email,
        "current_password": password,
    }
    if code is not None:
        body["mfa_code"] = code
    response = await client.post("/auth/email-recovery/request", json=body)
    assert response.status_code == 202, response.text
    link: str | None = response.json()["confirm_url"]
    return link


def _wrong_codes(secret: str, count: int) -> list[str]:
    """Return codes the TOTP check refuses right now.

    Args:
        secret (str): The TOTP secret.
        count (int): How many.

    Returns:
        list[str]: Six-digit codes outside the accepted window.
    """
    totp = pyotp.TOTP(secret)
    codes: list[str] = []
    candidate = 0
    while len(codes) < count:
        code = f"{candidate:06d}"
        if not totp.verify(code, valid_window=2):
            codes.append(code)
        candidate += 1
    return codes


class TestPasswordBudget:
    async def test_wrong_passwords_spend_the_budget(
        self, session: AsyncSession
    ) -> None:
        service = _service()
        await _user(service, session)
        async with _client(service, session) as client:
            for _ in range(ATTEMPTS):
                assert await _recover(client, password="wrong-pass-12-chars") is None
            assert await _recover(client) is None

    async def test_a_login_spent_budget_holds_here_too(
        self, session: AsyncSession
    ) -> None:
        service = _service()
        await _user(service, session)
        async with _client(service, session) as client:
            statuses = [
                (
                    await client.post(
                        "/auth/login",
                        json={"email": "old@acme.com", "password": "wrong-pass-12"},
                    )
                ).status_code
                for _ in range(ATTEMPTS)
            ]
            assert statuses[-1] == 429
            assert await _recover(client) is None

    async def test_within_budget_the_right_password_issues_the_link(
        self, session: AsyncSession
    ) -> None:
        service = _service()
        await _user(service, session)
        async with _client(service, session) as client:
            for _ in range(3):
                assert await _recover(client, password="wrong-pass-12-chars") is None
            assert await _recover(client) is not None


class TestMfaBudget:
    async def test_wrong_codes_spend_the_mfa_budget(
        self, session: AsyncSession
    ) -> None:
        service = _service()
        secret = await _user(service, session, mfa=True)
        assert secret is not None
        async with _client(service, session) as client:
            for code in _wrong_codes(secret, ATTEMPTS):
                assert await _recover(client, code=code) is None
            assert await _recover(client, code=pyotp.TOTP(secret).now()) is None

    async def test_within_budget_the_right_code_issues_the_link(
        self, session: AsyncSession
    ) -> None:
        service = _service()
        secret = await _user(service, session, mfa=True)
        assert secret is not None
        async with _client(service, session) as client:
            for code in _wrong_codes(secret, 2):
                assert await _recover(client, code=code) is None
            assert await _recover(client, code=pyotp.TOTP(secret).now()) is not None


class TestOverrides:
    async def test_a_legacy_override_is_still_called_and_warned(
        self, session: AsyncSession, caplog: pytest.LogCaptureFixture
    ) -> None:
        service = _service(_LegacyRecoveryService)
        await _user(service, session)
        with caplog.at_level(logging.WARNING):
            client = _client(service, session)
        assert "request_email_recovery() does not accept" in caplog.text
        async with client:
            assert await _recover(client) is not None


class TestIpBudget:
    async def test_a_spent_ip_budget_issues_no_link(
        self, session: AsyncSession
    ) -> None:
        """Failures across addresses fill the IP budget the login door uses."""
        service = _service()
        await _user(service, session)
        ip_throttle = AttemptThrottle(
            InMemoryThrottleBackend(), max_attempts=3, window_seconds=60
        )
        for index in range(3):
            assert (
                await service.request_email_recovery(
                    session,
                    email=f"ghost{index}@acme.com",
                    new_email="new@acme.net",
                    current_password=PASSWORD,
                    login_ip_throttle=ip_throttle,
                    client_ip="203.0.113.7",
                )
                is None
            )
        assert (
            await service.request_email_recovery(
                session,
                email="old@acme.com",
                new_email="new@acme.net",
                current_password=PASSWORD,
                login_ip_throttle=ip_throttle,
                client_ip="203.0.113.7",
            )
            is None
        )
        assert (
            await service.request_email_recovery(
                session,
                email="old@acme.com",
                new_email="new@acme.net",
                current_password=PASSWORD,
                login_ip_throttle=ip_throttle,
                client_ip="198.51.100.9",
            )
            is not None
        )

    async def test_concurrent_guesses_cannot_overrun_the_ip_budget(
        self, session: AsyncSession
    ) -> None:
        """A burst from one IP runs at most ``max_attempts`` password checks.

        Reading the budget first and spending it only on failure bounds
        sequential guesses alone: every request of a burst reads the budget
        before any of them fails. The slot is reserved before the check, as
        on ``/auth/login``.
        """
        import asyncio

        service = _service()
        await _user(service, session)
        real = service.passwords
        checks: list[str] = []

        class _CountingPasswords:
            def verify(self, plain: str, hashed: str) -> bool:
                checks.append(plain)
                return real.verify(plain, hashed)

        service.passwords = _CountingPasswords()  # type: ignore[assignment]
        factory = async_sessionmaker(session.bind, expire_on_commit=False)
        ip_throttle = AttemptThrottle(
            InMemoryThrottleBackend(), max_attempts=3, window_seconds=60
        )

        async def _guess(index: int) -> EmailChangeToken | None:
            async with factory() as own:
                return await service.request_email_recovery(
                    own,
                    email="old@acme.com",
                    new_email="new@acme.net",
                    current_password=f"wrong-guess-{index}",
                    login_ip_throttle=ip_throttle,
                    client_ip="203.0.113.7",
                )

        results = await asyncio.gather(*(_guess(index) for index in range(20)))
        assert all(result is None for result in results)
        assert len(checks) == 3

    async def test_a_proven_request_gives_the_ip_slot_back(
        self, session: AsyncSession
    ) -> None:
        """Only failed proofs spend the IP budget."""
        service = _service()
        await _user(service, session)
        ip_throttle = AttemptThrottle(
            InMemoryThrottleBackend(), max_attempts=1, window_seconds=60
        )
        for index in range(3):
            assert (
                await service.request_email_recovery(
                    session,
                    email="old@acme.com",
                    new_email=f"new{index}@acme.net",
                    current_password=PASSWORD,
                    login_ip_throttle=ip_throttle,
                    client_ip="203.0.113.7",
                )
                is not None
            )
        assert (await ip_throttle.status("login-ip:203.0.113.7")).attempts == 0
