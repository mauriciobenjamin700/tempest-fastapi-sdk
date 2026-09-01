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
from typing import Any
from uuid import UUID

from pydantic import EmailStr, Field

from tempest_fastapi_sdk.schemas.base import BaseSchema
from tempest_fastapi_sdk.schemas.response import BaseResponseSchema


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


class LogoutSchema(BaseSchema):
    """Request body for ``POST /auth/logout``.

    Carries the refresh token to revoke so the session can be
    killed before its natural expiry. Only meaningful when the
    service is wired with a ``refresh_token_model`` (DB-backed
    refresh tokens) — in stateless mode the endpoint is not
    mounted, since a stateless JWT cannot be revoked.

    Attributes:
        refresh_token (str): The opaque refresh token to revoke.
            Its whole rotation *family* is revoked, so the
            descendant token a thief may hold dies too.
        all_sessions (bool): When ``True``, revoke **every** active
            refresh token the user owns (log out everywhere), not
            just this token's family. Defaults to ``False``.
    """

    refresh_token: str = Field(
        title="Refresh token",
        description="The opaque refresh token to revoke (its family is killed).",
        examples=["3f7c1a9e8b…"],
    )
    all_sessions: bool = Field(
        default=False,
        title="Revoke every session",
        description=(
            "When ``True``, revoke every active refresh token of the "
            "user (log out everywhere), not just this family."
        ),
        examples=[False, True],
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


class AuthUserSchema(BaseResponseSchema):
    """The authenticated account, as returned by ``GET /auth/me``.

    Covers exactly the columns
    :class:`~tempest_fastapi_sdk.db.user_model.BaseUserModel`
    guarantees, so it is safe as the default response model for any
    project: ``id`` / ``is_active`` / ``created_at`` / ``updated_at``
    come from :class:`BaseResponseSchema`, and ``email`` / ``is_admin``
    / ``last_login_at`` are added here.

    ``hashed_password`` is deliberately absent. FastAPI serializes the
    handler's return value **through** the response model, so a column
    the schema does not declare never reaches the wire — that is what
    makes the default safe even though the handler hands over the whole
    ORM instance.

    A project whose user table carries extra fields (a display name, an
    avatar, a tenant id) subclasses this and passes the subclass as
    ``me_response_model`` to
    :func:`tempest_fastapi_sdk.make_auth_router`::

        class UserResponseSchema(AuthUserSchema):
            name: str | None = None

    Attributes:
        email (str): The account's login identifier.
        is_admin (bool): Whether the account may reach the admin site.
        last_login_at (datetime | None): Timestamp of the most recent
            successful login; ``None`` for an account that never
            logged in.
    """

    email: EmailStr = Field(
        title="Email",
        description="The account's login identifier.",
        examples=["person@example.com"],
    )
    is_admin: bool = Field(
        title="Is Admin",
        description="Whether the account may reach the admin site.",
        examples=[False, True],
    )
    last_login_at: datetime | None = Field(
        default=None,
        title="Last Login At",
        description=(
            "Timestamp of the most recent successful login. ``None`` "
            "for an account that has never logged in."
        ),
        examples=["2024-01-02T12:00:00Z", None],
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


class EmailChangeRequestSchema(BaseSchema):
    """Request body for ``POST /auth/email-change/request``.

    Used by an **already-authenticated** user to start moving to a new
    email address. The bearer ``access_token`` identifies the user;
    ``current_password`` re-confirms ownership before the change is
    staged. A confirmation link is sent to the NEW address — the change
    only takes effect once that link is confirmed.

    Attributes:
        new_email (EmailStr): The address the user wants to move to.
        current_password (str): The user's current plaintext password,
            re-entered for confirmation. A mismatch returns ``401``.
    """

    new_email: EmailStr = Field(
        title="New email",
        description="The address the user wants to move the account to.",
        examples=["nova@example.com"],
    )
    current_password: str = Field(
        min_length=1,
        title="Current password",
        description="The user's current plaintext password, for confirmation.",
        examples=["my-current-password"],
    )


class EmailChangeConfirmSchema(BaseSchema):
    """Request body for ``POST /auth/email-change/confirm``.

    Carries the opaque token the user copied from the confirmation
    link sent to their new address. The service consumes the token
    (one-shot) and flips the account email to the staged value.

    Attributes:
        token (str): Opaque token issued by ``request``. Plaintext form
            — the SDK stores only the hash.
    """

    token: str = Field(
        min_length=16,
        title="Email-change token",
        description="Opaque token from the confirmation email / response body.",
        examples=["abc123def456…"],
    )


class EmailRecoveryRequestSchema(BaseSchema):
    """Request body for ``POST /auth/email-recovery/request``.

    The **unauthenticated** recovery entry point for a user who lost
    access to their mailbox. Identity is proven by the account password
    (and a valid MFA code when TOTP is enrolled) rather than a bearer
    token. Always returns ``202`` with a generic message so the endpoint
    can't be used to enumerate accounts. On success a confirmation link
    is sent to the NEW address and a security notice to the old one.

    Attributes:
        email (EmailStr): The account's CURRENT (old) email — locates
            the account.
        new_email (EmailStr): The address the user wants to move to.
        current_password (str): The account password, for identity proof.
        mfa_code (str | None): TOTP or recovery code — required when the
            account has MFA enrolled, ignored otherwise.
    """

    email: EmailStr = Field(
        title="Current email",
        description=(
            "The account's current (old) email. The endpoint always "
            "returns 202 — it never leaks whether the email exists."
        ),
        examples=["ana@example.com"],
    )
    new_email: EmailStr = Field(
        title="New email",
        description="The address the user wants to recover the account to.",
        examples=["nova@example.com"],
    )
    current_password: str = Field(
        min_length=1,
        title="Account password",
        description="The account's plaintext password, for identity proof.",
        examples=["my-current-password"],
    )
    mfa_code: str | None = Field(
        default=None,
        title="MFA code",
        description=(
            "6-digit TOTP code or a recovery code. Required when the "
            "account has MFA enrolled; ignored otherwise."
        ),
        examples=[None, "123456", "abcde-fghij"],
    )


class EmailChangeResponseSchema(BaseSchema):
    """Response body for the email change / verify / recovery ``request`` endpoints.

    ``message`` is a generic, constant string. ``confirm_url`` is
    populated only when ``AUTH_RETURN_TOKEN_IN_RESPONSE=True`` or when
    the ``[email]`` extra isn't installed — otherwise the link travels
    only through email.

    Attributes:
        message (str): Human-readable summary of the next step.
        confirm_url (str | None): Confirmation URL when the caller asked
            for an inline response, ``None`` in production.
    """

    message: str = Field(
        title="Message",
        description="Human-readable summary of the next step.",
        examples=["Check your new inbox to confirm the change."],
    )
    confirm_url: str | None = Field(
        default=None,
        title="Confirmation URL",
        description=(
            "Set only when ``AUTH_RETURN_TOKEN_IN_RESPONSE=True`` "
            "(dev mode) or when the ``[email]`` extra is missing. "
            "``None`` in production — the link only goes via email."
        ),
        examples=[None, "http://localhost:3000/confirm-email?token=…"],
    )


class EmailChangeToken(BaseSchema):
    """Service-level result of issuing an email-change token.

    Returned by :meth:`UserAuthService.request_email_change` /
    :meth:`request_email_recovery` when the caller asked the service to
    surface the link (``AUTH_RETURN_TOKEN_IN_RESPONSE=True`` or no
    :class:`EmailUtils` wired). The plaintext token is one-shot, hashed
    at rest, and expires after ``AUTH_EMAIL_CHANGE_TTL_SECONDS``.

    Attributes:
        user_id (UUID): UUID of the user the token authorizes.
        new_email (str): The pending new address the token confirms.
        token (str): Plaintext token — display once, never store.
        url (str): Confirmation URL with the token already substituted
            into ``AUTH_EMAIL_CHANGE_URL_TEMPLATE``.
        expires_at (datetime): UTC timestamp the token becomes invalid.
    """

    user_id: UUID = Field(
        title="User id",
        description="UUID of the user this email-change token authorizes.",
        examples=["7d8e4d5a-9f4b-4c3a-bd0a-1234567890ab"],
    )
    new_email: str = Field(
        title="New email",
        description="The pending new address the token confirms.",
        examples=["nova@example.com"],
    )
    token: str = Field(
        title="Plaintext token",
        description="Opaque token — display once, never persist in cleartext.",
        examples=["abc123def456…"],
    )
    url: str = Field(
        title="Confirmation URL",
        description=(
            "Confirmation URL with the token already substituted. "
            "Derived from ``AUTH_EMAIL_CHANGE_URL_TEMPLATE``."
        ),
        examples=["http://localhost:3000/confirm-email?token=abc123…"],
    )
    expires_at: datetime = Field(
        title="Expires at",
        description="UTC timestamp the token becomes invalid.",
        examples=["2026-06-04T17:00:00Z"],
    )


class EmailVerificationToken(BaseSchema):
    """Service-level result of issuing an email re-verification token.

    Returned by :meth:`UserAuthService.request_email_verification` when
    the caller asked the service to surface the link. Confirms the
    user's CURRENT email (no address change). Expires after
    ``AUTH_EMAIL_VERIFICATION_TTL_SECONDS``.

    Attributes:
        user_id (UUID): UUID of the user the token authorizes.
        token (str): Plaintext token — display once, never store.
        url (str): Verification URL with the token already substituted
            into ``AUTH_EMAIL_VERIFICATION_URL_TEMPLATE``.
        expires_at (datetime): UTC timestamp the token becomes invalid.
    """

    user_id: UUID = Field(
        title="User id",
        description="UUID of the user this verification token authorizes.",
        examples=["7d8e4d5a-9f4b-4c3a-bd0a-1234567890ab"],
    )
    token: str = Field(
        title="Plaintext token",
        description="Opaque token — display once, never persist in cleartext.",
        examples=["abc123def456…"],
    )
    url: str = Field(
        title="Verification URL",
        description=(
            "Verification URL with the token already substituted. "
            "Derived from ``AUTH_EMAIL_VERIFICATION_URL_TEMPLATE``."
        ),
        examples=["http://localhost:3000/verify-email?token=abc123…"],
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


class WebAuthnOptionsSchema(BaseSchema):
    """Response body for both ``/auth/webauthn/*/begin`` endpoints.

    Attributes:
        challenge_id (str): Handle naming the server-side ceremony
            state. Echo it back on the matching ``/complete`` call; it
            is single-use, so a captured response cannot be replayed.
        options (dict[str, Any]): The ``publicKey`` payload to hand
            straight to ``navigator.credentials``. Passed through
            verbatim — the shape is the browser's contract, not the
            SDK's, so the SDK does not reshape it.
    """

    challenge_id: str = Field(
        title="Ceremony handle",
        description=(
            "Names the server-side challenge for this ceremony. Send it "
            "back on the ``/complete`` call. Single-use and short-lived "
            "(``AUTH_WEBAUTHN_CHALLENGE_TTL_SECONDS``)."
        ),
        examples=["Rk9vYmFyQmF6UXV1eA"],
    )
    options: dict[str, Any] = Field(
        title="WebAuthn options",
        description=(
            "The object the browser expects: pass ``options.publicKey`` "
            "to ``navigator.credentials.create()`` (registration) or "
            "``navigator.credentials.get()`` (login)."
        ),
        examples=[{"publicKey": {"challenge": "…", "rpId": "example.com"}}],
    )


class WebAuthnRegisterCompleteSchema(BaseSchema):
    """Request body for ``POST /auth/webauthn/register/complete``.

    Attributes:
        challenge_id (str): Handle returned by the ``begin`` call.
        credential (dict[str, Any]): The registration response, as
            produced by the browser's WebAuthn JSON serialization.
        name (str | None): Label for this authenticator.
    """

    challenge_id: str = Field(
        min_length=1,
        title="Ceremony handle",
        description="The ``challenge_id`` returned by the begin call.",
        examples=["Rk9vYmFyQmF6UXV1eA"],
    )
    credential: dict[str, Any] = Field(
        title="Registration response",
        description=(
            "What ``navigator.credentials.create()`` returned, serialized "
            "with ``PublicKeyCredential.toJSON()`` (or the equivalent "
            "base64url encoding your client library produces)."
        ),
        examples=[{"id": "…", "rawId": "…", "type": "public-key", "response": {}}],
    )
    name: str | None = Field(
        default=None,
        max_length=120,
        title="Authenticator label",
        description=(
            "Shown in the credential list so a person holding several "
            "passkeys can tell them apart."
        ),
        examples=["YubiKey 5", "iPhone"],
    )


class WebAuthnAuthenticateBeginSchema(BaseSchema):
    """Request body for ``POST /auth/webauthn/authenticate/begin``.

    Attributes:
        email (str | None): Narrows the ceremony to one account's
            credentials. Omit it for the usernameless flow, where the
            authenticator picks the account.
    """

    email: str | None = Field(
        default=None,
        title="Account email (optional)",
        description=(
            "Omit for the passwordless flow — the authenticator offers "
            "the accounts it stores. Pass it to help an authenticator "
            "that keeps no discoverable credential. An unknown address "
            "yields a normal ceremony with an empty credential list, so "
            "the endpoint cannot be used to enumerate accounts."
        ),
        examples=[None, "ana@example.com"],
    )


class WebAuthnAuthenticateCompleteSchema(BaseSchema):
    """Request body for ``POST /auth/webauthn/authenticate/complete``.

    Attributes:
        challenge_id (str): Handle returned by the ``begin`` call.
        credential (dict[str, Any]): The assertion, as produced by the
            browser's WebAuthn JSON serialization.
    """

    challenge_id: str = Field(
        min_length=1,
        title="Ceremony handle",
        description="The ``challenge_id`` returned by the begin call.",
        examples=["Rk9vYmFyQmF6UXV1eA"],
    )
    credential: dict[str, Any] = Field(
        title="Authentication response",
        description=(
            "What ``navigator.credentials.get()`` returned, serialized "
            "with ``PublicKeyCredential.toJSON()``."
        ),
        examples=[{"id": "…", "rawId": "…", "type": "public-key", "response": {}}],
    )


class WebAuthnCredentialSchema(BaseSchema):
    """One registered authenticator, as returned by the listing endpoint.

    Attributes:
        credential_id (str): Base64url credential ID — the value to pass
            back when deleting this credential.
        name (str | None): User-supplied label.
        transports (str | None): Comma-separated transport hints.
        aaguid (str | None): Authenticator model identifier, hex.
        backed_up (bool): Whether the credential is synced.
        created_at (datetime): Registration timestamp.
        last_used_at (datetime | None): Last successful assertion.
    """

    credential_id: str = Field(
        title="Credential ID (base64url)",
        description="Identifies the credential in the delete endpoint.",
        examples=["AQIDBAUGBwgJCgsMDQ4PEA"],
    )
    name: str | None = Field(
        default=None,
        title="Authenticator label",
        description="User-supplied label, or ``null`` when unnamed.",
        examples=["YubiKey 5", None],
    )
    transports: str | None = Field(
        default=None,
        title="Transport hints",
        description="Comma-separated transports reported at registration.",
        examples=["usb,nfc", "internal", None],
    )
    aaguid: str | None = Field(
        default=None,
        title="Authenticator model (AAGUID, hex)",
        description="Informational — never use it for authorization.",
        examples=["d8522d9f575b486688a9ba99fa02f35b"],
    )
    backed_up: bool = Field(
        title="Backed up (synced passkey)",
        description=(
            "``True`` when the authenticator reported the credential as "
            "backed up. A device-bound credential is lost with the "
            "device; a synced one is not."
        ),
        examples=[True, False],
    )
    created_at: datetime = Field(
        title="Registered at",
        description="When the credential was registered.",
        examples=["2026-08-13T12:00:00Z"],
    )
    last_used_at: datetime | None = Field(
        default=None,
        title="Last used at",
        description="Last successful assertion, or ``null`` if never used.",
        examples=["2026-08-13T18:30:00Z", None],
    )


class WebAuthnDeleteSchema(BaseSchema):
    """Request body for ``POST /auth/webauthn/credentials/delete``.

    Attributes:
        credential_id (str): Base64url credential ID to remove.
    """

    credential_id: str = Field(
        min_length=1,
        title="Credential ID (base64url)",
        description="The value the listing endpoint returned.",
        examples=["AQIDBAUGBwgJCgsMDQ4PEA"],
    )


class OAuthAccountSchema(BaseSchema):
    """One linked social identity, as returned by the listing endpoint.

    Deliberately omits the raw provider payload
    (:attr:`~tempest_fastapi_sdk.OAuthUser.raw`): it is whatever the
    IdP felt like sending, it is not part of any contract, and it is
    the field most likely to carry something the account owner did not
    expect to see echoed back.

    Attributes:
        provider (str): Provider key — the value to pass back when
            unlinking.
        subject (str): The provider's stable id for this person.
            Opaque; useful for support, never for lookup by the client.
        email (str | None): Email the provider reported at the last
            login through this link.
        email_verified (bool | None): Whether the provider stated it
            verified that address. ``None`` means it said nothing.
        name (str | None): Display name the provider reported.
        picture (str | None): Avatar URL the provider reported.
        created_at (datetime): When the identity was linked.
        last_login_at (datetime | None): Last login through this link.
    """

    provider: str = Field(
        title="Provider key",
        description="Identifies the link in the unlink endpoint.",
        examples=["google", "github", "oidc:auth0"],
    )
    subject: str = Field(
        title="Provider subject",
        description="The provider's stable id for this person.",
        examples=["101234567890123456789"],
    )
    email: str | None = Field(
        default=None,
        title="Email at the provider",
        description="Email the provider reported at the last login.",
        examples=["person@example.com", None],
    )
    email_verified: bool | None = Field(
        default=None,
        title="Provider verified the email",
        description=(
            "Whether the provider stated it verified the address. "
            "``null`` means the provider said nothing either way, "
            "which is not the same as ``false``."
        ),
        examples=[True, False, None],
    )
    name: str | None = Field(
        default=None,
        title="Display name at the provider",
        description="Display name the provider reported.",
        examples=["Ana Souza", None],
    )
    picture: str | None = Field(
        default=None,
        title="Avatar URL",
        description="Profile picture URL the provider reported.",
        examples=["https://lh3.googleusercontent.com/a/…", None],
    )
    created_at: datetime = Field(
        title="Linked at",
        description="When this identity was first linked to the account.",
        examples=["2024-01-02T12:00:00Z"],
    )
    last_login_at: datetime | None = Field(
        default=None,
        title="Last login through this link",
        description=(
            "Timestamp of the most recent callback completed through "
            "this provider. ``null`` before the first one."
        ),
        examples=["2024-01-02T12:00:00Z", None],
    )


class OAuthUnlinkSchema(BaseSchema):
    """Request body for ``POST /auth/oauth/accounts/unlink``.

    Attributes:
        provider (str): Provider key to detach, as the listing endpoint
            reported it.
    """

    provider: str = Field(
        min_length=1,
        title="Provider key",
        description="The value the listing endpoint returned.",
        examples=["google", "github"],
    )


class OAuthTokenLoginSchema(BaseSchema):
    """Request body for ``POST /auth/oauth/{provider}/token``.

    The token-in-hand half of social login. A native mobile client
    completes the provider's own SDK flow on the device and ends up
    holding an access token, with no browser to redirect through the
    ``/login`` → consent → ``/callback`` dance. It posts that token
    here and gets back the same session the redirect flow issues.

    The token travels in the **body**, never in the path or the query
    string: a URL is written to the access log, the browser history and
    every ``Referer`` header on the way, and this value is a live
    credential at the provider.

    Attributes:
        access_token (str): The provider's OAuth2 access token, as the
            device SDK returned it. Exchanged for the profile through
            the registered client's ``fetch_user``, so a forged or
            expired one fails at the provider rather than here.
        token_type (str): Scheme the ``Authorization`` header will use
            when the SDK calls the provider's userinfo endpoint.
            Defaults to ``Bearer``, which is what every provider the SDK
            ships a client for expects.
    """

    access_token: str = Field(
        min_length=1,
        title="Provider access token",
        description=(
            "The OAuth2 access token the device SDK obtained from the "
            "provider. Sent in the body — never in the URL — because it "
            "is a live credential."
        ),
        examples=["ya29.a0AfH6SM..."],
    )
    token_type: str = Field(
        default="Bearer",
        min_length=1,
        title="Token type",
        description=(
            "Authorization scheme used when calling the provider's userinfo endpoint."
        ),
        examples=["Bearer"],
    )


__all__: list[str] = [
    "ActivationResponseSchema",
    "ActivationToken",
    "AuthUserSchema",
    "EmailChangeConfirmSchema",
    "EmailChangeRequestSchema",
    "EmailChangeResponseSchema",
    "EmailChangeToken",
    "EmailRecoveryRequestSchema",
    "EmailVerificationToken",
    "LoginResponseSchema",
    "LoginSchema",
    "LogoutSchema",
    "MFAConfirmSchema",
    "MFADisableSchema",
    "MFAEnrollResponseSchema",
    "MFAVerifySchema",
    "OAuthAccountSchema",
    "OAuthTokenLoginSchema",
    "OAuthUnlinkSchema",
    "PasswordChangeSchema",
    "PasswordResetConfirmSchema",
    "PasswordResetRequestSchema",
    "PasswordResetResponseSchema",
    "PasswordResetToken",
    "SignupResponseSchema",
    "SignupSchema",
    "WebAuthnAuthenticateBeginSchema",
]
