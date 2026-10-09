"""Confirm a Pix payment against the provider before releasing anything.

A webhook delivery is a notice, not an authorization. Its signature proves
the provider sent it — and, at OpenPix, a RSA-1024 signature with no
freshness window proves that for a captured delivery forever. What
authorizes releasing an order is the provider answering, **now**, that the
charge the service stored is paid, for the order it belongs to, in the
amount the order costs.

That check is mechanical and always has the same answer, which is why it
lives here instead of in a recipe warning: re-read the charge with
:meth:`~tempest_fastapi_sdk.integrations.payment.base.PixProvider.get_pix_charge`,
then compare reference, status and amount. :func:`confirm_pix_payment`
does exactly that and says which check failed.

Releasing once is the other half, and it is a database question:
:func:`~tempest_fastapi_sdk.wallet.claim_once` stamps the order inside the
same transaction as the effects, so a webhook retry that confirms the same
paid charge again finds the order already claimed.
"""

from __future__ import annotations

from pydantic import Field

from tempest_fastapi_sdk import BaseStrEnum
from tempest_fastapi_sdk.integrations.payment.base import (
    PaymentStatus,
    PixCharge,
    PixProvider,
    _EnumSafeSchema,
)


class PixConfirmationOutcome(BaseStrEnum):
    """Why a re-read charge does or does not authorize releasing an order.

    The checks run in this order, and the first one that fails names the
    outcome: a charge for another order is reported as
    :attr:`REFERENCE_MISMATCH` even if it is also unpaid.

    Attributes:
        PAID (str): The charge belongs to the order, is paid, and carries
            the order's amount. The only outcome that authorizes release.
        REFERENCE_MISMATCH (str): The charge's ``reference`` is not the
            order's — the stored charge id points at someone else's charge.
        NOT_PAID (str): The provider does not report the charge as
            :attr:`~tempest_fastapi_sdk.integrations.payment.base.PaymentStatus.PAID`.
            The current status is in :attr:`PixPaymentConfirmation.charge`.
        AMOUNT_MISMATCH (str): Paid, but not the amount the order costs.
    """

    PAID = "paid"
    REFERENCE_MISMATCH = "reference_mismatch"
    NOT_PAID = "not_paid"
    AMOUNT_MISMATCH = "amount_mismatch"


class PixPaymentConfirmation(_EnumSafeSchema):
    """The result of re-reading a charge against what the order expects.

    Attributes:
        outcome (PixConfirmationOutcome): Which check decided the result.
        charge (PixCharge): The charge as the provider reported it on this
            read — not the copy that arrived in the webhook.
        expected_reference (str): The order reference the charge had to
            carry.
        expected_amount_cents (int): The amount, in cents, the charge had
            to carry.
    """

    outcome: PixConfirmationOutcome = Field(
        description="Qual conferência decidiu o resultado."
    )
    charge: PixCharge = Field(description="A cobrança como o provedor a relê.")
    expected_reference: str = Field(description="Referência que o pedido espera.")
    expected_amount_cents: int = Field(
        description="Valor, em centavos, que o pedido espera."
    )

    @property
    def paid(self) -> bool:
        """Whether the confirmation authorizes releasing the order.

        Returns:
            bool: ``True`` only for :attr:`PixConfirmationOutcome.PAID`.
        """
        return self.outcome is PixConfirmationOutcome.PAID


async def confirm_pix_payment(
    provider: PixProvider,
    charge_id: str,
    *,
    reference: str,
    amount_cents: int,
) -> PixPaymentConfirmation:
    """Re-read a charge and check it pays this order, in full.

    Pass the charge id **the service stored** when it opened the charge,
    not the one a webhook delivery names: the delivery is only the trigger
    to look, so a forged or replayed one can at most make the service ask
    the provider about a charge it already owns.

    The amount must match exactly. A charge that settled for another
    amount — a different charge, or one whose value the provider adjusted —
    is a decision for the service, not something to release silently at
    the wrong price.

    Nothing is written. Release the order under
    :func:`~tempest_fastapi_sdk.wallet.claim_once` when this returns
    :attr:`PixPaymentConfirmation.paid`, so the same paid charge confirmed
    twice — a webhook retry, a reconciliation sweep — releases once.

    Args:
        provider (PixProvider): Any provider that implements the contract.
        charge_id (str): The stored
            :attr:`~tempest_fastapi_sdk.integrations.payment.base.PixCharge.provider_charge_id`.
        reference (str): The order's reference, sent as
            :attr:`~tempest_fastapi_sdk.integrations.payment.base.PixChargeRequest.reference`
            when the charge was created.
        amount_cents (int): What the order costs, in cents.

    Returns:
        PixPaymentConfirmation: The outcome and the charge as read.

    Raises:
        Exception: Whatever the provider's ``get_pix_charge`` raises
            (timeout, unknown charge) propagates: an unanswered question is
            not a "no", and the caller must not treat it as one.
    """
    charge = await provider.get_pix_charge(charge_id)
    if charge.reference != reference:
        outcome = PixConfirmationOutcome.REFERENCE_MISMATCH
    elif charge.status is not PaymentStatus.PAID:
        outcome = PixConfirmationOutcome.NOT_PAID
    elif charge.amount_cents != amount_cents:
        outcome = PixConfirmationOutcome.AMOUNT_MISMATCH
    else:
        outcome = PixConfirmationOutcome.PAID
    return PixPaymentConfirmation(
        outcome=outcome,
        charge=charge,
        expected_reference=reference,
        expected_amount_cents=amount_cents,
    )


__all__: list[str] = [
    "PixConfirmationOutcome",
    "PixPaymentConfirmation",
    "confirm_pix_payment",
]
