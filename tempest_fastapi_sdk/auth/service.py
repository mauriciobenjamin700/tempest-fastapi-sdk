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

import logging
from datetime import datetime, timedelta
from typing import TYPE_CHECKING, cast
from uuid import UUID, uuid4

from sqlalchemy import delete, inspect, or_, select, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from tempest_fastapi_sdk.auth import guards as auth_guards
from tempest_fastapi_sdk.auth.exceptions import (
    AuthExceptions,
    MFAAlreadyEnrolledException,
    MFANotEnrolledException,
    MfaThrottledException,
)
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
    ActivationWithheld,
    EmailChangeToken,
    EmailVerificationToken,
    PasswordResetToken,
)
from tempest_fastapi_sdk.db.integrity import (
    IntegrityViolation,
    parse_integrity_error,
)
from tempest_fastapi_sdk.db.user_token_model import (
    BaseUserTokenModel,
    UserTokenPurpose,
)
from tempest_fastapi_sdk.exceptions import (
    ConflictException,
    EmailDeliveryException,
    ForbiddenException,
    IntegrationDisabledException,
    InvalidTokenException,
    NotFoundException,
    OAuthAccountInactiveException,
    OAuthAccountNotLinkedException,
    OAuthEmailMissingException,
    OAuthEmailTakenException,
    OAuthEmailUnverifiedException,
    OAuthRegistrationDisabledException,
    TooManyRequestsException,
    UnauthorizedException,
    ValidationException,
)
from tempest_fastapi_sdk.utils._signature import accepts_keyword
from tempest_fastapi_sdk.utils.datetime import utcnow
from tempest_fastapi_sdk.utils.jwt import JWTUtils
from tempest_fastapi_sdk.utils.opaque_token import (
    generate_opaque_token,
    hash_opaque_token,
)
from tempest_fastapi_sdk.utils.password import (
    PasswordPolicy,
    PasswordUtils,
    PasswordViolationCode,
    check_password_policy,
    generate_password,
)
from tempest_fastapi_sdk.utils.throttle import login_throttle_key
from tempest_fastapi_sdk.utils.token_types import (
    ACCESS_TOKEN_TYPE,
    AMR_CLAIM,
    AMR_EXTERNAL,
    AMR_OTP,
    MFA_TOKEN_TYPE,
    PASSWORD_AMR,
    REFRESH_TOKEN_TYPE,
    token_amr,
)

if TYPE_CHECKING:
    from collections.abc import (
        Awaitable,
        Callable,
        Collection,
        Coroutine,
        Mapping,
        Sequence,
    )
    from typing import Any, NoReturn

    from fastapi import APIRouter, Request
    from sqlalchemy import CursorResult

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
    from tempest_fastapi_sdk.sse.broker import SSEBroker
    from tempest_fastapi_sdk.utils.email import EmailUtils
    from tempest_fastapi_sdk.utils.throttle import AttemptThrottle


_LOGGER: logging.Logger = logging.getLogger(__name__)
"""Where an enumeration-safe flow records the mail it could not send."""

SIGNUP_PROTECTED_FIELDS: frozenset[str] = frozenset(
    {"id", "email", "hashed_password", "is_active", "is_admin"}
)
"""User columns ``signup(fields=...)`` refuses to set.

The service owns each of them: ``email`` is normalized and checked for
duplicates, ``hashed_password`` comes from the policy-checked password,
``is_active`` from ``AUTH_AUTO_ACTIVATE``. ``is_admin`` and ``id`` are
never something a sign-up form decides. ``make_auth_router`` drops them
from a ``signup_schema`` before forwarding, so a schema field named
``is_admin`` cannot reach the row.
"""


