"""Refusals of the wallet module that carry their own ``code``."""

from tempest_fastapi_sdk.exceptions.conflict import ConflictException
from tempest_fastapi_sdk.exceptions.not_found import NotFoundException


class WalletInsufficientFundsException(ConflictException):
    """Raised when a debit is larger than what the wallet can give.

    Two paths end here. :meth:`~tempest_fastapi_sdk.wallet.WalletService.debit`
    raises it when the conditional ``UPDATE`` matched no row because the
    **available** balance (total minus held) is below the amount. And
    :meth:`~tempest_fastapi_sdk.wallet.WalletService.reverse` raises it
    when a forced debit would take a
    :class:`~tempest_fastapi_sdk.wallet.WalletBalanceMixin` balance below
    zero and the database's ``CHECK`` refused the statement.

    Nothing was written either way: the balance and the statement are
    unchanged.
    """

    message: str = "Insufficient available wallet balance"
    code: str = "WALLET_INSUFFICIENT_FUNDS"


class WalletNotFoundException(NotFoundException):
    """Raised when the wallet owner the call names has no row."""

    message: str = "Wallet not found"
    code: str = "WALLET_NOT_FOUND"


class WalletEntryNotFoundException(NotFoundException):
    """Raised when the statement line a reversal names does not exist."""

    message: str = "Wallet entry not found"
    code: str = "WALLET_ENTRY_NOT_FOUND"


class WalletReferenceConflictException(ConflictException):
    """Raised when a replayed reference arrives with a different movement.

    A movement is identified by ``(reference_type, reference_id, kind)``
    or by its ``idempotency_key``. The same reference with the same
    owner and amount is a replay and returns the original entry; the
    same reference with another owner or amount is not a replay — it is
    two events claiming one identity, and guessing which one is right
    would move money on a guess.
    """

    message: str = "This reference already moved a different amount or another wallet"
    code: str = "WALLET_REFERENCE_CONFLICT"


__all__: list[str] = [
    "WalletEntryNotFoundException",
    "WalletInsufficientFundsException",
    "WalletNotFoundException",
    "WalletReferenceConflictException",
]
