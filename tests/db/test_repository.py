"""Tests for tempest_fastapi_sdk.db.repository.BaseRepository."""

import pytest
from sqlalchemy import String
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import Mapped, mapped_column

from tempest_fastapi_sdk import (
    BaseModel,
    BaseRepository,
    ConflictException,
    NotFoundException,
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
