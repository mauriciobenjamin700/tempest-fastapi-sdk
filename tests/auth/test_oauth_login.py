"""End-to-end tests for the bundled social-login endpoints.

The four routes ``AUTH_OAUTH_ENABLED`` mounts, the service method
behind them, and the three security rules that used to live only in the
prose of ``docs/recipes/oauth.md``:

* the callback compares ``state`` against the cookie the redirect set,
* an existing account is only claimed by email when the provider says
  it verified that address,
* the identity is keyed on ``(provider, subject)``, never on the email.

The load-bearing assertion is
:meth:`TestTheTokenIsTheBundledToken.test_access_token_passes_a_strict_type_check`.
Hand-rolled social login signs ``{"sub": ...}`` and nothing else, which
only survives ``token_type_allowed``'s legacy-compatibility branch — so
a service that has no legacy tokens and correctly sets ``strict=True``
breaks every Google login and no test notices, because none of that
path ran SDK code. Now it does.

``AUTH_COOKIE_SECURE=False`` throughout so httpx's cookie jar returns
the cookies over the test's plain-``http`` base URL, exactly as a real
browser drops a ``Secure`` cookie over HTTP.
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from typing import Any
from urllib.parse import parse_qs, urlparse

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
    OAuthError,
    OAuthTokens,
    OAuthUser,
    TokenDelivery,
    UserAuthService,
    make_auth_router,
    make_user_oauth_account_model,
    make_user_refresh_token_model,
    make_user_token_model,
    register_exception_handlers,
)
from tempest_fastapi_sdk.settings.mixins import AuthSettings, JWTSettings
from tempest_fastapi_sdk.utils.token_types import (
    ACCESS_TOKEN_TYPE,
    token_type_allowed,
)


class _OAuthUser(NameMixin, BaseUserModel):
    __tablename__ = "oauth_test_users"


class _PlainUser(BaseUserModel):
    __tablename__ = "oauth_plain_users"


_OAuthUserToken = make_user_token_model(
    user_table="oauth_test_users",
    tablename="oauth_test_user_tokens",
    class_name="_OAuthUserToken",
)
_OAuthAccount = make_user_oauth_account_model(
    user_table="oauth_test_users",
    tablename="oauth_test_accounts",
    class_name="_OAuthAccount",
)
_OAuthRefresh = make_user_refresh_token_model(
    user_table="oauth_test_users",
    tablename="oauth_test_refresh_tokens",
    class_name="_OAuthRefresh",
)
_PlainUserToken = make_user_token_model(
    user_table="oauth_plain_users",
    tablename="oauth_plain_user_tokens",
    class_name="_PlainUserToken",
)

_PASSWORD = "Str0ng-pass-12!"


class _FakeClient:
    """An :class:`~tempest_fastapi_sdk.OAuthClient` that never leaves the process.

    Records what it was asked to exchange so a test can assert the
    router actually called the provider, and returns a fixed profile.
    """

    def __init__(self, profile: OAuthUser, *, name: str = "google") -> None:
        """Initialize.

        Args:
            profile (OAuthUser): Identity ``fetch_user`` will return.
            name (str): Provider key this client answers to.
        """
        self.provider_name: str = name
        self.profile: OAuthUser = profile
        self.exchanged: list[str] = []

    def build_authorize_url(self, *, state: str, **extra: str) -> str:
        """Render a fake authorize URL carrying ``state``.

        Args:
            state (str): CSRF state to echo.
            **extra (str): Ignored.

        Returns:
            str: The fake provider's consent URL.
        """
        return f"https://idp.test/{self.provider_name}/auth?state={state}"

    async def exchange_code(self, code: str) -> OAuthTokens:
        """Record the code and return a fixed token bundle.

        Args:
            code (str): The authorization code.

        Returns:
            OAuthTokens: A fixed bundle.
        """
        self.exchanged.append(code)
        return OAuthTokens(access_token="provider-access", token_type="Bearer")

    async def fetch_user(self, tokens: OAuthTokens) -> OAuthUser:
        """Return the configured profile.

        Args:
            tokens (OAuthTokens): Ignored.

        Returns:
            OAuthUser: The configured identity.
        """
        return self.profile


def _profile(**overrides: Any) -> OAuthUser:
    """Build an ``OAuthUser`` with sensible defaults.

    Args:
        **overrides (Any): Field overrides.

    Returns:
        OAuthUser: The identity a provider would return.
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


