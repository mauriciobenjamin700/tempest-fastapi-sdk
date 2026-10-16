"""Social login asks an account with TOTP enrolled for the second factor.

Through 0.313.0 ``GET /auth/oauth/{provider}/callback`` and
``POST /auth/oauth/{provider}/token`` minted the JWT pair straight from the
provider's word, so an account with TOTP enrolled skipped the factor it
enrolled — the provider proves the identity, not the code on the user's
phone. Both routes now branch exactly like ``POST /auth/login``: an
enrolled account gets ``mfa_required=true`` plus an ``mfa_token``, no pair
and no session cookie, and ``/auth/mfa/verify`` opens the session stamped
``amr=["ext", "otp"]`` — each method one that ran. An account without MFA
keeps the one-step response, ``amr=["ext"]``.
"""

from __future__ import annotations

import logging
import re
import time
from collections.abc import AsyncIterator, Sequence
from typing import Any
from urllib.parse import parse_qs, urlparse
from uuid import UUID

import pyotp
import pytest
from fastapi import Depends, FastAPI
from httpx import ASGITransport, AsyncClient, Response
from sqlalchemy.ext.asyncio import (
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)

from tempest_fastapi_sdk import (
    AMR_EXTERNAL,
    AMR_OTP,
    AMR_PASSWORD,
    BaseModel,
    BaseUserModel,
    MFAMixin,
    NameMixin,
    OAuthTokens,
    OAuthUser,
    UserAuthService,
    make_auth_router,
    make_mfa_required_dependency,
    make_user_oauth_account_model,
    make_user_recovery_code_model,
    make_user_refresh_token_model,
    make_user_token_model,
    register_exception_handlers,
    token_amr,
)
from tempest_fastapi_sdk.settings.mixins import AuthSettings, JWTSettings

PASSWORD: str = "strong-pass-12-chars"
EMAIL: str = "ana@example.com"
JWT_SHAPE: re.Pattern[str] = re.compile(r"eyJ[\w-]+\.[\w-]+\.[\w-]+")
"""A compact JWS, the shape of every token the SDK signs."""


class _SocialMfaUser(MFAMixin, NameMixin, BaseUserModel):
    __tablename__ = "oauth_mfa_test_users"


_SocialMfaUserToken = make_user_token_model(
    user_table="oauth_mfa_test_users",
    tablename="oauth_mfa_test_user_tokens",
    class_name="_SocialMfaUserToken",
)
_SocialMfaRecoveryCode = make_user_recovery_code_model(
    user_table="oauth_mfa_test_users",
    tablename="oauth_mfa_test_recovery_codes",
    class_name="_SocialMfaRecoveryCode",
)
_SocialMfaRefreshToken = make_user_refresh_token_model(
    user_table="oauth_mfa_test_users",
    tablename="oauth_mfa_test_refresh_tokens",
    class_name="_SocialMfaRefreshToken",
)
_SocialMfaAccount = make_user_oauth_account_model(
    user_table="oauth_mfa_test_users",
    tablename="oauth_mfa_test_oauth_accounts",
    class_name="_SocialMfaAccount",
)


class _Provider:
    """A provider that speaks both halves of social login.

    Satisfies ``OAuthClient`` and ``OAuthAudienceVerifier``; every token is
    accepted as minted for this application.
    """

    provider_name: str = "google"

    def __init__(self, profile: OAuthUser) -> None:
        """Initialize.

        Args:
            profile (OAuthUser): What ``fetch_user`` returns.
        """
        self.profile: OAuthUser = profile

    def build_authorize_url(self, *, state: str, **extra: str) -> str:
        """Render a fake consent URL.

        Args:
            state (str): CSRF state to echo.
            **extra (str): Ignored.

        Returns:
            str: The fake consent URL.
        """
        return f"https://idp.test/auth?state={state}"

    async def exchange_code(self, code: str) -> OAuthTokens:
        """Return a fixed bundle.

        Args:
            code (str): Ignored.

        Returns:
            OAuthTokens: A fixed bundle.
        """
        return OAuthTokens(access_token="provider-access", token_type="Bearer")

    async def fetch_user(self, tokens: OAuthTokens) -> OAuthUser:
        """Return the configured profile.

        Args:
            tokens (OAuthTokens): Ignored.

        Returns:
            OAuthUser: The configured identity.
        """
        return self.profile

    async def verify_token_audience(self, tokens: OAuthTokens) -> None:
        """Accept every token.

        Args:
            tokens (OAuthTokens): Ignored.
        """


