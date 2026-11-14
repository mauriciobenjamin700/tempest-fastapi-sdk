"""AdminModel(readonly_fields_for=...): per-row locked columns, render and save."""

from __future__ import annotations

import re
from collections.abc import AsyncIterator, Sequence
from dataclasses import dataclass
from typing import Any
from uuid import UUID

import pytest
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient
from sqlalchemy import String, select
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
from tempest_fastapi_sdk.testing import admin_login

SECRET: str = "x" * 48
"""Admin session secret."""


class _LockUser(BaseUserModel):
    __tablename__ = "readonly_for_users"


class _Ticket(BaseModel):
    """A row whose note freezes once it is closed."""

    __tablename__ = "readonly_for_tickets"

    status: Mapped[str] = mapped_column(String(16), nullable=False)
    note: Mapped[str | None] = mapped_column(String(128), nullable=True)
    title: Mapped[str] = mapped_column(String(64), nullable=False)


_SLUG: str = "readonly_for_tickets"


def _note_locked_when_closed(row: Any) -> Sequence[str]:
    """Lock ``note`` on a closed ticket.

    Args:
        row (Any): The ticket.

    Returns:
        Sequence[str]: ``["note"]`` when closed; empty otherwise.
    """
    return ["note"] if row.status == "closed" else []


@dataclass
class _Panel:
    client: AsyncClient
    db: AsyncDatabaseManager


@pytest.fixture
async def panel() -> AsyncIterator[_Panel]:
    """A panel with the ticket admin and a signed-in administrator.

    Yields:
        _Panel: Client and database.
    """
    db = AsyncDatabaseManager("sqlite+aiosqlite:///:memory:")
    await db.connect()
    await db.create_tables()
    async with db.get_session_context() as session:
        user = _LockUser(email="root@example.com", hashed_password="", is_admin=True)
        user.set_password("hunter2")
        session.add(user)
        await session.commit()
    site = AdminSite(title="Admin")
    site.register(
        AdminModel(
            _Ticket,
            readonly_fields=["status"],
            readonly_fields_for=_note_locked_when_closed,
        )
    )
    app = FastAPI()
    app.include_router(
        make_admin_router(
            site,
            db=db,
            auth_backend=UserModelAuthBackend(_LockUser),
            secret_key=SECRET,
            cookie_secure=False,
        )
    )
    async with AsyncClient(
        transport=ASGITransport(app=app), base_url="http://test"
    ) as client:
        await admin_login(client, "root@example.com", "hunter2")
        yield _Panel(client=client, db=db)
    await db.drop_tables()
    await db.disconnect()


async def _ticket(p: _Panel, status: str) -> str:
    async with p.db.get_session_context() as session:
        row = _Ticket(status=status, note="original", title="t")
        session.add(row)
        await session.commit()
        return str(row.id)


async def _stored(p: _Panel, row_id: str) -> Any:
    async with p.db.get_session_context() as session:
        return (
            await session.execute(select(_Ticket).where(_Ticket.id == UUID(row_id)))
        ).scalar_one()


async def _post(p: _Panel, row_id: str, data: dict[str, str]) -> int:
    url = f"/admin/m/{_SLUG}/{row_id}/edit"
    page = await p.client.get(url)
    match = re.search(r'name="csrf_token" value="([^"]+)"', page.text)
    assert match is not None
    response = await p.client.post(
        url, data={"csrf_token": match.group(1), **data}, follow_redirects=False
    )
    return response.status_code


async def test_open_row_renders_the_field(panel: _Panel) -> None:
    row_id = await _ticket(panel, "open")
    page = await panel.client.get(f"/admin/m/{_SLUG}/{row_id}/edit")
    assert 'name="note"' in page.text
    assert 'name="title"' in page.text


async def test_closed_row_leaves_the_field_out(panel: _Panel) -> None:
    row_id = await _ticket(panel, "closed")
    page = await panel.client.get(f"/admin/m/{_SLUG}/{row_id}/edit")
    assert page.status_code == 200
    assert 'name="note"' not in page.text
    assert 'name="title"' in page.text


async def test_open_row_saves_the_field(panel: _Panel) -> None:
    row_id = await _ticket(panel, "open")
    assert await _post(panel, row_id, {"note": "changed", "title": "t2"}) == 303
    row = await _stored(panel, row_id)
    assert row.note == "changed"
    assert row.title == "t2"


async def test_tampered_post_on_a_closed_row_keeps_the_value(panel: _Panel) -> None:
    row_id = await _ticket(panel, "closed")
    assert await _post(panel, row_id, {"note": "forged", "title": "t2"}) == 303
    row = await _stored(panel, row_id)
    assert row.note == "original"
    assert row.title == "t2"


async def test_error_rerender_keeps_the_field_out(panel: _Panel) -> None:
    row_id = await _ticket(panel, "closed")
    url = f"/admin/m/{_SLUG}/{row_id}/edit"
    page = await panel.client.get(url)
    match = re.search(r'name="csrf_token" value="([^"]+)"', page.text)
    assert match is not None
    response = await panel.client.post(
        url,
        data={"csrf_token": match.group(1), "title": ""},
        follow_redirects=False,
    )
    assert response.status_code == 400
    assert 'name="note"' not in response.text


def test_static_and_per_row_fields_merge() -> None:
    admin = AdminModel(
        _Ticket,
        readonly_fields=["status"],
        readonly_fields_for=_note_locked_when_closed,
    )
    closed = _Ticket(status="closed", note=None, title="t")
    opened = _Ticket(status="open", note=None, title="t")
    assert admin.readonly_field_names_for(closed) == {"status", "note"}
    assert admin.readonly_field_names_for(opened) == {"status"}
    assert "note" not in admin.editable_field_names(instance=closed)
    assert "note" in admin.editable_field_names(instance=opened)
    assert "note" in admin.editable_field_names()
