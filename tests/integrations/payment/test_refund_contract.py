"""The refund contract, run against every ``PixRefundProvider`` alike.

Three providers implement :class:`PixRefundProvider`: the Mercado Pago and
OpenPix adapters, and ``FakePixProvider``. A service written against the
protocol must get the same answer from each one to the same questions:

* the same ``refund_reference`` never refunds twice — not on a plain retry,
  and not after the first answer was lost with the refund already applied;
* ``get_refunded_cents`` sums partial refunds;
* a refund above what is left raises
  :class:`PixRefundExceedsBalanceException`;
* the same reference with another amount raises
  :class:`PixRefundReferenceConflictException`.

The adapters run over ``httpx.MockTransport`` against small **simulated**
providers, not captured bodies: the property under test is a sequence of
calls with state between them, and a list of canned answers cannot answer
the second call according to the first. What each simulator models, and
where that model comes from, is in its docstring; the parts that were not
measured are said to be so. The bodies the adapters read are shaped like
the captured ones (``fixtures/mercado_pago_orders``, the OpenPix
specification's ``ChargeRefund``).
"""

from __future__ import annotations

import contextlib
import json
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import Any

import httpx
import pytest

from tempest_fastapi_sdk import HTTPClient
from tempest_fastapi_sdk.exceptions import (
    PixRefundExceedsBalanceException,
    PixRefundReferenceConflictException,
)
from tempest_fastapi_sdk.integrations.payment import (
    PaymentStatusEnum,
    PixChargeRequest,
    PixPayer,
    PixRefund,
    PixRefundProvider,
    PixRefundStatusEnum,
)
from tempest_fastapi_sdk.integrations.payment.adapters.mercado_pago import (
    MercadoPagoPixProvider,
)
from tempest_fastapi_sdk.integrations.payment.adapters.openpix import (
    OpenPixPixProvider,
)
from tempest_fastapi_sdk.integrations.payment.openpix import (
    OpenPixClient,
    OpenPixEnvironmentEnum,
)
from tempest_fastapi_sdk.testing.fakes import (
    FakeMercadoPagoOrdersAPI,
    FakePixProvider,
)
from tempest_fastapi_sdk.utils.retry import RetryPolicy

TOTAL_CENTS: int = 1990
"""Every harness starts from one paid charge of R$ 19,90."""


class OpenPixSimulator:
    """A completed OpenPix charge with ``correlationID``-keyed refunds.

    The routes and shapes are the specification's (``refundCharge``,
    ``listChargeRefunds``, ``ChargeRefund``). What OpenPix answers to a
    repeated refund ``correlationID`` and to a refund above the balance is
    not documented; the simulator answers ``400`` to both, which the
    adapter never relies on.

    Attributes:
        refunds (list[dict[str, Any]]): The refunds applied.
        lose_next_answer (bool): Apply the next refund, then time out.
        posts (int): How many refund ``POST`` requests arrived.
    """

    global_id: str = "Q2hhcmdlOjEyMw"
    correlation_id: str = "order-1"

    def __init__(self) -> None:
        """Start with one completed charge and no refund."""
        self.refunds: list[dict[str, Any]] = []
        self.lose_next_answer: bool = False
        self.posts: int = 0

    def __call__(self, request: httpx.Request) -> httpx.Response:
        """Answer one request.

        Args:
            request (httpx.Request): The request.

        Returns:
            httpx.Response: The answer.

        Raises:
            httpx.ReadTimeout: After applying a refund, when asked to lose
                the answer.
        """
        path = request.url.path
        if request.method == "GET" and path.endswith("/refund"):
            assert path == f"/api/v1/charge/{self.correlation_id}/refund"
            return httpx.Response(200, json={"refunds": list(self.refunds)})
        if request.method == "GET":
            return httpx.Response(
                200,
                json={
                    "charge": {
                        "value": TOTAL_CENTS,
                        "globalID": self.global_id,
                        "correlationID": self.correlation_id,
                        "status": "COMPLETED",
                    }
                },
            )
        assert path == f"/api/v1/charge/{self.correlation_id}/refund"
        self.posts += 1
        body = json.loads(request.content)
        left = TOTAL_CENTS - sum(r["value"] for r in self.refunds)
        if any(r["correlationID"] == body["correlationID"] for r in self.refunds):
            return httpx.Response(400, json={"error": "duplicated correlationID"})
        if body["value"] > left:
            return httpx.Response(400, json={"error": "value above the balance"})
        refund = {
            "value": body["value"],
            "status": "CONFIRMED",
            "correlationID": body["correlationID"],
            "refundId": f"refund-{len(self.refunds) + 1}",
            "endToEndId": f"D0000000020261113{len(self.refunds) + 1:011d}",
            "time": "2026-11-13T12:00:00.000Z",
        }
        self.refunds.append(refund)
        if self.lose_next_answer:
            self.lose_next_answer = False
            raise httpx.ReadTimeout("answer lost", request=request)
        return httpx.Response(200, json={"refund": refund})


