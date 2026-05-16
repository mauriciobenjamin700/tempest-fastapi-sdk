"""Opt-in SQLAlchemy mixins layered on top of :class:`BaseModel`."""

from __future__ import annotations

from datetime import datetime
from uuid import UUID

from sqlalchemy import TIMESTAMP, Uuid
from sqlalchemy.orm import Mapped, mapped_column

from tempest_fastapi_sdk.utils.datetime import utcnow


class SoftDeleteMixin:
    """Add a ``deleted_at`` timestamp for non-destructive deletes.

    Pairs with the canonical ``is_active`` flag on
    :class:`tempest_fastapi_sdk.BaseModel`: ``is_active`` toggles
    visibility quickly while ``deleted_at`` records when the soft
    delete happened (useful for audit and retention policies).

    A row is considered "alive" when ``deleted_at IS NULL``. Filtering
    is the caller's responsibility — the mixin keeps the column
    declarative-only so it composes with arbitrary query strategies
    (global filters, partial indexes, repository hooks).

    Attributes:
        deleted_at (datetime | None): Timestamp of the soft delete,
            or ``None`` while the row is alive.
    """

    deleted_at: Mapped[datetime | None] = mapped_column(
        TIMESTAMP(timezone=True),
        nullable=True,
        default=None,
        doc=(
            "Soft-delete timestamp. NULL while the row is alive; set "
            "by the application when soft-deleted. Audit-friendly "
            "alternative to physically removing the row."
        ),
    )

    def mark_deleted(self) -> None:
        """Stamp ``deleted_at`` with the current UTC instant."""
        self.deleted_at = utcnow()

    def mark_restored(self) -> None:
        """Clear ``deleted_at`` to mark the row alive again."""
        self.deleted_at = None

    @property
    def is_deleted(self) -> bool:
        """Whether the row is currently soft-deleted.

        Returns:
            bool: ``True`` when ``deleted_at`` is non-null.
        """
        return self.deleted_at is not None


class AuditMixin:
    """Add ``created_by`` / ``updated_by`` foreign-key columns.

    Tracks which user (by UUID) last touched a row. The mixin only
    declares the columns — populating them is the application's
    responsibility, typically inside the service layer (where the
    current user is in scope) right before calling the repository.

    Attributes:
        created_by (UUID | None): UUID of the user that created the
            row. Nullable for system-generated rows.
        updated_by (UUID | None): UUID of the user that last updated
            the row. Nullable until the first update.
    """

    created_by: Mapped[UUID | None] = mapped_column(
        Uuid(),
        nullable=True,
        default=None,
        doc="UUID of the user that created the row.",
    )
    updated_by: Mapped[UUID | None] = mapped_column(
        Uuid(),
        nullable=True,
        default=None,
        doc="UUID of the user that last updated the row.",
    )

    def stamp_created_by(self, user_id: UUID) -> None:
        """Set both audit columns to ``user_id`` on initial insert.

        Args:
            user_id (UUID): The acting user's primary key.
        """
        self.created_by = user_id
        self.updated_by = user_id

    def stamp_updated_by(self, user_id: UUID) -> None:
        """Update ``updated_by`` to ``user_id`` ahead of an UPDATE.

        Args:
            user_id (UUID): The acting user's primary key.
        """
        self.updated_by = user_id


__all__: list[str] = [
    "AuditMixin",
    "SoftDeleteMixin",
]
