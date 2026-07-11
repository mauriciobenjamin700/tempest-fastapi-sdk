"""End-to-end tests for admin CSV import."""

from __future__ import annotations

import re
from collections.abc import AsyncIterator

import pytest
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient
from sqlalchemy import Integer, String
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


class ImportUser(BaseUserModel):
    __tablename__ = "admin_import_users"


class Gadget(BaseModel):
    __tablename__ = "admin_import_gadget"
    name: Mapped[str] = mapped_column(String(64), nullable=False)
    qty: Mapped[int] = mapped_column(Integer, nullable=False, default=0)


SECRET = "x" * 48
_SLUG = Gadget.__tablename__


@pytest.fixture
async def app_import() -> AsyncIterator[tuple[FastAPI, AsyncDatabaseManager]]:
    db = AsyncDatabaseManager("sqlite+aiosqlite:///:memory:")
    await db.connect()
    await db.create_tables()
    async with db.get_session_context() as session:
        user = ImportUser(email="root@example.com", hashed_password="", is_admin=True)
        user.set_password("hunter2")
        session.add(user)
        await session.commit()

    site = AdminSite(title="Import Admin")
    site.register(AdminModel(model=Gadget, can_import=True))
    app = FastAPI()
    app.include_router(
        make_admin_router(
            site,
            db=db,
            auth_backend=UserModelAuthBackend(ImportUser),
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
        "/admin/login",
        data={"identifier": "root@example.com", "password": "hunter2"},
    )
    page = await client.get(f"/admin/m/{_SLUG}/import")
    match = re.search(r'name="csrf_token" value="([^"]+)"', page.text)
    assert match is not None
    return match.group(1)


@pytest.mark.asyncio
async def test_import_page_renders(
    app_import: tuple[FastAPI, AsyncDatabaseManager],
) -> None:
    app, _db = app_import
    async with _client(app) as client:
        await client.post(
            "/admin/login",
            data={"identifier": "root@example.com", "password": "hunter2"},
        )
        page = await client.get(f"/admin/m/{_SLUG}/import")
    assert page.status_code == 200
    assert "Expected header columns" in page.text
    assert "name" in page.text


@pytest.mark.asyncio
async def test_import_creates_rows(
    app_import: tuple[FastAPI, AsyncDatabaseManager],
) -> None:
    app, db = app_import
    csv_body = b"name,qty\nAlpha,3\nBeta,7\n"
    async with _client(app) as client:
        token = await _login_csrf(client)
        resp = await client.post(
            f"/admin/m/{_SLUG}/import",
            data={"csrf_token": token},
            files={"file": ("gadgets.csv", csv_body, "text/csv")},
        )
    assert resp.status_code == 200
    assert "Created 2 record(s)." in resp.text

    async with db.get_session_context() as session:
        repo: BaseRepository[Gadget] = BaseRepository(session, model=Gadget)
        rows = await repo.list(order_by=Gadget.name)
    assert {r.name for r in rows} == {"Alpha", "Beta"}
    assert {r.qty for r in rows} == {3, 7}


@pytest.mark.asyncio
async def test_import_reports_bad_rows(
    app_import: tuple[FastAPI, AsyncDatabaseManager],
) -> None:
    app, db = app_import
    # Row 3 has an empty required name; row 2 is valid.
    csv_body = b"name,qty\nGood,1\n,2\n"
    async with _client(app) as client:
        token = await _login_csrf(client)
        resp = await client.post(
            f"/admin/m/{_SLUG}/import",
            data={"csrf_token": token},
            files={"file": ("gadgets.csv", csv_body, "text/csv")},
        )
    assert resp.status_code == 200
    assert "Created 1 record(s)." in resp.text
    assert "1 row(s) skipped" in resp.text

    async with db.get_session_context() as session:
        repo: BaseRepository[Gadget] = BaseRepository(session, model=Gadget)
        rows = await repo.list()
    assert [r.name for r in rows] == ["Good"]


@pytest.mark.asyncio
async def test_import_disabled_404(
    app_import: tuple[FastAPI, AsyncDatabaseManager],
) -> None:
    # A fresh site where the model has no can_import.
    db = AsyncDatabaseManager("sqlite+aiosqlite:///:memory:")
    await db.connect()
    await db.create_tables()
    async with db.get_session_context() as session:
        user = ImportUser(email="a@b.com", hashed_password="", is_admin=True)
        user.set_password("pw")
        session.add(user)
        await session.commit()
    site = AdminSite(title="No Import")
    site.register(AdminModel(model=Gadget))  # can_import defaults False
    app = FastAPI()
    app.include_router(
        make_admin_router(
            site,
            db=db,
            auth_backend=UserModelAuthBackend(ImportUser),
            secret_key=SECRET,
            cookie_secure=False,
        )
    )
    async with _client(app) as client:
        await client.post(
            "/admin/login", data={"identifier": "a@b.com", "password": "pw"}
        )
        resp = await client.get(f"/admin/m/{_SLUG}/import")
    assert resp.status_code == 404
    await db.drop_tables()
    await db.disconnect()
