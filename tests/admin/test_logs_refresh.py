"""The admin logs page has a refresh link that brings the newest records (#711).

Before, seeing new records meant leaving the tab and coming back. The page
reads the log files on every request, so a link to itself is all a refresh
takes; it keeps ``source`` and ``q`` and drops ``page``, so it lands on the
newest records under the current filter.
"""

from __future__ import annotations

import html
import json
import re
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from pathlib import Path

import pytest
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient

from tempest_fastapi_sdk import (
    AdminSite,
    AsyncDatabaseManager,
    BaseUserModel,
    UserModelAuthBackend,
    make_admin_router,
)
from tempest_fastapi_sdk.testing import admin_login

SECRET = "x" * 48


class RefreshUser(BaseUserModel):
    __tablename__ = "admin_refresh_users"


def _record(message: str, level: str = "INFO") -> str:
    """Encode one structured log line.

    Args:
        message (str): The record's message.
        level (str): The record's level.

    Returns:
        str: The JSON line, newline included.
    """
    return (
        json.dumps(
            {
                "timestamp": "2026-11-09T12:00:00Z",
                "level": level,
                "logger": "app.orders",
                "message": message,
            }
        )
        + "\n"
    )


def _refresh_href(page: str) -> str:
    """Return the decoded ``href`` of the refresh link on a rendered page.

    Args:
        page (str): The HTML.

    Returns:
        str: The link target.
    """
    match = re.search(
        r'class="[^"]*tempest-admin-logs__refresh[^"]*" href="([^"]+)"', page
    )
    assert match is not None, "refresh link missing"
    return html.unescape(match.group(1))


@pytest.fixture
async def logs_app(tmp_path: Path) -> AsyncIterator[tuple[FastAPI, Path]]:
    """Boot an admin app over a log directory the test can append to.

    Yields:
        tuple[FastAPI, Path]: The app and its log directory.
    """
    log_dir = tmp_path / "logs"
    log_dir.mkdir()
    (log_dir / "info.log").write_text(_record("order placed alpha"), encoding="utf-8")

    db = AsyncDatabaseManager("sqlite+aiosqlite:///:memory:")
    await db.connect()
    await db.create_tables()
    async with db.get_session_context() as session:
        user = RefreshUser(email="root@example.com", hashed_password="", is_admin=True)
        user.set_password("hunter2")
        session.add(user)
        await session.commit()

    app = FastAPI()
    app.include_router(
        make_admin_router(
            AdminSite(title="Refresh Admin"),
            db=db,
            auth_backend=UserModelAuthBackend(RefreshUser),
            secret_key=SECRET,
            cookie_secure=False,
            show_logs=True,
            log_dir=str(log_dir),
        )
    )
    yield app, log_dir
    await db.drop_tables()
    await db.disconnect()


@asynccontextmanager
async def _logged_in(app: FastAPI) -> AsyncIterator[AsyncClient]:
    """Open a client with the admin session cookie set.

    Args:
        app (FastAPI): The app.

    Yields:
        AsyncClient: The authenticated client, closed on exit.
    """
    async with AsyncClient(
        transport=ASGITransport(app=app), base_url="http://test"
    ) as client:
        await admin_login(client, "root@example.com", "hunter2")
        yield client


class TestRefreshLink:
    async def test_the_page_has_a_refresh_link(
        self, logs_app: tuple[FastAPI, Path]
    ) -> None:
        app, _ = logs_app
        async with _logged_in(app) as client:
            response = await client.get("/admin/logs")

        assert response.status_code == 200
        assert _refresh_href(response.text) == "/admin/logs?source=all"
        assert "Read at" in response.text

    async def test_it_keeps_the_filter_and_drops_the_page(
        self, logs_app: tuple[FastAPI, Path]
    ) -> None:
        """From page 3 of a search, refresh lands on page 1 of the same search."""
        app, _ = logs_app
        async with _logged_in(app) as client:
            response = await client.get(
                "/admin/logs", params={"source": "info", "q": "order", "page": 3}
            )

        assert _refresh_href(response.text) == "/admin/logs?source=info&q=order"

    async def test_following_it_shows_a_record_written_after_the_first_load(
        self, logs_app: tuple[FastAPI, Path]
    ) -> None:
        app, log_dir = logs_app
        async with _logged_in(app) as client:
            first = await client.get("/admin/logs")
            with (log_dir / "info.log").open("a", encoding="utf-8") as handle:
                handle.write(_record("order placed beta"))
            refreshed = await client.get(_refresh_href(first.text))

        assert "order placed beta" not in first.text
        assert "order placed beta" in refreshed.text

    async def test_it_shows_while_no_log_file_exists_yet(self, tmp_path: Path) -> None:
        """The empty state is where one waits for records, so the link stays."""
        db = AsyncDatabaseManager("sqlite+aiosqlite:///:memory:")
        await db.connect()
        await db.create_tables()
        async with db.get_session_context() as session:
            user = RefreshUser(
                email="root@example.com", hashed_password="", is_admin=True
            )
            user.set_password("hunter2")
            session.add(user)
            await session.commit()
        app = FastAPI()
        app.include_router(
            make_admin_router(
                AdminSite(title="Refresh Admin"),
                db=db,
                auth_backend=UserModelAuthBackend(RefreshUser),
                secret_key=SECRET,
                cookie_secure=False,
                show_logs=True,
                log_dir=str(tmp_path / "empty"),
            )
        )
        try:
            async with _logged_in(app) as client:
                response = await client.get("/admin/logs")
        finally:
            await db.drop_tables()
            await db.disconnect()

        assert "No log files found" in response.text
        assert _refresh_href(response.text) == "/admin/logs?source=all"
