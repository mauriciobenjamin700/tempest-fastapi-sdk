"""Registering a passkey asks for the account's factors, not just a token.

A passkey signs in without the password and without the second factor, and
only account recovery removes it (``test_recovery_revokes_passkeys``). Before
this, ``POST /auth/webauthn/register/begin`` depended on the bearer access
token alone, so whoever held a leaked token could bind an authenticator of
their own and keep signing in after the token expired and after the owner
enrolled TOTP. The route now re-verifies the password, plus the
second factor when the account has MFA enrolled, like ``/auth/mfa/disable``.
"""

from __future__ import annotations

import time
from collections.abc import AsyncIterator
from typing import Any

import pyotp
import pytest
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import (
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)

from tempest_fastapi_sdk import (
    BaseModel,
    BaseUserModel,
    MemoryWebAuthnChallengeStore,
    MFAMixin,
    UserAuthService,
    WebAuthnService,
    make_auth_router,
    make_user_recovery_code_model,
    make_user_token_model,
    make_web_authn_credential_model,
    register_exception_handlers,
)
from tempest_fastapi_sdk.settings.mixins import AuthSettings, JWTSettings
from tests.auth.webauthn_authenticator import SoftwareAuthenticator

RP_ID: str = "example.com"
ORIGIN: str = "https://example.com"
PASSWORD: str = "strong-pass-12-chars"


class _StepUpUser(MFAMixin, BaseUserModel):
    __tablename__ = "webauthn_step_up_users"


