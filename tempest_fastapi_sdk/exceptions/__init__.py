"""Application exception primitives exposed at module level."""

from tempest_fastapi_sdk.exceptions.base import (
    AppException as AppException,
)
from tempest_fastapi_sdk.exceptions.base import (
    InheritedErrorCodeWarning as InheritedErrorCodeWarning,
)
from tempest_fastapi_sdk.exceptions.conflict import (
    ConflictException as ConflictException,
)
from tempest_fastapi_sdk.exceptions.forbidden import (
    ForbiddenException as ForbiddenException,
)
from tempest_fastapi_sdk.exceptions.i18n import (
    DEFAULT_LOCALE as DEFAULT_LOCALE,
)
from tempest_fastapi_sdk.exceptions.i18n import (
    MessageCatalog as MessageCatalog,
)
from tempest_fastapi_sdk.exceptions.i18n import (
    default_message_catalog as default_message_catalog,
)
from tempest_fastapi_sdk.exceptions.i18n import (
    parse_accept_language as parse_accept_language,
)
from tempest_fastapi_sdk.exceptions.jwt import (
    ExpiredTokenException as ExpiredTokenException,
)
from tempest_fastapi_sdk.exceptions.jwt import (
    InvalidTokenException as InvalidTokenException,
)
from tempest_fastapi_sdk.exceptions.not_found import (
    NotFoundException as NotFoundException,
)
from tempest_fastapi_sdk.exceptions.too_many_requests import (
    TooManyRequestsException as TooManyRequestsException,
)
from tempest_fastapi_sdk.exceptions.unauthorized import (
    UnauthorizedException as UnauthorizedException,
)
from tempest_fastapi_sdk.exceptions.upload import (
    FileTooLargeException as FileTooLargeException,
)
from tempest_fastapi_sdk.exceptions.upload import (
    InvalidFileTypeException as InvalidFileTypeException,
)
from tempest_fastapi_sdk.exceptions.validation import (
    ValidationException as ValidationException,
)

__all__: list[str] = [
    "DEFAULT_LOCALE",
    "AppException",
    "ConflictException",
    "ExpiredTokenException",
    "FileTooLargeException",
    "ForbiddenException",
    "InheritedErrorCodeWarning",
    "InvalidFileTypeException",
    "InvalidTokenException",
    "MessageCatalog",
    "NotFoundException",
    "TooManyRequestsException",
    "UnauthorizedException",
    "ValidationException",
    "default_message_catalog",
    "parse_accept_language",
]
