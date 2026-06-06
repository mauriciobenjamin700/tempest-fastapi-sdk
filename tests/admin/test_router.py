"""End-to-end tests for the admin HTML router."""

from __future__ import annotations

from uuid import UUID as _UUID

import pytest
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient
from sqlalchemy import ForeignKey as _ForeignKey
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


# --- Dashboard (counts + metrics) --------------------------------------------


@pytest.mark.asyncio
async def test_dashboard_shows_model_counts_and_metrics(
    app_with_admin: tuple[FastAPI, AsyncDatabaseManager, RouterUser],
) -> None:
    app, _db, _user = app_with_admin
    async with _client(app) as client:
        await client.post(
            "/admin/login",
            data={"identifier": "root@example.com", "password": "hunter2"},
        )
        dash = await client.get("/admin/")
    assert dash.status_code == 200
    # per-model count badge (3 widgets seeded)
    assert "tempest-admin-model-card__count" in dash.text
    assert ">3<" in dash.text
    # metrics panel on by default ([metrics]/psutil is a dev dep)
    assert "CPU" in dash.text


@pytest.mark.asyncio
async def test_dashboard_metrics_can_be_disabled() -> None:
    db = AsyncDatabaseManager("sqlite+aiosqlite:///:memory:")
    await db.connect()
    await db.create_tables()
    async with db.get_session_context() as session:
        user = RouterUser(email="nm@example.com", hashed_password="", is_admin=True)
        user.set_password("hunter2")
        session.add(user)
        await session.commit()

    site = AdminSite(title="No-Metrics Admin")
    site.register(AdminModel(model=WidgetModel, list_display=[WidgetModel.name]))
    app = FastAPI()
    app.include_router(
        make_admin_router(
            site,
            db=db,
            auth_backend=UserModelAuthBackend(RouterUser),
            secret_key=SECRET,
            cookie_secure=False,
            show_metrics=False,
        )
    )
    try:
        async with _client(app) as client:
            await client.post(
                "/admin/login",
                data={"identifier": "nm@example.com", "password": "hunter2"},
            )
            dash = await client.get("/admin/")
        assert dash.status_code == 200
        assert "System metrics" not in dash.text
    finally:
        await db.drop_tables()
        await db.disconnect()


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


# --- Write CRUD (create / edit / delete) --------------------------------------


def _csrf_from(html: str) -> str:
    import re

    match = re.search(r'name="csrf_token" value="([^"]+)"', html)
    assert match is not None, "csrf token not found in form"
    return match.group(1)


_SLUG = WidgetModel.__tablename__


@pytest.mark.asyncio
async def test_create_form_renders(
    app_with_admin: tuple[FastAPI, AsyncDatabaseManager, RouterUser],
) -> None:
    app, _db, _user = app_with_admin
    async with _client(app) as client:
        await _login(client)
        response = await client.get(f"/admin/m/{_SLUG}/new")
    assert response.status_code == 200
    assert 'name="name"' in response.text
    assert 'name="csrf_token"' in response.text
    # auto/PK columns are not part of the form.
    assert 'name="id"' not in response.text
    assert 'name="created_at"' not in response.text


@pytest.mark.asyncio
async def test_create_persists_row(
    app_with_admin: tuple[FastAPI, AsyncDatabaseManager, RouterUser],
) -> None:
    app, _db, _user = app_with_admin
    async with _client(app) as client:
        await _login(client)
        form = await client.get(f"/admin/m/{_SLUG}/new")
        token = _csrf_from(form.text)
        created = await client.post(
            f"/admin/m/{_SLUG}/new",
            data={"csrf_token": token, "name": "Fresh Widget", "is_active": "true"},
            follow_redirects=False,
        )
        assert created.status_code == 303
        listing = await client.get(f"/admin/m/{_SLUG}/?q=Fresh Widget")
    assert "Fresh Widget" in listing.text


@pytest.mark.asyncio
async def test_create_missing_required_rerenders_400(
    app_with_admin: tuple[FastAPI, AsyncDatabaseManager, RouterUser],
) -> None:
    app, _db, _user = app_with_admin
    async with _client(app) as client:
        await _login(client)
        form = await client.get(f"/admin/m/{_SLUG}/new")
        token = _csrf_from(form.text)
        response = await client.post(
            f"/admin/m/{_SLUG}/new",
            data={"csrf_token": token, "name": ""},
        )
    assert response.status_code == 400
    assert "required" in response.text.lower()


