"""``access_policy`` across model boundaries and the non-model surfaces.

The per-model routes already answer ``403`` to a denied action. These tests
cover what one model's page renders or writes of another model — inline
tables, the inline formset, foreign-key selects, list filters and
autocomplete — and the SQL console, task panel and logs page, which have no
model to ask the policy about.
"""

from __future__ import annotations

import re
from collections.abc import AsyncIterator
from dataclasses import dataclass
from pathlib import Path
from typing import Any
from uuid import UUID, uuid4

import pytest
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient
from sqlalchemy import ForeignKey, Integer, String, Uuid
from sqlalchemy.orm import Mapped, mapped_column

from tempest_fastapi_sdk import (
    AdminModel,
    AdminPermissionEnum,
    AdminSite,
    AdminSurfaceEnum,
    AsyncDatabaseManager,
    BaseModel,
    BaseUserModel,
    Inline,
    UserModelAuthBackend,
    make_admin_router,
)
from tempest_fastapi_sdk.admin import SqlShellService, TaskPanelService
from tempest_fastapi_sdk.testing import admin_login

SECRET = "x" * 48
PAY = 424242
DEPT_NAME = "Engineering"


class XUser(BaseUserModel):
    __tablename__ = "admin_xrbac_users"
    role: Mapped[str] = mapped_column(String(32), nullable=False, default="reader")


class XDept(BaseModel):
    __tablename__ = "admin_xrbac_dept"
    name: Mapped[str] = mapped_column(String(64), nullable=False)


class XEmp(BaseModel):
    __tablename__ = "admin_xrbac_emp"
    name: Mapped[str] = mapped_column(String(64), nullable=False)
    pay: Mapped[int] = mapped_column(Integer(), nullable=False, default=0)
    dept_id: Mapped[UUID] = mapped_column(
        Uuid(), ForeignKey("admin_xrbac_dept.id"), nullable=False
    )


class XTask(BaseModel):
    __tablename__ = "admin_xrbac_task"
    title: Mapped[str] = mapped_column(String(64), nullable=False)
    dept_id: Mapped[UUID] = mapped_column(
        Uuid(), ForeignKey("admin_xrbac_dept.id"), nullable=False
    )


class XUnreg(BaseModel):
    __tablename__ = "admin_xrbac_unreg"
    label: Mapped[str] = mapped_column(String(64), nullable=False)
    dept_id: Mapped[UUID] = mapped_column(
        Uuid(), ForeignKey("admin_xrbac_dept.id"), nullable=False
    )


_GRANTS: dict[str, dict[type[BaseModel], set[AdminPermissionEnum]]] = {
    "reader": {XDept: {AdminPermissionEnum.VIEW}},
    "editor": {
        XDept: {AdminPermissionEnum.VIEW},
        XEmp: {AdminPermissionEnum.VIEW, AdminPermissionEnum.EDIT},
        XTask: {AdminPermissionEnum.VIEW, AdminPermissionEnum.EDIT},
    },
    "fk": {
        XEmp: {
            AdminPermissionEnum.VIEW,
            AdminPermissionEnum.CREATE,
            AdminPermissionEnum.EDIT,
        },
        XTask: {AdminPermissionEnum.VIEW, AdminPermissionEnum.EDIT},
    },
}


def _policy(user: Any, admin: AdminModel[Any], action: AdminPermissionEnum) -> bool:
    """Grant everything to ``super`` and the ``_GRANTS`` table to the rest.

    Args:
        user (Any): The principal.
        admin (AdminModel[Any]): The admin asked about.
        action (AdminPermissionEnum): The action.

    Returns:
        bool: Whether the action is allowed.
    """
    if user.role == "super":
        return True
    return action in _GRANTS.get(user.role, {}).get(admin.model, set())


def _surfaces(user: Any, surface: AdminSurfaceEnum) -> bool:
    """Grant the non-model surfaces to ``super`` only.

    Args:
        user (Any): The principal.
        surface (AdminSurfaceEnum): The surface asked about.

    Returns:
        bool: Whether the surface is allowed.
    """
    return bool(user.role == "super")


