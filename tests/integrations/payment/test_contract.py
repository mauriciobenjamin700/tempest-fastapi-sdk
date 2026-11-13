"""Guards for the canonical payment contract.

Two properties are load-bearing and neither is visible in review:

* every adapter really matches ``PixProvider`` — by signature, not just by
  method name;
* every value of a provider's own status enum is mapped, so a regeneration
  that adds a state fails here instead of reporting an unpaid charge as
  pending.

A third guard fixes the import cost that justified putting the contract
inside ``integrations/payment/`` at all.
"""

from __future__ import annotations

import copy
import inspect
import json
import subprocess
import sys
from collections.abc import Callable
from pathlib import Path
from typing import Any, get_overloads, get_type_hints

import httpx
import pytest

from tempest_fastapi_sdk import HTTPClient
from tempest_fastapi_sdk.integrations.payment import (
    CardProvider,
    PaymentStatusEnum,
    PayoutProvider,
    PixCharge,
    PixChargeRequest,
    PixEventTypeEnum,
    PixPayer,
    PixProvider,
    PixRefundProvider,
)
from tempest_fastapi_sdk.integrations.payment.adapters.mercado_pago import (
    STATUS_MAP as MERCADO_PAGO_STATUS_MAP,
)
from tempest_fastapi_sdk.integrations.payment.adapters.mercado_pago import (
    MercadoPagoCardProvider,
    MercadoPagoPixProvider,
)
from tempest_fastapi_sdk.integrations.payment.adapters.openpix import (
    REFUND_STATUS_MAP as OPENPIX_REFUND_STATUS_MAP,
)
from tempest_fastapi_sdk.integrations.payment.adapters.openpix import (
    STATUS_MAP,
    OpenPixPayoutProvider,
    OpenPixPixProvider,
)
from tempest_fastapi_sdk.integrations.payment.mercado_pago import (
    DEFAULT_BASE_URL,
    OrderStatus,
)
from tempest_fastapi_sdk.integrations.payment.openpix import (
    ChargeRefundStatus,
    ChargeStatus,
    OpenPixClient,
    OpenPixEnvironmentEnum,
)
from tempest_fastapi_sdk.testing.fakes import (
    FakeCardProvider,
    FakePayoutProvider,
    FakePixProvider,
)

ADAPTERS: list[type[Any]] = [OpenPixPixProvider, MercadoPagoPixProvider]

CARD_ADAPTERS: list[type[Any]] = [MercadoPagoCardProvider, FakeCardProvider]
"""Every adapter that claims to implement :class:`CardProvider`."""

CARD_PROTOCOL_METHODS: tuple[str, ...] = (
    "create_card_charge",
    "get_card_charge",
    "capture_card_charge",
    "cancel_card_charge",
    "refund_card_charge",
)
"""The methods the card contract requires, all coroutines."""
"""Every adapter that claims to implement :class:`PixProvider`."""

PROTOCOL_METHODS: tuple[str, ...] = (
    "create_pix_charge",
    "get_pix_charge",
    "cancel_pix_charge",
    "parse_webhook",
)
"""The methods the contract requires."""


@pytest.mark.parametrize("adapter", ADAPTERS, ids=lambda a: a.__name__)
@pytest.mark.parametrize("method_name", PROTOCOL_METHODS)
def test_adapter_signature_matches_protocol(
    adapter: type[Any], method_name: str
) -> None:
    """Each adapter method takes and returns what the protocol declares.

    ``PixProvider`` is a plain ``Protocol``, so ``isinstance`` is not
    available — and would not be enough anyway, since a runtime-checkable
    protocol only compares attribute names. Comparing the signature is what
    catches an adapter whose ``create_pix_charge`` grew an extra required
    argument.
    """
    expected = inspect.signature(getattr(PixProvider, method_name))
    actual = inspect.signature(getattr(adapter, method_name))

    assert list(actual.parameters) == list(expected.parameters), (
        f"{adapter.__name__}.{method_name} takes "
        f"{list(actual.parameters)}, the contract declares "
        f"{list(expected.parameters)}"
    )
    assert actual.return_annotation == expected.return_annotation


PAYOUT_PROVIDERS: list[type[Any]] = [OpenPixPayoutProvider, FakePayoutProvider]
"""Every class that claims to implement :class:`PayoutProvider`."""


