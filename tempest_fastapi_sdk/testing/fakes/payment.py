"""A Pix provider that charges nobody.

Wire it where :class:`~tempest_fastapi_sdk.integrations.payment.PixProvider`
goes and the whole checkout flow runs with no credential, no sandbox and no
network — including the half that is hard to reach against a real provider:
the payment itself, an expiry, a refund.
"""

from __future__ import annotations

from collections.abc import Mapping
from types import MappingProxyType
from typing import Any

from tempest_fastapi_sdk.integrations.payment.base import (
    PaymentStatus,
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
