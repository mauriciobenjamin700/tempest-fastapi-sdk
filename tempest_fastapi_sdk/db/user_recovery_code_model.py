"""Recovery codes table for the bundled MFA flow.

A user enrolling in TOTP receives N (configurable via
:attr:`AuthSettings.AUTH_MFA_RECOVERY_CODES_COUNT`) single-use
recovery codes — printed once, on screen — so an Authenticator
loss does not lock the user out. The SDK persists only the
SHA-256 hash of each plaintext (same pattern as
``BaseUserTokenModel``), so a database leak does not yield
usable codes.

Concrete subclasses live in the consuming application so the
table joins the project's metadata and Alembic emits it under
the application's naming convention. The pattern mirrors
:class:`BaseUserTokenModel`.
"""

from __future__ import annotations

from datetime import datetime
from uuid import UUID

from sqlalchemy import TIMESTAMP, String
from sqlalchemy.orm import Mapped, mapped_column

from tempest_fastapi_sdk.db.model import BaseModel


class BaseUserRecoveryCodeModel(BaseModel):
    """Abstract recovery-code row used by the bundled MFA flow.

    Concrete subclasses pick the ``__tablename__``
    (``user_recovery_codes`` by convention) and add the FK to the
    project's concrete ``UserModel``. The SDK never sees the
    plaintext after the enrollment response — only the hash is
    persisted.

    Attributes:
        user_id (UUID): Owner of the recovery code. Concrete
            subclasses MUST declare this as a ``ForeignKey`` so
            cascading deletes wipe the codes alongside the user.
        code_hash (str): SHA-256 hex digest of the plaintext
            recovery code. 64 characters.
        used_at (datetime | None): Timestamp the code was redeemed.
            ``NULL`` while available; populated by
            ``UserAuthService.mfa_verify`` when the user uses a
            recovery code to log in (e.g. lost Authenticator).
    """

    __abstract__ = True

    user_id: Mapped[UUID]
    code_hash: Mapped[str] = mapped_column(
        String(64),
        nullable=False,
        unique=True,
        index=True,
        doc="SHA-256 hex digest of the plaintext recovery code.",
    )
    used_at: Mapped[datetime | None] = mapped_column(
        TIMESTAMP(timezone=True),
        nullable=True,
        default=None,
        doc=(
            "Timestamp the code was redeemed by the MFA verify flow. "
            "``NULL`` means the code is still available."
        ),
    )


def make_user_recovery_code_model(
    *,
    user_table: str,
    tablename: str = "user_recovery_codes",
    class_name: str = "UserRecoveryCodeModel",
) -> type[BaseUserRecoveryCodeModel]:
    """Build a concrete recovery-code model bound to ``user_table``.

    Mirrors :func:`tempest_fastapi_sdk.make_user_token_model` — a
    one-call helper for projects that do not need to subclass the
    abstract base manually.

    Args:
        user_table (str): Name of the project's concrete user
            table (e.g. ``"users"``) — used as the FK target.
        tablename (str): Name of the recovery-code table.
            Defaults to ``"user_recovery_codes"``.
        class_name (str): Python class name. Defaults to
            ``"UserRecoveryCodeModel"``.

    Returns:
        type[BaseUserRecoveryCodeModel]: Concrete SQLAlchemy
        mapping with the FK + cascade set up correctly.
    """
    from sqlalchemy import ForeignKey

    namespace: dict[str, object] = {
        "__tablename__": tablename,
        "user_id": mapped_column(
            ForeignKey(f"{user_table}.id", ondelete="CASCADE"),
            nullable=False,
            index=True,
        ),
    }
    return type(class_name, (BaseUserRecoveryCodeModel,), namespace)


__all__: list[str] = [
    "BaseUserRecoveryCodeModel",
    "make_user_recovery_code_model",
]
