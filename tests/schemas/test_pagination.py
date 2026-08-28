"""Tests for the pagination primitives."""

from pydantic import Field

from tempest_fastapi_sdk.schemas import (
    BasePaginationFilterSchema,
    BasePaginationSchema,
    BaseSchema,
    CompactPaginationFilterSchema,
    CompactPaginationSchema,
)


class Item(BaseSchema):
    name: str


class TestBasePaginationFilterSchema:
    def test_defaults(self) -> None:
        result = BasePaginationFilterSchema()
        assert result.page == 1
        assert result.page_size == 20
        assert result.order_by is None
        assert result.ascending is True
        assert result.is_active is None

    def test_get_conditions_strips_pagination_keys(self) -> None:
        result = BasePaginationFilterSchema(
            page=2,
            page_size=50,
            order_by="name",
            ascending=False,
            is_active=True,
        )
        conditions = result.get_conditions()
        assert "page" not in conditions
        assert "page_size" not in conditions
        assert "order_by" not in conditions
        assert "ascending" not in conditions
        assert conditions["is_active"] is True

    def test_get_pagination_conditions_keeps_only_pagination_keys(self) -> None:
        result = BasePaginationFilterSchema(
            page=2,
            page_size=50,
            order_by="name",
            ascending=False,
            is_active=True,
        )
        pagination = result.get_pagination_conditions()
        assert pagination == {
            "page": 2,
            "page_size": 50,
            "order_by": "name",
            "ascending": False,
        }
        assert "is_active" not in pagination


class TestBasePaginationSchema:
    def test_empty_page(self) -> None:
        result = BasePaginationSchema[Item](total=0, page=1, page_size=10, pages=0)
        assert result.items == []

    def test_carries_metadata(self) -> None:
        result = BasePaginationSchema[Item](
            items=[Item(name="a")], total=1, page=1, page_size=10, pages=1
        )
        assert result.items[0].name == "a"
        assert result.total == 1
        assert result.pages == 1
        assert result.page_size == 10


class TestTheWireNameOfThePageSize:
    """Issue #209: a service that already published `size` cannot rename it.

    Adopting the SDK's envelope would otherwise break every paginated
    endpoint at once — and an app already in a store cannot be asked to
    update in lockstep with its backend.
    """

    def test_the_default_envelope_still_publishes_page_size(self) -> None:
        """The rename is opt-in; nothing moves for anyone who did not ask."""
        page = BasePaginationSchema[int](
            items=[1], total=1, page=1, page_size=20, pages=1
        )

        assert page.model_dump(by_alias=True)["page_size"] == 20

    def test_the_compact_envelope_publishes_size(self) -> None:
        """FastAPI serialises a response model with `by_alias=True`."""
        page = CompactPaginationSchema[int](
            items=[1], total=1, page=1, page_size=20, pages=1
        )

        dumped = page.model_dump(by_alias=True)

        assert dumped["size"] == 20
        assert "page_size" not in dumped

    def test_the_python_name_is_unchanged(self) -> None:
        """So `BaseRepository.paginate` still takes it without a rename.

        The whole point of aliasing rather than renaming the field: the
        repository keeps answering `page_size`, and the conversion lives
        in the schema and nowhere else.
        """
        page = CompactPaginationSchema[int](
            items=[1], total=1, page=1, page_size=20, pages=1
        )

        assert page.page_size == 20
        assert page.model_dump()["page_size"] == 20

    def test_the_compact_filter_reads_size(self) -> None:
        """`GET /users?size=50`."""
        filters = CompactPaginationFilterSchema.model_validate({"size": 50})

        assert filters.page_size == 50

    def test_the_compact_filter_still_accepts_the_python_name(self) -> None:
        """`populate_by_name` keeps construction from code working."""
        filters = CompactPaginationFilterSchema(page_size=50)

        assert filters.page_size == 50

    def test_a_consumer_can_alias_without_losing_the_base_config(self) -> None:
        """The cheap path the issue asked for, on the base classes.

        `populate_by_name` is on for both bases, so a subclass may
        redeclare one field with an alias and still be constructed by its
        Python name — which is what makes the override usable at all.
        """

        class MyPage(BasePaginationSchema[int]):
            """A consumer's own envelope, renaming only the page size."""

            page_size: int = Field(
                validation_alias="perPage",
                serialization_alias="perPage",
            )

        page = MyPage(items=[1], total=1, page=1, page_size=20, pages=1)

        assert page.model_dump(by_alias=True)["perPage"] == 20
        assert MyPage.model_config["extra"] == "ignore"
