"""Pagination request and response primitives."""

import base64
import json
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


class CursorPaginationFilterSchema(BaseSchema):
    """Request filter for cursor-based pagination endpoints.

    Cursor pagination scales better than offset pagination on large
    tables (no ``COUNT(*)``, stable under concurrent inserts) at the
    cost of losing random-access semantics. Subclass to add domain
    filters; :meth:`get_conditions` strips the cursor/sort keys
    automatically.

    Attributes:
        cursor (str | None): Opaque cursor returned by the previous
            page. ``None`` requests the first page.
        limit (int): Maximum number of items to return.
        order_by (str): Column to sort by. Must be a sortable column
            with a stable secondary tie-break (``id`` is appended
            automatically by the repository).
        ascending (bool): Whether to sort ascending. Defaults to
            ``False`` so newest rows surface first.
    """

    cursor: str | None = Field(
        title="Cursor",
        description=(
            "Opaque pagination cursor from the previous page; "
            "None requests the first page."
        ),
        default=None,
    )
    limit: int = Field(
        title="Limit",
        description="Maximum number of items to return.",
        examples=[10, 25, 100],
        default=20,
        ge=1,
        le=500,
    )
    order_by: str = Field(
        title="Order By",
        description="Column to sort by.",
        examples=["created_at", "updated_at"],
        default="created_at",
    )
    ascending: bool = Field(
        title="Ascending Order",
        description=(
            "Whether to sort ascending. Defaults to False so newest "
            "rows are returned first."
        ),
        default=False,
    )

    def get_conditions(self) -> dict[str, Any]:
        """Return only the domain-level filter conditions.

        Returns:
            dict[str, Any]: The filters with pagination/sort keys
            stripped.
        """
        return self.to_dict(
            exclude=["cursor", "limit", "order_by", "ascending"],
        )


class CursorPaginationSchema(BaseSchema, Generic[T]):
    """Generic envelope returned by cursor-paginated endpoints.

    Attributes:
        items (list[T]): The items in the current page.
        next_cursor (str | None): Cursor to request the next page,
            or ``None`` when no more results exist.
        has_more (bool): Whether another page is available.
        limit (int): The page size used to produce this payload.
    """

    items: list[T] = Field(
        title="Items",
        description="The items on the current page.",
        default_factory=list,
    )
    next_cursor: str | None = Field(
        title="Next Cursor",
        description="Cursor for the next page, or None when exhausted.",
        default=None,
    )
    has_more: bool = Field(
        title="Has More",
        description="Whether another page is available.",
        default=False,
    )
    limit: int = Field(
        title="Limit",
        description="The page size used.",
        examples=[10, 25, 100],
        ge=1,
    )


def encode_cursor(payload: dict[str, Any]) -> str:
    """Serialize a cursor payload to an opaque base64-url-safe string.

    Args:
        payload (dict[str, Any]): The cursor state to encode.
            Typically ``{"id": <uuid>, "value": <sort-key-value>}``.

    Returns:
        str: A URL-safe base64 string without padding.
    """
    raw = json.dumps(payload, default=str, separators=(",", ":")).encode()
    return base64.urlsafe_b64encode(raw).rstrip(b"=").decode("ascii")


def decode_cursor(cursor: str) -> dict[str, Any]:
    """Decode a cursor previously produced by :func:`encode_cursor`.

    Args:
        cursor (str): The opaque cursor string.

    Returns:
        dict[str, Any]: The decoded payload.

    Raises:
        ValueError: When ``cursor`` is not valid base64 or doesn't
            decode to a JSON object.
    """
    padding = "=" * (-len(cursor) % 4)
    try:
        raw = base64.urlsafe_b64decode(cursor + padding)
        payload = json.loads(raw)
    except (ValueError, json.JSONDecodeError) as exc:
        raise ValueError("Invalid cursor") from exc
    if not isinstance(payload, dict):
        raise ValueError("Invalid cursor payload")
    return payload


__all__: list[str] = [
    "BasePaginationFilterSchema",
    "BasePaginationSchema",
    "CursorPaginationFilterSchema",
    "CursorPaginationSchema",
    "decode_cursor",
    "encode_cursor",
]
