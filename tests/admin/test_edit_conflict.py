"""Optimistic lock on the admin edit form (``updated_at`` claimed in the UPDATE)."""

from __future__ import annotations

import re
import uuid
from collections.abc import AsyncIterator
from typing import Any

import pytest
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient
from sqlalchemy import String, event
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
from tempest_fastapi_sdk.testing import admin_csrf_token, admin_login

SECRET = "c" * 48


class ConflictUser(BaseUserModel):
    """Operator of the conflict tests."""

    __tablename__ = "admin_conflict_users"


class ConflictItem(BaseModel):
    """Row edited by two operators at once."""

    __tablename__ = "admin_conflict_items"
    name: Mapped[str] = mapped_column(String(64), nullable=False)
    note: Mapped[str | None] = mapped_column(String(64), nullable=True)


EDIT = f"/admin/m/{ConflictItem.__tablename__}"
VERSION = re.compile(r'name="_row_version" value="([^"]+)"')


@pytest.fixture
async def world() -> AsyncIterator[tuple[AsyncClient, AsyncDatabaseManager, str]]:
    """Mount the admin over one seeded row and sign in.

    Yields:
        tuple[AsyncClient, AsyncDatabaseManager, str]: Client, database and
        the row id.
    """
    db = AsyncDatabaseManager("sqlite+aiosqlite:///:memory:")
    await db.connect()
    await db.create_tables()
    async with db.get_session_context() as session:
        user = ConflictUser(email="op@example.com", hashed_password="", is_admin=True)
        user.set_password("hunter2")
        item = ConflictItem(name="Original")
        session.add_all([user, item])
        await session.commit()
        item_id = str(item.id)
    site = AdminSite(title="Conflict")
    site.register(AdminModel(ConflictItem))
    app = FastAPI()
    app.include_router(
        make_admin_router(
            site,
            db=db,
            auth_backend=UserModelAuthBackend(ConflictUser),
            secret_key=SECRET,
            cookie_secure=False,
        )
    )
    async with AsyncClient(
        transport=ASGITransport(app=app), base_url="http://test"
    ) as client:
        await admin_login(client, "op@example.com", "hunter2")
        yield client, db, item_id
    await db.drop_tables()
    await db.disconnect()


async def _open(client: AsyncClient, item_id: str) -> tuple[str, str]:
    """Open the edit form.

    Args:
        client (AsyncClient): The signed-in client.
        item_id (str): The row id.

    Returns:
        tuple[str, str]: The CSRF token and the row version.
    """
    page = await client.get(f"{EDIT}/{item_id}/edit")
    match = VERSION.search(page.text)
    assert match is not None
    return admin_csrf_token(page.text), match.group(1)


async def _row(db: AsyncDatabaseManager, item_id: str) -> ConflictItem:
    """Reload the row.

    Args:
        db (AsyncDatabaseManager): The database.
        item_id (str): The row id.

    Returns:
        ConflictItem: The stored row.
    """
    async with db.get_session_context() as session:
        repo: BaseRepository[ConflictItem] = BaseRepository(session, model=ConflictItem)
        return await repo.get_by_id(uuid.UUID(item_id))


@pytest.mark.asyncio
async def test_second_save_of_the_same_version_is_refused(
    world: tuple[AsyncClient, AsyncDatabaseManager, str],
) -> None:
    client, db, item_id = world
    csrf, version = await _open(client, item_id)
    first = await client.post(
        f"{EDIT}/{item_id}/edit",
        data={"csrf_token": csrf, "name": "FromAlice", "_row_version": version},
    )
    assert first.status_code == 303
    second = await client.post(
        f"{EDIT}/{item_id}/edit",
        data={
            "csrf_token": csrf,
            "name": "FromBob",
            "note": "bob was here",
            "_row_version": version,
        },
    )
    assert second.status_code == 409
    assert "Someone else saved this record after you opened it" in second.text
    assert 'value="FromBob"' in second.text
    assert 'value="bob was here"' in second.text
    stored = await _row(db, item_id)
    assert stored.name == "FromAlice"
    assert stored.note is None
    refreshed = VERSION.search(second.text)
    assert refreshed is not None
    assert refreshed.group(1) != version
    assert refreshed.group(1) == stored.updated_at.isoformat()


