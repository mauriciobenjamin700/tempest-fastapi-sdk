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


class PushEndpointNotPublicException(ValidationException):
    """Raised when a push subscription names an endpoint delivery would refuse.

    The Web Push dispatcher only contacts endpoints that are ``https`` and
    resolve to public addresses (unless it was built with
    ``allow_private_networks=True``). An endpoint pointing inside the
    server's own network is refused at registration with this exception
    instead of being stored and refused on every send.

    ``details`` is empty on purpose: which address the host resolved to is
    exactly what a caller probing the internal network wants to learn.

    Subclasses :class:`ValidationException`, so ``except
    ValidationException`` keeps catching it and the HTTP status is 422.
    """

    message: str = "Push endpoint must be a public https URL"
    code: str = "PUSH_ENDPOINT_NOT_PUBLIC"
    field: str | None = "endpoint"


_VALUE_RENDER_CAP: int = 64
"""Longest ``repr`` echoed in an :class:`InvalidFilterValueException` detail."""


def _brief(value: Any) -> str:
    """Render a rejected filter value for an error detail, bounded.

    The value is echoed with ``repr`` so the caller sees what was sent,
    but truncated to :data:`_VALUE_RENDER_CAP` characters: a mistyped
    filter rarely carries a small value (an uploaded payload, a file
    id list), and the error is not the place to replay all of it. A
    generator is not consumed — ``repr`` shows its address.

    Args:
        value (Any): The value that cannot express the operator.

    Returns:
        str: ``repr(value)``, with a trailing ellipsis when truncated.
    """
    rendered = repr(value)
    if len(rendered) > _VALUE_RENDER_CAP:
        return rendered[:_VALUE_RENDER_CAP] + "..."
    return rendered


class InvalidFilterValueException(ValidationException):
    """Raised when a ``filters`` value cannot express its operator.

    A suffix operator fixes the shape its value must have: ``between``
    needs an ordered two-item ``(lo, hi)`` ``list`` / ``tuple``. A value
    that did not fit used to be dropped in silence, and the condition
    vanishing from the ``WHERE`` widened the statement instead of
    narrowing it — on ``bulk_update`` / ``delete_many`` /
    ``update_returning`` that meant touching every row the filter did
    **not** name (measured on SQLite, #484). The value is now refused
    before any statement runs. The ``in`` family is **not** refused on a
    scalar: a single value is wrapped in a one-item list (``{"status__in":
    "open"}`` → ``IN ('open')``), which is what the caller asked for.

    ``details["filter"]`` carries the rejected key (suffix included) and
    ``details["value"]`` a bounded ``repr`` of the value — enough to spot
    the mistake without echoing a whole payload.

    Subclasses :class:`ValidationException`, so ``except
    ValidationException`` keeps catching it and the HTTP status is 422.

    Attributes:
        filter_key (str): The rejected key, as the caller sent it.
        value (Any): The rejected value, as the caller sent it.
    """

    message: str = "Invalid filter value"
    code: str = "INVALID_FILTER_VALUE"
    field: str | None = "filters"
    details_example: ClassVar[dict[str, Any]] = {
        "filter": "created_at__between",
        "value": "[1]",
    }

    def __init__(self, filter_key: str, value: Any) -> None:
        """Initialize the refusal with the rejected key and value.

        Args:
            filter_key (str): The full ``filters`` key, suffix included.
            value (Any): The value that cannot express the operator.
        """
        super().__init__(details={"filter": filter_key, "value": _brief(value)})
        self.filter_key: str = filter_key
        self.value: Any = value


__all__: list[str] = [
    "InvalidFilterValueException",
    "OrderByNotAllowedException",
    "PageSizeTooLargeException",
    "UnknownFilterKeyException",
    "ValidationException",
]
