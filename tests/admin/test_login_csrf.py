"""``POST /admin/login`` refuses a submit without the form's CSRF token (#603).

Login CSRF: a page on another site makes an operator's browser post the
attacker's credentials to ``/login``, and the response sets the admin
cookie of the attacker's account. Every other admin form was protected by
the session's token; the login form ran before any session existed.
"""

from __future__ import annotations

import time
from collections.abc import AsyncIterator

import pytest
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient, Response

from tempest_fastapi_sdk import (
    AdminModel,
    AdminSite,
    AsyncDatabaseManager,
    AttemptThrottle,
    BaseModel,
    BaseUserModel,
    InMemoryThrottleBackend,
    UserModelAuthBackend,
    make_admin_router,
)
from tempest_fastapi_sdk.admin import AdminLoginCSRF, SignedCookieSessionStore
from tempest_fastapi_sdk.admin.session import (
    ADMIN_LOGIN_CSRF_COOKIE,
    ADMIN_LOGIN_CSRF_MAX_AGE_SECONDS,
    ADMIN_LOGIN_CSRF_SALT,
)
from tempest_fastapi_sdk.testing import admin_csrf_token, admin_login

SECRET = "x" * 48
"""Admin signing key used by every app in this module."""

PASSWORD = "hunter2"
"""Password of the seeded admin."""

IDENTIFIER_BUDGET = 3
"""Per-identifier login budget of the app under test."""


class CsrfUser(BaseUserModel):
    __tablename__ = "admin_login_csrf_users"


class CsrfThing(BaseModel):
    __tablename__ = "admin_login_csrf_thing"


@pytest.fixture
async def app() -> AsyncIterator[FastAPI]:
    """Build an admin app with one admin and a small identifier budget.

    Yields:
        FastAPI: The application.
    """
    async for application in _app(cookie_secure=False):
        yield application


@pytest.fixture
async def secure_app() -> AsyncIterator[FastAPI]:
    """The same app with the production default, ``Secure`` cookies.

    Yields:
        FastAPI: The application.
    """
    async for application in _app(cookie_secure=True):
        yield application


async def _app(*, cookie_secure: bool) -> AsyncIterator[FastAPI]:
    """Build the admin app under test.

    Args:
        cookie_secure (bool): Forwarded to ``make_admin_router``.

    Yields:
        FastAPI: The application.
    """
    db = AsyncDatabaseManager("sqlite+aiosqlite:///:memory:")
    await db.connect()
    await db.create_tables()
    async with db.get_session_context() as session:
        user = CsrfUser(email="root@example.com", hashed_password="", is_admin=True)
        user.set_password(PASSWORD)
        session.add(user)
        await session.commit()
    site = AdminSite(title="CSRF Admin")
    site.register(AdminModel(model=CsrfThing, list_display=[CsrfThing.id]))
    application = FastAPI()
    application.include_router(
        make_admin_router(
            site,
            db=db,
            auth_backend=UserModelAuthBackend(CsrfUser),
            secret_key=SECRET,
            cookie_secure=cookie_secure,
            show_metrics=False,
            login_throttle=AttemptThrottle(
                InMemoryThrottleBackend(),
                max_attempts=IDENTIFIER_BUDGET,
                window_seconds=900,
            ),
        )
    )
    yield application
    await db.drop_tables()
    await db.disconnect()


def _client(app: FastAPI, scheme: str = "http") -> AsyncClient:
    """Build a client bound to ``app``.

    Args:
        app (FastAPI): The application.
        scheme (str): ``"http"`` or ``"https"`` for the base URL.

    Returns:
        AsyncClient: A client that does not follow redirects.
    """
    return AsyncClient(transport=ASGITransport(app=app), base_url=f"{scheme}://test")


