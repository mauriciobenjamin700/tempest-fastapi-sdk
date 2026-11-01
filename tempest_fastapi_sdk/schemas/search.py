"""Request schema for text-search endpoints.

:class:`BaseSearchSchema` is to :meth:`BaseRepository.search
<tempest_fastapi_sdk.BaseRepository.search>` what
:class:`~tempest_fastapi_sdk.BasePaginationFilterSchema` is to ``paginate``:
its fields carry the names of the repository's arguments (``q`` is the term,
``fields`` the columns), and its ``searchable_columns`` mirrors the
repository's, the way ``orderable_columns`` mirrors ``order_by``. A column
outside the declared set is refused while the request is validated, with the
same ``422`` envelope the repository raises, and the set shows in the
OpenAPI schema instead of being discovered by trial and error.
"""

from __future__ import annotations

from typing import Any, ClassVar

from pydantic import Field, JsonValue, field_validator

from tempest_fastapi_sdk.exceptions.validation import ValidationException
from tempest_fastapi_sdk.schemas.base import BaseSchema

DEFAULT_MAX_SEARCH_TERM: int = 256
"""Longest ``q`` :class:`BaseSearchSchema` accepts, in characters."""


class BaseSearchSchema(BaseSchema):
    """Base request schema for a text-search endpoint.

    Searching is an oracle on the searched column (which rows come back says
    whether each row's value contains the term), so a service that takes
    ``fields`` from the request should declare ``searchable_columns`` on the
    repository **and** here. The repository refusal is the boundary; this
    one turns it into a validation error before any query and documents the
    accepted columns.

    Declare the same set on both sides:

    .. code-block:: python

        class ProductRepository(BaseRepository[ProductModel]):
            searchable_columns = frozenset({"name", "sku"})

        class ProductSearchSchema(BaseSearchSchema):
            searchable_columns = ProductRepository.searchable_columns

        rows = await repo.search(body.q, fields=body.fields or ["name"])

    Attributes:
        q (str): The search term, 1 to :data:`DEFAULT_MAX_SEARCH_TERM`
            characters.
        fields (list[str]): Columns to search; empty lets the endpoint pick
            its default.
        searchable_columns (ClassVar[frozenset[str] | None]): The columns
            ``fields`` accepts. ``None`` (the default) leaves the check to
            the repository; a declared set refuses every other name with a
            ``ValidationException`` (422, ``details={"field": <name>,
            "allowed": [...]}``), the envelope
            :meth:`BaseRepository.search
            <tempest_fastapi_sdk.BaseRepository.search>` uses.
    """

    searchable_columns: ClassVar[frozenset[str] | None] = None

    q: str = Field(
        title="Search Term",
        description="Text to look for in the searched columns.",
        examples=["notebook"],
        min_length=1,
        max_length=DEFAULT_MAX_SEARCH_TERM,
    )
    fields: list[str] = Field(
        default_factory=list,
        title="Fields",
        description="Columns to search; empty uses the endpoint's default.",
        examples=[["name"], ["name", "sku"]],
    )

    @classmethod
    def __pydantic_init_subclass__(cls, **kwargs: Any) -> None:
        """Publish a declared ``searchable_columns`` in the OpenAPI schema.

        Args:
            **kwargs (Any): Class-creation keyword arguments, forwarded to
                ``super()``.
        """
        super().__pydantic_init_subclass__(**kwargs)
        allowed = cls.searchable_columns
        if allowed is None:
            return
        names: list[JsonValue] = [*sorted(allowed)]
        info = cls.model_fields["fields"]
        info.json_schema_extra = {"items": {"type": "string", "enum": names}}
        cls.model_rebuild(force=True)

    @field_validator("fields")
    @classmethod
    def _reject_unlisted_fields(cls, value: list[str]) -> list[str]:
        """Refuse a column outside ``searchable_columns``.

        Args:
            value (list[str]): The columns the caller sent.

        Returns:
            list[str]: The columns, unchanged.

        Raises:
            ValidationException: When ``searchable_columns`` is declared and
                does not contain one of the names.
        """
        allowed = cls.searchable_columns
        if allowed is None:
            return value
        for name in value:
            if name not in allowed:
                raise ValidationException(
                    message=f"{name!r} is not a searchable column",
                    details={"field": name, "allowed": sorted(allowed)},
                )
        return value


__all__: list[str] = ["DEFAULT_MAX_SEARCH_TERM", "BaseSearchSchema"]
