"""confirm_pix_payment: re-read the charge, then release once under claim_once."""

from __future__ import annotations

from datetime import datetime

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import Mapped, mapped_column

from tempest_fastapi_sdk import BaseModel
from tempest_fastapi_sdk.db.transaction import transaction
from tempest_fastapi_sdk.integrations.payment import (
    PaymentStatus,
    PixChargeRequest,
    PixConfirmationOutcome,
    PixEventType,
    PixPaymentConfirmation,
    PixPaymentEvent,
    PixProvider,
    confirm_pix_payment,
)
from tempest_fastapi_sdk.testing.fakes import FakePixProvider
from tempest_fastapi_sdk.wallet import claim_once


class _SettledOrder(BaseModel):
    """An order with the claim column a Pix settlement stamps."""

    __tablename__ = "pix_settlement_test_orders"

    reference: Mapped[str] = mapped_column(unique=True)
    amount_cents: Mapped[int] = mapped_column()
    provider_charge_id: Mapped[str | None] = mapped_column(default=None)
    paid_at: Mapped[datetime | None] = mapped_column(nullable=True, default=None)


async def _open(provider: FakePixProvider, reference: str, amount_cents: int) -> str:
    """Open a pending charge on the fake and return its provider-side id.

    Args:
        provider (FakePixProvider): The fake provider.
        reference (str): The order reference.
        amount_cents (int): The charged amount.

    Returns:
        str: The provider charge id.
    """
    charge = await provider.create_pix_charge(
        PixChargeRequest(amount_cents=amount_cents, reference=reference),
    )
    return charge.provider_charge_id


class TestConfirmPixPayment:
    async def test_a_paid_charge_for_the_order_and_amount_is_paid(self) -> None:
        """Only the full match authorizes release."""
        provider = FakePixProvider()
        charge_id = await _open(provider, "order-1", 1990)
        provider.advance(charge_id, PaymentStatus.PAID)

        result = await confirm_pix_payment(
            provider, charge_id, reference="order-1", amount_cents=1990
        )

        assert result.outcome is PixConfirmationOutcome.PAID
        assert result.paid is True
        assert result.charge.status is PaymentStatus.PAID
        assert result.expected_amount_cents == 1990
        assert result.expected_reference == "order-1"

    async def test_it_reads_the_provider_not_the_event(self) -> None:
        """A webhook that says paid is not trusted: the API still says pending."""
        provider = FakePixProvider()
        charge_id = await _open(provider, "order-1", 1990)

        result = await confirm_pix_payment(
            provider, charge_id, reference="order-1", amount_cents=1990
        )

        assert provider.calls == ["create_pix_charge", "get_pix_charge"]
        assert result.outcome is PixConfirmationOutcome.NOT_PAID
        assert result.paid is False

    @pytest.mark.parametrize(
        "status",
        [s for s in PaymentStatus if s is not PaymentStatus.PAID],
    )
    async def test_every_other_status_is_not_paid(self, status: PaymentStatus) -> None:
        """Refunded, charged back, unknown: none of them release."""
        provider = FakePixProvider()
        charge_id = await _open(provider, "order-1", 1990)
        provider.advance(charge_id, status)

        result = await confirm_pix_payment(
            provider, charge_id, reference="order-1", amount_cents=1990
        )

        assert result.outcome is PixConfirmationOutcome.NOT_PAID

    async def test_a_different_amount_is_refused(self) -> None:
        """A paid charge for less than the order costs does not release it."""
        provider = FakePixProvider()
        charge_id = await _open(provider, "order-1", 100)
        provider.advance(charge_id, PaymentStatus.PAID)

        result = await confirm_pix_payment(
            provider, charge_id, reference="order-1", amount_cents=1990
        )

        assert result.outcome is PixConfirmationOutcome.AMOUNT_MISMATCH
        assert result.paid is False

    async def test_a_charge_of_another_order_is_refused_first(self) -> None:
        """Reference is checked before status: someone else's paid charge."""
        provider = FakePixProvider()
        charge_id = await _open(provider, "order-2", 1990)
        provider.advance(charge_id, PaymentStatus.PAID)

        result = await confirm_pix_payment(
            provider, charge_id, reference="order-1", amount_cents=1990
        )

        assert result.outcome is PixConfirmationOutcome.REFERENCE_MISMATCH

    async def test_a_provider_failure_propagates(self) -> None:
        """An unanswered read is not a "no": the caller sees the error."""
        provider = FakePixProvider()
        charge_id = await _open(provider, "order-1", 1990)
        provider.fail_next(TimeoutError("provider down"))

        with pytest.raises(TimeoutError):
            await confirm_pix_payment(
                provider, charge_id, reference="order-1", amount_cents=1990
            )

    def test_the_fake_satisfies_the_contract_it_is_called_with(self) -> None:
        """The helper is typed against PixProvider, which the fake implements."""
        provider: PixProvider = FakePixProvider()
        assert provider.provider_name == "fake"


async def _settle(
    session: AsyncSession,
    provider: PixProvider,
    event: PixPaymentEvent,
    released: list[str],
) -> str | None:
    """The recipe's settle: re-read by the stored id, release under claim_once.

    Args:
        session (AsyncSession): The database session.
        provider (PixProvider): The provider.
        event (PixPaymentEvent): The webhook event (only a trigger).
        released (list[str]): Records each release, to count side effects.

    Returns:
        str | None: The reference released by this call, if any.
    """
    if event.type is not PixEventType.CHARGE_PAID or event.charge is None:
        return None
    order = await session.scalar(
        select(_SettledOrder).where(_SettledOrder.reference == event.charge.reference)
    )
    if order is None or order.provider_charge_id is None:
        return None
    confirmation: PixPaymentConfirmation = await confirm_pix_payment(
        provider,
        order.provider_charge_id,
        reference=order.reference,
        amount_cents=order.amount_cents,
    )
    if not confirmation.paid:
        return None
    async with transaction(session):
        if not await claim_once(session, _SettledOrder, order.id, "paid_at"):
            return None
        released.append(order.reference)
    return order.reference


class TestReleaseOnce:
    async def test_two_paid_events_for_one_order_release_once(
        self, session: AsyncSession
    ) -> None:
        """A webhook retry confirms the same paid charge and finds it claimed."""
        provider = FakePixProvider()
        charge_id = await _open(provider, "order-1", 1990)
        order = _SettledOrder(
            reference="order-1", amount_cents=1990, provider_charge_id=charge_id
        )
        session.add(order)
        await session.commit()

        event = provider.advance(charge_id, PaymentStatus.PAID)
        released: list[str] = []
        first = await _settle(session, provider, event, released)
        second = await _settle(session, provider, event, released)

        assert (first, second) == ("order-1", None)
        assert released == ["order-1"]
        await session.refresh(order)
        assert order.paid_at is not None

    async def test_an_unpaid_charge_never_claims_the_order(
        self, session: AsyncSession
    ) -> None:
        """A forged paid event over a pending charge leaves the column empty."""
        provider = FakePixProvider()
        charge_id = await _open(provider, "order-2", 1990)
        order = _SettledOrder(
            reference="order-2", amount_cents=1990, provider_charge_id=charge_id
        )
        session.add(order)
        await session.commit()

        pending = provider.charges[charge_id]
        forged = PixPaymentEvent(
            provider="fake",
            type=PixEventType.CHARGE_PAID,
            provider_event_name="fake.paid",
            charge=pending,
        )
        released: list[str] = []

        assert await _settle(session, provider, forged, released) is None
        assert released == []
        await session.refresh(order)
        assert order.paid_at is None