@pytest.mark.asyncio
async def test_create_csrf_mismatch_403(
    app_with_admin: tuple[FastAPI, AsyncDatabaseManager, RouterUser],
) -> None:
    app, _db, _user = app_with_admin
    async with _client(app) as client:
        await _login(client)
        response = await client.post(
            f"/admin/m/{_SLUG}/new",
            data={"csrf_token": "bogus", "name": "X"},
        )
    assert response.status_code == 403


async def _first_widget_id(db: AsyncDatabaseManager) -> str:
    from sqlalchemy import select

    async with db.get_session_context() as session:
        widget = (await session.execute(select(WidgetModel).limit(1))).scalar_one()
        return str(widget.id)


@pytest.mark.asyncio
async def test_edit_form_prefills_and_saves(
    app_with_admin: tuple[FastAPI, AsyncDatabaseManager, RouterUser],
) -> None:
    app, db, _user = app_with_admin
    widget_id = await _first_widget_id(db)
    async with _client(app) as client:
        await _login(client)
        form = await client.get(f"/admin/m/{_SLUG}/{widget_id}/edit")
        assert form.status_code == 200
        assert "widget-0" in form.text  # prefilled value
        token = _csrf_from(form.text)
        saved = await client.post(
            f"/admin/m/{_SLUG}/{widget_id}/edit",
            data={"csrf_token": token, "name": "Renamed", "is_active": "true"},
            follow_redirects=False,
        )
        assert saved.status_code == 303
        detail = await client.get(f"/admin/m/{_SLUG}/{widget_id}")
    assert "Renamed" in detail.text


@pytest.mark.asyncio
async def test_delete_removes_row(
    app_with_admin: tuple[FastAPI, AsyncDatabaseManager, RouterUser],
) -> None:
    app, db, _user = app_with_admin
    widget_id = await _first_widget_id(db)
    async with _client(app) as client:
        await _login(client)
        detail = await client.get(f"/admin/m/{_SLUG}/{widget_id}")
        token = _csrf_from(detail.text)
        deleted = await client.post(
            f"/admin/m/{_SLUG}/{widget_id}/delete",
            data={"csrf_token": token},
            follow_redirects=False,
        )
        assert deleted.status_code == 303
        gone = await client.get(f"/admin/m/{_SLUG}/{widget_id}")
    assert gone.status_code == 404


@pytest.mark.asyncio
async def test_delete_csrf_mismatch_403(
    app_with_admin: tuple[FastAPI, AsyncDatabaseManager, RouterUser],
) -> None:
    app, db, _user = app_with_admin
    widget_id = await _first_widget_id(db)
    async with _client(app) as client:
        await _login(client)
        response = await client.post(
            f"/admin/m/{_SLUG}/{widget_id}/delete",
            data={"csrf_token": "bogus"},
        )
    assert response.status_code == 403


@pytest.mark.asyncio
async def test_detail_shows_edit_and_delete_controls(
    app_with_admin: tuple[FastAPI, AsyncDatabaseManager, RouterUser],
) -> None:
    app, db, _user = app_with_admin
    widget_id = await _first_widget_id(db)
    async with _client(app) as client:
        await _login(client)
        detail = await client.get(f"/admin/m/{_SLUG}/{widget_id}")
    assert f"/admin/m/{_SLUG}/{widget_id}/edit" in detail.text
    assert f"/admin/m/{_SLUG}/{widget_id}/delete" in detail.text


async def _all_widget_ids(db: AsyncDatabaseManager) -> list[str]:
    from sqlalchemy import select

    async with db.get_session_context() as session:
        rows = (await session.execute(select(WidgetModel))).scalars().all()
        return [str(r.id) for r in rows]


@pytest.mark.asyncio
async def test_list_renders_bulk_controls(
    app_with_admin: tuple[FastAPI, AsyncDatabaseManager, RouterUser],
) -> None:
    app, _db, _user = app_with_admin
    async with _client(app) as client:
        await _login(client)
        response = await client.get(f"/admin/m/{_SLUG}/")
    assert response.status_code == 200
    assert "data-select-all" in response.text
    assert 'name="ids"' in response.text
    assert 'name="action"' in response.text


@pytest.mark.asyncio
async def test_bulk_delete_removes_selected(
    app_with_admin: tuple[FastAPI, AsyncDatabaseManager, RouterUser],
) -> None:
    app, db, _user = app_with_admin
    ids = await _all_widget_ids(db)  # 3 widgets seeded
    async with _client(app) as client:
        await _login(client)
        page = await client.get(f"/admin/m/{_SLUG}/")
        token = _csrf_from(page.text)
        deleted = await client.post(
            f"/admin/m/{_SLUG}/bulk",
            data={"csrf_token": token, "action": "delete", "ids": ids[:2]},
            follow_redirects=False,
        )
        assert deleted.status_code == 303
    remaining = await _all_widget_ids(db)
    assert len(remaining) == 1


