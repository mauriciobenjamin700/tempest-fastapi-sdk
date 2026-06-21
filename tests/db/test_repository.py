"""Tests for tempest_fastapi_sdk.db.repository.BaseRepository."""

from datetime import UTC, datetime

import pytest
from sqlalchemy import Integer, String, inspect
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import Mapped, mapped_column

from tempest_fastapi_sdk import (
    AsyncDatabaseManager,
    BaseModel,
    BaseRepository,
    ConflictException,
    NotFoundException,
    SoftDeleteMixin,
)


class Product(BaseModel):
    __tablename__ = "product_for_repo_test"

    name: Mapped[str] = mapped_column(String(64), nullable=False, unique=True)
    category: Mapped[str] = mapped_column(String(64), nullable=False)


class ProductNotFoundError(NotFoundException):
    """Subclass kept only for isinstance/except matching."""

    message: str = "Product not found"
    code: str = "PRODUCT_NOT_FOUND"


class ProductRepository(BaseRepository[Product]):
    """Subclass kept around to demonstrate the custom-methods pattern."""

    def __init__(
        self,
        session: AsyncSession,
        *,
        not_found_message: str | None = None,
        create_conflict_message: str | None = None,
    ) -> None:
        super().__init__(
            session,
            model=Product,
            not_found_exception=ProductNotFoundError,
            not_found_message=not_found_message,
            create_conflict_message=create_conflict_message,
        )


@pytest.fixture
def repo(session: AsyncSession) -> ProductRepository:
    return ProductRepository(session)


class TestAddAndGet:
    async def test_add_and_get_by_id(self, repo: ProductRepository) -> None:
        product = await repo.add(Product(name="apple", category="fruit"))
        loaded = await repo.get({"id": product.id})
        assert loaded.name == "apple"

    async def test_get_missing_raises_custom_not_found(
        self, repo: ProductRepository
    ) -> None:
        with pytest.raises(ProductNotFoundError):
            await repo.get({"name": "ghost"})

    async def test_add_duplicate_raises_conflict(self, repo: ProductRepository) -> None:
        await repo.add(Product(name="apple", category="fruit"))
        with pytest.raises(ConflictException):
            await repo.add(Product(name="apple", category="fruit"))


class TestList:
    async def test_empty_returns_empty_list(self, repo: ProductRepository) -> None:
        result = await repo.list()
        assert result == []

    async def test_filter_by_category(self, repo: ProductRepository) -> None:
        await repo.add_all(
            [
                Product(name="apple", category="fruit"),
                Product(name="banana", category="fruit"),
                Product(name="carrot", category="veg"),
            ]
        )
        result = await repo.list(filters={"category": "fruit"})
        assert {p.name for p in result} == {"apple", "banana"}

    async def test_ilike_search_by_name(self, repo: ProductRepository) -> None:
        await repo.add_all(
            [
                Product(name="apple-red", category="fruit"),
                Product(name="apple-green", category="fruit"),
                Product(name="banana", category="fruit"),
            ]
        )
        result = await repo.list(filters={"name": "apple"})
        assert {p.name for p in result} == {"apple-red", "apple-green"}

    async def test_list_in_clause(self, repo: ProductRepository) -> None:
        await repo.add_all(
            [
                Product(name="a", category="x"),
                Product(name="b", category="y"),
                Product(name="c", category="z"),
            ]
        )
        result = await repo.list(filters={"category": ["x", "z"]})
        assert {p.name for p in result} == {"a", "c"}


class TestPaginate:
    async def test_basic_pagination(self, repo: ProductRepository) -> None:
        for i in range(15):
            await repo.add(Product(name=f"item-{i:02d}", category="fruit"))
        page = await repo.paginate(page=1, page_size=10)
        assert page["total"] == 15
        assert page["pages"] == 2
        assert len(page["items"]) == 10

    async def test_pagination_with_filters(self, repo: ProductRepository) -> None:
        for i in range(5):
            await repo.add(Product(name=f"a-{i}", category="fruit"))
        for i in range(3):
            await repo.add(Product(name=f"b-{i}", category="veg"))
        page = await repo.paginate(filters={"category": "veg"}, page_size=10)
        assert page["total"] == 3


