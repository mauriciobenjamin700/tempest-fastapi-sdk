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
    "ActivationResponseSchema",
    "ActivationToken",
    "LoginResponseSchema",
    "LoginSchema",
    "MFAConfirmSchema",
    "MFADisableSchema",
    "MFAEnrollResponseSchema",
    "MFAVerifySchema",
    "PasswordResetConfirmSchema",
    "PasswordResetRequestSchema",
    "PasswordResetResponseSchema",
    "PasswordResetToken",
    "SignupResponseSchema",
    "SignupSchema",
    "UserAuthService",
    "make_auth_router",
]
