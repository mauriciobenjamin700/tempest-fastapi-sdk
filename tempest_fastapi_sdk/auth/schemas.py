"""Pydantic DTOs for the bundled auth flows.

Every schema in this module inherits from :class:`BaseSchema`
(the SDK's gold-standard Pydantic base) so it shares the same
``ConfigDict`` (``extra="ignore"``, ``from_attributes=True``,
``str_strip_whitespace=True``, ``validate_assignment=True``,
``use_enum_values=True``) and exposes ``to_dict`` / ``to_json``.

The schemas split into two groups:

* **Request / response DTOs** consumed by
  :func:`tempest_fastapi_sdk.make_auth_router` — wired into the
  ``signup`` / ``activate`` / ``login`` / ``password-reset``
  endpoints. These end up in the OpenAPI ``/docs`` page so every
  field carries ``title``, ``description`` and ``examples``
  metadata.
* **Service-level value objects** (``ActivationToken``,
  ``PasswordResetToken``) returned by
  :class:`tempest_fastapi_sdk.UserAuthService` to the caller —
  carry the one-time plaintext token alongside the rendered URL
  and expiry so the caller can either mail it, log it, or hand
  it back to the client (dev mode).
"""

from __future__ import annotations

from datetime import datetime
from uuid import UUID

from pydantic import EmailStr, Field

from tempest_fastapi_sdk.schemas.base import BaseSchema


