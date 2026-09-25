"""Pagination request and response primitives."""

import base64
import copy
import json
from datetime import datetime
from typing import Any, ClassVar, Generic, TypeVar

from annotated_types import Le
from pydantic import BaseModel, ConfigDict, Field, field_validator

from tempest_fastapi_sdk.exceptions.validation import OrderByNotAllowedException
from tempest_fastapi_sdk.schemas.base import BaseSchema

DEFAULT_MAX_PAGE_SIZE: int = 100
"""Default ceiling on ``page_size`` for :class:`BasePaginationFilterSchema`.

Without one, a single request reads the whole table — and pays the cost
of mapping (and, often, presigning) every row on the way out.
"""

DEFAULT_MAX_CURSOR_LIMIT: int = 500
"""Default ceiling on ``limit`` for :class:`CursorPaginationFilterSchema`.

The value the field always carried as ``le=500``; now overridable.
"""


def _retarget_upper_bound(
    model: type[BaseModel],
    field_name: str,
    ceiling: int | None,
) -> None:
    """Replace the ``le`` bound of one field on an already-built model.

    Pydantic freezes a field's constraints when the class is built, so a
    ``ClassVar`` ceiling declared on a subclass cannot reach ``le=`` by
    itself. This swaps the ``Le`` marker on a copy of the inherited
    :class:`~pydantic.fields.FieldInfo` and rebuilds the model, which is
    what keeps the bound visible where a plain validator would not be:
    in the 422 (``less_than_equal``, same ``type`` and ``ctx`` as a
    literal ``le=``) and in the OpenAPI ``maximum`` FastAPI publishes for
    the query parameter.

    Args:
        model (type[BaseModel]): The subclass being initialized.
        field_name (str): The field whose upper bound changes.
        ceiling (int | None): The new inclusive ceiling. ``None`` removes
            the upper bound.
    """
    field = model.__pydantic_fields__[field_name]
    retargeted = copy.copy(field)
    retargeted.metadata = [m for m in field.metadata if not isinstance(m, Le)]
    if ceiling is not None:
        retargeted.metadata.append(Le(ceiling))
    model.__pydantic_fields__[field_name] = retargeted
    model.model_rebuild(force=True)


