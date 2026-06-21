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
        password (str): Plaintext password. The schema only rejects an
            empty string; the *configured* minimum length (default 12)
            and optional complexity rules are the single source of
            truth and are applied by :class:`UserAuthService`, so
            lowering / raising ``AUTH_PASSWORD_MIN_LENGTH`` (or toggling
            ``AUTH_PASSWORD_REQUIRE_COMPLEXITY``) takes effect on the
            router path without the schema fighting it.
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
        min_length=1,
        title="Password",
        description=(
            "Plaintext password — hashed with bcrypt before storage. "
            "The schema only rejects empty strings; the effective "
            "minimum length and complexity come from "
            "``AUTH_PASSWORD_MIN_LENGTH`` / "
            "``AUTH_PASSWORD_REQUIRE_COMPLEXITY`` and are enforced "
            "server-side."
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

    Two shapes packed into one schema so callers can branch on
    ``mfa_required`` without parsing different JSON layouts:

    * **Normal login (or MFA disabled / not enrolled)** —
      ``mfa_required=False``, ``access_token`` + ``refresh_token``
      populated, ``mfa_token=None``.
    * **MFA required (step 1 of two-step login)** —
      ``mfa_required=True``, ``access_token`` /
      ``refresh_token=None``, ``mfa_token`` populated. The
      frontend prompts for the TOTP code and replays it via
      ``POST /auth/mfa/verify`` to swap the short-lived token for
      the real JWT pair.

    The bundled router reuses this shape for both ``POST /auth/login``
    and ``POST /auth/password-reset/confirm`` since both flows end
    with an authenticated session.

    Attributes:
        user_id (UUID): UUID of the authenticated user.
        access_token (str | None): Short-lived JWT — populated only
            when ``mfa_required=False``.
        refresh_token (str | None): Long-lived JWT — populated
            only when ``mfa_required=False``.
        mfa_required (bool): When ``True``, the caller MUST submit
            the TOTP code via ``POST /auth/mfa/verify`` to
            complete the login.
        mfa_token (str | None): Short-lived JWT (5-minute TTL by
            default) the caller passes back to
            ``POST /auth/mfa/verify`` together with the TOTP code.
            Populated only when ``mfa_required=True``.
    """

    user_id: UUID = Field(
        title="User id",
        description="UUID of the authenticated user.",
        examples=["7d8e4d5a-9f4b-4c3a-bd0a-1234567890ab"],
    )
    access_token: str | None = Field(
        default=None,
        title="JWT access token",
        description=("Short-lived bearer token. ``None`` when ``mfa_required=True``."),
        examples=["eyJhbGciOi…", None],
    )
    refresh_token: str | None = Field(
        default=None,
        title="JWT refresh token",
        description=("Long-lived refresh token. ``None`` when ``mfa_required=True``."),
        examples=["eyJhbGciOi…", None],
    )
    mfa_required: bool = Field(
        default=False,
        title="MFA step required",
        description=(
            "When ``True``, the caller must complete step 2 via "
            "``POST /auth/mfa/verify``. ``False`` (default) signals "
            "a fully authenticated response — the JWT pair is in "
            "the body."
        ),
        examples=[False, True],
    )
    mfa_token: str | None = Field(
        default=None,
        title="Intermediate MFA token",
        description=(
            "Short-lived JWT (``AUTH_MFA_TOKEN_TTL_SECONDS``, 5min "
            "default) carrying the ``sub`` of the user awaiting "
            "step 2. ``None`` when ``mfa_required=False``."
        ),
        examples=[None, "eyJhbGciOi…"],
    )


class RefreshSchema(BaseSchema):
    """Request body for ``POST /auth/refresh``.

    Carries the long-lived refresh token so the caller can mint a fresh
    ``access_token`` + ``refresh_token`` pair without re-entering their
    email and password. Both tokens rotate on success — store the new
    refresh token and discard the one sent here.

    Attributes:
        refresh_token (str): The refresh JWT issued by login / signup /
            activation / password-reset / mfa-verify. Must still carry
            the ``refresh`` claim and not be expired.
    """

    refresh_token: str = Field(
        title="JWT refresh token",
        description="The long-lived refresh token to exchange for a new pair.",
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
        new_password (str): Plaintext replacement password. The schema
            only rejects empty strings; the effective minimum length
            and complexity come from ``AUTH_PASSWORD_MIN_LENGTH`` /
            ``AUTH_PASSWORD_REQUIRE_COMPLEXITY`` and are applied by the
            service.
    """

    token: str = Field(
        min_length=16,
        title="Reset token",
        description="Opaque token from the reset email / response body.",
        examples=["abc123def456…"],
    )
    new_password: str = Field(
        min_length=1,
        title="New password",
        description=(
            "Plaintext replacement password. The schema only rejects "
            "empty strings; the effective minimum length and complexity "
            "come from ``AUTH_PASSWORD_MIN_LENGTH`` / "
            "``AUTH_PASSWORD_REQUIRE_COMPLEXITY``, applied server-side."
        ),
        examples=["new-correct-horse-battery"],
    )


class PasswordChangeSchema(BaseSchema):
    """Request body for ``POST /auth/password-change``.

    Used by an **already-authenticated** user to rotate their own
    password. Unlike the reset flow there is no token — the bearer
    ``access_token`` identifies the user and ``current_password``
    re-confirms ownership before the new password is accepted.

    Attributes:
        current_password (str): The user's current plaintext password,
            re-entered for confirmation. A mismatch is rejected with
            ``401``.
        new_password (str): Plaintext replacement password. The schema
            only rejects empty strings; the effective minimum length
            and complexity come from ``AUTH_PASSWORD_MIN_LENGTH`` /
            ``AUTH_PASSWORD_REQUIRE_COMPLEXITY`` and are applied by the
            service.
    """

    current_password: str = Field(
        min_length=1,
        title="Current password",
        description="The user's current plaintext password, for confirmation.",
        examples=["my-old-password"],
    )
    new_password: str = Field(
        min_length=1,
        title="New password",
        description=(
            "Plaintext replacement password. The schema only rejects "
            "empty strings; the effective minimum length and complexity "
            "come from ``AUTH_PASSWORD_MIN_LENGTH`` / "
            "``AUTH_PASSWORD_REQUIRE_COMPLEXITY``, applied server-side."
        ),
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


class MFAEnrollResponseSchema(BaseSchema):
    """Response body for ``POST /auth/mfa/enroll`` — shown ONCE.

    The user is responsible for screenshotting / printing the
    payload before navigating away. The SDK does NOT re-show
    these values; calling ``enroll`` again rotates the secret
    and invalidates every previously issued recovery code.

    Attributes:
        secret (str): Base32 TOTP secret — exposed once so an
            advanced user can copy it manually into a desktop
            password manager (1Password, Bitwarden). Most users
            only scan the QR.
        provisioning_uri (str): ``otpauth://`` URI to render as a
            QR code. Authenticator apps scan it to import the
            secret + issuer + account name in one step.
        recovery_codes (list[str]): N single-use codes
            (``AUTH_MFA_RECOVERY_CODES_COUNT``, default 10).
            Display prominently — the user MUST save them
            somewhere offline.
    """

    secret: str = Field(
        title="TOTP secret (base32)",
        description=(
            "16-char base32 TOTP secret. Persisted on the user row; "
            "exposed in the response ONCE for manual import into "
            "desktop password managers."
        ),
        examples=["JBSWY3DPEHPK3PXP"],
    )
    provisioning_uri: str = Field(
        title="otpauth:// URI",
        description=(
            "Provisioning URI ready to be encoded as a QR code. "
            "Authenticator apps scan it to import the secret + "
            "issuer + account name in one step."
        ),
        examples=[
            "otpauth://totp/Acme:ana%40example.com?secret=JBSW…&issuer=Acme",
        ],
    )
    recovery_codes: list[str] = Field(
        default_factory=list,
        title="Single-use recovery codes",
        description=(
            "Plaintext recovery codes shown ONCE. Each can replace "
            "the TOTP code exactly once at login when the user "
            "loses access to their Authenticator app."
        ),
        examples=[["abcde-fghij", "klmno-pqrst", "uvwxy-zabcd"]],
    )


class MFAConfirmSchema(BaseSchema):
    """Request body for ``POST /auth/mfa/confirm``."""

    code: str = Field(
        min_length=6,
        max_length=8,
        title="TOTP code",
        description=(
            "6-digit code displayed by the Authenticator app. The "
            "SDK strips spaces / dashes before validation."
        ),
        examples=["123456"],
    )


class MFADisableSchema(BaseSchema):
    """Request body for ``POST /auth/mfa/disable``.

    Requires both the account password AND an active TOTP / recovery
    code so a hijacked session cannot silently disable MFA.
    """

    password: str = Field(
        min_length=1,
        title="Account password",
        description="Plaintext password — re-verified server-side.",
        examples=["strong-pass-12-chars"],
    )
    code: str = Field(
        min_length=6,
        max_length=16,
        title="TOTP code OR recovery code",
        description=(
            "Either a 6-digit code from the Authenticator OR one of "
            "the recovery codes printed at enrollment."
        ),
        examples=["123456", "abcde-fghij"],
    )


class MFAVerifySchema(BaseSchema):
    """Request body for ``POST /auth/mfa/verify``."""

    mfa_token: str = Field(
        min_length=1,
        title="Intermediate MFA token",
        description=(
            "Short-lived JWT returned by ``POST /auth/login`` "
            "when ``mfa_required=True``. Identifies the user the "
            "code belongs to without exposing the user id directly."
        ),
        examples=["eyJhbGciOi…"],
    )
    code: str = Field(
        min_length=6,
        max_length=16,
        title="TOTP code OR recovery code",
        description=(
            "6-digit Authenticator code OR a single-use recovery code from enrollment."
        ),
        examples=["123456", "abcde-fghij"],
    )


__all__: list[str] = [
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
]
