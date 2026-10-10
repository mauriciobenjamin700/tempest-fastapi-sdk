"""Payment providers that charge nobody.

Wire :class:`FakePixProvider` where
:class:`~tempest_fastapi_sdk.integrations.payment.PixProvider` goes, or
:class:`FakeCardProvider` where
:class:`~tempest_fastapi_sdk.integrations.payment.CardProvider` goes, and the
whole checkout flow runs with no credential, no sandbox and no network —
including the half that is hard to reach against a real provider: the payment
itself, a decline, an expiry, a refund.
"""

from __future__ import annotations

from collections import deque
from collections.abc import Mapping
from types import MappingProxyType
from typing import Any

from tempest_fastapi_sdk.integrations.payment.base import (
    CardCharge,
    CardChargeRequest,
    PaymentStatus,
    PayoutRequest,
    PayoutResult,
    PayoutStatus,
    PixCharge,
    PixChargeRequest,
    PixEventType,
    PixPaymentEvent,
)
from tempest_fastapi_sdk.testing.fakes._control import _Steerable

_STATUS_EVENTS: dict[PaymentStatus, PixEventType] = {
    PaymentStatus.PAID: PixEventType.CHARGE_PAID,
    PaymentStatus.EXPIRED: PixEventType.CHARGE_EXPIRED,
    PaymentStatus.CANCELLED: PixEventType.CHARGE_CANCELLED,
    PaymentStatus.REFUNDED: PixEventType.CHARGE_REFUNDED,
}


class FakePixProvider(_Steerable):
    """A ``PixProvider`` that keeps charges in a dict.

    Example:

        >>> provider = FakePixProvider()
        >>> charge = await provider.create_pix_charge(
        ...     PixChargeRequest(amount_cents=1990, reference="order-1"),
        ... )
        >>> event = provider.advance(charge.provider_charge_id, PaymentStatus.PAID)
        >>> event.type is PixEventType.CHARGE_PAID
        True

    Attributes:
        provider_name (str): Copied into :attr:`PixCharge.provider`.
        calls (list[str]): Contract methods that ran, in order.
    """

    provider_name: str = "fake"

    def __init__(self, *, provider_name: str = "fake") -> None:
        """Start with no charges.

        Args:
            provider_name (str): The name to stamp on every charge, when a
                test asserts on more than one provider at a time.
        """
        super().__init__()
        self.provider_name = provider_name
        self._charges: dict[str, PixCharge] = {}
        self._next_id: int = 1

    @property
    def charges(self) -> Mapping[str, PixCharge]:
        """Every charge this provider issued, keyed by provider-side id.

        Returns:
            Mapping[str, PixCharge]: A read-only view, so a test reads state
            without being able to corrupt it by accident.
        """
        return MappingProxyType(self._charges)

    async def create_pix_charge(self, request: PixChargeRequest) -> PixCharge:
        """Issue a pending charge.

        Args:
            request (PixChargeRequest): What the service asked to charge.

        Returns:
            PixCharge: The charge, in canonical shape.

        Raises:
            BaseException: Whatever :meth:`fail_next` queued.
        """
        self._record("create_pix_charge")
        charge_id = f"{self.provider_name}-{self._next_id}"
        self._next_id += 1
        charge = PixCharge(
            provider=self.provider_name,
            provider_charge_id=charge_id,
            reference=request.reference,
            amount_cents=request.amount_cents,
            status=PaymentStatus.PENDING,
            provider_status="created",
            br_code=f"000201{charge_id}",
            expires_at=None,
        )
        self._charges[charge_id] = charge
        return charge

    async def get_pix_charge(self, charge_id: str) -> PixCharge:
        """Read a charge back.

        Args:
            charge_id (str): The provider-side id.

        Returns:
            PixCharge: The stored charge.

        Raises:
            KeyError: When no charge carries that id.
            BaseException: Whatever :meth:`fail_next` queued.
        """
        self._record("get_pix_charge")
        return self._charges[charge_id]

    async def cancel_pix_charge(self, charge_id: str) -> PixCharge:
        """Withdraw a charge.

        Args:
            charge_id (str): The provider-side id.

        Returns:
            PixCharge: The charge in its cancelled shape.

        Raises:
            KeyError: When no charge carries that id.
            BaseException: Whatever :meth:`fail_next` queued.
        """
        self._record("cancel_pix_charge")
        return self._transition(charge_id, PaymentStatus.CANCELLED)

    def parse_webhook(self, event: Any) -> PixPaymentEvent:
        """Turn this fake's delivery shape into a canonical event.

        Args:
            event (Any): A mapping with ``charge_id`` and, optionally,
                ``status`` (a :class:`PaymentStatus` or its value). Defaults
                to a paid event, because that is the delivery a checkout
                test is usually after.

        Returns:
            PixPaymentEvent: The canonical event.

        Raises:
            KeyError: When no charge carries that id.
            BaseException: Whatever :meth:`fail_next` queued.
        """
        self._record("parse_webhook")
        charge_id = str(event["charge_id"])
        status = PaymentStatus(event.get("status", PaymentStatus.PAID))
        charge = self._transition(charge_id, status)
        return PixPaymentEvent(
            provider=self.provider_name,
            type=_STATUS_EVENTS.get(status, PixEventType.UNKNOWN),
            provider_event_name=f"fake.{status.value}",
            charge=charge,
            raw=dict(event),
        )

    def advance(self, charge_id: str, status: PaymentStatus) -> PixPaymentEvent:
        """Move a charge to ``status`` and return the event that reports it.

        Args:
            charge_id (str): The provider-side id.
            status (PaymentStatus): The state to move to.

        Returns:
            PixPaymentEvent: The event a real provider's webhook would have
            delivered for that transition.

        Raises:
            KeyError: When no charge carries that id.

        This is the steering the real provider does not give you: reaching
        ``PAID`` against a sandbox means somebody scanning a QR code, and
        reaching ``CHARGED_BACK`` means somebody disputing a payment. Here it
        is one call, and it does **not** consume a queued
        :meth:`fail_next` — steering the fake is not a call the service made.
        """
        charge = self._transition(charge_id, status)
        return PixPaymentEvent(
            provider=self.provider_name,
            type=_STATUS_EVENTS.get(status, PixEventType.UNKNOWN),
            provider_event_name=f"fake.{status.value}",
            charge=charge,
            raw={"charge_id": charge_id, "status": status.value},
        )

    def _transition(self, charge_id: str, status: PaymentStatus) -> PixCharge:
        """Store a charge in a new state and return it.

        Args:
            charge_id (str): The provider-side id.
            status (PaymentStatus): The state to move to.

        Returns:
            PixCharge: The updated charge.

        Raises:
            KeyError: When no charge carries that id.
        """
        charge = self._charges[charge_id]
        updated = charge.model_copy(
            update={"status": status, "provider_status": status.value},
        )
        self._charges[charge_id] = updated
        return updated


