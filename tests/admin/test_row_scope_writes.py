"""``row_scope`` on the write paths a per-route case does not reach.

Each test here pins one hole a security review found in the first cut of
the scope: a ``create_handler`` writing past it, a forged foreign key, an
edit wiping a reference the operator cannot see, the scope missing from the
``DELETE`` / ``UPDATE`` statement itself, author names read across tenants,
orphan uploads left by a refused create, and a dashboard that swallowed a
broken scope without a log line.
"""

from __future__ import annotations

import logging
import re
import uuid
from collections.abc import AsyncIterator, Sequence
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any

import pytest
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient
from pydantic import BaseModel as PydanticModel
from sqlalchemy import ColumnElement, String, and_, event
from sqlalchemy.orm import Mapped, mapped_column

from tempest_fastapi_sdk import (
    AdminModel,
    AdminSite,
    AsyncDatabaseManager,
    BaseModel,
    BaseRepository,
    UserModelAuthBackend,
    make_admin_router,
)
from tempest_fastapi_sdk.admin import (
    AdminActionContext,
    AdminActionResult,
    AdminCreateHandler,
    AdminRowScopeError,
)
from tempest_fastapi_sdk.admin.csv_import import sign_import_payload
from tempest_fastapi_sdk.testing import admin_csrf_token, admin_login
from tests.admin.row_scope_world import (
    NOTES,
    OTHER,
    OWN,
    SECRET,
    TICKETS,
    ScopeCategory,
    ScopeNote,
    ScopeTicket,
    ScopeUser,
    ScopeWorld,
    new_database,
    open_world,
)

FK_MISSING = "Select a valid choice; that record does not exist."


class ScopeVoucher(BaseModel):
    """Row created only through a ``create_handler``."""

    __tablename__ = "admin_scope_vouchers"
    code: Mapped[str] = mapped_column(String(32), nullable=False)
    company: Mapped[str] = mapped_column(String(32), nullable=False)


class ScopeAuthored(BaseModel):
    """Row carrying the ``created_by`` / ``updated_by`` audit stamps."""

    __tablename__ = "admin_scope_authored"
    title: Mapped[str] = mapped_column(String(32), nullable=False)
    company: Mapped[str] = mapped_column(String(32), nullable=False)
    created_by: Mapped[uuid.UUID | None] = mapped_column(nullable=True)
    updated_by: Mapped[uuid.UUID | None] = mapped_column(nullable=True)


class VoucherSchema(PydanticModel):
    """Input of the voucher handler."""

    code: str
    company: str


async def issue_voucher(
    data: VoucherSchema, ctx: AdminActionContext
) -> AdminActionResult:
    """Create a voucher through the session, as a service would.

    Args:
        data (VoucherSchema): The validated form.
        ctx (AdminActionContext): The request context.

    Returns:
        AdminActionResult: A plain success.
    """
    await ctx.repository.add(ScopeVoucher(code=data.code, company=data.company))
    return AdminActionResult(message="issued")


def _company(column: Any) -> Any:
    """Build a sync scope keeping the principal's company.

    Args:
        column (Any): The ``company`` column.

    Returns:
        Any: The ``row_scope``.
    """

    def scope(user: Any, admin: Any) -> ColumnElement[bool]:
        """Return ``column == user.company``.

        Args:
            user (Any): The principal.
            admin (Any): The admin.

        Returns:
            ColumnElement[bool]: The clause.
        """
        return column == user.company

    return scope


def _mount(db: AsyncDatabaseManager, admins: Sequence[AdminModel[Any]]) -> FastAPI:
    """Mount an admin over ``admins``.

    Args:
        db (AsyncDatabaseManager): The database.
        admins (Sequence[AdminModel[Any]]): What to register.

    Returns:
        FastAPI: The app.
    """
    site = AdminSite(title="Writes")
    for admin in admins:
        site.register(admin)
    app = FastAPI()
    app.include_router(
        make_admin_router(
            site,
            db=db,
            auth_backend=UserModelAuthBackend(ScopeUser),
            secret_key=SECRET,
            cookie_secure=False,
        )
    )
    return app


async def _operator(db: AsyncDatabaseManager) -> ScopeUser:
    """Insert the signed-in operator, of company ``OWN``.

    Args:
        db (AsyncDatabaseManager): The database.

    Returns:
        ScopeUser: The operator.
    """
    async with db.get_session_context() as session:
        user = ScopeUser(
            email="op@example.com", hashed_password="", is_admin=True, company=OWN
        )
        user.set_password("hunter2")
        session.add(user)
        await session.commit()
        return user


