"""Server-side login, signup and activation pages (#602).

Every flow is driven the way a browser without JavaScript drives it:
``GET`` the page, read the hidden ``csrf_token``, ``POST`` the form as
``application/x-www-form-urlencoded`` and follow nothing automatically.
"""

from __future__ import annotations

import re
import time
from collections.abc import AsyncIterator
from dataclasses import dataclass
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pyotp
import pytest
from fastapi import Depends, FastAPI
from httpx import ASGITransport, AsyncClient, Response
from pydantic import ValidationError
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from tempest_fastapi_sdk import (
    AttemptThrottle,
    BaseModel,
    BaseUserModel,
    InMemoryThrottleBackend,
    MemorySessionStore,
    MFAMixin,
    SessionAuth,
    UserAuthService,
    make_auth_pages_router,
    make_session_user_dependency,
    make_user_recovery_code_model,
    make_user_token_model,
    redirect_to_login,
    register_exception_handlers,
)
from tempest_fastapi_sdk.auth.form_csrf import FORM_CSRF_COOKIE, is_local_path
from tempest_fastapi_sdk.auth.pages import AUTH_PAGES_MFA_COOKIE
from tempest_fastapi_sdk.settings.mixins import (
    AuthSettings,
    JWTSettings,
    SessionSettings,
)

PASSWORD = "strong-pass-12-chars"
"""Password of every account the tests create."""


class PagesUser(MFAMixin, BaseUserModel):
    __tablename__ = "auth_pages_users"


PagesToken = make_user_token_model(
    user_table="auth_pages_users",
    tablename="auth_pages_tokens",
    class_name="PagesToken",
)

PagesRecoveryCode = make_user_recovery_code_model(
    user_table="auth_pages_users",
    tablename="auth_pages_recovery_codes",
    class_name="PagesRecoveryCode",
)


@dataclass
class Harness:
    """An app with the pages, plus what the tests reach into.

    Attributes:
        app (FastAPI): The application.
        service (UserAuthService): The auth service.
        session_auth (SessionAuth): The cookie session service.
        factory (async_sessionmaker[AsyncSession]): Direct DB access.
    """

    app: FastAPI
    service: UserAuthService
    session_auth: SessionAuth
    factory: async_sessionmaker[AsyncSession]


async def _build(
    tmp_path: Path,
    *,
    secure_cookies: bool = False,
    login_ip_throttle: AttemptThrottle | None = None,
    **auth: Any,
) -> Harness:
    """Build an app with the pages and two protected routes.

    Args:
        tmp_path (Path): Where the SQLite file lives.
        secure_cookies (bool): ``SESSION_COOKIE_SECURE``.
        login_ip_throttle (AttemptThrottle | None): Forwarded to the router.
        **auth (Any): ``AuthSettings`` overrides.

    Returns:
        Harness: The app and its parts.
    """
    engine = create_async_engine(f"sqlite+aiosqlite:///{tmp_path / 'pages.db'}")
    async with engine.begin() as conn:
        await conn.run_sync(BaseModel.metadata.create_all)
    factory = async_sessionmaker(engine, expire_on_commit=False)

    async def session_factory() -> AsyncIterator[AsyncSession]:
        async with factory() as session:
            yield session

    settings = AuthSettings(
        AUTH_MFA_ENABLED=True,
        AUTH_ACTIVATION_URL_TEMPLATE="http://test/activate/{token}",
        **auth,
    )
    service = UserAuthService(
        user_model=PagesUser,
        token_model=PagesToken,  # type: ignore[arg-type]
        auth_settings=settings,
        jwt_settings=JWTSettings(JWT_SECRET="x" * 32),
        email=None,
    )
    session_auth = SessionAuth(
        user_model=PagesUser,
        store=MemorySessionStore(),
        settings=SessionSettings(SESSION_COOKIE_SECURE=secure_cookies),
    )
    app = FastAPI()
    register_exception_handlers(app)
    app.include_router(
        make_auth_pages_router(
            service,
            session_factory,
            session_auth=session_auth,
            recovery_code_model=PagesRecoveryCode,
            login_ip_throttle=login_ip_throttle,
        )
    )
    current_user = Depends(
        make_session_user_dependency(
            PagesUser,
            session_factory,
            session_auth=session_auth,
            on_missing=redirect_to_login("/login"),
        )
    )
    mfa_user = Depends(
        make_session_user_dependency(
            PagesUser,
            session_factory,
            session_auth=session_auth,
            require_mfa=True,
        )
    )

    @app.get("/me")
    async def me(user: PagesUser = current_user) -> dict[str, str]:
        return {"email": user.email}

    @app.get("/vault")
    async def vault(user: PagesUser = mfa_user) -> dict[str, str]:
        return {"email": user.email}

    return Harness(app, service, session_auth, factory)