_StepUpUserToken = make_user_token_model(
    user_table="webauthn_step_up_users",
    tablename="webauthn_step_up_user_tokens",
    class_name="_StepUpUserToken",
)
_StepUpRecoveryCode = make_user_recovery_code_model(
    user_table="webauthn_step_up_users",
    tablename="webauthn_step_up_recovery_codes",
    class_name="_StepUpRecoveryCode",
)
_StepUpCredential = make_web_authn_credential_model(
    user_table="webauthn_step_up_users",
    tablename="webauthn_step_up_credentials",
    class_name="_StepUpCredential",
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


def _services() -> tuple[UserAuthService, WebAuthnService]:
    """Build the auth service with MFA and WebAuthn on.

    Returns:
        tuple[UserAuthService, WebAuthnService]: The pair.
    """
    auth = AuthSettings(
        _env_file=None,
        AUTH_AUTO_ACTIVATE=True,
        AUTH_MFA_ENABLED=True,
        AUTH_MFA_ISSUER="Tempest Test",
        AUTH_WEBAUTHN_ENABLED=True,
        AUTH_WEBAUTHN_RP_ID=RP_ID,
        AUTH_WEBAUTHN_RP_NAME="Tempest Test",
    )
    service = UserAuthService(
        user_model=_StepUpUser,
        token_model=_StepUpUserToken,
        auth_settings=auth,
        jwt_settings=JWTSettings(_env_file=None, JWT_SECRET="x" * 32),
        email=None,
    )
    webauthn = WebAuthnService(
        user_model=_StepUpUser,
        credential_model=_StepUpCredential,
        auth_settings=auth,
        challenge_store=MemoryWebAuthnChallengeStore(),
    )
    return service, webauthn


def _client(
    service: UserAuthService,
    webauthn: WebAuthnService,
    session: AsyncSession,
) -> AsyncClient:
    """Mount the auth router on a throwaway app.

    Args:
        service (UserAuthService): The auth service.
        webauthn (WebAuthnService): The WebAuthn service.
        session (AsyncSession): The session every request shares.

    Returns:
        AsyncClient: A client bound to the app over ASGI.
    """

    async def _factory() -> AsyncIterator[AsyncSession]:
        yield session

    app = FastAPI()
    register_exception_handlers(app)
    app.include_router(
        make_auth_router(
            service,
            session_factory=_factory,
            recovery_code_model=_StepUpRecoveryCode,
            webauthn=webauthn,
        )
    )
    return AsyncClient(transport=ASGITransport(app=app), base_url="http://t")


async def _account(
    service: UserAuthService,
    session: AsyncSession,
    *,
    mfa: bool,
) -> tuple[dict[str, str], str | None]:
    """Create an active account and return a bearer header for it.

    Args:
        service (UserAuthService): The auth service.
        session (AsyncSession): Active session.
        mfa (bool): Whether to enroll and confirm TOTP.

    Returns:
        tuple[dict[str, str], str | None]: The ``Authorization`` header and
        the enrolled Base32 secret (``None`` without MFA).
    """
    user, _ = await service.signup(
        session, email="owner@example.com", password=PASSWORD
    )
    secret: str | None = None
    if mfa:
        secret, _, _ = await service.mfa_enroll(
            session, user=user, recovery_code_model=_StepUpRecoveryCode
        )
        await service.mfa_confirm(session, user=user, code=pyotp.TOTP(secret).now())
    access, _ = await service.issue_token_pair(session, user)
    await session.commit()
    return {"Authorization": f"Bearer {access}"}, secret


async def _credential_count(session: AsyncSession) -> int:
    """Count the stored WebAuthn credentials.

    Args:
        session (AsyncSession): Active session.

    Returns:
        int: Number of credential rows.
    """
    result = await session.execute(select(func.count()).select_from(_StepUpCredential))
    return int(result.scalar_one())


def _next_code(secret: str) -> str:
    """Return the TOTP code of the step after the current one.

    Args:
        secret (str): The enrolled Base32 secret.

    Returns:
        str: The 6-digit code for ``now + 30s``, a step not yet spent.
    """
    return pyotp.TOTP(secret).at(int(time.time()) + 30)


class TestTheTokenAloneIsNotEnough:
    """The finding: a bearer token was all the route asked for."""

    async def test_no_body_is_refused(self, session: AsyncSession) -> None:
        service, webauthn = _services()
        headers, _ = await _account(service, session, mfa=False)
        async with _client(service, webauthn, session) as client:
            response = await client.post(
                "/auth/webauthn/register/begin", headers=headers
            )
        assert response.status_code == 422, response.text
        assert await _credential_count(session) == 0

    async def test_a_wrong_password_is_refused(self, session: AsyncSession) -> None:
        service, webauthn = _services()
        headers, _ = await _account(service, session, mfa=False)
        async with _client(service, webauthn, session) as client:
            response = await client.post(
                "/auth/webauthn/register/begin",
                headers=headers,
                json={"current_password": "not-the-password-1"},
            )
        assert response.status_code == 401, response.text
        assert "challenge_id" not in response.json()

    async def test_the_password_registers_an_account_without_mfa(
        self, session: AsyncSession
    ) -> None:
        service, webauthn = _services()
        headers, _ = await _account(service, session, mfa=False)
        device = SoftwareAuthenticator(rp_id=RP_ID)
        async with _client(service, webauthn, session) as client:
            begin = await client.post(
                "/auth/webauthn/register/begin",
                headers=headers,
                json={"current_password": PASSWORD},
            )
            assert begin.status_code == 200, begin.text
            complete = await client.post(
                "/auth/webauthn/register/complete",
                headers=headers,
                json={
                    "challenge_id": begin.json()["challenge_id"],
                    "credential": device.register(
                        begin.json()["options"], origin=ORIGIN
                    ),
                },
            )
        assert complete.status_code == 200, complete.text
        assert await _credential_count(session) == 1


class TestAnMfaAccountAlsoProvesTheSecondFactor:
    """A passkey would skip an enrolled TOTP, so registering one needs it."""

    @pytest.mark.parametrize(
        "body",
        [
            pytest.param({"current_password": PASSWORD}, id="no-code"),
            pytest.param(
                {"current_password": PASSWORD, "code": "000000"}, id="wrong-code"
            ),
        ],
    )
    async def test_the_password_alone_is_refused(
        self, session: AsyncSession, body: dict[str, Any]
    ) -> None:
        service, webauthn = _services()
        headers, _ = await _account(service, session, mfa=True)
        async with _client(service, webauthn, session) as client:
            response = await client.post(
                "/auth/webauthn/register/begin", headers=headers, json=body
            )
        assert response.status_code == 401, response.text

    async def test_password_and_code_open_the_ceremony_once(
        self, session: AsyncSession
    ) -> None:
        service, webauthn = _services()
        headers, secret = await _account(service, session, mfa=True)
        assert secret is not None
        body = {"current_password": PASSWORD, "code": _next_code(secret)}
        async with _client(service, webauthn, session) as client:
            first = await client.post(
                "/auth/webauthn/register/begin", headers=headers, json=body
            )
            replayed = await client.post(
                "/auth/webauthn/register/begin", headers=headers, json=body
            )
        assert first.status_code == 200, first.text
        assert first.json()["challenge_id"]
        assert replayed.status_code == 401, replayed.text
