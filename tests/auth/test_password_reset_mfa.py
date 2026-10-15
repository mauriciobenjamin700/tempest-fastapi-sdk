"""MFA on the password-reset and reactivation routes (#534).

``POST /auth/login`` refuses to hand out a JWT pair to an account with
TOTP enrolled: it returns ``mfa_required=true`` plus a short-lived
``mfa_token`` and defers the pair to ``/auth/mfa/verify``. Two sibling
routes that also mint a pair straight from a single-use emailed token did
not apply that rule, so whoever controlled the mailbox entered without
the second factor:

* ``POST /auth/password-reset/confirm`` — the scenario the issue names.
* ``POST /auth/activate/{token}`` reached through
  ``POST /auth/activation/request`` — the same defect for an account an
  operator deactivated (the SDK's own CLI flips ``is_active``), then
  reactivated through the self-service link.

Both now branch exactly like ``/auth/login``.
"""

from __future__ import annotations

import time
from collections.abc import AsyncIterator, Sequence
from typing import Any
from urllib.parse import parse_qs, urlparse
from uuid import UUID

import pyotp
import pytest
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient
from sqlalchemy import select
from sqlalchemy.ext.asyncio import (
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)

from tempest_fastapi_sdk import (
    MFA_AMR,
    BaseModel,
    BaseUserModel,
    MFAMixin,
    UserAuthService,
    make_auth_router,
    make_user_recovery_code_model,
    make_user_refresh_token_model,
    make_user_token_model,
    register_exception_handlers,
    token_amr,
)
from tempest_fastapi_sdk.settings.mixins import AuthSettings, JWTSettings

PASSWORD: str = "strong-pass-12-chars"
NEW_PASSWORD: str = "brand-new-pass-12-chars"


class _ResetMfaUser(MFAMixin, BaseUserModel):
    __tablename__ = "reset_mfa_test_users"


_ResetMfaUserToken = make_user_token_model(
    user_table="reset_mfa_test_users",
    tablename="reset_mfa_test_user_tokens",
    class_name="_ResetMfaUserToken",
)
_ResetMfaRecoveryCode = make_user_recovery_code_model(
    user_table="reset_mfa_test_users",
    tablename="reset_mfa_test_recovery_codes",
    class_name="_ResetMfaRecoveryCode",
)
_ResetMfaRefreshToken = make_user_refresh_token_model(
    user_table="reset_mfa_test_users",
    tablename="reset_mfa_test_refresh_tokens",
    class_name="_ResetMfaRefreshToken",
)