@dataclass
class Harness:
    """One provider under the contract, with a paid charge ready.

    Attributes:
        provider (PixRefundProvider): The provider.
        charge_id (str): The paid charge's provider-side id.
        applied (Callable[[], int]): How many refunds really happened.
        lose_next_answer (Callable[[], None]): Make the next refund apply
            and then fail on its way back.
    """

    provider: PixRefundProvider
    charge_id: str
    applied: Callable[[], int]
    lose_next_answer: Callable[[], None]


def _no_retry_http(base_url: str, handler: Callable[..., httpx.Response]) -> HTTPClient:
    """Build an ``HTTPClient`` that surfaces the first failure.

    A lost answer must reach the caller for the contract's retry to be the
    caller's; with the default policy the client would retry the timeout
    itself, under the same key.

    Args:
        base_url (str): The provider's host.
        handler (Callable[..., httpx.Response]): The simulator.

    Returns:
        HTTPClient: The client.
    """
    return HTTPClient(
        base_url=base_url,
        transport=httpx.MockTransport(handler),
        retry_policy=RetryPolicy(max_attempts=1),
    )


async def _fake() -> Harness:
    """Build the fake with a paid charge.

    Returns:
        Harness: The harness.
    """
    fake = FakePixProvider()
    charge = await fake.create_pix_charge(
        PixChargeRequest(amount_cents=TOTAL_CENTS, reference="order-1")
    )
    fake.advance(charge.provider_charge_id, PaymentStatusEnum.PAID)
    return Harness(
        provider=fake,
        charge_id=charge.provider_charge_id,
        applied=lambda: len(fake.refunds.get(charge.provider_charge_id, ())),
        lose_next_answer=lambda: fake.fail_after_next_refund(TimeoutError("lost")),
    )


async def _mercado_pago() -> Harness:
    """Build the Mercado Pago adapter over :class:`FakeMercadoPagoOrdersAPI`.

    The simulator replays a refund under a used ``X-Idempotency-Key`` and
    answers ``409 idempotency_key_already_used`` for another body — measured
    on the create, assumed for the refund. Its refusal of a refund above the
    balance (``400 invalid_refund_amount``) is **not** known; the adapter is
    written not to depend on the code.

    Returns:
        Harness: The harness.
    """
    api = FakeMercadoPagoOrdersAPI()
    provider = MercadoPagoPixProvider(
        api.http_client(retry_policy=RetryPolicy(max_attempts=1)),
        action_retry_delays=(),
    )
    charge = await provider.create_pix_charge(
        PixChargeRequest(
            amount_cents=TOTAL_CENTS,
            reference="order-1",
            payer=PixPayer(email="comprador@example.com"),
        )
    )
    api.pay(charge.provider_charge_id)
    return Harness(
        provider=provider,
        charge_id=charge.provider_charge_id,
        applied=lambda: len(
            api.order(charge.provider_charge_id)["transactions"].get("refunds", [])
        ),
        lose_next_answer=api.lose_next_answer,
    )


async def _openpix() -> Harness:
    """Build the OpenPix adapter over its simulator.

    Returns:
        Harness: The harness.
    """
    simulator = OpenPixSimulator()

    def lose() -> None:
        """Lose the next refund's answer."""
        simulator.lose_next_answer = True

    client = OpenPixClient(
        _no_retry_http(OpenPixEnvironmentEnum.SANDBOX.base_url, simulator)
    )
    return Harness(
        provider=OpenPixPixProvider(client),
        charge_id=OpenPixSimulator.global_id,
        applied=lambda: len(simulator.refunds),
        lose_next_answer=lose,
    )


HARNESSES: dict[str, Callable[[], Awaitable[Harness]]] = {
    "fake": _fake,
    "mercado_pago": _mercado_pago,
    "openpix": _openpix,
}
"""Every refund provider the contract runs against."""


@pytest.fixture(params=sorted(HARNESSES))
async def harness(request: pytest.FixtureRequest) -> Harness:
    """Build one harness per provider.

    Args:
        request (pytest.FixtureRequest): Carries the provider's key.

    Returns:
        Harness: A provider with a paid charge.
    """
    return await HARNESSES[str(request.param)]()


