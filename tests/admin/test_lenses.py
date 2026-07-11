"""End-to-end tests for admin lenses (saved list-view presets)."""

from __future__ import annotations

from collections.abc import AsyncIterator

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
    Lens,
    UserModelAuthBackend,
    make_admin_router,
)


class LensUser(BaseUserModel):
    __tablename__ = "admin_lens_users"


class Ticket(BaseModel):
    __tablename__ = "admin_lens_ticket"
    name: Mapped[str] = mapped_column(String(64), nullable=False)
    status: Mapped[str] = mapped_column(String(32), nullable=False)


SECRET = "x" * 48
_SLUG = Ticket.__tablename__


@pytest.fixture
async def app_lens() -> AsyncIterator[FastAPI]:
    db = AsyncDatabaseManager("sqlite+aiosqlite:///:memory:")
    await db.connect()
    await db.create_tables()
    async with db.get_session_context() as session:
        user = LensUser(email="root@x.com", hashed_password="", is_admin=True)
        user.set_password("pw")
        session.add(user)
        session.add_all(
            [
                Ticket(name="alpha", status="open"),
                Ticket(name="bravo", status="open"),
                Ticket(name="charlie", status="closed"),
            ]
        )
        await session.commit()

    site = AdminSite(title="Lens Admin")
    site.register(
        AdminModel(
            model=Ticket,
            lenses=[
                Lens("Open", filters={"status": "open"}),
                Lens("Closed", filters={"status": "closed"}, order_by="-created_at"),
            ],
        )
    )
    app = FastAPI()
    app.include_router(
        make_admin_router(
            site,
            db=db,
            auth_backend=UserModelAuthBackend(LensUser),
            secret_key=SECRET,
            cookie_secure=False,
        )
    )
    yield app
    await db.drop_tables()
    await db.disconnect()


def _client(app: FastAPI) -> AsyncClient:
    return AsyncClient(transport=ASGITransport(app=app), base_url="http://test")


async def _login(client: AsyncClient) -> None:
    await client.post(
        "/admin/login", data={"identifier": "root@x.com", "password": "pw"}
    )


@pytest.mark.asyncio
async def test_no_lens_shows_all(app_lens: FastAPI) -> None:
    async with _client(app_lens) as client:
        await _login(client)
        resp = await client.get(f"/admin/m/{_SLUG}/")
    assert resp.status_code == 200
    body = resp.text
    assert "alpha" in body and "bravo" in body and "charlie" in body
    # Tabs rendered.
    assert "tempest-admin-lenses" in body
    assert ">Open<" in body
    assert ">Closed<" in body


@pytest.mark.asyncio
async def test_lens_filters_rows(app_lens: FastAPI) -> None:
    async with _client(app_lens) as client:
        await _login(client)
        resp = await client.get(f"/admin/m/{_SLUG}/?lens=open")
    assert resp.status_code == 200
    body = resp.text
    assert "alpha" in body and "bravo" in body
    assert "charlie" not in body
    # The Open tab is marked active.
    assert "tempest-admin-lens--active" in body


@pytest.mark.asyncio
async def test_closed_lens(app_lens: FastAPI) -> None:
    async with _client(app_lens) as client:
        await _login(client)
        resp = await client.get(f"/admin/m/{_SLUG}/?lens=closed")
    body = resp.text
    assert "charlie" in body
    assert "alpha" not in body and "bravo" not in body


@pytest.mark.asyncio
async def test_unknown_lens_shows_all(app_lens: FastAPI) -> None:
    async with _client(app_lens) as client:
        await _login(client)
        resp = await client.get(f"/admin/m/{_SLUG}/?lens=nope")
    assert resp.status_code == 200
    body = resp.text
    assert "alpha" in body and "charlie" in body


def test_lens_slug() -> None:
    assert Lens("Open Tickets").slug() == "open-tickets"
    assert Lens("High-Priority!").slug() == "high-priority"
