"""The ``amr`` claim and the session cut at MFA confirmation (#525).

Before #525 the access token said nothing about how the session was
authenticated, and ``mfa_confirm`` left every refresh token alive: the
refresh of a session opened with the password alone, before the factor
existed, minted a token indistinguishable from one that passed MFA.
"""

from __future__ import annotations

import time
from collections.abc import AsyncIterator, Sequence
from typing import Any
from uuid import UUID

import pyotp
import pytest
from fastapi import Depends, FastAPI
from httpx import ASGITransport, AsyncClient
from sqlalchemy.ext.asyncio import (
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)

from tempest_fastapi_sdk import (
    MFA_AMR,
    PASSWORD_AMR,
    BaseModel,
    BaseUserModel,
    MFAMixin,
    UserAuthService,
    make_auth_router,
    make_mfa_required_dependency,
    make_user_recovery_code_model,
    make_user_refresh_token_model,
    make_user_token_model,
    register_exception_handlers,
    token_amr,
)
from tempest_fastapi_sdk.settings.mixins import AuthSettings, JWTSettings

PASSWORD: str = "strong-pass-12-chars"


class _AmrUser(MFAMixin, BaseUserModel):
    __tablename__ = "amr_test_users"


_AmrUserToken = make_user_token_model(
    user_table="amr_test_users",
    tablename="amr_test_user_tokens",
    class_name="_AmrUserToken",
)
_AmrRecoveryCode = make_user_recovery_code_model(
    user_table="amr_test_users",
    tablename="amr_test_recovery_codes",
    class_name="_AmrRecoveryCode",
)
_AmrRefreshToken = make_user_refresh_token_model(
    user_table="amr_test_users",
    tablename="amr_test_refresh_tokens",
    class_name="_AmrRefreshToken",
)


class _LegacyIssueService(UserAuthService):
    """Override of ``issue_token_pair`` written before ``amr`` existed."""

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
    db_refresh: bool = True,
    service_class: type[UserAuthService] = UserAuthService,
) -> UserAuthService:
    """Build an auth service with MFA on.

    Args:
        db_refresh (bool): Wire the DB-backed refresh tokens; ``False``
            keeps the stateless JWT refresh token.
        service_class (type[UserAuthService]): The class to instantiate.

    Returns:
        UserAuthService: The configured service.
    """
    return service_class(
        user_model=_AmrUser,
        token_model=_AmrUserToken,  # type: ignore[arg-type]
        auth_settings=AuthSettings(
            _env_file=None,
            AUTH_AUTO_ACTIVATE=True,
            AUTH_MFA_ENABLED=True,
            AUTH_MFA_ISSUER="Tempest Test",
        ),
        jwt_settings=JWTSettings(_env_file=None, JWT_SECRET="x" * 32),
        email=None,
        refresh_token_model=_AmrRefreshToken if db_refresh else None,  # type: ignore[arg-type]
    )


def _client(service: UserAuthService, session: AsyncSession) -> AsyncClient:
    """Mount the auth router plus an MFA-gated route on a throwaway app.

    Args:
        service (UserAuthService): The configured service.
        session (AsyncSession): The session every request shares.

    Returns:
        AsyncClient: A client bound to the app over ASGI.
    """

    async def _factory() -> AsyncIterator[AsyncSession]:
        yield session

    require_mfa = make_mfa_required_dependency(service.jwt)
    app = FastAPI()
    register_exception_handlers(app)
    app.include_router(
        make_auth_router(
            service,
            session_factory=_factory,
            recovery_code_model=_AmrRecoveryCode,
        )
    )

    @app.get("/staff-only")
    async def staff_only(
        claims: dict[str, Any] = Depends(require_mfa),
    ) -> dict[str, Any]:
        return {"amr": claims["amr"]}

    return AsyncClient(transport=ASGITransport(app=app), base_url="http://t")


def _amr(service: UserAuthService, token: str) -> Sequence[str] | None:
    """Decode ``token`` and return its ``amr``.

    Args:
        service (UserAuthService): Holds the JWT helper.
        token (str): An access token.

    Returns:
        Sequence[str] | None: The claim, or ``None`` when absent.
    """
    return token_amr(service.jwt.decode(token))


