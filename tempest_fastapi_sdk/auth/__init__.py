"""Bundled auth flow — signup, activation, password reset.

Exposes a service + router pair so a scaffolded project can mount
end-to-end account management in one wiring call. Requires the
``[auth]`` extra for password hashing and JWT issuance;
``[email]`` is optional — when missing, activation / reset links
return in the JSON response body instead of being mailed
(toggled by the ``AUTH_RETURN_TOKEN_IN_RESPONSE`` setting).

Re-exports use the PEP 484 ``from x import Y as Y`` explicit
re-export form combined with ``__all__`` so every type-checker
(mypy, pyright, pylance, basedpyright) accepts
``from tempest_fastapi_sdk.auth import UserAuthService`` without
a "private import usage" / "is not exported" diagnostic.
"""

from tempest_fastapi_sdk.auth.guards import require_active as require_active
from tempest_fastapi_sdk.auth.guards import require_admin as require_admin
from tempest_fastapi_sdk.auth.guards import (
    require_authenticated as require_authenticated,
)
from tempest_fastapi_sdk.auth.locale import (
    DEFAULT_AUTH_LOCALE as DEFAULT_AUTH_LOCALE,
)
from tempest_fastapi_sdk.auth.locale import SUPPORTED_LOCALES as SUPPORTED_LOCALES
from tempest_fastapi_sdk.auth.locale import format_expires_at as format_expires_at
from tempest_fastapi_sdk.auth.locale import negotiate_locale as negotiate_locale
from tempest_fastapi_sdk.auth.locale import normalize_locale as normalize_locale
from tempest_fastapi_sdk.auth.router import make_auth_router as make_auth_router
from tempest_fastapi_sdk.auth.schemas import (
    ActivationResponseSchema as ActivationResponseSchema,
)
from tempest_fastapi_sdk.auth.schemas import ActivationToken as ActivationToken
from tempest_fastapi_sdk.auth.schemas import (
    LoginResponseSchema as LoginResponseSchema,
)
from tempest_fastapi_sdk.auth.schemas import LoginSchema as LoginSchema
from tempest_fastapi_sdk.auth.schemas import MFAConfirmSchema as MFAConfirmSchema
from tempest_fastapi_sdk.auth.schemas import MFADisableSchema as MFADisableSchema
from tempest_fastapi_sdk.auth.schemas import (
    MFAEnrollResponseSchema as MFAEnrollResponseSchema,
)
from tempest_fastapi_sdk.auth.schemas import MFAVerifySchema as MFAVerifySchema
from tempest_fastapi_sdk.auth.schemas import (
    PasswordChangeSchema as PasswordChangeSchema,
)
from tempest_fastapi_sdk.auth.schemas import (
    PasswordResetConfirmSchema as PasswordResetConfirmSchema,
)
from tempest_fastapi_sdk.auth.schemas import (
    PasswordResetRequestSchema as PasswordResetRequestSchema,
)
from tempest_fastapi_sdk.auth.schemas import (
    PasswordResetResponseSchema as PasswordResetResponseSchema,
)
from tempest_fastapi_sdk.auth.schemas import PasswordResetToken as PasswordResetToken
from tempest_fastapi_sdk.auth.schemas import (
    SignupResponseSchema as SignupResponseSchema,
)
from tempest_fastapi_sdk.auth.schemas import SignupSchema as SignupSchema
from tempest_fastapi_sdk.auth.service import UserAuthService as UserAuthService

__all__: list[str] = [
    "DEFAULT_AUTH_LOCALE",
    "SUPPORTED_LOCALES",
    "ActivationResponseSchema",
    "ActivationToken",
    "LoginResponseSchema",
    "LoginSchema",
    "MFAConfirmSchema",
    "MFADisableSchema",
    "MFAEnrollResponseSchema",
    "MFAVerifySchema",
    "PasswordChangeSchema",
    "PasswordResetConfirmSchema",
    "PasswordResetRequestSchema",
    "PasswordResetResponseSchema",
    "PasswordResetToken",
    "SignupResponseSchema",
    "SignupSchema",
    "UserAuthService",
    "format_expires_at",
    "make_auth_router",
    "negotiate_locale",
    "normalize_locale",
    "require_active",
    "require_admin",
    "require_authenticated",
]
