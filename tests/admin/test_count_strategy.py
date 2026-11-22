"""``AdminModel(count_strategy=...)`` on SQLite: exact, estimate fallback, none."""

from __future__ import annotations

from collections.abc import AsyncIterator
from dataclasses import dataclass, field
from typing import Any, ClassVar

import pytest
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient
from sqlalchemy import String, and_, event
from sqlalchemy.dialects import postgresql
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


class HidingRepository(BaseRepository[CountItem]):
    """Tenant-style repository: ``paginate`` and ``count`` hide ``Item0``."""

    def __init__(self, session: Any) -> None:
        """Bind the repository.

        Args:
            session (Any): The session.
        """
        super().__init__(session, model=CountItem)

    async def paginate(self, *args: Any, **kwargs: Any) -> dict[str, Any]:
        """Paginate without ``Item0``.

        Args:
            *args (Any): Forwarded.
            **kwargs (Any): Forwarded; ``where`` gains the filter.

        Returns:
            dict[str, Any]: The page.
        """
        hidden = CountItem.name != "Item0"
        where = kwargs.pop("where", None)
        kwargs["where"] = hidden if where is None else and_(where, hidden)
        return await super().paginate(*args, **kwargs)

    async def count(self, filters: Any = None, where: Any = None) -> int:
        """Count without ``Item0``.

        Args:
            filters (Any): Forwarded.
            where (Any): ANDed with the filter.

        Returns:
            int: The count.
        """
        hidden = CountItem.name != "Item0"
        return await super().count(
            filters, hidden if where is None else and_(where, hidden)
        )


async def _open(
    strategy: Any, rows: int = 7, repository_class: Any = None
) -> AsyncIterator[CountWorld]:
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
            repository_class=repository_class,
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


@pytest.mark.asyncio
@pytest.mark.parametrize("strategy", ["none", "estimate"])
async def test_custom_repository_is_not_bypassed(strategy: str) -> None:
    async for world in _open(strategy, rows=4, repository_class=HidingRepository):
        page = await world.client.get(ITEMS)
        assert "Item0" not in page.text
        assert "Item1" in page.text
        assert "3 records" in page.text
        dashboard = await world.client.get("/admin/")
        assert 'tempest-admin-model-card__count">3<' in dashboard.text


class _FakeBind:
    """Stand-in bind carrying the real PostgreSQL dialect."""

    dialect = postgresql.dialect()


class _FakeSession:
    """Session that refuses to run SQL, to prove no query was sent."""

    def get_bind(self) -> _FakeBind:
        """Return the PostgreSQL-looking bind.

        Returns:
            _FakeBind: The bind.
        """
        return _FakeBind()

    async def execute(self, *args: Any, **kwargs: Any) -> Any:
        """Fail: the estimate must not be read.

        Args:
            *args (Any): Unused.
            **kwargs (Any): Unused.

        Raises:
            AssertionError: Always.
        """
        raise AssertionError("reltuples read for a single-table subclass")


class StiVehicle(BaseModel):
    """Single-table inheritance base."""

    __tablename__ = "admin_count_sti_vehicles"
    kind: Mapped[str] = mapped_column(String(16), nullable=False)
    __mapper_args__: ClassVar[dict[str, Any]] = {
        "polymorphic_on": "kind",
        "polymorphic_identity": "vehicle",
    }


class StiTruck(StiVehicle):
    """Single-table subclass: its table holds the siblings too."""

    __mapper_args__: ClassVar[dict[str, Any]] = {"polymorphic_identity": "truck"}


@pytest.mark.asyncio
async def test_single_table_subclass_counts_exactly() -> None:
    from tempest_fastapi_sdk.admin.router import _estimated_rows

    assert await _estimated_rows(_FakeSession(), StiTruck) is None  # type: ignore[arg-type]
    with pytest.raises(AssertionError, match="reltuples"):
        await _estimated_rows(_FakeSession(), StiVehicle)  # type: ignore[arg-type]