@pytest.fixture
async def session() -> AsyncIterator[AsyncSession]:
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as conn:
        await conn.run_sync(BaseModel.metadata.create_all)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    async with factory() as s:
        yield s
    await engine.dispose()


def _service(
    *,
    delivery: TokenDelivery = "bearer",
    with_account_model: bool = True,
    with_refresh_model: bool = False,
    user_model: type[BaseUserModel] = _OAuthUser,
    token_model: Any = _OAuthUserToken,
    **auth_overrides: Any,
) -> UserAuthService:
    """Build a service wired for social login.

    Args:
        delivery (TokenDelivery): ``AUTH_TOKEN_DELIVERY``.
        with_account_model (bool): Whether to wire the link model.
        with_refresh_model (bool): Whether to wire DB-backed refresh.
        user_model (type[BaseUserModel]): The user table.
        token_model (Any): The one-time-token table.
        **auth_overrides (Any): ``AuthSettings`` field overrides.

    Returns:
        UserAuthService: The configured service.
    """
    auth_overrides.setdefault("AUTH_OAUTH_ENABLED", True)
    auth = AuthSettings(
        _env_file=None,
        AUTH_AUTO_ACTIVATE=True,
        AUTH_TOKEN_DELIVERY=delivery,
        AUTH_COOKIE_SECURE=False,
        **auth_overrides,
    )
    return UserAuthService(
        user_model=user_model,
        token_model=token_model,
        auth_settings=auth,
        jwt_settings=JWTSettings(_env_file=None, JWT_SECRET="x" * 32),
        email=None,
        oauth_account_model=_OAuthAccount if with_account_model else None,
        refresh_token_model=_OAuthRefresh if with_refresh_model else None,
    )


def _client(
    service: UserAuthService,
    session: AsyncSession,
    *,
    clients: dict[str, Any] | None = None,
) -> AsyncClient:
    """Mount the auth router on a throwaway app and return a test client.

    Args:
        service (UserAuthService): The configured service.
        session (AsyncSession): The session every request shares.
        clients (dict[str, Any] | None): OAuth clients to register.
            ``None`` registers one fake Google client.

    Returns:
        AsyncClient: A client bound to the app over ASGI.
    """

    async def _factory() -> AsyncIterator[AsyncSession]:
        yield session

    app = FastAPI()
    app.include_router(
        make_auth_router(
            service,
            session_factory=_factory,
            oauth_clients=clients
            if clients is not None
            else {"google": _FakeClient(_profile())},
        )
    )
    return AsyncClient(transport=ASGITransport(app=app), base_url="http://t")


async def _login(
    client: AsyncClient,
    *,
    provider: str = "google",
    headers: dict[str, str] | None = None,
) -> Any:
    """Drive the redirect half and return the callback's response.

    Args:
        client (AsyncClient): The test client, whose cookie jar keeps
            the state cookie across the two calls exactly as a browser
            would.
        provider (str): Provider key.
        headers (dict[str, str] | None): Extra headers for the callback.

    Returns:
        Any: The callback response.
    """
    redirect = await client.get(f"/auth/oauth/{provider}/login")
    state = parse_qs(urlparse(redirect.headers["location"]).query)["state"][0]
    return await client.get(
        f"/auth/oauth/{provider}/callback",
        params={"code": "the-code", "state": state},
        headers=headers or {},
    )


