"""422 Unprocessable Entity exceptions."""

from collections.abc import Iterable
from typing import Any, ClassVar

from tempest_fastapi_sdk.exceptions.base import AppException


class ValidationException(AppException):
    """Raised when input fails a business rule beyond Pydantic.

    Pydantic emits 422 automatically for schema validation; use this
    for downstream rules that only the service layer can enforce.
    """

    status_code: int = 422
    message: str = "Validation error"
    code: str = "VALIDATION_ERROR"


class OrderByNotAllowedException(ValidationException):
    """Raised when ``order_by`` names a column the listing does not sort by.

    Sorting is an oracle. A caller who controls one row learns another
    row's value by binary search over where their own row lands, and a
    caller who controls nothing still ranks every row by the column —
    ordering users by ``wallet`` or ``hashed_password`` leaks both.

    ``details["allowed"]`` carries **only** the set the listing declared
    (``orderable_columns`` on the filter schema or on the repository),
    sorted. When no set was declared the key is absent: the repository
    does not know which listing called it, and the full mapper column
    list is exactly the map of what to try that the refusal must never
    publish.

    Subclasses :class:`ValidationException`, so ``except
    ValidationException`` keeps catching it; only the ``code`` is new.

    Attributes:
        order_by (str): The rejected value, as the caller sent it.
    """

    message: str = "Cannot order by this field"
    code: str = "ORDER_BY_NOT_ALLOWED"
    field: str | None = "order_by"
    details_example: ClassVar[dict[str, Any]] = {
        "order_by": "hashed_password",
        "allowed": ["created_at", "name"],
    }

    def __init__(
        self,
        order_by: str,
        *,
        allowed: Iterable[str] | None = None,
    ) -> None:
        """Initialize the refusal with the rejected column.

        Args:
            order_by (str): The ``order_by`` value the caller sent.
            allowed (Iterable[str] | None): The columns the listing
                sorts by. ``None`` omits ``allowed`` from ``details``.
        """
        details: dict[str, Any] = {"order_by": order_by}
        if allowed is not None:
            details["allowed"] = sorted(allowed)
        super().__init__(details=details)
        self.order_by: str = order_by


class PageSizeTooLargeException(ValidationException):
    """Raised when a repository is asked for a page above its ceiling.

    Raised by :class:`~tempest_fastapi_sdk.BaseRepository` when the
    repository declares ``max_page_size`` and ``page_size`` (or the cursor
    ``limit``) exceeds it. The filter schemas enforce their own ceiling as
    a regular pydantic ``less_than_equal`` error, so this is the second
    line for callers that reach the repository without one.

    Attributes:
        page_size (int): The requested page size.
        max_page_size (int): The ceiling it exceeded.
    """

    message: str = "Page size is above the allowed maximum"
    code: str = "PAGE_SIZE_TOO_LARGE"
    field: str | None = "page_size"
    details_example: ClassVar[dict[str, Any]] = {
        "page_size": 500,
        "max_page_size": 100,
    }

    def __init__(self, page_size: int, *, max_page_size: int) -> None:
        """Initialize the refusal with the requested size and the ceiling.

        Args:
            page_size (int): The page size the caller asked for.
            max_page_size (int): The ceiling the repository declares.
        """
        super().__init__(
            details={"page_size": page_size, "max_page_size": max_page_size},
        )
        self.page_size: int = page_size
        self.max_page_size: int = max_page_size


class UnknownFilterKeyException(ValidationException):
    """Raised when a ``filters`` key names no column or no operator.

    A repository ``filters`` mapping is column name to value, with an
    optional ``<column>__<op>`` suffix. A key the model has no column
    for — a typo such as ``{"usr_id": 1234}`` — or a suffix that is not a
    known operator — ``{"user_id__bogus": 1}`` — used to be dropped from
    the query in silence. On a read that returned every row; on
    ``bulk_update`` / ``delete_many`` it changed every row in the table,
    because the mapping was not empty and so the empty-filter guard never
    fired. The key is now refused before any statement runs.

    ``details["filter"]`` carries the rejected key only. The model's
    column list is deliberately absent: when the mapping comes from a
    query string, that list is a map of what to probe.

    Subclasses :class:`ValidationException`, so ``except
    ValidationException`` keeps catching it and the HTTP status is 422.

    Attributes:
        filter_key (str): The rejected key, as the caller sent it.
    """

    message: str = "Unknown filter field"
    code: str = "UNKNOWN_FILTER_KEY"
    field: str | None = "filters"
    details_example: ClassVar[dict[str, Any]] = {"filter": "usr_id"}

    def __init__(self, filter_key: str) -> None:
        """Initialize the refusal with the rejected key.

        Args:
            filter_key (str): The ``filters`` key the model cannot
                resolve.
        """
        super().__init__(details={"filter": filter_key})
        self.filter_key: str = filter_key


__all__: list[str] = [
    "OrderByNotAllowedException",
    "PageSizeTooLargeException",
    "UnknownFilterKeyException",
    "ValidationException",
]