class BasePaginationFilterSchema(BaseSchema):
    """Base filter schema for paginated list endpoints.

    Subclass it to add domain-specific filter fields. The base
    ``get_conditions`` method returns every populated field except
    the pagination/sort keys, which is the contract expected by
    :class:`tempest_fastapi_sdk.db.repository.BaseRepository.paginate`.

    Field names and defaults mirror the ``BaseRepository.paginate``
    keyword arguments so passing the schema straight through works
    without renaming:

    .. code-block:: python

        result = await repo.paginate(
            filters=f.get_conditions(),
            order_by=f.order_by,
            page=f.page,
            page_size=f.page_size,
            ascending=f.ascending,
        )

    Attributes:
        page (int): The page number to retrieve (1-indexed).
        page_size (int): The number of items per page.
        order_by (str | None): The column name to order by. ``None``
            falls back to the repository default (``created_at``
            descending).
        ascending (bool): Whether to order ascending. Ignored when
            ``order_by`` is ``None``.
        is_active (bool | None): Filter by active status. ``None``
            returns both active and inactive rows.
        orderable_columns (ClassVar[frozenset[str] | None]): The columns
            ``order_by`` accepts. ``None`` (the default) accepts any value
            and leaves the check to the repository; a declared set turns
            every other value into :class:`OrderByNotAllowedException`
            (422, ``details["allowed"]`` listing only this set). An empty
            ``order_by`` means "absent" and is never refused.
        max_page_size (ClassVar[int | None]): Inclusive ceiling on
            ``page_size`` (default :data:`DEFAULT_MAX_PAGE_SIZE`).
            Declaring it on a subclass rewrites the field's ``le=`` bound,
            so the refusal is pydantic's own ``less_than_equal`` and the
            OpenAPI parameter shows the ``maximum``. ``None`` removes the
            ceiling. A subclass that redeclares ``page_size`` without
            declaring ``max_page_size`` keeps the bound it wrote.

    Declaring both on a listing:

    .. code-block:: python

        class ProducerFilterSchema(BasePaginationFilterSchema):
            orderable_columns = frozenset({"created_at", "name"})
            max_page_size = 50

    ``populate_by_name`` is on, so a subclass may rename a field on the
    wire by redeclaring it with an alias and still construct the model
    by its Python name. See :class:`CompactPaginationFilterSchema` for
    the ready-made case.
    """

    model_config = ConfigDict(populate_by_name=True)

    orderable_columns: ClassVar[frozenset[str] | None] = None
    max_page_size: ClassVar[int | None] = DEFAULT_MAX_PAGE_SIZE

    page: int = Field(
        title="Page Number",
        description="The page number to retrieve (1-indexed).",
        examples=[1, 2, 3],
        default=1,
        ge=1,
    )
    page_size: int = Field(
        title="Page Size",
        description="The number of items per page.",
        examples=[10, 20, 50],
        default=20,
        ge=1,
        le=DEFAULT_MAX_PAGE_SIZE,
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

    @classmethod
    def __pydantic_init_subclass__(cls, **kwargs: Any) -> None:
        """Apply a ``max_page_size`` declared on the subclass to ``page_size``.

        Args:
            **kwargs (Any): Class-creation keyword arguments, forwarded
                to ``super()``.
        """
        super().__pydantic_init_subclass__(**kwargs)
        if "max_page_size" in cls.__dict__:
            _retarget_upper_bound(cls, "page_size", cls.max_page_size)

    @field_validator("order_by")
    @classmethod
    def _reject_unlisted_order_by(cls, value: str | None) -> str | None:
        """Refuse an ``order_by`` outside ``orderable_columns``.

        Raises the SDK exception rather than a ``ValueError`` so the 422
        carries ``code="ORDER_BY_NOT_ALLOWED"`` and ``details["allowed"]``
        through :func:`~tempest_fastapi_sdk.register_exception_handlers`,
        the same envelope the repository's refusal uses.

        Args:
            value (str | None): The ``order_by`` the caller sent.

        Returns:
            str | None: The column, or ``None`` when the value was absent
            or empty.

        Raises:
            OrderByNotAllowedException: When ``orderable_columns`` is
                declared and does not contain ``value``.
        """
        if not value:
            return None
        allowed = cls.orderable_columns
        if allowed is not None and value not in allowed:
            raise OrderByNotAllowedException(value, allowed=allowed)
        return value

    def get_conditions(self) -> dict[str, Any]:
        """Return the dict of filter conditions for the repository.

        Strips the pagination and sort keys so the resulting mapping
        contains only domain-level filters consumable by
        :meth:`BaseRepository.paginate`.

        Returns:
            dict[str, Any]: The dictionary of filter conditions.
        """
        return self.to_dict(
            exclude=["page", "page_size", "order_by", "ascending"],
        )

    def get_pagination_conditions(self) -> dict[str, Any]:
        """Return only the pagination and sort keyword arguments.

        Complements :meth:`get_conditions`: where that method strips the
        pagination keys to expose the domain filters, this one keeps only
        the pagination keys (``page``, ``page_size``, ``order_by``,
        ``ascending``). Together they let a service forward a filter
        schema to :meth:`BaseRepository.paginate` without manually
        unpacking the model, which would also leak domain filters such as
        ``is_active`` into kwargs the repository does not accept:

        .. code-block:: python

            data = await repo.paginate(
                filters=f.get_conditions(),
                **f.get_pagination_conditions(),
            )

        Returns:
            dict[str, Any]: The pagination/sort keyword arguments.
        """
        return {
            "page": self.page,
            "page_size": self.page_size,
            "order_by": self.order_by,
            "ascending": self.ascending,
        }


T = TypeVar("T", bound=BaseSchema)


class BasePaginationSchema(BaseSchema, Generic[T]):
    """Generic envelope returned by paginated endpoints.

    Wraps the page of items together with the pagination metadata
    the frontend needs to render controls. Field names match the
    request-side :class:`BasePaginationFilterSchema` and the
    repository keyword arguments, so the round-trip stays free of
    renames.

    Attributes:
        items (list[T]): The items in the current page.
        total (int): The total number of items across all pages.
        page (int): The current page number (1-indexed).
        page_size (int): The number of items per page.
        pages (int): The total number of pages.

    ``populate_by_name`` is on, so a subclass may rename a field on the
    wire by redeclaring it with an alias and still construct the model
    by its Python name. See :class:`CompactPaginationSchema` for the
    ready-made case.

    The repository is unaffected either way:
    :meth:`tempest_fastapi_sdk.db.repository.BaseRepository.paginate`
    returns ``{"items", "total", "page", "page_size", "pages"}`` whatever
    the envelope publishes, because the rename lives in the schema and
    nowhere else.
    """

    model_config = ConfigDict(populate_by_name=True)

    items: list[T] = Field(
        title="Items",
        description="The items on the current page.",
        examples=[[], [{"id": 1}, {"id": 2}]],
        default_factory=list,
    )
    total: int = Field(
        title="Total Items",
        description="The total number of items across all pages.",
        examples=[0, 100, 250],
        ge=0,
    )
    page: int = Field(
        title="Page Number",
        description="The current page number (1-indexed).",
        examples=[1, 2],
        ge=1,
    )
    page_size: int = Field(
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


class CompactPaginationFilterSchema(BasePaginationFilterSchema):
    """Filter that reads the page size from ``size`` on the wire.

    Same fields and same defaults as
    :class:`BasePaginationFilterSchema`; only the query parameter is
    spelled differently. Use it when a service already published
    ``?size=`` and cannot rename it — an app in a store cannot be asked
    to update in lockstep with the backend.

    The Python name stays ``page_size``, so
    :meth:`BaseRepository.paginate` still takes it without a rename, and
    switching a service between the two envelopes touches the schema and
    nothing else.

    .. code-block:: python

        from fastapi import Depends

        from tempest_fastapi_sdk import CompactPaginationFilterSchema

        # GET /users?size=50
        filters = CompactPaginationFilterSchema.model_validate({"size": 50})
        filters.page_size   # 50

    Attributes:
        page_size (int): Items per page, read from ``size``.
    """

    page_size: int = Field(
        title="Page Size",
        description="The number of items per page.",
        examples=[10, 20, 50],
        default=20,
        ge=1,
        le=DEFAULT_MAX_PAGE_SIZE,
        validation_alias="size",
        serialization_alias="size",
    )


class CompactPaginationSchema(BasePaginationSchema[T], Generic[T]):
    """Envelope that publishes the page size as ``size``.

    The counterpart of :class:`CompactPaginationFilterSchema` on the way
    out: ``{"items", "total", "page", "size", "pages"}``. Adopting the
    SDK's envelope in a service that already published that shape is
    otherwise a break on every paginated endpoint at once.

    FastAPI serialises a response model with ``by_alias=True``, so
    declaring this as ``response_model`` is all it takes. A direct
    ``model_dump()`` still answers ``page_size`` — pass
    ``by_alias=True`` to see the wire name.

    .. code-block:: python

        from tempest_fastapi_sdk import CompactPaginationSchema

        page = CompactPaginationSchema[int](
            items=[1], total=1, page=1, page_size=20, pages=1
        )
        page.model_dump(by_alias=True)["size"]   # 20

    Attributes:
        page_size (int): Items per page, published as ``size``.
    """

    page_size: int = Field(
        title="Page Size",
        description="The number of items per page.",
        examples=[10, 25],
        ge=1,
        validation_alias="size",
        serialization_alias="size",
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
        orderable_columns (ClassVar[frozenset[str] | None]): The columns
            ``order_by`` accepts; same contract as
            :attr:`BasePaginationFilterSchema.orderable_columns`. Include
            the default (``created_at``) when you declare it — a field
            default is not re-validated, so the repository is what would
            refuse it.
        max_limit (ClassVar[int | None]): Inclusive ceiling on ``limit``
            (default :data:`DEFAULT_MAX_CURSOR_LIMIT`), rewritten into the
            field's ``le=`` the same way ``max_page_size`` is.
    """

    orderable_columns: ClassVar[frozenset[str] | None] = None
    max_limit: ClassVar[int | None] = DEFAULT_MAX_CURSOR_LIMIT

    cursor: str | None = Field(
        title="Cursor",
        description=(
            "Opaque pagination cursor from the previous page; "
            "None requests the first page."
        ),
        examples=[None, "eyJpZCI6IjEyMyIsInZhbHVlIjoxNzM3In0"],
        default=None,
    )
    limit: int = Field(
        title="Limit",
        description="Maximum number of items to return.",
        examples=[10, 25, 100],
        default=20,
        ge=1,
        le=DEFAULT_MAX_CURSOR_LIMIT,
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

    @classmethod
    def __pydantic_init_subclass__(cls, **kwargs: Any) -> None:
        """Apply a ``max_limit`` declared on the subclass to ``limit``.

        Args:
            **kwargs (Any): Class-creation keyword arguments, forwarded
                to ``super()``.
        """
        super().__pydantic_init_subclass__(**kwargs)
        if "max_limit" in cls.__dict__:
            _retarget_upper_bound(cls, "limit", cls.max_limit)

    @field_validator("order_by")
    @classmethod
    def _reject_unlisted_order_by(cls, value: str) -> str:
        """Refuse an ``order_by`` outside ``orderable_columns``.

        Args:
            value (str): The ``order_by`` the caller sent.

        Returns:
            str: The column, unchanged.

        Raises:
            OrderByNotAllowedException: When ``orderable_columns`` is
                declared and does not contain ``value``.
        """
        allowed = cls.orderable_columns
        if allowed is not None and value not in allowed:
            raise OrderByNotAllowedException(value, allowed=allowed)
        return value

    def get_conditions(self) -> dict[str, Any]:
        """Return only the domain-level filter conditions.

        Returns:
            dict[str, Any]: The filters with pagination/sort keys
            stripped.
        """
        return self.to_dict(
            exclude=["cursor", "limit", "order_by", "ascending"],
        )

    def get_pagination_conditions(self) -> dict[str, Any]:
        """Return only the cursor pagination and sort keyword arguments.

        Complements :meth:`get_conditions`: where that method strips the
        pagination keys to expose the domain filters, this one keeps only
        the pagination keys (``cursor``, ``limit``, ``order_by``,
        ``ascending``). Together they let a service forward a filter
        schema to :meth:`BaseRepository.cursor_paginate` without manually
        unpacking the model, which would also leak domain filters into
        kwargs the repository does not accept:

        .. code-block:: python

            data = await repo.cursor_paginate(
                filters=f.get_conditions(),
                **f.get_pagination_conditions(),
            )

        Returns:
            dict[str, Any]: The cursor pagination/sort keyword arguments.
        """
        return {
            "cursor": self.cursor,
            "limit": self.limit,
            "order_by": self.order_by,
            "ascending": self.ascending,
        }


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
        examples=[[], [{"id": 1}, {"id": 2}]],
        default_factory=list,
    )
    next_cursor: str | None = Field(
        title="Next Cursor",
        description="Cursor for the next page, or None when exhausted.",
        examples=[None, "eyJpZCI6IjEyMyJ9"],
        default=None,
    )
    has_more: bool = Field(
        title="Has More",
        description="Whether another page is available.",
        examples=[True, False],
        default=False,
    )
    limit: int = Field(
        title="Limit",
        description="The page size used.",
        examples=[10, 25, 100],
        ge=1,
    )


class SyncFilterSchema(BaseSchema):
    """Request filter for delta-sync (offline-first) pull endpoints.

    Mirrors the keyword arguments of
    :meth:`tempest_fastapi_sdk.db.repository.BaseRepository.changes_since`
    so the schema passes straight through. An offline client persists
    the ``server_time`` from the previous :class:`SyncPaginationSchema`
    response and sends it back here as ``since`` on the next pull.

    Attributes:
        since (datetime | None): High-water mark. Only rows changed
            strictly after this instant are returned. ``None`` requests
            a full sync (every row).
        cursor (str | None): Opaque cursor from the previous page;
            ``None`` requests the first page.
        limit (int): Maximum number of items to return.
        include_deleted (bool): Whether soft-deleted rows are returned
            as tombstones. Defaults to ``True`` so deletions propagate
            to the client.
    """

    since: datetime | None = Field(
        title="Since",
        description=(
            "High-water mark; only rows changed strictly after this "
            "instant are returned. None requests a full sync."
        ),
        examples=[None, "2026-06-12T18:30:00Z"],
        default=None,
    )
    cursor: str | None = Field(
        title="Cursor",
        description=(
            "Opaque pagination cursor from the previous page; "
            "None requests the first page."
        ),
        examples=[None, "eyJpZCI6IjEyMyIsInZhbHVlIjoxNzM3In0"],
        default=None,
    )
    limit: int = Field(
        title="Limit",
        description="Maximum number of items to return.",
        examples=[25, 50, 100],
        default=50,
        ge=1,
        le=500,
    )
    include_deleted: bool = Field(
        title="Include Deleted",
        description=(
            "Whether soft-deleted rows are returned as tombstones so "
            "the client can mirror deletions. Defaults to True."
        ),
        examples=[True, False],
        default=True,
    )


class SyncPaginationSchema(BaseSchema, Generic[T]):
    """Generic envelope returned by delta-sync pull endpoints.

    Extends the cursor-pagination envelope with ``server_time`` — the
    instant the server started the query, which the client persists as
    the next :class:`SyncFilterSchema.since`. Using the server clock
    (not the client's, nor the max ``updated_at`` of the items) is what
    keeps the watermark immune to device clock skew.

    Attributes:
        items (list[T]): The changed rows in this page (oldest change
            first), including soft-deleted tombstones when requested.
        next_cursor (str | None): Cursor to request the next page, or
            ``None`` when this page drained the changes.
        has_more (bool): Whether another page is available.
        limit (int): The page size used to produce this payload.
        server_time (datetime): The server instant to persist as the
            next ``since``.
    """

    items: list[T] = Field(
        title="Items",
        description="The changed rows on the current page.",
        examples=[[], [{"id": 1}, {"id": 2}]],
        default_factory=list,
    )
    next_cursor: str | None = Field(
        title="Next Cursor",
        description="Cursor for the next page, or None when exhausted.",
        examples=[None, "eyJpZCI6IjEyMyJ9"],
        default=None,
    )
    has_more: bool = Field(
        title="Has More",
        description="Whether another page is available.",
        examples=[True, False],
        default=False,
    )
    limit: int = Field(
        title="Limit",
        description="The page size used.",
        examples=[25, 50, 100],
        ge=1,
    )
    server_time: datetime = Field(
        title="Server Time",
        description=(
            "The server instant the query started; persist it as the "
            "next 'since' watermark."
        ),
        examples=["2026-06-12T18:30:00Z"],
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
    "DEFAULT_MAX_CURSOR_LIMIT",
    "DEFAULT_MAX_PAGE_SIZE",
    "BasePaginationFilterSchema",
    "BasePaginationSchema",
    "CursorPaginationFilterSchema",
    "CursorPaginationSchema",
    "SyncFilterSchema",
    "SyncPaginationSchema",
    "decode_cursor",
    "encode_cursor",
]