class _LegacyResetService(UserAuthService):
    """Overrides written before #534, with the signatures of that day.

    Neither ``confirm_password_reset`` nor ``issue_token_pair`` changed
    shape, so an override keeps being called exactly as before and the
    MFA gate — which lives in the router, not the service — still
    applies on top of it.
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

    async def issue_token_pair(  # type: ignore[override]
        self,
        session: AsyncSession,
        user: BaseUserModel,
        *,
        family_id: UUID | None = None,
    ) -> tuple[str, str]:
        """Delegate without ``amr``, as overrides written before #525 do.

        Args:
            session (AsyncSession): Active SQLAlchemy session.
            user (BaseUserModel): The authenticated user.
            family_id (UUID | None): Rotation lineage.

        Returns:
            tuple[str, str]: Whatever the base class issues.
        """
        return await super().issue_token_pair(session, user, family_id=family_id)


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
    auto_activate: bool = True,
    service_class: type[UserAuthService] = UserAuthService,
) -> UserAuthService:
    """Build an auth service with MFA on and links surfaced in the body.

    Args:
        auto_activate (bool): Whether a signup is born active. ``False``
            issues an activation link instead, the flow the reactivation
            test drives.
        service_class (type[UserAuthService]): The class to instantiate,
            a subclass when the test exercises an override.

    Returns:
        UserAuthService: The configured service.
    """
    return service_class(
        user_model=_ResetMfaUser,
        token_model=_ResetMfaUserToken,  # type: ignore[arg-type]
        auth_settings=AuthSettings(
            _env_file=None,
            AUTH_AUTO_ACTIVATE=auto_activate,
            AUTH_RETURN_TOKEN_IN_RESPONSE=True,
            AUTH_MFA_ENABLED=True,
            AUTH_MFA_ISSUER="Tempest Test",
        ),
        jwt_settings=JWTSettings(_env_file=None, JWT_SECRET="x" * 32),
        email=None,
        refresh_token_model=_ResetMfaRefreshToken,  # type: ignore[arg-type]
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
    app.include_router(
        make_auth_router(
            service,
            session_factory=_factory,
            recovery_code_model=_ResetMfaRecoveryCode,
        )
    )
    return AsyncClient(transport=ASGITransport(app=app), base_url="http://t")


def _next_code(secret: str) -> str:
    """Return the code of the step after the current one.

    Args:
        secret (str): The enrolled Base32 secret.

    Returns:
        str: The 6-digit code for ``now + 30s``.
    """
    return pyotp.TOTP(secret).at(int(time.time()) + 30)


def _token_from_url(url: str) -> str:
    """Pull the ``token`` query value out of a link the SDK built.

    Args:
        url (str): The activation or reset URL.

    Returns:
        str: The plaintext single-use token.
    """
    return parse_qs(urlparse(url).query)["token"][0]


async def _signup_and_login(client: AsyncClient, email: str) -> dict[str, Any]:
    """Create an active account and log in with the password.

    Args:
        client (AsyncClient): The test client.
        email (str): The account address.

    Returns:
        dict[str, Any]: The login response body.
    """
    credentials = {"email": email, "password": PASSWORD}
    signed_up = await client.post("/auth/signup", json=credentials)
    assert signed_up.status_code == 201, signed_up.text
    response = await client.post("/auth/login", json=credentials)
    assert response.status_code == 200, response.text
    body: dict[str, Any] = response.json()
    return body


async def _enroll_and_confirm(
    client: AsyncClient, access_token: str
) -> tuple[str, dict[str, Any]]:
    """Enroll TOTP and confirm it through the routes.

    Args:
        client (AsyncClient): The test client.
        access_token (str): The bearer of the confirming session.

    Returns:
        tuple[str, dict[str, Any]]: The secret and the confirm response body.
    """
    bearer = {"Authorization": f"Bearer {access_token}"}
    enrolled = await client.post("/auth/mfa/enroll", headers=bearer)
    assert enrolled.status_code == 200, enrolled.text
    secret: str = enrolled.json()["secret"]
    confirmed = await client.post(
        "/auth/mfa/confirm",
        headers=bearer,
        json={"code": pyotp.TOTP(secret).now()},
    )
    assert confirmed.status_code == 200, confirmed.text
    body: dict[str, Any] = confirmed.json()
    return secret, body


def _amr(service: UserAuthService, token: str) -> Sequence[str] | None:
    """Decode ``token`` and return its ``amr``.

    Args:
        service (UserAuthService): Holds the JWT helper.
        token (str): An access token.

    Returns:
        Sequence[str] | None: The claim, or ``None`` when absent.
    """
    return token_amr(service.jwt.decode(token))


class TestPasswordResetWithMfa:
    async def test_password_reset_defers_the_pair_to_mfa_verify(
        self, session: AsyncSession
    ) -> None:
        """A reset on an MFA account answers the challenge, not a pair."""
        service = _service()
        async with _client(service, session) as client:
            body = await _signup_and_login(client, "reset-mfa@a.com")
            secret, _ = await _enroll_and_confirm(client, body["access_token"])

            requested = await client.post(
                "/auth/password-reset/request",
                json={"email": "reset-mfa@a.com"},
            )
            assert requested.status_code == 202, requested.text
            token = _token_from_url(requested.json()["reset_url"])

            confirmation = await client.post(
                "/auth/password-reset/confirm",
                json={"token": token, "new_password": NEW_PASSWORD},
            )
            assert confirmation.status_code == 200, confirmation.text
            payload = confirmation.json()

            assert payload["mfa_required"] is True
            assert payload["access_token"] is None
            assert payload["refresh_token"] is None
            assert payload["mfa_token"]

            stale = await client.post(
                "/auth/login",
                json={"email": "reset-mfa@a.com", "password": PASSWORD},
            )
            assert stale.status_code == 401, stale.text
            fresh = await client.post(
                "/auth/login",
                json={"email": "reset-mfa@a.com", "password": NEW_PASSWORD},
            )
            assert fresh.json()["mfa_required"] is True

            verified = await client.post(
                "/auth/mfa/verify",
                json={
                    "mfa_token": payload["mfa_token"],
                    "code": _next_code(secret),
                },
            )
            assert verified.status_code == 200, verified.text
        assert _amr(service, verified.json()["access_token"]) == MFA_AMR

    async def test_a_legacy_service_override_still_gets_the_challenge(
        self, session: AsyncSession
    ) -> None:
        """An override written before #534 keeps working — gate included."""
        service = _service(service_class=_LegacyResetService)
        async with _client(service, session) as client:
            body = await _signup_and_login(client, "legacy@a.com")
            await _enroll_and_confirm(client, body["access_token"])

            requested = await client.post(
                "/auth/password-reset/request",
                json={"email": "legacy@a.com"},
            )
            assert requested.status_code == 202, requested.text
            token = _token_from_url(requested.json()["reset_url"])

            confirmation = await client.post(
                "/auth/password-reset/confirm",
                json={"token": token, "new_password": NEW_PASSWORD},
            )
            assert confirmation.status_code == 200, confirmation.text
            payload = confirmation.json()
        assert payload["mfa_required"] is True
        assert payload["access_token"] is None
        assert payload["mfa_token"]

    async def test_password_reset_without_mfa_still_returns_the_pair(
        self, session: AsyncSession
    ) -> None:
        """An account with no factor keeps the one-step response."""
        service = _service()
        async with _client(service, session) as client:
            body = await _signup_and_login(client, "reset-plain@a.com")
            assert body["access_token"]

            requested = await client.post(
                "/auth/password-reset/request",
                json={"email": "reset-plain@a.com"},
            )
            assert requested.status_code == 202, requested.text
            token = _token_from_url(requested.json()["reset_url"])

            confirmation = await client.post(
                "/auth/password-reset/confirm",
                json={"token": token, "new_password": NEW_PASSWORD},
            )
            assert confirmation.status_code == 200, confirmation.text
            payload = confirmation.json()
        assert payload["mfa_required"] is False
        assert payload["access_token"]
        assert payload["refresh_token"]
        assert payload["mfa_token"] is None


