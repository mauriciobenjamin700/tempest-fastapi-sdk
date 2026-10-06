"""An authenticated admin route re-reads the principal on every request.

The session cookie proves a login happened; it does not prove the account
still qualifies. :meth:`UserModelAuthBackend.load_principal` returns ``None``
for a row that was deleted, deactivated or had ``is_admin`` revoked, and the
panel turns that into a ``303`` to the login page -- but only on the routes
that actually call it. Four did not: the log export, both halves of the SQL
console and the task cancel. A revoked administrator kept using them until
the cookie expired (8h by default), and the SQL console runs statements.

So nothing here checks that a guard exists somewhere in the router: each test
revokes the access *after* the login and then calls the route, which is the
order the defect had.
"""

from __future__ import annotations

import re
from collections.abc import AsyncIterator
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import httpx2
import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import select, text

from tempest_fastapi_sdk import (
    AdminSite,
    AsyncDatabaseManager,
    BaseUserModel,
    UserModelAuthBackend,
)
from tempest_fastapi_sdk.admin import SqlShellService, TaskPanelService
from tempest_fastapi_sdk.admin.router import make_admin_router
from tempest_fastapi_sdk.admin.sql_shell import SqlAudit
from tempest_fastapi_sdk.tasks import JobStatus, JobStore, make_job_model

SECRET = "x" * 48

_CSRF_INPUT = re.compile(r'name="csrf_token" value="([^"]+)"')


class RevalidationUser(BaseUserModel):
    """The admin whose access these tests revoke mid-session."""

    __tablename__ = "admin_revalidation_users"


RevalidationJob = make_job_model(
    tablename="admin_revalidation_jobs",
    class_name="RevalidationJob",
)


@dataclass(frozen=True)
class Surface:
    """One route the defect let through, and the request that reaches it.

    Attributes:
        label (str): What the operator was doing, for the assertion message.
        method (str): ``GET`` or ``POST``.
        path (str): The path, prefix included.
        data (dict[str, str]): Form fields for the POSTs.
        live_status (int): Status a principal who still qualifies gets. The
            cancel answers ``303`` because that is its own redirect, which
            is why the tests below compare the ``Location`` too.
    """

    label: str
    method: str
    path: str
    data: dict[str, str]
    live_status: int


@dataclass
class Panel:
    """Everything the tests drive, built once per test.

    Attributes:
        client (TestClient): A signed-in client that does not follow
            redirects, so a refusal to login is visible as a status code.
        db (AsyncDatabaseManager): The database the admin reads from, used
            to revoke access after the login.
        store (JobStore[Any]): Where the cancel route writes.
        audits (list[SqlAudit]): Every SQL console attempt, allowed or not.
    """

    client: TestClient
    db: AsyncDatabaseManager
    store: JobStore[Any]
    audits: list[SqlAudit] = field(default_factory=list)

    def csrf(self, path: str) -> str:
        """Return the CSRF token the given page ships in its form.

        Read from the rendered page rather than from the session store, so
        the hidden input is covered too: a token no form carries is a route
        no operator can POST to.

        Args:
            path (str): The page holding the form.

        Returns:
            str: The token to submit.

        Raises:
            AssertionError: When the page carries no token at all.
        """
        page = self.client.get(path)
        match = _CSRF_INPUT.search(page.text)
        assert match is not None, f"no csrf_token input on {path}"
        return match.group(1)

    def call(self, surface: Surface) -> httpx2.Response:
        """Perform one request on the signed-in client.

        Args:
            surface (Surface): The route and payload to reach.

        Returns:
            httpx2.Response: The response, with redirects left unfollowed.
        """
        if surface.method == "GET":
            return self.client.get(surface.path)
        return self.client.post(surface.path, data=surface.data)


