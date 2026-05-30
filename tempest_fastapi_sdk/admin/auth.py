"""Admin authentication backends — pluggable, default backed by BaseUserModel."""

from __future__ import annotations

from abc import ABC, abstractmethod
from typing import TYPE_CHECKING, Any
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


class AdminAuthBackend(ABC):
    """Abstract base for admin authentication.

    Implementations receive a session-bound async DB session per
    login attempt; the default :class:`UserModelAuthBackend` queries
    a :class:`BaseUserModel` subclass and enforces ``is_admin=True``.
    Custom backends can use the same protocol to integrate LDAP,
    OAuth, IAM tokens, etc.
    """

    @abstractmethod
    async def authenticate(
        self,
        session: AsyncSession,
        *,
        identifier: str,
        password: str,
    ) -> Any:
        """Verify credentials and return the authenticated principal.

        Args:
            session (AsyncSession): A live DB session.
            identifier (str): The login identifier (typically email).
            password (str): The plaintext password.

        Returns:
            Any: The authenticated principal. The admin router calls
            :meth:`principal_id` on the return value to derive the
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
    ) -> Any | None:
        """Reload the principal from storage given its ID.

        Called on every request once the session cookie has been
        validated. Returning ``None`` invalidates the session.

        Args:
            session (AsyncSession): A live DB session.
            principal_id (str): The identifier produced by
                :meth:`principal_id` at login.

        Returns:
            Any | None: The reloaded principal, or ``None`` when it
            no longer exists or no longer has admin access.
        """

    @abstractmethod
    def principal_id(self, principal: Any) -> str:
        """Return a stable identifier for the authenticated principal.

        Args:
            principal (Any): The value returned by :meth:`authenticate`.

        Returns:
            str: The identifier serialized into the session cookie.
        """

    def display_name(self, principal: Any) -> str:
        """Return a human-readable label for the principal.

        Defaults to the principal's ``email`` attribute (or its repr
        when missing); override for richer labels.

        Args:
            principal (Any): The principal.

        Returns:
            str: A label suitable for the admin header bar.
        """
        email = getattr(principal, "email", None)
        return str(email) if email else repr(principal)


class UserModelAuthBackend(AdminAuthBackend):
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

    def __init__(self, user_model: type[BaseUserModel]) -> None:
        """Initialize the backend.

        Args:
            user_model (type[BaseUserModel]): The user model to query.

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

    def principal_id(self, principal: Any) -> str:
        """Return the ``id`` UUID hex for serialization.

        Args:
            principal (Any): A :class:`BaseUserModel` instance.

        Returns:
            str: The UUID as ``str``.
        """
        return str(principal.id)


__all__: list[str] = [
    "AdminAuthBackend",
    "AdminAuthError",
    "UserModelAuthBackend",
]
