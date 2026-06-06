"""End-to-end tests for the admin HTML router."""

from __future__ import annotations

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


widget_admin = AdminModel(
    model=WidgetModel,
    list_display=[WidgetModel.id, WidgetModel.name, WidgetModel.is_active],
    list_filter=[WidgetModel.is_active],
    search_fields=[WidgetModel.name],
)


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
    site.register(widget_admin)
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


# --- Sortable columns ---------------------------------------------------------


async def _login(client: AsyncClient) -> None:
    await client.post(
        "/admin/login",
        data={"identifier": "root@example.com", "password": "hunter2"},
    )


@pytest.mark.asyncio
async def test_list_renders_sort_links(
    app_with_admin: tuple[FastAPI, AsyncDatabaseManager, RouterUser],
) -> None:
    app, _db, _user = app_with_admin
    async with _client(app) as client:
        await _login(client)
        response = await client.get(f"/admin/m/{WidgetModel.__tablename__}/")
    assert response.status_code == 200
    # Sortable header link for the `name` column is present.
    assert "sort=name" in response.text
    assert "tempest-sort" in response.text


@pytest.mark.asyncio
async def test_list_sort_by_name_desc_orders_rows(
    app_with_admin: tuple[FastAPI, AsyncDatabaseManager, RouterUser],
) -> None:
    app, _db, _user = app_with_admin
    async with _client(app) as client:
        await _login(client)
        response = await client.get(
            f"/admin/m/{WidgetModel.__tablename__}/?sort=name&dir=desc"
        )
    assert response.status_code == 200
    # Descending: widget-2 must appear before widget-0 in the markup.
    assert response.text.index("widget-2") < response.text.index("widget-0")
    # Active descending arrow rendered.
    assert "▼" in response.text


@pytest.mark.asyncio
async def test_list_sort_unknown_column_ignored(
    app_with_admin: tuple[FastAPI, AsyncDatabaseManager, RouterUser],
) -> None:
    app, _db, _user = app_with_admin
    async with _client(app) as client:
        await _login(client)
        # A non-existent / non-sortable column must not break the page.
        response = await client.get(
            f"/admin/m/{WidgetModel.__tablename__}/?sort=__nope__&dir=desc"
        )
    assert response.status_code == 200
    assert "widget-0" in response.text


# --- CSV / JSON export --------------------------------------------------------


@pytest.mark.asyncio
async def test_export_csv(
    app_with_admin: tuple[FastAPI, AsyncDatabaseManager, RouterUser],
) -> None:
    app, _db, _user = app_with_admin
    async with _client(app) as client:
        await _login(client)
        response = await client.get(f"/admin/m/{WidgetModel.__tablename__}/export.csv")
    assert response.status_code == 200
    assert response.headers["content-type"].startswith("text/csv")
    assert "attachment" in response.headers["content-disposition"]
    lines = response.text.strip().splitlines()
    assert lines[0].split(",") == ["id", "name", "is_active"]
    assert len(lines) == 1 + 3  # header + 3 widgets
    assert any("widget-0" in line for line in lines[1:])


@pytest.mark.asyncio
async def test_export_json(
    app_with_admin: tuple[FastAPI, AsyncDatabaseManager, RouterUser],
) -> None:
    import json

    app, _db, _user = app_with_admin
    async with _client(app) as client:
        await _login(client)
        response = await client.get(f"/admin/m/{WidgetModel.__tablename__}/export.json")
    assert response.status_code == 200
    assert response.headers["content-type"].startswith("application/json")
    data = json.loads(response.text)
    assert isinstance(data, list)
    assert len(data) == 3
    assert {row["name"] for row in data} == {"widget-0", "widget-1", "widget-2"}
    # bool column stays JSON-native, not stringified.
    assert data[0]["is_active"] is True


@pytest.mark.asyncio
async def test_export_respects_search(
    app_with_admin: tuple[FastAPI, AsyncDatabaseManager, RouterUser],
) -> None:
    app, _db, _user = app_with_admin
    async with _client(app) as client:
        await _login(client)
        response = await client.get(
            f"/admin/m/{WidgetModel.__tablename__}/export.csv?q=widget-1"
        )
    assert response.status_code == 200
    lines = response.text.strip().splitlines()
    assert len(lines) == 1 + 1  # header + only widget-1
    assert "widget-1" in lines[1]


@pytest.mark.asyncio
async def test_export_unsupported_format_404(
    app_with_admin: tuple[FastAPI, AsyncDatabaseManager, RouterUser],
) -> None:
    app, _db, _user = app_with_admin
    async with _client(app) as client:
        await _login(client)
        response = await client.get(f"/admin/m/{WidgetModel.__tablename__}/export.xml")
    assert response.status_code == 404


@pytest.mark.asyncio
async def test_export_requires_login(
    app_with_admin: tuple[FastAPI, AsyncDatabaseManager, RouterUser],
) -> None:
    app, _db, _user = app_with_admin
    async with _client(app) as client:
        response = await client.get(
            f"/admin/m/{WidgetModel.__tablename__}/export.csv",
            follow_redirects=False,
        )
    assert response.status_code == 303
    assert response.headers["location"] == "/admin/login"


# --- Responsive markup --------------------------------------------------------


@pytest.mark.asyncio
async def test_list_has_responsive_table_wrapper(
    app_with_admin: tuple[FastAPI, AsyncDatabaseManager, RouterUser],
) -> None:
    app, _db, _user = app_with_admin
    async with _client(app) as client:
        await _login(client)
        response = await client.get(f"/admin/m/{WidgetModel.__tablename__}/")
    assert response.status_code == 200
    assert "tempest-admin-table-wrap" in response.text
    # viewport meta is what makes the responsive CSS apply on mobile.
    assert "width=device-width" in response.text


@pytest.mark.asyncio
async def test_css_ships_media_queries(
    app_with_admin: tuple[FastAPI, AsyncDatabaseManager, RouterUser],
) -> None:
    app, _db, _user = app_with_admin
    async with _client(app) as client:
        response = await client.get("/admin/static/admin.css")
    assert response.status_code == 200
    assert "@media (max-width: 600px)" in response.text