@pytest.fixture
async def harness(tmp_path: Path) -> Harness:
    return await _build(tmp_path)


def _client(app: FastAPI, scheme: str = "http") -> AsyncClient:
    return AsyncClient(transport=ASGITransport(app=app), base_url=f"{scheme}://test")


def _token(html: str) -> str:
    match = re.search(r'name="csrf_token" value="([^"]+)"', html)
    assert match, html
    return match.group(1)


async def _form(client: AsyncClient, path: str, **fields: str) -> Response:
    """Load ``path``, then post ``fields`` with its CSRF token.

    Args:
        client (AsyncClient): The browser.
        path (str): The page whose form is submitted.
        **fields (str): Form fields.

    Returns:
        Response: The POST response, not followed.
    """
    page = await client.get(path)
    return await client.post(path, data={"csrf_token": _token(page.text), **fields})


async def _account(
    harness: Harness,
    email: str = "ana@example.com",
    *,
    active: bool = True,
) -> Any:
    """Create an account directly.

    Args:
        harness (Harness): The app.
        email (str): Its e-mail.
        active (bool): Whether it is active.

    Returns:
        Any: The user row.
    """
    async with harness.factory() as session:
        user = PagesUser(email=email, hashed_password="", is_active=active)
        user.set_password(PASSWORD)
        session.add(user)
        await session.commit()
        return user


async def _enroll(harness: Harness, email: str = "ana@example.com") -> str:
    """Create an active account with TOTP confirmed.

    Args:
        harness (Harness): The app.
        email (str): Its e-mail.

    Returns:
        str: The TOTP secret; the current step is already spent.
    """
    user = await _account(harness, email)
    async with harness.factory() as session:
        user = await session.get(PagesUser, user.id)
        secret, _, _ = await harness.service.mfa_enroll(
            session, user=user, recovery_code_model=PagesRecoveryCode
        )
        await harness.service.mfa_confirm(
            session, user=user, code=pyotp.TOTP(secret).now()
        )
        await session.commit()
    return secret


SESSION_COOKIE = SessionSettings().SESSION_COOKIE_NAME
"""Name of the session cookie under the default settings."""


def _session_cookie(response: Response) -> str | None:
    for value in response.headers.get_list("set-cookie"):
        if value.startswith(f"{SESSION_COOKIE}="):
            return value.split(";")[0].split("=", 1)[1] or None
    return None