@pytest.mark.parametrize("provider", PAYOUT_PROVIDERS, ids=lambda a: a.__name__)
def test_payout_provider_matches_the_protocol(provider: type[Any]) -> None:
    """Same parameters, same return, a coroutine, and a provider name.

    The wallet's withdrawal calls ``transfer_to_pix_key`` positionally and
    awaits it; a provider whose signature drifted would fail only there.
    """
    expected = inspect.signature(PayoutProvider.transfer_to_pix_key)
    actual = inspect.signature(provider.transfer_to_pix_key)

    assert list(actual.parameters) == list(expected.parameters)
    assert [p.kind for p in actual.parameters.values()] == [
        p.kind for p in expected.parameters.values()
    ]
    assert actual.return_annotation == expected.return_annotation
    assert inspect.iscoroutinefunction(provider.transfer_to_pix_key)
    assert isinstance(provider.provider_name, str)
    assert provider.provider_name


@pytest.mark.parametrize("adapter", ADAPTERS, ids=lambda a: a.__name__)
def test_adapter_declares_provider_name(adapter: type[Any]) -> None:
    """Each adapter carries the ``provider_name`` the contract requires."""
    assert isinstance(adapter.provider_name, str)
    assert adapter.provider_name


@pytest.mark.parametrize("adapter", ADAPTERS, ids=lambda a: a.__name__)
def test_adapter_methods_are_async_where_the_contract_says_so(
    adapter: type[Any],
) -> None:
    """The three API calls are coroutines; ``parse_webhook`` is not.

    A synchronous ``create_pix_charge`` would satisfy a name-only check and
    then block the event loop on every charge.
    """
    for name in ("create_pix_charge", "get_pix_charge", "cancel_pix_charge"):
        assert inspect.iscoroutinefunction(getattr(adapter, name)), name
    assert not inspect.iscoroutinefunction(adapter.parse_webhook)


@pytest.mark.parametrize("adapter", CARD_ADAPTERS, ids=lambda a: a.__name__)
@pytest.mark.parametrize("method_name", CARD_PROTOCOL_METHODS)
def test_card_adapter_signature_matches_protocol(
    adapter: type[Any], method_name: str
) -> None:
    """Each card adapter method takes and returns what the protocol declares."""
    expected = inspect.signature(getattr(CardProvider, method_name))
    actual = inspect.signature(getattr(adapter, method_name))

    assert list(actual.parameters) == list(expected.parameters)
    for name, parameter in expected.parameters.items():
        assert actual.parameters[name].default == parameter.default, name
    expected_hints = get_type_hints(getattr(CardProvider, method_name))
    actual_hints = get_type_hints(getattr(adapter, method_name))
    assert actual_hints == expected_hints
    assert inspect.iscoroutinefunction(getattr(adapter, method_name))
    assert adapter.provider_name


REFUND_ADAPTERS: list[type[Any]] = [
    OpenPixPixProvider,
    MercadoPagoPixProvider,
    FakePixProvider,
]
"""Every class that claims to implement :class:`PixRefundProvider`."""

REFUND_METHODS: tuple[str, ...] = ("refund_pix_charge", "get_refunded_cents")
"""The methods :class:`PixRefundProvider` adds to :class:`PixProvider`."""


def _keyed_hints(adapter: type[Any], method_name: str) -> dict[str, Any]:
    """Read the type hints of the form the refund protocol calls.

    ``MercadoPagoPixProvider.refund_pix_charge`` is overloaded: without
    ``refund_reference`` it keeps the ``PixCharge`` answer it had before the
    protocol existed, with it it answers a ``PixRefund``. The protocol is
    satisfied by the second overload, so that is the one compared.

    Args:
        adapter (type[Any]): The adapter.
        method_name (str): The method.

    Returns:
        dict[str, Any]: The hints of the matching overload, or of the method
        itself when it is not overloaded.
    """
    method = getattr(adapter, method_name)
    for overload in get_overloads(method):
        hints = get_type_hints(overload)
        if hints.get("refund_reference") is str:
            return hints
    return get_type_hints(method)


@pytest.mark.parametrize("adapter", REFUND_ADAPTERS, ids=lambda a: a.__name__)
@pytest.mark.parametrize("method_name", REFUND_METHODS)
def test_refund_adapter_matches_the_refund_protocol(
    adapter: type[Any], method_name: str
) -> None:
    """Same parameters, kinds and defaults, and the protocol's types.

    ``refund_reference`` is keyword-only everywhere: a positional third
    argument could not tell an amount from a reference. The Mercado Pago
    adapter's default of ``None`` is the compatibility form, and the only
    difference allowed.
    """
    expected = inspect.signature(getattr(PixRefundProvider, method_name))
    actual = inspect.signature(getattr(adapter, method_name))

    assert list(actual.parameters) == list(expected.parameters)
    assert [p.kind for p in actual.parameters.values()] == [
        p.kind for p in expected.parameters.values()
    ]
    expected_hints = get_type_hints(getattr(PixRefundProvider, method_name))
    assert _keyed_hints(adapter, method_name) == expected_hints
    assert inspect.iscoroutinefunction(getattr(adapter, method_name))


