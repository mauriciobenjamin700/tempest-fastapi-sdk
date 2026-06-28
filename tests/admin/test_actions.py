"""Tests for admin custom actions (@admin_action)."""

from __future__ import annotations

import re
from collections.abc import AsyncIterator

import pytest
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient
from sqlalchemy import String
from sqlalchemy.orm import Mapped, mapped_column

from tempest_fastapi_sdk import (
    AdminActionContext,
    AdminActionResult,
    AdminModel,
    AdminSite,
    AsyncDatabaseManager,
    BaseModel,
    BaseUserModel,
    UserModelAuthBackend,
    admin_action,
    make_admin_router,
)
from tempest_fastapi_sdk.admin.actions import resolve_admin_action


class ActionUser(BaseUserModel):
    __tablename__ = "admin_action_users"


class GizmoModel(BaseModel):
    __tablename__ = "admin_action_gizmo"
    name: Mapped[str] = mapped_column(String(64), nullable=False)


@admin_action(label="Rename to ACTED")
async def rename_acted(ctx: AdminActionContext) -> AdminActionResult:
    await ctx.repository.bulk_update({"id": ctx.ids}, {"name": "ACTED"})
    return AdminActionResult(f"Renamed {len(ctx.ids)} by {ctx.principal.email}.")


@admin_action(label="No message")
async def silent_action(ctx: AdminActionContext) -> None:
    return None


SECRET = "x" * 48
_SLUG = GizmoModel.__tablename__


@pytest.fixture
async def app_with_actions() -> AsyncIterator[FastAPI]:
    db = AsyncDatabaseManager("sqlite+aiosqlite:///:memory:")
    await db.connect()
    await db.create_tables()
    async with db.get_session_context() as session:
        user = ActionUser(email="root@example.com", hashed_password="", is_admin=True)
        user.set_password("hunter2")
        session.add(user)
        session.add_all([GizmoModel(name=f"gizmo-{i}") for i in range(3)])
        await session.commit()

    site = AdminSite(title="Test Admin")
    site.register(
        AdminModel(
            model=GizmoModel,
            list_display=[GizmoModel.id, GizmoModel.name],
            actions=[rename_acted, silent_action],
        )
    )
    app = FastAPI()
    app.include_router(
        make_admin_router(
            site,
            db=db,
            auth_backend=UserModelAuthBackend(ActionUser),
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
        "/admin/login",
        data={"identifier": "root@example.com", "password": "hunter2"},
    )


class TestActionRegistration:
    def test_decorator_attaches_metadata(self) -> None:
        action = resolve_admin_action(rename_acted)
        assert action.name == "rename_acted"
        assert action.label == "Rename to ACTED"

    def test_undecorated_function_rejected(self) -> None:
        async def plain(ctx: AdminActionContext) -> None:
            return None

        with pytest.raises(TypeError, match="not an @admin_action"):
            AdminModel(model=GizmoModel, actions=[plain])

    def test_duplicate_action_name_rejected(self) -> None:
        @admin_action(label="A", name="dup")
        async def a(ctx: AdminActionContext) -> None:
            return None

        @admin_action(label="B", name="dup")
        async def b(ctx: AdminActionContext) -> None:
            return None

        with pytest.raises(ValueError, match="Duplicate admin action"):
            AdminModel(model=GizmoModel, actions=[a, b])


class TestActionEndpoint:
    @pytest.mark.asyncio
    async def test_dropdown_lists_custom_action(
        self, app_with_actions: FastAPI
    ) -> None:
        async with _client(app_with_actions) as client:
            await _login(client)
            page = await client.get(f"/admin/m/{_SLUG}/")
        assert 'value="custom:rename_acted"' in page.text
        assert "Rename to ACTED" in page.text

    @pytest.mark.asyncio
    async def test_custom_action_runs_and_flashes(
        self, app_with_actions: FastAPI
    ) -> None:
        async with _client(app_with_actions) as client:
            await _login(client)
            page = await client.get(f"/admin/m/{_SLUG}/")
            match = re.search(r'name="csrf_token" value="([^"]+)"', page.text)
            assert match is not None
            token = match.group(1)
            ids = re.findall(r'name="ids" value="([^"]+)"', page.text)
            assert len(ids) == 3

            resp = await client.post(
                f"/admin/m/{_SLUG}/bulk",
                data={
                    "csrf_token": token,
                    "action": "custom:rename_acted",
                    "ids": [ids[0], ids[1]],
                },
                follow_redirects=False,
            )
            assert resp.status_code == 303
            assert "flash=" in resp.headers["location"]

            after = await client.get(resp.headers["location"])
        assert "Renamed 2 by root@example.com." in after.text
        assert after.text.count("ACTED") >= 2

    @pytest.mark.asyncio
    async def test_unknown_custom_action_400(self, app_with_actions: FastAPI) -> None:
        async with _client(app_with_actions) as client:
            await _login(client)
            page = await client.get(f"/admin/m/{_SLUG}/")
            match = re.search(r'name="csrf_token" value="([^"]+)"', page.text)
            assert match is not None
            token = match.group(1)
            ids = re.findall(r'name="ids" value="([^"]+)"', page.text)
            resp = await client.post(
                f"/admin/m/{_SLUG}/bulk",
                data={
                    "csrf_token": token,
                    "action": "custom:nope",
                    "ids": [ids[0]],
                },
                follow_redirects=False,
            )
        assert resp.status_code == 400