class _LegacyOAuthService(UserAuthService):
    """An override of ``login_with_oauth`` written before it was bypassed."""

    async def login_with_oauth(
        self,
        session: AsyncSession,
        profile: OAuthUser,
        *,
        locale: str,
        link_by_verified_email: bool | None = None,
        allow_account_creation: bool | None = None,
    ) -> tuple[BaseUserModel, str, str]:
        """Delegate unchanged.

        Args:
            session (AsyncSession): Active SQLAlchemy session.
            profile (OAuthUser): The provider identity.
            locale (str): Request locale.
            link_by_verified_email (bool | None): Forwarded.
            allow_account_creation (bool | None): Forwarded.

        Returns:
            tuple[BaseUserModel, str, str]: Whatever the base issues.
        """
        return await super().login_with_oauth(
            session,
            profile,
            locale=locale,
            link_by_verified_email=link_by_verified_email,
            allow_account_creation=allow_account_creation,
        )


class _LegacyMfaTokenService(UserAuthService):
    """An ``issue_mfa_token`` override written before ``amr`` existed."""

    def issue_mfa_token(self, user: BaseUserModel) -> str:  # type: ignore[override]
        """Delegate without ``amr``.

        Args:
            user (BaseUserModel): The account that passed step one.

        Returns:
            str: The base token, minted with the default ``amr``.
        """
        return super().issue_mfa_token(user, amr=None)


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
    service_class: type[UserAuthService] = UserAuthService,
) -> UserAuthService:
    """Build a service with MFA, social login and both deliveries on.

    Args:
        service_class (type[UserAuthService]): The class to instantiate.

    Returns:
        UserAuthService: The configured service.
    """
    return service_class(
        user_model=_SocialMfaUser,
        token_model=_SocialMfaUserToken,
        auth_settings=AuthSettings(
            _env_file=None,
            AUTH_AUTO_ACTIVATE=True,
            AUTH_MFA_ENABLED=True,
            AUTH_MFA_ISSUER="Tempest Test",
            AUTH_OAUTH_ENABLED=True,
            AUTH_OAUTH_LINK_BY_VERIFIED_EMAIL=True,
            AUTH_TOKEN_DELIVERY="both",
            AUTH_COOKIE_SECURE=False,
        ),
        jwt_settings=JWTSettings(_env_file=None, JWT_SECRET="x" * 32),
        email=None,
        refresh_token_model=_SocialMfaRefreshToken,
        oauth_account_model=_SocialMfaAccount,
    )


def _client(service: UserAuthService, session: AsyncSession) -> AsyncClient:
    """Mount the auth router plus one MFA-gated route.

    Args:
        service (UserAuthService): The configured service.
        session (AsyncSession): The session every request shares.

    Returns:
        AsyncClient: A client bound to the app. ``GET /staff`` answers 200
        only to a session that passed a second factor.
    """

    async def _factory() -> AsyncIterator[AsyncSession]:
        yield session

    app = FastAPI()
    register_exception_handlers(app)
    app.include_router(
        make_auth_router(
            service,
            session_factory=_factory,
            recovery_code_model=_SocialMfaRecoveryCode,
            oauth_clients={
                "google": _Provider(
                    OAuthUser(
                        provider="google",
                        subject="sub-1",
                        email=EMAIL,
                        email_verified=True,
                        name="Ana",
                    )
                )
            },
        )
    )
    require_mfa = make_mfa_required_dependency(service.jwt)

    @app.get("/staff")
    async def staff(claims: dict[str, Any] = Depends(require_mfa)) -> dict[str, str]:
        """Answer only an MFA session.

        Args:
            claims (dict[str, Any]): The verified access-token claims.

        Returns:
            dict[str, str]: The subject.
        """
        return {"sub": str(claims["sub"])}

    return AsyncClient(transport=ASGITransport(app=app), base_url="http://t")