@asynccontextmanager
async def _signed_in(app: FastAPI, path: str) -> AsyncIterator[tuple[AsyncClient, str]]:
    """Open a client, sign in and read a CSRF token from ``path``.

    Args:
        app (FastAPI): The app.
        path (str): A page carrying the token.

    Yields:
        tuple[AsyncClient, str]: The client and the token.
    """
    async with AsyncClient(
        transport=ASGITransport(app=app), base_url="http://test"
    ) as client:
        await admin_login(client, "op@example.com", "hunter2")
        page = await client.get(path)
        yield client, admin_csrf_token(page.text)


async def _count(db: AsyncDatabaseManager, model: type[BaseModel]) -> int:
    """Count every row of ``model``.

    Args:
        db (AsyncDatabaseManager): The database.
        model (type[BaseModel]): The model.

    Returns:
        int: The row count.
    """
    async with db.get_session_context() as session:
        return await BaseRepository(session, model=model).count()


@pytest.fixture
async def database() -> AsyncIterator[AsyncDatabaseManager]:
    """Yield a fresh in-memory database.

    Yields:
        AsyncDatabaseManager: The connected manager.
    """
    db = await new_database()
    yield db
    await db.drop_tables()
    await db.disconnect()


@pytest.fixture
async def world(
    database: AsyncDatabaseManager, tmp_path: Path
) -> AsyncIterator[ScopeWorld]:
    """The shared scoped world (tickets, categories, notes).

    Yields:
        ScopeWorld: The signed-in world.
    """
    async for opened in open_world(database, tmp_path, scoped=True):
        yield opened


@pytest.mark.asyncio
async def test_create_handler_row_outside_the_scope_is_rolled_back(
    database: AsyncDatabaseManager,
) -> None:
    await _operator(database)
    app = _mount(
        database,
        [
            AdminModel(
                ScopeVoucher,
                create_handler=AdminCreateHandler(
                    schema=VoucherSchema, handler=issue_voucher, label="Issue"
                ),
                row_scope=_company(ScopeVoucher.company),
            )
        ],
    )
    path = f"/admin/m/{ScopeVoucher.__tablename__}/new"
    async with _signed_in(app, path) as (client, csrf):
        form = await client.get(path)
        nonce = re.search(r'name="submission_nonce" value="([^"]+)"', form.text)
        assert nonce is not None
        refused = await client.post(
            path,
            data={
                "csrf_token": csrf,
                "submission_nonce": nonce.group(1),
                "code": "V-B",
                "company": OTHER,
            },
        )
        assert refused.status_code == 400
        assert "outside the rows you may manage" in refused.text
        assert await _count(database, ScopeVoucher) == 0
        form = await client.get(path)
        nonce = re.search(r'name="submission_nonce" value="([^"]+)"', form.text)
        assert nonce is not None
        accepted = await client.post(
            path,
            data={
                "csrf_token": csrf,
                "submission_nonce": nonce.group(1),
                "code": "V-A",
                "company": OWN,
            },
        )
    assert accepted.status_code == 303
    assert await _count(database, ScopeVoucher) == 1


@pytest.mark.asyncio
async def test_forged_fk_on_create_answers_like_a_missing_row(
    world: ScopeWorld,
) -> None:
    before = await world.ticket_count()
    forged = await world.client.post(
        f"{TICKETS}/new",
        data={
            "csrf_token": world.csrf,
            "name": "Forged",
            "company": OWN,
            "category_id": str(world.ids["category_out"]),
        },
    )
    missing = await world.client.post(
        f"{TICKETS}/new",
        data={
            "csrf_token": world.csrf,
            "name": "Missing",
            "company": OWN,
            "category_id": str(uuid.uuid4()),
        },
    )
    assert forged.status_code == 400
    assert FK_MISSING in forged.text
    assert missing.status_code == 400
    assert FK_MISSING in missing.text
    assert await world.ticket_count() == before
    accepted = await world.client.post(
        f"{TICKETS}/new",
        data={
            "csrf_token": world.csrf,
            "name": "Fine",
            "company": OWN,
            "category_id": str(world.ids["category_in"]),
        },
    )
    assert accepted.status_code == 303


