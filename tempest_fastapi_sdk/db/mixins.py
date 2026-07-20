"""Opt-in SQLAlchemy mixins layered on top of :class:`BaseModel`."""

from __future__ import annotations

from datetime import datetime
from uuid import UUID

from sqlalchemy import TIMESTAMP, String, Uuid
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


class MFAMixin:
    """Add TOTP (2FA) columns to a user model.

    Opt-in companion to :class:`tempest_fastapi_sdk.BaseUserModel`.
    Mix it into the concrete user model when the project enables the
    bundled MFA flow (``AUTH_MFA_ENABLED=True``) so the secret and
    activation timestamp live on the user row:

    ```python
    from tempest_fastapi_sdk import BaseUserModel, MFAMixin


    class UserModel(MFAMixin, BaseUserModel):
        __tablename__ = "users"
    ```

    Keeping these columns in a mixin (rather than on
    ``BaseUserModel``) means projects that never enable MFA do not
    carry dead columns, and the migration only lands when the
    feature is actually adopted.

    Attributes:
        totp_secret (str | None): Base32 TOTP secret persisted by the
            MFA flow. ``NULL`` until enrollment. Consider storing it
            encrypted at rest (Postgres ``pgcrypto`` or an
            application-level Fernet wrapper).
        totp_enabled_at (datetime | None): Set by the MFA confirm
            step the first time the user supplies a valid TOTP code.
            ``NULL`` means MFA is not active yet — login skips the
            TOTP step entirely.
    """

    totp_secret: Mapped[str | None] = mapped_column(
        String(64),
        nullable=True,
        default=None,
        doc=(
            "Base32 TOTP secret persisted by the MFA flow. NULL when "
            "the user has not enrolled yet OR has not completed "
            "enrollment. Consider storing this column encrypted at "
            "rest (Postgres pgcrypto or an application-level Fernet "
            "wrapper)."
        ),
    )
    totp_enabled_at: Mapped[datetime | None] = mapped_column(
        TIMESTAMP(timezone=True),
        nullable=True,
        default=None,
        doc=(
            "Set by the MFA confirm step the first time the user "
            "supplies a valid TOTP code. NULL means MFA is not yet "
            "active for this user — login skips the TOTP step entirely."
        ),
    )

    @property
    def is_mfa_active(self) -> bool:
        """Whether the user has completed MFA enrollment.

        Returns:
            bool: ``True`` when ``totp_enabled_at`` is non-null. Does
            NOT account for the ``AUTH_MFA_ENABLED`` kill-switch — use
            :meth:`tempest_fastapi_sdk.UserAuthService.is_mfa_enrolled`
            for the kill-switch-aware check.
        """
        return self.totp_enabled_at is not None


class LocaleColumnMixin:
    """Add a nullable ``locale`` column for the row's preferred language.

    Injects a BCP-47 locale tag column (e.g. ``"pt-BR"``, ``"en-US"``) so a
    model — typically a user — can carry the language its notifications and
    localized text should render in, without every project re-declaring the
    same column. Store a :class:`tempest_fastapi_sdk.Locale` member (it binds
    as its string value) or any raw tag.

    ``NULL`` means "no preference": resolve it to your app's default locale
    (e.g. via :class:`tempest_fastapi_sdk.MessageCatalog`) rather than
    treating it as an error.

    Attributes:
        locale (str | None): The row's preferred BCP-47 locale tag, or
            ``None`` when unset.
    """

    locale: Mapped[str | None] = mapped_column(
        String(35),
        nullable=True,
        default=None,
        doc=(
            "Preferred BCP-47 locale tag (e.g. 'pt-BR', 'en-US') for "
            "localizing this row's notifications and text. NULL means no "
            "preference — fall back to the application default."
        ),
    )


__all__: list[str] = [
    "AuditMixin",
    "LocaleColumnMixin",
    "MFAMixin",
    "SoftDeleteMixin",
]