class TestMountingIsRefusedWithoutItsPrerequisites:
    """Every missing piece fails at construction, not at the first request."""

    async def test_routes_absent_when_the_switch_is_off(
        self, session: AsyncSession
    ) -> None:
        service = _service(AUTH_OAUTH_ENABLED=False)

        async def _factory() -> AsyncIterator[AsyncSession]:
            yield session

        router = make_auth_router(service, session_factory=_factory)
        paths = {getattr(route, "path", "") for route in router.routes}
        assert not [p for p in paths if "oauth" in p]

    async def test_all_four_routes_mount_when_enabled(
        self, session: AsyncSession
    ) -> None:
        async def _factory() -> AsyncIterator[AsyncSession]:
            yield session

        router = make_auth_router(
            _service(),
            session_factory=_factory,
            oauth_clients={"google": _FakeClient(_profile())},
        )
        paths = {getattr(route, "path", "") for route in router.routes}
        assert "/auth/oauth/{provider}/login" in paths
        assert "/auth/oauth/{provider}/callback" in paths
        assert "/auth/oauth/accounts" in paths
        assert "/auth/oauth/accounts/unlink" in paths

    async def test_no_client_is_refused(self, session: AsyncSession) -> None:
        async def _factory() -> AsyncIterator[AsyncSession]:
            yield session

        with pytest.raises(RuntimeError, match="oauth_clients"):
            make_auth_router(_service(), session_factory=_factory)

    async def test_no_account_model_is_refused(self, session: AsyncSession) -> None:
        async def _factory() -> AsyncIterator[AsyncSession]:
            yield session

        with pytest.raises(RuntimeError, match="oauth_account_model"):
            make_auth_router(
                _service(with_account_model=False),
                session_factory=_factory,
                oauth_clients={"google": _FakeClient(_profile())},
            )

    async def test_user_model_without_name_is_refused(
        self, session: AsyncSession
    ) -> None:
        async def _factory() -> AsyncIterator[AsyncSession]:
            yield session

        with pytest.raises(RuntimeError, match="NameMixin"):
            make_auth_router(
                _service(user_model=_PlainUser, token_model=_PlainUserToken),
                session_factory=_factory,
                oauth_clients={"google": _FakeClient(_profile())},
            )


class TestTheRedirectHalf:
    """``GET /auth/oauth/{provider}/login``."""

    async def test_redirects_to_the_provider(self, session: AsyncSession) -> None:
        async with _client(_service(), session) as client:
            response = await client.get("/auth/oauth/google/login")
        assert response.status_code == 302
        assert response.headers["location"].startswith("https://idp.test/google/auth")

    async def test_sets_an_httponly_lax_state_cookie(
        self, session: AsyncSession
    ) -> None:
        async with _client(_service(), session) as client:
            response = await client.get("/auth/oauth/google/login")
        header = response.headers["set-cookie"]
        assert header.startswith("oauth_state=google:")
        assert "HttpOnly" in header
        assert "SameSite=lax" in header
        assert "Path=/auth" in header
        assert "Max-Age=600" in header

    async def test_the_ttl_setting_reaches_the_cookie(
        self, session: AsyncSession
    ) -> None:
        service = _service(AUTH_OAUTH_STATE_TTL_SECONDS=120)
        async with _client(service, session) as client:
            response = await client.get("/auth/oauth/google/login")
        assert "Max-Age=120" in response.headers["set-cookie"]

    async def test_lax_survives_a_strict_cookie_policy(
        self, session: AsyncSession
    ) -> None:
        """``Strict`` would withhold the cookie on the callback navigation.

        The provider returns the user with a cross-site top-level
        navigation. A ``Strict`` cookie is not sent there, so the state
        check the cookie exists for would fail on every single login.
        """
        service = _service(AUTH_COOKIE_SAMESITE="strict")
        async with _client(service, session) as client:
            response = await client.get("/auth/oauth/google/login")
        assert "SameSite=lax" in response.headers["set-cookie"]

    async def test_unknown_provider_is_404(self, session: AsyncSession) -> None:
        async with _client(_service(), session) as client:
            response = await client.get("/auth/oauth/gitlab/login")
        assert response.status_code == 404


