"""The record of a withdrawal a person pays by hand.

:class:`~tempest_fastapi_sdk.wallet.ManualPayoutProvider` writes one
:class:`BaseManualPayoutModel` row per withdrawal it e-mails to the
administrators, after the wallet debit and before the e-mail. The
administrator later sends the Pix, attaches the receipt in the admin panel
(:func:`~tempest_fastapi_sdk.wallet.manual_payout_admin`) and marks the row
paid.

As with :class:`~tempest_fastapi_sdk.wallet.BaseWalletEntryModel`, the
abstract row lives here and the project ships the concrete table, so the
user FK and ``__tablename__`` belong to the application's metadata. Use
:func:`make_manual_payout_model` for tests and light scripts.
"""

from __future__ import annotations

from datetime import datetime
from uuid import UUID

from sqlalchemy import BigInteger, ForeignKey, String
from sqlalchemy.orm import Mapped, mapped_column

from tempest_fastapi_sdk.core.enums import BaseStrEnum
from tempest_fastapi_sdk.db.datetime_type import UtcDateTime
from tempest_fastapi_sdk.db.model import BaseModel


class ManualPayoutStatusEnum(BaseStrEnum):
    """Where a manual payout stands.

    Attributes:
        PENDING: Debited and e-mailed; nobody has marked it paid yet.
        COMPLETED: An administrator marked it paid (the admin panel requires
            the receipt first; the service does so only with
            ``require_receipt=True``). Final.
        CANCELED: The e-mail to the administrators failed, so the payout was
            refused and the wallet debit given back. Final.
        REJECTED: An administrator refused the payout
            (:meth:`~tempest_fastapi_sdk.wallet.ManualPayoutService.reject`)
            and the amount went back to the wallet as a ``WITHDRAW_REFUND``
            line in the same commit. Final.
    """

    PENDING = "pending"
    COMPLETED = "completed"
    CANCELED = "canceled"
    REJECTED = "rejected"


class BaseManualPayoutModel(BaseModel):
    """Abstract row: one withdrawal waiting for, or paid by, a person.

    Attributes:
        user_id (UUID): FK to the wallet owner (set by subclass).
        amount_cents (int): The amount debited and to be sent.
        pix_key_masked (str): The destination key, masked. The full key is in
            the e-mail and in the owner's profile, not here.
        pix_key_type (str): The kind of key (``email``, ``cpf``...).
        correlation_id (str): The withdrawal's id — also the ledger line's
            ``reference_id``. Unique.
        status (str): A :class:`ManualPayoutStatusEnum` value.
        receipt_key (str | None): Storage key of the receipt PDF.
        completed_at (datetime | None): When it was marked paid.
        completed_by (str | None): Who marked it paid (the admin's id).
        end_to_end_id (str | None): The Pix end-to-end id of the transfer,
            when whoever marked it paid informed it.
        rejection_reason (str | None): Why the payout was refused. The
            panel lets an operator type it while the row is ``PENDING``;
            :meth:`~tempest_fastapi_sdk.wallet.ManualPayoutService.reject`
            writes it.
        rejected_at (datetime | None): When it was refused.
        rejected_by (str | None): Who refused it (the admin's id).
    """

    __abstract__ = True

    user_id: Mapped[UUID] = mapped_column(
        nullable=False,
        index=True,
        doc="FK to the wallet owner (set by subclass).",
    )
    amount_cents: Mapped[int] = mapped_column(
        BigInteger, nullable=False, doc="Amount in cents."
    )
    pix_key_masked: Mapped[str] = mapped_column(
        String(64), nullable=False, doc="Destination key, masked."
    )
    pix_key_type: Mapped[str] = mapped_column(
        String(16), nullable=False, doc="Kind of Pix key."
    )
    correlation_id: Mapped[str] = mapped_column(
        String(128), nullable=False, unique=True, doc="The withdrawal's id."
    )
    status: Mapped[str] = mapped_column(
        String(16),
        nullable=False,
        index=True,
        default=ManualPayoutStatusEnum.PENDING.value,
        doc="A ManualPayoutStatusEnum value.",
    )
    receipt_key: Mapped[str | None] = mapped_column(
        String(512), nullable=True, default=None, doc="Storage key of the receipt."
    )
    completed_at: Mapped[datetime | None] = mapped_column(
        UtcDateTime, nullable=True, default=None, doc="When it was marked paid."
    )
    completed_by: Mapped[str | None] = mapped_column(
        String(128), nullable=True, default=None, doc="Who marked it paid."
    )
    end_to_end_id: Mapped[str | None] = mapped_column(
        String(64), nullable=True, default=None, doc="Pix end-to-end id."
    )
    rejection_reason: Mapped[str | None] = mapped_column(
        String(512), nullable=True, default=None, doc="Why it was refused."
    )
    rejected_at: Mapped[datetime | None] = mapped_column(
        UtcDateTime, nullable=True, default=None, doc="When it was refused."
    )
    rejected_by: Mapped[str | None] = mapped_column(
        String(128), nullable=True, default=None, doc="Who refused it."
    )


def make_manual_payout_model(
    *,
    user_table: str = "users",
    tablename: str = "manual_payouts",
    class_name: str = "ManualPayoutModel",
    ondelete: str = "RESTRICT",
) -> type[BaseManualPayoutModel]:
    """Build a concrete ``ManualPayoutModel`` subclass at runtime.

    Args:
        user_table (str): Table name of the concrete user model the FK
            references.
        tablename (str): ``__tablename__`` for the generated class.
        class_name (str): Python class name.
        ondelete (str): ``ON DELETE`` action of the user FK. ``RESTRICT``
            by default, for the reason the wallet ledger uses it: the row
            is a money record.

    Returns:
        type[BaseManualPayoutModel]: A concrete mapped class.
    """
    attrs: dict[str, object] = {
        "__tablename__": tablename,
        "user_id": mapped_column(
            ForeignKey(f"{user_table}.id", ondelete=ondelete),
            nullable=False,
            index=True,
        ),
        "__module__": __name__,
        "__qualname__": class_name,
    }
    return type(class_name, (BaseManualPayoutModel,), attrs)


__all__: list[str] = [
    "BaseManualPayoutModel",
    "ManualPayoutStatusEnum",
    "make_manual_payout_model",
]
