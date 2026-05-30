"""Application exception primitives exposed at module level."""

from tempest_fastapi_sdk.exceptions.base import AppException
from tempest_fastapi_sdk.exceptions.conflict import ConflictException
from tempest_fastapi_sdk.exceptions.forbidden import ForbiddenException
from tempest_fastapi_sdk.exceptions.jwt import (
    ExpiredTokenException,
    InvalidTokenException,
)
from tempest_fastapi_sdk.exceptions.not_found import NotFoundException
from tempest_fastapi_sdk.exceptions.too_many_requests import (
    TooManyRequestsException,
)
from tempest_fastapi_sdk.exceptions.unauthorized import UnauthorizedException
from tempest_fastapi_sdk.exceptions.upload import (
    FileTooLargeException,
    InvalidFileTypeException,
)
from tempest_fastapi_sdk.exceptions.validation import ValidationException

__all__: list[str] = [
    "AppException",
    "ConflictException",
    "ExpiredTokenException",
    "FileTooLargeException",
    "ForbiddenException",
    "InvalidFileTypeException",
    "InvalidTokenException",
    "NotFoundException",
    "TooManyRequestsException",
    "UnauthorizedException",
    "ValidationException",
]