@pytest.mark.asyncio
async def test_bulk_deactivate_sets_is_active_false(
    app_with_admin: tuple[FastAPI, AsyncDatabaseManager, RouterUser],
) -> None:
    from uuid import UUID

    from sqlalchemy import select

    app, db, _user = app_with_admin
    ids = await _all_widget_ids(db)
    async with _client(app) as client:
        await _login(client)
        page = await client.get(f"/admin/m/{_SLUG}/")
        token = _csrf_from(page.text)
        response = await client.post(
            f"/admin/m/{_SLUG}/bulk",
            data={"csrf_token": token, "action": "deactivate", "ids": [ids[0]]},
            follow_redirects=False,
        )
        assert response.status_code == 303
    async with db.get_session_context() as session:
        widget = (
            await session.execute(
                select(WidgetModel).where(WidgetModel.id == UUID(ids[0]))
            )
        ).scalar_one()
        assert widget.is_active is False


@pytest.mark.asyncio
async def test_bulk_csrf_mismatch_403(
    app_with_admin: tuple[FastAPI, AsyncDatabaseManager, RouterUser],
) -> None:
    app, db, _user = app_with_admin
    ids = await _all_widget_ids(db)
    async with _client(app) as client:
        await _login(client)
        response = await client.post(
            f"/admin/m/{_SLUG}/bulk",
            data={"csrf_token": "bogus", "action": "delete", "ids": ids},
        )
    assert response.status_code == 403


@pytest.mark.asyncio
async def test_bulk_unknown_action_400(
    app_with_admin: tuple[FastAPI, AsyncDatabaseManager, RouterUser],
) -> None:
    app, db, _user = app_with_admin
    ids = await _all_widget_ids(db)
    async with _client(app) as client:
        await _login(client)
        page = await client.get(f"/admin/m/{_SLUG}/")
        token = _csrf_from(page.text)
        response = await client.post(
            f"/admin/m/{_SLUG}/bulk",
            data={"csrf_token": token, "action": "explode", "ids": ids},
        )
    assert response.status_code == 400


class CategoryModel(BaseModel):
    __tablename__ = "admin_fk_category"
    name: Mapped[str] = mapped_column(String(64), nullable=False)


class ProductModel(BaseModel):
    __tablename__ = "admin_fk_product"
    name: Mapped[str] = mapped_column(String(64), nullable=False)
    category_id: Mapped[_UUID | None] = mapped_column(
        _ForeignKey("admin_fk_category.id"), nullable=True, default=None
    )


@pytest.mark.asyncio
async def test_fk_field_renders_select_and_saves() -> None:
    """A FK to a registered admin renders as a related-rows dropdown."""
    from sqlalchemy import select

    db = AsyncDatabaseManager("sqlite+aiosqlite:///:memory:")
    await db.connect()
    await db.create_tables()
    async with db.get_session_context() as session:
        user = RouterUser(email="fk@example.com", hashed_password="", is_admin=True)
        user.set_password("hunter2")
        session.add(user)
        category = CategoryModel(name="Hardware")
        session.add(category)
        await session.commit()
        await session.refresh(category)
        category_id = str(category.id)

    site = AdminSite(title="FK Admin")
    site.register(AdminModel(model=CategoryModel, search_fields=[CategoryModel.name]))
    site.register(AdminModel(model=ProductModel))
    app = FastAPI()
    app.include_router(
        make_admin_router(
            site,
            db=db,
            auth_backend=UserModelAuthBackend(RouterUser),
            secret_key=SECRET,
            cookie_secure=False,
        )
    )
    try:
        async with _client(app) as client:
            await client.post(
                "/admin/login",
                data={"identifier": "fk@example.com", "password": "hunter2"},
            )
            form = await client.get("/admin/m/admin_fk_product/new")
            assert form.status_code == 200
            # FK renders a <select> listing the related row by its label.
            assert '<select name="category_id"' in form.text
            assert category_id in form.text
            assert "Hardware" in form.text

            token = _csrf_from(form.text)
            created = await client.post(
                "/admin/m/admin_fk_product/new",
                data={
                    "csrf_token": token,
                    "name": "Drill",
                    "category_id": category_id,
                },
                follow_redirects=False,
            )
            assert created.status_code == 303
        async with db.get_session_context() as session:
            product = (
                await session.execute(
                    select(ProductModel).where(ProductModel.name == "Drill")
                )
            ).scalar_one()
            assert str(product.category_id) == category_id
    finally:
        await db.drop_tables()
        await db.disconnect()


