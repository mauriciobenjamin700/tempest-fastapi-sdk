"""Tests for tempest_fastapi_sdk.api.middlewares.PublicSchemeMiddleware."""

from __future__ import annotations

import re
from collections.abc import AsyncIterator

import pytest
from fastapi import FastAPI, Request, WebSocket
from httpx import ASGITransport, AsyncClient
from starlette.testclient import TestClient
from starlette.types import Receive, Scope, Send

from tempest_fastapi_sdk import (
    AdminModel,
    AdminSite,
    AsyncDatabaseManager,
    BaseModel,
    BaseUserModel,
    PublicSchemeMiddleware,
    UserModelAuthBackend,
    make_admin_router,
)

SECRET = "x" * 48
"""Admin signing key of the panel under test."""

FORM_ACTION = re.compile(r'<form method="post" action="([^"]+)"')
"""The login form's ``action`` attribute, as ``login.html`` renders it."""


class SchemeUser(BaseUserModel):
    __tablename__ = "public_scheme_users"


class SchemeThing(BaseModel):
    __tablename__ = "public_scheme_thing"


def _url_app(scheme: str) -> FastAPI:
    """Build an app that echoes the URL Starlette builds for a route.

    Args:
        scheme (str): Forwarded to ``PublicSchemeMiddleware``.

    Returns:
        FastAPI: The application.
    """
    app = FastAPI()
    app.add_middleware(PublicSchemeMiddleware, scheme=scheme)

    @app.get("/target", name="target")
    async def target(request: Request) -> dict[str, str]:
        return {"url": str(request.url_for("target"))}

    @app.websocket("/ws")
    async def ws(websocket: WebSocket) -> None:
        await websocket.accept()
        await websocket.send_text(websocket.url.scheme)
        await websocket.close()

    return app


async def _get_url(app: FastAPI) -> str:
    """Fetch ``/target`` over plain ``http://``, as a proxy forwards it.

    Args:
        app (FastAPI): The application.

    Returns:
        str: The URL the route built for itself.
    """
    async with AsyncClient(
        transport=ASGITransport(app=app), base_url="http://api.example.com"
    ) as client:
        response = await client.get("/target")
    return str(response.json()["url"])


@pytest.mark.asyncio
async def test_https_rewrites_urls_built_behind_a_tls_proxy() -> None:
    assert await _get_url(_url_app("https")) == "https://api.example.com/target"


@pytest.mark.asyncio
async def test_empty_scheme_keeps_the_connection_scheme() -> None:
    assert await _get_url(_url_app("")) == "http://api.example.com/target"


def test_websocket_scope_gets_the_websocket_spelling() -> None:
    client = TestClient(_url_app("https"), base_url="http://api.example.com")
    with client.websocket_connect("/ws") as websocket:
        assert websocket.receive_text() == "wss"


@pytest.mark.asyncio
async def test_lifespan_scope_is_left_untouched() -> None:
    seen: list[Scope] = []

    async def inner(scope: Scope, receive: Receive, send: Send) -> None:
        seen.append(dict(scope))

    middleware = PublicSchemeMiddleware(inner, scheme="https")
    await middleware({"type": "lifespan"}, _never_receive, _never_send)
    assert seen == [{"type": "lifespan"}]


async def _never_receive() -> dict[str, str]:
    """Stand in for an ASGI ``receive`` the test never calls.

    Raises:
        AssertionError: Always.
    """
    raise AssertionError("receive was called")


async def _never_send(message: dict[str, str]) -> None:
    """Stand in for an ASGI ``send`` the test never calls.

    Args:
        message (dict[str, str]): The ASGI message.

    Raises:
        AssertionError: Always.
    """
    raise AssertionError("send was called")


def test_an_unknown_scheme_is_refused() -> None:
    with pytest.raises(ValueError, match="'ftp' is not one of"):
        PublicSchemeMiddleware(FastAPI(), scheme="ftp")  # type: ignore[arg-type]


@pytest.fixture
async def admin_db() -> AsyncIterator[AsyncDatabaseManager]:
    """Open an in-memory database with the admin tables.

    Yields:
        AsyncDatabaseManager: The connected manager.
    """
    db = AsyncDatabaseManager("sqlite+aiosqlite:///:memory:")
    await db.connect()
    await db.create_tables()
    yield db
    await db.drop_tables()
    await db.disconnect()


def _admin_app(db: AsyncDatabaseManager, scheme: str | None) -> FastAPI:
    """Build the admin panel, optionally behind ``PublicSchemeMiddleware``.

    Args:
        db (AsyncDatabaseManager): The connected database.
        scheme (str | None): The middleware's scheme, or ``None`` to
            leave the middleware out.

    Returns:
        FastAPI: The application.
    """
    site = AdminSite(title="Scheme Admin")
    site.register(AdminModel(model=SchemeThing, list_display=[SchemeThing.id]))
    app = FastAPI()
    if scheme is not None:
        app.add_middleware(PublicSchemeMiddleware, scheme=scheme)
    app.include_router(
        make_admin_router(
            site,
            db=db,
            auth_backend=UserModelAuthBackend(SchemeUser),
            secret_key=SECRET,
            cookie_secure=True,
            show_metrics=False,
        )
    )
    return app


async def _login_action(app: FastAPI) -> str:
    """Render the login page over ``http://`` and read the form's target.

    Args:
        app (FastAPI): The admin application.

    Returns:
        str: The ``action`` attribute of the login form.
    """
    async with AsyncClient(
        transport=ASGITransport(app=app), base_url="http://admin.example.com"
    ) as client:
        response = await client.get("/admin/login")
    assert response.status_code == 200
    match = FORM_ACTION.search(response.text)
    assert match is not None
    return match.group(1)


@pytest.mark.asyncio
async def test_behind_a_tls_proxy_the_login_form_posts_to_http(
    admin_db: AsyncDatabaseManager,
) -> None:
    """The defect: without the middleware the form targets ``http://``."""
    action = await _login_action(_admin_app(admin_db, None))
    assert action == "http://admin.example.com/admin/login"


@pytest.mark.asyncio
async def test_with_https_the_login_form_posts_to_https(
    admin_db: AsyncDatabaseManager,
) -> None:
    action = await _login_action(_admin_app(admin_db, "https"))
    assert action == "https://admin.example.com/admin/login"