class _Broker:
    """The registry half of a TaskIQ broker the task panel reads."""

    def get_all_tasks(self) -> dict[str, Any]:
        """Return an empty registry.

        Returns:
            dict[str, Any]: No tasks.
        """
        return {}


class _Queue:
    """A ``TaskQueue`` stand-in exposing only ``broker``."""

    def __init__(self) -> None:
        """Attach the fake broker."""
        self.broker = _Broker()


@dataclass
class Ids:
    """Row identifiers the tests address.

    Attributes:
        dept (str): The department id.
        emp_ada (str): Ada's id.
        emp_grace (str): Grace's id.
        task (str): The task id.
    """

    dept: str
    emp_ada: str
    emp_grace: str
    task: str


async def _build(
    tmp_path: Path, *, surface_policy: bool
) -> tuple[FastAPI, AsyncDatabaseManager, Ids]:
    """Seed the database and mount the admin.

    Args:
        tmp_path (Path): Directory for the (empty) logs.
        surface_policy (bool): Whether to pass ``surface_policy=``.

    Returns:
        tuple[FastAPI, AsyncDatabaseManager, Ids]: The app, the database
        and the seeded ids.
    """
    db = AsyncDatabaseManager("sqlite+aiosqlite:///:memory:")
    await db.connect()
    await db.create_tables()
    async with db.get_session_context() as session:
        for role in ("super", "reader", "editor", "fk"):
            user = XUser(email=f"{role}@x.com", is_admin=True, role=role)
            user.set_password("pw")
            session.add(user)
        dept = XDept(name=DEPT_NAME)
        session.add(dept)
        await session.flush()
        ada = XEmp(name="Ada", pay=1, dept_id=dept.id)
        grace = XEmp(name="Grace", pay=PAY, dept_id=dept.id)
        task = XTask(title="T-one", dept_id=dept.id)
        session.add_all([ada, grace, task, XUnreg(label="Hidden", dept_id=dept.id)])
        await session.commit()
        ids = Ids(str(dept.id), str(ada.id), str(grace.id), str(task.id))

    site = AdminSite(title="Cross RBAC")
    site.register(
        AdminModel(
            model=XDept,
            search_fields=[XDept.name],
            inlines=[
                Inline(XEmp, XEmp.dept_id, editable=True, can_delete=True),
                Inline(XTask, XTask.dept_id, editable=True),
                Inline(XUnreg, XUnreg.dept_id),
            ],
        )
    )
    site.register(AdminModel(model=XEmp, list_filter=[XEmp.dept_id]))
    site.register(
        AdminModel(
            model=XTask,
            can_create=False,
            can_delete=False,
            autocomplete_fields=[XTask.dept_id],
        )
    )
    app = FastAPI()
    app.include_router(
        make_admin_router(
            site,
            db=db,
            auth_backend=UserModelAuthBackend(XUser),
            secret_key=SECRET,
            cookie_secure=False,
            access_policy=_policy,
            surface_policy=_surfaces if surface_policy else None,
            sql_shell=SqlShellService(db, dialect="sqlite"),
            tasks=TaskPanelService(queue=_Queue()),
            show_logs=True,
            log_dir=tmp_path,
        )
    )
    return app, db, ids


@pytest.fixture
async def app_x(tmp_path: Path) -> AsyncIterator[tuple[FastAPI, Ids]]:
    app, db, ids = await _build(tmp_path, surface_policy=True)
    yield app, ids
    await db.drop_tables()
    await db.disconnect()


@pytest.fixture
async def app_no_surface_policy(
    tmp_path: Path,
) -> AsyncIterator[tuple[FastAPI, AsyncDatabaseManager]]:
    app, db, _ = await _build(tmp_path, surface_policy=False)
    yield app, db
    await db.drop_tables()
    await db.disconnect()


def _client(app: FastAPI) -> AsyncClient:
    return AsyncClient(transport=ASGITransport(app=app), base_url="http://test")