class TestUpdate:
    async def test_update_persists(self, repo: ProductRepository) -> None:
        product = await repo.add(Product(name="apple", category="fruit"))
        product.category = "produce"
        updated = await repo.update(product)
        assert updated.category == "produce"


class TestDelete:
    async def test_delete_by_id(self, repo: ProductRepository) -> None:
        product = await repo.add(Product(name="apple", category="fruit"))
        await repo.delete(product.id)
        with pytest.raises(ProductNotFoundError):
            await repo.get({"id": product.id})

    async def test_delete_missing_raises_not_found(
        self, repo: ProductRepository
    ) -> None:
        from uuid import uuid4

        with pytest.raises(ProductNotFoundError):
            await repo.delete(uuid4())

    async def test_delete_many_by_filter(self, repo: ProductRepository) -> None:
        await repo.add_all(
            [
                Product(name="a", category="x"),
                Product(name="b", category="x"),
                Product(name="c", category="y"),
            ]
        )
        deleted = await repo.delete_many({"category": "x"})
        assert deleted == 2
        remaining = await repo.list()
        assert [p.name for p in remaining] == ["c"]


class TestCount:
    async def test_count_with_filter(self, repo: ProductRepository) -> None:
        await repo.add_all(
            [
                Product(name="a", category="x"),
                Product(name="b", category="x"),
                Product(name="c", category="y"),
            ]
        )
        assert await repo.count({"category": "x"}) == 2
        assert await repo.count() == 3


class TestExists:
    async def test_returns_true_when_present(self, repo: ProductRepository) -> None:
        await repo.add(Product(name="apple", category="fruit"))
        assert await repo.exists({"name": "apple"}) is True

    async def test_returns_false_when_absent(self, repo: ProductRepository) -> None:
        assert await repo.exists({"name": "ghost"}) is False


class TestExistsExcluding:
    async def test_true_when_other_row_matches(self, repo: ProductRepository) -> None:
        await repo.add(Product(name="apple", category="fruit"))
        pear = await repo.add(Product(name="pear", category="fruit"))
        # "category=fruit" is used by another row (apple), excluding pear.
        assert (
            await repo.exists_excluding({"category": "fruit"}, exclude_id=pear.id)
            is True
        )

    async def test_false_when_only_excluded_row_matches(
        self, repo: ProductRepository
    ) -> None:
        apple = await repo.add(Product(name="apple", category="fruit"))
        # "name=apple" is unique to apple itself → excluding it leaves none.
        assert (
            await repo.exists_excluding({"name": "apple"}, exclude_id=apple.id) is False
        )

    async def test_none_exclude_behaves_like_exists(
        self, repo: ProductRepository
    ) -> None:
        apple = await repo.add(Product(name="apple", category="fruit"))
        assert await repo.exists_excluding({"name": "apple"}, exclude_id=None) is True
        assert (
            await repo.exists_excluding({"name": "ghost"}, exclude_id=apple.id) is False
        )


class TestResolve:
    async def test_returns_instance_unchanged(self, repo: ProductRepository) -> None:
        product = await repo.add(Product(name="apple", category="fruit"))
        resolved = await repo.resolve(product)
        assert resolved is product

    async def test_loads_by_uuid(self, repo: ProductRepository) -> None:
        product = await repo.add(Product(name="apple", category="fruit"))
        resolved = await repo.resolve(product.id)
        assert resolved.id == product.id
        assert resolved.name == "apple"

    async def test_raises_for_unknown_uuid(self, repo: ProductRepository) -> None:
        from uuid import uuid4

        with pytest.raises(ProductNotFoundError):
            await repo.resolve(uuid4())

    async def test_reattaches_detached_instance(self, db: AsyncDatabaseManager) -> None:
        """A detached instance is merged into the repository's session.

        Reproduces the production footgun: an instance loaded on one
        session (then closed → detached) is handed to a repository bound
        to a *different* session. ``resolve`` must re-attach it so a
        subsequent ``update`` commits instead of raising
        ``InvalidRequestError: Instance is not persistent``.
        """
        async with db.get_session_context() as first:
            product = Product(name="apple", category="fruit")
            first.add(product)
            await first.commit()
        # ``first`` is closed here → ``product`` is detached.
        assert inspect(product).detached is True

        async with db.get_session_context() as second:
            repo = ProductRepository(second)
            resolved = await repo.resolve(product)

            assert inspect(resolved).detached is False
            resolved.category = "pomes"
            updated = await repo.update(resolved)
            assert updated.category == "pomes"

        async with db.get_session_context() as verify:
            reloaded = await verify.get(Product, product.id)
            assert reloaded is not None
            assert reloaded.category == "pomes"


