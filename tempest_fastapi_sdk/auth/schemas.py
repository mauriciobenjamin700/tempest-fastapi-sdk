"""Pydantic DTOs for the bundled auth flows."""

from __future__ import annotations

from uuid import UUID

from pydantic import EmailStr, Field

from tempest_fastapi_sdk.schemas.base import BaseSchema


class SignupSchema(BaseSchema):
    """Request body for ``POST /auth/signup``."""

    email: EmailStr = Field(
        title="Email",
        description="Login identifier — normalized to lowercase before insert.",
        examples=["ana@example.com"],
    )
    password: str = Field(
        min_length=12,
        title="Password",
        description=(
            "Plaintext password — hashed with bcrypt before storage. "
            "Minimum length defaults to 12 chars; configurable via "
            "``AUTH_PASSWORD_MIN_LENGTH``."
        ),
        examples=["correct-horse-battery-staple"],
    )
    name: str | None = Field(
        default=None,
        max_length=120,
        title="Display name",
        description="Optional display name shown in the admin / UI.",
        examples=[None, "Ana Souza"],
    )


class SignupResponseSchema(BaseSchema):
    """Response body for ``POST /auth/signup``.

    Two shapes depending on settings:

    * ``AUTH_AUTO_ACTIVATE=True`` → ``activation_required=False``,
      ``access_token`` / ``refresh_token`` populated.
    * ``AUTH_AUTO_ACTIVATE=False`` → ``activation_required=True``;
      tokens are issued only after the user confirms via the
      activation link. ``activation_url`` is set when
      ``AUTH_RETURN_TOKEN_IN_RESPONSE=True`` (dev) or when the
      ``[email]`` extra is missing.
    """

    user_id: UUID = Field(
        title="User id",
        description="UUID of the freshly-created row.",
        examples=["7d8e4d5a-...-..."],
    )
    activation_required: bool = Field(
        title="Activation required",
        description="``True`` when the user must confirm the activation link.",
        examples=[True, False],
    )
    activation_url: str | None = Field(
        default=None,
        title="Activation URL",
        description=(
            "When set, the front-end URL the user must visit to "
            "confirm. ``None`` means the email was sent (production "
            "default) or activation was skipped via "
            "``AUTH_AUTO_ACTIVATE``."
        ),
        examples=[None, "http://localhost:3000/activate?token=…"],
    )
    access_token: str | None = Field(
        default=None,
        title="JWT access token",
        description="Set only when ``activation_required=False``.",
    )
    refresh_token: str | None = Field(
        default=None,
        title="JWT refresh token",
        description="Set only when ``activation_required=False``.",
    )


class ActivationResponseSchema(BaseSchema):
    """Response body for ``POST /auth/activate/{token}``."""

    user_id: UUID = Field(
        title="User id",
        description="UUID of the activated user.",
    )
    access_token: str = Field(
        title="JWT access token",
        description="Short-lived bearer token issued on successful activation.",
    )
    refresh_token: str = Field(
        title="JWT refresh token",
        description="Long-lived token for the refresh endpoint.",
    )


class LoginSchema(BaseSchema):
    """Request body for ``POST /auth/login``."""

    email: EmailStr = Field(
        title="Email",
        description="Login identifier.",
        examples=["ana@example.com"],
    )
    password: str = Field(
        title="Password",
        description="Plaintext password.",
        examples=["correct-horse-battery-staple"],
    )


class LoginResponseSchema(BaseSchema):
    """Response body for ``POST /auth/login``."""

    user_id: UUID = Field(
        title="User id",
        description="UUID of the authenticated user.",
    )
    access_token: str = Field(
        title="JWT access token",
        description="Short-lived bearer token.",
    )
    refresh_token: str = Field(
        title="JWT refresh token",
        description="Long-lived refresh token.",
    )


class PasswordResetRequestSchema(BaseSchema):
    """Request body for ``POST /auth/password-reset/request``."""

    email: EmailStr = Field(
        title="Email",
        description=(
            "Email of the account asking to reset. The endpoint "
            "always returns 202 — never leaks whether the email "
            "exists in the system."
        ),
        examples=["ana@example.com"],
    )


class PasswordResetResponseSchema(BaseSchema):
    """Response body for ``POST /auth/password-reset/request``."""

    message: str = Field(
        title="Message",
        description="Human-readable summary of the next step.",
        examples=["If the email matches an account, a reset link was sent."],
    )
    reset_url: str | None = Field(
        default=None,
        title="Reset URL",
        description=(
            "Set only when ``AUTH_RETURN_TOKEN_IN_RESPONSE=True`` "
            "(dev mode) or when the ``[email]`` extra is missing. "
            "``None`` in production — the link only goes via email."
        ),
        examples=[None, "http://localhost:3000/reset-password?token=…"],
    )


class PasswordResetConfirmSchema(BaseSchema):
    """Request body for ``POST /auth/password-reset/confirm``."""

    token: str = Field(
        min_length=16,
        title="Reset token",
        description="Opaque token from the reset email / response body.",
    )
    new_password: str = Field(
        min_length=12,
        title="New password",
        description="Plaintext replacement password.",
    )


__all__: list[str] = [
    "ActivationResponseSchema",
    "LoginResponseSchema",
    "LoginSchema",
    "PasswordResetConfirmSchema",
    "PasswordResetRequestSchema",
    "PasswordResetResponseSchema",
    "SignupResponseSchema",
    "SignupSchema",
]
