"""Tests for the admin sidebar navigation and the logs page."""

from __future__ import annotations

import json
from collections.abc import AsyncIterator
from pathlib import Path

import pytest
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient
from sqlalchemy import String
from sqlalchemy.orm import Mapped, mapped_column

from tempest_fastapi_sdk import (
    AdminModel,
    AdminSite,
    AsyncDatabaseManager,
    BaseModel,
    BaseUserModel,
    UserModelAuthBackend,
    make_admin_router,
)

SECRET = "x" * 48


class NavUser(BaseUserModel):
    __tablename__ = "admin_nav_users"


class GadgetModel(BaseModel):
    __tablename__ = "admin_nav_gadget"
    name: Mapped[str] = mapped_column(String(64), nullable=False)


gadget_admin = AdminModel(
    model=GadgetModel,
    list_display=[GadgetModel.id, GadgetModel.name],
    search_fields=[GadgetModel.name],
)


def _client(app: FastAPI) -> AsyncClient:
    return AsyncClient(transport=ASGITransport(app=app), base_url="http://test")


async def _build_app(
    *,
    show_logs: bool,
    log_dir: Path,
) -> tuple[FastAPI, AsyncDatabaseManager]:
    db = AsyncDatabaseManager("sqlite+aiosqlite:///:memory:")
    await db.connect()
    await db.create_tables()
    async with db.get_session_context() as session:
        user = NavUser(email="root@example.com", hashed_password="", is_admin=True)
        user.set_password("hunter2")
        session.add(user)
        session.add(GadgetModel(name="gadget-a"))
        await session.commit()

    site = AdminSite(title="Nav Admin")
    site.register(gadget_admin)
    app = FastAPI()
    app.include_router(
        make_admin_router(
            site,
            db=db,
            auth_backend=UserModelAuthBackend(NavUser),
            secret_key=SECRET,
            cookie_secure=False,
            show_logs=show_logs,
            log_dir=str(log_dir),
        )
    )
    return app, db


async def _login(client: AsyncClient) -> None:
    await client.post(
        "/admin/login",
        data={"identifier": "root@example.com", "password": "hunter2"},
    )


@pytest.fixture
async def app_no_logs(tmp_path: Path) -> AsyncIterator[FastAPI]:
    app, db = await _build_app(show_logs=False, log_dir=tmp_path / "logs")
    yield app
    await db.drop_tables()
    await db.disconnect()


@pytest.fixture
async def app_with_logs(tmp_path: Path) -> AsyncIterator[tuple[FastAPI, Path]]:
    log_dir = tmp_path / "logs"
    log_dir.mkdir()
    (log_dir / "info.log").write_text(
        json.dumps(
            {
                "timestamp": "2026-06-07T12:00:00Z",
                "level": "INFO",
                "logger": "app.orders",
                "message": "order placed alpha",
            }
        )
        + "\n"
        + json.dumps(
            {
                "timestamp": "2026-06-07T12:01:00Z",
                "level": "INFO",
                "logger": "app.orders",
                "message": "order placed beta",
            }
        )
        + "\n",
        encoding="utf-8",
    )
    app, db = await _build_app(show_logs=True, log_dir=log_dir)
    yield app, log_dir
    await db.drop_tables()
    await db.disconnect()


@pytest.mark.asyncio
async def test_sidebar_lists_models_without_logs_link(app_no_logs: FastAPI) -> None:
    async with _client(app_no_logs) as client:
        await _login(client)
        response = await client.get("/admin/")
    assert response.status_code == 200
    assert "tempest-admin-sidebar" in response.text
    assert "tempest-admin-burger" in response.text  # mobile toggle present
    assert "Gadgets" in response.text or "Admin Nav Gadget" in response.text
    # Logs disabled -> no logs nav entry and no /admin/logs route.
    assert "/admin/logs" not in response.text


@pytest.mark.asyncio
async def test_logs_link_shown_when_enabled(
    app_with_logs: tuple[FastAPI, Path],
) -> None:
    app, _log_dir = app_with_logs
    async with _client(app) as client:
        await _login(client)
        response = await client.get("/admin/")
    assert response.status_code == 200
    assert "/admin/logs" in response.text


@pytest.mark.asyncio
async def test_logs_page_renders_entries(
    app_with_logs: tuple[FastAPI, Path],
) -> None:
    app, _log_dir = app_with_logs
    async with _client(app) as client:
        await _login(client)
        response = await client.get("/admin/logs")
    assert response.status_code == 200
    assert "order placed alpha" in response.text
    assert "order placed beta" in response.text
    assert "app.orders" in response.text


@pytest.mark.asyncio
async def test_logs_page_search_filters(
    app_with_logs: tuple[FastAPI, Path],
) -> None:
    app, _log_dir = app_with_logs
    async with _client(app) as client:
        await _login(client)
        response = await client.get("/admin/logs?q=alpha")
    assert response.status_code == 200
    assert "order placed alpha" in response.text
    assert "order placed beta" not in response.text


@pytest.mark.asyncio
async def test_logs_requires_login(
    app_with_logs: tuple[FastAPI, Path],
) -> None:
    app, _log_dir = app_with_logs
    async with _client(app) as client:
        response = await client.get("/admin/logs", follow_redirects=False)
    assert response.status_code == 303
    assert response.headers["location"] == "/admin/login"


@pytest.mark.asyncio
async def test_logs_page_empty_state_when_no_files(tmp_path: Path) -> None:
    app, db = await _build_app(show_logs=True, log_dir=tmp_path / "absent")
    try:
        async with _client(app) as client:
            await _login(client)
            response = await client.get("/admin/logs")
        assert response.status_code == 200
        assert "No log files found" in response.text
    finally:
        await db.drop_tables()
        await db.disconnect()


@pytest.mark.asyncio
async def test_logs_route_absent_when_disabled(app_no_logs: FastAPI) -> None:
    async with _client(app_no_logs) as client:
        await _login(client)
        response = await client.get("/admin/logs", follow_redirects=False)
    assert response.status_code == 404
