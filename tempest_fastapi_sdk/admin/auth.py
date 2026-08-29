"""Admin authentication backends — pluggable, default backed by BaseUserModel."""

from __future__ import annotations

from abc import ABC, abstractmethod
from typing import TYPE_CHECKING, Generic, TypeVar
from uuid import UUID

from sqlalchemy import select

from tempest_fastapi_sdk.db.user_model import BaseUserModel
from tempest_fastapi_sdk.utils.datetime import utcnow

if TYPE_CHECKING:
    from sqlalchemy.ext.asyncio import AsyncSession


class AdminAuthError(Exception):
    """Raised by authentication backends when credentials are rejected.

    Captures the user-facing message the login template should render
    alongside the HTTP status code (default 401). Specific failure
    reasons should map to subclasses of this base exception for
    granular templating.
    """

    def __init__(
        self,
        message: str = "Invalid credentials",
        *,
        status_code: int = 401,
    ) -> None:
        """Initialize the error.

        Args:
            message (str): The end-user-facing message.
            status_code (int): HTTP status code to attach.
        """
        super().__init__(message)
        self.message: str = message
        self.status_code: int = status_code


PrincipalT = TypeVar("PrincipalT")
"""The type of the value :meth:`AdminAuthBackend.authenticate` returns."""


class AdminAuthBackend(ABC, Generic[PrincipalT]):
    """Abstract base for admin authentication.

    Implementations receive a session-bound async DB session per
    login attempt; the default :class:`UserModelAuthBackend` queries
    a :class:`BaseUserModel` subclass and enforces ``is_admin=True``.
    Custom backends can use the same protocol to integrate LDAP,
    OAuth, IAM tokens, etc.

    The class is generic in the *principal* — the value ``authenticate``
    returns and every other method consumes. Parameterizing it is what
    makes that contract checkable::

        class LdapAuthBackend(AdminAuthBackend[LdapEntry]):
            def display_name(self, principal: LdapEntry) -> str:
                return principal.common_name

    Subclassing without a parameter stays valid and resolves to
    ``AdminAuthBackend[Any]``, which is the behaviour that shipped before
    the class was generic — nothing has to change to keep working.
    """

    @abstractmethod
    async def authenticate(
        self,
        session: AsyncSession,
        *,
        identifier: str,
        password: str,
    ) -> PrincipalT:
        """Verify credentials and return the authenticated principal.

        Args:
            session (AsyncSession): A live DB session.
            identifier (str): The login identifier (typically email).
            password (str): The plaintext password.

        Returns:
            PrincipalT: The authenticated principal. The admin router
            calls :meth:`principal_id` on the return value to derive the
            session payload. Typically a :class:`BaseUserModel` row.

        Raises:
            AdminAuthError: On any rejection (unknown user, wrong
                password, not an admin, disabled account).
        """

    @abstractmethod
    async def load_principal(
        self,
        session: AsyncSession,
        principal_id: str,
    ) -> PrincipalT | None:
        """Reload the principal from storage given its ID.

        Called on every request once the session cookie has been
        validated. Returning ``None`` invalidates the session.

        Args:
            session (AsyncSession): A live DB session.
            principal_id (str): The identifier produced by
                :meth:`principal_id` at login.

        Returns:
            PrincipalT | None: The reloaded principal, or ``None`` when
            it no longer exists or no longer has admin access.
        """

    @abstractmethod
    def principal_id(self, principal: PrincipalT) -> str:
        """Return a stable identifier for the authenticated principal.

        Args:
            principal (PrincipalT): The value returned by
                :meth:`authenticate`.

        Returns:
            str: The identifier serialized into the session cookie.
        """

    def display_name(self, principal: PrincipalT) -> str:
        """Return a human-readable label for the principal.

        Defaults to the principal's ``email`` attribute (or its repr
        when missing); override for richer labels.

        Args:
            principal (PrincipalT): The principal.

        Returns:
            str: A label suitable for the admin header bar.
        """
        email = getattr(principal, "email", None)
        return str(email) if email else repr(principal)

    def mfa_enabled(self, principal: PrincipalT) -> bool:
        """Whether ``principal`` must pass a TOTP challenge after login.

        Defaults to ``False`` so non-user backends (LDAP, OAuth) skip
        the second factor unless they opt in. Override to gate the
        login behind MFA.

        Args:
            principal (PrincipalT): The authenticated principal.

        Returns:
            bool: ``True`` to require the TOTP step.
        """
        return False

    def verify_mfa(self, principal: PrincipalT, code: str) -> bool:
        """Verify a submitted TOTP ``code`` for ``principal``.

        Only called when :meth:`mfa_enabled` returned ``True``. The
        default rejects everything; backends that enable MFA must
        override it.

        Args:
            principal (PrincipalT): The authenticated principal.
            code (str): The 6-digit code from the authenticator app.

        Returns:
            bool: ``True`` when the code is valid.
        """
        return False