def test_the_mercado_pago_refund_keeps_its_old_form() -> None:
    """Without a reference, the call made before the protocol still types.

    ``refund_pix_charge(charge_id, amount_cents)`` answered a
    :class:`PixCharge` and still does; a caller that never passes
    ``refund_reference`` sees no change in signature or return.
    """
    overloads = get_overloads(MercadoPagoPixProvider.refund_pix_charge)
    legacy = [
        get_type_hints(overload)
        for overload in overloads
        if get_type_hints(overload).get("refund_reference") is type(None)
    ]

    assert len(overloads) == 2
    assert legacy == [
        {
            "charge_id": str,
            "amount_cents": int | None,
            "refund_reference": type(None),
            "return": PixCharge,
        }
    ]


def test_every_openpix_refund_status_is_mapped() -> None:
    """No member of OpenPix's ``ChargeRefundStatus`` falls through."""
    declared = {status.value for status in ChargeRefundStatus}

    assert declared == set(OPENPIX_REFUND_STATUS_MAP)


def test_every_openpix_status_is_mapped() -> None:
    """No member of OpenPix's ``ChargeStatus`` falls through the mapping.

    This is the guard the issue asked for: the day a regeneration adds a
    state, the failure is here rather than a charge silently reported as
    ``PENDING``.
    """
    unmapped = [status for status in ChargeStatus if status not in STATUS_MAP]

    assert not unmapped, f"OpenPix statuses with no canonical mapping: {unmapped}"


def test_every_mercado_pago_order_status_is_mapped() -> None:
    """No member of the generated ``OrderStatus`` falls through.

    The adapter keys its map by the wire string so that importing it does
    not build the generated schemas; this walks the generated enum instead,
    so a regeneration that adds a state fails here. The map also carries
    ``failed`` and ``refunded``, observed in the sandbox outside the
    document's list — and nothing else.
    """
    declared = {status.value for status in OrderStatus}
    unmapped = sorted(declared - set(MERCADO_PAGO_STATUS_MAP))

    assert not unmapped, f"Mercado Pago order statuses with no mapping: {unmapped}"
    assert set(MERCADO_PAGO_STATUS_MAP) - declared == {"failed", "refunded"}


def test_status_mapping_targets_are_canonical() -> None:
    """Every mapping target is a real :class:`PaymentStatusEnum` member."""
    for provider_status, canonical in STATUS_MAP.items():
        assert isinstance(canonical, PaymentStatusEnum), provider_status


def test_charge_keeps_the_enum_member_not_its_value() -> None:
    """``PixCharge.status`` is an enum member at runtime, not a bare string.

    ``BaseSchema`` sets ``use_enum_values=True``, which would make
    ``charge.status is PaymentStatusEnum.PAID`` false on every charge while
    ``==`` kept working — a defect that survives review precisely because
    the obvious check still passes. ``_EnumSafeSchema`` turns the flag off,
    and this test is what keeps it off.
    """
    charge = PixCharge(
        provider="openpix",
        provider_charge_id="ch_1",
        reference="order-1",
        amount_cents=1990,
        status=PaymentStatusEnum.PAID,
        provider_status="COMPLETED",
    )

    assert charge.status is PaymentStatusEnum.PAID
    assert charge.model_dump(mode="json")["status"] == "paid"


def test_event_type_survives_serialization() -> None:
    """The same property holds for :class:`PixEventTypeEnum`."""
    hints = get_type_hints(PixCharge)

    assert hints["status"] is PaymentStatusEnum
    assert PixEventTypeEnum.CHARGE_PAID.value == "charge_paid"


def test_charge_request_refuses_a_non_positive_amount() -> None:
    """A zero or negative charge is a bug in the caller, not a charge."""
    with pytest.raises(ValueError):
        PixChargeRequest(amount_cents=0, reference="order-1")


def test_importing_the_contract_does_not_build_provider_schemas() -> None:
    """Importing the namespace leaves the generated schemas unloaded.

    This is the measurement the placement decision rests on: the contract
    lives inside ``integrations/payment/`` and that only stays free while
    the providers, and now the adapters, resolve lazily. Run in a
    subprocess because the rest of this module has already imported the
    adapter, and therefore the schemas.
    """
    code = (
        "import sys; import tempest_fastapi_sdk.integrations.payment as p; "
        "print('tempest_fastapi_sdk.integrations.payment.openpix.schemas' "
        "in sys.modules)"
    )
    result = subprocess.run(
        [sys.executable, "-c", code],
        capture_output=True,
        text=True,
        check=True,
    )

    assert result.stdout.strip() == "False", result.stdout


MERCADO_PAGO_PIX_CREATE: Path = (
    Path(__file__).parent
    / "adapters"
    / "fixtures"
    / "mercado_pago_orders"
    / "pix_create.json"
)
"""A Pix order the sandbox created on 2026-10-09, redacted."""