class AuditedModel(BaseModel):
    __tablename__ = "admin_audited"
    name: Mapped[str] = mapped_column(String(64), nullable=False)
    created_by: Mapped[_UUID | None] = mapped_column(nullable=True, default=None)
    updated_by: Mapped[_UUID | None] = mapped_column(nullable=True, default=None)


@pytest.mark.asyncio
async def test_create_stamps_audit_and_detail_shows_actor() -> None:
    """The admin stamps created_by/updated_by and the detail shows them."""
    from sqlalchemy import select

    db = AsyncDatabaseManager("sqlite+aiosqlite:///:memory:")
    await db.connect()
    await db.create_tables()
    async with db.get_session_context() as session:
        user = RouterUser(email="root@example.com", hashed_password="", is_admin=True)
        user.set_password("hunter2")
        session.add(user)
        await session.commit()
        await session.refresh(user)
        user_id = user.id

    site = AdminSite(title="Audit Admin")
    site.register(AdminModel(model=AuditedModel, list_display=[AuditedModel.name]))
    app = FastAPI()
    app.include_router(
        make_admin_router(
            site,
            db=db,
            auth_backend=UserModelAuthBackend(RouterUser),
            secret_key=SECRET,
            cookie_secure=False,
            show_metrics=False,
        )
    )
    try:
        async with _client(app) as client:
            await client.post(
                "/admin/login",
                data={"identifier": "root@example.com", "password": "hunter2"},
            )
            form = await client.get("/admin/m/admin_audited/new")
            token = _csrf_from(form.text)
            await client.post(
                "/admin/m/admin_audited/new",
                data={"csrf_token": token, "name": "Tracked"},
                follow_redirects=False,
            )
            # created_by stamped with the acting admin.
            async with db.get_session_context() as session:
                row = (
                    await session.execute(
                        select(AuditedModel).where(AuditedModel.name == "Tracked")
                    )
                ).scalar_one()
                assert row.created_by == user_id
                assert row.updated_by == user_id
                row_id = str(row.id)

            detail = await client.get(f"/admin/m/admin_audited/{row_id}")
        assert detail.status_code == 200
        assert "Audit" in detail.text
        # actor UUID resolved to the admin's display name
        assert "root@example.com" in detail.text
    finally:
        await db.drop_tables()
        await db.disconnect()


@pytest.mark.asyncio
async def test_detail_audit_panel_without_actors(
    app_with_admin: tuple[FastAPI, AsyncDatabaseManager, RouterUser],
) -> None:
    # WidgetModel has no AuditMixin → audit panel shows timestamps only,
    # no "created by" row.
    app, db, _user = app_with_admin
    widget_id = await _first_widget_id(db)
    async with _client(app) as client:
        await _login(client)
        detail = await client.get(f"/admin/m/{_SLUG}/{widget_id}")
    assert detail.status_code == 200
    assert "Audit" in detail.text
    assert "created by" not in detail.text


@pytest.mark.asyncio
async def test_permissions_disable_write_views() -> None:
    """can_create / can_edit / can_delete = False hide + 404 the views."""
    db = AsyncDatabaseManager("sqlite+aiosqlite:///:memory:")
    await db.connect()
    await db.create_tables()
    async with db.get_session_context() as session:
        user = RouterUser(email="ro@example.com", hashed_password="", is_admin=True)
        user.set_password("hunter2")
        session.add(user)
        session.add(WidgetModel(name="locked"))
        await session.commit()

    site = AdminSite(title="RO Admin")
    site.register(
        AdminModel(
            model=WidgetModel,
            list_display=[WidgetModel.id, WidgetModel.name],
            can_create=False,
            can_edit=False,
            can_delete=False,
        )
    )
    app = FastAPI()
    app.include_router(
        make_admin_router(
            site,
            db=db,
            auth_backend=UserModelAuthBackend(RouterUser),
            secret_key=SECRET,
            cookie_secure=False,
        )
    )
    try:
        async with _client(app) as client:
            await client.post(
                "/admin/login",
                data={"identifier": "ro@example.com", "password": "hunter2"},
            )
            listing = await client.get(f"/admin/m/{_SLUG}/")
            assert listing.status_code == 200
            assert "+ New" not in listing.text
            # No write permission anywhere → no bulk controls.
            assert "data-select-all" not in listing.text
            new = await client.get(f"/admin/m/{_SLUG}/new")
            assert new.status_code == 404
    finally:
        await db.drop_tables()
        await db.disconnect()
