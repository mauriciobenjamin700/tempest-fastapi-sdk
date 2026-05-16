"""404 Not Found exception."""

from typing import ClassVar

from tempest_fastapi_sdk.exceptions.base import AppException


class NotFoundException(AppException):
    """Raised when a single resource cannot be located.

    Use for ``get_by_id`` / ``get_by_email`` style lookups. NEVER use
    for collection endpoints — those should return ``[]`` instead.
    """

    status_code: int = 404
    message: str = "Resource not found"
    code: ClassVar[str] = "NOT_FOUND"


__all__: list[str] = [
    "NotFoundException",
]