def _csrf(html: str) -> str:
    match = re.search(r'name="csrf_token" value="([^"]+)"', html)
    assert match is not None
    return match.group(1)


async def _names(app: FastAPI, model: type[XEmp] | type[XTask]) -> list[str]:
    """Return which seeded or posted names the super user's list shows.

    Args:
        app (FastAPI): The app.
        model (type[XEmp] | type[XTask]): The child model.

    Returns:
        list[str]: The known names present on the list page, sorted.
    """
    async with _client(app) as client:
        await admin_login(client, "super@x.com", "pw")
        body = (await client.get(f"/admin/m/{model.__tablename__}/")).text
    known = ["Ada", "Grace", "Added", "T-one", "T-added"]
    return sorted(name for name in known if name in body)


class TestInlineReadNeedsChildView:
    @pytest.mark.asyncio
    async def test_reader_does_not_see_child_rows(
        self, app_x: tuple[FastAPI, Ids]
    ) -> None:
        app, ids = app_x
        async with _client(app) as client:
            await admin_login(client, "reader@x.com", "pw")
            emp_list = await client.get(f"/admin/m/{XEmp.__tablename__}/")
            detail = await client.get(f"/admin/m/{XDept.__tablename__}/{ids.dept}")
        assert emp_list.status_code == 403
        assert detail.status_code == 200
        assert "Grace" not in detail.text
        assert str(PAY) not in detail.text

    @pytest.mark.asyncio
    async def test_unregistered_child_is_asked_about_too(
        self, app_x: tuple[FastAPI, Ids]
    ) -> None:
        app, ids = app_x
        async with _client(app) as client:
            await admin_login(client, "reader@x.com", "pw")
            detail = await client.get(f"/admin/m/{XDept.__tablename__}/{ids.dept}")
        assert "Hidden" not in detail.text

    @pytest.mark.asyncio
    async def test_super_still_sees_every_inline(
        self, app_x: tuple[FastAPI, Ids]
    ) -> None:
        app, ids = app_x
        async with _client(app) as client:
            await admin_login(client, "super@x.com", "pw")
            detail = await client.get(f"/admin/m/{XDept.__tablename__}/{ids.dept}")
        assert "Grace" in detail.text
        assert "Hidden" in detail.text
        assert "row.new0." in detail.text
        assert "__delete" in detail.text


