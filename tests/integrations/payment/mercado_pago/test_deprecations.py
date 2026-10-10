"""What the provider retires, and what the Orders API answers instead.

Measured on 2026-10-09 (``vendor/mercadopago-evidence.md`` section 9): the
dashboard labels the Payments API *"Esta API será descontinuada em breve"*,
and the Orders API that replaces it ran end to end in the sandbox. This
module pins the two consequences in the generated client: the retired
operations are gone, with exactly the schemas only they reached, and the
Orders models accept every state the sandbox actually returned.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import Any

import pytest
import yaml

REPO_ROOT: Path = Path(__file__).resolve().parents[4]
SCRIPTS: str = str(REPO_ROOT / "scripts")
if SCRIPTS not in sys.path:
    sys.path.insert(0, SCRIPTS)

from mercadopago_overlay import (  # noqa: E402
    DEPRECATED_OPERATIONS,
    LIFTED_ENUMS,
    apply,
)

FIXTURES: Path = (
    REPO_ROOT / "tests/integrations/payment/adapters/fixtures/mercado_pago_orders"
)


def _vendored() -> dict[str, Any]:
    """Load the vendored specification.

    Returns:
        dict[str, Any]: The document as the provider publishes it.
    """
    document: dict[str, Any] = yaml.safe_load(
        (REPO_ROOT / "vendor" / "mercadopago-openapi.yaml").read_text(encoding="utf-8")
    )
    return document


class TestRetiredOperations:
    """Removed with their evidence, and only theirs."""

    def test_every_retired_operation_names_its_source(self) -> None:
        """The dashboard's deprecation notice, or the document's own flag."""
        for retired in DEPRECATED_OPERATIONS:
            assert (
                "descontinuada" in retired.evidence
                or "deprecated: true" in retired.evidence
            ), retired.path

    def test_the_payments_api_is_gone_from_the_client(self) -> None:
        """No method addresses `/v1/payments` any more."""
        source = (
            REPO_ROOT
            / "tempest_fastapi_sdk/integrations/payment/mercado_pago/client.py"
        ).read_text(encoding="utf-8")

        assert '"/v1/payments' not in source
        assert 'f"/v1/payments' not in source

    def test_the_document_marks_the_in_store_ones_deprecated(self) -> None:
        """The second source is checkable in the vendored file itself."""
        document = _vendored()
        spec_marked = [
            retired
            for retired in DEPRECATED_OPERATIONS
            if "deprecated: true" in retired.evidence
        ]

        assert spec_marked
        for retired in spec_marked:
            operation = document["paths"][retired.path][retired.method]
            assert operation.get("deprecated") is True, retired.path

    def test_only_schemas_orphaned_by_the_removal_are_pruned(self) -> None:
        """A schema the vendored document already left unreferenced stays."""
        patched, report = apply(_vendored())
        schemas = patched["components"]["schemas"]

        assert "Payment" in report.pruned_schemas
        assert "PaymentRequest" in report.pruned_schemas
        assert "Payment" not in schemas
        assert "Order" in schemas
        assert "PaymentResponse" in schemas


class TestOrderStatesTheSandboxReturned:
    """The lifted enums accept what the API sends, not only what it lists."""

    @pytest.mark.parametrize(
        "name",
        sorted(path.stem for path in FIXTURES.glob("*.json")),
    )
    def test_every_captured_order_validates(self, name: str) -> None:
        """Before the lift, creating a Pix raised on `waiting_transfer`."""
        from tempest_fastapi_sdk.integrations.payment.mercado_pago import (
            Order,
            RefundOrderResponse,
        )

        body = json.loads((FIXTURES / f"{name}.json").read_text(encoding="utf-8"))[
            "body"
        ]
        if "data" in body:
            body = body["data"]
        model = RefundOrderResponse if name.startswith("card_refund") else Order

        model.model_validate(body)

    def test_the_declared_values_keep_their_class(self) -> None:
        """Lifted, not deleted: the generated enum survives."""
        from tempest_fastapi_sdk.integrations.payment.mercado_pago import (
            OrderStatus,
            OrderTransactionPaymentStatusDetail,
        )

        assert OrderStatus.PROCESSED.value == "processed"
        assert OrderTransactionPaymentStatusDetail.WAITING_CAPTURE.value == (
            "waiting_capture"
        )
        assert set(LIFTED_ENUMS) == {"Order", "OrderTransactionPayment"}