async def _enrolled_account(client: AsyncClient) -> str:
    """Create the password account the provider identity links to, with TOTP.

    Args:
        client (AsyncClient): The test client.

    Returns:
        str: The enrolled Base32 secret.
    """
    credentials = {"email": EMAIL, "password": PASSWORD}
    signed_up = await client.post("/auth/signup", json=credentials)
    assert signed_up.status_code == 201, signed_up.text
    bearer = {"Authorization": f"Bearer {signed_up.json()['access_token']}"}
    enrolled = await client.post("/auth/mfa/enroll", headers=bearer)
    assert enrolled.status_code == 200, enrolled.text
    secret: str = enrolled.json()["secret"]
    confirmed = await client.post(
        "/auth/mfa/confirm",
        headers=bearer,
        json={"code": pyotp.TOTP(secret).now()},
    )
    assert confirmed.status_code == 200, confirmed.text
    client.cookies.clear()
    return secret


async def _callback(client: AsyncClient) -> Response:
    """Drive the redirect half and return the callback's response.

    Args:
        client (AsyncClient): The test client; its jar carries the state
            cookie between the two calls, as a browser would.

    Returns:
        Response: The callback response.
    """
    redirect = await client.get("/auth/oauth/google/login")
    state = parse_qs(urlparse(redirect.headers["location"]).query)["state"][0]
    return await client.get(
        "/auth/oauth/google/callback",
        params={"code": "the-code", "state": state},
    )


async def _token_login(client: AsyncClient) -> Response:
    """Log in through the token-in-hand route.

    Args:
        client (AsyncClient): The test client.

    Returns:
        Response: The route's response.
    """
    return await client.post(
        "/auth/oauth/google/token", json={"access_token": "provider-access"}
    )


def _next_code(secret: str) -> str:
    """Return the code of the step after the current one.

    Args:
        secret (str): The enrolled Base32 secret.

    Returns:
        str: The 6-digit code for ``now + 30s``.
    """
    return pyotp.TOTP(secret).at(int(time.time()) + 30)


def _amr(service: UserAuthService, token: str) -> Sequence[str] | None:
    """Decode ``token`` and return its ``amr``.

    Args:
        service (UserAuthService): Holds the JWT helper.
        token (str): An access token.

    Returns:
        Sequence[str] | None: The claim, or ``None`` when absent.
    """
    return token_amr(service.jwt.decode(token))


def _session_cookies(response: Response) -> list[str]:
    """Return the ``Set-Cookie`` values that carry a signed token.

    Args:
        response (Response): Any response.

    Returns:
        list[str]: The headers holding a JWT — the session cookies; the
        cleared OAuth state cookie does not count.
    """
    return [
        header
        for header in response.headers.get_list("set-cookie")
        if JWT_SHAPE.search(header) or "refresh" in header.split("=", 1)[0]
    ]