async def _submit(client: AsyncClient, token: str | None) -> Response:
    """Post the right credentials with ``token`` (or no field at all).

    Args:
        client (AsyncClient): The client.
        token (str | None): The ``csrf_token`` field, or ``None`` to omit it.

    Returns:
        Response: The ``POST /admin/login`` response.
    """
    data = {"identifier": "root@example.com", "password": PASSWORD}
    if token is not None:
        data["csrf_token"] = token
    return await client.post("/admin/login", data=data, follow_redirects=False)


def _sets_session(response: Response) -> bool:
    """Whether ``response`` sets the admin session cookie.

    Args:
        response (Response): A login response.

    Returns:
        bool: ``True`` when ``tempest_admin=`` is among its cookies.
    """
    return any(
        value.startswith("tempest_admin=")
        for value in response.headers.get_list("set-cookie")
    )


def _signed_at(token: str, timestamp: int) -> str:
    """Sign ``token`` as the router would have at ``timestamp``.

    Args:
        token (str): The token.
        timestamp (int): The signing time, in epoch seconds.

    Returns:
        str: The cookie value.
    """
    import itsdangerous

    class _Clock(itsdangerous.TimestampSigner):
        def get_timestamp(self) -> int:
            return timestamp

    signer = _Clock(SECRET, salt=ADMIN_LOGIN_CSRF_SALT)
    return signer.sign(token.encode("ascii")).decode("ascii")


class TestRefused:
    """Every way of not presenting the form's token is a ``403``."""

    async def test_a_submit_without_the_field(self, app: FastAPI) -> None:
        """The cross-site page: no cookie, no field."""
        async with _client(app) as client:
            response = await _submit(client, None)
        assert response.status_code == 403
        assert "sign-in form expired" in response.text
        assert not _sets_session(response)

    async def test_the_field_without_its_cookie(self, app: FastAPI) -> None:
        """A token read elsewhere does not travel with its cookie."""
        async with _client(app) as reader:
            token = admin_csrf_token((await reader.get("/admin/login")).text)
        async with _client(app) as client:
            response = await _submit(client, token)
        assert response.status_code == 403
        assert not _sets_session(response)

    async def test_a_token_from_another_cookie(self, app: FastAPI) -> None:
        """The attacker's own form token against the victim's cookie."""
        async with _client(app) as attacker:
            foreign = admin_csrf_token((await attacker.get("/admin/login")).text)
        async with _client(app) as victim:
            await victim.get("/admin/login")
            response = await _submit(victim, foreign)
        assert response.status_code == 403
        assert not _sets_session(response)

    async def test_an_expired_cookie(self, app: FastAPI) -> None:
        stale = int(time.time()) - ADMIN_LOGIN_CSRF_MAX_AGE_SECONDS - 60
        async with _client(app) as client:
            client.cookies.set(
                ADMIN_LOGIN_CSRF_COOKIE,
                _signed_at("tok", stale),
                path="/admin",
            )
            response = await _submit(client, "tok")
        assert response.status_code == 403
        assert not _sets_session(response)

    async def test_a_tampered_cookie(self, app: FastAPI) -> None:
        async with _client(app) as client:
            client.cookies.set(ADMIN_LOGIN_CSRF_COOKIE, "tok.forged.sig", path="/admin")
            response = await _submit(client, "tok")
        assert response.status_code == 403

    async def test_the_refusal_renders_a_usable_form(self, app: FastAPI) -> None:
        """The honest stale-tab case recovers with one retry."""
        async with _client(app) as client:
            refused = await _submit(client, "stale")
            retried = await _submit(client, admin_csrf_token(refused.text))
        assert refused.status_code == 403
        assert retried.status_code == 303

    async def test_refusals_spend_no_login_attempts(self, app: FastAPI) -> None:
        """A cross-site flood cannot lock the operator out."""
        async with _client(app) as client:
            for _ in range(IDENTIFIER_BUDGET * 2):
                assert (await _submit(client, None)).status_code == 403
            response = await admin_login(client, "root@example.com", PASSWORD)
        assert response.status_code == 303

    async def test_a_wrong_password_is_not_revealed_without_a_token(
        self,
        app: FastAPI,
    ) -> None:
        """The refusal comes before the password is checked."""
        async with _client(app) as client:
            response = await client.post(
                "/admin/login",
                data={"identifier": "root@example.com", "password": "nope"},
            )
        assert response.status_code == 403


