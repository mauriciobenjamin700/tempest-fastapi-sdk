"""Balance columns and the append-only statement table of the wallet module.

The balance is a cached integer on the application's own user row,
moved only by conditional ``UPDATE`` statements
(:class:`~tempest_fastapi_sdk.wallet.WalletRepository`). The statement
table records every movement with the balance it produced, so the cache
can be reconciled against the ledger and every cent has a reason.

Money is integer cents throughout. A ``float`` column accumulates
residue (``33.30 - 30.13`` stores ``3.169999999999998``) that ends up in
every sum and report; an integer column cannot.

As with the SDK's other reusable tables, the abstract row lives here and
the project ships the concrete table, so the user FK and
``__tablename__`` live in the application's metadata.
:func:`make_wallet_entry_model` builds one for tests and light scripts.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any
from uuid import UUID

from sqlalchemy import (
    CheckConstraint,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.orm import Mapped, declared_attr, mapped_column

from tempest_fastapi_sdk.db.datetime_type import UtcDateTime
from tempest_fastapi_sdk.db.model import BaseModel

WALLET_BALANCE_CHECK_NAME: str = "wallet_cents_non_negative"
"""Name of the ``CHECK (wallet_cents >= 0)`` constraint.

Rendered through the SDK naming convention as
``ck_<table>_wallet_cents_non_negative``, so a migration that drops it
(to move a table to :class:`OverdraftWalletBalanceMixin`) can name it.
"""


class WalletBalanceMixin:
    """Add a ``wallet_cents`` balance that the database refuses to take below 0.

    Mix into the application's user model. The column is ``NOT NULL``
    with a default of ``0`` and carries ``CHECK (wallet_cents >= 0)``:
    even a statement that bypasses the wallet service cannot leave the
    balance negative. That makes a reversal of money already spent fail
    with :class:`~tempest_fastapi_sdk.wallet.WalletInsufficientFundsException`
    instead of producing a debt — use :class:`OverdraftWalletBalanceMixin`
    when a debt is the behavior you want.

    Attributes:
        wallet_cents (int): The cached balance, in integer cents.
    """

    @declared_attr
    def wallet_cents(cls) -> Mapped[int]:  # noqa: N805
        """Build the balance column with its non-negative check.

        A ``declared_attr`` so every mapped class gets its own
        constraint object; one shared instance would attach to the first
        table only.

        Returns:
            Mapped[int]: The mapped column.
        """
        return mapped_column(
            Integer,
            CheckConstraint("wallet_cents >= 0", name=WALLET_BALANCE_CHECK_NAME),
            nullable=False,
            default=0,
            server_default="0",
            doc="Cached wallet balance in integer cents; never negative.",
        )


class OverdraftWalletBalanceMixin:
    """Add a ``wallet_cents`` balance that may go below 0.

    The same column as :class:`WalletBalanceMixin` without the ``CHECK``.
    Ordinary debits still never overdraw — ``debit_available`` only
    matches a row whose available balance covers the amount — so the
    only movement that can take the balance negative is a forced one:
    :meth:`~tempest_fastapi_sdk.wallet.WalletService.reverse` of a credit
    the user already withdrew (a chargeback, say), which then records
    the debt instead of failing.

    Attributes:
        wallet_cents (int): The cached balance, in integer cents.
    """

    wallet_cents: Mapped[int] = mapped_column(
        Integer,
        nullable=False,
        default=0,
        server_default="0",
        doc="Cached wallet balance in integer cents; may be negative.",
    )


class BaseWalletEntryModel(BaseModel):
    """Abstract append-only statement line: one row per balance movement.

    Every movement is tied to the event that caused it through
    ``(reference_type, reference_id, kind)``, which is unique: the same
    paid order cannot credit twice, however many times its webhook is
    delivered. ``idempotency_key`` is a second, optional key for
    movements whose caller has no natural reference.

    ``available_at`` is when a credit stops being held. A credit with a
    48-hour hold carries ``created_at + 48h``; anything not held carries
    the moment it was posted. The held balance is the signed sum of the
    entries whose ``available_at`` is still in the future — computed
    inside the debit's ``UPDATE``, never in Python.

    The table ships a unique constraint on the reference triple and an
    index on ``(user_id, available_at)`` — the shape of the held-balance
    subquery. A concrete subclass that needs more ``__table_args__``
    extends ``super().__table_args__`` rather than replacing it, since the
    unique constraint is what makes a replayed event a no-op.

    Attributes:
        user_id (UUID): FK to the wallet owner (set by subclass).
        kind (str): The movement kind (``"credit"``, ``"debit"``,
            ``"reversal"``, or an application kind).
        amount_cents (int): Signed amount in cents; positive adds to the
            balance, negative takes from it.
        balance_after_cents (int): The balance right after this movement.
        available_at (datetime): When this movement stops being held.
        reference_type (str): Kind of the event that caused it.
        reference_id (UUID): Id of that event.
        description (str | None): Free text for the statement line.
        idempotency_key (str | None): Optional caller-supplied key,
            unique when present.
    """

    __abstract__ = True

    user_id: Mapped[UUID] = mapped_column(
        nullable=False,
        doc="FK to the wallet owner (set by subclass).",
    )
    kind: Mapped[str] = mapped_column(
        String(32),
        nullable=False,
        doc="Movement kind: credit, debit, reversal or an application kind.",
    )
    amount_cents: Mapped[int] = mapped_column(
        Integer,
        nullable=False,
        doc="Signed amount in cents; negative takes from the balance.",
    )
    balance_after_cents: Mapped[int] = mapped_column(
        Integer,
        nullable=False,
        doc="The balance right after this movement.",
    )
    available_at: Mapped[datetime] = mapped_column(
        UtcDateTime,
        nullable=False,
        doc="When this movement stops being held.",
    )
    reference_type: Mapped[str] = mapped_column(
        String(64),
        nullable=False,
        doc="Kind of the event that caused the movement (e.g. 'order').",
    )
    reference_id: Mapped[UUID] = mapped_column(
        nullable=False,
        doc="Id of the event that caused the movement.",
    )
    description: Mapped[str | None] = mapped_column(
        Text,
        nullable=True,
        default=None,
        doc="Free text for the statement line.",
    )
    idempotency_key: Mapped[str | None] = mapped_column(
        String(128),
        nullable=True,
        default=None,
        unique=True,
        doc="Optional caller-supplied key, unique when present.",
    )

    @declared_attr.directive
    def __table_args__(cls) -> tuple[Any, ...]:  # noqa: N805
        """Declare the replay guard and the held-balance index.

        Returns:
            tuple[Any, ...]: The unique ``(reference_type, reference_id,
            kind)`` constraint and the ``(user_id, available_at)`` index.
        """
        return (
            UniqueConstraint("reference_type", "reference_id", "kind"),
            Index(
                f"ix_{cls.__tablename__}_user_id_available_at",
                "user_id",
                "available_at",
            ),
        )


def make_wallet_entry_model(
    *,
    user_table: str = "users",
    tablename: str = "wallet_entries",
    class_name: str = "WalletEntryModel",
) -> type[BaseWalletEntryModel]:
    """Build a concrete ``WalletEntryModel`` subclass at runtime.

    Args:
        user_table (str): Table name of the concrete user model the
            ``user_id`` FK references.
        tablename (str): ``__tablename__`` for the generated class.
        class_name (str): Python class name.

    Returns:
        type[BaseWalletEntryModel]: A concrete mapped class. The FK uses
        ``ON DELETE RESTRICT``: a statement line is a financial record,
        so deleting a user who still has one is refused rather than
        cascaded away.
    """
    attrs: dict[str, object] = {
        "__tablename__": tablename,
        "user_id": mapped_column(
            ForeignKey(f"{user_table}.id", ondelete="RESTRICT"),
            nullable=False,
        ),
        "__module__": __name__,
        "__qualname__": class_name,
    }
    return type(class_name, (BaseWalletEntryModel,), attrs)


__all__: list[str] = [
    "WALLET_BALANCE_CHECK_NAME",
    "BaseWalletEntryModel",
    "OverdraftWalletBalanceMixin",
    "WalletBalanceMixin",
    "make_wallet_entry_model",
]