def _next_code(secret: str) -> str:
    """Return the code of the step after the current one.

    Args:
        secret (str): The enrolled Base32 secret.

    Returns:
        str: The 6-digit code for ``now + 30s``.
    """
    return pyotp.TOTP(secret).at(int(time.time()) + 30)


async def _signup_and_login(client: AsyncClient, email: str) -> dict[str, Any]:
    """Create an account and log in with the password.

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


async def _mfa_login(client: AsyncClient, email: str, secret: str) -> dict[str, Any]:
    """Run the two-step login.

    Args:
        client (AsyncClient): The test client.
        email (str): The account address.
        secret (str): The enrolled TOTP secret.

    Returns:
        dict[str, Any]: The ``/auth/mfa/verify`` response body.
    """
    step_one = await client.post(
        "/auth/login", json={"email": email, "password": PASSWORD}
    )
    assert step_one.json()["mfa_required"] is True
    step_two = await client.post(
        "/auth/mfa/verify",
        json={"mfa_token": step_one.json()["mfa_token"], "code": _next_code(secret)},
    )
    assert step_two.status_code == 200, step_two.text
    body: dict[str, Any] = step_two.json()
    return body


class TestAmrClaim:
    async def test_password_login_is_pwd(self, session: AsyncSession) -> None:
        """``POST /auth/login`` without MFA stamps ``amr == ["pwd"]``."""
        service = _service()
        async with _client(service, session) as client:
            body = await _signup_and_login(client, "pwd@a.com")
        assert _amr(service, body["access_token"]) == PASSWORD_AMR

    async def test_mfa_verify_adds_otp(self, session: AsyncSession) -> None:
        """Login then ``/auth/mfa/verify`` stamps ``["pwd", "otp"]``."""
        service = _service()
        async with _client(service, session) as client:
            body = await _signup_and_login(client, "verify@a.com")
            secret, _ = await _enroll_and_confirm(client, body["access_token"])
            verified = await _mfa_login(client, "verify@a.com", secret)
        assert _amr(service, verified["access_token"]) == MFA_AMR

    async def test_refresh_of_an_mfa_family_keeps_otp(
        self, session: AsyncSession
    ) -> None:
        """A family born at ``/auth/mfa/verify`` stays MFA across rotations."""
        service = _service()
        async with _client(service, session) as client:
            body = await _signup_and_login(client, "keep@a.com")
            secret, _ = await _enroll_and_confirm(client, body["access_token"])
            verified = await _mfa_login(client, "keep@a.com", secret)
            first = await client.post(
                "/auth/refresh", json={"refresh_token": verified["refresh_token"]}
            )
            second = await client.post(
                "/auth/refresh", json={"refresh_token": first.json()["refresh_token"]}
            )
        assert first.status_code == 200, first.text
        assert second.status_code == 200, second.text
        assert _amr(service, second.json()["access_token"]) == MFA_AMR

    async def test_stateless_refresh_repeats_the_amr(
        self, session: AsyncSession
    ) -> None:
        """Without a refresh model, the refresh JWT carries ``amr`` forward."""
        service = _service(db_refresh=False)
        async with _client(service, session) as client:
            body = await _signup_and_login(client, "stateless@a.com")
            refreshed = await client.post(
                "/auth/refresh", json={"refresh_token": body["refresh_token"]}
            )
        assert refreshed.status_code == 200, refreshed.text
        assert _amr(service, refreshed.json()["access_token"]) == PASSWORD_AMR


class TestConfirmEndsOlderSessions:
    async def test_pre_mfa_refresh_is_refused_after_confirm(
        self, session: AsyncSession
    ) -> None:
        """The issue's repro: the pre-MFA refresh token answers 401 now."""
        service = _service()
        async with _client(service, session) as client:
            body = await _signup_and_login(client, "cut@a.com")
            await _enroll_and_confirm(client, body["access_token"])
            stale = await client.post(
                "/auth/refresh", json={"refresh_token": body["refresh_token"]}
            )
        assert stale.status_code == 401, stale.text

    async def test_confirm_hands_the_caller_an_mfa_session(
        self, session: AsyncSession
    ) -> None:
        """The confirming device keeps a session, now marked as MFA."""
        service = _service()
        async with _client(service, session) as client:
            body = await _signup_and_login(client, "handover@a.com")
            _, confirmed = await _enroll_and_confirm(client, body["access_token"])
            refreshed = await client.post(
                "/auth/refresh", json={"refresh_token": confirmed["refresh_token"]}
            )
        assert _amr(service, confirmed["access_token"]) == MFA_AMR
        assert refreshed.status_code == 200, refreshed.text
        assert _amr(service, refreshed.json()["access_token"]) == MFA_AMR

    async def test_opt_out_keeps_the_session_but_never_promotes_it(
        self, session: AsyncSession
    ) -> None:
        """``revoke_sessions=False``: the old refresh works, still ``["pwd"]``."""
        service = _service()
        user, _ = await service.signup(session, email="optout@a.com", password=PASSWORD)
        _, refresh = await service.issue_token_pair(session, user)
        secret, _, _ = await service.mfa_enroll(
            session, user=user, recovery_code_model=_AmrRecoveryCode
        )
        await service.mfa_confirm(
            session,
            user=user,
            code=pyotp.TOTP(secret).now(),
            revoke_sessions=False,
        )
        await session.commit()

        _, access, _ = await service.refresh_tokens(session, refresh_token=refresh)

        assert _amr(service, access) == PASSWORD_AMR


