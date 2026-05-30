"""Reusable user table — foundation for the admin login flow.

Subclass :class:`BaseUserModel` when an application wants the SDK's
admin site (``tempest_fastapi_sdk.admin``) to manage authentication.
The base model ships the columns the admin auth backend expects
(``email``, ``hashed_password``, ``is_admin``, ``last_login_at``) on
top of the standard four columns inherited from :class:`BaseModel`.
Concrete subclasses can add domain-specific fields freely.
"""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import TIMESTAMP, Boolean, String
from sqlalchemy.orm import Mapped, mapped_column

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

    The class is marked ``__abstract__`` so SQLAlchemy does not try
    to map it directly; concrete projects subclass it and either keep
    the auto-derived ``__tablename__`` (``user``) or override it.

    Attributes:
        email (str): Login identifier. Unique. 320 chars max
            (RFC 5321 mailbox limit).
        hashed_password (str): Bcrypt hash; never store plaintext.
        is_admin (bool): Whether the user can access the admin site.
        last_login_at (datetime | None): Last successful login timestamp.
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
        TIMESTAMP(timezone=True),
        nullable=True,
        default=None,
        doc="Timestamp of the user's most recent successful login.",
    )

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


__all__: list[str] = [
    "BaseUserModel",
]