class TestLogin:
    async def test_the_form_carries_a_token(self, harness: Harness) -> None:
        async with _client(harness.app) as client:
            page = await client.get("/login")
        assert page.status_code == 200
        assert 'name="csrf_token"' in page.text
        assert FORM_CSRF_COOKIE in page.headers["set-cookie"]

    async def test_a_post_without_the_token_is_refused(self, harness: Harness) -> None:
        """Login CSRF: a cross-site form has no token to send."""
        await _account(harness)
        async with _client(harness.app) as client:
            response = await client.post(
                "/login", data={"email": "ana@example.com", "password": PASSWORD}
            )
        assert response.status_code == 403
        assert _session_cookie(response) is None

    async def test_signs_in_and_reaches_the_protected_route(
        self, harness: Harness
    ) -> None:
        await _account(harness)
        async with _client(harness.app) as client:
            response = await _form(
                client, "/login", email="ana@example.com", password=PASSWORD
            )
            me = await client.get("/me")
        assert response.status_code == 303
        assert response.headers["location"] == "/"
        assert _session_cookie(response)
        assert me.json() == {"email": "ana@example.com"}

    @pytest.mark.parametrize(
        "email, password",
        [
            ("ana@example.com", "wrong-password-123"),
            ("nobody@example.com", PASSWORD),
        ],
    )
    async def test_refusal_is_generic(
        self, harness: Harness, email: str, password: str
    ) -> None:
        await _account(harness)
        async with _client(harness.app) as client:
            response = await _form(client, "/login", email=email, password=password)
        assert response.status_code == 401
        assert "E-mail ou senha inválidos." in response.text
        assert f'value="{email}"' in response.text
        assert password not in response.text

    async def test_an_inactive_account_reads_like_a_wrong_password(
        self, harness: Harness
    ) -> None:
        await _account(harness, active=False)
        async with _client(harness.app) as client:
            response = await _form(
                client, "/login", email="ana@example.com", password=PASSWORD
            )
        assert response.status_code == 401
        assert "E-mail ou senha inválidos." in response.text

    async def test_the_budget_is_the_json_routes(self, harness: Harness) -> None:
        await _account(harness)
        async with _client(harness.app) as client:
            for _ in range(5):
                await _form(
                    client, "/login", email="ana@example.com", password="nope-nope-123"
                )
            blocked = await _form(
                client, "/login", email="ana@example.com", password=PASSWORD
            )
        assert blocked.status_code == 429
        assert int(blocked.headers["Retry-After"]) > 0
        assert _session_cookie(blocked) is None

    async def test_english_page_follows_accept_language(self, harness: Harness) -> None:
        async with _client(harness.app) as client:
            page = await client.get("/login", headers={"accept-language": "en-US"})
        assert "Sign in" in page.text


class TestNext:
    async def test_a_protected_route_sends_to_login_with_next(
        self, harness: Harness
    ) -> None:
        async with _client(harness.app) as client:
            response = await client.get("/me?tab=1")
        assert response.status_code == 303
        assert response.headers["location"] == "/login?next=/me%3Ftab%3D1"

    async def test_login_goes_back_to_next(self, harness: Harness) -> None:
        await _account(harness)
        async with _client(harness.app) as client:
            response = await _form(
                client,
                "/login",
                email="ana@example.com",
                password=PASSWORD,
                next="/me?tab=1",
            )
        assert response.headers["location"] == "/me?tab=1"

    @pytest.mark.parametrize(
        "target",
        ["https://evil.example", "//evil.example", "/\\evil.example", "evil"],
    )
    async def test_an_offsite_next_is_ignored(
        self, harness: Harness, target: str
    ) -> None:
        await _account(harness)
        async with _client(harness.app) as client:
            response = await _form(
                client,
                "/login",
                email="ana@example.com",
                password=PASSWORD,
                next=target,
            )
        assert response.headers["location"] == "/"


