"""401 Unauthorized exception."""

from tempest_fastapi_sdk.exceptions.base import AppException

MISSING_TOKEN_MESSAGE: str = "Authorization token is missing or invalid"
"""Default ``detail`` of the ``401`` the bearer-token dependencies raise.

Every dependency that reads a token takes a parameter overriding it
(``error_message`` / ``unauthorized_message``), so a service answering in
another language passes its own phrase instead of shipping this one.
"""


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
    "MISSING_TOKEN_MESSAGE",
    "UnauthorizedException",
]
