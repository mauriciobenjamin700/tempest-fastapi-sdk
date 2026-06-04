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

from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import TYPE_CHECKING, Any
from uuid import UUID

from sqlalchemy import select

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
    from tempest_fastapi_sdk.db.connection import AsyncDatabaseManager
    from tempest_fastapi_sdk.db.user_model import BaseUserModel
    from tempest_fastapi_sdk.settings.mixins import AuthSettings, JWTSettings
    from tempest_fastapi_sdk.utils.email import EmailUtils


@dataclass(frozen=True, slots=True)
class ActivationToken:
    """Returned by :meth:`UserAuthService.signup` when activation is required.

    Carries the **plaintext** token alongside the rendered URL so
    the caller can decide whether to mail it, log it, or hand it
    back to the client (dev mode). The hash is already persisted
    — there's no way to recover the plaintext later.
    """

    user_id: UUID
    token: str
    url: str
    expires_at: datetime


@dataclass(frozen=True, slots=True)
class PasswordResetToken:
    """Returned by :meth:`UserAuthService.request_password_reset`.

    Same shape as :class:`ActivationToken` — only the purpose
    differs.
    """

    user_id: UUID
    token: str
    url: str
    expires_at: datetime


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

    Every method takes the active ``AsyncSession`` explicitly so
    callers control the transaction boundary — the service never
    opens its own session.
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

    # ------------------------------------------------------------------
    # Signup
    # ------------------------------------------------------------------

    async def signup(
        self,
        session: Any,
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
        session: Any,
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
        session: Any,
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
        session: Any,
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
        session: Any,
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

    # ------------------------------------------------------------------
    # Token helpers
    # ------------------------------------------------------------------

    def issue_jwt_pair(self, user: BaseUserModel) -> tuple[str, str]:
        """Return ``(access, refresh)`` JWTs for an authenticated user.

        Args:
            user (BaseUserModel): The authenticated user.

        Returns:
            tuple[str, str]: ``(access_token, refresh_token)``.
        """
        access = self.jwt.encode(
            {"sub": str(user.id), "email": user.email},
            ttl=timedelta(seconds=self.jwt_settings.JWT_ACCESS_TTL_SECONDS),
        )
        refresh = self.jwt.encode(
            {"sub": str(user.id), "refresh": True},
            ttl=timedelta(seconds=self.jwt_settings.JWT_REFRESH_TTL_SECONDS),
        )
        return access, refresh

    # ------------------------------------------------------------------
    # Internals
    # ------------------------------------------------------------------

    def _enforce_password_policy(self, password: str) -> None:
        """Raise when ``password`` is shorter than the configured floor."""
        floor = self.auth_settings.AUTH_PASSWORD_MIN_LENGTH
        if len(password) < floor:
            raise ValidationException(
                message=f"password must be at least {floor} characters",
                details={"min_length": floor},
            )

    async def _issue_token(
        self,
        session: Any,
        *,
        user_id: UUID,
        purpose: UserTokenPurpose,
        ttl_seconds: int,
        url_template: str,
    ) -> tuple[str, str, datetime]:
        """Persist a fresh token row, return ``(plain, url, expires_at)``."""
        plain, digest = generate_opaque_token(48)
        expires_at = utcnow() + timedelta(seconds=ttl_seconds)
        record = self.token_model(
            user_id=user_id,
            token_hash=digest,
            purpose=purpose.value,
            expires_at=expires_at,
        )
        session.add(record)
        await session.flush()
        url = url_template.replace("{token}", plain)
        return plain, url, expires_at

    async def _consume_token(
        self,
        session: Any,
        *,
        token: str,
        purpose: UserTokenPurpose,
    ) -> BaseUserTokenModel:
        """Look up + mark used. Raise on invalid / expired tokens."""
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
        record.used_at = utcnow()
        await session.flush()
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
        html = self.email.render_template(
            self.auth_settings.AUTH_ACTIVATION_TEMPLATE,
            {
                "user": user,
                "activation_url": url,
                "expires_at": expires_at,
            },
        )
        await self.email.send(
            user.email,
            subject="Activate your account",
            body=f"Open this link to activate your account: {url}",
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
        html = self.email.render_template(
            self.auth_settings.AUTH_PASSWORD_RESET_TEMPLATE,
            {
                "user": user,
                "reset_url": url,
                "expires_at": expires_at,
            },
        )
        await self.email.send(
            user.email,
            subject="Reset your password",
            body=f"Open this link to reset your password: {url}",
            html=html,
        )


__all__: list[str] = [
    "ActivationToken",
    "PasswordResetToken",
    "UserAuthService",
]
