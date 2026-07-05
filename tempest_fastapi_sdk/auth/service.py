"""User auth flows — signup, activation, login, password reset.

Implements every step of the local-account lifecycle on top of
the SDK primitives:

* :class:`tempest_fastapi_sdk.PasswordUtils` for bcrypt hashing.
* :class:`tempest_fastapi_sdk.JWTUtils` for token issuance.
* :func:`tempest_fastapi_sdk.generate_opaque_token` /
  :func:`hash_opaque_token` for one-shot activation + reset
  tokens (plaintext returned once, hash persisted).
* :class:`tempest_fastapi_sdk.EmailUtils` (optional) for
  template-rendered transactional mail.

The service is generic over the concrete ``UserModel`` and
``UserTokenModel`` the consuming project ships — pattern matches
the rest of the SDK so the same code runs against any
:class:`BaseUserModel` subclass.
"""

from __future__ import annotations

from datetime import datetime, timedelta
from typing import TYPE_CHECKING
from uuid import UUID, uuid4

from sqlalchemy import delete, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from tempest_fastapi_sdk.auth.guards import (
    UserT,
    require_active,
    require_admin,
    require_authenticated,
)
from tempest_fastapi_sdk.auth.locale import (
    auth_email_message,
    format_expires_at,
)
from tempest_fastapi_sdk.auth.schemas import (
    ActivationToken,
    EmailChangeToken,
    EmailVerificationToken,
    PasswordResetToken,
)
from tempest_fastapi_sdk.db.user_token_model import (
    BaseUserTokenModel,
    UserTokenPurpose,
)
from tempest_fastapi_sdk.exceptions import (
    ConflictException,
    InvalidTokenException,
    NotFoundException,
    UnauthorizedException,
    ValidationException,
)
from tempest_fastapi_sdk.utils.datetime import utcnow
from tempest_fastapi_sdk.utils.jwt import JWTUtils
from tempest_fastapi_sdk.utils.opaque_token import (
    generate_opaque_token,
    hash_opaque_token,
)
from tempest_fastapi_sdk.utils.password import PasswordUtils

if TYPE_CHECKING:
    from collections.abc import Callable, Coroutine
    from typing import Any

    from tempest_fastapi_sdk.db.connection import AsyncDatabaseManager
    from tempest_fastapi_sdk.db.user_model import BaseUserModel
    from tempest_fastapi_sdk.db.user_recovery_code_model import (
        BaseUserRecoveryCodeModel,
    )
    from tempest_fastapi_sdk.db.user_refresh_token_model import (
        BaseUserRefreshTokenModel,
    )
    from tempest_fastapi_sdk.settings.mixins import AuthSettings, JWTSettings
    from tempest_fastapi_sdk.utils.email import EmailUtils


