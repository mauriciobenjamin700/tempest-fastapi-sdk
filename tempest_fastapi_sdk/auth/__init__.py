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

from tempest_fastapi_sdk.auth.firebase import (
    DEFAULT_FIREBASE_APP_NAME as DEFAULT_FIREBASE_APP_NAME,
)
from tempest_fastapi_sdk.auth.firebase import (
    FirebaseAuth as FirebaseAuth,
)
from tempest_fastapi_sdk.auth.firebase import (
    FirebaseCredentialError as FirebaseCredentialError,
)
from tempest_fastapi_sdk.auth.firebase import (
    FirebaseIdentity as FirebaseIdentity,
)
from tempest_fastapi_sdk.auth.firebase import (
    FirebaseTokenExpiredError as FirebaseTokenExpiredError,
)
from tempest_fastapi_sdk.auth.firebase import (
    FirebaseTokenInvalidError as FirebaseTokenInvalidError,
)
from tempest_fastapi_sdk.auth.firebase import (
    FirebaseTokenMissingError as FirebaseTokenMissingError,
)
from tempest_fastapi_sdk.auth.firebase import (
    FirebaseTokenRevokedError as FirebaseTokenRevokedError,
)
from tempest_fastapi_sdk.auth.firebase import (
    FirebaseUnavailableError as FirebaseUnavailableError,
)
from tempest_fastapi_sdk.auth.firebase import (
    FirebaseUserDisabledError as FirebaseUserDisabledError,
)
from tempest_fastapi_sdk.auth.firebase import (
    FirebaseUserResolver as FirebaseUserResolver,
)
from tempest_fastapi_sdk.auth.guards import require_active as require_active
from tempest_fastapi_sdk.auth.guards import require_admin as require_admin
from tempest_fastapi_sdk.auth.guards import (
    require_authenticated as require_authenticated,
)
from tempest_fastapi_sdk.auth.introspection import (
    IntrospectionAuth as IntrospectionAuth,
)
from tempest_fastapi_sdk.auth.locale import (
    DEFAULT_AUTH_LOCALE as DEFAULT_AUTH_LOCALE,
)
from tempest_fastapi_sdk.auth.locale import (
    LOCALE_QUERY_PARAM as LOCALE_QUERY_PARAM,
)
from tempest_fastapi_sdk.auth.locale import SUPPORTED_LOCALES as SUPPORTED_LOCALES
from tempest_fastapi_sdk.auth.locale import format_expires_at as format_expires_at
from tempest_fastapi_sdk.auth.locale import negotiate_locale as negotiate_locale
from tempest_fastapi_sdk.auth.locale import normalize_locale as normalize_locale
from tempest_fastapi_sdk.auth.locale import resolve_locale as resolve_locale
from tempest_fastapi_sdk.auth.locale import stamp_locale as stamp_locale
from tempest_fastapi_sdk.auth.router import make_auth_router as make_auth_router
from tempest_fastapi_sdk.auth.schemas import (
    ActivationResponseSchema as ActivationResponseSchema,
)
from tempest_fastapi_sdk.auth.schemas import ActivationToken as ActivationToken
from tempest_fastapi_sdk.auth.schemas import AuthUserSchema as AuthUserSchema
from tempest_fastapi_sdk.auth.schemas import (
    EmailChangeConfirmSchema as EmailChangeConfirmSchema,
)
from tempest_fastapi_sdk.auth.schemas import (
    EmailChangeRequestSchema as EmailChangeRequestSchema,
)
from tempest_fastapi_sdk.auth.schemas import (
    EmailChangeResponseSchema as EmailChangeResponseSchema,
)
from tempest_fastapi_sdk.auth.schemas import EmailChangeToken as EmailChangeToken
from tempest_fastapi_sdk.auth.schemas import (
    EmailRecoveryRequestSchema as EmailRecoveryRequestSchema,
)
from tempest_fastapi_sdk.auth.schemas import (
    EmailVerificationToken as EmailVerificationToken,
)
from tempest_fastapi_sdk.auth.schemas import (
    LoginResponseSchema as LoginResponseSchema,
)
from tempest_fastapi_sdk.auth.schemas import LoginSchema as LoginSchema
from tempest_fastapi_sdk.auth.schemas import LogoutSchema as LogoutSchema
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
from tempest_fastapi_sdk.auth.schemas import RefreshSchema as RefreshSchema
from tempest_fastapi_sdk.auth.schemas import (
    SignupResponseSchema as SignupResponseSchema,
)
from tempest_fastapi_sdk.auth.schemas import SignupSchema as SignupSchema
from tempest_fastapi_sdk.auth.schemas import (
    WebAuthnAuthenticateBeginSchema as WebAuthnAuthenticateBeginSchema,
)
from tempest_fastapi_sdk.auth.schemas import (
    WebAuthnAuthenticateCompleteSchema as WebAuthnAuthenticateCompleteSchema,
)
from tempest_fastapi_sdk.auth.schemas import (
    WebAuthnCredentialSchema as WebAuthnCredentialSchema,
)
from tempest_fastapi_sdk.auth.schemas import (
    WebAuthnDeleteSchema as WebAuthnDeleteSchema,
)
from tempest_fastapi_sdk.auth.schemas import (
    WebAuthnOptionsSchema as WebAuthnOptionsSchema,
)
from tempest_fastapi_sdk.auth.schemas import (
    WebAuthnRegisterCompleteSchema as WebAuthnRegisterCompleteSchema,
)
from tempest_fastapi_sdk.auth.service import UserAuthService as UserAuthService
from tempest_fastapi_sdk.auth.token_delivery import (
    AuthCookieConfig as AuthCookieConfig,
)
from tempest_fastapi_sdk.auth.token_delivery import TokenDelivery as TokenDelivery
from tempest_fastapi_sdk.auth.token_delivery import (
    apply_auth_cookies as apply_auth_cookies,
)
from tempest_fastapi_sdk.auth.token_delivery import (
    clear_auth_cookies as clear_auth_cookies,
)
from tempest_fastapi_sdk.auth.webauthn import (
    MemoryWebAuthnChallengeStore as MemoryWebAuthnChallengeStore,
)
from tempest_fastapi_sdk.auth.webauthn import (
    RedisWebAuthnChallengeStore as RedisWebAuthnChallengeStore,
)
from tempest_fastapi_sdk.auth.webauthn import (
    WebAuthnChallengeStore as WebAuthnChallengeStore,
)
from tempest_fastapi_sdk.auth.webauthn import WebAuthnService as WebAuthnService