@pytest.mark.asyncio
async def test_saving_again_with_the_new_version_overwrites(
    world: tuple[AsyncClient, AsyncDatabaseManager, str],
) -> None:
    client, db, item_id = world
    csrf, version = await _open(client, item_id)
    await client.post(
        f"{EDIT}/{item_id}/edit",
        data={"csrf_token": csrf, "name": "FromAlice", "_row_version": version},
    )
    refused = await client.post(
        f"{EDIT}/{item_id}/edit",
        data={"csrf_token": csrf, "name": "FromBob", "_row_version": version},
    )
    match = VERSION.search(refused.text)
    assert match is not None
    again = await client.post(
        f"{EDIT}/{item_id}/edit",
        data={"csrf_token": csrf, "name": "FromBob", "_row_version": match.group(1)},
    )
    assert again.status_code == 303
    assert (await _row(db, item_id)).name == "FromBob"


@pytest.mark.asyncio
async def test_change_outside_the_admin_is_detected(
    world: tuple[AsyncClient, AsyncDatabaseManager, str],
) -> None:
    client, db, item_id = world
    csrf, version = await _open(client, item_id)
    async with db.get_session_context() as session:
        repo: BaseRepository[ConflictItem] = BaseRepository(session, model=ConflictItem)
        row = await repo.get_by_id(uuid.UUID(item_id))
        row.note = "set by a worker"
        await repo.update(row)
    resp = await client.post(
        f"{EDIT}/{item_id}/edit",
        data={"csrf_token": csrf, "name": "FromForm", "_row_version": version},
    )
    assert resp.status_code == 409
    stored = await _row(db, item_id)
    assert stored.name == "Original"
    assert stored.note == "set by a worker"


@pytest.mark.asyncio
async def test_post_without_version_saves_as_before(
    world: tuple[AsyncClient, AsyncDatabaseManager, str],
) -> None:
    client, db, item_id = world
    csrf, _version = await _open(client, item_id)
    resp = await client.post(
        f"{EDIT}/{item_id}/edit",
        data={"csrf_token": csrf, "name": "NoLock"},
    )
    assert resp.status_code == 303
    assert (await _row(db, item_id)).name == "NoLock"


@pytest.mark.asyncio
async def test_version_is_claimed_by_the_update_itself(
    world: tuple[AsyncClient, AsyncDatabaseManager, str],
) -> None:
    client, db, item_id = world
    csrf, version = await _open(client, item_id)
    statements: list[str] = []

    def capture(
        conn: Any,
        cursor: Any,
        statement: str,
        parameters: Any,
        context: Any,
        executemany: bool,
    ) -> None:
        """Record every statement sent to the driver.

        Args:
            conn (Any): The connection.
            cursor (Any): The DB-API cursor.
            statement (str): The SQL.
            parameters (Any): The bound parameters.
            context (Any): The execution context.
            executemany (bool): Whether it is an executemany.
        """
        statements.append(" ".join(statement.split()))

    engine = db.engine.sync_engine
    event.listen(engine, "before_cursor_execute", capture)
    try:
        resp = await client.post(
            f"{EDIT}/{item_id}/edit",
            data={"csrf_token": csrf, "name": "Claimed", "_row_version": version},
        )
    finally:
        event.remove(engine, "before_cursor_execute", capture)
    assert resp.status_code == 303
    table = ConflictItem.__tablename__
    guard = [
        sql
        for sql in statements
        if sql.startswith(f"UPDATE {table} SET updated_at=")
        and f"{table}.updated_at = ?" in sql
    ]
    assert len(guard) == 1
    guard_at = statements.index(guard[0])
    write_at = next(
        index
        for index, sql in enumerate(statements)
        if sql.startswith(f"UPDATE {table} SET name=")
    )
    assert guard_at < write_at
    between = statements[guard_at:write_at]
    assert not any(sql.startswith(("COMMIT", "BEGIN")) for sql in between)
    assert (await _row(db, item_id)).name == "Claimed"