class TestTheCallbackCreatesTheAccount:
    """First arrival of an unknown identity."""

    async def test_creates_an_active_user_from_the_profile(
        self, session: AsyncSession
    ) -> None:
        service = _service()
        async with _client(service, session) as client:
            response = await _login(client)
        assert response.status_code == 200
        body = response.json()
        assert body["access_token"]
        assert body["refresh_token"]
        rows = (
            (
                await session.execute(
                    select(_OAuthUser).where(_OAuthUser.email == "ana@example.com")
                )
            )
            .scalars()
            .all()
        )
        assert len(rows) == 1
        assert rows[0].is_active is True
        assert rows[0].name == "Ana Souza"

    async def test_stores_the_link_row(self, session: AsyncSession) -> None:
        async with _client(_service(), session) as client:
            await _login(client)
        links = (await session.execute(select(_OAuthAccount))).scalars().all()
        assert len(links) == 1
        assert links[0].provider == "google"
        assert links[0].subject == "sub-1"
        assert links[0].email_verified is True

    async def test_generated_password_satisfies_the_complexity_policy(
        self, session: AsyncSession
    ) -> None:
        """The account is created even with the strictest policy on.

        A generator drawing from a flat alphabet fails this roughly a
        quarter of the time; the failure would surface as a 422 from
        inside the callback about a password nobody typed.
        """
        service = _service(
            AUTH_PASSWORD_REQUIRE_COMPLEXITY=True,
            AUTH_PASSWORD_MIN_LENGTH=20,
        )
        for index in range(15):
            clients = {
                "google": _FakeClient(
                    _profile(subject=f"sub-{index}", email=f"p{index}@example.com")
                )
            }
            async with _client(service, session, clients=clients) as client:
                response = await _login(client)
            assert response.status_code == 200, response.text
        created = (await session.execute(select(_OAuthUser))).scalars().all()
        assert len(created) == 15

    async def test_a_second_login_reuses_the_same_row(
        self, session: AsyncSession
    ) -> None:
        service = _service()
        async with _client(service, session) as client:
            first = await _login(client)
            second = await _login(client)
        assert first.json()["user_id"] == second.json()["user_id"]
        assert len((await session.execute(select(_OAuthUser))).scalars().all()) == 1
        assert len((await session.execute(select(_OAuthAccount))).scalars().all()) == 1


class TestTheTokenIsTheBundledToken:
    """The whole point of routing social login through the service."""

    async def test_access_token_passes_a_strict_type_check(
        self, session: AsyncSession
    ) -> None:
        """The claim set matches ``POST /auth/login``, ``typ`` included.

        A hand-rolled ``self.tokens.encode({"sub": str(user.id)})``
        carries no ``typ`` and is accepted only by
        ``token_type_allowed``'s legacy-compatibility branch — so a
        service that has no legacy tokens and sets ``strict=True``
        rejects every social login.
        """
        service = _service()
        async with _client(service, session) as client:
            response = await _login(client)
        payload = service.jwt.decode(response.json()["access_token"])
        assert payload["typ"] == ACCESS_TOKEN_TYPE
        assert payload["email"] == "ana@example.com"
        assert token_type_allowed(payload, [ACCESS_TOKEN_TYPE], strict=True)

    async def test_refresh_token_rotates_through_the_normal_endpoint(
        self, session: AsyncSession
    ) -> None:
        """The social session joins the rotation family, not a second scheme."""
        service = _service(with_refresh_model=True)
        async with _client(service, session) as client:
            issued = (await _login(client)).json()
            rotated = await client.post(
                "/auth/refresh",
                json={"refresh_token": issued["refresh_token"]},
            )
        assert rotated.status_code == 200
        assert rotated.json()["refresh_token"] != issued["refresh_token"]

    async def test_logout_revokes_the_social_session(
        self, session: AsyncSession
    ) -> None:
        service = _service(with_refresh_model=True)
        async with _client(service, session) as client:
            issued = (await _login(client)).json()
            await client.post(
                "/auth/logout",
                json={"refresh_token": issued["refresh_token"]},
            )
            replay = await client.post(
                "/auth/refresh",
                json={"refresh_token": issued["refresh_token"]},
            )
        assert replay.status_code == 401


class TestTheStateCheck:
    """The rule that used to live only in a ``!!! danger`` admonition."""

    async def test_missing_cookie_is_rejected(self, session: AsyncSession) -> None:
        async with _client(_service(), session) as client:
            redirect = await client.get("/auth/oauth/google/login")
            state = parse_qs(urlparse(redirect.headers["location"]).query)["state"][0]
            client.cookies.clear()
            response = await client.get(
                "/auth/oauth/google/callback",
                params={"code": "c", "state": state},
            )
        assert response.status_code == 401

    async def test_wrong_state_is_rejected(self, session: AsyncSession) -> None:
        async with _client(_service(), session) as client:
            await client.get("/auth/oauth/google/login")
            response = await client.get(
                "/auth/oauth/google/callback",
                params={"code": "c", "state": "forged"},
            )
        assert response.status_code == 401

    async def test_a_state_minted_for_another_provider_is_rejected(
        self, session: AsyncSession
    ) -> None:
        """The cookie carries the provider, so states are not fungible."""
        clients = {
            "google": _FakeClient(_profile()),
            "github": _FakeClient(_profile(provider="github"), name="github"),
        }
        async with _client(_service(), session, clients=clients) as client:
            redirect = await client.get("/auth/oauth/github/login")
            state = parse_qs(urlparse(redirect.headers["location"]).query)["state"][0]
            response = await client.get(
                "/auth/oauth/google/callback",
                params={"code": "c", "state": state},
            )
        assert response.status_code == 401

    async def test_provider_reported_error_is_rejected(
        self, session: AsyncSession
    ) -> None:
        async with _client(_service(), session) as client:
            redirect = await client.get("/auth/oauth/google/login")
            state = parse_qs(urlparse(redirect.headers["location"]).query)["state"][0]
            response = await client.get(
                "/auth/oauth/google/callback",
                params={"error": "access_denied", "state": state},
            )
        assert response.status_code == 401


