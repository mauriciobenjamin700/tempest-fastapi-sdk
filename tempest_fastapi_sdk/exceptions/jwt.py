"""JWT-related exceptions."""

from typing import ClassVar

from tempest_fastapi_sdk.exceptions.unauthorized import UnauthorizedException


class InvalidTokenException(UnauthorizedException):
    """Raised when a JWT fails signature or claim validation."""

    message: str = "Invalid token"
    code: ClassVar[str] = "INVALID_TOKEN"


class ExpiredTokenException(UnauthorizedException):
    """Raised when a JWT's ``exp`` claim is in the past."""

    message: str = "Token expired"
    code: ClassVar[str] = "TOKEN_EXPIRED"


__all__: list[str] = [
    "ExpiredTokenException",
    "InvalidTokenException",
]
