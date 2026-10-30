"""Reusable user table — foundation for the admin login flow.

Subclass :class:`BaseUserModel` when an application wants the SDK's
admin site (``tempest_fastapi_sdk.admin``) to manage authentication.
The base model ships the columns the admin auth backend expects
(``email``, ``hashed_password``, ``is_admin``, ``last_login_at``) and the
``email_verified_at`` proof the bundled auth flow keeps, on top of the
standard four columns inherited from :class:`BaseModel`.
Concrete subclasses can add domain-specific fields freely.
"""

from __future__ import annotations

from datetime import datetime
from typing import TYPE_CHECKING

from sqlalchemy import Boolean, String, event, inspect
from sqlalchemy.engine import Connection
from sqlalchemy.orm import Mapped, Mapper, mapped_column

from tempest_fastapi_sdk.db.datetime_type import UtcDateTime
from tempest_fastapi_sdk.db.model import BaseModel
from tempest_fastapi_sdk.utils.password import PasswordUtils


class BaseUserModel(BaseModel):
    """Abstract user table with the columns the admin auth flow needs.

    Inherits ``id``/``is_active``/``created_at``/``updated_at`` from
    :class:`BaseModel` and adds:

    * ``email`` (unique, indexed) — login identifier. Always stored
      lowercased; helpers handle the normalization.
    * ``hashed_password`` — bcrypt hash produced by
      :class:`tempest_fastapi_sdk.PasswordUtils`. Use
      :meth:`set_password` to write and :meth:`check_password` to
      verify so the hashing strategy stays consistent across callers.
    * ``is_admin`` — gate enforced by the admin auth backend; only
      users with ``is_admin=True`` may log in to ``/admin``.
    * ``last_login_at`` — populated by the admin login view on every
      successful authentication.
    * ``email_verified_at`` — when the SDK last saw proof that the
      account holder reads the mailbox behind ``email``; ``NULL`` when it
      never did. See the attribute entry below for what counts as proof.

    The class is marked ``__abstract__`` so SQLAlchemy does not try
    to map it directly; concrete projects subclass it and either keep
    the auto-derived ``__tablename__`` (``user``) or override it.

    Four further columns are declared **type-only**, under
    ``TYPE_CHECKING``: ``totp_secret`` / ``totp_enabled_at`` /
    ``totp_last_step``, whose
    runtime columns come from
    :class:`~tempest_fastapi_sdk.MFAMixin`, and ``name``, whose runtime
    column comes from :class:`~tempest_fastapi_sdk.NameMixin`. The
    bundled service reads all four, but a project that never enables
    MFA or never stores a display name should not carry the columns —
    so the migration lands only when the mixin is actually adopted
    (``class UserModel(MFAMixin, NameMixin, BaseUserModel)``). Code
    reading them at runtime must go through ``getattr``/``hasattr``,
    because a plain ``BaseUserModel`` subclass has none of them.

    Attributes:
        email (str): Login identifier. Unique. 320 chars max
            (RFC 5321 mailbox limit).
        hashed_password (str): Bcrypt hash; never store plaintext.
        is_admin (bool): Whether the user can access the admin site.
        last_login_at (datetime | None): Last successful login timestamp.
        email_verified_at (datetime | None): When ownership of the
            current ``email`` was proven, or ``None`` when it never was.
            :class:`~tempest_fastapi_sdk.UserAuthService` sets it only
            where the SDK itself saw the proof: confirming an activation
            or e-mail-verification link that was mailed to this very
            address, confirming an e-mail change whose link was mailed to
            the new address, and creating an account from a social login
            whose provider reported ``email_verified is True``. Activating
            through the admin site or ``tempest user activate``, and the
            flows where the link comes back in the HTTP response
            (``AUTH_AUTO_ACTIVATE``, ``AUTH_RETURN_TOKEN_IN_RESPONSE``, no
            ``EmailUtils``), leave it alone. An ORM flush that changes
            ``email`` without also writing this column resets it to
            ``None``, because the proof belonged to the previous address;
            a bulk ``UPDATE`` statement bypasses that and must clear it
            itself. Linking a social identity by e-mail
            (``AUTH_OAUTH_LINK_BY_VERIFIED_EMAIL``) requires it.
    """

    __abstract__ = True

    email: Mapped[str] = mapped_column(
        String(320),
        nullable=False,
        unique=True,
        index=True,
        doc="Login identifier (unique, lowercased).",
    )
    hashed_password: Mapped[str] = mapped_column(
        String(255),
        nullable=False,
        doc="Bcrypt hash of the user's password.",
    )
    is_admin: Mapped[bool] = mapped_column(
        Boolean,
        nullable=False,
        default=False,
        doc="Whether the user can access the admin site.",
    )
    last_login_at: Mapped[datetime | None] = mapped_column(
        UtcDateTime,
        nullable=True,
        default=None,
        doc="Timestamp of the user's most recent successful login.",
    )
    email_verified_at: Mapped[datetime | None] = mapped_column(
        UtcDateTime,
        nullable=True,
        default=None,
        doc="When ownership of the current email was proven (NULL: never).",
    )

    if TYPE_CHECKING:
        totp_secret: Mapped[str | None]
        totp_enabled_at: Mapped[datetime | None]
        totp_last_step: Mapped[int | None]
        name: Mapped[str]

    def set_password(self, plain: str, *, rounds: int = 12) -> None:
        """Hash ``plain`` and write it to :attr:`hashed_password`.

        Args:
            plain (str): The plaintext password.
            rounds (int): bcrypt cost factor. Defaults to ``12``.

        Raises:
            ImportError: When the ``[auth]`` extra is not installed.
        """
        self.hashed_password = PasswordUtils(rounds=rounds).hash(plain)

    def check_password(self, plain: str) -> bool:
        """Return whether ``plain`` matches :attr:`hashed_password`.

        Args:
            plain (str): The plaintext password to verify.

        Returns:
            bool: ``True`` when the password is correct.

        Raises:
            ImportError: When the ``[auth]`` extra is not installed.
        """
        if not self.hashed_password:
            return False
        return PasswordUtils().verify(plain, self.hashed_password)

    @staticmethod
    def normalize_email(value: str) -> str:
        """Trim whitespace and lowercase ``value``.

        Args:
            value (str): Raw user input.

        Returns:
            str: The normalized email.
        """
        return value.strip().lower()


@event.listens_for(BaseUserModel, "before_update", propagate=True)
def _forget_proof_of_previous_email(
    mapper: Mapper[BaseUserModel],
    connection: Connection,
    target: BaseUserModel,
) -> None:
    """Reset ``email_verified_at`` when a flush moves the row's ``email``.

    The timestamp proves ownership of one address; once ``email`` points
    somewhere else it would vouch for a mailbox nobody read. A flush that
    writes ``email_verified_at`` in the same unit of work (the confirmed
    e-mail change does) keeps the value it was given, so the SDK's own
    flows decide, and every other writer (the admin site, a script, the
    consumer's own endpoint) loses the proof by default.

    Args:
        mapper (Mapper[BaseUserModel]): The mapper of the concrete user
            class being flushed.
        connection (Connection): The connection the flush runs on.
        target (BaseUserModel): The row about to be updated.
    """
    state = inspect(target)
    if not state.attrs.email.history.has_changes():
        return
    if state.attrs.email_verified_at.history.has_changes():
        return
    target.email_verified_at = None


__all__: list[str] = [
    "BaseUserModel",
]