class TestGetOrNone:
    async def test_returns_none_when_absent(self, repo: ProductRepository) -> None:
        assert await repo.get_or_none({"name": "ghost"}) is None

    async def test_returns_row_when_present(self, repo: ProductRepository) -> None:
        product = await repo.add(Product(name="apple", category="fruit"))
        loaded = await repo.get_or_none({"id": product.id})
        assert loaded is not None
        assert loaded.name == "apple"


class TestGetById:
    async def test_returns_row(self, repo: ProductRepository) -> None:
        product = await repo.add(Product(name="apple", category="fruit"))
        loaded = await repo.get_by_id(product.id)
        assert loaded.name == "apple"

    async def test_raises_when_missing(self, repo: ProductRepository) -> None:
        from uuid import uuid4

        with pytest.raises(ProductNotFoundError):
            await repo.get_by_id(uuid4())


class TestFirst:
    async def test_returns_none_when_empty(self, repo: ProductRepository) -> None:
        assert await repo.first() is None

    async def test_returns_first_ordered(self, repo: ProductRepository) -> None:
        await repo.add_all(
            [
                Product(name="b", category="x"),
                Product(name="a", category="x"),
                Product(name="c", category="x"),
            ]
        )
        result = await repo.first(order_by=Product.name)
        assert result is not None
        assert result.name == "a"

    async def test_respects_filters(self, repo: ProductRepository) -> None:
        await repo.add_all(
            [
                Product(name="a", category="x"),
                Product(name="b", category="y"),
            ]
        )
        result = await repo.first(filters={"category": "y"})
        assert result is not None
        assert result.category == "y"


class TestSoftDeleteAndRestore:
    async def test_soft_delete_flips_is_active(self, repo: ProductRepository) -> None:
        product = await repo.add(Product(name="apple", category="fruit"))
        updated = await repo.soft_delete(product.id)
        assert updated.is_active is False

    async def test_restore_reactivates(self, repo: ProductRepository) -> None:
        product = await repo.add(Product(name="apple", category="fruit"))
        await repo.soft_delete(product.id)
        restored = await repo.restore(product.id)
        assert restored.is_active is True

    async def test_soft_delete_missing_raises(self, repo: ProductRepository) -> None:
        from uuid import uuid4

        with pytest.raises(ProductNotFoundError):
            await repo.soft_delete(uuid4())


class TestBulkUpdate:
    async def test_bulk_update_affects_matching_rows(
        self, repo: ProductRepository
    ) -> None:
        await repo.add_all(
            [
                Product(name="a", category="x"),
                Product(name="b", category="x"),
                Product(name="c", category="y"),
            ]
        )
        affected = await repo.bulk_update(
            filters={"category": "x"},
            values={"category": "z"},
        )
        assert affected == 2
        remaining_x = await repo.list(filters={"category": "x"})
        assert remaining_x == []
        rows_z = await repo.list(filters={"category": "z"})
        assert {p.name for p in rows_z} == {"a", "b"}

    async def test_bulk_update_rejects_empty_filters(
        self, repo: ProductRepository
    ) -> None:
        with pytest.raises(ValueError):
            await repo.bulk_update(filters={}, values={"category": "z"})