@pytest.fixture
async def panel(tmp_path: Path) -> AsyncIterator[Panel]:
    """Yield a signed-in client over an admin with every surface mounted.

    Args:
        tmp_path (Path): Pytest temporary directory.

    Yields:
        Panel: The harness, torn down with its database.
    """
    log_dir = tmp_path / "logs"
    log_dir.mkdir()
    (log_dir / "info.log").write_text("", encoding="utf-8")

    db = AsyncDatabaseManager("sqlite+aiosqlite:///:memory:")
    await db.connect()
    await db.create_tables()
    async with db.get_session_context() as session:
        await session.execute(text("CREATE TABLE orders (id INTEGER, total INTEGER)"))
        await session.execute(text("INSERT INTO orders VALUES (1, 100)"))
        user = RevalidationUser(email="root@example.com", is_admin=True)
        user.set_password("hunter2")
        session.add(user)

    store: JobStore[Any] = JobStore(db, model=RevalidationJob)
    audits: list[SqlAudit] = []
    app = FastAPI()
    app.include_router(
        make_admin_router(
            AdminSite(title="Revalidation Admin"),
            db=db,
            auth_backend=UserModelAuthBackend(RevalidationUser),
            secret_key=SECRET,
            cookie_secure=False,
            show_logs=True,
            log_dir=str(log_dir),
            sql_shell=SqlShellService(
                db,
                dialect="sqlite",
                auditor=audits.append,
            ),
            tasks=TaskPanelService(job_store=store),
        )
    )
    client = TestClient(app, follow_redirects=False)
    login = client.post(
        "/admin/login",
        data={"identifier": "root@example.com", "password": "hunter2"},
    )
    assert login.status_code == 303, login.text
    assert client.get("/admin/").status_code == 200
    yield Panel(client=client, db=db, store=store, audits=audits)
    await db.drop_tables()
    await db.disconnect()


async def _revoke(db: AsyncDatabaseManager, **columns: Any) -> None:
    """Change the signed-in admin's row after the login.

    Args:
        db (AsyncDatabaseManager): The manager the admin reads from.
        **columns (Any): Column values to write on the seeded row.
    """
    async with db.get_session_context() as session:
        row = (await session.execute(select(RevalidationUser))).scalar_one()
        for name, value in columns.items():
            setattr(row, name, value)


async def _delete_admin(db: AsyncDatabaseManager) -> None:
    """Delete the signed-in admin's row outright.

    Args:
        db (AsyncDatabaseManager): The manager the admin reads from.
    """
    async with db.get_session_context() as session:
        row = (await session.execute(select(RevalidationUser))).scalar_one()
        await session.delete(row)


async def _surfaces(panel: Panel) -> list[Surface]:
    """Return one entry per route that used to run on the cookie alone.

    Built while the principal still qualifies, because a revoked admin gets
    redirected before the pages that carry the CSRF tokens render. The token
    itself is read from the dashboard's logout form: it is the same session
    token, and that page is not one of the routes under test, so the payload
    never depends on the forms being fixed here.

    Args:
        panel (Panel): The signed-in harness.

    Returns:
        list[Surface]: The four routes, in the order of the advisory.
    """
    job = await panel.store.enqueue("extract")
    csrf = panel.csrf("/admin/")
    return [
        Surface("log export", "GET", "/admin/logs/export", {}, 200),
        Surface("sql console", "GET", "/admin/sql", {}, 200),
        Surface(
            "sql run",
            "POST",
            "/admin/sql",
            {"sql": "SELECT total FROM orders", "csrf_token": csrf},
            200,
        ),
        Surface(
            "task cancel",
            "POST",
            f"/admin/tasks/{job.id}/cancel",
            {"csrf_token": csrf},
            303,
        ),
    ]


def _served(panel: Panel, surface: Surface) -> tuple[int, str | None]:
    """Return the status and ``Location`` one route answered with.

    Args:
        panel (Panel): The signed-in harness.
        surface (Surface): The route to call.

    Returns:
        tuple[int, str | None]: The status code and the ``Location`` header.
    """
    response = panel.call(surface)
    return response.status_code, response.headers.get("location")