class TestSession:
    async def test_logout_revokes_on_the_server(self, harness: Harness) -> None:
        """A copied cookie stops working once the user signs out."""
        await _account(harness)
        async with _client(harness.app) as client:
            login = await _form(
                client, "/login", email="ana@example.com", password=PASSWORD
            )
            cookie = _session_cookie(login)
            token = client.cookies[FORM_CSRF_COOKIE]
            logout = await client.post("/logout", data={"csrf_token": token})
        assert logout.status_code == 303
        assert logout.headers["location"] == "/login"
        assert cookie is not None
        assert await harness.session_auth.resolve(cookie) is None

    async def test_logout_without_the_token_is_refused(self, harness: Harness) -> None:
        await _account(harness)
        async with _client(harness.app) as client:
            await _form(client, "/login", email="ana@example.com", password=PASSWORD)
            response = await client.post("/logout")
            me = await client.get("/me")
        assert response.status_code == 403
        assert me.status_code == 200

    async def test_a_new_login_rotates_the_previous_session(
        self, harness: Harness
    ) -> None:
        await _account(harness)
        async with _client(harness.app) as client:
            first = _session_cookie(
                await _form(
                    client, "/login", email="ana@example.com", password=PASSWORD
                )
            )
            await _form(client, "/login", email="ana@example.com", password=PASSWORD)
        assert first is not None
        assert await harness.session_auth.resolve(first) is None

    async def test_a_deactivated_account_loses_access_at_once(
        self, harness: Harness
    ) -> None:
        user = await _account(harness)
        async with _client(harness.app) as client:
            await _form(client, "/login", email="ana@example.com", password=PASSWORD)
            async with harness.factory() as session:
                row = await session.get(PagesUser, user.id)
                assert row is not None
                row.is_active = False
                await session.commit()
            response = await client.get("/me")
        assert response.status_code == 303
        assert response.headers["location"].startswith("/login")

    async def test_the_session_records_the_password_method(
        self, harness: Harness
    ) -> None:
        await _account(harness)
        async with _client(harness.app) as client:
            login = await _form(
                client, "/login", email="ana@example.com", password=PASSWORD
            )
        cookie = _session_cookie(login)
        assert cookie is not None
        session = await harness.session_auth.resolve(cookie)
        assert session is not None
        assert session.amr == ["pwd"]

    async def test_require_mfa_refuses_a_password_only_session(
        self, harness: Harness
    ) -> None:
        await _account(harness)
        async with _client(harness.app) as client:
            await _form(client, "/login", email="ana@example.com", password=PASSWORD)
            response = await client.get("/vault")
        assert response.status_code == 403


class TestMfa:
    async def test_password_step_holds_no_session(self, harness: Harness) -> None:
        await _enroll(harness)
        async with _client(harness.app) as client:
            response = await _form(
                client, "/login", email="ana@example.com", password=PASSWORD
            )
        assert response.status_code == 303
        assert response.headers["location"] == "/login/mfa"
        assert _session_cookie(response) is None
        assert AUTH_PAGES_MFA_COOKIE in response.headers["set-cookie"]
        assert "mfa_token" not in response.headers["location"]

    async def test_the_right_code_opens_an_mfa_session(self, harness: Harness) -> None:
        secret = await _enroll(harness)
        code = pyotp.TOTP(secret).at(int(time.time()) + 30)
        async with _client(harness.app) as client:
            await _form(client, "/login", email="ana@example.com", password=PASSWORD)
            response = await _form(client, "/login/mfa", code=code)
            vault = await client.get("/vault")
        assert response.status_code == 303
        cookie = _session_cookie(response)
        assert cookie is not None
        session = await harness.session_auth.resolve(cookie)
        assert session is not None
        assert session.amr == ["pwd", "otp"]
        assert vault.status_code == 200

    async def test_a_wrong_code_stays_on_the_page(self, harness: Harness) -> None:
        await _enroll(harness)
        async with _client(harness.app) as client:
            await _form(client, "/login", email="ana@example.com", password=PASSWORD)
            response = await _form(client, "/login/mfa", code="000000")
        assert response.status_code == 401
        assert "Código inválido." in response.text
        assert _session_cookie(response) is None

    async def test_a_code_is_single_use(self, harness: Harness) -> None:
        secret = await _enroll(harness)
        code = pyotp.TOTP(secret).at(int(time.time()) + 30)
        async with _client(harness.app) as first:
            await _form(first, "/login", email="ana@example.com", password=PASSWORD)
            assert (await _form(first, "/login/mfa", code=code)).status_code == 303
        async with _client(harness.app) as second:
            await _form(second, "/login", email="ana@example.com", password=PASSWORD)
            replay = await _form(second, "/login/mfa", code=code)
        assert replay.status_code == 401

    async def test_without_the_held_step_goes_back_to_login(
        self, harness: Harness
    ) -> None:
        async with _client(harness.app) as client:
            response = await client.get("/login/mfa")
        assert response.status_code == 303
        assert response.headers["location"] == "/login"

    async def test_a_tampered_step_goes_back_to_login(self, harness: Harness) -> None:
        await _enroll(harness)
        async with _client(harness.app) as client:
            await _form(client, "/login", email="ana@example.com", password=PASSWORD)
            client.cookies.set(AUTH_PAGES_MFA_COOKIE, "forged", path="/")
            response = await _form(client, "/login/mfa", code="123456")
        assert response.status_code == 401
        assert "Entre de novo." in response.text


