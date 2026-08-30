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
    GuardException,
    UserT,
    require_active,
    require_admin,
    require_authenticated,
)
from tempest_fastapi_sdk.auth.locale import (
    auth_email_message,
    default_display_name,
    format_expires_at,
    resolve_locale,
    stamp_locale,
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
    OAuthAccountInactiveException,
    OAuthAccountNotLinkedException,
    OAuthEmailMissingException,
    OAuthEmailTakenException,
    OAuthEmailUnverifiedException,
    OAuthRegistrationDisabledException,
    UnauthorizedException,
    ValidationException,
)
from tempest_fastapi_sdk.utils.datetime import utcnow
from tempest_fastapi_sdk.utils.jwt import JWTUtils
from tempest_fastapi_sdk.utils.opaque_token import (
    generate_opaque_token,
    hash_opaque_token,
)
from tempest_fastapi_sdk.utils.password import PasswordUtils, generate_password
from tempest_fastapi_sdk.utils.token_types import (
    ACCESS_TOKEN_TYPE,
    MFA_TOKEN_TYPE,
    REFRESH_TOKEN_TYPE,
)

if TYPE_CHECKING:
    from collections.abc import Callable, Collection, Coroutine
    from typing import Any

    from tempest_fastapi_sdk.api.oauth import OAuthUser
    from tempest_fastapi_sdk.db.connection import AsyncDatabaseManager
    from tempest_fastapi_sdk.db.user_model import BaseUserModel
    from tempest_fastapi_sdk.db.user_oauth_account_model import (
        BaseUserOAuthAccountModel,
    )
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
        oauth_account_model: type[BaseUserOAuthAccountModel] | None = None,
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
            oauth_account_model (type[BaseUserOAuthAccountModel] | None):
                Concrete linked-identity model — usually
                ``src.db.models.UserOAuthAccountModel``. **Opt-in:**
                required by :meth:`login_with_oauth` and therefore by
                the ``/auth/oauth/*`` endpoints; ``None`` (default)
                leaves social login unavailable.
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
        self.oauth_account_model: type[BaseUserOAuthAccountModel] | None = (
            oauth_account_model
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
    # Social login (OAuth2 / OIDC)
    # ------------------------------------------------------------------

    async def login_with_oauth(
        self,
        session: AsyncSession,
        profile: OAuthUser,
        *,
        locale: str,
        link_by_verified_email: bool | None = None,
        allow_account_creation: bool | None = None,
    ) -> tuple[BaseUserModel, str, str]:
        """Resolve a third-party identity to a local user and log them in.

        The half of social login the OAuth clients deliberately leave
        out: they end at a :class:`~tempest_fastapi_sdk.OAuthUser`, and
        this turns it into a session. Feeding the result through
        :meth:`issue_token_pair` — rather than signing a token by hand
        at the call site — is what makes a Google login the *same*
        session as an email login: the same ``typ`` claim, the same
        opaque refresh token, the same rotation family, the same reuse
        detection, the same ``POST /auth/logout``.

        Resolution order, and why:

        1. **``(provider, subject)``** — the linked identity. This is
           the only lookup that authenticates. The pair is the
           provider's own stable id, unique in the account table, and
           unaffected by the user later changing their email at either
           end.
        2. **Email, when linking is allowed** — a first-time identity
           whose address already belongs to a local account. Attaches
           the identity to that account, but *only* when the provider
           explicitly states it verified the address
           (``email_verified is True``; ``None`` means the provider
           said nothing, which is not a yes). This is the branch that
           converts a provider's word about an email into control of an
           existing account, so it is off unless asked for twice — once
           by ``AUTH_OAUTH_LINK_BY_VERIFIED_EMAIL``, once by the
           provider's own flag.
        3. **Creation** — an unknown identity with an unknown email.
           Creates the user row with the provider's email, the
           provider's name (or a localized placeholder) and a generated
           password, then links the identity to it.

        A created account is **active immediately**: the point of the
        flow is that the identity provider performed the verification,
        so re-verifying by email would ask the user to prove something
        Google already proved. ``AUTH_AUTO_ACTIVATE`` is not consulted
        here — a project that wants human approval before first login
        turns account creation off and has an administrator create and
        link the row.

        The generated password is never shown to anyone. The account
        owner reaches a password of their own through the ordinary
        ``POST /auth/password-reset/request``, which needs no new flow
        because the email is already on the row.

        Args:
            session (AsyncSession): Active SQLAlchemy session. Rows are
                added and flushed; the caller owns the commit.
            profile (OAuthUser): The identity the provider returned,
                normalized by the client.
            locale (str): Canonical locale for this request, as
                :func:`~tempest_fastapi_sdk.resolve_locale` resolved it.
                Required rather than defaulted because it is what picks
                the placeholder display name — a silent default would
                answer a Portuguese user in English.
            link_by_verified_email (bool | None): Override for
                ``AUTH_OAUTH_LINK_BY_VERIFIED_EMAIL``. ``None``
                (default) reads the setting.
            allow_account_creation (bool | None): Override for
                ``AUTH_OAUTH_ALLOW_ACCOUNT_CREATION``, which itself
                falls back to ``AUTH_SIGNUP_ENABLED``. ``None``
                (default) walks that chain.

        Returns:
            tuple[BaseUserModel, str, str]: The authenticated user and
            the ``(access_token, refresh_token)`` pair, identical in
            shape to what ``POST /auth/login`` returns.

        Raises:
            RuntimeError: When the service was built without an
                ``oauth_account_model``. A configuration error, not a
                request error.
            OAuthEmailMissingException: When the provider returned
                no email. The column is ``NOT NULL UNIQUE``, so there is
                nothing to store and inventing an address would create
                an account nobody can recover.
            OAuthEmailTakenException: When the email already belongs to
                another account and linking is not allowed.
            OAuthEmailUnverifiedException: When linking was allowed but
                the provider did not state it verified the address.
                Deliberately distinct from the one above: that one has a
                next step for the user, this one has none.
            OAuthRegistrationDisabledException: When the identity is
                unknown and account creation is disabled.
            OAuthAccountInactiveException: When the resolved account is
                inactive.
        """
        if self.oauth_account_model is None:
            raise RuntimeError(
                "login_with_oauth requires a concrete oauth_account_model "
                "(subclass of BaseUserOAuthAccountModel) passed to "
                "UserAuthService(oauth_account_model=...)."
            )
        account_model = self.oauth_account_model
        found = await session.execute(
            select(account_model).where(
                account_model.provider == profile.provider,
                account_model.subject == profile.subject,
            )
        )
        account = found.scalar_one_or_none()
        if account is not None:
            user = await self._load_linked_user(session, account.user_id)
        else:
            user = await self._resolve_oauth_user(
                session,
                profile,
                locale=locale,
                link_by_verified_email=(
                    self.auth_settings.AUTH_OAUTH_LINK_BY_VERIFIED_EMAIL
                    if link_by_verified_email is None
                    else link_by_verified_email
                ),
                allow_account_creation=self._oauth_creation_allowed(
                    allow_account_creation
                ),
            )
            account = account_model(
                user_id=user.id,
                provider=profile.provider,
                subject=profile.subject,
            )
            session.add(account)
        self._sync_oauth_account(account, profile)
        now = utcnow()
        account.last_login_at = now
        user.last_login_at = now
        await session.flush()
        await session.refresh(user)
        access, refresh = await self.issue_token_pair(session, user)
        return user, access, refresh

    def _oauth_creation_allowed(self, override: bool | None) -> bool:
        """Resolve whether the callback may create an account.

        Walks the two-step fallback the settings describe: the explicit
        argument, then ``AUTH_OAUTH_ALLOW_ACCOUNT_CREATION``, then
        ``AUTH_SIGNUP_ENABLED``. The last hop is what makes closing the
        public signup door close the social one with it, instead of
        leaving a second, quieter way in.

        Args:
            override (bool | None): The caller's explicit choice, or
                ``None`` to read the settings.

        Returns:
            bool: Whether an unknown identity may create a user row.
        """
        if override is not None:
            return override
        configured = self.auth_settings.AUTH_OAUTH_ALLOW_ACCOUNT_CREATION
        if configured is not None:
            return configured
        return self.auth_settings.AUTH_SIGNUP_ENABLED

    async def _load_linked_user(
        self,
        session: AsyncSession,
        user_id: UUID,
    ) -> BaseUserModel:
        """Load the user behind an existing link, refusing dead accounts.

        Args:
            session (AsyncSession): Active SQLAlchemy session.
            user_id (UUID): The FK stored on the link row.

        Returns:
            BaseUserModel: The linked user.

        Raises:
            OAuthAccountInactiveException: When the row is gone or
                inactive. A deactivated account must not be revived by
                arriving through the provider instead of the login
                form.
        """
        result = await session.execute(
            select(self.user_model).where(self.user_model.id == user_id)
        )
        user: BaseUserModel | None = result.scalar_one_or_none()
        if user is None or not user.is_active:
            raise OAuthAccountInactiveException(
                details={"user_id": str(user_id)},
            )
        return user

    async def _resolve_oauth_user(
        self,
        session: AsyncSession,
        profile: OAuthUser,
        *,
        locale: str,
        link_by_verified_email: bool,
        allow_account_creation: bool,
    ) -> BaseUserModel:
        """Find or create the local user for a first-time identity.

        Args:
            session (AsyncSession): Active SQLAlchemy session.
            profile (OAuthUser): The identity the provider returned.
            locale (str): Canonical locale, for the placeholder name.
            link_by_verified_email (bool): Whether an existing account
                may be claimed by a verified email match.
            allow_account_creation (bool): Whether an unknown identity
                may create a row.

        Returns:
            BaseUserModel: The user this identity belongs to.

        Raises:
            OAuthEmailMissingException: When the provider returned no
                email.
            OAuthEmailTakenException: When the email is taken and
                linking is not allowed. Refusing names the collision
                rather than hiding it: the alternative is attaching the
                identity anyway, which is account takeover.
            OAuthEmailUnverifiedException: When linking was allowed but
                the provider did not verify the address.
            OAuthRegistrationDisabledException: When creation is
                disabled.
            OAuthAccountInactiveException: When the matched account is
                inactive.
        """
        email = (profile.email or "").strip().lower()
        if not email:
            raise OAuthEmailMissingException(
                details={"provider": profile.provider},
            )
        result = await session.execute(
            select(self.user_model).where(self.user_model.email == email)
        )
        existing: BaseUserModel | None = result.scalar_one_or_none()
        if existing is not None:
            if not link_by_verified_email:
                raise OAuthEmailTakenException(
                    details={"email": email, "provider": profile.provider},
                )
            if profile.email_verified is not True:
                raise OAuthEmailUnverifiedException(
                    details={"email": email, "provider": profile.provider},
                )
            if not existing.is_active:
                raise OAuthAccountInactiveException(
                    details={"email": email, "provider": profile.provider},
                )
            return existing
        if not allow_account_creation:
            raise OAuthRegistrationDisabledException(
                details={"provider": profile.provider},
            )
        return await self._create_oauth_user(
            session,
            profile,
            email=email,
            locale=locale,
        )

    async def _create_oauth_user(
        self,
        session: AsyncSession,
        profile: OAuthUser,
        *,
        email: str,
        locale: str,
    ) -> BaseUserModel:
        """Insert the user row for a brand-new social identity.

        The password is generated rather than left empty because
        ``hashed_password`` is ``NOT NULL`` and every other flow
        assumes a real hash sits there — an empty column would make
        :meth:`~tempest_fastapi_sdk.BaseUserModel.check_password`
        silently return ``False`` for every input, which is correct but
        indistinguishable from a wrong password.

        The generated value is passed back through
        :meth:`_enforce_password_policy` before use. It cannot fail for
        the shipped policy — that is what
        :func:`~tempest_fastapi_sdk.generate_password` guarantees by
        construction — but a project that overrides the policy with
        something the generator cannot satisfy should learn that here,
        loudly, rather than by storing a hash that violates its own
        rules.

        Args:
            session (AsyncSession): Active SQLAlchemy session.
            profile (OAuthUser): The identity the provider returned.
            email (str): The already-normalized email.
            locale (str): Canonical locale, for the placeholder name.

        Returns:
            BaseUserModel: The persisted, active user.

        Raises:
            ValidationException: When the configured policy rejects the
                generated password — a misconfiguration.
        """
        password = generate_password(
            min_length=self.auth_settings.AUTH_PASSWORD_MIN_LENGTH,
            max_bytes=self.auth_settings.AUTH_PASSWORD_MAX_BYTES,
            require_complexity=(self.auth_settings.AUTH_PASSWORD_REQUIRE_COMPLEXITY),
        )
        self._enforce_password_policy(password)
        user = self.user_model(email=email, is_active=True)
        user.hashed_password = self.passwords.hash(password)
        if hasattr(user, "name"):
            user.name = (profile.name or "").strip() or default_display_name(locale)
        session.add(user)
        await session.flush()
        await session.refresh(user)
        return user

    @staticmethod
    def _sync_oauth_account(
        account: BaseUserOAuthAccountModel,
        profile: OAuthUser,
    ) -> None:
        """Copy the provider's current profile onto the link row.

        Runs on every callback, not just the first, so a display name
        or avatar changed at the provider is reflected locally instead
        of freezing at whatever it was the day the account was linked.
        Only the link row is refreshed — the user's own ``name`` is
        left alone, because a user who edited it in this application
        did so on purpose.

        Args:
            account (BaseUserOAuthAccountModel): The link row.
            profile (OAuthUser): The identity the provider returned.
        """
        account.email = profile.email
        account.email_verified = profile.email_verified
        account.name = profile.name
        account.picture = profile.picture

    async def list_oauth_accounts(
        self,
        session: AsyncSession,
        user: BaseUserModel,
    ) -> list[BaseUserOAuthAccountModel]:
        """Return every identity linked to ``user``.

        A user with no links is a user who signed up with a password —
        an ordinary state, so this returns ``[]`` rather than raising.

        Args:
            session (AsyncSession): Active SQLAlchemy session.
            user (BaseUserModel): The authenticated account.

        Returns:
            list[BaseUserOAuthAccountModel]: The linked identities,
            oldest first. Empty when none are linked.

        Raises:
            RuntimeError: When the service was built without an
                ``oauth_account_model``.
        """
        if self.oauth_account_model is None:
            raise RuntimeError(
                "list_oauth_accounts requires a concrete oauth_account_model "
                "passed to UserAuthService(oauth_account_model=...)."
            )
        account_model = self.oauth_account_model
        result = await session.execute(
            select(account_model)
            .where(account_model.user_id == user.id)
            .order_by(account_model.created_at)
        )
        return list(result.scalars().all())

    async def unlink_oauth_account(
        self,
        session: AsyncSession,
        user: BaseUserModel,
        *,
        provider: str,
    ) -> None:
        """Detach ``provider`` from ``user``.

        The user keeps their password and every other link, so the
        account stays reachable — unlinking the only provider on an
        account whose password was generated leaves the reset flow as
        the way back in, which is the same door that flow always
        offered.

        Args:
            session (AsyncSession): Active SQLAlchemy session. The row
                is deleted and flushed; the caller owns the commit.
            user (BaseUserModel): The authenticated account.
            provider (str): Provider key to unlink.

        Raises:
            RuntimeError: When the service was built without an
                ``oauth_account_model``.
            OAuthAccountNotLinkedException: When that provider is not
                linked to this user. A single named resource, so 404 is
                the right answer — unlike the plural listing above.
        """
        if self.oauth_account_model is None:
            raise RuntimeError(
                "unlink_oauth_account requires a concrete oauth_account_model "
                "passed to UserAuthService(oauth_account_model=...)."
            )
        account_model = self.oauth_account_model
        result = await session.execute(
            select(account_model).where(
                account_model.user_id == user.id,
                account_model.provider == provider,
            )
        )
        account = result.scalar_one_or_none()
        if account is None:
            raise OAuthAccountNotLinkedException(
                details={"provider": provider},
            )
        await session.delete(account)
        await session.flush()

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

    async def _attach(
        self, session: AsyncSession, user: BaseUserModel
    ) -> BaseUserModel:
        """Return ``user`` guaranteed to belong to ``session``.

        Every method that receives an already-loaded user from its caller —
        rather than fetching it itself — is one ``session`` mismatch away from
        losing its writes. A detached (or foreign-session) instance accepts
        attribute assignment happily, ``session.flush()`` then finds nothing to
        write because the instance is not in that session's identity map, and the
        following ``session.refresh()`` raises ``InvalidRequestError: Instance is
        not persistent within this Session``. The failure mode is the worst kind:
        the write silently vanishes, and the error surfaces one line later
        pointing at ``refresh`` instead of at the real cause.

        The bundled router hands over a request-scoped, attached instance, so
        this is normally a no-op check. It matters for callers driving the
        service directly — a background task, a CLI command, a test — where the
        user may have been loaded somewhere else entirely.

        ``merge`` is used rather than a re-fetch by primary key so pending
        in-memory changes on the passed instance are carried over instead of
        being discarded.

        Args:
            session (AsyncSession): The session the write must land on.
            user (BaseUserModel): The user handed in by the caller.

        Returns:
            BaseUserModel: ``user`` itself when already attached, otherwise the
                session-local instance to mutate in its place.
        """
        if user in session:
            return user
        merged: BaseUserModel = await session.merge(user)
        return merged

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
        user = await self._attach(session, user)
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
        refresh = self._encode_refresh(user)
        return access, refresh

    def _encode_access(self, user: BaseUserModel) -> str:
        """Sign a short-lived access JWT carrying ``sub`` + ``email``.

        The ``typ`` claim is what stops the other tokens minted with this
        same secret — the refresh token and the MFA-pending token — from
        being replayed as an access token against a route guard that only
        reads ``sub``. See
        :mod:`tempest_fastapi_sdk.utils.token_types`.
        """
        return self.jwt.encode(
            {"sub": str(user.id), "email": user.email, "typ": ACCESS_TOKEN_TYPE},
            ttl=timedelta(seconds=self.jwt_settings.JWT_ACCESS_TTL_SECONDS),
        )

    def _encode_refresh(self, user: BaseUserModel) -> str:
        """Sign a long-lived stateless refresh JWT.

        Carries both ``typ`` and the historical ``refresh: True`` marker:
        the former is what the request guards check, the latter keeps a
        token issued by this version exchangeable at ``/auth/refresh`` by a
        service still running an older SDK during a rolling deploy.
        """
        return self.jwt.encode(
            {"sub": str(user.id), "refresh": True, "typ": REFRESH_TOKEN_TYPE},
            ttl=timedelta(seconds=self.jwt_settings.JWT_REFRESH_TTL_SECONDS),
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
            return access, self._encode_refresh(user)
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
            is_refresh = (
                claims.get("refresh") is True or claims.get("typ") == REFRESH_TOKEN_TYPE
            )
            if not is_refresh:
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

        Notes:
            Asserts that ``refresh_token_model`` is configured rather than
            handling ``None``: every caller reaches this only after the
            opt-in DB-backed refresh flow is known to be enabled.
        """
        model = self.refresh_token_model
        assert model is not None
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

        Notes:
            Reuse of an already-rotated token is the classic stolen-token
            signal, so the whole family is revoked before raising: without
            that, an attacker holding a descendant token could keep the
            session alive indefinitely. The revocation is **committed** —
            see :meth:`_revoke_reused_family` for why a flush was not
            enough.

            Timestamps are compared as naive UTC because the two supported
            backends disagree — SQLite stores naive, Postgres returns
            timezone-aware — and normalizing lets one code path serve both.

            Asserts that ``refresh_token_model`` is configured; callers only
            get here with the opt-in refresh flow enabled.
        """
        model = self.refresh_token_model
        assert model is not None
        digest = hash_opaque_token(token)
        result = await session.execute(select(model).where(model.token_hash == digest))
        record: BaseUserRefreshTokenModel | None = result.scalar_one_or_none()
        if record is None:
            raise InvalidTokenException(message="refresh token not recognized")
        if record.revoked_at is not None:
            raise InvalidTokenException(message="refresh token revoked")
        if record.used_at is not None:
            await self._revoke_reused_family(session, record)
            raise InvalidTokenException(message="refresh token reuse detected")
        now = utcnow().replace(tzinfo=None)
        expires_at = (
            record.expires_at.replace(tzinfo=None)
            if record.expires_at.tzinfo is not None
            else record.expires_at
        )
        if expires_at < now:
            raise InvalidTokenException(message="refresh token expired")
        return record

    async def _revoke_reused_family(
        self,
        session: AsyncSession,
        record: BaseUserRefreshTokenModel,
    ) -> None:
        """Persist the revocation that a detected replay has to leave behind.

        Args:
            session (AsyncSession): The caller's session, which is about to
                be unwound by the exception this precedes.
            record (BaseUserRefreshTokenModel): The replayed token row,
                read for its ``family_id`` before anything expires it.

        The caller raises immediately after this returns, so a flush is not
        enough: in a FastAPI request the exception travels out through the
        session dependency's teardown, the unit of work is rolled back, and
        the revocation goes with it. Measured on the issue's repro — the
        replay was refused with 401 and ``revoked: 0`` of two rows, so every
        descendant token kept refreshing. Detection without consequence is
        the worst of the three outcomes: it looks like the theft was handled.

        The order here is the whole design:

        1. ``family_id`` is read **first**, because the rollback below
           expires the instance and reading an expired column in async
           context raises ``MissingGreenlet`` rather than reloading.
        2. ``rollback()`` drops whatever else the request had staged. Those
           writes were already doomed — the request is about to fail — and
           committing them as a side effect of a security decision would be
           a surprise nobody asked for.
        3. Only then the ``UPDATE``, and a ``commit`` that outlives the
           unwind.

        The revocation deliberately reuses the caller's session rather than
        opening its own. A second session would need a second connection,
        and on SQLite the caller's open read transaction blocks that
        connection's commit — a lock error in place of a revocation, in the
        one configuration every service uses for tests.
        """
        family_id = record.family_id
        await session.rollback()
        await self._revoke_family(session, family_id)
        await session.commit()

    async def _revoke_family(
        self,
        session: AsyncSession,
        family_id: UUID,
    ) -> None:
        """Flip ``revoked_at`` on every still-active token in a family.

        Asserts that ``refresh_token_model`` is configured; callers only
        reach this with the opt-in refresh flow enabled.
        """
        model = self.refresh_token_model
        assert model is not None
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
        query_param: str | None = None,
        strict: bool = False,
        legacy_claims: Collection[str] = (),
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
            query_param (str | None): Query-string parameter to read the
                access token from when both the ``Authorization`` header
                and the cookie are absent (lookup order: header → cookie →
                query). ``None`` (default) disables it. Unlike
                ``cookie_name`` it is **never** auto-derived — it is an
                opt-in escape hatch for cookieless clients such as the
                browser ``EventSource`` (SSE), which cannot send a header.
                A token in the URL leaks into access logs, history and the
                ``Referer`` header, so enable it only over TLS with
                short-lived access tokens (never a refresh token).
            strict (bool): Refuse a token that carries no recognizable
                type marker. ``False`` (default) accepts it, which is
                the compatibility window for sessions minted before the
                ``typ`` claim existed. A service whose legacy tokens
                declared their type under a claim of their own has no
                ``typ`` and no SDK fallback marker on any of them, so
                under the default each of its refresh tokens authorizes
                any route for the length of its TTL — that service wants
                ``strict=True``.
            legacy_claims (Collection[str]): Extra claim names to read
                the token type from when ``typ`` is absent, in order.
                Pair with ``strict=True``. See
                :func:`~tempest_fastapi_sdk.token_type_allowed`.

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
            query_param=query_param,
            strict=strict,
            legacy_claims=legacy_claims,
            session_dependency=session_dependency or self.db.session_dependency,
        )

    # ------------------------------------------------------------------
    # Authorization guards (imperative, on an already-loaded user)
    # ------------------------------------------------------------------

    @staticmethod
    def require_authenticated(
        user: UserT | None,
        *,
        exception: GuardException | None = None,
    ) -> UserT:
        """Assert the user is authenticated; return it narrowed to non-``None``.

        Thin static mirror of
        :func:`tempest_fastapi_sdk.require_authenticated` so a service
        already in scope can guard without an extra import:

            >>> user = auth_service.require_authenticated(current)

        Deliberately **narrower than the function it forwards to**: that one
        takes an unbound ``SubjectT``, because the SDK also hands out
        subjects that are not user models (a ``FirebaseIdentity``, for one).
        Reached through this service the subject is always the service's own
        ``UserT``, so keeping the bound here buys back the checking the free
        function had to give up. Guarding a provider identity calls the
        function directly.

        Args:
            user (UserT | None): The resolved request user.
            exception (GuardException | None): Factory for the refusal.
                ``None`` (default) raises
                :class:`UnauthorizedException` with the generic
                ``UNAUTHORIZED`` code.

        Returns:
            UserT: The same user, narrowed to non-``None``.

        Raises:
            AppException: When ``user`` is ``None`` — whatever
                ``exception`` builds, or :class:`UnauthorizedException`
                (HTTP 401).
        """
        return require_authenticated(user, exception=exception)

    @staticmethod
    def require_active(
        user: UserT | None,
        *,
        exception: GuardException | None = None,
        unauthenticated: GuardException | None = None,
    ) -> UserT:
        """Assert the user is authenticated and active. See :func:`require_active`.

        Args:
            user (UserT | None): The resolved request user.
            exception (GuardException | None): Factory for the refusal
                when ``is_active`` is falsy. ``None`` (default) raises
                :class:`ForbiddenException`.
            unauthenticated (GuardException | None): Factory for the
                ``user is None`` case.

        Returns:
            UserT: The authenticated, active user.

        Raises:
            AppException: When ``user`` is ``None`` (HTTP 401 by
                default) or ``user.is_active`` is falsy (HTTP 403 by
                default).
        """
        return require_active(
            user, exception=exception, unauthenticated=unauthenticated
        )

    @staticmethod
    def require_admin(
        user: UserT | None,
        *,
        exception: GuardException | None = None,
        unauthenticated: GuardException | None = None,
    ) -> UserT:
        """Assert the user is authenticated and an admin. See :func:`require_admin`.

        Args:
            user (UserT | None): The resolved request user.
            exception (GuardException | None): Factory for the refusal
                when ``is_admin`` is falsy. ``None`` (default) raises
                :class:`ForbiddenException` with the generic
                ``FORBIDDEN`` code — which is exactly what a project
                carrying its own ``USER_IS_NOT_ADMIN`` needs to replace.
            unauthenticated (GuardException | None): Factory for the
                ``user is None`` case.

        Returns:
            UserT: The authenticated admin user.

        Raises:
            AppException: When ``user`` is ``None`` (HTTP 401 by
                default) or ``user.is_admin`` is falsy (HTTP 403 by
                default).
        """
        return require_admin(user, exception=exception, unauthenticated=unauthenticated)

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

        The upper bound (``AUTH_PASSWORD_MAX_BYTES``, default 72) is
        measured in UTF-8 **bytes**, because that is the unit bcrypt
        counts: ``bcrypt.hashpw`` raises ``ValueError`` past 72 bytes.
        Without this check that surfaced as an HTTP 500 from signup /
        password-reset / password-change, and 72 bytes is reached well
        before 72 characters on non-ASCII input (four bytes per emoji).

        Args:
            password (str): The plaintext password to check.

        Raises:
            ValidationException: When the password is too short, too
                long for the hasher, or — under complexity mode —
                missing a required character class.
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
        ceiling = self.auth_settings.AUTH_PASSWORD_MAX_BYTES
        encoded_length = len(password.encode("utf-8"))
        if encoded_length > ceiling:
            raise ValidationException(
                message=f"password must be at most {ceiling} bytes",
                details={
                    "max_bytes": ceiling,
                    "length_bytes": encoded_length,
                },
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

        Returns:
            tuple[str, str, datetime]: The plaintext token (surfaced
            exactly once), the rendered URL, and the expiry.
        """
        if self.auth_settings.AUTH_SINGLE_ACTIVE_TOKEN:
            await self._burn_pending_tokens(session, user_id=user_id, purpose=purpose)
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

    async def _burn_pending_tokens(
        self,
        session: AsyncSession,
        *,
        user_id: UUID,
        purpose: UserTokenPurpose,
    ) -> None:
        """Spend every unused token of ``purpose`` belonging to ``user_id``.

        Called by :meth:`_issue_token` before inserting the new row, so
        **only the most recent link opens the account** — the property
        every mainstream provider applies, and the one whose absence
        makes the user's own correct reaction useless.

        The scenario it closes: an attacker requests a password reset
        for a victim. The victim gets a recovery email they did not ask
        for, gets suspicious, and resets the password themselves. That
        is exactly the right response, and without this it does not
        close the window — the attacker's token stays valid until
        ``AUTH_PASSWORD_RESET_TTL_SECONDS``, so a token leaked through
        any side channel (a proxy log, a browser extension, a forwarded
        email, a shared device) still resets the password *after* the
        victim considered the incident handled.

        Applied to every purpose rather than to password reset alone:
        a pending email change to an address the attacker controls has
        the same shape, and so does an activation link. Marking a token
        ``used_at`` rather than deleting it keeps the row for audit and
        reuses the check :meth:`_lookup_token` already performs.

        Expired rows are marked too. Filtering them out would buy a
        clock comparison in SQL to avoid writing to rows that are
        already dead.

        Args:
            session (AsyncSession): Active SQLAlchemy session. The
                statement is executed but not committed — the caller
                owns the transaction, so the burn and the new token land
                together or not at all.
            user_id (UUID): Owner of the tokens to spend.
            purpose (UserTokenPurpose): Which flow's tokens to spend.
                Only the same purpose is touched: requesting a password
                reset does not invalidate a pending email change.
        """
        await session.execute(
            update(self.token_model)
            .where(
                self.token_model.user_id == user_id,
                self.token_model.purpose == purpose.value,
                self.token_model.used_at.is_(None),
            )
            .values(used_at=utcnow())
        )

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
        """Render + send the activation email when EmailUtils is wired.

        The language comes from :func:`resolve_locale`, so the user's
        stored ``locale`` wins over ``AUTH_DEFAULT_LOCALE``, and the link
        carries the choice forward as ``?lang=`` (unless
        ``AUTH_STAMP_LOCALE_IN_LINK`` is off) so the page it opens cannot
        answer in a different language.
        """
        if self.email is None or self.auth_settings.AUTH_RETURN_TOKEN_IN_RESPONSE:
            return
        _plain, url, expires_at = token_bundle
        locale = resolve_locale(
            user=user,
            default=self.auth_settings.AUTH_DEFAULT_LOCALE,
        )
        if self.auth_settings.AUTH_STAMP_LOCALE_IN_LINK:
            url = stamp_locale(url, locale)
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
        """Render + send the reset email when EmailUtils is wired.

        Same locale resolution as the activation email: stored user
        preference first, the emailed link stamped with the result.
        """
        if self.email is None or self.auth_settings.AUTH_RETURN_TOKEN_IN_RESPONSE:
            return
        _plain, url, expires_at = token_bundle
        locale = resolve_locale(
            user=user,
            default=self.auth_settings.AUTH_DEFAULT_LOCALE,
        )
        if self.auth_settings.AUTH_STAMP_LOCALE_IN_LINK:
            url = stamp_locale(url, locale)
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
        """Send the confirmation email to the NEW address when wired.

        Localized from the user's stored preference, with the link
        stamped so the confirmation page matches this email.
        """
        if self.email is None or self.auth_settings.AUTH_RETURN_TOKEN_IN_RESPONSE:
            return
        _plain, url, expires_at = token_bundle
        locale = resolve_locale(
            user=user,
            default=self.auth_settings.AUTH_DEFAULT_LOCALE,
        )
        if self.auth_settings.AUTH_STAMP_LOCALE_IN_LINK:
            url = stamp_locale(url, locale)
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
        """Send the re-verification email to the current address when wired.

        Localized from the user's stored preference, with the link
        stamped so the verification page matches this email.
        """
        if self.email is None or self.auth_settings.AUTH_RETURN_TOKEN_IN_RESPONSE:
            return
        _plain, url, expires_at = token_bundle
        locale = resolve_locale(
            user=user,
            default=self.auth_settings.AUTH_DEFAULT_LOCALE,
        )
        if self.auth_settings.AUTH_STAMP_LOCALE_IN_LINK:
            url = stamp_locale(url, locale)
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
        carries no token, only a security notice. Nothing to stamp for
        the same reason: the notice has no link.
        """
        if self.email is None or not self.auth_settings.AUTH_EMAIL_CHANGE_NOTIFY_OLD:
            return
        locale = resolve_locale(
            user=user,
            default=self.auth_settings.AUTH_DEFAULT_LOCALE,
        )
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

        Args:
            user (BaseUserModel): The user to inspect.

        Returns:
            bool: Whether the condition holds for that user.
        """
        if not self.auth_settings.AUTH_MFA_ENABLED:
            return False
        return getattr(user, "totp_enabled_at", None) is not None

    def issue_mfa_token(self, user: BaseUserModel) -> str:
        """Mint the short-lived JWT that bridges step 1 and step 2 of login.

        The token proves the password step only. Its ``typ`` claim keeps a
        route guard from accepting it as an access token, which would let a
        caller who knows just the password skip the second factor
        entirely.

        Args:
            user (BaseUserModel): The user to inspect.

        Returns:
            str: The generated value.
        """
        return self.jwt.encode(
            {
                "sub": str(user.id),
                "purpose": "mfa_pending",
                "typ": MFA_TOKEN_TYPE,
            },
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

        Notes:
            Enrolling wipes any previously stored recovery codes — enrollment
            has rotation semantics, so an old code set never stays valid
            alongside a new one.
        """
        from tempest_fastapi_sdk.utils.totp import TOTPHelper

        totp = TOTPHelper(issuer=self.auth_settings.AUTH_MFA_ISSUER)
        secret = totp.generate_secret()
        provisioning = totp.provisioning_uri(secret, user.email)
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
        user = await self._attach(session, user)
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
        user = await self._attach(session, user)
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
        user = await self._attach(session, user)
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
        """Check ``code`` against TOTP first, then unused recovery codes.

        The recovery-code branch is the fallback, matched against the
        single-use codes handed out (in plaintext) at enrollment.

        Args:
            session (AsyncSession): The active DB session.
            user (BaseUserModel): The user being verified.
            code (str): The submitted TOTP or recovery code.
            recovery_code_model (type[BaseUserRecoveryCodeModel]): The model
                holding this project's recovery codes.

        Returns:
            bool: ``True`` when the code matched a valid TOTP window or an
            unused recovery code.
        """
        from tempest_fastapi_sdk.utils.totp import TOTPHelper

        if user.totp_secret:
            totp = TOTPHelper(issuer=self.auth_settings.AUTH_MFA_ISSUER)
            if totp.verify(
                user.totp_secret,
                code,
                window=self.auth_settings.AUTH_MFA_VERIFY_WINDOW,
            ):
                return True
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