async def revoke_user_refresh_tokens(
    session: AsyncSession,
    refresh_token_model: type[BaseUserRefreshTokenModel],
    *,
    user_id: UUID,
) -> int:
    """Revoke every still-active refresh token one user owns.

    "Log out everywhere", addressed by user id rather than by a
    presented token — the shape an administrator needs. Rotating a
    credential out of band (an operator resetting a password from the
    CLI, a support agent locking a compromised account) leaves every
    already-issued refresh token exchangeable until its natural expiry,
    which is exactly the window the reset exists to close, and
    :meth:`UserAuthService.revoke_refresh_token` cannot close it because
    it starts from a token the operator does not have.

    Rows already carrying a ``revoked_at`` are left untouched, so the
    returned count is the number of sessions this call actually killed
    and calling twice reports ``0`` the second time.

    The update is flushed, never committed — the caller owns the
    transaction, so the revocation lands or rolls back together with
    whatever else it is changing (the new password hash, typically).

    Args:
        session (AsyncSession): Active SQLAlchemy session.
        refresh_token_model (type[BaseUserRefreshTokenModel]): The
            project's concrete refresh-token table.
        user_id (UUID): Owner whose sessions are being killed.

    Returns:
        int: How many refresh-token rows were revoked.
    """
    result = cast(
        "CursorResult[Any]",
        await session.execute(
            update(refresh_token_model)
            .where(
                refresh_token_model.user_id == user_id,
                refresh_token_model.revoked_at.is_(None),
            )
            .values(revoked_at=utcnow())
        ),
    )
    await session.flush()
    return int(result.rowcount or 0)


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
        exceptions: AuthExceptions | None = None,
        refuses_email_link: Callable[[Any], bool] | None = None,
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
                When ``None``, the service returns the link in the
                response (and never tries to send) — except under
                ``ENV=production``, where every flow carrying a link
                answers ``503 INTEGRATION_DISABLED`` instead (see
                :meth:`require_mail_delivery`).
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
            exceptions (AuthExceptions | None): The class raised at each
                refusal point — wrong credentials, inactive account,
                taken email, each password-policy violation, invalid
                single-use link. Pass your own subclasses to keep the
                ``code`` your clients already handle. ``None`` (default)
                raises the SDK's own classes.
            refuses_email_link (Callable[[Any], bool] | None): Predicate
                deciding, per account, whether the *emailed* link flows
                are closed to it — an operator or staff member whose
                mailbox must not be enough to rotate the password or
                reactivate the account. Called with the service's own
                ``user_model`` instance; ``True`` means refused, so a
                privileged account passes ``lambda user: user.is_staff``.
                The argument is ``Any`` for the same reason as
                ``on_signup``'s: callable parameters are contravariant, so
                a signature naming ``BaseUserModel`` would reject the
                predicate everyone writes (typed against their own model).
                ``None`` (default) keeps every account on the current
                behavior. See :class:`EmailLinkForbiddenException`.
        """
        self.exceptions: AuthExceptions = exceptions or AuthExceptions()
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
        self.refuses_email_link: Callable[[Any], bool] | None = refuses_email_link
        self.session_revokers: list[Callable[[UUID], Awaitable[int]]] = []

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
        fields: Mapping[str, Any] | None = None,
    ) -> tuple[BaseUserModel, ActivationToken | ActivationWithheld | None]:
        """Create a user row and (optionally) issue an activation token.

        The second tuple slot says how the account gets active, and each
        of its three values means one thing only:

        * ``None`` — ``AUTH_AUTO_ACTIVATE`` is true and the user is born
          ``is_active=True``; the caller can mint JWTs right away. The
          ``refuses_email_link`` predicate is not consulted, because an
          account born active depends on no link.
        * :class:`ActivationToken` — the user is inserted with
          ``is_active=False`` and an activation token is persisted (and
          mailed, when ``EmailUtils`` is wired) for the caller to surface.
        * :class:`ActivationWithheld` — the user is inserted with
          ``is_active=False`` but ``refuses_email_link`` matches the new
          row, so no token is persisted and no mail is sent: the link
          would only answer ``EMAIL_LINK_FORBIDDEN`` when used. The
          predicate sees the row as inserted, with ``fields`` applied and
          before any ``on_signup`` hook of the router runs. The caller
          must not mint JWTs for it, and should answer the way it answers
          a pending signup, so the refusal cannot be enumerated.

        Args:
            session (AsyncSession): Active SQLAlchemy session.
            email (str): Account email — normalized to lowercase.
            password (str): Plaintext password; length is enforced
                against ``AUTH_PASSWORD_MIN_LENGTH``.
            name (str | None): Optional display name; passed
                through to the model when the column exists.
            fields (Mapping[str, Any] | None): Extra column values set on
                the new row **before** the insert, which is what lets a
                product fill a ``NOT NULL`` column the SDK does not know
                about (a phone, a document). Each key must be a mapped
                column of ``user_model`` outside
                :data:`SIGNUP_PROTECTED_FIELDS`. ``None`` (default) sets
                nothing extra.

        Returns:
            tuple[BaseUserModel, ActivationToken | ActivationWithheld | None]:
            The persisted user and how it gets active — ``None`` when born
            active, the token to surface, or the marker that the link was
            withheld.

        Raises:
            IntegrationDisabledException: When ``AUTH_AUTO_ACTIVATE`` is
                off and the e-mail integration is switched off — see
                :meth:`require_mail_delivery`. Raised before any row is
                written.
            EmailDeliveryException: When the activation mail could not be
                sent (``503 EMAIL_UNAVAILABLE``). The user and token rows
                are flushed but not committed, so the caller's rollback
                drops them — ``make_auth_router`` never reaches its commit
                and the request session discards both. Signup already
                answers ``409`` for a taken address, so the ``503`` tells
                a caller nothing it could not learn there.
            ValueError: When ``fields`` names something that is not a
                column of ``user_model``, or a protected column; and when
                the insert is refused because a ``NOT NULL`` column got no
                value — a wiring defect, so the message names the column
                instead of surfacing a bare ``IntegrityError``.
            ValidationException: When the password is too short.
            ConflictException: When the email, or another unique column,
                is already taken. For a column other than ``email`` the
                row is refused by the database, and ``details["columns"]``
                carries what the server named; the session must then be
                rolled back before reuse, as after any failed flush.
        """
        if not self.auth_settings.AUTH_AUTO_ACTIVATE:
            self.require_mail_delivery()
        extra = dict(fields or {})
        self._check_signup_fields(extra)
        self._enforce_password_policy(password)
        normalized = email.strip().lower()
        existing = await session.execute(
            select(self.user_model).where(
                self.user_model.email == normalized,
            )
        )
        if existing.scalar_one_or_none() is not None:
            raise self.exceptions.email_taken(
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
        for key, value in extra.items():
            setattr(user, key, value)
        session.add(user)
        try:
            await session.flush()
        except IntegrityError as error:
            raise self._signup_integrity_error(error) from error
        await session.refresh(user)

        if self.auth_settings.AUTH_AUTO_ACTIVATE:
            return user, None
        if self.email_link_refused(user):
            return user, ActivationWithheld(user_id=user.id)

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

    def signup_column_names(self) -> frozenset[str]:
        """Return the ``user_model`` columns ``signup(fields=...)`` accepts.

        Every mapped column attribute, minus
        :data:`SIGNUP_PROTECTED_FIELDS`. ``make_auth_router`` uses it to
        forward only those fields of a ``signup_schema`` that are columns,
        leaving the rest (a terms checkbox, a captcha answer) to
        ``on_signup``.

        Returns:
            frozenset[str]: The assignable column attribute names.
        """
        columns = {attribute.key for attribute in inspect(self.user_model).column_attrs}
        return frozenset(columns - SIGNUP_PROTECTED_FIELDS)

    def _check_signup_fields(self, fields: Mapping[str, Any]) -> None:
        """Refuse ``signup(fields=...)`` keys the service will not set.

        Args:
            fields (Mapping[str, Any]): The extra column values.

        Raises:
            ValueError: When a key is protected or not a column.
        """
        protected = sorted(set(fields) & SIGNUP_PROTECTED_FIELDS)
        if protected:
            raise ValueError(
                f"signup(fields=...) cannot set protected columns: {protected}"
            )
        unknown = sorted(set(fields) - self.signup_column_names())
        if unknown:
            raise ValueError(
                f"signup(fields=...) keys are not columns of "
                f"{self.user_model.__name__}: {unknown}"
            )

    def _signup_integrity_error(self, error: IntegrityError) -> Exception:
        """Translate a refused signup insert into the error to raise.

        A unique violation is a ``409`` the client can act on: a second
        signup with the same phone, or the same email arriving between
        the duplicate check and the insert. The email case raises
        ``exceptions.email_taken``, the same class the duplicate check
        raises, so the product's ``code`` does not depend on which of the
        two caught it. A ``NOT NULL`` violation is a
        wiring defect on the product side — a column the model requires
        and nobody passed in ``fields`` — so it stays a server error, but
        one that names the column.

        Args:
            error (IntegrityError): The error the flush raised.

        Returns:
            Exception: A :class:`ConflictException` for a unique
            violation, a :class:`ValueError` for a ``NOT NULL`` one, or
            ``error`` itself for anything else.
        """
        failure = parse_integrity_error(error)
        if failure.kind is IntegrityViolation.UNIQUE:
            column = failure.column
            conflict = (
                self.exceptions.email_taken if column == "email" else ConflictException
            )
            return conflict(
                message=(
                    f"{column} already in use" if column else "account already exists"
                ),
                details={
                    "columns": list(failure.columns),
                    "constraint": failure.constraint,
                },
                field=column,
            )
        if failure.kind is IntegrityViolation.NOT_NULL:
            return ValueError(
                f"signup could not insert {self.user_model.__name__}: column "
                f"{failure.column!r} is NOT NULL and got no value; pass it in "
                "signup(fields=...) or give the column a default"
            )
        return error

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
            ForbiddenException: When ``refuses_email_link`` matches the
                account — ``exceptions.email_link_forbidden``. The token
                is consumed, the spend is committed, and ``is_active`` is
                left untouched.
        """
        record = await self._consume_token(
            session,
            token=token,
            purpose=UserTokenPurpose.ACTIVATION,
        )
        user: BaseUserModel | None = await session.get(self.user_model, record.user_id)
        if user is None:
            raise self.exceptions.invalid_token(
                message="token references a missing user"
            )
        if self.email_link_refused(user):
            await self._refuse_email_link(session)
        user.is_active = True
        await session.flush()
        await session.refresh(user)
        return user

    async def request_activation(
        self,
        session: AsyncSession,
        *,
        email: str,
    ) -> ActivationToken | None:
        """Re-issue the activation link for an inactive account.

        The signup email is sent once. When it does not arrive — a typo in
        a spam filter, a bounced relay, a closed tab — the account is
        stuck: signing up again conflicts on the email, logging in is
        refused because the user is inactive, and
        :meth:`request_email_verification` needs a bearer token that only a
        successful login hands out. This is the way back in, and it needs
        no credentials by design.

        "Inactive" includes an account an operator deactivated
        (``is_active=False``, e.g. ``tempest user deactivate``): the link
        goes to it too, and opening it makes the account active again.
        That is intended — deactivation blocks login, and the holder of
        the mailbox can reactivate it on their own. To close the account
        for good, match it with the ``refuses_email_link`` predicate, which
        this flow answers with no token and no mail.

        Nothing is leaked about the address. An unknown email, an already
        active account and a genuine re-send are indistinguishable to the
        caller: all three return ``None`` unless the token is meant to be
        surfaced, and the route answers ``202`` either way. An active
        account is deliberately *not* re-activated or re-mailed — the link
        would authorize nothing and the mail would only tell a stranger
        that the address has an account here. An account the predicate
        ``refuses_email_link`` matches is answered the same way: no token,
        no mail, ``None``.

        A mail the SMTP server does not take is answered the same way too:
        the :class:`~tempest_fastapi_sdk.exceptions.EmailDeliveryException`
        is logged at ``ERROR`` and swallowed, nothing is stored (the
        previous link keeps working), and ``None`` comes back — a ``503``
        here would exist only for registered pending accounts, which is
        the enumeration this flow avoids. See
        :meth:`_issue_token_after_mail`.

        Args:
            session (AsyncSession): Active SQLAlchemy session.
            email (str): Account email, matched case-insensitively.

        Returns:
            ActivationToken | None: The token bundle when the caller
            surfaces the link (``AUTH_RETURN_TOKEN_IN_RESPONSE``, or no
            ``EmailUtils`` wired) and the account was indeed pending;
            ``None`` otherwise, including when the mail failed.

        Raises:
            IntegrationDisabledException: When the e-mail integration is
                switched off — see :meth:`require_mail_delivery`.
        """
        self.require_mail_delivery()
        normalized = email.strip().lower()
        user_result = await session.execute(
            select(self.user_model).where(
                self.user_model.email == normalized,
            )
        )
        user: BaseUserModel | None = user_result.scalar_one_or_none()
        if user is None or user.is_active or self.email_link_refused(user):
            return None

        activation = await self._issue_token_after_mail(
            session,
            user=user,
            purpose=UserTokenPurpose.ACTIVATION,
            ttl_seconds=self.auth_settings.AUTH_ACTIVATION_TTL_SECONDS,
            url_template=self.auth_settings.AUTH_ACTIVATION_URL_TEMPLATE,
            mail=self._maybe_send_activation_email,
        )
        if activation is None:
            return None

        if self.auth_settings.AUTH_RETURN_TOKEN_IN_RESPONSE or self.email is None:
            return ActivationToken(
                user_id=user.id,
                token=activation[0],
                url=activation[1],
                expires_at=activation[2],
            )
        return None

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

        Every refusal pays one bcrypt verification: an unknown address
        is checked against a throwaway hash
        (:meth:`~tempest_fastapi_sdk.PasswordUtils.dummy_verify`) and an
        inactive account's password is verified before the
        ``is_active`` check, so neither the body nor the response time
        separates the three refusals.

        ``AUTH_REVEAL_INACTIVE_ACCOUNT`` changes one branch only: the
        **right** password on a deactivated account raises
        ``exceptions.account_inactive`` (``403`` ``ACCOUNT_INACTIVE`` by
        default) instead of the generic refusal, so the owner learns why
        they cannot get in. The password is still verified first, so a
        wrong one stays the generic refusal and the cost stays one
        bcrypt.

        Args:
            session (AsyncSession): Active SQLAlchemy session.
            email (str): Login identifier.
            password (str): Plaintext password.

        Returns:
            BaseUserModel: The authenticated user.

        Raises:
            UnauthorizedException: On any failure — wrong password,
                missing user, inactive user (``exceptions.invalid_credentials``).
                The message is deliberately generic so attackers can't
                enumerate accounts.
            ForbiddenException: When ``AUTH_REVEAL_INACTIVE_ACCOUNT`` is on
                and the right password opens a deactivated account
                (``exceptions.account_inactive``).
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
            self.passwords.dummy_verify(password)
            raise self.exceptions.invalid_credentials(
                message="invalid email or password"
            )
        if not self.passwords.verify(password, user.hashed_password):
            raise self.exceptions.invalid_credentials(
                message="invalid email or password"
            )
        if not user.is_active:
            if self.auth_settings.AUTH_REVEAL_INACTIVE_ACCOUNT:
                raise self.inactive_account_error()
            raise self.exceptions.invalid_credentials(
                message="invalid email or password"
            )
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
           provider's own flag. Linking also requires that activating
           a local account proves mailbox control here, so it is
           refused under ``AUTH_AUTO_ACTIVATE``,
           ``AUTH_RETURN_TOKEN_IN_RESPONSE`` or a service without
           ``EmailUtils``: there, an active row says nothing about who
           owns its address.
        3. **Creation** — an unknown identity with an unknown email.
           Creates the user row with the provider's email, the
           provider's name (or a localized placeholder) and a generated
           password, then links the identity to it. Only when the
           provider states it verified the address
           (``email_verified is True``): the row owns that address from
           the start, so an unverified claim is refused rather than
           turned into an account.

        A created account is **active immediately**: the identity
        provider performed the verification, as its ``email_verified``
        flag states, so re-verifying by email would ask the user to
        prove something Google already proved. ``AUTH_AUTO_ACTIVATE`` is not consulted
        here — a project that wants human approval before first login
        turns account creation off and has an administrator create and
        link the row.

        The generated password is never shown to anyone. The account
        owner reaches a password of their own through the ordinary
        ``POST /auth/password-reset/request``, which needs no new flow
        because the email is already on the row.

        This method always issues the pair, ``amr=["ext"]``, whether or
        not the account has TOTP enrolled. The bundled router no longer
        calls it: it calls :meth:`authenticate_oauth` and asks an enrolled
        account for the second factor first. Call it directly only where
        that is the decision you want.

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
                another account and linking is not allowed, or this
                service's activation does not prove mailbox control.
            OAuthEmailUnverifiedException: When the provider did not
                state it verified the address, and the identity would
                either link to an existing account or create a new one.
                Deliberately distinct from the one above: that one has a
                next step for the user, this one has none.
            OAuthRegistrationDisabledException: When the identity is
                unknown and account creation is disabled.
            OAuthAccountInactiveException: When the resolved account is
                inactive.
        """
        user = await self.authenticate_oauth(
            session,
            profile,
            locale=locale,
            link_by_verified_email=link_by_verified_email,
            allow_account_creation=allow_account_creation,
        )
        access, refresh = await self._issue_pair_with_amr(
            session, user, family_id=None, amr=(AMR_EXTERNAL,)
        )
        return user, access, refresh

    async def authenticate_oauth(
        self,
        session: AsyncSession,
        profile: OAuthUser,
        *,
        locale: str,
        link_by_verified_email: bool | None = None,
        allow_account_creation: bool | None = None,
    ) -> BaseUserModel:
        """Resolve a third-party identity to a local user, issuing nothing.

        Everything :meth:`login_with_oauth` does except minting the pair:
        the ``(provider, subject)`` lookup, the verified-email link, the
        account creation, the ``last_login_at`` stamps. The bundled router
        calls this one so it can ask for the second factor of an account
        with TOTP enrolled before any session exists, exactly like
        ``POST /auth/login``. See :meth:`login_with_oauth` for the
        resolution order and its reasons.

        Args:
            session (AsyncSession): Active SQLAlchemy session. Rows are
                added and flushed; the caller owns the commit.
            profile (OAuthUser): The identity the provider returned,
                normalized by the client.
            locale (str): Canonical locale for this request; picks the
                placeholder display name of a created account.
            link_by_verified_email (bool | None): Override for
                ``AUTH_OAUTH_LINK_BY_VERIFIED_EMAIL``.
            allow_account_creation (bool | None): Override for
                ``AUTH_OAUTH_ALLOW_ACCOUNT_CREATION``.

        Returns:
            BaseUserModel: The authenticated, active user.

        Raises:
            RuntimeError: When the service was built without an
                ``oauth_account_model``.
            OAuthEmailMissingException: When the provider returned no email.
            OAuthEmailTakenException: When the email belongs to another
                account and linking is not allowed, or this service's
                activation does not prove mailbox control.
            OAuthEmailUnverifiedException: When the provider did not state
                it verified the address, on the link or the creation path.
            OAuthRegistrationDisabledException: When the identity is unknown
                and account creation is disabled.
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
        return user

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

    def _activation_proves_email_ownership(self) -> bool:
        """Return whether an active local account has proven its email.

        The user table has no verification timestamp, so the only
        evidence that the owner of an address created the row is that
        the row became active, and that evidence holds only when
        activation required a link delivered to the mailbox. Three
        configurations break it, each letting a registrant activate an
        address they cannot read: ``AUTH_AUTO_ACTIVATE`` (active at
        signup), ``AUTH_RETURN_TOKEN_IN_RESPONSE`` (the activation link
        comes back in the signup response), and a service built without
        ``EmailUtils`` (which returns the link the same way). The same
        three let an e-mail change complete without the new mailbox.

        Returns:
            bool: ``True`` when activating, or changing the email of, a
            local account requires reading the mailbox.
        """
        if self.email is None:
            return False
        if self.auth_settings.AUTH_AUTO_ACTIVATE:
            return False
        return not self.auth_settings.AUTH_RETURN_TOKEN_IN_RESPONSE

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

        Both doors that turn a provider's word about an email into a
        local account demand that word be a verified ``yes``
        (``email_verified is True``): linking an existing row, and
        creating a new one. A created account owns the address from its
        first second, so creating it from an unverified claim would let
        whoever controls the provider identity squat an address they do
        not own, and keep the link after the real owner recovers the
        account by password reset.

        Linking also needs the *local* side to have proven the address.
        The user table records no verification timestamp, only
        ``is_active``, and ``is_active`` is proof of mailbox control only
        when activation went through a mailed link. Under
        ``AUTH_AUTO_ACTIVATE``, ``AUTH_RETURN_TOKEN_IN_RESPONSE`` or a
        service with no ``EmailUtils`` the registrant activates without
        the mailbox, so a stranger could pre-register the address and
        absorb its owner's first social login; those configurations
        refuse the link (see :meth:`_activation_proves_email_ownership`).

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
                linking is not allowed, or when linking is allowed but
                this service's configuration lets an account become
                active without proving its email. Refusing names the
                collision rather than hiding it: the alternative is
                attaching the identity anyway, which is account takeover.
            OAuthEmailUnverifiedException: When the provider did not
                state it verified the address — whether the identity
                would link to an existing account or create a new one.
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
            if not self._activation_proves_email_ownership():
                raise OAuthEmailTakenException(
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
        if profile.email_verified is not True:
            raise OAuthEmailUnverifiedException(
                details={"email": email, "provider": profile.provider},
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
        ``AUTH_RETURN_TOKEN_IN_RESPONSE`` flag. An account the
        ``refuses_email_link`` predicate matches is answered
        exactly like an unknown address: no token is minted, no
        mail goes out, and ``None`` comes back.

        A mail the SMTP server does not take is answered like an
        unknown address too: the
        :class:`~tempest_fastapi_sdk.exceptions.EmailDeliveryException`
        is logged at ``ERROR`` and swallowed, no token is stored (a
        previous link keeps working), and ``None`` comes back — a
        ``503`` only for registered addresses would be an enumeration
        oracle. See :meth:`_issue_token_after_mail`.

        Args:
            session (AsyncSession): Active SQLAlchemy session.
            email (str): Account email.

        Returns:
            PasswordResetToken | None: The token bundle when the
            caller is configured to surface the link, ``None``
            when the link is meant to live only in the email.

        Raises:
            IntegrationDisabledException: When the e-mail integration is
                switched off — see :meth:`require_mail_delivery`. Raised
                before the lookup, for known and unknown addresses alike.
        """
        self.require_mail_delivery()
        normalized = email.strip().lower()
        user_result = await session.execute(
            select(self.user_model).where(
                self.user_model.email == normalized,
            )
        )
        user_obj = user_result.scalar_one_or_none()
        user: BaseUserModel | None = user_obj
        if user is None or self.email_link_refused(user):
            return None

        reset = await self._issue_token_after_mail(
            session,
            user=user,
            purpose=UserTokenPurpose.PASSWORD_RESET,
            ttl_seconds=self.auth_settings.AUTH_PASSWORD_RESET_TTL_SECONDS,
            url_template=self.auth_settings.AUTH_PASSWORD_RESET_URL_TEMPLATE,
            mail=self._maybe_send_password_reset_email,
        )
        if reset is None:
            return None

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
        revoke_sessions: bool = True,
    ) -> BaseUserModel:
        """Consume a reset token and replace the user's password.

        A reset is usually run because the old password is no longer
        trusted, so by default every refresh token the user holds is
        revoked in the same transaction (:meth:`revoke_user_sessions`) —
        a session opened with the password alone does not survive the
        rotation, exactly as in :meth:`mfa_confirm`. Access tokens
        already issued live until their short TTL ends. The bundled
        ``/auth/password-reset/confirm`` route keeps that default; the
        hash is written and the sessions are killed together, so a
        failure on either side rolls both back.

        Stateless refresh tokens (no ``refresh_token_model``) have no row
        to revoke and keep working until they expire.

        Args:
            session (AsyncSession): Active SQLAlchemy session.
            token (str): Plaintext token from the reset URL.
            new_password (str): Plaintext replacement password.
            revoke_sessions (bool): Revoke every refresh token of the user
                (default). ``False`` is the explicit opt-out, for a caller
                that ends the sessions some other way.

        Returns:
            BaseUserModel: The user whose password was rotated.

        Raises:
            ValidationException: When the new password breaks the policy.
            InvalidTokenException: On bad / expired / spent tokens, and
                when the token points to a user that no longer exists —
                for the caller that is a link that does not work, as in
                :meth:`activate` and :meth:`confirm_email_change`.
            ForbiddenException: ``exceptions.email_link_forbidden`` —
                ``403`` — when the ``refuses_email_link`` predicate
                matches the account. The token is spent and the spend is
                committed, so the link cannot be retried; the password
                hash is left untouched.
        """
        self._enforce_password_policy(new_password)
        record = await self._consume_token(
            session,
            token=token,
            purpose=UserTokenPurpose.PASSWORD_RESET,
        )
        user: BaseUserModel | None = await session.get(self.user_model, record.user_id)
        if user is None:
            raise self.exceptions.invalid_token(
                message="token references a missing user"
            )
        if self.email_link_refused(user):
            await self._refuse_email_link(session)
        user.hashed_password = self.passwords.hash(new_password)
        await session.flush()
        if revoke_sessions:
            await self.revoke_user_sessions(session, user_id=user.id)
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
        revoke_sessions: bool = True,
    ) -> BaseUserModel:
        """Replace an authenticated user's password after re-auth.

        The "change my own password while logged in" flow: the caller is
        already authenticated (the router resolves ``user`` from the
        bearer token), and must prove ownership by supplying their
        ``current_password`` before the new one is accepted. No token is
        involved — this is distinct from the email-driven reset flow
        (:meth:`request_password_reset` / :meth:`confirm_password_reset`).

        A password is changed because the old one may be known to someone
        else, so by default every session of the user is revoked in the
        same transaction (:meth:`revoke_user_sessions`), as in
        :meth:`confirm_password_reset`: a refresh token minted before the
        change no longer rotates. That includes the caller's own refresh
        token — the access token it holds lives until its short TTL ends,
        and the next session starts at login.

        Args:
            session (AsyncSession): Active SQLAlchemy session.
            user (BaseUserModel): The authenticated user (already loaded
                from the JWT subject).
            current_password (str): The user's current plaintext password,
                re-entered for confirmation.
            new_password (str): The plaintext replacement password.
            revoke_sessions (bool): Revoke every session of the user
                (default). ``False`` is the explicit opt-out, for a caller
                that ends the sessions some other way.

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
        if revoke_sessions:
            await self.revoke_user_sessions(session, user_id=user.id)
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
            IntegrationDisabledException: When the e-mail integration is
                switched off — see :meth:`require_mail_delivery`.
            UnauthorizedException: When ``current_password`` is wrong.
            ValidationException: When ``new_email`` equals the current one.
            ConflictException: When ``new_email`` is already in use.
            EmailDeliveryException: When the confirmation mail could not
                be sent (``503 EMAIL_UNAVAILABLE``). The token row is
                flushed but not committed, so the caller's rollback drops
                it and a pending link from an earlier request survives.
                The caller is authenticated, so there is nothing to
                enumerate.
        """
        self.require_mail_delivery()
        if not self.passwords.verify(current_password, user.hashed_password):
            raise UnauthorizedException(message="current password is incorrect")
        normalized = new_email.strip().lower()
        if normalized == user.email:
            raise ValidationException(
                message="new email is the same as the current one",
            )
        if await self._email_taken(session, normalized, exclude_user_id=user.id):
            raise self.exceptions.email_taken(
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
        revoke_sessions: bool = True,
    ) -> BaseUserModel:
        """Consume an ``EMAIL_CHANGE`` token and apply the pending address.

        Reads the pending new email from the token payload, re-checks it
        is still free (an address can be taken between request and
        confirm), flips ``user.email`` and — when
        ``AUTH_EMAIL_CHANGE_NOTIFY_OLD`` is on — sends a security notice
        to the previous address.

        The address is where the password reset goes, so moving it is a
        credential change: by default every session of the user is
        revoked in the same transaction (:meth:`revoke_user_sessions`), as
        in :meth:`confirm_password_reset`.

        Args:
            session (AsyncSession): Active SQLAlchemy session.
            token (str): Plaintext token from the confirmation link.
            revoke_sessions (bool): Revoke every session of the user
                (default). ``False`` is the explicit opt-out, for a caller
                that ends the sessions some other way.

        Returns:
            BaseUserModel: The user whose email was changed.

        Raises:
            InvalidTokenException: On bad / expired / spent tokens, a
                missing payload, or a missing user.
            ConflictException: When the target email was taken meanwhile.
            IntegrationDisabledException: When
                ``AUTH_EMAIL_CHANGE_NOTIFY_OLD`` is on and the e-mail
                integration is switched off — see
                :meth:`require_mail_delivery`. Raised before the token is
                consumed, so the link keeps working once e-mail is back.
            EmailDeliveryException: When ``AUTH_EMAIL_CHANGE_NOTIFY_OLD``
                is on and the notice to the old address could not be sent
                (``503 EMAIL_UNAVAILABLE``). The new address and the token
                spend are flushed but not committed: the caller's rollback
                undoes both, so the account keeps its old address and the
                same link can be confirmed again once the server is back.
                The change is never applied without the notice the
                setting promises.
        """
        if self.auth_settings.AUTH_EMAIL_CHANGE_NOTIFY_OLD:
            self.require_mail_delivery(carries_token=False)
        record = await self._consume_token(
            session,
            token=token,
            purpose=UserTokenPurpose.EMAIL_CHANGE,
        )
        new_email = (record.payload or "").strip().lower()
        if not new_email:
            raise self.exceptions.invalid_token(
                message="email-change token has no target address",
            )
        user: BaseUserModel | None = await session.get(self.user_model, record.user_id)
        if user is None:
            raise self.exceptions.invalid_token(
                message="token references a missing user"
            )
        if await self._email_taken(session, new_email, exclude_user_id=user.id):
            raise self.exceptions.email_taken(
                message="email already in use",
                details={"email": new_email},
            )
        old_email = user.email
        user.email = new_email
        await session.flush()
        if revoke_sessions:
            await self.revoke_user_sessions(session, user_id=user.id)
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

        Raises:
            IntegrationDisabledException: When the e-mail integration is
                switched off — see :meth:`require_mail_delivery`.
            EmailDeliveryException: When the mail could not be sent
                (``503 EMAIL_UNAVAILABLE``). The token row is flushed but
                not committed, so the caller's rollback drops it. The
                caller is authenticated, so there is nothing to enumerate.
        """
        self.require_mail_delivery()
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
            raise self.exceptions.invalid_token(
                message="token references a missing user"
            )
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
        login_throttle: AttemptThrottle | None = None,
        login_ip_throttle: AttemptThrottle | None = None,
        client_ip: str | None = None,
        mfa_throttle: AttemptThrottle | None = None,
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

        The password and the second factor checked here are the same ones
        ``/auth/login`` and ``/auth/mfa/verify`` check, so they spend the
        same budgets — otherwise this route would be the way around them.
        ``login_throttle`` is reserved on
        :func:`~tempest_fastapi_sdk.utils.throttle.login_throttle_key` of
        ``email`` before the password is checked, and ``mfa_throttle`` on
        ``f"mfa:{user_id}"`` before the code is checked; ``login_ip_throttle``
        reserves one slot before anything else, so concurrent guesses
        cannot outrun it, and gives it back once the password and code both
        matched, as on ``/auth/login``. A spent
        budget answers like any other soft failure — ``None``, the generic
        ``202`` — whatever the password or code, and never issues a link.
        A matching password clears the e-mail key and a matching code the
        MFA key, as on the login doors. The bundled router passes its own
        throttles; ``None`` (default) leaves that budget unchecked.

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
            login_throttle (AttemptThrottle | None): The per-e-mail login
                budget.
            login_ip_throttle (AttemptThrottle | None): The per-IP login
                budget, keyed ``f"login-ip:{client_ip}"``. Ignored without
                ``client_ip``.
            client_ip (str | None): The caller's address, for
                ``login_ip_throttle``.
            mfa_throttle (AttemptThrottle | None): The per-account
                second-factor budget.

        Returns:
            EmailChangeToken | None: The token bundle when the caller
            surfaces the link, ``None`` otherwise (including every soft
            identity-proof failure and every spent budget).

        Raises:
            ValidationException: When ``recovery_code_model`` is missing
                for an MFA-enrolled account, or ``new_email`` equals the
                current one.
            ConflictException: When ``new_email`` is already in use.
            IntegrationDisabledException: When the e-mail integration is
                switched off — see :meth:`require_mail_delivery`. Raised
                before the lookup, so it reveals nothing about the account.
            EmailDeliveryException: When the confirmation mail could not
                be sent (``503 EMAIL_UNAVAILABLE``). Unlike the soft
                failures this is raised, not turned into ``None``: the
                send happens only after the password (and the MFA code,
                when enrolled) matched, so the ``503`` is answered only
                to someone who already proved the account is theirs —
                the same point where a taken ``new_email`` answers
                ``409``. The token row is flushed but not committed, so
                the caller's rollback drops it.
        """
        self.require_mail_delivery()
        ip_throttle = login_ip_throttle if client_ip is not None else None
        ip_key = f"login-ip:{client_ip}"
        if ip_throttle is not None:
            try:
                await ip_throttle.reserve(ip_key)
            except TooManyRequestsException:
                return None
        email_key = login_throttle_key(email)
        if login_throttle is not None:
            attempt = await login_throttle.hit(email_key)
            if attempt.attempts > login_throttle.max_attempts:
                return None
        normalized_old = email.strip().lower()
        result = await session.execute(
            select(self.user_model).where(self.user_model.email == normalized_old),
        )
        user: BaseUserModel | None = result.scalar_one_or_none()
        if user is None or not self.passwords.verify(
            current_password, user.hashed_password
        ):
            return None
        if login_throttle is not None:
            await login_throttle.reset(email_key)
        if self.is_mfa_enrolled(user):
            if not mfa_code:
                return None
            if recovery_code_model is None:
                raise ValidationException(
                    message="recovery_code_model is required to verify MFA",
                )
            mfa_key = f"mfa:{user.id}"
            if mfa_throttle is not None:
                attempt = await mfa_throttle.hit(mfa_key)
                if attempt.attempts > mfa_throttle.max_attempts:
                    return None
            if not await self._verify_mfa_code(
                session, user, mfa_code, recovery_code_model
            ):
                return None
            if mfa_throttle is not None:
                await mfa_throttle.reset(mfa_key)
        if ip_throttle is not None:
            await ip_throttle.release(ip_key)
        normalized_new = new_email.strip().lower()
        if normalized_new == user.email:
            raise ValidationException(
                message="new email is the same as the current one",
            )
        if await self._email_taken(session, normalized_new, exclude_user_id=user.id):
            raise self.exceptions.email_taken(
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

    def issue_jwt_pair(
        self,
        user: BaseUserModel,
        *,
        amr: Sequence[str] | None = PASSWORD_AMR,
    ) -> tuple[str, str]:
        """Return a stateless ``(access, refresh)`` JWT pair.

        Both tokens are signed JWTs with no DB persistence — this is
        the **legacy** issuance path, kept for back-compat and used
        whenever ``refresh_token_model`` is not wired. When a
        refresh-token model *is* wired, prefer :meth:`issue_token_pair`,
        which mints an opaque DB-backed refresh token instead.

        Args:
            user (BaseUserModel): The authenticated user.
            amr (Sequence[str] | None): RFC 8176 methods the session was
                authenticated with, stamped as ``amr`` on both tokens so a
                refresh repeats them. Defaults to
                :data:`~tempest_fastapi_sdk.PASSWORD_AMR`. ``None`` omits
                the claim (an origin nobody recorded).

        Returns:
            tuple[str, str]: ``(access_token, refresh_token)`` — both
            stateless JWTs.
        """
        access = self._encode_access(user, amr=amr)
        refresh = self._encode_refresh(user, amr=amr)
        return access, refresh

    def _encode_access(
        self,
        user: BaseUserModel,
        *,
        amr: Sequence[str] | None = PASSWORD_AMR,
    ) -> str:
        """Sign a short-lived access JWT carrying ``sub`` + ``email``.

        The ``typ`` claim is what stops the other tokens minted with this
        same secret — the refresh token and the MFA-pending token — from
        being replayed as an access token against a route guard that only
        reads ``sub``. See
        :mod:`tempest_fastapi_sdk.utils.token_types`.

        The ``amr`` claim (RFC 8176) says how the session was authenticated,
        which is what :func:`~tempest_fastapi_sdk.make_mfa_required_dependency`
        reads to tell a password-only session from one that passed MFA.

        Args:
            user (BaseUserModel): The authenticated user.
            amr (Sequence[str] | None): The methods to stamp; ``None`` omits
                the claim.

        Returns:
            str: The signed access token.
        """
        claims: dict[str, Any] = {
            "sub": str(user.id),
            "email": user.email,
            "typ": ACCESS_TOKEN_TYPE,
        }
        if amr is not None:
            claims[AMR_CLAIM] = list(amr)
        return self.jwt.encode(
            claims,
            ttl=timedelta(seconds=self.jwt_settings.JWT_ACCESS_TTL_SECONDS),
        )

    def _encode_refresh(
        self,
        user: BaseUserModel,
        *,
        amr: Sequence[str] | None = PASSWORD_AMR,
    ) -> str:
        """Sign a long-lived stateless refresh JWT.

        Carries both ``typ`` and the historical ``refresh: True`` marker:
        the former is what the request guards check, the latter keeps a
        token issued by this version exchangeable at ``/auth/refresh`` by a
        service still running an older SDK during a rolling deploy. The
        ``amr`` it carries is what :meth:`refresh_tokens` copies onto the
        next pair, so a refresh never promotes the session.

        Args:
            user (BaseUserModel): The authenticated user.
            amr (Sequence[str] | None): The methods to stamp; ``None`` omits
                the claim.

        Returns:
            str: The signed refresh token.
        """
        claims: dict[str, Any] = {
            "sub": str(user.id),
            "refresh": True,
            "typ": REFRESH_TOKEN_TYPE,
        }
        if amr is not None:
            claims[AMR_CLAIM] = list(amr)
        return self.jwt.encode(
            claims,
            ttl=timedelta(seconds=self.jwt_settings.JWT_REFRESH_TTL_SECONDS),
        )

    async def issue_token_pair(
        self,
        session: AsyncSession,
        user: BaseUserModel,
        *,
        family_id: UUID | None = None,
        amr: Sequence[str] | None = PASSWORD_AMR,
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
            amr (Sequence[str] | None): RFC 8176 methods the session was
                authenticated with. Stamped as ``amr`` on the access token
                and stored with the refresh token, so every rotation repeats
                it. Defaults to :data:`~tempest_fastapi_sdk.PASSWORD_AMR`;
                the bundled router passes
                :data:`~tempest_fastapi_sdk.MFA_AMR` after the second
                factor. ``None`` omits the claim.

        Returns:
            tuple[str, str]: ``(access_token, refresh_token)`` — the
            ``refresh_token`` is plaintext (opaque) or a JWT depending
            on configuration. The plaintext opaque token is surfaced
            exactly once; only its hash is persisted.
        """
        access = self._encode_access(user, amr=amr)
        if self.refresh_token_model is None:
            return access, self._encode_refresh(user, amr=amr)
        refresh = await self._issue_refresh_record(
            session, user_id=user.id, family_id=family_id, amr=amr
        )
        return access, refresh

    async def _issue_pair_with_amr(
        self,
        session: AsyncSession,
        user: BaseUserModel,
        *,
        family_id: UUID | None,
        amr: Sequence[str] | None,
    ) -> tuple[str, str]:
        """Call :meth:`issue_token_pair` with ``amr``, if the override takes it.

        A subclass that overrides ``issue_token_pair`` with the signature it
        had before ``amr`` existed would fail with ``TypeError`` on every
        refresh and OAuth login. Calling it without the keyword falls back to
        the override's own default — :data:`PASSWORD_AMR` when it delegates
        to ``super()`` —, which never claims a second factor the session did
        not prove.

        Args:
            session (AsyncSession): Active SQLAlchemy session.
            user (BaseUserModel): The authenticated user.
            family_id (UUID | None): Rotation lineage to attach to.
            amr (Sequence[str] | None): The methods to stamp.

        Returns:
            tuple[str, str]: ``(access_token, refresh_token)``.
        """
        if accepts_keyword(self.issue_token_pair, "amr"):
            return await self.issue_token_pair(
                session, user, family_id=family_id, amr=amr
            )
        return await self.issue_token_pair(session, user, family_id=family_id)

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
          family. The database decides the rotation in one conditional
          ``UPDATE`` (:meth:`_spend_refresh_record`), so two concurrent
          redemptions of one token yield one new pair and one ``401``
          that revokes the family — never two live descendants.
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
            require_active(user, exception=self.inactive_account_error)
            if accepts_keyword(self.issue_jwt_pair, "amr"):
                access, refresh = self.issue_jwt_pair(user, amr=token_amr(claims))
            else:
                access, refresh = self.issue_jwt_pair(user)
            return user, access, refresh

        record = await self._spend_refresh_record(session, refresh_token)
        family_id = record.family_id
        family_amr = tuple(record.amr.split()) if record.amr else None
        user = await self.get_user(record.user_id, session)
        require_active(user, exception=self.inactive_account_error)
        access, refresh = await self._issue_pair_with_amr(
            session, user, family_id=family_id, amr=family_amr
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
            await revoke_user_refresh_tokens(
                session,
                model,
                user_id=record.user_id,
            )
        else:
            await self._revoke_family(session, record.family_id)

    async def revoke_user_sessions(
        self,
        session: AsyncSession,
        *,
        user_id: UUID,
    ) -> int:
        """Revoke every active session of a user, by id.

        The administrative counterpart of :meth:`revoke_refresh_token`:
        logout-everywhere for a user whose token nobody holds — the
        account an operator just reset the password of, or one being
        locked after a report. Wraps
        :func:`revoke_user_refresh_tokens` with the service's configured
        table so the caller needs no model reference of its own, then
        runs every revoker in :attr:`session_revokers` — the cookie
        sessions :func:`~tempest_fastapi_sdk.auth.make_auth_pages_router`
        opens are registered there (:meth:`add_session_revoker`), so a
        password reset, password change or e-mail change ends them too.

        Without ``refresh_token_model`` no refresh row is revoked, since
        stateless JWT refresh tokens carry no server-side row to flip;
        those keep working until they expire.

        The revokers act on their own store as they run, outside the
        database transaction: a rollback afterwards does not bring a
        revoked cookie session back.

        Args:
            session (AsyncSession): Active SQLAlchemy session. The
                update is flushed (the caller owns the commit).
            user_id (UUID): Owner whose sessions are being killed.

        Returns:
            int: How many refresh-token rows were revoked, plus what each
            revoker reported.
        """
        model = self.refresh_token_model
        revoked = (
            0
            if model is None
            else await revoke_user_refresh_tokens(session, model, user_id=user_id)
        )
        for revoker in self.session_revokers:
            revoked += await revoker(user_id)
        return revoked

    def add_session_revoker(self, revoker: Callable[[UUID], Awaitable[int]]) -> None:
        """Register a store of sessions that :meth:`revoke_user_sessions` ends.

        A session that is not a refresh-token row — the opaque cookie of
        :class:`~tempest_fastapi_sdk.sessions.SessionAuth` — is invisible to
        the refresh table, so a password reset would leave it alive.
        :func:`~tempest_fastapi_sdk.auth.make_auth_pages_router` registers
        its ``session_auth.revoke_all`` here; register your own when you
        open sessions over this service some other way. Registering the
        same callable twice keeps one entry.

        Args:
            revoker (Callable[[UUID], Awaitable[int]]): Called with the
                user id; ends every session of that user and returns how
                many it ended.
        """
        if revoker not in self.session_revokers:
            self.session_revokers.append(revoker)

    async def _issue_refresh_record(
        self,
        session: AsyncSession,
        *,
        user_id: UUID,
        family_id: UUID | None,
        amr: Sequence[str] | None = PASSWORD_AMR,
    ) -> str:
        """Persist a fresh opaque refresh-token row, return the plaintext.

        Args:
            session (AsyncSession): Active SQLAlchemy session.
            user_id (UUID): Owner of the token.
            family_id (UUID | None): Rotation lineage to attach to;
                ``None`` starts a new family.
            amr (Sequence[str] | None): Methods the family was opened with,
                stored space-separated so a rotation repeats them. ``None``
                stores ``NULL``.

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
            amr=" ".join(amr) if amr else None,
        )
        session.add(record)
        await session.flush()
        return plain

    async def _spend_refresh_record(
        self,
        session: AsyncSession,
        token: str,
    ) -> BaseUserRefreshTokenModel:
        """Mark a refresh token used in one conditional statement.

        The rotation is one ``UPDATE ... SET used_at = now WHERE
        token_hash = :h AND used_at IS NULL AND revoked_at IS NULL AND
        expires_at > now RETURNING *``, as in :meth:`_consume_token`: two
        concurrent redemptions of the same token both issue it, and only
        one finds ``used_at`` still empty. Reading the row first and
        writing ``used_at`` afterwards — the shape this replaces — let
        both pass the read and each mint a child in the family, with no
        reuse detected.

        When no row comes back, :meth:`_lookup_refresh_record` names the
        reason and, for a token already rotated, revokes the family. A
        row it still reads as live means a concurrent redemption is in
        flight and has not committed: that is a replay too, and the family
        is revoked the same way.

        Args:
            session (AsyncSession): Active SQLAlchemy session. The
                statement is executed but not committed.
            token (str): Plaintext opaque token.

        Returns:
            BaseUserRefreshTokenModel: The token row, with ``used_at`` set.

        Raises:
            InvalidTokenException: When the token is unknown, revoked,
                expired or already rotated — including when a concurrent
                redemption spent it first.
        """
        model = self.refresh_token_model
        assert model is not None
        now = utcnow()
        result = await session.execute(
            update(model)
            .where(
                model.token_hash == hash_opaque_token(token),
                model.used_at.is_(None),
                model.revoked_at.is_(None),
                model.expires_at > now,
            )
            .values(used_at=now)
            .returning(model)
        )
        record: BaseUserRefreshTokenModel | None = result.scalar_one_or_none()
        if record is not None:
            return record
        live = await self._lookup_refresh_record(session, token)
        await self._revoke_reused_family(session, live)
        raise InvalidTokenException(message="refresh token reuse detected")

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
        require_active: bool = True,
        inactive_exception: GuardException | None = None,
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
            require_active (bool): Refuse a token whose account is no
                longer active (default ``True``). ``login`` already
                refuses an inactive account, but a token issued before the
                deactivation stays valid until it expires — up to the
                whole access TTL — so without this check every route had
                to remember :func:`~tempest_fastapi_sdk.require_active`.
                ``False`` restores the pre-0.305.0 behavior, for the one
                route that must serve an inactive account (reactivation).
            inactive_exception (GuardException | None): Factory for the
                refusal of an inactive account. ``None`` (default)
                raises :meth:`inactive_account_error` — ``403``
                ``ACCOUNT_INACTIVE``, or the product's
                ``exceptions.account_inactive``. With
                ``soft=True`` an inactive account yields ``None``
                instead, as an invalid token does.

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

        resolved_cookie_name = (
            cookie_name if cookie_name is not None else self._access_cookie_name()
        )

        async def load_active_user(
            subject: str | UUID,
            session: AsyncSession,
        ) -> BaseUserModel | None:
            """Load the token's user and refuse it when inactive.

            Args:
                subject (str | UUID): The token's ``sub`` claim.
                session (AsyncSession): The request-scoped session.

            Returns:
                BaseUserModel | None: The active user, or ``None`` for an
                inactive one under ``soft=True``.

            Raises:
                AppException: ``inactive_exception()`` (``403`` by
                    default) for an inactive account when ``soft`` is
                    ``False``.
            """
            user = await self.get_user(subject, session)
            if user.is_active:
                return user
            if soft:
                return None
            return auth_guards.require_active(
                user,
                exception=inactive_exception or self.inactive_account_error,
            )

        return make_jwt_user_dependency(
            self.jwt,
            load_active_user if require_active else self.get_user,
            soft=soft,
            cookie_name=resolved_cookie_name,
            query_param=query_param,
            strict=strict,
            legacy_claims=legacy_claims,
            session_dependency=session_dependency or self.db.session_dependency,
        )

    def _access_cookie_name(self) -> str | None:
        """Return the access cookie a login set, under a cookie delivery.

        Returns:
            str | None: ``AUTH_ACCESS_COOKIE_NAME`` when
            ``AUTH_TOKEN_DELIVERY`` is ``"cookie"`` or ``"both"``, else
            ``None`` (bearer only).
        """
        if self.auth_settings.AUTH_TOKEN_DELIVERY in ("cookie", "both"):
            return self.auth_settings.AUTH_ACCESS_COOKIE_NAME
        return None

    def sse_router(
        self,
        broker: SSEBroker | Callable[[Request], SSEBroker],
        *,
        session_dependency: Callable[..., Any] | None = None,
        strict: bool = False,
        legacy_claims: Collection[str] = (),
        allow_access_token_query: bool = False,
        access_token_param: str = "access_token",
        channel_for: Callable[[Any], str] | None = None,
        prefix: str = "/sse",
        tag: str = "SSE",
        ticket_ttl: timedelta | None = None,
        ticket_param: str = "ticket",
    ) -> APIRouter:
        """Build the authenticated SSE router for this service's users.

        Wires :func:`~tempest_fastapi_sdk.make_sse_router` to the service:
        ``POST <prefix>/ticket`` is guarded by
        :meth:`current_user_dependency` (header or the login cookie, active
        account), the ticket is signed with this service's ``jwt``, and
        ``GET <prefix>/stream`` also accepts ``Authorization: Bearer`` for a
        client that can send it.

        The browser opens the stream in two steps::

            const { ticket } = await api.post("/sse/ticket");
            const events = new EventSource(`/sse/stream?ticket=${ticket}`);

        ``allow_access_token_query=True`` additionally accepts the access
        token itself as ``?access_token=`` on the stream — the shape a
        client written against ``current_user_dependency(query_param=...)``
        already sends. It exists so that client keeps working while it
        moves to the ticket; the access token in the URL authorizes every
        route until it expires, so turn it off once nothing sends it.

        Args:
            broker (SSEBroker | Callable[[Request], SSEBroker]): The
                broker, or a factory called with each request.
            session_dependency (Callable[..., Any] | None): The
                request-scoped session provider shared with repositories;
                see :meth:`current_user_dependency`.
            strict (bool): Refuse access tokens without a type marker; see
                :meth:`current_user_dependency`.
            legacy_claims (Collection[str]): Extra claims naming a legacy
                token's type; see :meth:`current_user_dependency`.
            allow_access_token_query (bool): Also open the stream with the
                access token in ``?<access_token_param>=``. ``False``
                (default) refuses it.
            access_token_param (str): Query parameter read when
                ``allow_access_token_query`` is set. Defaults to
                ``"access_token"``.
            channel_for (Callable[[Any], str] | None): Maps the user to its
                broker channel. ``None`` (default) uses ``str(user.id)``.
            prefix (str): URL prefix. Defaults to ``"/sse"``.
            tag (str): OpenAPI tag. Defaults to ``"SSE"``.
            ticket_ttl (timedelta | None): Ticket lifetime. ``None``
                (default) uses
                :data:`~tempest_fastapi_sdk.DEFAULT_SSE_TICKET_TTL`.
            ticket_param (str): Query parameter of the ticket. Defaults to
                ``"ticket"``.

        Returns:
            APIRouter: The router, ready for ``app.include_router``.

        Raises:
            RuntimeError: When the service was created without ``db=``.
            ValueError: When ``allow_access_token_query`` is set and
                ``access_token_param`` equals ``ticket_param``, or
                ``ticket_ttl`` is shorter than one second.
        """
        from tempest_fastapi_sdk.api.dependencies.auth import (
            make_jwt_user_dependency,
        )
        from tempest_fastapi_sdk.sse.router import make_sse_router

        db = self.db
        if db is None:
            raise RuntimeError(
                "UserAuthService was created without `db=`; pass an "
                "AsyncDatabaseManager to use sse_router."
            )
        if allow_access_token_query and access_token_param == ticket_param:
            raise ValueError(
                "access_token_param and ticket_param must differ: the stream "
                "reads the ticket first, so the access token would never be "
                "tried."
            )
        current_user = self.current_user_dependency(
            session_dependency=session_dependency,
            strict=strict,
            legacy_claims=legacy_claims,
        )

        async def load_streaming_user(subject: str | UUID) -> BaseUserModel | None:
            """Load the active user in a session that closes before the stream.

            A request-scoped session lives until the response ends, and an
            SSE response ends when the client leaves — so sharing it would
            hold a database connection for as long as each stream stays
            open. The user is read here, the session closes, and the
            stream runs on the detached row (``expire_on_commit=False``).

            Args:
                subject (str | UUID): The token's ``sub`` claim.

            A subject that resolves to no user — an account deleted after
            the token was issued — is ``None`` too, so the stream answers
            the same ``401`` as any unusable credential instead of a
            ``404`` that tells a token holder which ids exist.

            Returns:
                BaseUserModel | None: The user, or ``None`` when inactive or
                gone.
            """
            try:
                async with db.get_session_context() as session:
                    user = await self.get_user(subject, session)
            except NotFoundException:
                return None
            return user if user.is_active else None

        fallback_user = make_jwt_user_dependency(
            self.jwt,
            load_streaming_user,
            soft=True,
            cookie_name=self._access_cookie_name(),
            query_param=access_token_param if allow_access_token_query else None,
            strict=strict,
            legacy_claims=legacy_claims,
        )
        return make_sse_router(
            broker,
            tokens=self.jwt,
            current_user=current_user,
            fallback_user=fallback_user,
            channel_for=channel_for,
            prefix=prefix,
            tag=tag,
            ticket_ttl=ticket_ttl,
            ticket_param=ticket_param,
        )

    def inactive_account_error(self) -> ForbiddenException:
        """Build the refusal for an account that is no longer active.

        One factory for every place the service refuses an inactive
        account it has already identified — ``login`` under
        ``AUTH_REVEAL_INACTIVE_ACCOUNT``, :meth:`refresh_tokens`,
        :meth:`current_user_dependency` and the authenticated routes of
        ``make_auth_router`` — so the
        ``code`` a client sees does not depend on which of them caught it.

        Returns:
            ForbiddenException: ``exceptions.account_inactive`` with the
            ``ACCOUNT_INACTIVE`` message key (``403`` by default).
        """
        return self.exceptions.account_inactive(
            message="account is not active",
            message_key="ACCOUNT_INACTIVE",
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

        The exception class comes from :attr:`exceptions` — one field
        per violation — and its ``message_key`` is the violation's
        :class:`~tempest_fastapi_sdk.PasswordViolationCode`, so a message
        catalog renders "too short" and "too long" differently while the
        ``details`` stay structured.

        Raises:
            ValidationException: When the password is too short, too
                long for the hasher, or — under complexity mode —
                missing a required character class.
        """
        violation = check_password_policy(
            password,
            PasswordPolicy.from_settings(self.auth_settings),
        )
        if violation is None:
            return
        exception = {
            PasswordViolationCode.PASSWORD_TOO_SHORT: (
                self.exceptions.password_too_short
            ),
            PasswordViolationCode.PASSWORD_TOO_LONG: self.exceptions.password_too_long,
            PasswordViolationCode.PASSWORD_TOO_WEAK: self.exceptions.password_too_weak,
        }[violation.code]
        raise exception(
            message=violation.message,
            details=violation.details,
            message_key=violation.code.value,
            message_params=violation.details,
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
        plain, digest, url, expires_at = self._mint_token(
            ttl_seconds=ttl_seconds,
            url_template=url_template,
        )
        await self._store_token(
            session,
            user_id=user_id,
            purpose=purpose,
            digest=digest,
            expires_at=expires_at,
            payload=payload,
        )
        return plain, url, expires_at

    def _mint_token(
        self,
        *,
        ttl_seconds: int,
        url_template: str,
    ) -> tuple[str, str, str, datetime]:
        """Generate a token in memory, without touching the database.

        Args:
            ttl_seconds (int): Token lifetime in seconds.
            url_template (str): URL template with a ``{token}`` slot.

        Returns:
            tuple[str, str, str, datetime]: The plaintext token, its
            digest (what :meth:`_store_token` persists), the rendered URL
            and the expiry.
        """
        plain, digest = generate_opaque_token(48)
        expires_at = utcnow() + timedelta(seconds=ttl_seconds)
        return plain, digest, url_template.replace("{token}", plain), expires_at

    async def _store_token(
        self,
        session: AsyncSession,
        *,
        user_id: UUID,
        purpose: UserTokenPurpose,
        digest: str,
        expires_at: datetime,
        payload: str | None = None,
    ) -> None:
        """Persist a minted token, superseding the pending ones first.

        Args:
            session (AsyncSession): Active SQLAlchemy session. Flushed,
                not committed.
            user_id (UUID): The user the token authorizes.
            purpose (UserTokenPurpose): What the token authorizes.
            digest (str): The digest :meth:`_mint_token` returned.
            expires_at (datetime): The expiry :meth:`_mint_token` returned.
            payload (str | None): Optional flow context stored on the row.
        """
        if self.auth_settings.AUTH_SINGLE_ACTIVE_TOKEN:
            await self._burn_pending_tokens(session, user_id=user_id, purpose=purpose)
        record = self.token_model(
            user_id=user_id,
            token_hash=digest,
            purpose=purpose.value,
            expires_at=expires_at,
            payload=payload,
        )
        session.add(record)
        await session.flush()

    async def _issue_token_after_mail(
        self,
        session: AsyncSession,
        *,
        user: BaseUserModel,
        purpose: UserTokenPurpose,
        ttl_seconds: int,
        url_template: str,
        mail: Callable[
            [BaseUserModel, tuple[str, str, datetime]], Coroutine[Any, Any, None]
        ],
    ) -> tuple[str, str, datetime] | None:
        """Mail a link first and persist its token only once it went out.

        The issuing path of the flows that answer the same for a known and
        an unknown address (:meth:`request_activation`,
        :meth:`request_password_reset`). There a failed send must not
        change the response — ``503`` for a real account against ``202``
        for a stranger would tell which addresses are registered — so the
        :class:`~tempest_fastapi_sdk.exceptions.EmailDeliveryException` is
        caught, logged at ``ERROR`` with the flow only (never the address)
        and turned into ``None``, the same value an unknown address
        produces.

        The order is what keeps that failure free of writes: the token is
        generated in memory, mailed, and only then stored — superseding
        the pending ones. A send that fails therefore leaves the database
        as it was, the previous link included, with no savepoint and no
        rollback of the caller's transaction. A store that fails after the
        mail went out leaves a link that answers ``invalid token``, the
        same outcome as a commit that fails after the send in the other
        flows.

        Args:
            session (AsyncSession): Active SQLAlchemy session.
            user (BaseUserModel): The account the link is for.
            purpose (UserTokenPurpose): What the token authorizes.
            ttl_seconds (int): Token lifetime in seconds.
            url_template (str): URL template with a ``{token}`` slot.
            mail (Callable[[BaseUserModel, tuple[str, str, datetime]],
                Coroutine[Any, Any, None]]): The ``_maybe_send_*`` method
                that renders and sends the link.

        Returns:
            tuple[str, str, datetime] | None: ``(plain, url, expires_at)``
            of the stored token, or ``None`` when the mail could not go
            out and nothing was stored.
        """
        plain, digest, url, expires_at = self._mint_token(
            ttl_seconds=ttl_seconds,
            url_template=url_template,
        )
        try:
            await mail(user, (plain, url, expires_at))
        except EmailDeliveryException:
            _LOGGER.error(
                "Auth %s link could not be mailed; answered like an unknown "
                "address and stored no token",
                purpose.value,
                extra={"auth_flow": purpose.value},
            )
            return None
        await self._store_token(
            session,
            user_id=user.id,
            purpose=purpose,
            digest=digest,
            expires_at=expires_at,
        )
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

    def email_link_refused(self, user: BaseUserModel) -> bool:
        """Return whether the emailed link flows are closed for ``user``.

        The gate behind ``refuses_email_link``: :meth:`signup` (when it
        would mint the first activation link) and every request and
        confirm method that would mint or spend an activation or
        password-reset link ask here first, and so does the backend-rendered reset form
        (``AUTH_BACKEND_LINKS=True``), which only peeks the token and
        would otherwise show a form whose submit is bound to fail. The
        argument is the service's own ``user_model`` instance, which is
        what the configured predicate was written against.

        Args:
            user (BaseUserModel): The account the link points at.

        Returns:
            bool: ``True`` when the account refuses emailed links. Always
            ``False`` under the default ``refuses_email_link=None``.
        """
        predicate = self.refuses_email_link
        if predicate is None:
            return False
        return predicate(user)

    async def _refuse_email_link(
        self,
        session: AsyncSession,
    ) -> NoReturn:
        """Persist the spent token, then raise the typed ``403``.

        Called only after :meth:`_consume_token` staged the ``used_at``
        write in the caller's session. The raise travels out through the
        request's session dependency, whose teardown rolls the unit of
        work back — without the commit the token would come back to life
        and a leaked link would stay redeemable. Same reasoning as
        :meth:`_revoke_on_replay`: a flush is not enough once the caller
        raises. The consumed token is the only write these methods stage
        before the refusal, so nothing else rides along.

        The class comes from ``self.exceptions.email_link_forbidden`` so a
        product keeps the ``code`` its clients already handle, and the
        ``message_key`` pins the SDK's translation even when the class is
        swapped.

        Args:
            session (AsyncSession): Active session holding the staged
                token consumption.

        Raises:
            ForbiddenException: ``exceptions.email_link_forbidden`` with
                the ``EMAIL_LINK_FORBIDDEN`` message key (``403`` by
                default).
        """
        await session.commit()
        raise self.exceptions.email_link_forbidden(message_key="EMAIL_LINK_FORBIDDEN")

    async def _consume_token(
        self,
        session: AsyncSession,
        *,
        token: str,
        purpose: UserTokenPurpose,
    ) -> BaseUserTokenModel:
        """Spend a single-use token in one conditional statement.

        The redemption is one ``UPDATE ... SET used_at = now WHERE
        token_hash = :h AND purpose = :p AND used_at IS NULL AND
        expires_at > now RETURNING *``. The database decides who wins:
        two concurrent redemptions of the same link both issue it, and
        only one finds ``used_at`` still empty. Reading the row first and
        writing ``used_at`` afterwards — the shape this replaces — let
        both requests see ``used_at IS NULL`` before either wrote it, so
        a password reset applied twice and an email change confirmed
        twice.

        When no row comes back, :meth:`_lookup_token` runs to name the
        reason (not recognized, already used, expired). That read only
        picks the error message; it never decides the redemption.

        ``RETURNING`` needs PostgreSQL, or SQLite 3.35 or newer.

        Args:
            session (AsyncSession): Active SQLAlchemy session. The
                statement is executed but not committed.
            token (str): Plaintext token.
            purpose (UserTokenPurpose): Expected token purpose.

        Returns:
            BaseUserTokenModel: The token record, with ``used_at`` set.

        Raises:
            InvalidTokenException: When the token is unknown, already
                used, or expired — including when a concurrent
                redemption spent it first.
        """
        now = utcnow()
        result = await session.execute(
            update(self.token_model)
            .where(
                self.token_model.token_hash == hash_opaque_token(token),
                self.token_model.purpose == purpose.value,
                self.token_model.used_at.is_(None),
                self.token_model.expires_at > now,
            )
            .values(used_at=now)
            .returning(self.token_model)
        )
        record: BaseUserTokenModel | None = result.scalar_one_or_none()
        if record is not None:
            return record
        await self._lookup_token(session, token=token, purpose=purpose)
        raise self.exceptions.invalid_token(message="token is no longer valid")

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
        """Find a token record and run the validity checks, read-only.

        Args:
            session (AsyncSession): Active SQLAlchemy session.
            token (str): Plaintext token.
            purpose (UserTokenPurpose): Expected token purpose.

        Returns:
            BaseUserTokenModel: The valid, unused token record.

        Raises:
            InvalidTokenException: When the token is unknown, already
                used, or expired.
        """
        digest = hash_opaque_token(token)
        result = await session.execute(
            select(self.token_model).where(
                self.token_model.token_hash == digest,
                self.token_model.purpose == purpose.value,
            )
        )
        record: BaseUserTokenModel | None = result.scalar_one_or_none()
        if record is None:
            raise self.exceptions.invalid_token(message="token not recognized")
        if record.used_at is not None:
            raise self.exceptions.invalid_token(message="token already used")
        now = utcnow().replace(tzinfo=None)
        expires_at = (
            record.expires_at.replace(tzinfo=None)
            if record.expires_at.tzinfo is not None
            else record.expires_at
        )
        if expires_at < now:
            raise self.exceptions.invalid_token(message="token expired")
        return record

    def require_mail_delivery(self, *, carries_token: bool = True) -> None:
        """Refuse an operation whose e-mail could never go out.

        The flows that hand a link by e-mail (signup without
        ``AUTH_AUTO_ACTIVATE``, activation re-send, password reset, e-mail
        change and verification, e-mail recovery) call this **before**
        reading or writing anything. With the e-mail integration switched
        off (an :class:`EmailUtils` built with ``enabled=False``, which is
        what ``EmailUtils(**settings.email_kwargs())`` gives under
        ``EMAIL_ENABLED=false``) the request is answered ``503
        INTEGRATION_DISABLED`` and nothing is created: an account whose
        activation link can never arrive is never inserted, and a reset
        request never mints a token nobody receives.

        Nothing is refused when no mail would be sent anyway — no
        ``EmailUtils`` wired, or ``AUTH_RETURN_TOKEN_IN_RESPONSE`` on for
        a flow whose link travels in the response. A mailer without an
        ``enabled`` attribute (a test double written before the flag
        existed) counts as enabled.

        The check runs before the account lookup, so a refused reset
        answers the same for a known and an unknown address.

        Under ``ENV=production`` (read from ``auth_settings`` when it
        composes :class:`~tempest_fastapi_sdk.settings.EnvironmentSettings`)
        a flow carrying a link is refused the same way whenever the link
        would travel in the response body instead of the mail — no
        ``EmailUtils`` wired, or ``AUTH_RETURN_TOKEN_IN_RESPONSE`` on. Those
        are development conveniences: in production they hand a reset or
        activation link to whoever typed the address. The production guard
        refuses the flag at boot; this covers the missing mailer, which is
        a constructor argument no settings guard can see.

        Args:
            carries_token (bool): ``True`` (default) for a mail carrying a
                link, which ``AUTH_RETURN_TOKEN_IN_RESPONSE`` replaces by
                the response body. ``False`` for the security notice to
                the old address after an e-mail change, which that flag
                does not cover.

        Raises:
            IntegrationDisabledException: When the wired mailer is
                disabled and the operation would need it, or under
                ``ENV=production`` when the link would be surfaced in
                the response.
        """
        if carries_token and self._links_surface_in_production():
            raise IntegrationDisabledException("email")
        if self.email is None or getattr(self.email, "enabled", True):
            return
        if carries_token and self.auth_settings.AUTH_RETURN_TOKEN_IN_RESPONSE:
            return
        raise IntegrationDisabledException("email")

    def _links_surface_in_production(self) -> bool:
        """Tell whether a link would reach the response body in production.

        Returns:
            bool: ``True`` when ``auth_settings`` reports ``ENV=production``
            and the link would be surfaced instead of mailed — no
            ``EmailUtils`` wired, or ``AUTH_RETURN_TOKEN_IN_RESPONSE`` on.
            ``auth_settings`` without an ``ENV`` field counts as not
            production.
        """
        if getattr(self.auth_settings, "ENV", None) != "production":
            return False
        return self.email is None or self.auth_settings.AUTH_RETURN_TOKEN_IN_RESPONSE

    async def _send_email(
        self,
        kind: str,
        to: str,
        *,
        subject: str,
        body: str,
        html: str,
    ) -> None:
        """Send one auth email now, or after the response when opted in.

        With ``AUTH_SEND_EMAILS_IN_BACKGROUND`` on and a request served by
        :func:`~tempest_fastapi_sdk.auth.make_auth_router` in progress, the
        send goes to the request's ``BackgroundTasks``
        (:mod:`tempest_fastapi_sdk.auth.email_delivery`): the response does
        not wait for SMTP, and a failure is logged instead of answering
        ``503 EMAIL_UNAVAILABLE``. Otherwise — the default, or a call from
        outside a request — the send is awaited and a failure raises, as
        before (#644).

        Args:
            kind (str): Which email, for the failure log.
            to (str): The recipient.
            subject (str): The subject line.
            body (str): The plain-text body.
            html (str): The rendered HTML body.

        Raises:
            EmailDeliveryException: When sent inline and the server
                refuses the message (``503 EMAIL_UNAVAILABLE``).
        """
        from tempest_fastapi_sdk.auth.email_delivery import (
            current_background_tasks,
            send_and_log,
        )

        if self.email is None:
            return
        email = self.email
        background = (
            current_background_tasks()
            if self.auth_settings.AUTH_SEND_EMAILS_IN_BACKGROUND
            else None
        )
        if background is None:
            await email.send(to, subject=subject, body=body, html=html)
            return

        async def deliver() -> None:
            await email.send(to, subject=subject, body=body, html=html)

        background.add_task(send_and_log, deliver, recipient=to, kind=kind)

    def _render_email(
        self,
        template_name: str,
        context: dict[str, Any],
        *,
        locale: str,
    ) -> str:
        """Render an auth email with the brand settings in its context.

        The bundled templates extend ``_email_layout.html``, which reads
        ``brand``: the header name or logo, the button color, the text
        color that reads on it and a link color that reads on the white
        card (:func:`~tempest_fastapi_sdk.utils.color.is_light` picks both:
        a pale brand color would vanish as link text), and the footer line,
        all from the ``AUTH_EMAIL_*`` settings. A
        project template that shadows one of them gets the same ``brand``
        and can ignore it.

        Args:
            template_name (str): The template file.
            context (dict[str, Any]): The email's own variables.
            locale (str): The language to render in.

        Returns:
            str: The HTML body.
        """
        from tempest_fastapi_sdk.utils.color import is_light

        assert self.email is not None
        settings = self.auth_settings
        color = settings.AUTH_EMAIL_BRAND_COLOR
        light = is_light(color)
        brand = {
            "name": settings.AUTH_EMAIL_BRAND_NAME,
            "color": color,
            "text_color": "#0f172a" if light else "#ffffff",
            "link_color": "#1f2937" if light else color,
            "logo_url": settings.AUTH_EMAIL_BRAND_LOGO_URL,
            "footer": settings.AUTH_EMAIL_FOOTER_TEXT,
        }
        rendered: str = self.email.render_template(
            template_name,
            {**context, "brand": brand},
            locale=locale,
        )
        return rendered

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
        html = self._render_email(
            self.auth_settings.AUTH_ACTIVATION_TEMPLATE,
            {
                "user": user,
                "activation_url": url,
                "expires_at": expires_at,
                "expires_at_str": format_expires_at(expires_at, locale),
            },
            locale=locale,
        )
        await self._send_email(
            "activation",
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
        html = self._render_email(
            self.auth_settings.AUTH_PASSWORD_RESET_TEMPLATE,
            {
                "user": user,
                "reset_url": url,
                "expires_at": expires_at,
                "expires_at_str": format_expires_at(expires_at, locale),
            },
            locale=locale,
        )
        await self._send_email(
            "password_reset",
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
        html = self._render_email(
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
        await self._send_email(
            "email_change",
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
        html = self._render_email(
            self.auth_settings.AUTH_EMAIL_VERIFICATION_TEMPLATE,
            {
                "user": user,
                "verify_url": url,
                "expires_at": expires_at,
                "expires_at_str": format_expires_at(expires_at, locale),
            },
            locale=locale,
        )
        await self._send_email(
            "email_verification",
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
        html = self._render_email(
            self.auth_settings.AUTH_EMAIL_CHANGED_NOTICE_TEMPLATE,
            {
                "user": user,
                "old_email": old_email,
                "new_email": new_email,
            },
            locale=locale,
        )
        await self._send_email(
            "email_changed_notice",
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

    def issue_mfa_token(
        self,
        user: BaseUserModel,
        *,
        amr: Sequence[str] | None = PASSWORD_AMR,
    ) -> str:
        """Mint the short-lived JWT that bridges step 1 and step 2 of login.

        The token proves the first step only. Its ``typ`` claim keeps a
        route guard from accepting it as an access token, which would let a
        caller who knows just the password skip the second factor
        entirely.

        Args:
            user (BaseUserModel): The user to inspect.
            amr (Sequence[str] | None): RFC 8176 methods the first step
                proved — :data:`~tempest_fastapi_sdk.PASSWORD_AMR` after
                ``/auth/login``, ``["ext"]`` after a social login,
                ``["email"]`` after an activation or reset link. Stored as
                the token's ``amr`` claim, which :meth:`second_factor_amr`
                reads back at ``/auth/mfa/verify``. ``None`` omits it.

        Returns:
            str: The generated value.
        """
        claims: dict[str, Any] = {
            "sub": str(user.id),
            "purpose": "mfa_pending",
            "typ": MFA_TOKEN_TYPE,
        }
        if amr:
            claims[AMR_CLAIM] = list(amr)
        return self.jwt.encode(
            claims,
            ttl=timedelta(
                seconds=self.auth_settings.AUTH_MFA_TOKEN_TTL_SECONDS,
            ),
        )

    def second_factor_amr(self, mfa_token: str) -> tuple[str, ...]:
        """Return the ``amr`` of the session ``/auth/mfa/verify`` opens.

        The first-step methods recorded in ``mfa_token`` by
        :meth:`issue_mfa_token`, followed by ``otp`` — so a password login
        becomes :data:`~tempest_fastapi_sdk.MFA_AMR` (``["pwd", "otp"]``), a
        social login ``["ext", "otp"]`` and an emailed link
        ``["email", "otp"]``. Each value names a method that actually ran,
        which is what RFC 8176 asks of the claim. A token minted without
        the claim (by an ``issue_mfa_token`` override written before it
        existed) falls back to ``["pwd"]`` as the first step, the
        behavior of every release before this one.

        Call it only after :meth:`mfa_verify` accepted the token: it
        decodes without re-checking the purpose.

        Args:
            mfa_token (str): The token :meth:`mfa_verify` just accepted.

        Returns:
            tuple[str, ...]: The methods to stamp on the new pair.
        """
        first = token_amr(self.jwt.decode(mfa_token)) or PASSWORD_AMR
        return (*(method for method in first if method != AMR_OTP), AMR_OTP)

    async def mfa_enroll(
        self,
        session: AsyncSession,
        *,
        user: BaseUserModel,
        recovery_code_model: type[BaseUserRecoveryCodeModel],
    ) -> tuple[str, str, list[str]]:
        """Issue a fresh TOTP secret + recovery codes for ``user``.

        ``totp_enabled_at`` is **NOT** set yet; the caller MUST
        confirm a valid code via :meth:`mfa_confirm` before MFA is
        actually active. Until then, the persisted secret is dead
        weight and login keeps working without the TOTP step.

        Refused once MFA is active (``totp_enabled_at`` set): enrolling
        wipes the recovery codes, so allowing it over an active factor
        would let a bare access token switch MFA off without the
        password and code that :meth:`mfa_disable` requires. Rotation
        is :meth:`mfa_disable` followed by a new enroll and confirm.
        Calling it again while the previous enrollment is still
        pending (never confirmed) is allowed and replaces the staged
        secret and codes. The check reads the column, not
        :meth:`is_mfa_enrolled`, so it holds even with the
        ``AUTH_MFA_ENABLED`` kill-switch off.

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
            MFAAlreadyEnrolledException: When MFA is already active for
                ``user`` (409, code ``MFA_ALREADY_ENROLLED``).
            ImportError: When the ``[mfa]`` extra is not installed.

        Notes:
            Enrolling over a pending enrollment wipes the recovery codes
            it staged, so an old code set never stays valid alongside a
            new one.
        """
        from tempest_fastapi_sdk.utils.totp import TOTPHelper

        if getattr(user, "totp_enabled_at", None) is not None:
            raise MFAAlreadyEnrolledException()
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
        user.totp_last_step = None
        await session.flush()
        await session.refresh(user)
        return secret, provisioning, plaintexts

    async def mfa_confirm(
        self,
        session: AsyncSession,
        *,
        user: BaseUserModel,
        code: str,
        revoke_sessions: bool = True,
    ) -> None:
        """Mark MFA as active after the user proves they can read the QR.

        Turning MFA on is the moment a user suspects someone else has the
        password, so by default every refresh token the user holds is
        revoked in the same transaction (:meth:`revoke_user_sessions`): a
        session opened with the password alone, before the factor existed,
        does not get to keep refreshing past it. Access tokens already
        issued live until their short TTL ends, and carry ``amr ==
        ["pwd"]``, so :func:`~tempest_fastapi_sdk.make_mfa_required_dependency`
        refuses them meanwhile. The bundled ``/auth/mfa/confirm`` route then
        issues the caller a fresh pair with
        :data:`~tempest_fastapi_sdk.MFA_AMR`, so the device that confirmed
        stays signed in.

        Stateless refresh tokens (no ``refresh_token_model``) have no row to
        revoke and keep working until they expire — but they carry the
        ``amr`` of the login that minted them, so they never mint an MFA
        token either.

        Args:
            session (AsyncSession): Active SQLAlchemy session.
            user (BaseUserModel): The user finishing enrollment.
            code (str): 6-digit code from the Authenticator app.
            revoke_sessions (bool): Revoke every refresh token of the user
                (default). ``False`` is the explicit opt-out, for a caller
                that ends the sessions some other way; the tokens it keeps
                still refresh with the ``amr`` they were born with, never
                with ``"otp"``.

        Raises:
            UnauthorizedException: When the code does not match the
                pending secret, or when its step was already spent
                (no MFA enrollment happens either way).
            ValidationException: When no secret is staged (caller
                must run :meth:`mfa_enroll` first).

        Notes:
            The accepted step is stored in ``totp_last_step``, so the
            code that proves the QR was read cannot be spent again at
            ``/auth/mfa/verify`` — the first login needs a later step.
        """
        if not user.totp_secret:
            raise ValidationException(
                message="MFA not initialized — call enroll first",
            )
        user = await self._attach(session, user)
        if not await self._claim_totp_step(session, user, code):
            raise UnauthorizedException(message="invalid MFA code")
        user.totp_enabled_at = utcnow()
        await session.flush()
        if revoke_sessions:
            await self.revoke_user_sessions(session, user_id=user.id)
        await session.refresh(user)

    async def reauthenticate(
        self,
        session: AsyncSession,
        *,
        user: BaseUserModel,
        current_password: str,
        code: str | None = None,
        recovery_code_model: type[BaseUserRecoveryCodeModel] | None = None,
        throttle: AttemptThrottle | None = None,
    ) -> BaseUserModel:
        """Demand fresh proof of the account's factors before a sensitive change.

        A bearer access token proves that *someone* holds a session, not
        that the account owner is at the keyboard: a token read from a
        log, a shared device or a script injected into the page carries
        the same authority. Operations that add a lasting way into the
        account — registering a passkey is the one the bundled router
        guards with this — must not be reachable with the token alone,
        or a short-lived leak becomes a credential that outlives the
        token, a password reset and a later TOTP enrollment.

        The password is checked first, against the stored hash, as
        :meth:`change_password` does. When the account has TOTP enrolled
        (:meth:`is_mfa_enrolled`) the second factor is required too and is
        spent like at login: a TOTP step is recorded, a recovery code is
        stamped ``used_at``. An account without MFA is asked for the
        password only, and ``code`` is ignored.

        Args:
            session (AsyncSession): Active SQLAlchemy session. The caller
                owns the commit.
            user (BaseUserModel): The authenticated account.
            current_password (str): The account's plaintext password.
            code (str | None): A 6-digit TOTP code or a recovery code;
                required when the account has MFA enrolled.
            recovery_code_model (type[BaseUserRecoveryCodeModel] | None):
                The project's concrete recovery-code model. ``None``
                (default) accepts TOTP codes only.
            throttle (AttemptThrottle | None): Failure budget for the
                second factor, keyed on ``f"mfa:{user_id}"`` like
                :meth:`mfa_verify`. ``None`` (default) leaves the factor
                check unthrottled.

        Returns:
            BaseUserModel: The account, attached to ``session``.

        Raises:
            UnauthorizedException: When the password does not match, or
                when MFA is enrolled and ``code`` is missing or matches
                neither an unspent TOTP step nor an unused recovery code.
            MfaThrottledException: When ``throttle`` has the account's
                attempt budget spent. Raised before the code is checked.
        """
        if not self.passwords.verify(current_password, user.hashed_password):
            raise UnauthorizedException(message="current password is incorrect")
        user = await self._attach(session, user)
        if not self.is_mfa_enrolled(user):
            return user
        if code is None:
            raise UnauthorizedException(message="invalid MFA code")
        throttle_key = f"mfa:{user.id}"
        if throttle is not None:
            attempt = await throttle.hit(throttle_key)
            if attempt.attempts > throttle.max_attempts:
                raise MfaThrottledException(
                    message="too many invalid MFA codes, try again later",
                    retry_after_seconds=attempt.retry_after_seconds,
                )
        if recovery_code_model is None:
            accepted = await self._claim_totp_step(session, user, code)
        else:
            accepted = await self._verify_mfa_code(
                session, user, code, recovery_code_model
            )
        if not accepted:
            raise UnauthorizedException(message="invalid MFA code")
        if throttle is not None:
            await throttle.reset(throttle_key)
        return user

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
            code (str): Active TOTP or single-use recovery code. A TOTP
                code whose step was already accepted (by
                :meth:`mfa_confirm`, :meth:`mfa_verify` or an earlier
                call) is refused like a wrong one.
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
        await self.mfa_reset(
            session,
            user=user,
            recovery_code_model=recovery_code_model,
            revoke_sessions=False,
        )

    async def mfa_reset(
        self,
        session: AsyncSession,
        *,
        user: BaseUserModel,
        recovery_code_model: type[BaseUserRecoveryCodeModel],
        revoke_sessions: bool = True,
    ) -> int:
        """Switch MFA off for ``user`` without proof of the factor.

        The operator's recovery path for an account whose owner lost both
        the authenticator and the recovery codes: :meth:`mfa_disable` cannot
        help them, because it asks for exactly what they lost. Nothing here
        is verified — no password, no code — so it belongs behind an
        operator boundary (``tempest user mfa-reset``, an admin action),
        never on a route the account holder reaches. The caller owns the
        commit and should record an audit event in the same transaction.

        The secret, ``totp_enabled_at`` and ``totp_last_step`` are cleared
        and every recovery code of the user is deleted, exactly as
        :meth:`mfa_disable` does after its checks. The check for an active
        factor reads the column, not :meth:`is_mfa_enrolled`, so it holds
        with the ``AUTH_MFA_ENABLED`` kill-switch off.

        Args:
            session (AsyncSession): Active SQLAlchemy session. The changes
                are flushed, not committed.
            user (BaseUserModel): The user whose second factor is removed.
            recovery_code_model (type[BaseUserRecoveryCodeModel]): The
                project's concrete recovery-code model, whose rows for
                ``user`` are deleted.
            revoke_sessions (bool): Revoke every refresh token of the user
                through :meth:`revoke_user_sessions` (default): whoever holds
                the lost device may hold a live session too. ``False`` is
                the explicit opt-out.

        Returns:
            int: How many refresh-token rows were revoked — ``0`` with
            ``revoke_sessions=False`` or without a ``refresh_token_model``.

        Raises:
            ValidationException: When MFA is not active for ``user``.
        """
        if not user.totp_secret or not user.totp_enabled_at:
            raise ValidationException(message="MFA not active")
        user = await self._attach(session, user)
        user.totp_secret = None
        user.totp_enabled_at = None
        user.totp_last_step = None
        await session.execute(
            delete(recovery_code_model).where(
                recovery_code_model.user_id == user.id,
            ),
        )
        await session.flush()
        revoked = 0
        if revoke_sessions:
            revoked = await self.revoke_user_sessions(session, user_id=user.id)
        await session.refresh(user)
        return revoked

    async def mfa_verify(
        self,
        session: AsyncSession,
        *,
        mfa_token: str,
        code: str,
        recovery_code_model: type[BaseUserRecoveryCodeModel],
        throttle: AttemptThrottle | None = None,
    ) -> BaseUserModel:
        """Step 2 of two-step login — swap the intermediate token for JWTs.

        Args:
            session (AsyncSession): Active SQLAlchemy session.
            mfa_token (str): Intermediate JWT issued by step 1.
            code (str): 6-digit TOTP code OR plaintext recovery
                code from enrollment.
            recovery_code_model (type[BaseUserRecoveryCodeModel]): The
                project's concrete recovery-code model.
            throttle (AttemptThrottle | None): Failure budget keyed on
                ``f"mfa:{user_id}"`` — the account, not ``mfa_token``,
                because step 1 mints a fresh token on every login and a
                per-token key would hand the budget back to whoever
                retries ``/auth/login``. Each verification reserves one
                attempt **before** the code is checked, and the attempt
                past ``max_attempts`` is refused with ``429`` whatever the
                code; a success resets the key, so only wrong codes
                accumulate. Reserving first, rather than counting after the
                check, is what keeps a burst of concurrent guesses from all
                passing a "not blocked yet" read together. ``None``
                (default) leaves the call unthrottled — the bundled router
                always passes one.

        Returns:
            BaseUserModel: The fully authenticated user — caller
            mints the JWT pair next.

        Raises:
            UnauthorizedException: On bad / expired ``mfa_token``,
                bad code, user not enrolled in MFA, or a TOTP code
                whose step was already accepted.
            MfaThrottledException: When ``throttle`` has the
                account's attempt budget spent (``429``, carrying
                ``Retry-After``). Raised before the code is checked.

        Notes:
            A TOTP code is single-use: the accepted step is written to
            ``totp_last_step`` in one conditional ``UPDATE``, and a code
            whose step is at or below it is refused — so a code seen in
            transit is not worth a second login inside its drift window,
            even from a session that loaded the row before it was spent.
            Recovery codes were already single-use (``used_at``).
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
        throttle_key = f"mfa:{user_id}"
        if throttle is not None:
            attempt = await throttle.hit(throttle_key)
            if attempt.attempts > throttle.max_attempts:
                raise MfaThrottledException(
                    message="too many invalid MFA codes, try again later",
                    retry_after_seconds=attempt.retry_after_seconds,
                )
        if not await self._verify_mfa_code(session, user, code, recovery_code_model):
            raise UnauthorizedException(message="invalid MFA code")
        if throttle is not None:
            await throttle.reset(throttle_key)
        user.last_login_at = utcnow()
        await session.flush()
        await session.refresh(user)
        return user

    async def change_password_with_mfa(
        self,
        session: AsyncSession,
        *,
        user: BaseUserModel,
        code: str,
        new_password: str,
        recovery_code_model: type[BaseUserRecoveryCodeModel] | None = None,
        revoke_sessions: bool = True,
        throttle: AttemptThrottle | None = None,
    ) -> BaseUserModel:
        """Replace the password of an MFA account on proof of the second factor.

        The path for the account whose reset e-mail cannot be reached (or
        must not be trusted) while its owner still holds the authenticator
        app or a recovery code: the factor is the proof, in place of the
        current password :meth:`change_password` asks for and of the link
        :meth:`confirm_password_reset` consumes.

        The checks run cheapest-first and the factor is spent last: the
        account must have MFA active, then the new password must pass the
        policy, and only then is ``code`` verified — so a password the
        policy refuses never burns a recovery code. ``code`` is checked by
        the same routine :meth:`mfa_verify` and :meth:`mfa_disable` use: a
        TOTP code is single-use (its step is recorded) and a recovery code
        is stamped ``used_at``.

        Unlike :meth:`mfa_verify` this is not a login: no token pair is
        minted and ``last_login_at`` is left alone. By default every refresh
        token of the user is revoked in the same transaction
        (:meth:`revoke_user_sessions`), as in :meth:`confirm_password_reset`.
        The caller owns the commit.

        Args:
            session (AsyncSession): Active SQLAlchemy session.
            user (BaseUserModel): The account whose password is replaced.
            code (str): A 6-digit TOTP code, or one of the plaintext
                recovery codes handed out at enrollment.
            new_password (str): Plaintext replacement password.
            recovery_code_model (type[BaseUserRecoveryCodeModel] | None):
                The project's concrete recovery-code model. ``None``
                (default) accepts TOTP codes only — a recovery code then
                reads as a wrong code.
            revoke_sessions (bool): Revoke every refresh token of the user
                (default). ``False`` is the explicit opt-out, for a caller
                that ends the sessions some other way.
            throttle (AttemptThrottle | None): Failure budget, keyed on
                ``f"mfa:{user_id}"`` — the same key :meth:`mfa_verify`
                uses, so guesses here and at the login step draw on one
                budget. Reserved before the code is checked and reset on
                success. ``None`` (default) leaves the call unthrottled.

        Returns:
            BaseUserModel: The user whose password was rotated.

        Raises:
            MFANotEnrolledException: When the account has no active
                second factor (or the ``AUTH_MFA_ENABLED`` kill-switch is
                off); nothing is spent.
            ValidationException: When ``new_password`` breaks the policy;
                the code is not checked, so it is not spent.
            MfaThrottledException: When ``throttle`` has the account's
                attempt budget spent. Raised before the code is checked.
            UnauthorizedException: When ``code`` matches neither an unspent
                TOTP step nor an unused recovery code.
        """
        if not self.is_mfa_enrolled(user):
            raise MFANotEnrolledException()
        self._enforce_password_policy(new_password)
        user = await self._attach(session, user)
        throttle_key = f"mfa:{user.id}"
        if throttle is not None:
            attempt = await throttle.hit(throttle_key)
            if attempt.attempts > throttle.max_attempts:
                raise MfaThrottledException(
                    message="too many invalid MFA codes, try again later",
                    retry_after_seconds=attempt.retry_after_seconds,
                )
        if recovery_code_model is None:
            accepted = await self._claim_totp_step(session, user, code)
        else:
            accepted = await self._verify_mfa_code(
                session, user, code, recovery_code_model
            )
        if not accepted:
            raise UnauthorizedException(message="invalid MFA code")
        if throttle is not None:
            await throttle.reset(throttle_key)
        user.hashed_password = self.passwords.hash(new_password)
        await session.flush()
        if revoke_sessions:
            await self.revoke_user_sessions(session, user_id=user.id)
        await session.refresh(user)
        return user

    async def _claim_totp_step(
        self,
        session: AsyncSession,
        user: BaseUserModel,
        code: str,
    ) -> bool:
        """Accept ``code`` as a TOTP code at most once.

        Resolves the step ``code`` belongs to and records it in
        ``totp_last_step`` with a single conditional ``UPDATE`` that only
        matches while the stored step is ``NULL`` or older. The row count
        is the verdict: a replayed code (same step) or an older one
        matches nothing, and the decision is taken by the stored row
        rather than by the loaded object, so a session holding a stale
        copy cannot accept a step another session already spent.

        Args:
            session (AsyncSession): The active DB session. The statement
                is executed but not committed — the caller owns the
                transaction.
            user (BaseUserModel): The user whose secret is checked.
            code (str): The submitted 6-digit code.

        Returns:
            bool: ``True`` when ``code`` matched a step inside the
            configured window that had not been accepted before — and
            that step is now recorded. ``False`` when there is no secret,
            no step matches, or the step was already spent.
        """
        from tempest_fastapi_sdk.utils.totp import TOTPHelper

        secret: str | None = getattr(user, "totp_secret", None)
        if not secret:
            return False
        step = TOTPHelper(issuer=self.auth_settings.AUTH_MFA_ISSUER).matching_step(
            secret,
            code,
            window=self.auth_settings.AUTH_MFA_VERIFY_WINDOW,
        )
        if step is None:
            return False
        model = self.user_model
        result = cast(
            "CursorResult[Any]",
            await session.execute(
                update(model)
                .where(
                    model.id == user.id,
                    or_(
                        model.totp_last_step.is_(None),
                        model.totp_last_step < step,
                    ),
                )
                .values(totp_last_step=step)
            ),
        )
        return int(result.rowcount or 0) == 1

    async def _verify_mfa_code(
        self,
        session: AsyncSession,
        user: BaseUserModel,
        code: str,
        recovery_code_model: type[BaseUserRecoveryCodeModel],
    ) -> bool:
        """Check ``code`` against TOTP first, then unused recovery codes.

        Both branches spend what they accept: the TOTP branch records the
        step through :meth:`_claim_totp_step`, so the same code is refused
        on the next call, and the recovery-code branch stamps ``used_at``
        on the single-use code handed out (in plaintext) at enrollment.

        Args:
            session (AsyncSession): The active DB session.
            user (BaseUserModel): The user being verified.
            code (str): The submitted TOTP or recovery code.
            recovery_code_model (type[BaseUserRecoveryCodeModel]): The model
                holding this project's recovery codes.

        Returns:
            bool: ``True`` when the code matched a TOTP step not accepted
            before or an unused recovery code.
        """
        if await self._claim_totp_step(session, user, code):
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
    "SIGNUP_PROTECTED_FIELDS",
    "ActivationToken",
    "EmailChangeToken",
    "EmailVerificationToken",
    "PasswordResetToken",
    "UserAuthService",
    "revoke_user_refresh_tokens",
]