class TestSignup:
    async def test_signup_activation_and_session(self, harness: Harness) -> None:
        """The issue's acceptance path: form, e-mail link, logged in."""
        async with _client(harness.app) as client:
            done = await _form(
                client, "/signup", email="new@example.com", password=PASSWORD
            )
            assert done.status_code == 200
            assert "Confira seu e-mail" in done.text
            link = re.search(r'href="(http://test/activate/[^"]+)"', done.text)
            assert link
            activated = await client.get(link.group(1).removeprefix("http://test"))
            me = await client.get("/me")
        assert activated.status_code == 303
        cookie = _session_cookie(activated)
        assert cookie is not None
        session = await harness.session_auth.resolve(cookie)
        assert session is not None
        assert session.amr == ["email"]
        assert me.json() == {"email": "new@example.com"}

    async def test_invalid_fields_come_back_without_the_password(
        self, harness: Harness
    ) -> None:
        async with _client(harness.app) as client:
            response = await _form(
                client, "/signup", email="new@example.com", password="short"
            )
        assert response.status_code == 422
        assert 'value="new@example.com"' in response.text
        assert 'aria-invalid="true"' in response.text
        assert "short" not in response.text.split("<form", 1)[1].split("</form>")[0]

    @pytest.mark.parametrize(
        "language, label",
        [("pt-BR", ">Senha<"), ("en-US", ">Password<")],
    )
    async def test_base_field_labels_follow_the_page_language(
        self, harness: Harness, language: str, label: str
    ) -> None:
        async with _client(harness.app) as client:
            page = await client.get("/signup", headers={"accept-language": language})
        assert label in page.text

    async def test_a_duplicate_email_is_refused_on_its_field(
        self, harness: Harness
    ) -> None:
        await _account(harness)
        async with _client(harness.app) as client:
            response = await _form(
                client, "/signup", email="ana@example.com", password=PASSWORD
            )
        assert response.status_code == 409
        assert 'id="email-error">Já existe uma conta com esse e-mail.' in response.text

    async def test_the_password_policy_lands_on_the_password_field(
        self, harness: Harness
    ) -> None:
        """The service's policy error, localized, marking the field."""
        async with _client(harness.app) as client:
            response = await _form(
                client, "/signup", email="new@example.com", password="curta-curta"
            )
        assert response.status_code == 422
        assert (
            'id="password-error">A senha precisa ter pelo menos 12 caracteres'
            in response.text
        )
        assert 'aria-describedby="password-error"' in response.text

    async def test_an_explicit_language_survives_the_submit(
        self, harness: Harness
    ) -> None:
        async with _client(harness.app) as client:
            page = await client.get("/signup?lang=en-US")
            assert 'action="/signup?lang=en-US"' in page.text
            response = await client.post(
                "/signup?lang=en-US",
                data={"csrf_token": _token(page.text), "email": "x@example.com"},
            )
        assert "Create account" in response.text

    async def test_without_the_token_nothing_is_created(self, harness: Harness) -> None:
        async with _client(harness.app) as client:
            response = await client.post(
                "/signup", data={"email": "new@example.com", "password": PASSWORD}
            )
        assert response.status_code == 403
        async with harness.factory() as session:
            rows = await session.execute(PagesUser.__table__.select())
            assert rows.first() is None

    async def test_auto_activate_opens_the_session(self, tmp_path: Path) -> None:
        harness = await _build(tmp_path, AUTH_AUTO_ACTIVATE=True)
        async with _client(harness.app) as client:
            response = await _form(
                client, "/signup", email="new@example.com", password=PASSWORD
            )
            me = await client.get("/me")
        assert response.status_code == 303
        assert me.json() == {"email": "new@example.com"}

    async def test_disabled_signup_has_no_page(self, tmp_path: Path) -> None:
        harness = await _build(tmp_path, AUTH_SIGNUP_ENABLED=False)
        async with _client(harness.app) as client:
            response = await client.get("/signup")
        assert response.status_code == 404

    async def test_an_invalid_activation_link_shows_the_error_page(
        self, harness: Harness
    ) -> None:
        async with _client(harness.app) as client:
            response = await client.get("/activate/not-a-token")
        assert response.status_code == 400
        assert _session_cookie(response) is None

    async def test_activating_an_mfa_account_asks_for_the_code(
        self, harness: Harness
    ) -> None:
        secret = await _enroll(harness, "mfa@example.com")
        async with harness.factory() as session:
            user = (
                await session.execute(
                    PagesUser.__table__.select().where(
                        PagesUser.email == "mfa@example.com"
                    )
                )
            ).first()
            assert user is not None
            row = await session.get(PagesUser, user.id)
            assert row is not None
            row.is_active = False
            await session.commit()
            activation = await harness.service.request_activation(
                session, email="mfa@example.com"
            )
            await session.commit()
        assert activation is not None
        assert secret
        async with _client(harness.app) as client:
            response = await client.get(f"/activate/{activation.token}")
        assert response.status_code == 303
        assert response.headers["location"] == "/login/mfa"
        assert _session_cookie(response) is None