@pytest.mark.asyncio
async def test_forged_fk_on_edit_is_refused(world: ScopeWorld) -> None:
    resp = await world.client.post(
        f"{TICKETS}/{world.ids['ticket_in']}/edit",
        data={
            "csrf_token": world.csrf,
            "name": "TicketInside",
            "company": OWN,
            "category_id": str(world.ids["category_out"]),
        },
    )
    assert resp.status_code == 400
    assert FK_MISSING in resp.text
    inside = await world.ticket("in")
    assert inside is not None and inside.category_id == world.ids["category_in"]


@pytest.mark.asyncio
async def test_forged_fk_on_inline_is_refused(world: ScopeWorld) -> None:
    note = world.ids["note_in"]
    resp = await world.client.post(
        f"{TICKETS}/{world.ids['ticket_in']}/inlines/{ScopeNote.__tablename__}",
        data={
            "csrf_token": world.csrf,
            f"row.{note}.body": "NoteInside",
            f"row.{note}.company": OWN,
            f"row.{note}.category_id": str(world.ids["category_out"]),
        },
    )
    assert resp.status_code == 400
    assert FK_MISSING in resp.text
    stored = await world.note("in")
    assert stored is not None and stored.category_id is None


@pytest.mark.asyncio
async def test_forged_fk_on_import_is_refused(world: ScopeWorld) -> None:
    before = await world.ticket_count()
    text = f"name,company,category_id\nForged,{OWN},{world.ids['category_out']}\n"
    preview = await world.client.post(
        f"{TICKETS}/import",
        data={"csrf_token": world.csrf},
        files={"file": ("t.csv", text.encode(), "text/csv")},
    )
    assert FK_MISSING in preview.text
    assert 'name="payload"' not in preview.text
    payload = sign_import_payload(
        SECRET,
        slug=ScopeTicket.__tablename__,
        principal_id=world.principal_id,
        text=text,
    )
    page = await world.client.get(f"{TICKETS}/")
    nonce = re.search(r'name="submission_nonce" value="([^"]+)"', page.text)
    assert nonce is not None
    confirm = await world.client.post(
        f"{TICKETS}/import/confirm",
        data={
            "csrf_token": world.csrf,
            "payload": payload,
            "submission_nonce": nonce.group(1),
        },
    )
    assert confirm.status_code == 400
    assert await world.ticket_count() == before


@pytest.mark.asyncio
async def test_edit_keeps_a_reference_the_operator_cannot_see(
    world: ScopeWorld,
) -> None:
    async with world.db.get_session_context() as session:
        repo: BaseRepository[ScopeNote] = BaseRepository(session, model=ScopeNote)
        note = await repo.get_by_id(world.ids["note_in"])
        note.category_id = world.ids["category_out"]
        await repo.update(note)
    hidden_id = str(world.ids["category_out"])
    form = await world.client.get(f"{NOTES}/{world.ids['note_in']}/edit")
    assert form.status_code == 200
    assert f'<option value="{hidden_id}" selected>{hidden_id}</option>' in form.text
    assert "CategoryOutside" not in form.text
    resp = await world.client.post(
        f"{NOTES}/{world.ids['note_in']}/edit",
        data={
            "csrf_token": world.csrf,
            "body": "Renamed",
            "company": OWN,
            "ticket_id": str(world.ids["ticket_in"]),
            "category_id": hidden_id,
        },
    )
    assert resp.status_code == 303
    stored = await world.note("in")
    assert stored is not None
    assert stored.body == "Renamed"
    assert stored.category_id == world.ids["category_out"]


def _capture(db: AsyncDatabaseManager) -> tuple[list[str], Any]:
    """Start recording every SQL statement the engine sends.

    Args:
        db (AsyncDatabaseManager): The database.

    Returns:
        tuple[list[str], Any]: The statement list and the listener.
    """
    statements: list[str] = []

    def record(
        conn: Any,
        cursor: Any,
        statement: str,
        parameters: Any,
        context: Any,
        executemany: bool,
    ) -> None:
        """Append one statement.

        Args:
            conn (Any): The connection.
            cursor (Any): The cursor.
            statement (str): The SQL.
            parameters (Any): The parameters.
            context (Any): The context.
            executemany (bool): Whether it is an executemany.
        """
        statements.append(" ".join(statement.split()))

    event.listen(db.engine.sync_engine, "before_cursor_execute", record)
    return statements, record


