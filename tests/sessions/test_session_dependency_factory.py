"""``make_session_dependency`` with a ``SessionAuth`` built after import (#381).

A service that builds its ``SessionAuth`` from settings — behind an
``@lru_cache`` that tests clear and rebuild per test — has no instance to
pass when the dependency is declared at module level. The factory form
resolves the service per request, and the ``required=True`` overload types
the dependency as ``Session``, so the consumer drops the ``if`` it wrote only
to narrow ``Session | None``.
"""

from __future__ import annotations

import sys
import tempfile
from functools import lru_cache
from pathlib import Path

import pytest
from fastapi import Depends, FastAPI, Form, Request, status
from fastapi.responses import HTMLResponse, RedirectResponse, Response
from httpx import ASGITransport, AsyncClient

from tempest_fastapi_sdk import (
    MemorySessionStore,
    Session,
    SessionAuth,
    SessionMiddleware,
    SessionSettings,
    make_session_dependency,
    redirect_to,
)

CREDENTIALS: dict[str, str] = {"user": "root", "password": "first-pass-123"}
"""What the cached settings read — the environment, in a real service."""


@lru_cache
def get_session_auth() -> SessionAuth:
    """Build the service from the current credentials, once per cache.

    Returns:
        SessionAuth: A fixed-credential service with its own store.
    """
    return SessionAuth.from_credentials(
        CREDENTIALS["user"],
        CREDENTIALS["password"],
        store=MemorySessionStore(),
        settings=SessionSettings(SESSION_COOKIE_SECURE=False),
    )


def _app_session_auth(request: Request) -> SessionAuth:
    """Return the service the request's app was built with.

    Args:
        request (Request): The inbound request.

    Returns:
        SessionAuth: ``request.app.state.session_auth``.
    """
    auth: SessionAuth = request.app.state.session_auth
    return auth


require_admin = make_session_dependency(
    session_auth=_app_session_auth,
    on_missing=redirect_to("/login"),
)
"""Declared at import, before any ``SessionAuth`` exists."""


def build_app() -> FastAPI:
    """Build an app the way a service's ``create_app`` would.

    Returns:
        FastAPI: An app whose ``state.session_auth`` comes from the cache.
    """
    app = FastAPI()
    app.state.session_auth = get_session_auth()

    @app.post("/login")
    async def login(
        request: Request,
        username: str = Form(),
        password: str = Form(),
    ) -> Response:
        auth: SessionAuth = request.app.state.session_auth
        _session, plaintext = await auth.login_with_credentials(username, password)
        response = RedirectResponse("/admin", status_code=status.HTTP_303_SEE_OTHER)
        response.set_cookie(value=plaintext, **auth.settings.session_cookie_kwargs())
        return response

    @app.get("/admin")
    async def admin(session: Session = Depends(require_admin)) -> HTMLResponse:
        return HTMLResponse(f"hello {session.user_id}")

    return app


async def _login(client: AsyncClient, password: str) -> None:
    """Log in on ``client``, keeping the cookie in its jar.

    Args:
        client (AsyncClient): The client bound to one app.
        password (str): The password to submit.
    """
    response = await client.post(
        "/login",
        data={"username": CREDENTIALS["user"], "password": password},
    )
    assert response.status_code == status.HTTP_303_SEE_OTHER