class TestRefundContract:
    """What every ``PixRefundProvider`` answers alike."""

    async def test_a_partial_refund_is_reported(self, harness: Harness) -> None:
        """The refund carries the reference, the amount and the running total."""
        refund = await harness.provider.refund_pix_charge(
            harness.charge_id, 500, refund_reference="ticket-1"
        )

        assert isinstance(refund, PixRefund)
        assert refund.refund_reference == "ticket-1"
        assert refund.provider_charge_id == harness.charge_id
        assert refund.amount_cents == 500
        assert refund.refunded_cents == 500
        assert refund.status is PixRefundStatusEnum.COMPLETED
        assert refund.provider_refund_id

    async def test_the_same_reference_does_not_refund_twice(
        self, harness: Harness
    ) -> None:
        """A plain retry answers the first refund; the money moved once."""
        first = await harness.provider.refund_pix_charge(
            harness.charge_id, 500, refund_reference="ticket-1"
        )
        again = await harness.provider.refund_pix_charge(
            harness.charge_id, 500, refund_reference="ticket-1"
        )

        assert again.provider_refund_id == first.provider_refund_id
        assert harness.applied() == 1
        assert await harness.provider.get_refunded_cents(harness.charge_id) == 500

    async def test_a_lost_answer_is_safe_to_retry(self, harness: Harness) -> None:
        """The refund landed and the caller did not hear: the retry is a replay."""
        harness.lose_next_answer()
        with contextlib.suppress(TimeoutError, httpx.TimeoutException):
            await harness.provider.refund_pix_charge(
                harness.charge_id, 700, refund_reference="ticket-2"
            )

        refund = await harness.provider.refund_pix_charge(
            harness.charge_id, 700, refund_reference="ticket-2"
        )

        assert refund.amount_cents == 700
        assert harness.applied() == 1
        assert await harness.provider.get_refunded_cents(harness.charge_id) == 700

    async def test_partial_refunds_are_summed(self, harness: Harness) -> None:
        """Two references, two refunds, one total; ``None`` takes the rest."""
        assert await harness.provider.get_refunded_cents(harness.charge_id) == 0

        await harness.provider.refund_pix_charge(
            harness.charge_id, 500, refund_reference="ticket-1"
        )
        second = await harness.provider.refund_pix_charge(
            harness.charge_id, 300, refund_reference="ticket-2"
        )
        rest = await harness.provider.refund_pix_charge(
            harness.charge_id, refund_reference="ticket-3"
        )

        assert second.refunded_cents == 800
        assert rest.amount_cents == TOTAL_CENTS - 800
        assert rest.refunded_cents == TOTAL_CENTS
        assert harness.applied() == 3
        assert await harness.provider.get_refunded_cents(harness.charge_id) == (
            TOTAL_CENTS
        )

    async def test_a_refund_above_the_balance_is_refused(
        self, harness: Harness
    ) -> None:
        """More than is left raises the typed exception, and nothing moves."""
        await harness.provider.refund_pix_charge(
            harness.charge_id, 1500, refund_reference="ticket-1"
        )

        with pytest.raises(PixRefundExceedsBalanceException) as caught:
            await harness.provider.refund_pix_charge(
                harness.charge_id, 600, refund_reference="ticket-2"
            )

        assert caught.value.refundable_cents == TOTAL_CENTS - 1500
        assert caught.value.requested_cents == 600
        assert caught.value.code == "PIX_REFUND_EXCEEDS_BALANCE"
        assert harness.applied() == 1

    async def test_a_full_refund_with_nothing_left_is_refused(
        self, harness: Harness
    ) -> None:
        """``None`` on a fully refunded charge is not an empty refund."""
        await harness.provider.refund_pix_charge(
            harness.charge_id, refund_reference="ticket-1"
        )

        with pytest.raises(PixRefundExceedsBalanceException) as caught:
            await harness.provider.refund_pix_charge(
                harness.charge_id, refund_reference="ticket-2"
            )

        assert caught.value.refundable_cents == 0
        assert harness.applied() == 1

    async def test_a_replay_survives_an_empty_balance(self, harness: Harness) -> None:
        """Repeating the refund that emptied the charge is still a replay.

        The balance check runs before the request, so this is the case it
        must not turn into an error: nothing is left *because* of this
        reference.
        """
        first = await harness.provider.refund_pix_charge(
            harness.charge_id, refund_reference="ticket-1"
        )
        again = await harness.provider.refund_pix_charge(
            harness.charge_id, refund_reference="ticket-1"
        )

        assert again.amount_cents == first.amount_cents == TOTAL_CENTS
        assert harness.applied() == 1

    async def test_a_reference_reused_for_another_amount_conflicts(
        self, harness: Harness
    ) -> None:
        """Same key, other amount: refused, not answered with the old refund."""
        await harness.provider.refund_pix_charge(
            harness.charge_id, 500, refund_reference="ticket-1"
        )

        with pytest.raises(PixRefundReferenceConflictException) as caught:
            await harness.provider.refund_pix_charge(
                harness.charge_id, 400, refund_reference="ticket-1"
            )

        assert caught.value.refund_reference == "ticket-1"
        assert harness.applied() == 1

    async def test_a_non_positive_amount_is_refused_before_the_provider(
        self, harness: Harness
    ) -> None:
        """Zero is not "the rest"; ``None`` is."""
        with pytest.raises(ValueError, match="positive"):
            await harness.provider.refund_pix_charge(
                harness.charge_id, 0, refund_reference="ticket-1"
            )

        assert harness.applied() == 0
