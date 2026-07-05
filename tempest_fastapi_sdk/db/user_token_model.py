"""Opaque-token table used by the bundled auth flows.

Stores short-lived one-shot tokens for account activation,
password reset, email verification — anything that needs a
"send a link, accept once, mark consumed" lifecycle.

Concrete subclasses live in the consuming application so the
table is part of the project's metadata and Alembic emits it
under the application's naming convention. Pattern mirrors
:class:`BaseUserModel` — the SDK ships the abstract row, the
project ships the concrete table mapping.
"""

from __future__ import annotations

from datetime import datetime
from enum import StrEnum
from uuid import UUID

from sqlalchemy import TIMESTAMP, ForeignKey, String
from sqlalchemy.orm import Mapped, mapped_column

from tempest_fastapi_sdk.db.model import BaseModel


class UserTokenPurpose(StrEnum):
    """What a token authorizes when redeemed.

    Each value maps to a distinct flow exposed by
    :class:`tempest_fastapi_sdk.auth.UserAuthService`:

    * ``ACTIVATION`` — confirm the email address the user signed
      up with.
    * ``PASSWORD_RESET`` — let the user pick a new password
      without the old one.
    * ``EMAIL_VERIFICATION`` — re-verify the user's current email
      (resend the confirmation link).
    * ``EMAIL_CHANGE`` — confirm a move to a **new** email address.
      The pending new address travels in the token row's
      :attr:`BaseUserTokenModel.payload`.
    """

    ACTIVATION = "activation"
    PASSWORD_RESET = "password_reset"
    EMAIL_VERIFICATION = "email_verification"
    EMAIL_CHANGE = "email_change"


class BaseUserTokenModel(BaseModel):
    """Abstract one-shot token used by the bundled auth flows.

    Concrete subclasses pick the ``__tablename__`` (``user_tokens``
    by convention) and add an FK to the project's concrete
    ``UserModel``. The SDK never stores the plaintext token — only
    its hash via
    :func:`tempest_fastapi_sdk.hash_opaque_token`. The plaintext
    is returned exactly once (at creation) so it can be embedded
    in the activation / reset link.

    Attributes:
        user_id (UUID): Foreign key to the user this token
            authorizes. Inherits the project's user table name —
            concrete subclasses set the FK target explicitly.
        token_hash (str): SHA-256 hash of the plaintext token.
            Indexed + unique so lookups by hash are fast and
            duplicates impossible.
        purpose (str): One of :class:`UserTokenPurpose`.
        expires_at (datetime): UTC timestamp the token becomes
            invalid. ``redeem()`` checks this against ``now()``
            on every consumption.
        used_at (datetime | None): UTC timestamp the token was
            redeemed. Non-null means the token is spent and must
            not be accepted again.
        payload (str | None): Optional context the flow needs at
            redemption time — e.g. the **pending new email** for an
            ``EMAIL_CHANGE`` token. ``None`` for flows that carry no
            extra data (activation, password reset). Kept generic so
            future one-shot flows can reuse it. Bounded at 320 chars,
            the RFC-5321 maximum email length.
    """

    __abstract__ = True

    user_id: Mapped[UUID] = mapped_column(
        nullable=False,
        index=True,
        doc="FK to the user this token belongs to (set by subclass).",
    )
    token_hash: Mapped[str] = mapped_column(
        String(128),
        nullable=False,
        unique=True,
        index=True,
        doc="SHA-256 hash of the plaintext token.",
    )
    purpose: Mapped[str] = mapped_column(
        String(32),
        nullable=False,
        index=True,
        doc="What the token authorizes (UserTokenPurpose value).",
    )
    expires_at: Mapped[datetime] = mapped_column(
        TIMESTAMP(timezone=True),
        nullable=False,
        doc="UTC timestamp the token expires at.",
    )
    used_at: Mapped[datetime | None] = mapped_column(
        TIMESTAMP(timezone=True),
        nullable=True,
        default=None,
        doc="UTC timestamp the token was redeemed (one-shot).",
    )
    payload: Mapped[str | None] = mapped_column(
        String(320),
        nullable=True,
        default=None,
        doc="Optional flow context (e.g. the pending new email for EMAIL_CHANGE).",
    )


def make_user_token_model(
    *,
    user_table: str,
    tablename: str = "user_tokens",
    class_name: str = "UserTokenModel",
) -> type[BaseUserTokenModel]:
    """Build a concrete ``UserTokenModel`` subclass at runtime.

    Used by tests and lightweight scripts. Production projects
    should instead ship a hand-written
    ``src/db/models/user_token.py`` so the FK column is editable
    and the class is importable for refactors.

    Args:
        user_table (str): Table name of the concrete ``UserModel``
            the FK should reference (usually ``"users"``).
        tablename (str): ``__tablename__`` for the generated
            class.
        class_name (str): Python class name; affects ``repr`` and
            Alembic identifiers.

    Returns:
        type[BaseUserTokenModel]: A concrete mapped class.
    """
    attrs: dict[str, object] = {
        "__tablename__": tablename,
        "user_id": mapped_column(
            ForeignKey(f"{user_table}.id", ondelete="CASCADE"),
            nullable=False,
            index=True,
        ),
        "__module__": __name__,
        "__qualname__": class_name,
    }
    return type(class_name, (BaseUserTokenModel,), attrs)


__all__: list[str] = [
    "BaseUserTokenModel",
    "UserTokenPurpose",
    "make_user_token_model",
]