class TestFactoryResolvesPerRequest:
    """The module-level dependency follows whichever app serves the request."""

    async def test_two_apps_with_swapped_settings_each_use_their_own(self) -> None:
        get_session_auth.cache_clear()
        first = build_app()
        CREDENTIALS["password"] = "second-pass-456"
        get_session_auth.cache_clear()
        second = build_app()
        CREDENTIALS["password"] = "first-pass-123"

        async with (
            AsyncClient(transport=ASGITransport(app=first), base_url="http://a") as a,
            AsyncClient(transport=ASGITransport(app=second), base_url="http://a") as b,
        ):
            await _login(a, "first-pass-123")
            await _login(b, "second-pass-456")
            on_first = await a.get("/admin")
            on_second = await b.get("/admin")
            b.cookies = a.cookies
            foreign_cookie = await b.get("/admin")

        assert on_first.status_code == status.HTTP_200_OK
        assert on_second.status_code == status.HTTP_200_OK
        assert foreign_cookie.status_code == status.HTTP_303_SEE_OTHER
        assert foreign_cookie.headers["location"] == "/login"
        get_session_auth.cache_clear()

    async def test_without_a_cookie_the_redirect_still_applies(self) -> None:
        get_session_auth.cache_clear()
        app = build_app()

        async with AsyncClient(
            transport=ASGITransport(app=app), base_url="http://a"
        ) as client:
            response = await client.get("/admin")

        assert response.status_code == status.HTTP_303_SEE_OTHER
        assert response.headers["location"] == "/login"
        get_session_auth.cache_clear()

    async def test_the_factory_is_skipped_when_the_middleware_resolved(self) -> None:
        auth = SessionAuth.from_credentials(
            "root",
            "middle-pass-789",
            store=MemorySessionStore(),
            settings=SessionSettings(SESSION_COOKIE_SECURE=False),
        )
        _session, plaintext = await auth.login_with_credentials(
            "root", "middle-pass-789"
        )
        calls: list[str] = []

        def factory(request: Request) -> SessionAuth:
            """Count the calls; the middleware already did the work.

            Args:
                request (Request): The inbound request.

            Returns:
                SessionAuth: The service.
            """
            calls.append(request.url.path)
            return auth

        app = FastAPI()
        app.add_middleware(SessionMiddleware, session_auth=auth, settings=auth.settings)
        dependency = make_session_dependency(session_auth=factory)

        @app.get("/me")
        async def me(session: Session = Depends(dependency)) -> dict[str, str]:
            return {"user_id": str(session.user_id)}

        async with AsyncClient(
            transport=ASGITransport(app=app), base_url="http://a"
        ) as client:
            client.cookies.set(auth.settings.SESSION_COOKIE_NAME, plaintext)
            response = await client.get("/me")

        assert response.status_code == status.HTTP_200_OK
        assert calls == []

    def test_declaring_the_dependency_never_calls_the_factory(self) -> None:
        def factory(request: Request) -> SessionAuth:
            """Fail if called — there is no request at declaration time.

            Args:
                request (Request): Unused.

            Raises:
                AssertionError: Always.
            """
            raise AssertionError("factory called at declaration")

        make_session_dependency(session_auth=factory)


DOWNSTREAM_SNIPPET: str = """
from collections.abc import Awaitable, Callable

from fastapi import Request

from tempest_fastapi_sdk import Session, SessionAuth, make_session_dependency


def get_auth(request: Request) -> SessionAuth:
    auth: SessionAuth = request.app.state.session_auth
    return auth


required: Callable[[Request], Awaitable[Session]] = make_session_dependency(
    session_auth=get_auth,
)
optional: Callable[[Request], Awaitable[Session | None]] = (
    make_session_dependency(required=False, session_auth=get_auth)
)
narrowed: Callable[[Request], Awaitable[Session]] = make_session_dependency(
    required=False,
)
"""
"""The last assignment is the planted error: an optional dependency is not
``Session``. Without it the snippet would also pass if the overloads
collapsed to ``Any``."""


def test_required_dependency_is_typed_session_under_mypy_strict(
    tmp_path: Path,
) -> None:
    """``required=True`` is ``Session``, ``required=False`` stays optional."""
    mypy_api = pytest.importorskip("mypy.api", reason="mypy is a dev-group dependency")
    module = tmp_path / "downstream_admin.py"
    module.write_text(DOWNSTREAM_SNIPPET, encoding="utf-8")
    cache = Path(tempfile.gettempdir()) / "tempest-session-dependency-mypy"

    stdout, stderr, _ = mypy_api.run(
        [
            str(module),
            "--strict",
            "--no-error-summary",
            "--hide-error-context",
            "--no-color-output",
            "--cache-dir",
            str(cache),
            "--python-executable",
            sys.executable,
        ],
    )

    errors = [line for line in stdout.splitlines() if ": error:" in line]
    assert len(errors) == 1, f"{stdout}\n{stderr}"
    assert "downstream_admin.py:20:" in errors[0]
    assert "[assignment]" in errors[0]