class MercadoPagoOrders:
    """Mercado Pago's measured idempotency rule, as a transport.

    Same ``X-Idempotency-Key``, same order — what the sandbox answered on
    2026-10-10 (``vendor/mercadopago-evidence.md`` section 9.6). A new key
    opens a new order with a new id.

    Attributes:
        orders (dict[str, dict[str, Any]]): Orders by idempotency key.
    """

    def __init__(self) -> None:
        """Start with no orders."""
        self.orders: dict[str, dict[str, Any]] = {}
        self._template: dict[str, Any] = json.loads(
            MERCADO_PAGO_PIX_CREATE.read_text(encoding="utf-8")
        )["body"]

    def __call__(self, request: httpx.Request) -> httpx.Response:
        """Answer a create, honouring the idempotency key.

        Args:
            request (httpx.Request): The ``POST /v1/orders``.

        Returns:
            httpx.Response: ``201`` with the order the key maps to.
        """
        key = request.headers["X-Idempotency-Key"]
        if key not in self.orders:
            order = copy.deepcopy(self._template)
            order["id"] = f"ORDTST{len(self.orders) + 1:04d}"
            self.orders[key] = order
        return httpx.Response(201, json=self.orders[key])


class OpenPixCharges:
    """OpenPix's documented rule for ``return_existing``, as a transport.

    With ``?return_existing=true`` a create for a ``correlationID`` that
    already has a charge answers that charge; without it, every create is a
    new charge.

    Attributes:
        charges (dict[str, dict[str, Any]]): Charges by correlation id.
    """

    def __init__(self) -> None:
        """Start with no charges."""
        self.charges: dict[str, dict[str, Any]] = {}
        self._created: int = 0

    def __call__(self, request: httpx.Request) -> httpx.Response:
        """Answer a create, honouring ``return_existing``.

        Args:
            request (httpx.Request): The ``POST /api/v1/charge``.

        Returns:
            httpx.Response: ``200`` with the charge.
        """
        sent: dict[str, Any] = json.loads(request.content)
        reference = str(sent["correlationID"])
        existing = self.charges.get(reference)
        if existing is None or request.url.params.get("return_existing") != "true":
            self._created += 1
            existing = {
                "value": sent["value"],
                "identifier": f"charge{self._created:04d}",
                "correlationID": reference,
                "status": "ACTIVE",
                "brCode": "00020101021226980014br.gov.bcb.pix",
            }
            self.charges[reference] = existing
        return httpx.Response(200, json={"charge": existing})


def _mercado_pago() -> PixProvider:
    """Build the Mercado Pago adapter over :class:`MercadoPagoOrders`.

    Returns:
        PixProvider: The adapter, with its default idempotency key.
    """
    return MercadoPagoPixProvider(
        HTTPClient(
            base_url=DEFAULT_BASE_URL,
            transport=httpx.MockTransport(MercadoPagoOrders()),
        )
    )


def _openpix() -> PixProvider:
    """Build the OpenPix adapter over :class:`OpenPixCharges`.

    Returns:
        PixProvider: The adapter, over the generated client.
    """
    return OpenPixPixProvider(
        OpenPixClient(
            HTTPClient(
                base_url=OpenPixEnvironmentEnum.SANDBOX.base_url,
                transport=httpx.MockTransport(OpenPixCharges()),
            )
        )
    )


PIX_PROVIDER_FACTORIES: list[Callable[[], PixProvider]] = [
    _openpix,
    _mercado_pago,
    FakePixProvider,
]
"""Every Pix provider, each over a stand-in that applies its provider's rule."""


@pytest.mark.parametrize(
    "build", PIX_PROVIDER_FACTORIES, ids=["openpix", "mercado_pago", "fake"]
)
async def test_the_same_reference_answers_the_same_charge(
    build: Callable[[], PixProvider],
) -> None:
    """``reference`` is the create's idempotency key, with no configuration.

    The promise :attr:`PixChargeRequest.reference` makes (#754). A retry
    after a timeout, a double click or a reprocess must not open a second
    charge, and swapping providers must not change that. A different
    reference still opens a different charge, so the assertion cannot pass
    on a provider that answers one fixed charge.
    """
    provider = build()
    request = PixChargeRequest(
        amount_cents=1990, reference="order-754", payer=PixPayer(email="a@b.co")
    )

    first = await provider.create_pix_charge(request)
    again = await provider.create_pix_charge(request)
    other = await provider.create_pix_charge(
        request.model_copy(update={"reference": "order-755"})
    )

    assert again.provider_charge_id == first.provider_charge_id
    assert other.provider_charge_id != first.provider_charge_id