class FakePayoutProvider(_Steerable):
    """A ``PayoutProvider`` that sends nothing and remembers every transfer.

    Steer the two failure branches a withdrawal has with :meth:`fail_next`:
    ``PayoutRejectedException`` (the money did not leave, the wallet gives
    the debit back) and any other exception, such as ``TimeoutError`` (the
    outcome is unknown, the debit is kept).

    Example:
        >>> payout = FakePayoutProvider()
        >>> result = await payout.transfer_to_pix_key(
        ...     PayoutRequest(
        ...         amount_cents=5000,
        ...         pix_key="driver@example.com",
        ...         pix_key_type=PixKeyType.EMAIL,
        ...         correlation_id="w-1",
        ...     ),
        ... )
        >>> result.status is PayoutStatus.CONFIRMED
        True

    Attributes:
        provider_name (str): Copied into :attr:`PayoutResult.provider`.
        status (PayoutStatus): What every accepted transfer reports.
        transfers (list[PayoutRequest]): Accepted transfers, in order.
        calls (list[str]): Contract methods that ran, in order.
    """

    provider_name: str = "fake"

    def __init__(self, *, status: PayoutStatus = PayoutStatus.CONFIRMED) -> None:
        """Start with no transfers.

        Args:
            status (PayoutStatus): The status every accepted transfer gets.
        """
        super().__init__()
        self.status: PayoutStatus = status
        self.transfers: list[PayoutRequest] = []

    async def transfer_to_pix_key(self, request: PayoutRequest, /) -> PayoutResult:
        """Accept a transfer, unless a failure was queued.

        Args:
            request (PayoutRequest): The transfer.

        Returns:
            PayoutResult: The accepted transfer, with :attr:`status`.

        Raises:
            BaseException: Whatever :meth:`fail_next` queued. A failed call
                is not added to :attr:`transfers`.
        """
        self._record("transfer_to_pix_key")
        self.transfers.append(request)
        return PayoutResult(
            correlation_id=request.correlation_id,
            status=self.status,
            provider=self.provider_name,
            provider_status=self.status.value,
        )


