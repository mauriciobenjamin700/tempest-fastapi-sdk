"""409 Conflict exception."""

from tempest_fastapi_sdk.exceptions.base import AppException


class ConflictException(AppException):
    """Raised when a write would violate a uniqueness/integrity rule.

    Typically surfaced by the repository when SQLAlchemy raises an
    ``IntegrityError`` on insert/update.
    """

    status_code: int = 409
    message: str = "Resource conflict"
    code: str = "CONFLICT"


__all__: list[str] = [
    "ConflictException",
]
