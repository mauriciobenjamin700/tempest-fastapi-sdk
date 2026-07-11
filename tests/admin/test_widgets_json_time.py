"""End-to-end tests for the admin JSON + time field widgets."""

from __future__ import annotations

import datetime as dt
import re
from collections.abc import AsyncIterator
from typing import Any

import pytest
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient
from sqlalchemy import JSON, String, Time
from sqlalchemy.orm import Mapped, mapped_column

from tempest_fastapi_sdk import (
    AdminModel,
    AdminSite,
    AsyncDatabaseManager,
    BaseModel,
    BaseRepository,
    BaseUserModel,
    UserModelAuthBackend,
    make_admin_router,
)


class WidgetUser(BaseUserModel):
    __tablename__ = "admin_widget_users"


class Event(BaseModel):
    __tablename__ = "admin_widget_event"
    name: Mapped[str] = mapped_column(String(64), nullable=False)
    payload: Mapped[dict[str, Any] | None] = mapped_column(JSON, nullable=True)
    start_at: Mapped[dt.time | None] = mapped_column(Time, nullable=True)


SECRET = "x" * 48
_SLUG = Event.__tablename__


@pytest.fixture
async def app_widgets() -> AsyncIterator[tuple[FastAPI, AsyncDatabaseManager]]:
    db = AsyncDatabaseManager("sqlite+aiosqlite:///:memory:")
    await db.connect()
    await db.create_tables()
    async with db.get_session_context() as session:
        user = WidgetUser(email="root@x.com", hashed_password="", is_admin=True)
        user.set_password("pw")
        session.add(user)
        await session.commit()

    site = AdminSite(title="Widget Admin")
    site.register(AdminModel(model=Event))
    app = FastAPI()
    app.include_router(
        make_admin_router(
            site,
            db=db,
            auth_backend=UserModelAuthBackend(WidgetUser),
            secret_key=SECRET,
            cookie_secure=False,
        )
    )
    yield app, db
    await db.drop_tables()
    await db.disconnect()


def _client(app: FastAPI) -> AsyncClient:
    return AsyncClient(transport=ASGITransport(app=app), base_url="http://test")


async def _login_csrf(client: AsyncClient) -> str:
    await client.post(
        "/admin/login", data={"identifier": "root@x.com", "password": "pw"}
    )
    page = await client.get(f"/admin/m/{_SLUG}/new")
    match = re.search(r'name="csrf_token" value="([^"]+)"', page.text)
    assert match is not None
    return match.group(1)


@pytest.mark.asyncio
async def test_new_form_renders_json_and_time(
    app_widgets: tuple[FastAPI, AsyncDatabaseManager],
) -> None:
    app, _ = app_widgets
    async with _client(app) as client:
        await client.post(
            "/admin/login", data={"identifier": "root@x.com", "password": "pw"}
        )
        page = await client.get(f"/admin/m/{_SLUG}/new")
    assert page.status_code == 200
    assert "tempest-admin-form__json" in page.text
    assert 'type="time"' in page.text


@pytest.mark.asyncio
async def test_create_parses_json_and_time(
    app_widgets: tuple[FastAPI, AsyncDatabaseManager],
) -> None:
    app, db = app_widgets
    async with _client(app) as client:
        token = await _login_csrf(client)
        resp = await client.post(
            f"/admin/m/{_SLUG}/new",
            data={
                "csrf_token": token,
                "name": "launch",
                "payload": '{"tier": "gold", "seats": 3}',
                "start_at": "09:30",
            },
            follow_redirects=False,
        )
    assert resp.status_code == 303

    async with db.get_session_context() as session:
        repo: BaseRepository[Event] = BaseRepository(session, model=Event)
        row = await repo.first({"name": "launch"})
    assert row is not None
    # JSON stored as a real dict, not a string.
    assert row.payload == {"tier": "gold", "seats": 3}
    assert row.start_at == dt.time(9, 30)


@pytest.mark.asyncio
async def test_invalid_json_rejected(
    app_widgets: tuple[FastAPI, AsyncDatabaseManager],
) -> None:
    app, db = app_widgets
    async with _client(app) as client:
        token = await _login_csrf(client)
        resp = await client.post(
            f"/admin/m/{_SLUG}/new",
            data={
                "csrf_token": token,
                "name": "bad",
                "payload": "{not json}",
            },
            follow_redirects=False,
        )
    assert resp.status_code == 400
    assert "Invalid JSON" in resp.text

    async with db.get_session_context() as session:
        repo: BaseRepository[Event] = BaseRepository(session, model=Event)
        assert await repo.count({"name": "bad"}) == 0


@pytest.mark.asyncio
async def test_edit_prefills_pretty_json_and_time(
    app_widgets: tuple[FastAPI, AsyncDatabaseManager],
) -> None:
    app, db = app_widgets
    async with db.get_session_context() as session:
        repo: BaseRepository[Event] = BaseRepository(session, model=Event)
        row = await repo.add(Event(name="e", payload={"a": 1}, start_at=dt.time(14, 5)))
        row_id = str(row.id)

    async with _client(app) as client:
        await client.post(
            "/admin/login", data={"identifier": "root@x.com", "password": "pw"}
        )
        page = await client.get(f"/admin/m/{_SLUG}/{row_id}/edit")
    body = page.text
    # JSON pretty-printed (indented) in the textarea — the quotes are
    # HTML-escaped by the template (`"` → `&#34;`).
    assert "&#34;a&#34;: 1" in body
    assert 'value="14:05"' in body


@pytest.mark.asyncio
async def test_detail_pretty_prints_json(
    app_widgets: tuple[FastAPI, AsyncDatabaseManager],
) -> None:
    app, db = app_widgets
    async with db.get_session_context() as session:
        repo: BaseRepository[Event] = BaseRepository(session, model=Event)
        row = await repo.add(Event(name="e", payload={"z": 9}))
        row_id = str(row.id)

    async with _client(app) as client:
        await client.post(
            "/admin/login", data={"identifier": "root@x.com", "password": "pw"}
        )
        page = await client.get(f"/admin/m/{_SLUG}/{row_id}")
    body = page.text
    # Detail renders JSON in a monospaced <pre>, pretty-printed.
    assert "tempest-admin-detail__json" in body
    assert "&#34;z&#34;: 9" in body
