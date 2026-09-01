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
from tempest_fastapi_sdk.exceptions.factories import (
    DEFAULT_CONFLICT_TEMPLATE as DEFAULT_CONFLICT_TEMPLATE,
)
from tempest_fastapi_sdk.exceptions.factories import (
    DEFAULT_CONFLICT_TEMPLATE_ANONYMOUS as DEFAULT_CONFLICT_TEMPLATE_ANONYMOUS,
)
from tempest_fastapi_sdk.exceptions.factories import (
    DEFAULT_NOT_FOUND_TEMPLATE as DEFAULT_NOT_FOUND_TEMPLATE,
)
from tempest_fastapi_sdk.exceptions.factories import (
    DEFAULT_NOT_FOUND_TEMPLATE_ANONYMOUS as DEFAULT_NOT_FOUND_TEMPLATE_ANONYMOUS,
)
from tempest_fastapi_sdk.exceptions.factories import (
    conflict_exception as conflict_exception,
)
from tempest_fastapi_sdk.exceptions.factories import (
    not_found_exception as not_found_exception,
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
from tempest_fastapi_sdk.exceptions.oauth import (
    OAuthAccountInactiveException as OAuthAccountInactiveException,
)
from tempest_fastapi_sdk.exceptions.oauth import (
    OAuthAccountNotLinkedException as OAuthAccountNotLinkedException,
)
from tempest_fastapi_sdk.exceptions.oauth import (
    OAuthAudienceUnverifiableException as OAuthAudienceUnverifiableException,
)
from tempest_fastapi_sdk.exceptions.oauth import (
    OAuthCodeMissingException as OAuthCodeMissingException,
)
from tempest_fastapi_sdk.exceptions.oauth import (
    OAuthEmailMissingException as OAuthEmailMissingException,
)
from tempest_fastapi_sdk.exceptions.oauth import (
    OAuthEmailTakenException as OAuthEmailTakenException,
)
from tempest_fastapi_sdk.exceptions.oauth import (
    OAuthEmailUnverifiedException as OAuthEmailUnverifiedException,
)
from tempest_fastapi_sdk.exceptions.oauth import (
    OAuthProviderDeniedException as OAuthProviderDeniedException,
)
from tempest_fastapi_sdk.exceptions.oauth import (
    OAuthProviderNotConfiguredException as OAuthProviderNotConfiguredException,
)
from tempest_fastapi_sdk.exceptions.oauth import (
    OAuthRegistrationDisabledException as OAuthRegistrationDisabledException,
)
from tempest_fastapi_sdk.exceptions.oauth import (
    OAuthStateMismatchException as OAuthStateMismatchException,
)
from tempest_fastapi_sdk.exceptions.oauth import (
    OAuthTokenAudienceMismatchException as OAuthTokenAudienceMismatchException,
)
from tempest_fastapi_sdk.exceptions.oauth import (
    OAuthTokenRejectedException as OAuthTokenRejectedException,
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
    "DEFAULT_CONFLICT_TEMPLATE",
    "DEFAULT_CONFLICT_TEMPLATE_ANONYMOUS",
    "DEFAULT_LOCALE",
    "DEFAULT_NOT_FOUND_TEMPLATE",
    "DEFAULT_NOT_FOUND_TEMPLATE_ANONYMOUS",
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
    "OAuthAccountInactiveException",
    "OAuthAccountNotLinkedException",
    "OAuthAudienceUnverifiableException",
    "OAuthCodeMissingException",
    "OAuthEmailMissingException",
    "OAuthEmailTakenException",
    "OAuthEmailUnverifiedException",
    "OAuthProviderDeniedException",
    "OAuthProviderNotConfiguredException",
    "OAuthRegistrationDisabledException",
    "OAuthStateMismatchException",
    "OAuthTokenAudienceMismatchException",
    "OAuthTokenRejectedException",
    "TooManyRequestsException",
    "UnauthorizedException",
    "ValidationException",
    "conflict_exception",
    "default_message_catalog",
    "not_found_exception",
    "parse_accept_language",
]