class TestTheEmailRules:
    """Email is required, and never silently claims an existing account."""

    async def test_a_provider_without_an_email_is_refused(
        self, session: AsyncSession
    ) -> None:
        clients = {"google": _FakeClient(_profile(email=None))}
        async with _client(_service(), session, clients=clients) as client:
            response = await _login(client)
        assert response.status_code == 422
        assert (await session.execute(select(_OAuthUser))).scalars().first() is None

    async def test_existing_email_is_a_conflict_by_default(
        self, session: AsyncSession
    ) -> None:
        service = _service()
        await service.signup(session, email="ana@example.com", password=_PASSWORD)
        await session.commit()
        async with _client(service, session) as client:
            response = await _login(client)
        assert response.status_code == 409
        assert (await session.execute(select(_OAuthAccount))).scalars().first() is None

    async def test_verified_email_links_when_the_setting_allows_it(
        self, session: AsyncSession
    ) -> None:
        service = _service(AUTH_OAUTH_LINK_BY_VERIFIED_EMAIL=True)
        user, _ = await service.signup(
            session, email="ana@example.com", password=_PASSWORD
        )
        await session.commit()
        async with _client(service, session) as client:
            response = await _login(client)
        assert response.status_code == 200
        assert response.json()["user_id"] == str(user.id)
        assert len((await session.execute(select(_OAuthUser))).scalars().all()) == 1

    async def test_unverified_email_never_links(self, session: AsyncSession) -> None:
        """``email_verified=None`` is not a yes.

        The provider said nothing about the address. Treating silence
        as verification is what hands an attacker any account whose
        email they can guess.
        """
        service = _service(AUTH_OAUTH_LINK_BY_VERIFIED_EMAIL=True)
        await service.signup(session, email="ana@example.com", password=_PASSWORD)
        await session.commit()
        clients = {"google": _FakeClient(_profile(email_verified=None))}
        async with _client(service, session, clients=clients) as client:
            response = await _login(client)
        assert response.status_code == 409


class TestTheIdentityIsTheKeyNotTheEmail:
    """A changed email at the provider does not fork the account."""

    async def test_same_subject_new_email_keeps_one_user(
        self, session: AsyncSession
    ) -> None:
        service = _service()
        async with _client(service, session) as client:
            first = await _login(client)
        moved = {"google": _FakeClient(_profile(email="ana@newmail.example"))}
        async with _client(service, session, clients=moved) as client:
            second = await _login(client)
        assert first.json()["user_id"] == second.json()["user_id"]
        assert len((await session.execute(select(_OAuthUser))).scalars().all()) == 1


class TestAccountCreationGate:
    """``AUTH_OAUTH_ALLOW_ACCOUNT_CREATION`` and its fallback."""

    async def test_closed_signup_closes_the_social_door(
        self, session: AsyncSession
    ) -> None:
        service = _service(AUTH_SIGNUP_ENABLED=False)
        async with _client(service, session) as client:
            response = await _login(client)
        assert response.status_code == 403

    async def test_the_knob_reopens_it_explicitly(self, session: AsyncSession) -> None:
        service = _service(
            AUTH_SIGNUP_ENABLED=False,
            AUTH_OAUTH_ALLOW_ACCOUNT_CREATION=True,
        )
        async with _client(service, session) as client:
            response = await _login(client)
        assert response.status_code == 200

    async def test_the_knob_can_close_it_while_signup_stays_open(
        self, session: AsyncSession
    ) -> None:
        service = _service(
            AUTH_SIGNUP_ENABLED=True,
            AUTH_OAUTH_ALLOW_ACCOUNT_CREATION=False,
        )
        async with _client(service, session) as client:
            response = await _login(client)
        assert response.status_code == 403