@pytest.mark.asyncio
async def test_scope_is_inside_the_delete_statements(world: ScopeWorld) -> None:
    async with world.db.get_session_context() as session:
        extra = ScopeNote(body="Extra", company=OWN, ticket_id=world.ids["ticket_in"])
        session.add(extra)
        await session.commit()
        extra_id = extra.id
    statements, record = _capture(world.db)
    try:
        single = await world.client.post(
            f"{NOTES}/{world.ids['note_in']}/delete", data={"csrf_token": world.csrf}
        )
        bulk = await world.client.post(
            f"{NOTES}/bulk",
            data={"csrf_token": world.csrf, "action": "delete", "ids": [str(extra_id)]},
        )
    finally:
        event.remove(world.db.engine.sync_engine, "before_cursor_execute", record)
    assert single.status_code == 303
    assert bulk.status_code == 303
    deletes = [
        sql
        for sql in statements
        if sql.startswith(f"DELETE FROM {ScopeNote.__tablename__}")
    ]
    assert len(deletes) == 2
    assert all(f"{ScopeNote.__tablename__}.company = ?" in sql for sql in deletes)
    assert await world.note("in") is None
    async with world.db.get_session_context() as session:
        repo: BaseRepository[ScopeNote] = BaseRepository(session, model=ScopeNote)
        assert await repo.get_or_none({"id": extra_id}) is None


@pytest.mark.asyncio
async def test_deactivate_that_leaves_the_scope_is_refused(
    database: AsyncDatabaseManager, tmp_path: Path
) -> None:
    async for opened in open_world(database, tmp_path, scoped=True):
        app = _mount(
            database,
            [
                AdminModel(
                    ScopeTicket,
                    row_scope=lambda user, admin: and_(
                        ScopeTicket.company == user.company,
                        ScopeTicket.is_active.is_(True),
                    ),
                )
            ],
        )
        async with _signed_in(app, f"{TICKETS}/") as (client, csrf):
            resp = await client.post(
                f"{TICKETS}/bulk",
                data={
                    "csrf_token": csrf,
                    "action": "deactivate",
                    "ids": [str(opened.ids["ticket_in"])],
                },
            )
        assert resp.status_code == 303
        assert "flash_cat=error" in resp.headers["location"]
        inside = await opened.ticket("in")
        assert inside is not None and inside.is_active is True


@pytest.mark.asyncio
async def test_audit_author_of_another_tenant_is_shown_by_id(
    database: AsyncDatabaseManager,
) -> None:
    operator = await _operator(database)
    async with database.get_session_context() as session:
        foreigner = ScopeUser(
            email="foreigner@example.com",
            hashed_password="",
            is_admin=True,
            company=OTHER,
        )
        session.add(foreigner)
        await session.flush()
        row = ScopeAuthored(
            title="Shared", company=OWN, created_by=foreigner.id, updated_by=operator.id
        )
        session.add(row)
        await session.commit()
        foreigner_id, row_id = str(foreigner.id), row.id
    app = _mount(
        database,
        [
            AdminModel(ScopeUser, row_scope=_company(ScopeUser.company)),
            AdminModel(ScopeAuthored, row_scope=_company(ScopeAuthored.company)),
        ],
    )
    async with _signed_in(app, "/admin/") as (client, _csrf):
        page = await client.get(f"/admin/m/{ScopeAuthored.__tablename__}/{row_id}")
    assert page.status_code == 200
    assert "foreigner@example.com" not in page.text
    assert foreigner_id in page.text
    assert "op@example.com" in page.text


@pytest.mark.asyncio
async def test_refused_create_deletes_the_file_it_stored(
    world: ScopeWorld, tmp_path: Path
) -> None:
    resp = await world.client.post(
        f"{TICKETS}/new",
        data={"csrf_token": world.csrf, "name": "WithFile", "company": OTHER},
        files={"attachment": ("proof.txt", b"orphan", "text/plain")},
    )
    assert resp.status_code == 400
    assert "outside the rows you may manage" in resp.text
    stored = [path for path in tmp_path.rglob("*") if path.is_file()]
    assert sorted(path.name for path in stored) == ["in.txt", "out.txt"]


@pytest.mark.asyncio
async def test_broken_scope_fails_closed_and_the_dashboard_logs_it(
    database: AsyncDatabaseManager, caplog: pytest.LogCaptureFixture
) -> None:
    await _operator(database)
    app = _mount(
        database,
        [AdminModel(ScopeCategory, row_scope=lambda user, admin: None)],
    )
    caplog.set_level(logging.ERROR)
    async with _signed_in(app, "/admin/") as (client, _csrf):
        dashboard = await client.get("/admin/")
        assert dashboard.status_code == 200
        assert "could not count admin_scope_categories" in caplog.text
        with pytest.raises(AdminRowScopeError):
            await client.get(f"/admin/m/{ScopeCategory.__tablename__}/")
