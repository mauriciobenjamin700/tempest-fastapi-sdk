"""Bundled auth flow — signup, activation, password reset.

Exposes a service + router pair so a scaffolded project can mount
end-to-end account management in one wiring call. Requires the
``[auth]`` extra for password hashing and JWT issuance;
``[email]`` is optional — when missing, activation / reset links
return in the JSON response body instead of being mailed
(toggled by the ``AUTH_RETURN_TOKEN_IN_RESPONSE`` setting).
"""

from tempest_fastapi_sdk.auth.router import make_auth_router
from tempest_fastapi_sdk.auth.schemas import (
    ActivationResponseSchema,
    LoginResponseSchema,
    LoginSchema,
    PasswordResetConfirmSchema,
    PasswordResetRequestSchema,
    PasswordResetResponseSchema,
    SignupResponseSchema,
    SignupSchema,
)
from tempest_fastapi_sdk.auth.service import (
    ActivationToken,
    PasswordResetToken,
    UserAuthService,
)

__all__: list[str] = [
    "ActivationResponseSchema",
    "ActivationToken",
    "LoginResponseSchema",
    "LoginSchema",
    "PasswordResetConfirmSchema",
    "PasswordResetRequestSchema",
    "PasswordResetResponseSchema",
    "PasswordResetToken",
    "SignupResponseSchema",
    "SignupSchema",
    "UserAuthService",
    "make_auth_router",
]
