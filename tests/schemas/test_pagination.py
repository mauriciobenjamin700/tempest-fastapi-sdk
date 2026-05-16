"""Tests for the pagination primitives."""

from tempest_fastapi_sdk.schemas import (
    BasePaginationFilterSchema,
    BasePaginationSchema,
    BaseSchema,
)


class Item(BaseSchema):
    name: str


class TestBasePaginationFilterSchema:
    def test_defaults(self) -> None:
        result = BasePaginationFilterSchema()
        assert result.page == 1
        assert result.size == 10
        assert result.order_by is None
        assert result.ascending is True
        assert result.is_active is None

    def test_get_conditions_strips_pagination_keys(self) -> None:
        result = BasePaginationFilterSchema(
            page=2, size=50, order_by="name", ascending=False, is_active=True
        )
        conditions = result.get_conditions()
        assert "page" not in conditions
        assert "size" not in conditions
        assert "order_by" not in conditions
        assert "ascending" not in conditions
        assert conditions["is_active"] is True


class TestBasePaginationSchema:
    def test_empty_page(self) -> None:
        result = BasePaginationSchema[Item](total=0, page=1, size=10, pages=0)
        assert result.items == []

    def test_carries_metadata(self) -> None:
        result = BasePaginationSchema[Item](
            items=[Item(name="a")], total=1, page=1, size=10, pages=1
        )
        assert result.items[0].name == "a"
        assert result.total == 1
        assert result.pages == 1
