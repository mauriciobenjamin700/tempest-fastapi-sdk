"""``POST /auth/oauth/{provider}/token`` — social login for clients with
no browser.

A native mobile app runs the provider's own SDK on the device and ends
up holding an access token. There is no browser to redirect through
``/login`` → consent → ``/callback``, so before this route every mobile
product hand-rolled the same endpoint: call userinfo with the token,
trust what comes back, mint a session.

Trusting what comes back is the bug, and it is what these tests pin.
Userinfo answers *whose* token this is, never *who it was issued for*,
so an attacker who walks the victim through a consent screen for their
own app holds a token that resolves to the victim here — and posting it
would hand over the victim's session without a password. The route
therefore asks the provider which application the token belongs to
**before** it looks anything up, and refuses outright when the
registered client cannot answer.

:meth:`TestTheAudienceIsCheckedFirst.test_a_token_for_another_app_creates_nothing`
and
:meth:`TestAProviderThatCannotAnswerIsRefused.test_the_profile_is_never_fetched`
are the two that would go quiet if the check were dropped.
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from typing import Any

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
    BaseModel,
    BaseUserModel,
    NameMixin,
    OAuthTokenAudienceMismatchException,
    OAuthTokens,
    OAuthUser,
    TokenDelivery,
    UserAuthService,
    make_auth_router,
    make_user_oauth_account_model,
    make_user_token_model,
    register_exception_handlers,
)
from tempest_fastapi_sdk.settings.mixins import AuthSettings, JWTSettings


class _TokenLoginUser(NameMixin, BaseUserModel):
    __tablename__ = "oauth_token_users"


_TokenLoginUserToken = make_user_token_model(
    user_table="oauth_token_users",
    tablename="oauth_token_user_tokens",
    class_name="_TokenLoginUserToken",
)
_TokenLoginAccount = make_user_oauth_account_model(
    user_table="oauth_token_users",
    tablename="oauth_token_accounts",
    class_name="_TokenLoginAccount",
)


def _profile(**overrides: Any) -> OAuthUser:
    """Build the identity the provider would report.

    Args:
        **overrides (Any): Field overrides.

    Returns:
        OAuthUser: The normalized identity.
    """
    fields: dict[str, Any] = {
        "provider": "google",
        "subject": "sub-1",
        "email": "ana@example.com",
        "email_verified": True,
        "name": "Ana Souza",
    }
    fields.update(overrides)
    return OAuthUser(**fields)


class _BlindClient:
    """A client that speaks the redirect flow and nothing else.

    Satisfies ``OAuthClient`` — which is the point: a client written
    before the audience check existed keeps working for the redirect
    routes and must be refused by the token-in-hand one.
    """

    def __init__(self, profile: OAuthUser, *, name: str = "google") -> None:
        """Initialize.

        Args:
            profile (OAuthUser): What ``fetch_user`` returns.
            name (str): Provider key this client answers to.
        """
        self.provider_name: str = name
        self.profile: OAuthUser = profile
        self.calls: list[str] = []

    def build_authorize_url(self, *, state: str, **extra: str) -> str:
        """Render a fake consent URL.

        Args:
            state (str): CSRF state to echo.
            **extra (str): Ignored.

        Returns:
            str: The fake provider's consent URL.
        """
        return f"https://idp.test/{self.provider_name}/auth?state={state}"

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
        self.calls.append("fetch_user")
        return self.profile


class _VerifyingClient(_BlindClient):
    """A client that can answer which application a token belongs to."""

    def __init__(
        self,
        profile: OAuthUser,
        *,
        name: str = "google",
        mismatch: bool = False,
    ) -> None:
        """Initialize.

        Args:
            profile (OAuthUser): What ``fetch_user`` returns.
            name (str): Provider key this client answers to.
            mismatch (bool): Whether the audience check refuses.
        """
        super().__init__(profile, name=name)
        self.mismatch: bool = mismatch
        self.presented: list[str] = []

    async def verify_token_audience(self, tokens: OAuthTokens) -> None:
        """Record the token and answer per ``mismatch``.

        Args:
            tokens (OAuthTokens): The bundle the caller presented.

        Raises:
            OAuthTokenAudienceMismatchException: When configured to
                refuse.
        """
        self.calls.append("verify_token_audience")
        self.presented.append(tokens.access_token)
        if self.mismatch:
            raise OAuthTokenAudienceMismatchException(
                details={"provider": self.provider_name},
            )


@pytest.fixture
async def session() -> AsyncIterator[AsyncSession]:
    """Yield one session over a fresh in-memory database."""
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as conn:
        await conn.run_sync(BaseModel.metadata.create_all)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    async with factory() as active:
        yield active
    await engine.dispose()


def _service(
    *,
    delivery: TokenDelivery = "bearer",
    enabled: bool = True,
) -> UserAuthService:
    """Build a service wired for social login.

    Args:
        delivery (TokenDelivery): ``AUTH_TOKEN_DELIVERY``.
        enabled (bool): Whether ``AUTH_OAUTH_ENABLED`` is on.

    Returns:
        UserAuthService: The configured service.
    """
    return UserAuthService(
        user_model=_TokenLoginUser,
        token_model=_TokenLoginUserToken,  # type: ignore[arg-type]
        auth_settings=AuthSettings(
            _env_file=None,
            AUTH_AUTO_ACTIVATE=True,
            AUTH_OAUTH_ENABLED=enabled,
            AUTH_TOKEN_DELIVERY=delivery,
            AUTH_COOKIE_SECURE=False,
        ),
        jwt_settings=JWTSettings(_env_file=None, JWT_SECRET="x" * 32),
        email=None,
        oauth_account_model=_TokenLoginAccount,
    )


def _app(
    session: AsyncSession,
    client: Any,
    *,
    delivery: TokenDelivery = "bearer",
) -> FastAPI:
    """Mount the auth router with ``client`` registered as ``google``.

    Args:
        session (AsyncSession): The session every request shares.
        client (Any): The OAuth client to register.
        delivery (TokenDelivery): ``AUTH_TOKEN_DELIVERY``.

    Returns:
        FastAPI: The application under test.
    """

    async def _factory() -> AsyncIterator[AsyncSession]:
        yield session

    app = FastAPI()
    register_exception_handlers(app)
    app.include_router(
        make_auth_router(
            _service(delivery=delivery),
            session_factory=_factory,
            oauth_clients={"google": client},
        )
    )
    return app


def _http(app: FastAPI) -> AsyncClient:
    """Bind a client to ``app`` over ASGI.

    Args:
        app (FastAPI): The application under test.

    Returns:
        AsyncClient: The test client.
    """
    return AsyncClient(transport=ASGITransport(app=app), base_url="http://t")


async def _count_users(session: AsyncSession) -> int:
    """Count the rows the login could have created.

    Args:
        session (AsyncSession): The session under test.

    Returns:
        int: How many users exist.
    """
    rows = (await session.execute(select(_TokenLoginUser))).scalars().all()
    return len(rows)


class TestTheRouteMountsWithTheRedirectFlow:
    """One switch turns social login on, token-in-hand included."""

    async def test_it_is_mounted_when_oauth_is_enabled(
        self, session: AsyncSession
    ) -> None:
        app = _app(session, _VerifyingClient(_profile()))

        paths = app.openapi()["paths"]

        assert "/auth/oauth/{provider}/token" in paths

    async def test_it_is_absent_when_oauth_is_off(self, session: AsyncSession) -> None:
        async def _factory() -> AsyncIterator[AsyncSession]:
            yield session

        router = make_auth_router(
            _service(enabled=False),
            session_factory=_factory,
        )

        paths = {getattr(route, "path", "") for route in router.routes}
        assert not [p for p in paths if "oauth" in p]

    async def test_an_unknown_provider_is_an_unknown_route(
        self, session: AsyncSession
    ) -> None:
        app = _app(session, _VerifyingClient(_profile()))

        async with _http(app) as client:
            response = await client.post(
                "/auth/oauth/facebook/token",
                json={"access_token": "x"},
            )

        assert response.status_code == 404
        assert response.json()["code"] == "OAUTH_PROVIDER_NOT_CONFIGURED"


class TestTheSessionIsTheOrdinaryOne:
    """Token-in-hand produces what the callback produces."""

    async def test_a_first_login_creates_the_account_and_returns_a_pair(
        self, session: AsyncSession
    ) -> None:
        provider = _VerifyingClient(_profile())
        app = _app(session, provider)

        async with _http(app) as client:
            response = await client.post(
                "/auth/oauth/google/token",
                json={"access_token": "ya29.device-token"},
            )

        assert response.status_code == 200
        body = response.json()
        assert body["access_token"]
        assert body["refresh_token"]
        assert provider.presented == ["ya29.device-token"]
        row = (
            await session.execute(
                select(_TokenLoginUser).where(
                    _TokenLoginUser.email == "ana@example.com"
                )
            )
        ).scalar_one()
        assert row.name == "Ana Souza"

    async def test_cookie_delivery_sets_the_session_cookies(
        self, session: AsyncSession
    ) -> None:
        app = _app(session, _VerifyingClient(_profile()), delivery="cookie")

        async with _http(app) as client:
            response = await client.post(
                "/auth/oauth/google/token",
                json={"access_token": "ya29.device-token"},
            )

        assert response.status_code == 200
        assert "access_token" in response.cookies
        assert "refresh_token" in response.cookies
        assert response.json()["access_token"] is None


class TestTheAudienceIsCheckedFirst:
    """The check runs before the profile lookup, and before any write."""

    async def test_verification_precedes_the_profile_call(
        self, session: AsyncSession
    ) -> None:
        provider = _VerifyingClient(_profile())
        app = _app(session, provider)

        async with _http(app) as client:
            await client.post(
                "/auth/oauth/google/token",
                json={"access_token": "ya29.device-token"},
            )

        assert provider.calls == ["verify_token_audience", "fetch_user"]

    async def test_a_token_for_another_app_creates_nothing(
        self, session: AsyncSession
    ) -> None:
        provider = _VerifyingClient(_profile(), mismatch=True)
        app = _app(session, provider)

        async with _http(app) as client:
            response = await client.post(
                "/auth/oauth/google/token",
                json={"access_token": "ya29.victim-token"},
            )

        assert response.status_code == 401
        assert response.json()["code"] == "OAUTH_TOKEN_AUDIENCE_MISMATCH"
        assert provider.calls == ["verify_token_audience"]
        assert await _count_users(session) == 0


class TestAProviderThatCannotAnswerIsRefused:
    """Fail closed: no audience check, no login."""

    async def test_the_route_answers_501(self, session: AsyncSession) -> None:
        app = _app(session, _BlindClient(_profile()))

        async with _http(app) as client:
            response = await client.post(
                "/auth/oauth/google/token",
                json={"access_token": "ya29.device-token"},
            )

        assert response.status_code == 501
        assert response.json()["code"] == "OAUTH_AUDIENCE_UNVERIFIABLE"

    async def test_the_profile_is_never_fetched(self, session: AsyncSession) -> None:
        provider = _BlindClient(_profile())
        app = _app(session, provider)

        async with _http(app) as client:
            await client.post(
                "/auth/oauth/google/token",
                json={"access_token": "ya29.device-token"},
            )

        assert provider.calls == []
        assert await _count_users(session) == 0

    async def test_the_redirect_flow_still_works_for_that_client(
        self, session: AsyncSession
    ) -> None:
        app = _app(session, _BlindClient(_profile()))

        async with _http(app) as client:
            response = await client.get("/auth/oauth/google/login")

        assert response.status_code == 302


class TestTheTokenNeverRidesInTheUrl:
    """The credential is a body field, and only a body field."""

    async def test_the_path_carries_no_token_parameter(
        self, session: AsyncSession
    ) -> None:
        app = _app(session, _VerifyingClient(_profile()))

        operation = app.openapi()["paths"]["/auth/oauth/{provider}/token"]["post"]

        names = {p["name"] for p in operation.get("parameters", [])}
        assert names == {"provider"}
        assert operation["requestBody"]["required"] is True

    async def test_a_missing_token_is_a_422(self, session: AsyncSession) -> None:
        app = _app(session, _VerifyingClient(_profile()))

        async with _http(app) as client:
            response = await client.post(
                "/auth/oauth/google/token",
                json={"access_token": ""},
            )

        assert response.status_code == 422
