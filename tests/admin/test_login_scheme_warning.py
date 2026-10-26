"""The admin login page warns when ``Secure`` cookies meet a plain scheme.

Behind a TLS-terminating proxy the app sees ``http``; the login form then
posts to ``http://`` and the browser flags the password field. Nothing in
the response says why, so the page logs the setting that fixes it.
"""

from __future__ import annotations

import logging
from collections.abc import AsyncIterator

import pytest
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient

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

ROUTER_LOGGER = "tempest_fastapi_sdk.admin.router"
"""Logger the admin router writes to."""


class WarnUser(BaseUserModel):
    __tablename__ = "admin_scheme_warning_users"


class WarnThing(BaseModel):
    __tablename__ = "admin_scheme_warning_thing"


@pytest.fixture
async def db() -> AsyncIterator[AsyncDatabaseManager]:
    """Open an in-memory database with the admin tables.

    Yields:
        AsyncDatabaseManager: The connected manager.
    """
    manager = AsyncDatabaseManager("sqlite+aiosqlite:///:memory:")
    await manager.connect()
    await manager.create_tables()
    yield manager
    await manager.drop_tables()
    await manager.disconnect()


def _app(
    db: AsyncDatabaseManager,
    *,
    cookie_secure: bool,
    public_scheme: str | None = None,
) -> FastAPI:
    """Build the admin panel under test.

    Args:
        db (AsyncDatabaseManager): The connected database.
        cookie_secure (bool): Forwarded to ``make_admin_router``.
        public_scheme (str | None): ``PublicSchemeMiddleware`` scheme, or
            ``None`` to leave the middleware out.

    Returns:
        FastAPI: The application.
    """
    site = AdminSite(title="Warn Admin")
    site.register(AdminModel(model=WarnThing, list_display=[WarnThing.id]))
    app = FastAPI()
    if public_scheme is not None:
        app.add_middleware(PublicSchemeMiddleware, scheme=public_scheme)
    app.include_router(
        make_admin_router(
            site,
            db=db,
            auth_backend=UserModelAuthBackend(WarnUser),
            secret_key=SECRET,
            cookie_secure=cookie_secure,
            show_metrics=False,
        )
    )
    return app


async def _warnings(
    app: FastAPI, base_url: str, caplog: pytest.LogCaptureFixture
) -> list[str]:
    """Render the login page and collect the router's warnings.

    Args:
        app (FastAPI): The admin application.
        base_url (str): Origin the request is sent to.
        caplog (pytest.LogCaptureFixture): The log capture.

    Returns:
        list[str]: Messages the router logged at ``WARNING``.
    """
    caplog.clear()
    with caplog.at_level(logging.WARNING, logger=ROUTER_LOGGER):
        async with AsyncClient(
            transport=ASGITransport(app=app), base_url=base_url
        ) as client:
            response = await client.get("/admin/login")
    assert response.status_code == 200
    return [
        record.getMessage()
        for record in caplog.records
        if record.name == ROUTER_LOGGER and record.levelno == logging.WARNING
    ]


@pytest.mark.asyncio
async def test_secure_cookies_over_http_on_a_public_host_warn(
    db: AsyncDatabaseManager, caplog: pytest.LogCaptureFixture
) -> None:
    messages = await _warnings(
        _app(db, cookie_secure=True), "http://admin.example.com", caplog
    )
    assert len(messages) == 1
    assert "SERVER_PUBLIC_SCHEME=https" in messages[0]
    assert "admin.example.com" in messages[0]


@pytest.mark.asyncio
async def test_the_middleware_silences_the_warning(
    db: AsyncDatabaseManager, caplog: pytest.LogCaptureFixture
) -> None:
    app = _app(db, cookie_secure=True, public_scheme="https")
    assert await _warnings(app, "http://admin.example.com", caplog) == []


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("cookie_secure", "base_url"),
    [
        (True, "https://admin.example.com"),
        (True, "http://localhost:8000"),
        (True, "http://127.0.0.1:8000"),
        (False, "http://admin.example.com"),
    ],
)
async def test_no_warning_where_the_form_is_fine(
    db: AsyncDatabaseManager,
    caplog: pytest.LogCaptureFixture,
    cookie_secure: bool,
    base_url: str,
) -> None:
    app = _app(db, cookie_secure=cookie_secure)
    assert await _warnings(app, base_url, caplog) == []
