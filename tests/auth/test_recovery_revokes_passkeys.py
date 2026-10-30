"""Account recovery deletes the passkeys registered on the account.

A passkey signs in without the password and without the second factor. A
password reset and an MFA reset revoked the sessions but left every WebAuthn
credential in place, so a passkey registered by whoever held the account
kept signing in after the owner recovered it. The recovery flows
(``confirm_password_reset``, ``mfa_reset``) now delete every passkey of the
user by default, with ``revoke_passkeys=False`` as the explicit opt-out; the
authenticated flows (``change_password``, ``mfa_disable``) keep them.
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from typing import Any
from urllib.parse import parse_qs, urlparse

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
from tempest_fastapi_sdk.exceptions import UnauthorizedException
from tempest_fastapi_sdk.settings.mixins import AuthSettings, JWTSettings
from tests.auth.webauthn_authenticator import SoftwareAuthenticator

RP_ID: str = "example.com"
ORIGIN: str = "https://example.com"
PASSWORD: str = "strong-pass-12-chars"
NEW_PASSWORD: str = "brand-new-pass-12-chars"
EMAIL: str = "owner@example.com"


class _RecoveryUser(MFAMixin, BaseUserModel):
    __tablename__ = "passkey_recovery_users"


_RecoveryUserToken = make_user_token_model(
    user_table="passkey_recovery_users",
    tablename="passkey_recovery_user_tokens",
    class_name="_RecoveryUserToken",
)
_RecoveryCode = make_user_recovery_code_model(
    user_table="passkey_recovery_users",
    tablename="passkey_recovery_codes",
    class_name="_RecoveryCode",
)
_RecoveryCredential = make_web_authn_credential_model(
    user_table="passkey_recovery_users",
    tablename="passkey_recovery_credentials",
    class_name="_RecoveryCredential",
)


@pytest.fixture
async def session() -> AsyncIterator[AsyncSession]:
    """Yield a session over a fresh in-memory database.

    Yields:
        AsyncSession: The session every call of a test shares.
    """
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as conn:
        await conn.run_sync(BaseModel.metadata.create_all)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    async with factory() as s:
        yield s
    await engine.dispose()


def _settings() -> AuthSettings:
    """Build development settings with MFA and WebAuthn on.

    Returns:
        AuthSettings: The settings instance.
    """
    return AuthSettings(
        _env_file=None,
        AUTH_AUTO_ACTIVATE=True,
        AUTH_RETURN_TOKEN_IN_RESPONSE=True,
        AUTH_MFA_ENABLED=True,
        AUTH_MFA_ISSUER="Tempest Test",
        AUTH_WEBAUTHN_ENABLED=True,
        AUTH_WEBAUTHN_RP_ID=RP_ID,
        AUTH_WEBAUTHN_RP_NAME="Tempest Test",
    )


def _services(
    *,
    wire_model: bool = True,
) -> tuple[UserAuthService, WebAuthnService]:
    """Build the auth service and the WebAuthn service over the same tables.

    Args:
        wire_model (bool): Pass ``webauthn_credential_model`` to the auth
            service. ``False`` leaves it unset, as a project that only hands
            the WebAuthn service to ``make_auth_router`` does.

    Returns:
        tuple[UserAuthService, WebAuthnService]: The pair.
    """
    settings = _settings()
    service = UserAuthService(
        user_model=_RecoveryUser,
        token_model=_RecoveryUserToken,
        auth_settings=settings,
        jwt_settings=JWTSettings(_env_file=None, JWT_SECRET="x" * 32),
        email=None,
        webauthn_credential_model=_RecoveryCredential if wire_model else None,
    )
    webauthn = WebAuthnService(
        user_model=_RecoveryUser,
        credential_model=_RecoveryCredential,
        auth_settings=settings,
        challenge_store=MemoryWebAuthnChallengeStore(),
    )
    return service, webauthn


async def _owner(service: UserAuthService, session: AsyncSession) -> Any:
    """Create the active account the tests recover.

    Args:
        service (UserAuthService): The auth service.
        session (AsyncSession): Active session.

    Returns:
        Any: The persisted user.
    """
    user, _ = await service.signup(session, email=EMAIL, password=PASSWORD)
    await session.commit()
    return user


async def _register(
    webauthn: WebAuthnService,
    session: AsyncSession,
    user: Any,
) -> SoftwareAuthenticator:
    """Register a fresh software authenticator on ``user``.

    Args:
        webauthn (WebAuthnService): The WebAuthn service.
        session (AsyncSession): Active session.
        user (Any): The account the passkey is bound to.

    Returns:
        SoftwareAuthenticator: The device holding the private key.
    """
    device = SoftwareAuthenticator(rp_id=RP_ID)
    options, challenge_id = await webauthn.register_begin(session, user=user)
    await webauthn.register_complete(
        session,
        user=user,
        challenge_id=challenge_id,
        response=device.register(options, origin=ORIGIN),
    )
    await session.commit()
    return device


async def _sign_in(
    webauthn: WebAuthnService,
    session: AsyncSession,
    device: SoftwareAuthenticator,
) -> Any:
    """Run a passwordless login with ``device``.

    Args:
        webauthn (WebAuthnService): The WebAuthn service.
        session (AsyncSession): Active session.
        device (SoftwareAuthenticator): The authenticator signing.

    Returns:
        Any: The authenticated user.
    """
    options, challenge_id = await webauthn.authenticate_begin(session)
    return await webauthn.authenticate_complete(
        session,
        challenge_id=challenge_id,
        response=device.authenticate(options, origin=ORIGIN),
    )


async def _passkeys(session: AsyncSession) -> int:
    """Count the stored WebAuthn credentials.

    Args:
        session (AsyncSession): Active session.

    Returns:
        int: Number of credential rows.
    """
    result = await session.execute(
        select(func.count()).select_from(_RecoveryCredential)
    )
    return int(result.scalar_one())


async def _reset_token(service: UserAuthService, session: AsyncSession) -> str:
    """Issue a reset token for the owner and return its plaintext.

    Args:
        service (UserAuthService): The auth service.
        session (AsyncSession): Active session.

    Returns:
        str: The single-use reset token.
    """
    issued = await service.request_password_reset(session, email=EMAIL)
    await session.commit()
    assert issued is not None
    return issued.token


async def _enroll_mfa(
    service: UserAuthService,
    session: AsyncSession,
    user: Any,
) -> list[str]:
    """Enroll and confirm TOTP for ``user``.

    Args:
        service (UserAuthService): The auth service.
        session (AsyncSession): Active session.
        user (Any): The account enrolling.

    Returns:
        list[str]: The plaintext recovery codes.
    """
    secret, _, codes = await service.mfa_enroll(
        session, user=user, recovery_code_model=_RecoveryCode
    )
    await service.mfa_confirm(session, user=user, code=pyotp.TOTP(secret).now())
    await session.commit()
    return codes


class TestPasswordResetRevokesPasskeys:
    async def test_a_passkey_registered_before_the_reset_stops_signing_in(
        self, session: AsyncSession
    ) -> None:
        """The finding: a passkey bound while the account was held survived."""
        service, webauthn = _services()
        user = await _owner(service, session)
        intruder = await _register(webauthn, session, user)
        assert (await _sign_in(webauthn, session, intruder)).id == user.id

        token = await _reset_token(service, session)
        await service.confirm_password_reset(
            session, token=token, new_password=NEW_PASSWORD
        )
        await session.commit()

        with pytest.raises(UnauthorizedException, match="unknown credential"):
            await _sign_in(webauthn, session, intruder)

    async def test_the_owners_own_passkeys_go_too(self, session: AsyncSession) -> None:
        """Nothing tells the two apart, so every passkey of the user goes."""
        service, webauthn = _services()
        user = await _owner(service, session)
        await _register(webauthn, session, user)
        await _register(webauthn, session, user)
        assert await _passkeys(session) == 2

        token = await _reset_token(service, session)
        await service.confirm_password_reset(
            session, token=token, new_password=NEW_PASSWORD
        )
        await session.commit()

        assert await _passkeys(session) == 0

    async def test_other_accounts_keep_their_passkeys(
        self, session: AsyncSession
    ) -> None:
        service, webauthn = _services()
        user = await _owner(service, session)
        other, _ = await service.signup(
            session, email="other@example.com", password=PASSWORD
        )
        await session.commit()
        await _register(webauthn, session, user)
        other_device = await _register(webauthn, session, other)

        token = await _reset_token(service, session)
        await service.confirm_password_reset(
            session, token=token, new_password=NEW_PASSWORD
        )
        await session.commit()

        assert (await _sign_in(webauthn, session, other_device)).id == other.id

    async def test_opt_out_keeps_them(self, session: AsyncSession) -> None:
        service, webauthn = _services()
        user = await _owner(service, session)
        device = await _register(webauthn, session, user)

        token = await _reset_token(service, session)
        await service.confirm_password_reset(
            session,
            token=token,
            new_password=NEW_PASSWORD,
            revoke_passkeys=False,
        )
        await session.commit()

        assert (await _sign_in(webauthn, session, device)).id == user.id

    async def test_without_a_credential_model_nothing_fails(
        self, session: AsyncSession
    ) -> None:
        """A project without passkeys resets as before."""
        service, webauthn = _services(wire_model=False)
        user = await _owner(service, session)
        await _register(webauthn, session, user)

        token = await _reset_token(service, session)
        await service.confirm_password_reset(
            session, token=token, new_password=NEW_PASSWORD
        )
        await session.commit()

        assert await _passkeys(session) == 1
        assert await service.revoke_user_passkeys(session, user_id=user.id) == 0

    async def test_the_bundled_route_deletes_them_with_the_router_wiring(
        self, session: AsyncSession
    ) -> None:
        """``make_auth_router(webauthn=...)`` fills the model in the service."""
        service, webauthn = _services(wire_model=False)
        user = await _owner(service, session)
        intruder = await _register(webauthn, session, user)

        async def _factory() -> AsyncIterator[AsyncSession]:
            yield session

        app = FastAPI()
        register_exception_handlers(app)
        app.include_router(
            make_auth_router(
                service,
                session_factory=_factory,
                recovery_code_model=_RecoveryCode,
                webauthn=webauthn,
            )
        )
        assert service.webauthn_credential_model is _RecoveryCredential
        async with AsyncClient(
            transport=ASGITransport(app=app), base_url="http://t"
        ) as client:
            requested = await client.post(
                "/auth/password-reset/request", json={"email": EMAIL}
            )
            token = parse_qs(urlparse(requested.json()["reset_url"]).query)["token"][0]
            confirmed = await client.post(
                "/auth/password-reset/confirm",
                json={"token": token, "new_password": NEW_PASSWORD},
            )
            assert confirmed.status_code == 200, confirmed.text
            begin = await client.post("/auth/webauthn/authenticate/begin", json={})
            login = await client.post(
                "/auth/webauthn/authenticate/complete",
                json={
                    "challenge_id": begin.json()["challenge_id"],
                    "credential": intruder.authenticate(
                        begin.json()["options"], origin=ORIGIN
                    ),
                },
            )
        assert login.status_code == 401, login.text
        assert await _passkeys(session) == 0


class TestAuthenticatedPasswordChangeKeepsPasskeys:
    async def test_change_password_keeps_them(self, session: AsyncSession) -> None:
        """The caller proved the password; this is not recovery."""
        service, webauthn = _services()
        user = await _owner(service, session)
        device = await _register(webauthn, session, user)

        await service.change_password(
            session,
            user=user,
            current_password=PASSWORD,
            new_password=NEW_PASSWORD,
        )
        await session.commit()

        assert (await _sign_in(webauthn, session, device)).id == user.id


class TestMfaResetRevokesPasskeys:
    async def test_a_passkey_stops_signing_in_after_the_reset(
        self, session: AsyncSession
    ) -> None:
        service, webauthn = _services()
        user = await _owner(service, session)
        await _enroll_mfa(service, session, user)
        intruder = await _register(webauthn, session, user)

        await service.mfa_reset(session, user=user, recovery_code_model=_RecoveryCode)
        await session.commit()

        with pytest.raises(UnauthorizedException, match="unknown credential"):
            await _sign_in(webauthn, session, intruder)
        assert await _passkeys(session) == 0

    async def test_opt_out_keeps_them(self, session: AsyncSession) -> None:
        service, webauthn = _services()
        user = await _owner(service, session)
        await _enroll_mfa(service, session, user)
        device = await _register(webauthn, session, user)

        await service.mfa_reset(
            session,
            user=user,
            recovery_code_model=_RecoveryCode,
            revoke_passkeys=False,
        )
        await session.commit()

        assert (await _sign_in(webauthn, session, device)).id == user.id

    async def test_without_a_credential_model_nothing_fails(
        self, session: AsyncSession
    ) -> None:
        service, webauthn = _services(wire_model=False)
        user = await _owner(service, session)
        await _enroll_mfa(service, session, user)
        await _register(webauthn, session, user)

        await service.mfa_reset(session, user=user, recovery_code_model=_RecoveryCode)
        await session.commit()

        assert await _passkeys(session) == 1

    async def test_mfa_disable_by_the_owner_keeps_them(
        self, session: AsyncSession
    ) -> None:
        """Password plus a live code is the owner, not a recovery."""
        service, webauthn = _services()
        user = await _owner(service, session)
        codes = await _enroll_mfa(service, session, user)
        device = await _register(webauthn, session, user)

        await service.mfa_disable(
            session,
            user=user,
            password=PASSWORD,
            code=codes[0],
            recovery_code_model=_RecoveryCode,
        )
        await session.commit()

        assert user.totp_enabled_at is None
        assert (await _sign_in(webauthn, session, device)).id == user.id
