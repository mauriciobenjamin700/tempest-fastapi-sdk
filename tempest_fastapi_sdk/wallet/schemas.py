"""Pydantic DTOs for the wallet module."""

from __future__ import annotations

from datetime import datetime
from enum import StrEnum
from uuid import UUID

from pydantic import Field

from tempest_fastapi_sdk.schemas.base import BaseSchema


class WalletEntryKind(StrEnum):
    """The movement kinds the wallet service writes on its own.

    ``kind`` is a free string column, so an application may post its
    own kinds (``"sale"``, ``"tip"``) through the service's ``kind``
    argument; these are the defaults and the one kind the service
    reserves for :meth:`~tempest_fastapi_sdk.wallet.WalletService.reverse`.
    """

    CREDIT = "credit"
    DEBIT = "debit"
    REVERSAL = "reversal"


class WalletEntrySchema(BaseSchema):
    """One statement line as returned to callers.

    Attributes:
        id (UUID): The entry id.
        user_id (UUID): The wallet owner.
        kind (str): The movement kind.
        amount_cents (int): Signed amount in cents.
        balance_after_cents (int): The balance right after the movement.
        available_at (datetime): When the movement stops being held.
        reference_type (str): Kind of the event that caused it.
        reference_id (UUID): Id of that event.
        description (str | None): Free text for the statement line.
        idempotency_key (str | None): The caller-supplied key, if any.
        created_at (datetime): When the movement was posted.
    """

    id: UUID
    user_id: UUID
    kind: str
    amount_cents: int
    balance_after_cents: int
    available_at: datetime
    reference_type: str
    reference_id: UUID
    description: str | None = None
    idempotency_key: str | None = None
    created_at: datetime


class WalletBalanceSchema(BaseSchema):
    """A wallet's balance, split into what can be spent and what is held.

    Attributes:
        total_cents (int): The cached balance on the owner's row.
        held_cents (int): The signed sum of entries whose
            ``available_at`` is still in the future.
        available_cents (int): ``total_cents - held_cents`` — what a
            debit can take right now.
        next_release_at (datetime | None): The earliest future
            ``available_at`` of a held credit, or ``None`` when nothing
            is held.
    """

    total_cents: int = Field(description="The cached balance on the owner's row.")
    held_cents: int = Field(description="Credits still inside their hold.")
    available_cents: int = Field(description="What a debit can take right now.")
    next_release_at: datetime | None = Field(
        default=None,
        description="When the next held credit becomes available.",
    )


__all__: list[str] = [
    "WalletBalanceSchema",
    "WalletEntryKind",
    "WalletEntrySchema",
]