class TestInlineFormsetNeedsCreateAndDelete:
    @pytest.mark.asyncio
    async def test_editor_page_has_no_add_row_nor_delete_box(
        self, app_x: tuple[FastAPI, Ids]
    ) -> None:
        app, ids = app_x
        async with _client(app) as client:
            await admin_login(client, "editor@x.com", "pw")
            detail = await client.get(f"/admin/m/{XDept.__tablename__}/{ids.dept}")
        assert detail.status_code == 200
        assert f"row.{ids.emp_ada}.name" in detail.text
        assert "row.new0." not in detail.text
        assert "__delete" not in detail.text

    @pytest.mark.asyncio
    async def test_editor_cannot_add_or_delete_through_formset(
        self, app_x: tuple[FastAPI, Ids]
    ) -> None:
        app, ids = app_x
        url = f"/admin/m/{XDept.__tablename__}/{ids.dept}/inlines/{XEmp.__tablename__}"
        async with _client(app) as client:
            await admin_login(client, "editor@x.com", "pw")
            page = await client.get(f"/admin/m/{XDept.__tablename__}/{ids.dept}")
            token = _csrf(page.text)
            add = await client.post(
                url,
                data={
                    "csrf_token": token,
                    f"row.{ids.emp_ada}.name": "Ada",
                    f"row.{ids.emp_ada}.pay": "1",
                    "row.new0.name": "Added",
                    "row.new0.pay": "1",
                },
            )
            delete = await client.post(
                url,
                data={
                    "csrf_token": token,
                    f"row.{ids.emp_grace}.name": "Grace",
                    f"row.{ids.emp_grace}.pay": str(PAY),
                    f"row.{ids.emp_grace}.__delete": "on",
                },
            )
            edit = await client.post(
                url,
                data={
                    "csrf_token": token,
                    f"row.{ids.emp_ada}.name": "Ada L.",
                    f"row.{ids.emp_ada}.pay": "2",
                },
            )
        assert add.status_code == 403
        assert delete.status_code == 403
        assert edit.status_code == 303
        assert await _names(app, XEmp) == ["Ada", "Grace"]

    @pytest.mark.asyncio
    async def test_child_with_can_create_off_refuses_formset_add(
        self, app_x: tuple[FastAPI, Ids]
    ) -> None:
        app, ids = app_x
        url = f"/admin/m/{XDept.__tablename__}/{ids.dept}/inlines/{XTask.__tablename__}"
        async with _client(app) as client:
            await admin_login(client, "super@x.com", "pw")
            page = await client.get(f"/admin/m/{XDept.__tablename__}/{ids.dept}")
            resp = await client.post(
                url,
                data={
                    "csrf_token": _csrf(page.text),
                    f"row.{ids.task}.title": "T-one",
                    "row.new0.title": "T-added",
                },
            )
        assert resp.status_code == 403
        assert await _names(app, XTask) == ["T-one"]

    @pytest.mark.asyncio
    async def test_super_can_add_and_delete(self, app_x: tuple[FastAPI, Ids]) -> None:
        app, ids = app_x
        url = f"/admin/m/{XDept.__tablename__}/{ids.dept}/inlines/{XEmp.__tablename__}"
        async with _client(app) as client:
            await admin_login(client, "super@x.com", "pw")
            page = await client.get(f"/admin/m/{XDept.__tablename__}/{ids.dept}")
            resp = await client.post(
                url,
                data={
                    "csrf_token": _csrf(page.text),
                    f"row.{ids.emp_grace}.name": "Grace",
                    f"row.{ids.emp_grace}.pay": str(PAY),
                    f"row.{ids.emp_grace}.__delete": "on",
                    "row.new0.name": "Added",
                    "row.new0.pay": "1",
                },
            )
        assert resp.status_code == 303
        assert await _names(app, XEmp) == ["Ada", "Added"]


class TestReferencedModelNeedsView:
    @pytest.mark.asyncio
    async def test_fk_select_hides_denied_labels(
        self, app_x: tuple[FastAPI, Ids]
    ) -> None:
        app, _ = app_x
        async with _client(app) as client:
            await admin_login(client, "fk@x.com", "pw")
            form = await client.get(f"/admin/m/{XEmp.__tablename__}/new")
            listing = await client.get(f"/admin/m/{XEmp.__tablename__}/")
        assert form.status_code == 200
        assert listing.status_code == 200
        assert DEPT_NAME not in form.text
        assert DEPT_NAME not in listing.text

    @pytest.mark.asyncio
    async def test_super_sees_fk_labels(self, app_x: tuple[FastAPI, Ids]) -> None:
        app, _ = app_x
        async with _client(app) as client:
            await admin_login(client, "super@x.com", "pw")
            form = await client.get(f"/admin/m/{XEmp.__tablename__}/new")
            listing = await client.get(f"/admin/m/{XEmp.__tablename__}/")
        assert DEPT_NAME in form.text
        assert DEPT_NAME in listing.text

    @pytest.mark.asyncio
    async def test_autocomplete_needs_view_on_referenced(
        self, app_x: tuple[FastAPI, Ids]
    ) -> None:
        app, ids = app_x
        url = f"/admin/m/{XTask.__tablename__}/autocomplete/dept_id?q=Eng"
        async with _client(app) as client:
            await admin_login(client, "fk@x.com", "pw")
            denied = await client.get(url)
            edit = await client.get(f"/admin/m/{XTask.__tablename__}/{ids.task}/edit")
            await admin_login(client, "super@x.com", "pw")
            allowed = await client.get(url)
        assert denied.status_code == 403
        assert edit.status_code == 200
        assert DEPT_NAME not in edit.text
        assert allowed.status_code == 200
        assert DEPT_NAME in allowed.text