class UserAuthService:
    """Compose ``UserModel`` + ``UserTokenModel`` into a full auth flow.

    Example:

        >>> service = UserAuthService(
        ...     db=db,
        ...     user_model=UserModel,
        ...     token_model=UserTokenModel,
        ...     auth_settings=settings,
        ...     jwt_settings=settings,
        ...     email=email_utils,
        ... )
        >>> async with db.get_session_context() as s:
        ...     result = await service.signup(s, payload)

    The core flow methods take the active ``AsyncSession`` explicitly
    so callers control the transaction boundary. The only exception is
    :meth:`load_user` (and the :meth:`current_user_dependency` it backs),
    which opens its own short-lived session from the ``db=`` handle so
    it can serve as a one-argument FastAPI dependency loader.
    """

    def __init__(
        self,
        *,
        user_model: type[BaseUserModel],
        token_model: type[BaseUserTokenModel],
        auth_settings: AuthSettings,
        jwt_settings: JWTSettings,
        email: EmailUtils | None = None,
        passwords: PasswordUtils | None = None,
        jwt: JWTUtils | None = None,
        db: AsyncDatabaseManager | None = None,
        refresh_token_model: type[BaseUserRefreshTokenModel] | None = None,
    ) -> None:
        """Initialize the service.

        Args:
            user_model (type[BaseUserModel]): Concrete user model
                — usually ``src.db.models.UserModel``.
            token_model (type[BaseUserTokenModel]): Concrete token
                model — usually ``src.db.models.UserTokenModel``.
            auth_settings (AuthSettings): The mixin populating
                activation / reset behavior.
            jwt_settings (JWTSettings): The mixin populating
                signing keys and TTLs.
            email (EmailUtils | None): Configured email helper.
                When ``None``, the service always returns the link
                in the response (and never tries to send).
            passwords (PasswordUtils | None): Override for tests;
                defaults to a fresh instance.
            jwt (JWTUtils | None): Override for tests; defaults
                to one built from ``jwt_settings``.
            db (AsyncDatabaseManager | None): Optional handle for
                services that open their own sessions inside
                helpers like background tasks.
            refresh_token_model (type[BaseUserRefreshTokenModel] | None):
                Concrete refresh-token model — usually
                ``src.db.models.UserRefreshTokenModel``. **Opt-in:**
                when provided, refresh tokens become opaque,
                DB-backed, single-use values with real rotation,
                reuse detection and revocation. When ``None``
                (default), the service keeps issuing the legacy
                stateless JWT refresh token (no DB persistence, no
                revocation).
        """
        self.user_model: type[BaseUserModel] = user_model
        self.token_model: type[BaseUserTokenModel] = token_model
        self.auth_settings: AuthSettings = auth_settings
        self.jwt_settings: JWTSettings = jwt_settings
        self.email: EmailUtils | None = email
        self.passwords: PasswordUtils = passwords or PasswordUtils()
        self.jwt: JWTUtils = jwt or JWTUtils(
            secret=jwt_settings.JWT_SECRET,
            algorithm=jwt_settings.JWT_ALGORITHM,
        )
        self.db: AsyncDatabaseManager | None = db
        self.refresh_token_model: type[BaseUserRefreshTokenModel] | None = (
            refresh_token_model
        )

    # ------------------------------------------------------------------
    # Signup
    # ------------------------------------------------------------------

    async def signup(
        self,
        session: AsyncSession,
        *,
        email: str,
        password: str,
        name: str | None = None,
    ) -> tuple[BaseUserModel, ActivationToken | None]:
        """Create a user row and (optionally) issue an activation token.

        When ``AUTH_AUTO_ACTIVATE`` is true, the user is marked
        ``is_active=True`` immediately and ``None`` is returned in
        the second tuple slot — the caller can mint JWTs right
        away. Otherwise the user is inserted with ``is_active=False``
        and an activation token is returned for the caller to mail
        or echo back.

        Args:
            session (AsyncSession): Active SQLAlchemy session.
            email (str): Account email — normalized to lowercase.
            password (str): Plaintext password; length is enforced
                against ``AUTH_PASSWORD_MIN_LENGTH``.
            name (str | None): Optional display name; passed
                through to the model when the column exists.

        Returns:
            tuple[BaseUserModel, ActivationToken | None]: The
            persisted user and (when activation is required) the
            token to surface.

        Raises:
            ValidationException: When the password is too short.
            ConflictException: When the email is already taken.
        """
        self._enforce_password_policy(password)
        normalized = email.strip().lower()
        existing = await session.execute(
            select(self.user_model).where(
                self.user_model.email == normalized,
            )
        )
        if existing.scalar_one_or_none() is not None:
            raise ConflictException(
                message="email already in use",
                details={"email": normalized},
            )

        user = self.user_model(
            email=normalized,
            is_active=self.auth_settings.AUTH_AUTO_ACTIVATE,
        )
        user.hashed_password = self.passwords.hash(password)
        if name is not None and hasattr(user, "name"):
            user.name = name
        session.add(user)
        await session.flush()
        await session.refresh(user)

        if self.auth_settings.AUTH_AUTO_ACTIVATE:
            return user, None

        activation = await self._issue_token(
            session,
            user_id=user.id,
            purpose=UserTokenPurpose.ACTIVATION,
            ttl_seconds=self.auth_settings.AUTH_ACTIVATION_TTL_SECONDS,
            url_template=self.auth_settings.AUTH_ACTIVATION_URL_TEMPLATE,
        )
        await self._maybe_send_activation_email(user, activation)
        return user, ActivationToken(
            user_id=user.id,
            token=activation[0],
            url=activation[1],
            expires_at=activation[2],
        )

    # ------------------------------------------------------------------
    # Activation
    # ------------------------------------------------------------------

    async def activate(
        self,
        session: AsyncSession,
        *,
        token: str,
    ) -> BaseUserModel:
        """Consume an activation token and flip ``is_active`` on the user.

        Args:
            session (AsyncSession): Active SQLAlchemy session.
            token (str): Plaintext token from the activation URL.

        Returns:
            BaseUserModel: The freshly-activated user.

        Raises:
            InvalidTokenException: When the token is malformed,
                expired, already used, or doesn't match a row.
        """
        record = await self._consume_token(
            session,
            token=token,
            purpose=UserTokenPurpose.ACTIVATION,
        )
        user: BaseUserModel | None = await session.get(self.user_model, record.user_id)
        if user is None:
            raise InvalidTokenException(message="token references a missing user")
        user.is_active = True
        await session.flush()
        await session.refresh(user)
        return user

    # ------------------------------------------------------------------
    # Login
    # ------------------------------------------------------------------

    async def login(
        self,
        session: AsyncSession,
        *,
        email: str,
        password: str,
    ) -> BaseUserModel:
        """Validate credentials and return the matching user row.

        Args:
            session (AsyncSession): Active SQLAlchemy session.
            email (str): Login identifier.
            password (str): Plaintext password.

        Returns:
            BaseUserModel: The authenticated user.

        Raises:
            UnauthorizedException: On any failure — wrong password,
                missing user, inactive user. The message is
                deliberately generic so attackers can't enumerate
                accounts.
        """
        normalized = email.strip().lower()
        user_result = await session.execute(
            select(self.user_model).where(
                self.user_model.email == normalized,
            )
        )
        user_obj = user_result.scalar_one_or_none()
        user: BaseUserModel | None = user_obj
        if user is None or not user.is_active:
            raise UnauthorizedException(message="invalid email or password")
        if not self.passwords.verify(password, user.hashed_password):
            raise UnauthorizedException(message="invalid email or password")
        user.last_login_at = utcnow()
        await session.flush()
        await session.refresh(user)
        return user

    # ------------------------------------------------------------------
    # Password reset
    # ------------------------------------------------------------------

    async def request_password_reset(
        self,
        session: AsyncSession,
        *,
        email: str,
    ) -> PasswordResetToken | None:
        """Mint a one-shot reset token for ``email``.

        Returns ``None`` when no user matches — callers should
        still respond ``202`` to avoid leaking account
        existence. Sends the email (when ``EmailUtils`` is wired)
        or returns the token for inline display per the
        ``AUTH_RETURN_TOKEN_IN_RESPONSE`` flag.

        Args:
            session (AsyncSession): Active SQLAlchemy session.
            email (str): Account email.

        Returns:
            PasswordResetToken | None: The token bundle when the
            caller is configured to surface the link, ``None``
            when the link is meant to live only in the email.
        """
        normalized = email.strip().lower()
        user_result = await session.execute(
            select(self.user_model).where(
                self.user_model.email == normalized,
            )
        )
        user_obj = user_result.scalar_one_or_none()
        user: BaseUserModel | None = user_obj
        if user is None:
            return None

        reset = await self._issue_token(
            session,
            user_id=user.id,
            purpose=UserTokenPurpose.PASSWORD_RESET,
            ttl_seconds=self.auth_settings.AUTH_PASSWORD_RESET_TTL_SECONDS,
            url_template=self.auth_settings.AUTH_PASSWORD_RESET_URL_TEMPLATE,
        )
        await self._maybe_send_password_reset_email(user, reset)

        if self.auth_settings.AUTH_RETURN_TOKEN_IN_RESPONSE or self.email is None:
            return PasswordResetToken(
                user_id=user.id,
                token=reset[0],
                url=reset[1],
                expires_at=reset[2],
            )
        return None

    async def confirm_password_reset(
        self,
        session: AsyncSession,
        *,
        token: str,
        new_password: str,
    ) -> BaseUserModel:
        """Consume a reset token and replace the user's password.

        Args:
            session (AsyncSession): Active SQLAlchemy session.
            token (str): Plaintext token from the reset URL.
            new_password (str): Plaintext replacement password.

        Returns:
            BaseUserModel: The user whose password was rotated.

        Raises:
            ValidationException: When the new password is too short.
            InvalidTokenException: On bad / expired / spent tokens.
        """
        self._enforce_password_policy(new_password)
        record = await self._consume_token(
            session,
            token=token,
            purpose=UserTokenPurpose.PASSWORD_RESET,
        )
        user: BaseUserModel | None = await session.get(self.user_model, record.user_id)
        if user is None:
            raise NotFoundException(message="user not found")
        user.hashed_password = self.passwords.hash(new_password)
        await session.flush()
        await session.refresh(user)
        return user

    async def change_password(
        self,
        session: AsyncSession,
        *,
        user: BaseUserModel,
        current_password: str,
        new_password: str,
    ) -> BaseUserModel:
        """Replace an authenticated user's password after re-auth.

        The "change my own password while logged in" flow: the caller is
        already authenticated (the router resolves ``user`` from the
        bearer token), and must prove ownership by supplying their
        ``current_password`` before the new one is accepted. No token is
        involved — this is distinct from the email-driven reset flow
        (:meth:`request_password_reset` / :meth:`confirm_password_reset`).

        Args:
            session (AsyncSession): Active SQLAlchemy session.
            user (BaseUserModel): The authenticated user (already loaded
                from the JWT subject).
            current_password (str): The user's current plaintext password,
                re-entered for confirmation.
            new_password (str): The plaintext replacement password.

        Returns:
            BaseUserModel: The user whose password was rotated.

        Raises:
            UnauthorizedException: When ``current_password`` does not match
                the stored hash.
            ValidationException: When ``new_password`` violates the
                configured password policy.
        """
        if not self.passwords.verify(current_password, user.hashed_password):
            raise UnauthorizedException(message="current password is incorrect")
        self._enforce_password_policy(new_password)
        user.hashed_password = self.passwords.hash(new_password)
        await session.flush()
        await session.refresh(user)
        return user

    # ------------------------------------------------------------------
    # Email change / re-verification / recovery
    # ------------------------------------------------------------------

    async def request_email_change(
        self,
        session: AsyncSession,
        *,
        user: BaseUserModel,
        current_password: str,
        new_email: str,
    ) -> EmailChangeToken | None:
        """Stage a move to ``new_email`` for an authenticated user.

        The "change my email while logged in" flow, mirroring
        :meth:`change_password`: the caller is already authenticated
        (the router resolves ``user`` from the bearer token) and proves
        ownership with ``current_password``. A single-use ``EMAIL_CHANGE``
        token carrying the pending address in its payload is issued, and a
        confirmation link is emailed to the **new** address. The account
        email is NOT touched until :meth:`confirm_email_change` consumes
        the token.

        Args:
            session (AsyncSession): Active SQLAlchemy session.
            user (BaseUserModel): The authenticated user.
            current_password (str): The user's current plaintext password,
                re-entered for confirmation.
            new_email (str): The address to move to — normalized to
                lowercase.

        Returns:
            EmailChangeToken | None: The token bundle when the caller is
            configured to surface the link (``AUTH_RETURN_TOKEN_IN_RESPONSE``
            or no ``EmailUtils``), ``None`` when the link only travels by
            email.

        Raises:
            UnauthorizedException: When ``current_password`` is wrong.
            ValidationException: When ``new_email`` equals the current one.
            ConflictException: When ``new_email`` is already in use.
        """
        if not self.passwords.verify(current_password, user.hashed_password):
            raise UnauthorizedException(message="current password is incorrect")
        normalized = new_email.strip().lower()
        if normalized == user.email:
            raise ValidationException(
                message="new email is the same as the current one",
            )
        if await self._email_taken(session, normalized, exclude_user_id=user.id):
            raise ConflictException(
                message="email already in use",
                details={"email": normalized},
            )
        bundle = await self._issue_token(
            session,
            user_id=user.id,
            purpose=UserTokenPurpose.EMAIL_CHANGE,
            ttl_seconds=self.auth_settings.AUTH_EMAIL_CHANGE_TTL_SECONDS,
            url_template=self.auth_settings.AUTH_EMAIL_CHANGE_URL_TEMPLATE,
            payload=normalized,
        )
        await self._maybe_send_email_change_email(user, normalized, bundle)
        if self.auth_settings.AUTH_RETURN_TOKEN_IN_RESPONSE or self.email is None:
            return EmailChangeToken(
                user_id=user.id,
                new_email=normalized,
                token=bundle[0],
                url=bundle[1],
                expires_at=bundle[2],
            )
        return None

    async def confirm_email_change(
        self,
        session: AsyncSession,
        *,
        token: str,
    ) -> BaseUserModel:
        """Consume an ``EMAIL_CHANGE`` token and apply the pending address.

        Reads the pending new email from the token payload, re-checks it
        is still free (an address can be taken between request and
        confirm), flips ``user.email`` and — when
        ``AUTH_EMAIL_CHANGE_NOTIFY_OLD`` is on — sends a security notice
        to the previous address.

        Args:
            session (AsyncSession): Active SQLAlchemy session.
            token (str): Plaintext token from the confirmation link.

        Returns:
            BaseUserModel: The user whose email was changed.

        Raises:
            InvalidTokenException: On bad / expired / spent tokens, a
                missing payload, or a missing user.
            ConflictException: When the target email was taken meanwhile.
        """
        record = await self._consume_token(
            session,
            token=token,
            purpose=UserTokenPurpose.EMAIL_CHANGE,
        )
        new_email = (record.payload or "").strip().lower()
        if not new_email:
            raise InvalidTokenException(
                message="email-change token has no target address",
            )
        user: BaseUserModel | None = await session.get(self.user_model, record.user_id)
        if user is None:
            raise InvalidTokenException(message="token references a missing user")
        if await self._email_taken(session, new_email, exclude_user_id=user.id):
            raise ConflictException(
                message="email already in use",
                details={"email": new_email},
            )
        old_email = user.email
        user.email = new_email
        await session.flush()
        await session.refresh(user)
        await self._maybe_send_email_changed_notice(user, old_email, new_email)
        return user

    async def request_email_verification(
        self,
        session: AsyncSession,
        *,
        user: BaseUserModel,
    ) -> EmailVerificationToken | None:
        """Issue a re-verification token for the user's CURRENT email.

        Resends a "confirm you own this address" link to the account's
        existing email — no address change. Confirming it via
        :meth:`confirm_email_verification` marks the user active.

        Args:
            session (AsyncSession): Active SQLAlchemy session.
            user (BaseUserModel): The user re-verifying.

        Returns:
            EmailVerificationToken | None: The token bundle when the
            caller surfaces the link, ``None`` when it only travels by
            email.
        """
        bundle = await self._issue_token(
            session,
            user_id=user.id,
            purpose=UserTokenPurpose.EMAIL_VERIFICATION,
            ttl_seconds=self.auth_settings.AUTH_EMAIL_VERIFICATION_TTL_SECONDS,
            url_template=self.auth_settings.AUTH_EMAIL_VERIFICATION_URL_TEMPLATE,
        )
        await self._maybe_send_email_verification_email(user, bundle)
        if self.auth_settings.AUTH_RETURN_TOKEN_IN_RESPONSE or self.email is None:
            return EmailVerificationToken(
                user_id=user.id,
                token=bundle[0],
                url=bundle[1],
                expires_at=bundle[2],
            )
        return None

    async def confirm_email_verification(
        self,
        session: AsyncSession,
        *,
        token: str,
    ) -> BaseUserModel:
        """Consume an ``EMAIL_VERIFICATION`` token and mark the user active.

        Args:
            session (AsyncSession): Active SQLAlchemy session.
            token (str): Plaintext token from the verification link.

        Returns:
            BaseUserModel: The verified user.

        Raises:
            InvalidTokenException: On bad / expired / spent tokens, or a
                missing user.
        """
        record = await self._consume_token(
            session,
            token=token,
            purpose=UserTokenPurpose.EMAIL_VERIFICATION,
        )
        user: BaseUserModel | None = await session.get(self.user_model, record.user_id)
        if user is None:
            raise InvalidTokenException(message="token references a missing user")
        user.is_active = True
        await session.flush()
        await session.refresh(user)
        return user

    async def request_email_recovery(
        self,
        session: AsyncSession,
        *,
        email: str,
        new_email: str,
        current_password: str,
        mfa_code: str | None = None,
        recovery_code_model: type[BaseUserRecoveryCodeModel] | None = None,
    ) -> EmailChangeToken | None:
        """Start an email move for a user who lost access to their mailbox.

        The **unauthenticated** recovery entry point. The account is
        located by its current (old) ``email``; identity is proven by
        ``current_password`` and — when the account has MFA enrolled — a
        valid ``mfa_code``. On success an ``EMAIL_CHANGE`` token is issued
        and the confirmation link emailed to the NEW address; confirming
        it runs through the same :meth:`confirm_email_change` path (which
        notifies the old address).

        To avoid account enumeration this returns ``None`` (the router
        answers a generic ``202``) for every soft failure — unknown email,
        wrong password, or a missing/invalid MFA code. The only hard error
        is a target address already in use, or a deployment that enabled
        recovery without wiring ``recovery_code_model`` for an MFA user.

        Args:
            session (AsyncSession): Active SQLAlchemy session.
            email (str): The account's current (old) email.
            new_email (str): The address to recover the account to.
            current_password (str): The account password, for identity
                proof.
            mfa_code (str | None): TOTP or recovery code, required when
                the account has MFA enrolled.
            recovery_code_model (type[BaseUserRecoveryCodeModel] | None):
                The project's recovery-code model, needed to verify a
                recovery-code ``mfa_code``.

        Returns:
            EmailChangeToken | None: The token bundle when the caller
            surfaces the link, ``None`` otherwise (including every soft
            identity-proof failure).

        Raises:
            ValidationException: When ``recovery_code_model`` is missing
                for an MFA-enrolled account, or ``new_email`` equals the
                current one.
            ConflictException: When ``new_email`` is already in use.
        """
        normalized_old = email.strip().lower()
        result = await session.execute(
            select(self.user_model).where(self.user_model.email == normalized_old),
        )
        user: BaseUserModel | None = result.scalar_one_or_none()
        if user is None:
            return None
        if not self.passwords.verify(current_password, user.hashed_password):
            return None
        if self.is_mfa_enrolled(user):
            if not mfa_code:
                return None
            if recovery_code_model is None:
                raise ValidationException(
                    message="recovery_code_model is required to verify MFA",
                )
            if not await self._verify_mfa_code(
                session, user, mfa_code, recovery_code_model
            ):
                return None
        normalized_new = new_email.strip().lower()
        if normalized_new == user.email:
            raise ValidationException(
                message="new email is the same as the current one",
            )
        if await self._email_taken(session, normalized_new, exclude_user_id=user.id):
            raise ConflictException(
                message="email already in use",
                details={"email": normalized_new},
            )
        bundle = await self._issue_token(
            session,
            user_id=user.id,
            purpose=UserTokenPurpose.EMAIL_CHANGE,
            ttl_seconds=self.auth_settings.AUTH_EMAIL_CHANGE_TTL_SECONDS,
            url_template=self.auth_settings.AUTH_EMAIL_CHANGE_URL_TEMPLATE,
            payload=normalized_new,
        )
        await self._maybe_send_email_change_email(user, normalized_new, bundle)
        if self.auth_settings.AUTH_RETURN_TOKEN_IN_RESPONSE or self.email is None:
            return EmailChangeToken(
                user_id=user.id,
                new_email=normalized_new,
                token=bundle[0],
                url=bundle[1],
                expires_at=bundle[2],
            )
        return None

    # ------------------------------------------------------------------
    # Token helpers
    # ------------------------------------------------------------------

    def issue_jwt_pair(self, user: BaseUserModel) -> tuple[str, str]:
        """Return a stateless ``(access, refresh)`` JWT pair.

        Both tokens are signed JWTs with no DB persistence — this is
        the **legacy** issuance path, kept for back-compat and used
        whenever ``refresh_token_model`` is not wired. When a
        refresh-token model *is* wired, prefer :meth:`issue_token_pair`,
        which mints an opaque DB-backed refresh token instead.

        Args:
            user (BaseUserModel): The authenticated user.

        Returns:
            tuple[str, str]: ``(access_token, refresh_token)`` — both
            stateless JWTs.
        """
        access = self._encode_access(user)
        refresh = self.jwt.encode(
            {"sub": str(user.id), "refresh": True},
            ttl=timedelta(seconds=self.jwt_settings.JWT_REFRESH_TTL_SECONDS),
        )
        return access, refresh

    def _encode_access(self, user: BaseUserModel) -> str:
        """Sign a short-lived access JWT carrying ``sub`` + ``email``."""
        return self.jwt.encode(
            {"sub": str(user.id), "email": user.email},
            ttl=timedelta(seconds=self.jwt_settings.JWT_ACCESS_TTL_SECONDS),
        )

    async def issue_token_pair(
        self,
        session: AsyncSession,
        user: BaseUserModel,
        *,
        family_id: UUID | None = None,
    ) -> tuple[str, str]:
        """Issue an ``(access, refresh)`` pair, DB-backed when configured.

        This is the issuance path the bundled router uses at every
        login-equivalent step (login / signup-auto-activate /
        activation / password-reset / mfa-verify). Its shape depends
        on whether a refresh-token model is wired:

        * **``refresh_token_model`` set** — the access token is a
          stateless JWT, the refresh token is an **opaque** value
          persisted (hashed) as a single-use row. ``family_id`` ties
          the new token to a rotation lineage; ``None`` starts a fresh
          family (a brand-new login).
        * **``refresh_token_model`` is ``None``** — falls back to
          :meth:`issue_jwt_pair`, the legacy stateless behavior.

        Args:
            session (AsyncSession): Active SQLAlchemy session. The new
                refresh row is added + flushed (the caller owns the
                commit).
            user (BaseUserModel): The authenticated user.
            family_id (UUID | None): Existing rotation lineage to
                attach the new token to. ``None`` starts a new family.

        Returns:
            tuple[str, str]: ``(access_token, refresh_token)`` — the
            ``refresh_token`` is plaintext (opaque) or a JWT depending
            on configuration. The plaintext opaque token is surfaced
            exactly once; only its hash is persisted.
        """
        access = self._encode_access(user)
        if self.refresh_token_model is None:
            refresh = self.jwt.encode(
                {"sub": str(user.id), "refresh": True},
                ttl=timedelta(seconds=self.jwt_settings.JWT_REFRESH_TTL_SECONDS),
            )
            return access, refresh
        refresh = await self._issue_refresh_record(
            session, user_id=user.id, family_id=family_id
        )
        return access, refresh

    async def refresh_tokens(
        self,
        session: AsyncSession,
        *,
        refresh_token: str,
    ) -> tuple[BaseUserModel, str, str]:
        """Exchange a valid refresh token for a brand-new ``(access, refresh)`` pair.

        The password-less counterpart to :meth:`login`: the caller
        proves their identity with the long-lived refresh token
        (returned at login / signup / activation / reset / mfa-verify)
        instead of an email + password. The resolved user must be
        **active** and a fresh pair is minted — both tokens rotate, so
        the caller should persist the new refresh token and discard the
        old one.

        Behavior depends on whether a refresh-token model is wired:

        * **``refresh_token_model`` set (DB-backed)** — the opaque
          token is looked up by hash. A token that is unknown, expired
          or already revoked is rejected with ``401``. Replaying an
          already-**rotated** token (``used_at`` set) is treated as a
          stolen-token signal: the **entire family** is revoked and the
          call raises ``401``. On success the presented token is marked
          ``used_at`` and a new opaque token is minted in the same
          family.
        * **``refresh_token_model`` is ``None`` (stateless / legacy)** —
          the refresh JWT is decoded and must carry the ``refresh``
          claim, so a stolen *access* token can't be replayed here.

        Args:
            session (AsyncSession): Active SQLAlchemy session.
            refresh_token (str): The long-lived refresh token.

        Returns:
            tuple[BaseUserModel, str, str]: ``(user, access_token,
            refresh_token)`` — the resolved user and the new pair.

        Raises:
            ExpiredTokenException: Stateless mode only — when the JWT is
                past its ``exp`` claim (HTTP 401).
            InvalidTokenException: When the token is unknown, malformed,
                expired, revoked, reused, or not a refresh token
                (HTTP 401).
            NotFoundException: When the subject references no user.
            ForbiddenException: When the resolved user is inactive.
        """
        if self.refresh_token_model is None:
            claims = self.jwt.decode(refresh_token)
            if not claims.get("refresh"):
                raise InvalidTokenException(message="not a refresh token")
            subject = claims.get("sub")
            if not subject:
                raise InvalidTokenException(message="refresh token missing subject")
            user = await self.get_user(subject, session)
            require_active(user)
            access, refresh = self.issue_jwt_pair(user)
            return user, access, refresh

        record = await self._lookup_refresh_record(session, refresh_token)
        user = await self.get_user(record.user_id, session)
        require_active(user)
        record.used_at = utcnow()
        await session.flush()
        access, refresh = await self.issue_token_pair(
            session, user, family_id=record.family_id
        )
        return user, access, refresh

    async def revoke_refresh_token(
        self,
        session: AsyncSession,
        *,
        refresh_token: str,
        all_sessions: bool = False,
    ) -> None:
        """Revoke a refresh token (logout). Idempotent + best-effort.

        Looks the opaque token up by hash and flips ``revoked_at`` so
        it can no longer be exchanged at ``POST /auth/refresh``, even
        before its natural expiry. By default only the token's own
        **family** (the rotation lineage from one login) is revoked;
        pass ``all_sessions=True`` to kill every active refresh token
        the user owns (log out everywhere).

        No-op when ``refresh_token_model`` is not wired (stateless JWTs
        cannot be revoked) and when the token is unknown — the call
        never raises so logout endpoints stay idempotent and never leak
        whether a token existed.

        Args:
            session (AsyncSession): Active SQLAlchemy session. The
                update is flushed (the caller owns the commit).
            refresh_token (str): The opaque refresh token to revoke.
            all_sessions (bool): When ``True``, revoke every active
                token of the user, not just this family. Defaults to
                ``False``.
        """
        model = self.refresh_token_model
        if model is None:
            return
        digest = hash_opaque_token(refresh_token)
        result = await session.execute(select(model).where(model.token_hash == digest))
        record: BaseUserRefreshTokenModel | None = result.scalar_one_or_none()
        if record is None:
            return
        if all_sessions:
            await session.execute(
                update(model)
                .where(model.user_id == record.user_id, model.revoked_at.is_(None))
                .values(revoked_at=utcnow())
            )
            await session.flush()
        else:
            await self._revoke_family(session, record.family_id)

    async def _issue_refresh_record(
        self,
        session: AsyncSession,
        *,
        user_id: UUID,
        family_id: UUID | None,
    ) -> str:
        """Persist a fresh opaque refresh-token row, return the plaintext.

        Args:
            session (AsyncSession): Active SQLAlchemy session.
            user_id (UUID): Owner of the token.
            family_id (UUID | None): Rotation lineage to attach to;
                ``None`` starts a new family.

        Returns:
            str: The plaintext opaque token — surfaced once, never
            stored in cleartext.
        """
        model = self.refresh_token_model
        assert model is not None  # guarded by callers
        plain, digest = generate_opaque_token(48)
        record = model(
            user_id=user_id,
            token_hash=digest,
            family_id=family_id or uuid4(),
            expires_at=utcnow()
            + timedelta(seconds=self.jwt_settings.JWT_REFRESH_TTL_SECONDS),
        )
        session.add(record)
        await session.flush()
        return plain

    async def _lookup_refresh_record(
        self,
        session: AsyncSession,
        token: str,
    ) -> BaseUserRefreshTokenModel:
        """Find a refresh-token row + run validity + reuse-detection checks.

        Args:
            session (AsyncSession): Active SQLAlchemy session.
            token (str): Plaintext opaque token.

        Returns:
            BaseUserRefreshTokenModel: The valid, unused, unrevoked
            token row.

        Raises:
            InvalidTokenException: When the token is unknown, revoked,
                expired, or already rotated (reuse — the family is
                revoked as a side effect before raising).
        """
        model = self.refresh_token_model
        assert model is not None  # guarded by callers
        digest = hash_opaque_token(token)
        result = await session.execute(select(model).where(model.token_hash == digest))
        record: BaseUserRefreshTokenModel | None = result.scalar_one_or_none()
        if record is None:
            raise InvalidTokenException(message="refresh token not recognized")
        if record.revoked_at is not None:
            raise InvalidTokenException(message="refresh token revoked")
        if record.used_at is not None:
            # Reuse of an already-rotated token is the classic stolen-token
            # signal — kill the whole family so the attacker can't keep the
            # session alive with the descendant token.
            await self._revoke_family(session, record.family_id)
            raise InvalidTokenException(message="refresh token reuse detected")
        # SQLite stores timestamps as naive UTC; Postgres returns
        # timezone-aware. Normalize to naive for the comparison so the
        # same code path works against both backends.
        now = utcnow().replace(tzinfo=None)
        expires_at = (
            record.expires_at.replace(tzinfo=None)
            if record.expires_at.tzinfo is not None
            else record.expires_at
        )
        if expires_at < now:
            raise InvalidTokenException(message="refresh token expired")
        return record

    async def _revoke_family(
        self,
        session: AsyncSession,
        family_id: UUID,
    ) -> None:
        """Flip ``revoked_at`` on every still-active token in a family."""
        model = self.refresh_token_model
        assert model is not None  # guarded by callers
        await session.execute(
            update(model)
            .where(model.family_id == family_id, model.revoked_at.is_(None))
            .values(revoked_at=utcnow())
        )
        await session.flush()

    # ------------------------------------------------------------------
    # Current-user resolution
    # ------------------------------------------------------------------

    async def get_user(
        self,
        subject: str | UUID,
        session: AsyncSession,
    ) -> BaseUserModel:
        """Resolve a JWT subject (the user id) to the persisted user.

        Session-explicit twin of :meth:`load_user` — use it when the
        caller already owns a session (matches the rest of the
        service's API, where every method takes the ``AsyncSession``).

        Args:
            subject (str | UUID): The JWT ``sub`` claim — the user id.
            session (AsyncSession): Active SQLAlchemy session.

        Returns:
            BaseUserModel: The loaded user.

        Raises:
            NotFoundException: When the subject is malformed or no user
                with that id exists.
        """
        try:
            user_id = subject if isinstance(subject, UUID) else UUID(str(subject))
        except (ValueError, AttributeError) as exc:
            raise NotFoundException(message="User not found") from exc
        user: BaseUserModel | None = await session.get(self.user_model, user_id)
        if user is None:
            raise NotFoundException(message="User not found")
        return user

    async def load_user(self, subject: str) -> BaseUserModel:
        """Resolve a JWT subject to a user, opening the service's own session.

        This is the single-argument async callable that
        :func:`tempest_fastapi_sdk.make_jwt_user_dependency` expects, so
        a project can wire the authenticated-user dependency without
        hand-writing a loader:

            >>> get_current_user = auth_service.current_user_dependency()

        Requires the service to have been built with ``db=`` so it can
        open a session on its own.

        Args:
            subject (str): The JWT ``sub`` claim — the user id.

        Returns:
            BaseUserModel: The loaded user.

        Raises:
            RuntimeError: When the service was created without ``db=``.
            NotFoundException: When no user with that id exists.
        """
        if self.db is None:
            raise RuntimeError(
                "UserAuthService was created without `db=`; pass an "
                "AsyncDatabaseManager to use load_user / "
                "current_user_dependency."
            )
        async with self.db.get_session_context() as session:
            return await self.get_user(subject, session)

    def current_user_dependency(
        self,
        *,
        soft: bool = False,
        session_dependency: Callable[..., Any] | None = None,
        cookie_name: str | None = None,
    ) -> Callable[..., Coroutine[Any, Any, Any]]:
        """Build a FastAPI dependency that returns the authenticated user.

        Wraps :func:`tempest_fastapi_sdk.make_jwt_user_dependency` with
        this service's own :class:`JWTUtils` and :meth:`get_user`, so
        the bearer token is verified with the **same** secret the
        service signs with — there is no second ``JWTUtils`` to keep in
        sync. Mount it on any of your routes:

            >>> get_current_user = auth_service.current_user_dependency()
            >>> get_current_user_or_none = auth_service.current_user_dependency(
            ...     soft=True
            ... )

        The authenticated user is loaded on the **request-scoped**
        session (``self.db.session_dependency`` by default), so it is
        attached to the same session the request's repositories use and
        can be mutated / refreshed without an
        ``InvalidRequestError: Instance is not persistent within this
        Session``. If your repositories depend on a different session
        callable (e.g. a project-local ``get_session`` wrapper), pass it
        as ``session_dependency`` so both resolve to the *same* request
        session — FastAPI caches a sub-dependency by its callable, so a
        distinct wrapper would open a second session and detach the user.

        Requires the service to have been built with ``db=``.

        Args:
            soft (bool): When ``True``, the dependency returns ``None``
                instead of raising on a missing / invalid token — for
                endpoints that work both authenticated and anonymous.
            session_dependency (Callable[..., Any] | None): The
                request-scoped session provider to share with
                repositories. Defaults to ``self.db.session_dependency``.
            cookie_name (str | None): Cookie to read the access token
                from when the ``Authorization`` header is absent (the
                header still wins). ``None`` (default) auto-derives it
                from ``AUTH_ACCESS_COOKIE_NAME`` whenever
                ``AUTH_TOKEN_DELIVERY`` is ``"cookie"`` or ``"both"`` —
                so a route guarded by this dependency accepts the cookie
                the bundled login set, with no extra wiring. Pass an
                explicit name to force it, or a bearer-only delivery
                mode leaves it ``None`` (header only).

        Returns:
            Callable[..., Coroutine[Any, Any, Any]]: An async FastAPI
            dependency yielding the user (or ``None`` in soft mode).

        Raises:
            RuntimeError: When the service was created without ``db=``.
        """
        if self.db is None:
            raise RuntimeError(
                "UserAuthService was created without `db=`; pass an "
                "AsyncDatabaseManager to use current_user_dependency."
            )
        from tempest_fastapi_sdk.api.dependencies.auth import (
            make_jwt_user_dependency,
        )

        resolved_cookie_name = cookie_name
        if resolved_cookie_name is None and self.auth_settings.AUTH_TOKEN_DELIVERY in (
            "cookie",
            "both",
        ):
            resolved_cookie_name = self.auth_settings.AUTH_ACCESS_COOKIE_NAME

        return make_jwt_user_dependency(
            self.jwt,
            self.get_user,
            soft=soft,
            cookie_name=resolved_cookie_name,
            session_dependency=session_dependency or self.db.session_dependency,
        )

    # ------------------------------------------------------------------
    # Authorization guards (imperative, on an already-loaded user)
    # ------------------------------------------------------------------

    @staticmethod
    def require_authenticated(user: UserT | None) -> UserT:
        """Assert the user is authenticated; return it narrowed to non-``None``.

        Thin static mirror of
        :func:`tempest_fastapi_sdk.require_authenticated` so a service
        already in scope can guard without an extra import:

            >>> user = auth_service.require_authenticated(current)

        Args:
            user (UserT | None): The resolved request user.

        Returns:
            UserT: The same user, narrowed to non-``None``.

        Raises:
            UnauthorizedException: When ``user`` is ``None`` (HTTP 401).
        """
        return require_authenticated(user)

    @staticmethod
    def require_active(user: UserT | None) -> UserT:
        """Assert the user is authenticated and active. See :func:`require_active`.

        Args:
            user (UserT | None): The resolved request user.

        Returns:
            UserT: The authenticated, active user.

        Raises:
            UnauthorizedException: When ``user`` is ``None`` (HTTP 401).
            ForbiddenException: When ``user.is_active`` is falsy (HTTP 403).
        """
        return require_active(user)

    @staticmethod
    def require_admin(user: UserT | None) -> UserT:
        """Assert the user is authenticated and an admin. See :func:`require_admin`.

        Args:
            user (UserT | None): The resolved request user.

        Returns:
            UserT: The authenticated admin user.

        Raises:
            UnauthorizedException: When ``user`` is ``None`` (HTTP 401).
            ForbiddenException: When ``user.is_admin`` is falsy (HTTP 403).
        """
        return require_admin(user)

    # ------------------------------------------------------------------
    # Internals
    # ------------------------------------------------------------------

    def _enforce_password_policy(self, password: str) -> None:
        """Validate ``password`` against the configured policy.

        Length (``AUTH_PASSWORD_MIN_LENGTH``) is always enforced.
        When ``AUTH_PASSWORD_REQUIRE_COMPLEXITY`` is on, the effective
        length floor is raised to at least 8 (a configured value below
        8 is ignored in complexity mode) and the password must also
        contain at least one lowercase letter, one uppercase letter,
        one digit, and one special (non-alphanumeric) character.

        Args:
            password (str): The plaintext password to check.

        Raises:
            ValidationException: When the password is too short or,
                under complexity mode, missing a required character
                class.
        """
        require_complexity = self.auth_settings.AUTH_PASSWORD_REQUIRE_COMPLEXITY
        floor = self.auth_settings.AUTH_PASSWORD_MIN_LENGTH
        if require_complexity:
            floor = max(floor, 8)
        if len(password) < floor:
            raise ValidationException(
                message=f"password must be at least {floor} characters",
                details={"min_length": floor},
            )
        if not require_complexity:
            return
        missing: list[str] = []
        if not any(c.islower() for c in password):
            missing.append("lowercase")
        if not any(c.isupper() for c in password):
            missing.append("uppercase")
        if not any(c.isdigit() for c in password):
            missing.append("digit")
        if not any(not c.isalnum() for c in password):
            missing.append("special")
        if missing:
            raise ValidationException(
                message=(
                    "password must contain at least one "
                    + ", ".join(missing)
                    + " character"
                ),
                details={"missing_classes": missing},
            )

    async def _issue_token(
        self,
        session: AsyncSession,
        *,
        user_id: UUID,
        purpose: UserTokenPurpose,
        ttl_seconds: int,
        url_template: str,
        payload: str | None = None,
    ) -> tuple[str, str, datetime]:
        """Persist a fresh token row, return ``(plain, url, expires_at)``.

        Args:
            session (AsyncSession): Active SQLAlchemy session.
            user_id (UUID): The user the token authorizes.
            purpose (UserTokenPurpose): What the token authorizes.
            ttl_seconds (int): Token lifetime in seconds.
            url_template (str): URL template with a ``{token}`` slot.
            payload (str | None): Optional flow context stored on the
                row (e.g. the pending new email for ``EMAIL_CHANGE``).
        """
        plain, digest = generate_opaque_token(48)
        expires_at = utcnow() + timedelta(seconds=ttl_seconds)
        record = self.token_model(
            user_id=user_id,
            token_hash=digest,
            purpose=purpose.value,
            expires_at=expires_at,
            payload=payload,
        )
        session.add(record)
        await session.flush()
        url = url_template.replace("{token}", plain)
        return plain, url, expires_at

    async def _email_taken(
        self,
        session: AsyncSession,
        email: str,
        *,
        exclude_user_id: UUID | None = None,
    ) -> bool:
        """Return ``True`` when ``email`` already belongs to another user.

        Args:
            session (AsyncSession): Active SQLAlchemy session.
            email (str): Normalized email to check.
            exclude_user_id (UUID | None): A user id to exclude from the
                check (the user performing the change).

        Returns:
            bool: Whether a different user already holds ``email``.
        """
        stmt = select(self.user_model.id).where(self.user_model.email == email)
        if exclude_user_id is not None:
            stmt = stmt.where(self.user_model.id != exclude_user_id)
        result = await session.execute(stmt)
        return result.scalar_one_or_none() is not None

    async def _consume_token(
        self,
        session: AsyncSession,
        *,
        token: str,
        purpose: UserTokenPurpose,
    ) -> BaseUserTokenModel:
        """Look up + mark used. Raise on invalid / expired tokens."""
        record = await self._lookup_token(session, token=token, purpose=purpose)
        record.used_at = utcnow()
        await session.flush()
        return record

    async def peek_token(
        self,
        session: AsyncSession,
        *,
        token: str,
        purpose: UserTokenPurpose,
    ) -> tuple[BaseUserTokenModel, BaseUserModel]:
        """Validate a token + load its user **without** consuming it.

        Mirrors :meth:`_consume_token` (raises on
        invalid/expired/already-used tokens) but leaves
        ``used_at`` untouched — used by ``GET`` endpoints in
        backend-only mode that need to render a page (e.g. the
        password-reset form) before the user actually submits.

        Args:
            session (AsyncSession): Active SQLAlchemy session.
            token (str): Plaintext token.
            purpose (UserTokenPurpose): Expected token purpose.

        Returns:
            tuple[BaseUserTokenModel, BaseUserModel]: The token
            record and its associated user.

        Raises:
            InvalidTokenException: On unknown / already-used /
                expired tokens.
            NotFoundException: When the token references a user
                that no longer exists.
        """
        record = await self._lookup_token(session, token=token, purpose=purpose)
        user: BaseUserModel | None = await session.get(self.user_model, record.user_id)
        if user is None:
            raise NotFoundException(message="user not found")
        return record, user

    async def _lookup_token(
        self,
        session: AsyncSession,
        *,
        token: str,
        purpose: UserTokenPurpose,
    ) -> BaseUserTokenModel:
        """Find a token record + run validity checks (without marking used)."""
        digest = hash_opaque_token(token)
        result = await session.execute(
            select(self.token_model).where(
                self.token_model.token_hash == digest,
                self.token_model.purpose == purpose.value,
            )
        )
        record: BaseUserTokenModel | None = result.scalar_one_or_none()
        if record is None:
            raise InvalidTokenException(message="token not recognized")
        if record.used_at is not None:
            raise InvalidTokenException(message="token already used")
        # SQLite stores timestamps as naive UTC; Postgres returns
        # timezone-aware. Normalize to naive for the comparison so the
        # same code path works against both backends.
        now = utcnow().replace(tzinfo=None)
        expires_at = (
            record.expires_at.replace(tzinfo=None)
            if record.expires_at.tzinfo is not None
            else record.expires_at
        )
        if expires_at < now:
            raise InvalidTokenException(message="token expired")
        return record

    async def _maybe_send_activation_email(
        self,
        user: BaseUserModel,
        token_bundle: tuple[str, str, datetime],
    ) -> None:
        """Render + send the activation email when EmailUtils is wired."""
        if self.email is None or self.auth_settings.AUTH_RETURN_TOKEN_IN_RESPONSE:
            return
        _plain, url, expires_at = token_bundle
        locale = self.auth_settings.AUTH_DEFAULT_LOCALE
        html = self.email.render_template(
            self.auth_settings.AUTH_ACTIVATION_TEMPLATE,
            {
                "user": user,
                "activation_url": url,
                "expires_at": expires_at,
                "expires_at_str": format_expires_at(expires_at, locale),
            },
            locale=locale,
        )
        await self.email.send(
            user.email,
            subject=auth_email_message(locale, "activation_subject"),
            body=auth_email_message(locale, "activation_body").format(url=url),
            html=html,
        )

    async def _maybe_send_password_reset_email(
        self,
        user: BaseUserModel,
        token_bundle: tuple[str, str, datetime],
    ) -> None:
        """Render + send the reset email when EmailUtils is wired."""
        if self.email is None or self.auth_settings.AUTH_RETURN_TOKEN_IN_RESPONSE:
            return
        _plain, url, expires_at = token_bundle
        locale = self.auth_settings.AUTH_DEFAULT_LOCALE
        html = self.email.render_template(
            self.auth_settings.AUTH_PASSWORD_RESET_TEMPLATE,
            {
                "user": user,
                "reset_url": url,
                "expires_at": expires_at,
                "expires_at_str": format_expires_at(expires_at, locale),
            },
            locale=locale,
        )
        await self.email.send(
            user.email,
            subject=auth_email_message(locale, "password_reset_subject"),
            body=auth_email_message(locale, "password_reset_body").format(url=url),
            html=html,
        )

    async def _maybe_send_email_change_email(
        self,
        user: BaseUserModel,
        new_email: str,
        token_bundle: tuple[str, str, datetime],
    ) -> None:
        """Send the confirmation email to the NEW address when wired."""
        if self.email is None or self.auth_settings.AUTH_RETURN_TOKEN_IN_RESPONSE:
            return
        _plain, url, expires_at = token_bundle
        locale = self.auth_settings.AUTH_DEFAULT_LOCALE
        html = self.email.render_template(
            self.auth_settings.AUTH_EMAIL_CHANGE_TEMPLATE,
            {
                "user": user,
                "new_email": new_email,
                "confirm_url": url,
                "expires_at": expires_at,
                "expires_at_str": format_expires_at(expires_at, locale),
            },
            locale=locale,
        )
        await self.email.send(
            new_email,
            subject=auth_email_message(locale, "email_change_subject"),
            body=auth_email_message(locale, "email_change_body").format(url=url),
            html=html,
        )

    async def _maybe_send_email_verification_email(
        self,
        user: BaseUserModel,
        token_bundle: tuple[str, str, datetime],
    ) -> None:
        """Send the re-verification email to the current address when wired."""
        if self.email is None or self.auth_settings.AUTH_RETURN_TOKEN_IN_RESPONSE:
            return
        _plain, url, expires_at = token_bundle
        locale = self.auth_settings.AUTH_DEFAULT_LOCALE
        html = self.email.render_template(
            self.auth_settings.AUTH_EMAIL_VERIFICATION_TEMPLATE,
            {
                "user": user,
                "verify_url": url,
                "expires_at": expires_at,
                "expires_at_str": format_expires_at(expires_at, locale),
            },
            locale=locale,
        )
        await self.email.send(
            user.email,
            subject=auth_email_message(locale, "email_verification_subject"),
            body=auth_email_message(locale, "email_verification_body").format(url=url),
            html=html,
        )

    async def _maybe_send_email_changed_notice(
        self,
        user: BaseUserModel,
        old_email: str,
        new_email: str,
    ) -> None:
        """Alert the OLD address after a confirmed change when configured.

        Skipped when ``EmailUtils`` is not wired or
        ``AUTH_EMAIL_CHANGE_NOTIFY_OLD`` is off. Unlike the token emails
        this is not gated by ``AUTH_RETURN_TOKEN_IN_RESPONSE`` — it
        carries no token, only a security notice.
        """
        if self.email is None or not self.auth_settings.AUTH_EMAIL_CHANGE_NOTIFY_OLD:
            return
        locale = self.auth_settings.AUTH_DEFAULT_LOCALE
        html = self.email.render_template(
            self.auth_settings.AUTH_EMAIL_CHANGED_NOTICE_TEMPLATE,
            {
                "user": user,
                "old_email": old_email,
                "new_email": new_email,
            },
            locale=locale,
        )
        await self.email.send(
            old_email,
            subject=auth_email_message(locale, "email_changed_notice_subject"),
            body=auth_email_message(locale, "email_changed_notice_body").format(
                new_email=new_email,
            ),
            html=html,
        )

    # ------------------------------------------------------------------
    # MFA (TOTP)
    # ------------------------------------------------------------------

    def is_mfa_enrolled(self, user: BaseUserModel) -> bool:
        """Return ``True`` when ``user`` has finished MFA enrollment.

        Checks both ``totp_enabled_at`` and the global kill-switch
        :attr:`AuthSettings.AUTH_MFA_ENABLED` — when the kill-switch
        is off, every user is treated as unenrolled so the login
        flow stays single-step.
        """
        if not self.auth_settings.AUTH_MFA_ENABLED:
            return False
        return getattr(user, "totp_enabled_at", None) is not None

    def issue_mfa_token(self, user: BaseUserModel) -> str:
        """Mint the short-lived JWT that bridges step 1 and step 2 of login."""
        return self.jwt.encode(
            {"sub": str(user.id), "purpose": "mfa_pending"},
            ttl=timedelta(
                seconds=self.auth_settings.AUTH_MFA_TOKEN_TTL_SECONDS,
            ),
        )

    async def mfa_enroll(
        self,
        session: AsyncSession,
        *,
        user: BaseUserModel,
        recovery_code_model: type[BaseUserRecoveryCodeModel],
    ) -> tuple[str, str, list[str]]:
        """Issue a fresh TOTP secret + recovery codes for ``user``.

        Idempotent in spirit — calling it again rotates the secret
        AND invalidates every previously issued recovery code.
        ``totp_enabled_at`` is **NOT** set yet; the caller MUST
        confirm a valid code via :meth:`mfa_confirm` before MFA is
        actually active. Until then, the persisted secret is dead
        weight and login keeps working without the TOTP step.

        Args:
            session (AsyncSession): Active SQLAlchemy session.
            user (BaseUserModel): The user enrolling.
            recovery_code_model (type[BaseUserRecoveryCodeModel]): The
                project's concrete subclass of
                :class:`BaseUserRecoveryCodeModel`.

        Returns:
            tuple[str, str, list[str]]: ``(secret, provisioning_uri,
            recovery_codes_plaintext)`` — show all three to the user
            EXACTLY ONCE. The SDK persists only the hash of each
            recovery code.

        Raises:
            ImportError: When the ``[mfa]`` extra is not installed.
        """
        from tempest_fastapi_sdk.utils.totp import TOTPHelper

        totp = TOTPHelper(issuer=self.auth_settings.AUTH_MFA_ISSUER)
        secret = totp.generate_secret()
        provisioning = totp.provisioning_uri(secret, user.email)
        # Wipe previously stored recovery codes (rotation semantics).
        await session.execute(
            delete(recovery_code_model).where(
                recovery_code_model.user_id == user.id,
            ),
        )
        plaintexts: list[str] = []
        for _ in range(self.auth_settings.AUTH_MFA_RECOVERY_CODES_COUNT):
            plaintext, code_hash = generate_opaque_token(8)
            plaintexts.append(plaintext)
            record = recovery_code_model(
                user_id=user.id,
                code_hash=code_hash,
            )
            session.add(record)
        user.totp_secret = secret
        user.totp_enabled_at = None
        await session.flush()
        await session.refresh(user)
        return secret, provisioning, plaintexts

    async def mfa_confirm(
        self,
        session: AsyncSession,
        *,
        user: BaseUserModel,
        code: str,
    ) -> None:
        """Mark MFA as active after the user proves they can read the QR.

        Args:
            session (AsyncSession): Active SQLAlchemy session.
            user (BaseUserModel): The user finishing enrollment.
            code (str): 6-digit code from the Authenticator app.

        Raises:
            UnauthorizedException: When the code does not match the
                pending secret (no MFA enrollment happens).
            ValidationException: When no secret is staged (caller
                must run :meth:`mfa_enroll` first).
        """
        from tempest_fastapi_sdk.utils.totp import TOTPHelper

        if not user.totp_secret:
            raise ValidationException(
                message="MFA not initialized — call enroll first",
            )
        totp = TOTPHelper(issuer=self.auth_settings.AUTH_MFA_ISSUER)
        if not totp.verify(
            user.totp_secret,
            code,
            window=self.auth_settings.AUTH_MFA_VERIFY_WINDOW,
        ):
            raise UnauthorizedException(message="invalid MFA code")
        user.totp_enabled_at = utcnow()
        await session.flush()
        await session.refresh(user)

    async def mfa_disable(
        self,
        session: AsyncSession,
        *,
        user: BaseUserModel,
        password: str,
        code: str,
        recovery_code_model: type[BaseUserRecoveryCodeModel],
    ) -> None:
        """Disable MFA — requires password + active TOTP/recovery code.

        Args:
            session (AsyncSession): Active SQLAlchemy session.
            user (BaseUserModel): The user disabling MFA.
            password (str): Plaintext password — re-verified.
            code (str): Active TOTP or single-use recovery code.
            recovery_code_model (type[BaseUserRecoveryCodeModel]): The
                project's concrete recovery-code model — needed
                because disabling MFA wipes every code.

        Raises:
            UnauthorizedException: On wrong password OR invalid
                code.
            ValidationException: When MFA is not active in the
                first place.
        """
        if not self.passwords.verify(password, user.hashed_password):
            raise UnauthorizedException(message="invalid password")
        if not user.totp_secret or not user.totp_enabled_at:
            raise ValidationException(message="MFA not active")
        if not await self._verify_mfa_code(session, user, code, recovery_code_model):
            raise UnauthorizedException(message="invalid MFA code")
        user.totp_secret = None
        user.totp_enabled_at = None
        await session.execute(
            delete(recovery_code_model).where(
                recovery_code_model.user_id == user.id,
            ),
        )
        await session.flush()
        await session.refresh(user)

    async def mfa_verify(
        self,
        session: AsyncSession,
        *,
        mfa_token: str,
        code: str,
        recovery_code_model: type[BaseUserRecoveryCodeModel],
    ) -> BaseUserModel:
        """Step 2 of two-step login — swap the intermediate token for JWTs.

        Args:
            session (AsyncSession): Active SQLAlchemy session.
            mfa_token (str): Intermediate JWT issued by step 1.
            code (str): 6-digit TOTP code OR plaintext recovery
                code from enrollment.
            recovery_code_model (type[BaseUserRecoveryCodeModel]): The
                project's concrete recovery-code model.

        Returns:
            BaseUserModel: The fully authenticated user — caller
            mints the JWT pair next.

        Raises:
            UnauthorizedException: On bad / expired ``mfa_token``,
                bad code, or user not enrolled in MFA.
        """
        try:
            payload = self.jwt.decode(mfa_token)
        except Exception as exc:
            raise UnauthorizedException(message="invalid MFA token") from exc
        if payload.get("purpose") != "mfa_pending":
            raise UnauthorizedException(message="invalid MFA token")
        try:
            user_id = UUID(payload["sub"])
        except (KeyError, ValueError) as exc:
            raise UnauthorizedException(message="invalid MFA token") from exc
        user: BaseUserModel | None = await session.get(self.user_model, user_id)
        if user is None or not user.is_active:
            raise UnauthorizedException(message="invalid MFA token")
        if not self.is_mfa_enrolled(user):
            raise UnauthorizedException(message="MFA not enrolled")
        if not await self._verify_mfa_code(session, user, code, recovery_code_model):
            raise UnauthorizedException(message="invalid MFA code")
        user.last_login_at = utcnow()
        await session.flush()
        await session.refresh(user)
        return user

    async def _verify_mfa_code(
        self,
        session: AsyncSession,
        user: BaseUserModel,
        code: str,
        recovery_code_model: type[BaseUserRecoveryCodeModel],
    ) -> bool:
        """Check ``code`` against TOTP first, then unused recovery codes."""
        from tempest_fastapi_sdk.utils.totp import TOTPHelper

        if user.totp_secret:
            totp = TOTPHelper(issuer=self.auth_settings.AUTH_MFA_ISSUER)
            if totp.verify(
                user.totp_secret,
                code,
                window=self.auth_settings.AUTH_MFA_VERIFY_WINDOW,
            ):
                return True
        # Fallback: single-use recovery code (plaintext from enrollment).
        digest = hash_opaque_token(code.strip())
        result = await session.execute(
            select(recovery_code_model).where(
                recovery_code_model.user_id == user.id,
                recovery_code_model.code_hash == digest,
                recovery_code_model.used_at.is_(None),
            )
        )
        record = result.scalar_one_or_none()
        if record is None:
            return False
        record.used_at = utcnow()
        await session.flush()
        return True


__all__: list[str] = [
    "ActivationToken",
    "EmailChangeToken",
    "EmailVerificationToken",
    "PasswordResetToken",
    "UserAuthService",
]
