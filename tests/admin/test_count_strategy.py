"""``AdminModel(count_strategy=...)`` on SQLite: exact, estimate fallback, none."""

from __future__ import annotations

from collections.abc import AsyncIterator
from dataclasses import dataclass, field
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
    BaseUserModel,
    UserModelAuthBackend,
    make_admin_router,
)
from tempest_fastapi_sdk.admin import AdminCountStrategyEnum
from tempest_fastapi_sdk.testing import admin_login

SECRET = "k" * 48


class CountUser(BaseUserModel):
    """Operator of the count tests."""

    __tablename__ = "admin_count_users"


class CountItem(BaseModel):
    """Rows the list counts."""

    __tablename__ = "admin_count_items"
    name: Mapped[str] = mapped_column(String(64), nullable=False)


ITEMS = f"/admin/m/{CountItem.__tablename__}/"


@dataclass
class CountWorld:
    """A signed-in client and the SQL statements it caused."""

    client: AsyncClient
    statements: list[str] = field(default_factory=list)

    def counts(self) -> int:
        """Return how many captured statements ran ``count(``.

        Returns:
            int: The number of counting statements.
        """
        return sum("count(" in sql.lower() for sql in self.statements)


async def _open(strategy: Any, rows: int = 7) -> AsyncIterator[CountWorld]:
    """Seed ``rows`` items, mount the admin with page size 3 and sign in.

    Args:
        strategy (Any): The ``count_strategy``.
        rows (int): How many items to insert.

    Yields:
        CountWorld: The signed-in world.
    """
    db = AsyncDatabaseManager("sqlite+aiosqlite:///:memory:")
    await db.connect()
    await db.create_tables()
    async with db.get_session_context() as session:
        user = CountUser(email="op@example.com", hashed_password="", is_admin=True)
        user.set_password("hunter2")
        session.add(user)
        session.add_all([CountItem(name=f"Item{n}") for n in range(rows)])
        await session.commit()
    site = AdminSite(title="Count")
    site.register(
        AdminModel(
            CountItem,
            search_fields=["name"],
            page_size=3,
            count_strategy=strategy,
        )
    )
    app = FastAPI()
    app.include_router(
        make_admin_router(
            site,
            db=db,
            auth_backend=UserModelAuthBackend(CountUser),
            secret_key=SECRET,
            cookie_secure=False,
            show_metrics=False,
        )
    )
    world_statements: list[str] = []

    def capture(
        conn: Any, cursor: Any, statement: str, *args: Any, **kwargs: Any
    ) -> None:
        """Record every statement sent to the database.

        Args:
            conn (Any): The connection.
            cursor (Any): The DBAPI cursor.
            statement (str): The SQL.
            *args (Any): Unused.
            **kwargs (Any): Unused.
        """
        world_statements.append(statement)

    assert db.engine is not None
    event.listen(db.engine.sync_engine, "before_cursor_execute", capture)
    async with AsyncClient(
        transport=ASGITransport(app=app), base_url="http://test"
    ) as client:
        await admin_login(client, "op@example.com", "hunter2")
        world = CountWorld(client=client, statements=world_statements)
        yield world
    event.remove(db.engine.sync_engine, "before_cursor_execute", capture)
    await db.drop_tables()
    await db.disconnect()


@pytest.mark.asyncio
async def test_exact_is_the_default_and_counts() -> None:
    assert AdminModel(CountItem).count_strategy is AdminCountStrategyEnum.EXACT
    async for world in _open("exact"):
        world.statements.clear()
        page = await world.client.get(ITEMS)
        assert "7 records" in page.text
        assert "Page 1 of 3" in page.text
        assert world.counts() == 1


@pytest.mark.asyncio
async def test_estimate_on_sqlite_counts_exactly_without_tilde() -> None:
    async for world in _open(AdminCountStrategyEnum.ESTIMATE):
        world.statements.clear()
        page = await world.client.get(ITEMS)
        assert "7 records" in page.text
        assert "~" not in page.text.split("tempest-admin-count", 1)[1][:80]
        assert "Page 1 of 3" in page.text
        assert world.counts() == 1


@pytest.mark.asyncio
async def test_none_shows_only_previous_and_next() -> None:
    async for world in _open("none"):
        world.statements.clear()
        first = await world.client.get(ITEMS)
        assert world.counts() == 0
        assert "records" not in first.text
        assert "Page 1" in first.text
        assert "of " not in first.text.split("tempest-admin-list__pagination", 1)[1]
        assert "page=2" in first.text
        assert "Prev" not in first.text
        last = await world.client.get(f"{ITEMS}?page=3")
        assert "Item" in last.text
        assert "page=4" not in last.text
        assert "page=2" in last.text
        assert world.counts() == 0


@pytest.mark.asyncio
async def test_none_on_an_exact_multiple_has_no_next() -> None:
    async for world in _open("none", rows=6):
        second = await world.client.get(f"{ITEMS}?page=2")
        assert "page=3" not in second.text
        assert "page=1" in second.text


@pytest.mark.asyncio
async def test_none_keeps_search_on_the_page_links() -> None:
    async for world in _open("none"):
        page = await world.client.get(f"{ITEMS}?q=Item")
        assert "q=Item&amp;page=2" in page.text


@pytest.mark.asyncio
async def test_dashboard_follows_the_strategy() -> None:
    async for world in _open("none"):
        world.statements.clear()
        page = await world.client.get("/admin/")
        assert "tempest-admin-model-card__count" not in page.text
        assert world.counts() == 0
    async for world in _open("estimate"):
        page = await world.client.get("/admin/")
        assert 'tempest-admin-model-card__count">7<' in page.text


def test_unknown_strategy_is_refused() -> None:
    with pytest.raises(ValueError, match="count_strategy"):
        AdminModel(CountItem, count_strategy="approximate")  # type: ignore[arg-type]