class TestReactivationWithMfa:
    async def test_reactivating_an_mfa_account_defers_the_pair(
        self, session: AsyncSession
    ) -> None:
        """The reactivation link obeys the same rule as the reset link."""
        service = _service(auto_activate=False)
        async with _client(service, session) as client:
            signed_up = await client.post(
                "/auth/signup",
                json={"email": "reactivate@a.com", "password": PASSWORD},
            )
            assert signed_up.status_code == 201, signed_up.text
            first_token = _token_from_url(signed_up.json()["activation_url"])
            activated = await client.post(f"/auth/activate/{first_token}")
            assert activated.status_code == 200, activated.text
            assert activated.json()["access_token"]
            secret, _ = await _enroll_and_confirm(
                client, activated.json()["access_token"]
            )

            user = (
                await session.execute(
                    select(_ResetMfaUser).where(
                        _ResetMfaUser.email == "reactivate@a.com"
                    )
                )
            ).scalar_one()
            user.is_active = False
            await session.commit()

            requested = await client.post(
                "/auth/activation/request",
                json={"email": "reactivate@a.com"},
            )
            assert requested.status_code == 202, requested.text
            second_token = _token_from_url(requested.json()["activation_url"])

            reactivated = await client.post(f"/auth/activate/{second_token}")
            assert reactivated.status_code == 200, reactivated.text
            payload = reactivated.json()

            assert payload["mfa_required"] is True
            assert payload["access_token"] is None
            assert payload["refresh_token"] is None
            assert payload["mfa_token"]

            verified = await client.post(
                "/auth/mfa/verify",
                json={
                    "mfa_token": payload["mfa_token"],
                    "code": _next_code(secret),
                },
            )
            assert verified.status_code == 200, verified.text
        assert _amr(service, verified.json()["access_token"]) == MFA_AMR

    async def test_activating_a_fresh_account_still_returns_the_pair(
        self, session: AsyncSession
    ) -> None:
        """A brand-new account has no factor and keeps the one-step response."""
        service = _service(auto_activate=False)
        async with _client(service, session) as client:
            signed_up = await client.post(
                "/auth/signup",
                json={"email": "fresh@a.com", "password": PASSWORD},
            )
            assert signed_up.status_code == 201, signed_up.text
            token = _token_from_url(signed_up.json()["activation_url"])

            activated = await client.post(f"/auth/activate/{token}")
            assert activated.status_code == 200, activated.text
            payload = activated.json()
        assert payload["mfa_required"] is False
        assert payload["access_token"]
        assert payload["refresh_token"]
        assert payload["mfa_token"] is None
