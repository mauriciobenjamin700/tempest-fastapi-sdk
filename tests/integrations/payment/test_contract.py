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

import inspect
import subprocess
import sys
from typing import Any, get_type_hints

import pytest

from tempest_fastapi_sdk.integrations.payment import (
    CardProvider,
    PaymentStatus,
    PayoutProvider,
    PixCharge,
    PixChargeRequest,
    PixEventType,
    PixProvider,
)
from tempest_fastapi_sdk.integrations.payment.adapters.mercado_pago import (
    STATUS_MAP as MERCADO_PAGO_STATUS_MAP,
)
from tempest_fastapi_sdk.integrations.payment.adapters.mercado_pago import (
    MercadoPagoCardProvider,
    MercadoPagoPixProvider,
)
from tempest_fastapi_sdk.integrations.payment.adapters.openpix import (
    STATUS_MAP,
    OpenPixPayoutProvider,
    OpenPixPixProvider,
)
from tempest_fastapi_sdk.integrations.payment.mercado_pago import OrderStatus
from tempest_fastapi_sdk.integrations.payment.openpix import ChargeStatus
from tempest_fastapi_sdk.testing.fakes import FakePayoutProvider

ADAPTERS: list[type[Any]] = [OpenPixPixProvider, MercadoPagoPixProvider]

CARD_ADAPTERS: list[type[Any]] = [MercadoPagoCardProvider]
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
    """Every mapping target is a real :class:`PaymentStatus` member."""
    for provider_status, canonical in STATUS_MAP.items():
        assert isinstance(canonical, PaymentStatus), provider_status


def test_charge_keeps_the_enum_member_not_its_value() -> None:
    """``PixCharge.status`` is an enum member at runtime, not a bare string.

    ``BaseSchema`` sets ``use_enum_values=True``, which would make
    ``charge.status is PaymentStatus.PAID`` false on every charge while
    ``==`` kept working — a defect that survives review precisely because
    the obvious check still passes. ``_EnumSafeSchema`` turns the flag off,
    and this test is what keeps it off.
    """
    charge = PixCharge(
        provider="openpix",
        provider_charge_id="ch_1",
        reference="order-1",
        amount_cents=1990,
        status=PaymentStatus.PAID,
        provider_status="COMPLETED",
    )

    assert charge.status is PaymentStatus.PAID
    assert charge.model_dump(mode="json")["status"] == "paid"


def test_event_type_survives_serialization() -> None:
    """The same property holds for :class:`PixEventType`."""
    hints = get_type_hints(PixCharge)

    assert hints["status"] is PaymentStatus
    assert PixEventType.CHARGE_PAID.value == "charge_paid"


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
