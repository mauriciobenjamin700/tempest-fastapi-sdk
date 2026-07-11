"""End-to-end tests for admin granular RBAC (access_policy)."""

from __future__ import annotations

from collections.abc import AsyncIterator
from typing import Any

import pytest
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient
from sqlalchemy import String
from sqlalchemy.orm import Mapped, mapped_column

from tempest_fastapi_sdk import (
    AdminModel,
    AdminPermission,
    AdminSite,
    AsyncDatabaseManager,
    BaseModel,
    BaseUserModel,
    UserModelAuthBackend,
    make_admin_router,
)


class RoleUser(BaseUserModel):
    __tablename__ = "admin_rbac_users"
    role: Mapped[str] = mapped_column(String(32), nullable=False, default="support")


class Item(BaseModel):
    __tablename__ = "admin_rbac_item"
    name: Mapped[str] = mapped_column(String(64), nullable=False)


class Secret(BaseModel):
    __tablename__ = "admin_rbac_secret"
    code: Mapped[str] = mapped_column(String(64), nullable=False)


SECRET = "x" * 48


def _policy(user: Any, admin: AdminModel[Any], action: AdminPermission) -> bool:
    if getattr(user, "role", "") == "super":
        return True
    # "support": may only VIEW Item; nothing on Secret.
    if admin.model is Secret:
        return False
    return action is AdminPermission.VIEW


@pytest.fixture
async def app_rbac() -> AsyncIterator[tuple[FastAPI, str]]:
    db = AsyncDatabaseManager("sqlite+aiosqlite:///:memory:")
    await db.connect()
    await db.create_tables()
    async with db.get_session_context() as session:
        for email, role in [("super@x.com", "super"), ("support@x.com", "support")]:
            u = RoleUser(email=email, hashed_password="", is_admin=True, role=role)
            u.set_password("pw")
            session.add(u)
        item = Item(name="widget")
        session.add(item)
        await session.commit()
        await session.refresh(item)
        item_id = str(item.id)

    site = AdminSite(title="RBAC Admin")
    site.register(AdminModel(model=Item))
    site.register(AdminModel(model=Secret))
    app = FastAPI()
    app.include_router(
        make_admin_router(
            site,
            db=db,
            auth_backend=UserModelAuthBackend(RoleUser),
            secret_key=SECRET,
            cookie_secure=False,
            access_policy=_policy,
        )
    )
    yield app, item_id
    await db.drop_tables()
    await db.disconnect()


def _client(app: FastAPI) -> AsyncClient:
    return AsyncClient(transport=ASGITransport(app=app), base_url="http://test")


async def _login(client: AsyncClient, email: str) -> None:
    await client.post("/admin/login", data={"identifier": email, "password": "pw"})


class TestSupportRole:
    @pytest.mark.asyncio
    async def test_can_view_item(self, app_rbac: tuple[FastAPI, str]) -> None:
        app, _ = app_rbac
        async with _client(app) as client:
            await _login(client, "support@x.com")
            resp = await client.get(f"/admin/m/{Item.__tablename__}/")
        assert resp.status_code == 200

    @pytest.mark.asyncio
    async def test_cannot_view_secret(self, app_rbac: tuple[FastAPI, str]) -> None:
        app, _ = app_rbac
        async with _client(app) as client:
            await _login(client, "support@x.com")
            resp = await client.get(f"/admin/m/{Secret.__tablename__}/")
        assert resp.status_code == 403

    @pytest.mark.asyncio
    async def test_dashboard_and_nav_hide_secret(
        self, app_rbac: tuple[FastAPI, str]
    ) -> None:
        app, _ = app_rbac
        async with _client(app) as client:
            await _login(client, "support@x.com")
            resp = await client.get("/admin/")
        assert resp.status_code == 200
        assert Item.__tablename__ in resp.text
        assert Secret.__tablename__ not in resp.text

    @pytest.mark.asyncio
    async def test_create_denied(self, app_rbac: tuple[FastAPI, str]) -> None:
        app, _ = app_rbac
        async with _client(app) as client:
            await _login(client, "support@x.com")
            get_new = await client.get(f"/admin/m/{Item.__tablename__}/new")
            list_page = await client.get(f"/admin/m/{Item.__tablename__}/")
        assert get_new.status_code == 403
        # No "+ New" link on the list when create is denied.
        assert "+ New" not in list_page.text

    @pytest.mark.asyncio
    async def test_delete_denied(self, app_rbac: tuple[FastAPI, str]) -> None:
        app, item_id = app_rbac
        async with _client(app) as client:
            await _login(client, "support@x.com")
            detail = await client.get(f"/admin/m/{Item.__tablename__}/{item_id}")
            resp = await client.post(
                f"/admin/m/{Item.__tablename__}/{item_id}/delete",
                data={"csrf_token": "irrelevant"},
            )
        # 403 from the policy before the CSRF check even matters.
        assert resp.status_code == 403
        assert "Delete" not in detail.text


class TestSuperRole:
    @pytest.mark.asyncio
    async def test_sees_everything(self, app_rbac: tuple[FastAPI, str]) -> None:
        app, _ = app_rbac
        async with _client(app) as client:
            await _login(client, "super@x.com")
            dash = await client.get("/admin/")
            secret = await client.get(f"/admin/m/{Secret.__tablename__}/")
            new = await client.get(f"/admin/m/{Item.__tablename__}/new")
        assert Secret.__tablename__ in dash.text
        assert secret.status_code == 200
        assert new.status_code == 200