class SignupSchema(BaseSchema):
    """Request body for ``POST /auth/signup``.

    Carries the credentials and the optional display name a new
    account starts with. The email is normalized to lowercase
    before insert (matches the unique-index convention every SDK
    user table follows); the password is hashed with bcrypt by
    :class:`tempest_fastapi_sdk.PasswordUtils` and never stored
    in plaintext.

    Attributes:
        email (EmailStr): Login identifier — validated by
            ``email-validator`` so malformed addresses fail at
            the Pydantic layer (422) instead of at insert time.
        password (str): Plaintext password. Length floor is
            enforced both here (schema-level) and inside
            :class:`UserAuthService` (service-level redundancy on
            purpose — Pydantic validators don't fire on direct
            ``service.signup(...)`` calls from other code paths).
        name (str | None): Optional display name shown in the
            admin UI / front-end profile. ``None`` keeps the
            column ``NULL``.
    """

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

    The shape depends on the active settings:

    * When ``AUTH_AUTO_ACTIVATE=True`` the user is born active,
      ``activation_required=False`` and both ``access_token`` /
      ``refresh_token`` are populated — the client can log in
      immediately.
    * When ``AUTH_AUTO_ACTIVATE=False`` (production default) the
      user must confirm the activation link before logging in.
      ``activation_required=True``, the tokens stay ``None`` and
      ``activation_url`` is set only when
      ``AUTH_RETURN_TOKEN_IN_RESPONSE=True`` (dev) or when the
      ``[email]`` extra isn't wired (so the link has to ship via
      the response instead of via SMTP).

    Attributes:
        user_id (UUID): Primary key of the freshly-inserted row.
        activation_required (bool): Whether the user still needs
            to confirm via the activation link.
        activation_url (str | None): Front-end URL the user must
            visit. ``None`` when the link travelled via email or
            activation was skipped.
        access_token (str | None): Short-lived JWT. Only set
            when ``activation_required=False``.
        refresh_token (str | None): Long-lived JWT. Only set
            when ``activation_required=False``.
    """

    user_id: UUID = Field(
        title="User id",
        description="UUID of the freshly-created row.",
        examples=["7d8e4d5a-9f4b-4c3a-bd0a-1234567890ab"],
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
        description=(
            "Short-lived bearer token. Set only when ``activation_required=False``."
        ),
        examples=[None, "eyJhbGciOi…"],
    )
    refresh_token: str | None = Field(
        default=None,
        title="JWT refresh token",
        description=(
            "Long-lived refresh token used by ``POST /auth/refresh``. "
            "Set only when ``activation_required=False``."
        ),
        examples=[None, "eyJhbGciOi…"],
    )


class ActivationResponseSchema(BaseSchema):
    """Response body for ``POST /auth/activate/{token}``.

    Returned after the SDK has consumed a one-shot activation
    token and flipped the user's ``is_active=True``. The user is
    automatically logged in — both JWTs are issued so the
    front-end can complete the post-confirmation redirect in one
    round-trip.

    Attributes:
        user_id (UUID): UUID of the freshly-activated user.
        access_token (str): Short-lived JWT.
        refresh_token (str): Long-lived JWT.
    """

    user_id: UUID = Field(
        title="User id",
        description="UUID of the activated user.",
        examples=["7d8e4d5a-9f4b-4c3a-bd0a-1234567890ab"],
    )
    access_token: str = Field(
        title="JWT access token",
        description="Short-lived bearer token issued on successful activation.",
        examples=["eyJhbGciOi…"],
    )
    refresh_token: str = Field(
        title="JWT refresh token",
        description="Long-lived token for the refresh endpoint.",
        examples=["eyJhbGciOi…"],
    )


class LoginSchema(BaseSchema):
    """Request body for ``POST /auth/login``.

    Standard email + password authentication. Both error paths
    (wrong password / unknown email / inactive user) collapse
    into the same generic ``UnauthorizedException`` so attackers
    can't enumerate accounts by reading the response.

    Attributes:
        email (EmailStr): Login identifier.
        password (str): Plaintext password — verified against
            the bcrypt hash stored on the row.
    """

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
    """Response body for ``POST /auth/login`` and the password-reset confirm.

    Issued only when credentials validate; the bundled router
    reuses this shape for both ``POST /auth/login`` and
    ``POST /auth/password-reset/confirm`` since both flows end
    with an authenticated session.

    Attributes:
        user_id (UUID): UUID of the authenticated user.
        access_token (str): Short-lived JWT.
        refresh_token (str): Long-lived JWT.
    """

    user_id: UUID = Field(
        title="User id",
        description="UUID of the authenticated user.",
        examples=["7d8e4d5a-9f4b-4c3a-bd0a-1234567890ab"],
    )
    access_token: str = Field(
        title="JWT access token",
        description="Short-lived bearer token.",
        examples=["eyJhbGciOi…"],
    )
    refresh_token: str = Field(
        title="JWT refresh token",
        description="Long-lived refresh token.",
        examples=["eyJhbGciOi…"],
    )


class PasswordResetRequestSchema(BaseSchema):
    """Request body for ``POST /auth/password-reset/request``.

    The endpoint always returns ``202`` with a generic message —
    even when the email isn't on file — so probing the endpoint
    can't enumerate accounts. The reset link travels via email
    (production) or in the response body when
    ``AUTH_RETURN_TOKEN_IN_RESPONSE=True`` (dev).

    Attributes:
        email (EmailStr): Email of the account asking for a
            reset.
    """

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
    """Response body for ``POST /auth/password-reset/request``.

    ``message`` is the same generic string regardless of whether
    the email matched an account. ``reset_url`` is populated only
    when ``AUTH_RETURN_TOKEN_IN_RESPONSE=True`` or when the
    ``[email]`` extra isn't installed — otherwise the link only
    travels through SMTP.

    Attributes:
        message (str): Human-readable summary of the next step.
            Always identical across the "email found" / "email
            not found" branches.
        reset_url (str | None): Front-end reset URL when the
            caller asked for an inline response, ``None`` in
            production.
    """

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
    """Request body for ``POST /auth/password-reset/confirm``.

    Carries the opaque token the user copied from the reset
    link plus the replacement password. The service consumes the
    token (one-shot — ``used_at`` is stamped) and replaces the
    bcrypt hash atomically.

    Attributes:
        token (str): Opaque token issued by ``request``. The
            plaintext form — the SDK stores only the hash, so
            this value cannot be guessed from the database.
        new_password (str): Plaintext replacement password.
            Length floor is enforced both schema-side and inside
            the service.
    """

    token: str = Field(
        min_length=16,
        title="Reset token",
        description="Opaque token from the reset email / response body.",
        examples=["abc123def456…"],
    )
    new_password: str = Field(
        min_length=12,
        title="New password",
        description="Plaintext replacement password.",
        examples=["new-correct-horse-battery"],
    )


class ActivationToken(BaseSchema):
    """Service-level result of issuing an account-activation token.

    Returned by :meth:`UserAuthService.signup` when activation is
    required — i.e. when ``AUTH_AUTO_ACTIVATE`` is false. The
    plaintext token is included here exactly once; only its
    SHA-256 hash is persisted, so this value cannot be recovered
    later. Use it to mail the activation link, log it during
    tests, or hand it back to the client in dev mode.

    Attributes:
        user_id (UUID): UUID of the user the token authorizes.
        token (str): Plaintext token — show once, never store.
        url (str): Front-end activation URL with the token
            already substituted into ``AUTH_ACTIVATION_URL_TEMPLATE``.
        expires_at (datetime): UTC timestamp the token becomes
            invalid (default 7 days after issuance).
    """

    user_id: UUID = Field(
        title="User id",
        description="UUID of the user the token authorizes.",
        examples=["7d8e4d5a-9f4b-4c3a-bd0a-1234567890ab"],
    )
    token: str = Field(
        title="Plaintext token",
        description="Opaque token — display once, never persist in cleartext.",
        examples=["abc123def456…"],
    )
    url: str = Field(
        title="Activation URL",
        description=(
            "Front-end URL with the token already substituted. "
            "Derived from ``AUTH_ACTIVATION_URL_TEMPLATE``."
        ),
        examples=["http://localhost:3000/activate?token=abc123…"],
    )
    expires_at: datetime = Field(
        title="Expires at",
        description="UTC timestamp the token becomes invalid.",
        examples=["2026-06-11T16:00:00Z"],
    )


class PasswordResetToken(BaseSchema):
    """Service-level result of issuing a password-reset token.

    Returned by :meth:`UserAuthService.request_password_reset`
    when the email matches a user **and** the caller asked the
    service to surface the link (either via
    ``AUTH_RETURN_TOKEN_IN_RESPONSE=True`` or because no
    :class:`EmailUtils` was wired). The plaintext token is
    one-shot, hashed at rest, and expires after
    ``AUTH_PASSWORD_RESET_TTL_SECONDS`` (default 1 hour).

    Attributes:
        user_id (UUID): UUID of the user whose password the
            token authorizes resetting.
        token (str): Plaintext token — display once, never store.
        url (str): Front-end reset URL with the token already
            substituted into ``AUTH_PASSWORD_RESET_URL_TEMPLATE``.
        expires_at (datetime): UTC timestamp the token becomes
            invalid.
    """

    user_id: UUID = Field(
        title="User id",
        description="UUID of the user this reset token authorizes.",
        examples=["7d8e4d5a-9f4b-4c3a-bd0a-1234567890ab"],
    )
    token: str = Field(
        title="Plaintext token",
        description="Opaque token — display once, never persist in cleartext.",
        examples=["abc123def456…"],
    )
    url: str = Field(
        title="Reset URL",
        description=(
            "Front-end URL with the token already substituted. "
            "Derived from ``AUTH_PASSWORD_RESET_URL_TEMPLATE``."
        ),
        examples=["http://localhost:3000/reset-password?token=abc123…"],
    )
    expires_at: datetime = Field(
        title="Expires at",
        description="UTC timestamp the token becomes invalid.",
        examples=["2026-06-04T17:00:00Z"],
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
]