class TestActivationRequest:
    @pytest.mark.parametrize("email", ["pending@example.com", "nobody@example.com"])
    async def test_the_answer_does_not_tell_accounts_apart(
        self, harness: Harness, email: str
    ) -> None:
        await _account(harness, "pending@example.com", active=False)
        async with _client(harness.app) as client:
            response = await _form(client, "/activation/request", email=email)
        assert response.status_code == 200
        assert "Se existir uma conta pendente" in response.text

    async def test_the_budget_is_per_client(self, harness: Harness) -> None:
        async with _client(harness.app) as client:
            for _ in range(5):
                await _form(client, "/activation/request", email="x@example.com")
            blocked = await _form(client, "/activation/request", email="x@example.com")
        assert blocked.status_code == 429


class TestSafety:
    @pytest.mark.parametrize(
        "value, safe",
        [
            ("/", True),
            ("/me?tab=1", True),
            ("//evil.example", False),
            ("/\\evil.example", False),
            ("https://evil.example", False),
            ("evil", False),
            ("/a\\b", False),
            ("/a\nb", False),
        ],
    )
    def test_is_local_path(self, value: str, safe: bool) -> None:
        assert is_local_path(value) is safe

    @pytest.mark.parametrize(
        "field", ["AUTH_PAGES_LOGIN_REDIRECT", "AUTH_PAGES_LOGOUT_REDIRECT"]
    )
    def test_an_offsite_redirect_setting_fails_at_boot(self, field: str) -> None:
        with pytest.raises(ValidationError, match="path on this site"):
            AuthSettings(**{field: "https://evil.example"})

    @pytest.mark.parametrize("prefix", ["auth", "/auth/"])
    def test_a_malformed_prefix_fails_at_boot(self, prefix: str) -> None:
        with pytest.raises(ValidationError, match="AUTH_PAGES_PREFIX"):
            AuthSettings(AUTH_PAGES_PREFIX=prefix)

    async def test_mfa_without_a_recovery_model_fails_at_boot(
        self, tmp_path: Path
    ) -> None:
        harness = await _build(tmp_path)

        async def session_factory() -> AsyncIterator[AsyncSession]:
            async with harness.factory() as session:
                yield session

        with pytest.raises(RuntimeError, match="recovery_code_model"):
            make_auth_pages_router(
                harness.service,
                session_factory,
                session_auth=harness.session_auth,
            )

    async def test_secure_cookies_over_plain_http_refuse_every_form(
        self, tmp_path: Path
    ) -> None:
        """What the recipe's ``SESSION_COOKIE_SECURE`` warning claims."""
        harness = await _build(tmp_path, secure_cookies=True)
        await _account(harness)
        async with _client(harness.app) as client:
            response = await _form(
                client, "/login", email="ana@example.com", password=PASSWORD
            )
        assert response.status_code == 403

    def test_the_pages_stay_out_of_openapi(self, harness: Harness) -> None:
        assert harness.app.openapi()["paths"].keys() == {"/me", "/vault"}