class TestRevokedAccessEndsAtTheNextRequest:
    """Losing admin access takes effect now, not when the cookie expires."""

    async def test_the_same_cookie_stops_serving_a_demoted_admin(
        self, panel: Panel
    ) -> None:
        surfaces = await _surfaces(panel)
        await _revoke(panel.db, is_admin=False)
        assert {s.label: _served(panel, s) for s in surfaces} == {
            s.label: (303, "/admin/login") for s in surfaces
        }

    async def test_the_same_cookie_stops_serving_a_deactivated_admin(
        self, panel: Panel
    ) -> None:
        surfaces = await _surfaces(panel)
        await _revoke(panel.db, is_active=False)
        assert {s.label: _served(panel, s) for s in surfaces} == {
            s.label: (303, "/admin/login") for s in surfaces
        }

    async def test_the_same_cookie_stops_serving_a_deleted_admin(
        self, panel: Panel
    ) -> None:
        surfaces = await _surfaces(panel)
        await _delete_admin(panel.db)
        assert {s.label: _served(panel, s) for s in surfaces} == {
            s.label: (303, "/admin/login") for s in surfaces
        }

    async def test_a_revoked_console_never_reaches_the_shell(
        self, panel: Panel
    ) -> None:
        surfaces = await _surfaces(panel)
        await _revoke(panel.db, is_admin=False)
        panel.call(surfaces[2])
        assert panel.audits == []

    async def test_a_revoked_cancel_leaves_the_row_alone(self, panel: Panel) -> None:
        job = await panel.store.enqueue("extract")
        surface = Surface(
            "task cancel",
            "POST",
            f"/admin/tasks/{job.id}/cancel",
            {"csrf_token": panel.csrf(f"/admin/tasks/{job.id}")},
            303,
        )
        await _revoke(panel.db, is_admin=False)
        panel.call(surface)
        assert (await panel.store.get(job.id)).status == JobStatus.QUEUED.value

    async def test_a_live_admin_is_not_refused_by_any_of_them(
        self, panel: Panel
    ) -> None:
        """The refusal comes from the row, not from a session nobody kept."""
        surfaces = await _surfaces(panel)
        assert {s.label: _served(panel, s)[0] for s in surfaces} == {
            s.label: s.live_status for s in surfaces
        }


class TestConsoleAndCancelCarryTheCsrfToken:
    """The two POSTs a browser form reaches require the session token."""

    async def test_the_console_page_ships_the_token(self, panel: Panel) -> None:
        assert panel.csrf("/admin/sql")

    async def test_the_cancel_form_ships_the_token(self, panel: Panel) -> None:
        job = await panel.store.enqueue("extract")
        await panel.store.claim(job.id)
        page = panel.client.get(f"/admin/tasks/{job.id}")
        assert 'name="csrf_token"' in page.text

    async def test_a_console_run_without_a_token_is_refused(self, panel: Panel) -> None:
        response = panel.client.post(
            "/admin/sql",
            data={"sql": "SELECT total FROM orders"},
        )
        assert response.status_code == 422
        assert panel.audits == []

    async def test_a_console_run_with_a_wrong_token_is_refused(
        self, panel: Panel
    ) -> None:
        response = panel.client.post(
            "/admin/sql",
            data={"sql": "SELECT total FROM orders", "csrf_token": "not-the-token"},
        )
        assert response.status_code == 403
        assert "csrf token mismatch" in response.text
        assert panel.audits == []

    async def test_a_console_run_with_the_token_runs(self, panel: Panel) -> None:
        response = panel.client.post(
            "/admin/sql",
            data={
                "sql": "SELECT total FROM orders",
                "csrf_token": panel.csrf("/admin/sql"),
            },
        )
        assert response.status_code == 200
        assert "100" in response.text
        assert len(panel.audits) == 1
        assert panel.audits[0].allowed is True

    async def test_a_cancel_without_a_token_is_refused(self, panel: Panel) -> None:
        job = await panel.store.enqueue("extract")
        response = panel.client.post(f"/admin/tasks/{job.id}/cancel")
        assert response.status_code == 422
        assert (await panel.store.get(job.id)).status == JobStatus.QUEUED.value

    async def test_a_cancel_with_a_wrong_token_is_refused(self, panel: Panel) -> None:
        job = await panel.store.enqueue("extract")
        response = panel.client.post(
            f"/admin/tasks/{job.id}/cancel",
            data={"csrf_token": "not-the-token"},
        )
        assert response.status_code == 403
        assert "csrf token mismatch" in response.text
        assert (await panel.store.get(job.id)).status == JobStatus.QUEUED.value

    async def test_a_cancel_with_the_token_flips_the_row(self, panel: Panel) -> None:
        job = await panel.store.enqueue("extract")
        response = panel.client.post(
            f"/admin/tasks/{job.id}/cancel",
            data={"csrf_token": panel.csrf(f"/admin/tasks/{job.id}")},
        )
        assert response.status_code == 303
        assert response.headers["location"] == f"/admin/tasks/{job.id}"
        assert (await panel.store.get(job.id)).status == JobStatus.CANCELLED.value


class TestConsoleRendersThePrincipalItLoaded:
    """The console uses the principal it reloads, like every other page."""

    async def test_the_header_names_the_signed_in_admin(self, panel: Panel) -> None:
        page = panel.client.get("/admin/sql")
        assert page.status_code == 200
        assert 'class="tempest-admin-header__user">root@example.com<' in page.text
        assert "tempest-admin-header__logout" in page.text