class UserModelAuthBackend(AdminAuthBackend[BaseUserModel]):
    """Default backend backed by :class:`BaseUserModel`.

    Authenticates by selecting the row whose ``email`` matches the
    inbound identifier (case-insensitive), verifying the password via
    :class:`tempest_fastapi_sdk.PasswordUtils` and enforcing both
    ``is_admin=True`` and ``is_active=True``. The
    :attr:`last_login_at` column is stamped on every successful login.

    Args:
        user_model (type[BaseUserModel]): The concrete model class.
            Must be a subclass of :class:`BaseUserModel`.
    """

    def __init__(
        self,
        user_model: type[BaseUserModel],
        *,
        mfa_issuer: str = "Admin",
        mfa_window: int = 1,
    ) -> None:
        """Initialize the backend.

        Args:
            user_model (type[BaseUserModel]): The user model to query.
            mfa_issuer (str): Issuer label used when verifying TOTP
                codes (matches what the authenticator app stored).
            mfa_window (int): TOTP clock-drift tolerance in 30s steps.

        Raises:
            TypeError: When ``user_model`` is not a subclass of
                :class:`BaseUserModel`.
        """
        if not isinstance(user_model, type) or not issubclass(
            user_model, BaseUserModel
        ):
            raise TypeError(
                "user_model must be a subclass of BaseUserModel",
            )
        self.user_model: type[BaseUserModel] = user_model
        self.mfa_issuer: str = mfa_issuer
        self.mfa_window: int = mfa_window

    async def authenticate(
        self,
        session: AsyncSession,
        *,
        identifier: str,
        password: str,
    ) -> BaseUserModel:
        """Verify credentials against the configured user model.

        Args:
            session (AsyncSession): A live DB session.
            identifier (str): The user's email.
            password (str): The plaintext password.

        Returns:
            BaseUserModel: The authenticated row with
            :attr:`last_login_at` already stamped.

        Raises:
            AdminAuthError: On any rejection.
        """
        normalized = self.user_model.normalize_email(identifier)
        result = await session.execute(
            select(self.user_model).where(self.user_model.email == normalized)
        )
        user = result.scalar_one_or_none()
        if user is None:
            raise AdminAuthError("Invalid credentials")
        if not user.is_active:
            raise AdminAuthError("Account disabled")
        if not user.is_admin:
            raise AdminAuthError("This account is not authorized for /admin")
        if not user.check_password(password):
            raise AdminAuthError("Invalid credentials")
        user.last_login_at = utcnow()
        await session.commit()
        await session.refresh(user)
        return user

    async def load_principal(
        self,
        session: AsyncSession,
        principal_id: str,
    ) -> BaseUserModel | None:
        """Reload the user by ID, ensuring they still qualify for admin.

        Returns ``None`` when the user no longer exists, has been
        soft-deleted, or had ``is_admin`` revoked.

        Args:
            session (AsyncSession): A live DB session.
            principal_id (str): The UUID hex string from the cookie.

        Returns:
            BaseUserModel | None: The user row, or ``None``.
        """
        try:
            uid = UUID(principal_id)
        except (ValueError, TypeError):
            return None
        result = await session.execute(
            select(self.user_model).where(self.user_model.id == uid)
        )
        user = result.scalar_one_or_none()
        if user is None or not user.is_active or not user.is_admin:
            return None
        return user

    def principal_id(self, principal: BaseUserModel) -> str:
        """Return the ``id`` UUID hex for serialization.

        Args:
            principal (BaseUserModel): The authenticated row.

        Returns:
            str: The UUID as ``str``.
        """
        return str(principal.id)

    def mfa_enabled(self, principal: BaseUserModel) -> bool:
        """Whether the user has completed MFA enrollment.

        True when the user carries a populated ``totp_secret`` and a
        non-null ``totp_enabled_at`` (from
        :class:`tempest_fastapi_sdk.MFAMixin`). Users without the mixin
        simply never enable MFA.

        Args:
            principal (BaseUserModel): The authenticated row.

        Returns:
            bool: ``True`` to require the TOTP step.
        """
        return bool(
            getattr(principal, "totp_secret", None)
            and getattr(principal, "totp_enabled_at", None)
        )

    def verify_mfa(self, principal: BaseUserModel, code: str) -> bool:
        """Verify ``code`` against the user's persisted TOTP secret.

        Args:
            principal (BaseUserModel): The authenticated row.
            code (str): The submitted authenticator code.

        Returns:
            bool: ``True`` when the code is valid.

        Raises:
            ImportError: When the ``[mfa]`` extra (``pyotp``) is not
                installed but a user has MFA enabled.
        """
        from tempest_fastapi_sdk.utils.totp import TOTPHelper

        secret = getattr(principal, "totp_secret", None)
        if not secret:
            return False
        return TOTPHelper(issuer=self.mfa_issuer).verify(
            secret, code, window=self.mfa_window
        )


__all__: list[str] = [
    "AdminAuthBackend",
    "AdminAuthError",
    "UserModelAuthBackend",
]