class TestCustomMessages:
    async def test_default_not_found_message(self, session: AsyncSession) -> None:
        repo = ProductRepository(session)
        with pytest.raises(ProductNotFoundError) as excinfo:
            await repo.get({"name": "ghost"})
        assert excinfo.value.detail == "Product not found"

    async def test_override_not_found_message(self, session: AsyncSession) -> None:
        repo = ProductRepository(
            session,
            not_found_message="produto não localizado",
        )
        with pytest.raises(ProductNotFoundError) as excinfo:
            await repo.get({"name": "ghost"})
        assert excinfo.value.detail == "produto não localizado"

    async def test_override_create_conflict_message(
        self, session: AsyncSession
    ) -> None:
        repo = ProductRepository(
            session,
            create_conflict_message="já existe um produto com esses dados",
        )
        await repo.add(Product(name="apple", category="fruit"))
        with pytest.raises(ConflictException) as excinfo:
            await repo.add(Product(name="apple", category="fruit"))
        assert excinfo.value.detail == "já existe um produto com esses dados"


class TestMappers:
    def test_map_to_schema_must_be_overridden(self, repo: ProductRepository) -> None:
        with pytest.raises(NotImplementedError):
            repo.map_to_schema(Product(name="a", category="b"))

    def test_map_to_response_must_be_overridden(self, repo: ProductRepository) -> None:
        with pytest.raises(NotImplementedError):
            repo.map_to_response(Product(name="a", category="b"))

    def test_map_to_model_default(self, repo: ProductRepository) -> None:
        result = repo.map_to_model({"name": "a", "category": "b"})
        assert isinstance(result, Product)
        assert result.name == "a"


class SyncItem(BaseModel, SoftDeleteMixin):
    __tablename__ = "sync_item_for_repo_test"

    name: Mapped[str] = mapped_column(String(64), nullable=False)
    value: Mapped[int] = mapped_column(Integer, nullable=False, default=0)


class SyncItemRepository(BaseRepository[SyncItem]):
    """Plain repository over a soft-deletable, numeric-valued model."""

    def __init__(self, session: AsyncSession) -> None:
        super().__init__(session, model=SyncItem)


@pytest.fixture
def sync_repo(session: AsyncSession) -> SyncItemRepository:
    return SyncItemRepository(session)


# A watermark safely before/after any row written during a test run.
_PAST = datetime(2000, 1, 1, tzinfo=UTC)
_FUTURE = datetime(2999, 1, 1, tzinfo=UTC)


class TestComparisonOperators:
    async def test_gt(self, sync_repo: SyncItemRepository) -> None:
        await sync_repo.add_all([SyncItem(name=f"i{n}", value=n) for n in (1, 2, 3)])
        result = await sync_repo.list(filters={"value__gt": 1})
        assert sorted(r.value for r in result) == [2, 3]

    async def test_gte(self, sync_repo: SyncItemRepository) -> None:
        await sync_repo.add_all([SyncItem(name=f"i{n}", value=n) for n in (1, 2, 3)])
        result = await sync_repo.list(filters={"value__gte": 2})
        assert sorted(r.value for r in result) == [2, 3]

    async def test_lt_and_lte(self, sync_repo: SyncItemRepository) -> None:
        await sync_repo.add_all([SyncItem(name=f"i{n}", value=n) for n in (1, 2, 3)])
        assert sorted(
            r.value for r in await sync_repo.list(filters={"value__lt": 3})
        ) == [1, 2]
        assert sorted(
            r.value for r in await sync_repo.list(filters={"value__lte": 2})
        ) == [1, 2]

    async def test_ne(self, sync_repo: SyncItemRepository) -> None:
        await sync_repo.add_all([SyncItem(name=f"i{n}", value=n) for n in (1, 2, 3)])
        result = await sync_repo.list(filters={"value__ne": 2})
        assert sorted(r.value for r in result) == [1, 3]

    async def test_none_value_skips_operator(
        self, sync_repo: SyncItemRepository
    ) -> None:
        await sync_repo.add_all([SyncItem(name=f"i{n}", value=n) for n in (1, 2, 3)])
        result = await sync_repo.list(filters={"value__gt": None})
        assert len(result) == 3

    async def test_unknown_op_suffix_is_ignored(
        self, sync_repo: SyncItemRepository
    ) -> None:
        # "value__wat" is not a recognized operator and not a real
        # column, so the condition is dropped rather than crashing.
        await sync_repo.add(SyncItem(name="a", value=1))
        result = await sync_repo.list(filters={"value__wat": 1})
        assert len(result) == 1


