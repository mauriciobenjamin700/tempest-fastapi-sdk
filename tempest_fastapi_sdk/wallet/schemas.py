"""Response shapes of the wallet module."""

from __future__ import annotations

from datetime import datetime
from uuid import UUID

from pydantic import Field

from tempest_fastapi_sdk.core.enums import BaseStrEnum
from tempest_fastapi_sdk.integrations.payment.base import PayoutResult
from tempest_fastapi_sdk.schemas.base import BaseSchema
from tempest_fastapi_sdk.utils.regex import PixKeyType


class WalletEntryKind(BaseStrEnum):
    """Ledger kinds the wallet service writes on its own.

    Credits for a sale, a tip or a commission are the application's
    vocabulary and go in as plain strings; these are the movements the
    service itself creates.

    Attributes:
        WITHDRAW: Debit for a payout.
        WITHDRAW_REFUND: The payout was refused, the debit came back.
        REVERSAL: A previous credit was taken back (a refund to the payer).
        OPENING_BALANCE: First line of a wallet that already had money
            before the ledger existed.
    """

    WITHDRAW = "WITHDRAW"
    WITHDRAW_REFUND = "WITHDRAW_REFUND"
    REVERSAL = "REVERSAL"
    OPENING_BALANCE = "OPENING_BALANCE"


class WalletEntrySchema(BaseSchema):
    """One ledger line, as a statement shows it.

    Attributes:
        id (UUID): The line id.
        user_id (UUID): The wallet owner.
        kind (str): What the movement is.
        amount_cents (int): Signed amount in cents.
        balance_after_cents (int): Balance right after the movement.
        available_at (datetime): When the credit becomes withdrawable.
        description (str): Statement text.
        reference_type (str | None): What the movement refers to.
        reference_id (str | None): Id of that reference.
        created_at (datetime): When the movement happened.
    """

    id: UUID
    user_id: UUID
    kind: str
    amount_cents: int
    balance_after_cents: int
    available_at: datetime
    description: str = ""
    reference_type: str | None = None
    reference_id: str | None = None
    created_at: datetime


class WalletBalanceSchema(BaseSchema):
    """How much of a wallet is withdrawable now.

    Attributes:
        user_id (UUID): The wallet owner.
        total_cents (int): The whole balance.
        held_cents (int): Credits not yet released, capped at the total.
        available_cents (int): ``total_cents - held_cents``.
        next_release_at (datetime | None): When the next held credit is
            released, or ``None`` when nothing is held.
    """

    user_id: UUID
    total_cents: int = Field(ge=0)
    held_cents: int = Field(ge=0)
    available_cents: int = Field(ge=0)
    next_release_at: datetime | None = None


class WithdrawalSchema(BaseSchema):
    """A completed withdrawal: the debit and what the provider answered.

    Attributes:
        entry (WalletEntrySchema): The ``WITHDRAW`` ledger line.
        payout (PayoutResult): The provider's answer.
    """

    entry: WalletEntrySchema
    payout: PayoutResult


class WithdrawRequestSchema(BaseSchema):
    """Body of ``POST {prefix}/withdraw``.

    Attributes:
        amount_cents (int | None): Amount to withdraw; ``None`` takes the
            whole available balance.
    """

    amount_cents: int | None = Field(
        default=None,
        gt=0,
        description="Valor do saque em centavos; vazio saca todo o disponível.",
    )


class PixDestinationSchema(BaseSchema):
    """Where a user's withdrawals go, as read from their profile.

    Attributes:
        pix_key (str): The registered key.
        pix_key_type (PixKeyType): Its kind.
    """

    pix_key: str = Field(min_length=1)
    pix_key_type: PixKeyType


__all__: list[str] = [
    "PixDestinationSchema",
    "WalletBalanceSchema",
    "WalletEntryKind",
    "WalletEntrySchema",
    "WithdrawRequestSchema",
    "WithdrawalSchema",
]
