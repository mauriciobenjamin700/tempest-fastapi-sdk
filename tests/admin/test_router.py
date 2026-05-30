"""End-to-end tests for the admin HTML router."""

from __future__ import annotations

from typing import ClassVar

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


class RouterUser(BaseUserModel):
    __tablename__ = "admin_router_users"


class WidgetModel(BaseModel):
    __tablename__ = "admin_router_widget"
    name: Mapped[str] = mapped_column(String(64), nullable=False)


class WidgetAdmin(AdminModel[WidgetModel]):
    model = WidgetModel
    list_display: ClassVar[list[str]] = ["id", "name", "is_active"]
    list_filter: ClassVar[list[str]] = ["is_active"]
    search_fields: ClassVar[list[str]] = ["name"]


SECRET = "x" * 48


@pytest.fixture
async def app_with_admin() -> tuple[FastAPI, AsyncDatabaseManager, RouterUser]:
    db = AsyncDatabaseManager("sqlite+aiosqlite:///:memory:")
    await db.connect()
    await db.create_tables()

    async with db.get_session_context() as session:
        user = RouterUser(email="root@example.com", hashed_password="", is_admin=True)
        user.set_password("hunter2")
        session.add(user)
        session.add_all(
            [WidgetModel(name=f"widget-{i}") for i in range(3)],
        )
        await session.commit()
        await session.refresh(user)

    site = AdminSite(title="Test Admin")
    site.register(WidgetAdmin)
    backend = UserModelAuthBackend(RouterUser)

    app = FastAPI()
    app.include_router(
        make_admin_router(
            site,
            db=db,
            auth_backend=backend,
            secret_key=SECRET,
            cookie_secure=False,
        )
    )
    yield app, db, user
    await db.drop_tables()
    await db.disconnect()


def _client(app: FastAPI) -> AsyncClient:
    return AsyncClient(transport=ASGITransport(app=app), base_url="http://test")


@pytest.mark.asyncio
async def test_login_page_renders(
    app_with_admin: tuple[FastAPI, AsyncDatabaseManager, RouterUser],
) -> None:
    app, _db, _user = app_with_admin
    async with _client(app) as client:
        response = await client.get("/admin/login")
    assert response.status_code == 200
    assert "Sign in" in response.text


@pytest.mark.asyncio
async def test_dashboard_requires_login(
    app_with_admin: tuple[FastAPI, AsyncDatabaseManager, RouterUser],
) -> None:
    app, _db, _user = app_with_admin
    async with _client(app) as client:
        response = await client.get("/admin/", follow_redirects=False)
    assert response.status_code == 303
    assert response.headers["location"] == "/admin/login"


@pytest.mark.asyncio
async def test_login_then_dashboard(
    app_with_admin: tuple[FastAPI, AsyncDatabaseManager, RouterUser],
) -> None:
    app, _db, _user = app_with_admin
    async with _client(app) as client:
        login = await client.post(
            "/admin/login",
            data={"identifier": "root@example.com", "password": "hunter2"},
            follow_redirects=False,
        )
        assert login.status_code == 303
        assert "tempest_admin" in login.headers.get("set-cookie", "")

        dashboard = await client.get("/admin/")
    assert dashboard.status_code == 200
    assert "Test Admin" in dashboard.text
    assert "Widgets" in dashboard.text or "Admin Router Widgets" in dashboard.text


@pytest.mark.asyncio
async def test_login_wrong_password_returns_401(
    app_with_admin: tuple[FastAPI, AsyncDatabaseManager, RouterUser],
) -> None:
    app, _db, _user = app_with_admin
    async with _client(app) as client:
        response = await client.post(
            "/admin/login",
            data={"identifier": "root@example.com", "password": "WRONG"},
        )
    assert response.status_code == 401
    assert "Invalid credentials" in response.text


@pytest.mark.asyncio
async def test_list_view_paginates(
    app_with_admin: tuple[FastAPI, AsyncDatabaseManager, RouterUser],
) -> None:
    app, _db, _user = app_with_admin
    async with _client(app) as client:
        await client.post(
            "/admin/login",
            data={"identifier": "root@example.com", "password": "hunter2"},
        )
        response = await client.get(f"/admin/m/{WidgetModel.__tablename__}/")
    assert response.status_code == 200
    assert "widget-0" in response.text
    assert "3 record" in response.text or "3 records" in response.text


@pytest.mark.asyncio
async def test_list_view_search(
    app_with_admin: tuple[FastAPI, AsyncDatabaseManager, RouterUser],
) -> None:
    app, _db, _user = app_with_admin
    async with _client(app) as client:
        await client.post(
            "/admin/login",
            data={"identifier": "root@example.com", "password": "hunter2"},
        )
        response = await client.get(f"/admin/m/{WidgetModel.__tablename__}/?q=widget-1")
    assert response.status_code == 200
    assert "widget-1" in response.text


@pytest.mark.asyncio
async def test_detail_view(
    app_with_admin: tuple[FastAPI, AsyncDatabaseManager, RouterUser],
) -> None:
    app, db, _user = app_with_admin
    async with db.get_session_context() as session:
        from sqlalchemy import select

        widget = (await session.execute(select(WidgetModel).limit(1))).scalar_one()
        widget_id = str(widget.id)

    async with _client(app) as client:
        await client.post(
            "/admin/login",
            data={"identifier": "root@example.com", "password": "hunter2"},
        )
        response = await client.get(f"/admin/m/{WidgetModel.__tablename__}/{widget_id}")
    assert response.status_code == 200
    assert widget_id in response.text


@pytest.mark.asyncio
async def test_detail_view_404(
    app_with_admin: tuple[FastAPI, AsyncDatabaseManager, RouterUser],
) -> None:
    from uuid import uuid4

    app, _db, _user = app_with_admin
    async with _client(app) as client:
        await client.post(
            "/admin/login",
            data={"identifier": "root@example.com", "password": "hunter2"},
        )
        response = await client.get(f"/admin/m/{WidgetModel.__tablename__}/{uuid4()}")
    assert response.status_code == 404


@pytest.mark.asyncio
async def test_static_css_served(
    app_with_admin: tuple[FastAPI, AsyncDatabaseManager, RouterUser],
) -> None:
    app, _db, _user = app_with_admin
    async with _client(app) as client:
        response = await client.get("/admin/static/admin.css")
    assert response.status_code == 200
    assert "tempest-admin" in response.text
