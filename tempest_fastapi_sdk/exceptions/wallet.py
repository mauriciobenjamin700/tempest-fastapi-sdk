"""Wallet and payout exceptions raised by :mod:`tempest_fastapi_sdk.wallet`."""

from tempest_fastapi_sdk.exceptions.base import AppException


class InsufficientBalanceException(AppException):
    """Raised when a debit asks for more than the wallet can give.

    For a withdrawal "can give" means the **available** balance — the
    balance minus credits still on hold — not the whole balance.
    """

    status_code: int = 409
    message: str = "Insufficient wallet balance"
    code: str = "WALLET_INSUFFICIENT_BALANCE"


class PayoutRejectedException(AppException):
    """Raised when the payout provider definitively refused the transfer.

    Definitive means the money did not leave: the provider answered with a
    refusal, so the wallet debit can be returned safely.
    :meth:`~tempest_fastapi_sdk.wallet.WalletService.withdraw` does that
    before re-raising.
    """

    status_code: int = 502
    message: str = "Payout rejected by the provider"
    code: str = "WALLET_PAYOUT_REJECTED"


class PayoutUncertainException(AppException):
    """Raised when the payout outcome is unknown.

    A timeout, a dropped connection or an answer that cannot be read: the
    money may or may not have left. The wallet debit is **kept** — giving
    it back would let the same balance be paid twice — and the case is
    logged ``CRITICAL`` for reconciliation against the provider.
    """

    status_code: int = 502
    message: str = "Payout outcome unknown"
    code: str = "WALLET_PAYOUT_UNCERTAIN"


class ManualPayoutNotFoundException(AppException):
    """Raised when no manual payout row has the given id."""

    status_code: int = 404
    message: str = "Manual payout not found"
    code: str = "WALLET_MANUAL_PAYOUT_NOT_FOUND"


class ManualPayoutNotPendingException(AppException):
    """Raised when a manual payout is no longer ``PENDING``.

    Another administrator (or a retry of the same request) already marked
    it paid or refused it, or the e-mail to the administrators failed and
    it was canceled. Nothing changed: the status, the wallet and the hooks
    were left as they were.
    """

    status_code: int = 409
    message: str = "Manual payout is no longer pending"
    code: str = "WALLET_MANUAL_PAYOUT_NOT_PENDING"


class ManualPayoutReceiptMissingException(AppException):
    """Raised when a payout must carry a receipt to be marked paid and has none."""

    status_code: int = 409
    message: str = "Manual payout has no receipt attached"
    code: str = "WALLET_MANUAL_PAYOUT_RECEIPT_MISSING"


__all__: list[str] = [
    "InsufficientBalanceException",
    "ManualPayoutNotFoundException",
    "ManualPayoutNotPendingException",
    "ManualPayoutReceiptMissingException",
    "PayoutRejectedException",
    "PayoutUncertainException",
]
