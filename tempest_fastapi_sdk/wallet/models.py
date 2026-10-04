"""Wallet balance column and ledger table for the wallet module.

The balance lives on the application's own user row, in integer cents,
because that is where both services that motivated this module already
keep it — a separate balance table would add a join to every read and a
second row to keep consistent. :class:`WalletBalanceMixin` declares the
column for a new model; an existing one points the service at its own
column with ``balance_attribute=``.

Every movement of that balance is also written to the ledger
(:class:`BaseWalletEntryModel`): signed amount, the balance right after
it, and when a credit becomes withdrawable. The balance column stays the
source of truth for "how much"; the ledger answers "why" and "since
when".

As with the SDK's other reusable tables, the abstract row lives here and
the project ships the concrete table, so the user FK and
``__tablename__`` belong to the application's metadata. Use
:func:`make_wallet_entry_model` for tests and light scripts.
"""

from __future__ import annotations

from datetime import datetime
from uuid import UUID

from sqlalchemy import BigInteger, ForeignKey, String
from sqlalchemy.orm import Mapped, mapped_column

from tempest_fastapi_sdk.db.datetime_type import UtcDateTime
from tempest_fastapi_sdk.db.model import BaseModel


class WalletBalanceMixin:
    """Integer-cents balance column for a user model.

    Mix into the application's concrete user model. The attribute name
    ``wallet_cents`` is what :class:`~tempest_fastapi_sdk.wallet.WalletService`
    reads by default.

    Attributes:
        wallet_cents (int): Current balance in cents. Never negative when
            every write goes through the service.
    """

    wallet_cents: Mapped[int] = mapped_column(
        BigInteger,
        nullable=False,
        default=0,
        server_default="0",
        doc="Wallet balance in integer cents.",
    )


class BaseWalletEntryModel(BaseModel):
    """Abstract ledger row: one credit or debit of a user's wallet.

    Attributes:
        user_id (UUID): FK to the wallet owner (set by subclass).
        kind (str): What the movement is (``"SALE"``, ``"WITHDRAW"``...).
            The service writes the values in
            :class:`~tempest_fastapi_sdk.wallet.WalletEntryKind`; the
            application writes its own for credits.
        amount_cents (int): Signed amount — positive credits, negative
            debits.
        balance_after_cents (int): The balance right after this movement.
        available_at (datetime): When a credit becomes withdrawable. Equal
            to the creation time for a credit with no hold, and for every
            debit.
        description (str): Human-readable line for the statement.
        reference_type (str | None): What the movement refers to
            (``"order"``, ``"payout"``...).
        reference_id (str | None): The id of that reference, as text so a
            provider correlation id fits as well as a UUID.
    """

    __abstract__ = True

    user_id: Mapped[UUID] = mapped_column(
        nullable=False,
        index=True,
        doc="FK to the wallet owner (set by subclass).",
    )
    kind: Mapped[str] = mapped_column(
        String(64),
        nullable=False,
        index=True,
        doc="What the movement is.",
    )
    amount_cents: Mapped[int] = mapped_column(
        BigInteger,
        nullable=False,
        doc="Signed amount in cents: positive credit, negative debit.",
    )
    balance_after_cents: Mapped[int] = mapped_column(
        BigInteger,
        nullable=False,
        doc="Balance in cents right after this movement.",
    )
    available_at: Mapped[datetime] = mapped_column(
        UtcDateTime,
        nullable=False,
        index=True,
        doc="When a credit becomes withdrawable.",
    )
    description: Mapped[str] = mapped_column(
        String(255),
        nullable=False,
        default="",
        doc="Statement line.",
    )
    reference_type: Mapped[str | None] = mapped_column(
        String(64),
        nullable=True,
        default=None,
        doc="What the movement refers to.",
    )
    reference_id: Mapped[str | None] = mapped_column(
        String(128),
        nullable=True,
        default=None,
        index=True,
        doc="Id of the reference, as text.",
    )


def make_wallet_entry_model(
    *,
    user_table: str = "users",
    tablename: str = "wallet_entries",
    class_name: str = "WalletEntryModel",
    ondelete: str = "RESTRICT",
) -> type[BaseWalletEntryModel]:
    """Build a concrete ``WalletEntryModel`` subclass at runtime.

    Args:
        user_table (str): Table name of the concrete user model the FK
            references.
        tablename (str): ``__tablename__`` for the generated class.
        class_name (str): Python class name.
        ondelete (str): ``ON DELETE`` action of the user FK. Defaults to
            ``"RESTRICT"``: a user with ledger lines cannot be hard-deleted,
            because ``CASCADE`` would erase the money history with them.
            Deactivate or anonymize the user instead; pass ``"CASCADE"``
            only if losing that history is acceptable.

    Returns:
        type[BaseWalletEntryModel]: A concrete mapped class.
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
    return type(class_name, (BaseWalletEntryModel,), attrs)


__all__: list[str] = [
    "BaseWalletEntryModel",
    "WalletBalanceMixin",
    "make_wallet_entry_model",
]
