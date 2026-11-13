"""Refund exceptions raised by every :class:`PixRefundProvider` alike."""

from typing import Any, ClassVar

from tempest_fastapi_sdk.exceptions.base import AppException


class PixRefundExceedsBalanceException(AppException):
    """Raised when a refund asks for more than the charge has left.

    "Left" is the charge's amount minus what
    :meth:`~tempest_fastapi_sdk.integrations.payment.PixRefundProvider.get_refunded_cents`
    counts, so a refund still in flight already reduces it. A full refund
    (``amount_cents=None``) of a charge with nothing left raises this too:
    there is nothing to give back, and answering with an empty refund would
    look like one happened.

    Attributes:
        charge_id (str): The charge the refund was addressed to.
        requested_cents (int | None): What was asked for; ``None`` for a
            full refund.
        refundable_cents (int): What the charge had left.
    """

    status_code: int = 409
    message: str = "The refund exceeds what is left of the charge"
    code: str = "PIX_REFUND_EXCEEDS_BALANCE"
    details_example: ClassVar[dict[str, Any]] = {
        "charge_id": "ORD01JABCDEF",
        "requested_cents": 2000,
        "refundable_cents": 1500,
    }

    def __init__(
        self,
        charge_id: str,
        *,
        requested_cents: int | None,
        refundable_cents: int,
    ) -> None:
        """Initialize the exception.

        Args:
            charge_id (str): The charge the refund was addressed to.
            requested_cents (int | None): What was asked for; ``None`` for a
                full refund.
            refundable_cents (int): What the charge had left.
        """
        self.charge_id: str = charge_id
        self.requested_cents: int | None = requested_cents
        self.refundable_cents: int = refundable_cents
        super().__init__(
            details={
                "charge_id": charge_id,
                "requested_cents": requested_cents,
                "refundable_cents": refundable_cents,
            },
        )


class PixRefundReferenceConflictException(AppException):
    """Raised when a refund reference is reused for a different refund.

    The reference is the refund's idempotency key: the same reference with
    the same amount answers the refund that already exists, and the same
    reference with another amount is a second, different refund wearing
    the first one's key. Giving it back silently would let the caller
    believe the new amount went out.

    Attributes:
        charge_id (str): The charge the refund was addressed to.
        refund_reference (str): The reused reference.
    """

    status_code: int = 409
    message: str = "The refund reference was already used for another amount"
    code: str = "PIX_REFUND_REFERENCE_CONFLICT"
    details_example: ClassVar[dict[str, Any]] = {
        "charge_id": "ORD01JABCDEF",
        "refund_reference": "ticket-42-refund",
    }

    def __init__(self, charge_id: str, *, refund_reference: str) -> None:
        """Initialize the exception.

        Args:
            charge_id (str): The charge the refund was addressed to.
            refund_reference (str): The reused reference.
        """
        self.charge_id: str = charge_id
        self.refund_reference: str = refund_reference
        super().__init__(
            details={"charge_id": charge_id, "refund_reference": refund_reference},
        )


__all__: list[str] = [
    "PixRefundExceedsBalanceException",
    "PixRefundReferenceConflictException",
]
