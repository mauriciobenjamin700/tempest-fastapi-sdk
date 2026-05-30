"""403 Forbidden exception."""

from tempest_fastapi_sdk.exceptions.base import AppException


class ForbiddenException(AppException):
    """Raised when the caller is authenticated but lacks permission."""

    status_code: int = 403
    message: str = "Forbidden"
    code: str = "FORBIDDEN"


__all__: list[str] = [
    "ForbiddenException",
]
