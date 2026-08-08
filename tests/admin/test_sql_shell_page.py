"""Tests for the SQL console page mounted on the admin router."""

from __future__ import annotations

from collections.abc import AsyncIterator
from typing import Any

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import text

from tempest_fastapi_sdk import BaseUserModel
from tempest_fastapi_sdk.admin import AdminSite, make_admin_router
from tempest_fastapi_sdk.admin.auth import UserModelAuthBackend
from tempest_fastapi_sdk.admin.sql_shell import (
    SqlAudit,
    SqlCapability,
    SqlShellPolicy,
    SqlShellService,
)
from tempest_fastapi_sdk.db.connection import AsyncDatabaseManager


class ConsoleUser(BaseUserModel):
    """Admin principal for the console tests."""

    __tablename__ = "sql_console_users"


@pytest.fixture
async def console() -> AsyncIterator[tuple[TestClient, list[SqlAudit], Any]]:
    """Return a logged-in client over an admin with the console mounted."""
    db = AsyncDatabaseManager("sqlite+aiosqlite:///:memory:")
    await db.connect()
    await db.create_tables()
    async with db.get_session_context() as session:
        await session.execute(text("CREATE TABLE orders (id INTEGER, total INTEGER)"))
        await session.execute(text("INSERT INTO orders VALUES (1, 100), (2, 200)"))
        await session.execute(text("CREATE TABLE secrets (token TEXT)"))
        await session.execute(text("INSERT INTO secrets VALUES ('shh')"))
        user = ConsoleUser(email="ops@x.com", is_admin=True)
        user.set_password("pw")
        session.add(user)

    audits: list[SqlAudit] = []
    shell = SqlShellService(
        db,
        policy=SqlShellPolicy(denied_tables={"secrets"}),
        dialect="sqlite",
        auditor=audits.append,
    )
    app = FastAPI()
    app.include_router(
        make_admin_router(
            AdminSite(title="T"),
            db=db,
            auth_backend=UserModelAuthBackend(ConsoleUser),
            secret_key="k" * 32,
            cookie_secure=False,
            sql_shell=shell,
        ),
    )
    client = TestClient(app)
    client.post("/admin/login", data={"identifier": "ops@x.com", "password": "pw"})
    yield client, audits, shell
    await db.disconnect()


class TestConsolePage:
    def test_the_page_renders_and_shows_the_policy(
        self,
        console: tuple[TestClient, list[SqlAudit], Any],
    ) -> None:
        client, _audits, _shell = console
        response = client.get("/admin/sql")
        assert response.status_code == 200
        assert "SQL console" in response.text
        assert "secrets" in response.text

    def test_the_sidebar_links_to_it(
        self,
        console: tuple[TestClient, list[SqlAudit], Any],
    ) -> None:
        client, _audits, _shell = console
        assert "/admin/sql" in client.get("/admin/").text

    def test_a_select_renders_its_rows(
        self,
        console: tuple[TestClient, list[SqlAudit], Any],
    ) -> None:
        client, _audits, _shell = console
        response = client.post(
            "/admin/sql",
            data={"sql": "SELECT id, total FROM orders ORDER BY id"},
        )
        assert response.status_code == 200
        assert "100" in response.text
        assert "200" in response.text

    def test_a_refusal_is_shown_as_a_policy_refusal(
        self,
        console: tuple[TestClient, list[SqlAudit], Any],
    ) -> None:
        client, _audits, _shell = console
        response = client.post("/admin/sql", data={"sql": "DROP TABLE orders"})
        assert response.status_code == 200
        assert "Refused by policy" in response.text
        assert "drop is not permitted" in response.text

    def test_a_denied_table_is_refused(
        self,
        console: tuple[TestClient, list[SqlAudit], Any],
    ) -> None:
        client, _audits, _shell = console
        response = client.post("/admin/sql", data={"sql": "SELECT * FROM secrets"})
        assert "Refused by policy" in response.text
        assert "shh" not in response.text

    def test_a_broken_statement_is_shown_as_a_failure_not_a_refusal(
        self,
        console: tuple[TestClient, list[SqlAudit], Any],
    ) -> None:
        client, _audits, _shell = console
        response = client.post("/admin/sql", data={"sql": "SELECT * FROM nope"})
        assert "Statement failed" in response.text
        assert "Refused by policy" not in response.text

    def test_the_submitted_sql_is_kept_in_the_editor(
        self,
        console: tuple[TestClient, list[SqlAudit], Any],
    ) -> None:
        client, _audits, _shell = console
        response = client.post("/admin/sql", data={"sql": "SELECT 1 FROM orders"})
        assert "SELECT 1 FROM orders" in response.text

    def test_every_attempt_is_audited_with_the_principal(
        self,
        console: tuple[TestClient, list[SqlAudit], Any],
    ) -> None:
        client, audits, _shell = console
        client.post("/admin/sql", data={"sql": "SELECT id FROM orders"})
        client.post("/admin/sql", data={"sql": "DROP TABLE orders"})
        assert len(audits) == 2
        assert audits[0].allowed is True
        assert audits[1].allowed is False
        assert all(entry.principal for entry in audits)