@pytest.mark.parametrize("route", [_callback, _token_login], ids=["callback", "token"])
class TestAnEnrolledAccountIsChallenged:
    async def test_the_route_answers_the_challenge_not_a_pair(
        self, session: AsyncSession, route: Any
    ) -> None:
        """No pair in the body, no session cookie, an ``mfa_token``."""
        service = _service()
        async with _client(service, session) as client:
            await _enrolled_account(client)
            response = await route(client)
        assert response.status_code == 200, response.text
        body = response.json()
        assert body["mfa_required"] is True
        assert body["mfa_token"]
        assert body["access_token"] is None
        assert body["refresh_token"] is None
        assert _session_cookies(response) == []

    async def test_mfa_verify_opens_a_session_stamped_ext_and_otp(
        self, session: AsyncSession, route: Any
    ) -> None:
        """The pair names both methods that ran, and passes the MFA gate."""
        service = _service()
        async with _client(service, session) as client:
            secret = await _enrolled_account(client)
            challenge = (await route(client)).json()
            verified = await client.post(
                "/auth/mfa/verify",
                json={"mfa_token": challenge["mfa_token"], "code": _next_code(secret)},
            )
            assert verified.status_code == 200, verified.text
            access: str = verified.json()["access_token"]
            gated = await client.get(
                "/staff", headers={"Authorization": f"Bearer {access}"}
            )
            refreshed = await client.post(
                "/auth/refresh",
                json={"refresh_token": verified.json()["refresh_token"]},
            )
        assert _amr(service, access) == (AMR_EXTERNAL, AMR_OTP)
        assert gated.status_code == 200, gated.text
        assert refreshed.status_code == 200, refreshed.text
        assert _amr(service, refreshed.json()["access_token"]) == (
            AMR_EXTERNAL,
            AMR_OTP,
        )


@pytest.mark.parametrize("route", [_callback, _token_login], ids=["callback", "token"])
async def test_an_account_without_mfa_keeps_the_one_step_login(
    session: AsyncSession, route: Any
) -> None:
    """Unchanged: the pair, ``amr=["ext"]``, refused by the MFA gate."""
    service = _service()
    async with _client(service, session) as client:
        response = await route(client)
        body = response.json()
        gated = await client.get(
            "/staff", headers={"Authorization": f"Bearer {body['access_token']}"}
        )
    assert response.status_code == 200, response.text
    assert body["mfa_required"] is False
    assert _amr(service, body["access_token"]) == (AMR_EXTERNAL,)
    assert gated.status_code == 403
    assert gated.json()["code"] == "MFA_REQUIRED"


def test_a_login_with_oauth_override_is_reported_as_bypassed(
    session: AsyncSession, caplog: pytest.LogCaptureFixture
) -> None:
    """The routes no longer call it, and the router says so at build time."""
    with caplog.at_level(logging.WARNING):
        _client(_service(_LegacyOAuthService), session)
    assert any(
        "overrides login_with_oauth()" in record.getMessage()
        for record in caplog.records
    )


def test_the_base_service_reports_nothing(
    session: AsyncSession, caplog: pytest.LogCaptureFixture
) -> None:
    """The warning names a real override, not every service."""
    with caplog.at_level(logging.WARNING):
        _client(_service(), session)
    assert not any(
        "login_with_oauth" in record.getMessage()
        or "issue_mfa_token" in record.getMessage()
        for record in caplog.records
    )


async def test_an_issue_mfa_token_override_without_amr_keeps_the_old_stamp(
    session: AsyncSession, caplog: pytest.LogCaptureFixture
) -> None:
    """The legacy override still logs in, falling back to ``["pwd", "otp"]``."""
    with caplog.at_level(logging.WARNING):
        service = _service(_LegacyMfaTokenService)
        client = _client(service, session)
    assert any(
        "issue_mfa_token() does not accept 'amr'" in record.getMessage()
        for record in caplog.records
    )
    async with client:
        secret = await _enrolled_account(client)
        challenge = (await _callback(client)).json()
        verified = await client.post(
            "/auth/mfa/verify",
            json={"mfa_token": challenge["mfa_token"], "code": _next_code(secret)},
        )
    assert verified.status_code == 200, verified.text
    assert _amr(service, verified.json()["access_token"]) == (AMR_PASSWORD, AMR_OTP)


def test_second_factor_amr_never_repeats_otp() -> None:
    """A first step that somehow already lists ``otp`` gets it once."""
    service = _service()
    user = _SocialMfaUser(email="x@a.com", hashed_password="h")
    user.id = UUID(int=1)
    token = service.issue_mfa_token(user, amr=(AMR_EXTERNAL, AMR_OTP))
    assert service.second_factor_amr(token) == (AMR_EXTERNAL, AMR_OTP)