class TestTheLocalizedPlaceholderName:
    """A provider that reports no name still yields a readable row."""

    async def test_defaults_to_portuguese(self, session: AsyncSession) -> None:
        clients = {"google": _FakeClient(_profile(name=None))}
        async with _client(_service(), session, clients=clients) as client:
            await _login(client)
        user = (await session.execute(select(_OAuthUser))).scalars().one()
        assert user.name == "Você"

    async def test_accept_language_picks_english(self, session: AsyncSession) -> None:
        clients = {"google": _FakeClient(_profile(name=None))}
        async with _client(_service(), session, clients=clients) as client:
            await _login(client, headers={"accept-language": "en-US"})
        user = (await session.execute(select(_OAuthUser))).scalars().one()
        assert user.name == "You"


class TestCookieDelivery:
    """The callback honours ``AUTH_TOKEN_DELIVERY``."""

    async def test_cookie_mode_omits_the_tokens_from_the_body(
        self, session: AsyncSession
    ) -> None:
        service = _service(delivery="cookie")
        async with _client(service, session) as client:
            response = await _login(client)
            assert response.json()["access_token"] is None
            assert client.cookies.get("access_token")

    async def test_both_mode_fills_body_and_cookies_at_once(
        self, session: AsyncSession
    ) -> None:
        """One registered redirect URI, so ``both`` cannot mean two routes."""
        service = _service(delivery="both")
        async with _client(service, session) as client:
            response = await _login(client)
            assert response.json()["access_token"]
            assert client.cookies.get("access_token")


class TestLinkedAccountManagement:
    """``GET /auth/oauth/accounts`` and its unlink counterpart."""

    async def test_password_only_account_lists_nothing(
        self, session: AsyncSession
    ) -> None:
        service = _service()
        await service.signup(session, email="bob@example.com", password=_PASSWORD)
        await session.commit()
        async with _client(service, session) as client:
            token = (
                await client.post(
                    "/auth/login",
                    json={"email": "bob@example.com", "password": _PASSWORD},
                )
            ).json()["access_token"]
            response = await client.get(
                "/auth/oauth/accounts",
                headers={"Authorization": f"Bearer {token}"},
            )
        assert response.status_code == 200
        assert response.json() == []

    async def test_lists_then_unlinks(self, session: AsyncSession) -> None:
        service = _service()
        async with _client(service, session) as client:
            token = (await _login(client)).json()["access_token"]
            auth = {"Authorization": f"Bearer {token}"}
            listed = await client.get("/auth/oauth/accounts", headers=auth)
            assert [row["provider"] for row in listed.json()] == ["google"]
            removed = await client.post(
                "/auth/oauth/accounts/unlink",
                json={"provider": "google"},
                headers=auth,
            )
            assert removed.status_code == 204
            again = await client.post(
                "/auth/oauth/accounts/unlink",
                json={"provider": "google"},
                headers=auth,
            )
        assert again.status_code == 404
        assert (await session.execute(select(_OAuthAccount))).scalars().first() is None


class TestProviderFailureSurfaces:
    """`OAuthError` reaches the caller as the 502 the recipe documents."""

    async def test_a_failing_exchange_answers_502(self, session: AsyncSession) -> None:
        """The problem is at the provider, so it is a gateway error.

        Reproduced rather than deduced: the status lives on the exception
        class, and whether it survives the trip out of a route body depends
        on ``register_exception_handlers`` being mounted, which is exactly
        what the recipe tells the reader to do.
        """

        class _Failing(_FakeClient):
            async def exchange_code(self, code: str) -> OAuthTokens:
                raise OAuthError(
                    message="token exchange failed (400)",
                    details={"body": "invalid_grant"},
                )

        async def _factory() -> AsyncIterator[AsyncSession]:
            yield session

        app = FastAPI()
        register_exception_handlers(app)
        app.include_router(
            make_auth_router(
                _service(),
                session_factory=_factory,
                oauth_clients={"google": _Failing(_profile())},
            )
        )
        async with AsyncClient(
            transport=ASGITransport(app=app), base_url="http://t"
        ) as client:
            response = await _login(client)

        assert response.status_code == 502
        assert response.json()["code"] == "OAUTH_ERROR"