def _mounted_paths(app: FastAPI) -> set[str]:
    """Collect every route path reachable from ``app``, mounts included.

    Starlette 1.5 stopped flattening ``include_router`` into ``app.routes``
    and keeps an ``_IncludedRouter`` entry instead, which holds the child
    routes on ``original_router``. A flat ``route.path`` comprehension
    therefore raises ``AttributeError`` on the container, and — worse for
    a test asserting a path is *absent* — a comprehension that merely
    skipped the container would pass while seeing no admin route at all.
    Descending through both shapes keeps the assertion meaningful.

    Args:
        app (FastAPI): The application to inspect.

    Returns:
        set[str]: Every registered path.
    """
    paths: set[str] = set()
    pending: list[object] = list(app.routes)
    while pending:
        route = pending.pop()
        path = getattr(route, "path", None)
        if isinstance(path, str):
            paths.add(path)
        nested = getattr(route, "original_router", None)
        if nested is not None:
            pending.append(nested)
        pending.extend(getattr(route, "routes", []))
    return paths


class TestConsoleIsOptIn:
    @pytest.mark.asyncio
    async def test_no_route_without_a_service(self) -> None:
        db = AsyncDatabaseManager("sqlite+aiosqlite:///:memory:")
        await db.connect()
        await db.create_tables()
        app = FastAPI()
        app.include_router(
            make_admin_router(
                AdminSite(title="T"),
                db=db,
                auth_backend=UserModelAuthBackend(ConsoleUser),
                secret_key="k" * 32,
                cookie_secure=False,
            ),
        )
        assert "/admin/sql" not in _mounted_paths(app)
        await db.disconnect()


class TestConsoleRequiresLogin:
    @pytest.mark.asyncio
    async def test_an_anonymous_request_is_redirected(self) -> None:
        db = AsyncDatabaseManager("sqlite+aiosqlite:///:memory:")
        await db.connect()
        await db.create_tables()
        app = FastAPI()
        app.include_router(
            make_admin_router(
                AdminSite(title="T"),
                db=db,
                auth_backend=UserModelAuthBackend(ConsoleUser),
                secret_key="k" * 32,
                cookie_secure=False,
                sql_shell=SqlShellService(db, dialect="sqlite"),
            ),
        )
        client = TestClient(app, follow_redirects=False)
        assert client.get("/admin/sql").status_code in {302, 303, 307}
        assert client.post("/admin/sql", data={"sql": "SELECT 1"}).status_code in {
            302,
            303,
            307,
        }
        await db.disconnect()


class TestMutatingPolicyWarning:
    @pytest.mark.asyncio
    async def test_a_writable_console_warns_on_the_page(self) -> None:
        db = AsyncDatabaseManager("sqlite+aiosqlite:///:memory:")
        await db.connect()
        await db.create_tables()
        async with db.get_session_context() as session:
            user = ConsoleUser(email="w@x.com", is_admin=True)
            user.set_password("pw")
            session.add(user)
        app = FastAPI()
        app.include_router(
            make_admin_router(
                AdminSite(title="T"),
                db=db,
                auth_backend=UserModelAuthBackend(ConsoleUser),
                secret_key="k" * 32,
                cookie_secure=False,
                sql_shell=SqlShellService(
                    db,
                    policy=SqlShellPolicy(
                        capabilities={SqlCapability.READ, SqlCapability.UPDATE},
                    ),
                    dialect="sqlite",
                ),
            ),
        )
        client = TestClient(app)
        client.post("/admin/login", data={"identifier": "w@x.com", "password": "pw"})
        assert "can modify data" in client.get("/admin/sql").text
        await db.disconnect()