class TestMfaRequiredDependency:
    async def test_password_only_token_is_403_mfa_required(
        self, session: AsyncSession
    ) -> None:
        """A valid token without a second factor is refused with its own code."""
        service = _service()
        async with _client(service, session) as client:
            body = await _signup_and_login(client, "gate-pwd@a.com")
            response = await client.get(
                "/staff-only",
                headers={"Authorization": f"Bearer {body['access_token']}"},
            )
        assert response.status_code == 403, response.text
        assert response.json()["code"] == "MFA_REQUIRED"

    async def test_mfa_token_is_accepted(self, session: AsyncSession) -> None:
        """The token out of ``/auth/mfa/verify`` passes the gate."""
        service = _service()
        async with _client(service, session) as client:
            body = await _signup_and_login(client, "gate-mfa@a.com")
            secret, _ = await _enroll_and_confirm(client, body["access_token"])
            verified = await _mfa_login(client, "gate-mfa@a.com", secret)
            response = await client.get(
                "/staff-only",
                headers={"Authorization": f"Bearer {verified['access_token']}"},
            )
        assert response.status_code == 200, response.text
        assert response.json() == {"amr": list(MFA_AMR)}

    async def test_token_without_amr_is_refused(self, session: AsyncSession) -> None:
        """A token minted before the claim existed never counts as MFA."""
        service = _service()
        legacy = service.jwt.encode(
            {"sub": "00000000-0000-0000-0000-000000000001", "typ": "access"}
        )
        async with _client(service, session) as client:
            response = await client.get(
                "/staff-only", headers={"Authorization": f"Bearer {legacy}"}
            )
        assert response.status_code == 403, response.text
        assert response.json()["code"] == "MFA_REQUIRED"

    async def test_missing_token_is_401(self, session: AsyncSession) -> None:
        """No token at all is still an authentication failure."""
        service = _service()
        async with _client(service, session) as client:
            response = await client.get("/staff-only")
        assert response.status_code == 401, response.text


class TestOverrideWithoutAmr:
    async def test_old_issue_token_pair_override_keeps_working(
        self, session: AsyncSession
    ) -> None:
        """No ``TypeError``: the flow runs and the token never claims MFA."""
        service = _service(service_class=_LegacyIssueService)
        async with _client(service, session) as client:
            body = await _signup_and_login(client, "legacy@a.com")
            secret, confirmed = await _enroll_and_confirm(client, body["access_token"])
            verified = await _mfa_login(client, "legacy@a.com", secret)
            refreshed = await client.post(
                "/auth/refresh", json={"refresh_token": verified["refresh_token"]}
            )
        assert refreshed.status_code == 200, refreshed.text
        assert _amr(service, confirmed["access_token"]) == PASSWORD_AMR
        assert _amr(service, verified["access_token"]) == PASSWORD_AMR
