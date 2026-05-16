"""Pagination request and response primitives."""

from typing import Any, Generic, TypeVar

from pydantic import Field

from tempest_fastapi_sdk.schemas.base import BaseSchema


class BasePaginationFilterSchema(BaseSchema):
    """Base filter schema for paginated list endpoints.

    Subclass it to add domain-specific filter fields. The base
    ``get_conditions`` method returns every populated field except
    the pagination/sort keys, which is the contract expected by
    :class:`tempest_fastapi_sdk.db.repository.BaseRepository.paginate`.

    Attributes:
        page (int): The page number to retrieve (1-indexed).
        size (int): The number of items per page.
        order_by (str | None): The column name to order by. ``None``
            falls back to the repository default (``created_at``
            descending).
        ascending (bool): Whether to order ascending. Ignored when
            ``order_by`` is ``None``.
        is_active (bool | None): Filter by active status. ``None``
            returns both active and inactive rows.
    """

    page: int = Field(
        title="Page Number",
        description="The page number to retrieve (1-indexed).",
        examples=[1, 2, 3],
        default=1,
        ge=1,
    )
    size: int = Field(
        title="Page Size",
        description="The number of items per page.",
        examples=[10, 20, 50],
        default=10,
        ge=1,
    )
    order_by: str | None = Field(
        title="Order By",
        description=(
            "The column name to order by. If None, falls back to the "
            "repository default (created_at descending)."
        ),
        examples=["created_at", "name", None],
        default=None,
    )
    ascending: bool = Field(
        title="Ascending Order",
        description="Whether to order results ascending.",
        examples=[True, False],
        default=True,
    )
    is_active: bool | None = Field(
        title="Is Active",
        description="Filter by active status. None returns both.",
        examples=[True, False, None],
        default=None,
    )

    def get_conditions(self) -> dict[str, Any]:
        """Return the dict of filter conditions for the repository.

        Strips the pagination and sort keys so the resulting mapping
        contains only domain-level filters consumable by
        :meth:`BaseRepository.paginate`.

        Returns:
            dict[str, Any]: The dictionary of filter conditions.
        """
        return self.to_dict(
            exclude=["page", "size", "order_by", "ascending"],
        )


T = TypeVar("T", bound=BaseSchema)


class BasePaginationSchema(BaseSchema, Generic[T]):
    """Generic envelope returned by paginated endpoints.

    Wraps the page of items together with the pagination metadata
    the frontend needs to render controls.

    Attributes:
        items (list[T]): The items in the current page.
        total (int): The total number of items across all pages.
        page (int): The current page number (1-indexed).
        size (int): The number of items per page.
        pages (int): The total number of pages.
    """

    items: list[T] = Field(
        title="Items",
        description="The items on the current page.",
        default_factory=list,
    )
    total: int = Field(
        title="Total Items",
        description="The total number of items across all pages.",
        examples=[100, 250],
        ge=0,
    )
    page: int = Field(
        title="Page Number",
        description="The current page number (1-indexed).",
        examples=[1, 2],
        ge=1,
    )
    size: int = Field(
        title="Page Size",
        description="The number of items per page.",
        examples=[10, 25],
        ge=1,
    )
    pages: int = Field(
        title="Total Pages",
        description="The total number of pages available.",
        examples=[10, 25],
        ge=0,
    )


__all__: list[str] = [
    "BasePaginationFilterSchema",
    "BasePaginationSchema",
]