class TestCursorPaginateCustomQuery:
    async def test_custom_query_is_respected(
        self, sync_repo: SyncItemRepository
    ) -> None:
        from sqlalchemy import select

        await sync_repo.add_all([SyncItem(name=f"i{n}", value=n) for n in (1, 2, 3, 4)])
        query = select(SyncItem).where(SyncItem.value >= 3)
        page = await sync_repo.cursor_paginate(
            order_by="value", ascending=True, query=query
        )
        assert [r.value for r in page["items"]] == [3, 4]


class TestChangesSince:
    async def test_full_sync_returns_all_with_server_time(
        self, sync_repo: SyncItemRepository
    ) -> None:
        await sync_repo.add_all([SyncItem(name=f"i{n}", value=n) for n in (1, 2, 3)])
        page = await sync_repo.changes_since(None)
        assert len(page["items"]) == 3
        assert isinstance(page["server_time"], datetime)

    async def test_since_future_returns_nothing(
        self, sync_repo: SyncItemRepository
    ) -> None:
        await sync_repo.add(SyncItem(name="a", value=1))
        page = await sync_repo.changes_since(_FUTURE)
        assert page["items"] == []

    async def test_since_past_returns_all(self, sync_repo: SyncItemRepository) -> None:
        await sync_repo.add_all(
            [SyncItem(name="a", value=1), SyncItem(name="b", value=2)]
        )
        page = await sync_repo.changes_since(_PAST)
        assert len(page["items"]) == 2

    async def test_filters_scope_the_delta(self, sync_repo: SyncItemRepository) -> None:
        await sync_repo.add_all(
            [SyncItem(name="keep", value=1), SyncItem(name="drop", value=2)]
        )
        page = await sync_repo.changes_since(_PAST, filters={"name": "keep"})
        assert [r.name for r in page["items"]] == ["keep"]

    async def test_include_deleted_returns_tombstones(
        self, sync_repo: SyncItemRepository
    ) -> None:
        alive = await sync_repo.add(SyncItem(name="alive", value=1))
        gone = await sync_repo.add(SyncItem(name="gone", value=2))
        gone.mark_deleted()
        await sync_repo.update(gone)

        page = await sync_repo.changes_since(_PAST, include_deleted=True)
        ids = {r.id for r in page["items"]}
        assert ids == {alive.id, gone.id}

    async def test_exclude_deleted_hides_tombstones(
        self, sync_repo: SyncItemRepository
    ) -> None:
        alive = await sync_repo.add(SyncItem(name="alive", value=1))
        gone = await sync_repo.add(SyncItem(name="gone", value=2))
        gone.mark_deleted()
        await sync_repo.update(gone)

        page = await sync_repo.changes_since(_PAST, include_deleted=False)
        ids = {r.id for r in page["items"]}
        assert ids == {alive.id}

    async def test_pagination_drains_via_cursor(
        self, sync_repo: SyncItemRepository
    ) -> None:
        await sync_repo.add_all([SyncItem(name=f"i{n}", value=n) for n in range(5)])
        first = await sync_repo.changes_since(_PAST, limit=2)
        assert first["has_more"] is True
        assert len(first["items"]) == 2

        seen = list(first["items"])
        cursor = first["next_cursor"]
        while cursor is not None:
            page = await sync_repo.changes_since(_PAST, limit=2, cursor=cursor)
            seen.extend(page["items"])
            cursor = page["next_cursor"]
        assert len({r.id for r in seen}) == 5