class TestSidebarFollowsView:
    @pytest.mark.asyncio
    async def test_form_pages_list_only_viewable_models(
        self, app_x: tuple[FastAPI, Ids]
    ) -> None:
        """Every authenticated page filters the sidebar, forms included.

        The create and edit forms used to render the registry-wide default,
        naming models the principal is refused.
        """
        app, ids = app_x
        dept_link = f'href="/admin/m/{XDept.__tablename__}/"'
        emp_link = f'href="/admin/m/{XEmp.__tablename__}/"'
        async with _client(app) as client:
            await admin_login(client, "fk@x.com", "pw")
            pages = [
                await client.get(f"/admin/m/{XEmp.__tablename__}/new"),
                await client.get(f"/admin/m/{XEmp.__tablename__}/{ids.emp_ada}/edit"),
                await client.get(f"/admin/m/{XEmp.__tablename__}/"),
            ]
        for page in pages:
            assert page.status_code == 200, page.url
            assert emp_link in page.text, page.url
            assert dept_link not in page.text, page.url

    @pytest.mark.asyncio
    async def test_super_sidebar_lists_every_model(
        self, app_x: tuple[FastAPI, Ids]
    ) -> None:
        app, _ = app_x
        async with _client(app) as client:
            await admin_login(client, "super@x.com", "pw")
            page = await client.get(f"/admin/m/{XEmp.__tablename__}/new")
        assert f'href="/admin/m/{XDept.__tablename__}/"' in page.text


class TestSurfaces:
    @pytest.mark.asyncio
    async def test_restricted_principal_is_refused_every_surface(
        self, app_x: tuple[FastAPI, Ids]
    ) -> None:
        app, _ = app_x
        async with _client(app) as client:
            await admin_login(client, "reader@x.com", "pw")
            dashboard = await client.get("/admin/")
            responses = [
                await client.get("/admin/sql"),
                await client.post(
                    "/admin/sql",
                    data={
                        "csrf_token": _csrf(dashboard.text),
                        "sql": f"SELECT name, pay FROM {XEmp.__tablename__}",
                    },
                ),
                await client.get("/admin/tasks"),
                await client.post(
                    f"/admin/tasks/{uuid4()}/cancel",
                    data={"csrf_token": _csrf(dashboard.text)},
                ),
                await client.get("/admin/logs"),
                await client.get("/admin/logs/export"),
            ]
        assert [r.status_code for r in responses] == [403] * 6
        for path in ("/admin/sql", "/admin/tasks", "/admin/logs"):
            assert f'href="{path}"' not in dashboard.text

    @pytest.mark.asyncio
    async def test_surface_policy_grants_the_console(
        self, app_x: tuple[FastAPI, Ids]
    ) -> None:
        app, _ = app_x
        async with _client(app) as client:
            await admin_login(client, "super@x.com", "pw")
            page = await client.get("/admin/sql")
            run = await client.post(
                "/admin/sql",
                data={
                    "csrf_token": _csrf(page.text),
                    "sql": f"SELECT name, pay FROM {XEmp.__tablename__}",
                },
            )
            tasks = await client.get("/admin/tasks")
            logs = await client.get("/admin/logs")
        assert page.status_code == 200
        assert 'href="/admin/sql"' in page.text
        assert run.status_code == 200
        assert str(PAY) in run.text
        assert tasks.status_code == 200
        assert logs.status_code == 200

    @pytest.mark.asyncio
    async def test_access_policy_alone_closes_the_surfaces(
        self, app_no_surface_policy: tuple[FastAPI, AsyncDatabaseManager]
    ) -> None:
        app, _ = app_no_surface_policy
        async with _client(app) as client:
            await admin_login(client, "super@x.com", "pw")
            statuses = [
                (await client.get(path)).status_code
                for path in ("/admin/sql", "/admin/tasks", "/admin/logs")
            ]
        assert statuses == [403, 403, 403]