__all__: list[str] = [
    "DEFAULT_AUTH_LOCALE",
    "DEFAULT_FIREBASE_APP_NAME",
    "LOCALE_QUERY_PARAM",
    "SUPPORTED_LOCALES",
    "ActivationResponseSchema",
    "ActivationToken",
    "AuthCookieConfig",
    "AuthUserSchema",
    "EmailChangeConfirmSchema",
    "EmailChangeRequestSchema",
    "EmailChangeResponseSchema",
    "EmailChangeToken",
    "EmailRecoveryRequestSchema",
    "EmailVerificationToken",
    "FirebaseAuth",
    "FirebaseCredentialError",
    "FirebaseIdentity",
    "FirebaseTokenExpiredError",
    "FirebaseTokenInvalidError",
    "FirebaseTokenMissingError",
    "FirebaseTokenRevokedError",
    "FirebaseUnavailableError",
    "FirebaseUserDisabledError",
    "FirebaseUserResolver",
    "IntrospectionAuth",
    "LoginResponseSchema",
    "LoginSchema",
    "LogoutSchema",
    "MFAConfirmSchema",
    "MFADisableSchema",
    "MFAEnrollResponseSchema",
    "MFAVerifySchema",
    "MemoryWebAuthnChallengeStore",
    "PasswordChangeSchema",
    "PasswordResetConfirmSchema",
    "PasswordResetRequestSchema",
    "PasswordResetResponseSchema",
    "PasswordResetToken",
    "RedisWebAuthnChallengeStore",
    "RefreshSchema",
    "SignupResponseSchema",
    "SignupSchema",
    "TokenDelivery",
    "UserAuthService",
    "WebAuthnAuthenticateBeginSchema",
    "WebAuthnAuthenticateCompleteSchema",
    "WebAuthnChallengeStore",
    "WebAuthnCredentialSchema",
    "WebAuthnDeleteSchema",
    "WebAuthnOptionsSchema",
    "WebAuthnRegisterCompleteSchema",
    "WebAuthnService",
    "apply_auth_cookies",
    "clear_auth_cookies",
    "format_expires_at",
    "make_auth_router",
    "negotiate_locale",
    "normalize_locale",
    "require_active",
    "require_admin",
    "require_authenticated",
    "resolve_locale",
    "stamp_locale",
]