class TestAccepted:
    async def test_form_then_submit_signs_in(self, app: FastAPI) -> None:
        async with _client(app) as client:
            response = await admin_login(client, "root@example.com", PASSWORD)
            dashboard = await client.get("/admin/")
        assert response.status_code == 303
        assert response.headers["location"] == "/admin/"
        assert _sets_session(response)
        assert dashboard.status_code == 200

    async def test_the_pre_login_cookie_is_dropped_on_success(
        self,
        app: FastAPI,
    ) -> None:
        async with _client(app) as client:
            response = await admin_login(client, "root@example.com", PASSWORD)
        cleared = [
            value
            for value in response.headers.get_list("set-cookie")
            if value.startswith(f"{ADMIN_LOGIN_CSRF_COOKIE}=")
        ]
        assert cleared
        assert "Max-Age=0" in cleared[0] or "expires=" in cleared[0].lower()

    async def test_two_tabs_share_one_token(self, app: FastAPI) -> None:
        """A second render reuses the valid token instead of rotating it."""
        async with _client(app) as client:
            first = admin_csrf_token((await client.get("/admin/login")).text)
            second = admin_csrf_token((await client.get("/admin/login")).text)
            response = await _submit(client, first)
        assert first == second
        assert response.status_code == 303

    async def test_the_form_carries_the_hidden_field(self, app: FastAPI) -> None:
        async with _client(app) as client:
            page = await client.get("/admin/login")
        assert page.status_code == 200
        assert 'type="hidden" name="csrf_token"' in page.text
        assert ADMIN_LOGIN_CSRF_COOKIE in page.headers["set-cookie"]


class TestSecureCookies:
    """What the recipe's ``Secure`` admonition and testing tip claim."""

    async def test_over_plain_http_every_submit_is_refused(
        self,
        secure_app: FastAPI,
    ) -> None:
        """The ``Secure`` token cookie never comes back over ``http://``."""
        async with _client(secure_app, "http") as client:
            response = await admin_login(client, "root@example.com", PASSWORD)
        assert response.status_code == 403
        assert "sign-in form expired" in response.text

    async def test_over_https_the_login_works(self, secure_app: FastAPI) -> None:
        async with _client(secure_app, "https") as client:
            response = await admin_login(client, "root@example.com", PASSWORD)
            dashboard = await client.get("/admin/")
        assert response.status_code == 303
        assert dashboard.status_code == 200


class TestSigner:
    def test_a_session_cookie_does_not_verify_as_a_login_token(self) -> None:
        """Same key, different salt: one cookie never passes for the other."""
        import itsdangerous

        session_signer = itsdangerous.TimestampSigner(
            SECRET, salt="tempest-admin-session"
        )
        forged = session_signer.sign(b"tok").decode("ascii")
        login_signer = itsdangerous.TimestampSigner(SECRET, salt=ADMIN_LOGIN_CSRF_SALT)
        with pytest.raises(itsdangerous.BadSignature):
            login_signer.unsign(forged)

    def test_the_session_store_salt_is_the_one_assumed_above(self) -> None:
        store = SignedCookieSessionStore(SECRET)
        assert store._signer.salt == b"tempest-admin-session"

    def test_a_short_key_is_refused(self) -> None:
        with pytest.raises(ValueError, match="32 bytes"):
            AdminLoginCSRF("short")


class TestTestingHelper:
    def test_a_page_without_the_field_raises(self) -> None:
        with pytest.raises(ValueError, match="no csrf_token"):
            admin_csrf_token("<form></form>")