class FakeCardProvider(_Steerable):
    """A ``CardProvider`` that keeps charges in a dict.

    A charge is approved unless :meth:`decline_next` queued a decline, which
    comes back as a :attr:`PaymentStatus.FAILED` charge — returned, not
    raised, as the real adapter returns the provider's HTTP 402. A
    ``capture=False`` request comes back :attr:`PaymentStatus.AUTHORIZED`.

    The fake refuses the transitions a real provider refuses, with
    ``ValueError``: capturing or cancelling anything but an authorization,
    refunding anything but a paid charge, refunding more than is left. The
    real adapter raises ``httpx.HTTPStatusError`` there instead; what this
    fake guarantees is that the branch is reachable, not the exception type.

    Example:

        >>> provider = FakeCardProvider()
        >>> charge = await provider.create_card_charge(
        ...     CardChargeRequest(
        ...         amount_cents=10000,
        ...         reference="order-1",
        ...         card_token="tok",
        ...         payment_method_id="visa",
        ...     ),
        ... )
        >>> refunded = await provider.refund_card_charge(
        ...     charge.provider_charge_id, amount_cents=3000
        ... )
        >>> (refunded.status, refunded.refunded_cents)
        (<PaymentStatus.PAID: 'paid'>, 3000)

    Attributes:
        provider_name (str): Copied into :attr:`CardCharge.provider`.
        calls (list[str]): Contract methods that ran, in order.
    """

    provider_name: str = "fake"

    def __init__(self, *, provider_name: str = "fake") -> None:
        """Start with no charges and no declines queued.

        Args:
            provider_name (str): The name to stamp on every charge.
        """
        super().__init__()
        self.provider_name = provider_name
        self._charges: dict[str, CardCharge] = {}
        self._declines: deque[str] = deque()
        self._next_id: int = 1

    @property
    def charges(self) -> Mapping[str, CardCharge]:
        """Every charge this provider issued, keyed by provider-side id.

        Returns:
            Mapping[str, CardCharge]: A read-only view.
        """
        return MappingProxyType(self._charges)

    def decline_next(self, status_detail: str = "rejected_by_issuer") -> None:
        """Make the next :meth:`create_card_charge` come back declined.

        Args:
            status_detail (str): The reason, copied into
                :attr:`CardCharge.status_detail`. The default is the one the
                Mercado Pago sandbox answered for a declined test card.

        Queue several to decline several creates in a row. Unlike
        :meth:`fail_next`, the create still answers: a decline is a result.
        """
        self._declines.append(status_detail)

    async def create_card_charge(self, request: CardChargeRequest) -> CardCharge:
        """Approve, authorize or decline a charge.

        Args:
            request (CardChargeRequest): What the service asked to charge.

        Returns:
            CardCharge: ``PAID``, ``AUTHORIZED`` when ``capture=False``, or
            ``FAILED`` when :meth:`decline_next` queued a decline.

        Raises:
            BaseException: Whatever :meth:`fail_next` queued.
        """
        self._record("create_card_charge")
        charge_id = f"{self.provider_name}-{self._next_id}"
        self._next_id += 1
        status_detail: str | None
        if self._declines:
            status = PaymentStatus.FAILED
            status_detail = self._declines.popleft()
        elif request.capture:
            status, status_detail = PaymentStatus.PAID, "accredited"
        else:
            status, status_detail = PaymentStatus.AUTHORIZED, "waiting_capture"
        charge = CardCharge(
            provider=self.provider_name,
            provider_charge_id=charge_id,
            reference=request.reference,
            amount_cents=request.amount_cents,
            status=status,
            provider_status=status.value,
            status_detail=status_detail,
            payment_method_id=request.payment_method_id,
            installments=request.installments,
        )
        self._charges[charge_id] = charge
        return charge

    async def get_card_charge(self, charge_id: str) -> CardCharge:
        """Read a charge back.

        Args:
            charge_id (str): The provider-side id.

        Returns:
            CardCharge: The stored charge.

        Raises:
            KeyError: When no charge carries that id.
            BaseException: Whatever :meth:`fail_next` queued.
        """
        self._record("get_card_charge")
        return self._charges[charge_id]

    async def capture_card_charge(self, charge_id: str) -> CardCharge:
        """Capture an authorization.

        Args:
            charge_id (str): The provider-side id.

        Returns:
            CardCharge: The charge, ``PAID``.

        Raises:
            KeyError: When no charge carries that id.
            ValueError: When the charge is not ``AUTHORIZED``.
            BaseException: Whatever :meth:`fail_next` queued.
        """
        self._record("capture_card_charge")
        self._require(charge_id, PaymentStatus.AUTHORIZED, "capture")
        return self.advance(charge_id, PaymentStatus.PAID, status_detail="accredited")

    async def cancel_card_charge(self, charge_id: str) -> CardCharge:
        """Release an authorization.

        Args:
            charge_id (str): The provider-side id.

        Returns:
            CardCharge: The charge, ``CANCELLED``.

        Raises:
            KeyError: When no charge carries that id.
            ValueError: When the charge is not ``AUTHORIZED``.
            BaseException: Whatever :meth:`fail_next` queued.
        """
        self._record("cancel_card_charge")
        self._require(charge_id, PaymentStatus.AUTHORIZED, "cancel")
        return self.advance(charge_id, PaymentStatus.CANCELLED, status_detail=None)

    async def refund_card_charge(
        self, charge_id: str, amount_cents: int | None = None
    ) -> CardCharge:
        """Refund a paid charge in full or in part.

        Args:
            charge_id (str): The provider-side id.
            amount_cents (int | None): How much to refund; ``None`` refunds
                what is left.

        Returns:
            CardCharge: ``PAID`` with ``status_detail`` ``partially_refunded``
            while something is left, ``REFUNDED`` once nothing is — the
            states the Mercado Pago sandbox reported.

        Raises:
            KeyError: When no charge carries that id.
            ValueError: When the charge is not ``PAID``, or the amount is not
                positive or exceeds what is left.
            BaseException: Whatever :meth:`fail_next` queued.
        """
        self._record("refund_card_charge")
        charge = self._require(charge_id, PaymentStatus.PAID, "refund")
        left = charge.amount_cents - charge.refunded_cents
        amount = left if amount_cents is None else amount_cents
        if amount <= 0 or amount > left:
            raise ValueError(
                f"Cannot refund {amount} cents of {charge_id!r}: {left} left."
            )
        refunded = charge.refunded_cents + amount
        done = refunded == charge.amount_cents
        updated = charge.model_copy(
            update={
                "refunded_cents": refunded,
                "status": PaymentStatus.REFUNDED if done else PaymentStatus.PAID,
                "provider_status": (
                    PaymentStatus.REFUNDED.value if done else PaymentStatus.PAID.value
                ),
                "status_detail": "refunded" if done else "partially_refunded",
            }
        )
        self._charges[charge_id] = updated
        return updated

    def advance(
        self,
        charge_id: str,
        status: PaymentStatus,
        *,
        status_detail: str | None = None,
    ) -> CardCharge:
        """Move a charge to ``status``, with no transition check.

        Args:
            charge_id (str): The provider-side id.
            status (PaymentStatus): The state to move to — say
                :attr:`PaymentStatus.CHARGED_BACK`, which no contract method
                reaches.
            status_detail (str | None): The reason to store.

        Returns:
            CardCharge: The updated charge.

        Raises:
            KeyError: When no charge carries that id.

        Steering, not a call the service made: it does not consume a queued
        :meth:`fail_next`.
        """
        updated = self._charges[charge_id].model_copy(
            update={
                "status": status,
                "provider_status": status.value,
                "status_detail": status_detail,
            }
        )
        self._charges[charge_id] = updated
        return updated

    def _require(
        self, charge_id: str, status: PaymentStatus, action: str
    ) -> CardCharge:
        """Read a charge and refuse the action unless it is in ``status``.

        Args:
            charge_id (str): The provider-side id.
            status (PaymentStatus): The state the action needs.
            action (str): The action's name, for the message.

        Returns:
            CardCharge: The charge.

        Raises:
            KeyError: When no charge carries that id.
            ValueError: When the charge is in another state.
        """
        charge = self._charges[charge_id]
        if charge.status is not status:
            raise ValueError(
                f"Cannot {action} {charge_id!r}: it is {charge.status.value}, "
                f"not {status.value}."
            )
        return charge