class TestReviewFindings:
    """Gaps a review found before merge, pinned."""

    async def test_secure_cookies_use_the_host_prefix(self, tmp_path: Path) -> None:
        """A sibling subdomain cannot plant a ``__Host-`` cookie."""
        harness = await _build(tmp_path, secure_cookies=True)
        await _account(harness)
        async with _client(harness.app, "https") as client:
            page = await client.get("/login")
            response = await _form(
                client, "/login", email="ana@example.com", password=PASSWORD
            )
        assert f"__Host-{FORM_CSRF_COOKIE}=" in page.headers["set-cookie"]
        assert "Path=/" in page.headers["set-cookie"]
        assert response.status_code == 303

    async def test_a_planted_unprefixed_cookie_does_not_pass(
        self, tmp_path: Path
    ) -> None:
        """The plain name is what a sibling host could set; it is not read."""
        harness = await _build(tmp_path, secure_cookies=True)
        await _account(harness)
        async with _client(harness.app, "https") as client:
            client.cookies.set(FORM_CSRF_COOKIE, "planted", path="/")
            response = await client.post(
                "/login",
                data={
                    "csrf_token": "planted",
                    "email": "ana@example.com",
                    "password": PASSWORD,
                },
            )
        assert response.status_code == 403

    async def test_the_mfa_step_cookie_stays_on_the_login_path(
        self, harness: Harness
    ) -> None:
        await _enroll(harness)
        async with _client(harness.app) as client:
            response = await _form(
                client, "/login", email="ana@example.com", password=PASSWORD
            )
        cookie = next(
            value
            for value in response.headers.get_list("set-cookie")
            if value.startswith(f"{AUTH_PAGES_MFA_COOKIE}=")
        )
        assert "Path=/login" in cookie

    async def test_next_with_a_query_survives_the_mfa_step(
        self, harness: Harness
    ) -> None:
        secret = await _enroll(harness)
        code = pyotp.TOTP(secret).at(int(time.time()) + 30)
        async with _client(harness.app) as client:
            step = await _form(
                client,
                "/login",
                email="ana@example.com",
                password=PASSWORD,
                next="/me?a=1&b=2",
            )
            page = await client.get(step.headers["location"])
            done = await client.post(
                "/login/mfa",
                data={
                    "csrf_token": _token(page.text),
                    "code": code,
                    "next": "/me?a=1&b=2",
                },
            )
        assert step.headers["location"] == "/login/mfa?next=/me%3Fa%3D1%26b%3D2"
        assert 'value="/me?a=1&amp;b=2"' in page.text
        assert done.headers["location"] == "/me?a=1&b=2"

    @pytest.mark.parametrize("path", ["/login/mfa", "/activation/request"])
    async def test_every_form_refuses_a_post_without_the_token(
        self, harness: Harness, path: str
    ) -> None:
        await _enroll(harness)
        async with _client(harness.app) as client:
            await _form(client, "/login", email="ana@example.com", password=PASSWORD)
            response = await client.post(path, data={"code": "1", "email": "x@x.io"})
        assert response.status_code == 403

    async def test_the_mfa_budget_answers_429(self, harness: Harness) -> None:
        await _enroll(harness)
        async with _client(harness.app) as client:
            await _form(client, "/login", email="ana@example.com", password=PASSWORD)
            for _ in range(5):
                await _form(client, "/login/mfa", code="000000")
            blocked = await _form(client, "/login/mfa", code="000000")
        assert blocked.status_code == 429
        assert int(blocked.headers["Retry-After"]) > 0

    async def test_the_signup_budget_answers_429(self, harness: Harness) -> None:
        async with _client(harness.app) as client:
            for index in range(10):
                await _form(
                    client, "/signup", email=f"u{index}@example.com", password="x"
                )
            blocked = await _form(
                client, "/signup", email="last@example.com", password=PASSWORD
            )
        assert blocked.status_code == 429

    async def test_the_ip_budget_applies_to_the_pages(self, tmp_path: Path) -> None:
        throttle = AttemptThrottle(
            InMemoryThrottleBackend(), max_attempts=2, window_seconds=900
        )
        harness = await _build(tmp_path, login_ip_throttle=throttle)
        await _account(harness)
        async with _client(harness.app) as client:
            for email in ("a@example.com", "b@example.com"):
                await _form(client, "/login", email=email, password="nope-nope-123")
            blocked = await _form(
                client, "/login", email="ana@example.com", password=PASSWORD
            )
        assert blocked.status_code == 429

    async def test_one_throttle_shared_by_both_routers_is_one_budget(
        self, harness: Harness
    ) -> None:
        """What the recipe tells you to do when both routers are mounted."""
        from tempest_fastapi_sdk import make_auth_router

        shared = AttemptThrottle(
            InMemoryThrottleBackend(), max_attempts=3, window_seconds=900
        )
        await _account(harness)

        async def session_factory() -> AsyncIterator[AsyncSession]:
            async with harness.factory() as session:
                yield session

        app = FastAPI()
        register_exception_handlers(app)
        app.include_router(
            make_auth_router(
                harness.service,
                session_factory=session_factory,
                recovery_code_model=PagesRecoveryCode,
                login_throttle=shared,
            )
        )
        app.include_router(
            make_auth_pages_router(
                harness.service,
                session_factory,
                session_auth=harness.session_auth,
                recovery_code_model=PagesRecoveryCode,
                login_throttle=shared,
            )
        )
        async with _client(app) as client:
            for _ in range(3):
                await client.post(
                    "/auth/login",
                    json={"email": "ana@example.com", "password": "nope-nope-123"},
                )
            blocked = await _form(
                client, "/login", email="ana@example.com", password=PASSWORD
            )
        assert blocked.status_code == 429

    async def test_resend_with_the_mailer_off_renders_the_page(
        self, harness: Harness
    ) -> None:
        await _account(harness, "pending@example.com", active=False)
        harness.service.email = SimpleNamespace(enabled=False)  # type: ignore[assignment]
        async with _client(harness.app) as client:
            response = await _form(
                client, "/activation/request", email="pending@example.com"
            )
        assert response.status_code == 503
        assert "<form" in response.text


class TestRevealInactiveAccount:
    """``AUTH_REVEAL_INACTIVE_ACCOUNT`` answers with the page, not JSON."""

    async def test_the_inactive_refusal_renders_the_login_page(
        self, tmp_path: Path
    ) -> None:
        harness = await _build(tmp_path, AUTH_REVEAL_INACTIVE_ACCOUNT=True)
        await _account(harness, active=False)
        async with _client(harness.app) as client:
            response = await _form(
                client, "/login", email="ana@example.com", password=PASSWORD
            )
        assert response.status_code == 403
        assert response.headers["content-type"].startswith("text/html")
        assert "<form" in response.text
        assert _session_cookie(response) is None
