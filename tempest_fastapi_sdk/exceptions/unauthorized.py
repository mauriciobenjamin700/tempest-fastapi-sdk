"""401 Unauthorized exception."""

from tempest_fastapi_sdk.exceptions.base import AppException


class UnauthorizedException(AppException):
    """Raised when the caller is not authenticated.

    Use for missing/invalid/expired credentials. For "authenticated
    but not allowed" cases, use
    :class:`tempest_fastapi_sdk.exceptions.forbidden.ForbiddenException`.
    """

    status_code: int = 401
    message: str = "Unauthorized"
    code: str = "UNAUTHORIZED"


__all__: list[str] = [
    "UnauthorizedException",
]
