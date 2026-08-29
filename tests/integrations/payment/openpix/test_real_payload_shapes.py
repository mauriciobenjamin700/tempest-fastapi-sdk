"""Response payloads captured from the API, not built from the document.

Every other fixture here is constructed from the same specification the
models are generated from, so fantasy and implementation agree and a
document that lies about a type is invisible. These bodies were read off
`api.woovi-sandbox.com` (issue #238, 2026-08-29) and are pinned verbatim:
if a regeneration ever reverts the overlay, these fail.

The one that shipped broken was `expiresIn`. The document declares it
`string` on `Charge`, `number` on `ChargePayload` and `integer` on
`WebhookCharge` — three types for one field — and the API returns the
integer, so **every** charge read raised ``ValidationError`` until the
overlay corrected it.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import Any

import pytest

from tempest_fastapi_sdk.integrations.payment.openpix import (
    Charge,
    CreateChargeResponse,
    GetChargeResponse,
)

_REPO_ROOT: Path = Path(__file__).resolve().parents[4]

CAPTURED_CHARGE: dict[str, Any] = {
    "value": 1190,
    "identifier": "5400e12faa5b4dd2a1b7f7f0e0a3a0c1",
    "correlationID": "f2dc576d-a6db-4677-9cb7-4de54964cc87",
    "status": "ACTIVE",
    "expiresIn": 3600,
    "fee": 50,
    "expiresDate": "2026-08-29T15:17:16.060Z",
    "brCode": "00020101021226980014br.gov.bcb.pix",
    "comment": "Purchase of 100 coins",
}
"""One charge, as the sandbox returned it with HTTP 200."""


class TestCapturedChargeValidates:
    def test_create_charge_response_accepts_the_real_body(self) -> None:
        """The exact failure in the report, as a regression."""
        parsed = CreateChargeResponse.model_validate({"charge": CAPTURED_CHARGE})
        assert parsed.charge is not None
        assert parsed.charge.expires_in == 3600

    def test_get_charge_response_accepts_the_real_body(self) -> None:
        parsed = GetChargeResponse.model_validate({"charge": CAPTURED_CHARGE})
        assert parsed.charge is not None
        assert parsed.charge.expires_in == 3600

    def test_expires_in_is_an_int_not_a_string(self) -> None:
        """A union would have hidden this and pushed it onto every caller."""
        annotation = str(Charge.model_fields["expires_in"].annotation)
        assert "int" in annotation
        assert "str" not in annotation

    def test_the_undeclared_money_fields_survive(self) -> None:
        """``fee`` is the v0.260.0 correction; it must not regress here."""
        parsed = GetChargeResponse.model_validate({"charge": CAPTURED_CHARGE})
        assert parsed.charge is not None
        assert parsed.charge.fee == 50


class TestOverlayOverridesStillApply:
    """A regeneration must not silently put the wrong type back.

    The overlay is a script, not a package module, so the guard imports it
    the way the regeneration script does.
    """

    @staticmethod
    def _overlay() -> Any:
        """Import the overlay module from ``scripts/``.

        Returns:
            Any: The imported module.
        """
        scripts = str(_REPO_ROOT / "scripts")
        if scripts not in sys.path:
            sys.path.insert(0, scripts)
        import openpix_overlay

        return openpix_overlay

    def test_every_override_is_present_in_the_generated_model(self) -> None:
        """Each corrected property still carries the corrected type."""
        overlay = self._overlay()
        from tempest_fastapi_sdk.integrations.payment.openpix import schemas

        python_types: dict[str, str] = {"integer": "int", "string": "str"}
        for schema_name, properties in overlay.MISTYPED_PROPERTIES.items():
            model = getattr(schemas, schema_name)
            for wire_name, corrected in properties.items():
                field = next(
                    info
                    for name, info in model.model_fields.items()
                    if wire_name in (info.validation_alias, name)
                )
                expected = python_types[corrected["type"]]
                assert expected in str(field.annotation), (
                    f"{schema_name}.{wire_name} lost its overlay correction — "
                    "a regeneration put the document's wrong type back"
                )

    def test_an_override_that_upstream_fixed_retires(self) -> None:
        """The override disappears from the report, not from the output."""
        overlay = self._overlay()
        document = json.loads(
            (_REPO_ROOT / "vendor" / "openpix-openapi.json").read_text()
        )
        document["components"]["schemas"]["Charge"]["properties"]["expiresIn"] = {
            "type": "integer"
        }
        _patched, report = overlay.apply(document)
        assert "Charge.expiresIn" not in report.retyped_properties

    def test_the_override_fires_on_the_document_we_vendor(self) -> None:
        """Proof the guard is not passing because nothing is overridden."""
        overlay = self._overlay()
        document = json.loads(
            (_REPO_ROOT / "vendor" / "openpix-openapi.json").read_text()
        )
        _patched, report = overlay.apply(document)
        assert "Charge.expiresIn" in report.retyped_properties

    def test_the_vendored_document_is_left_alone(self) -> None:
        """The overlay corrects a copy; the vendored file stays upstream's."""
        document = json.loads(
            (_REPO_ROOT / "vendor" / "openpix-openapi.json").read_text()
        )
        declared = document["components"]["schemas"]["Charge"]["properties"]
        assert declared["expiresIn"] == {"type": "string"}


class TestTheLiftedEnumKeepsItsClass:
    """A closed enum on a response is a refused read, not a typed one.

    The correction has two halves and both are load-bearing: the property
    stops being restricted, and the values keep a class of their own. The
    naive form — deleting the ``enum`` in place — does the first and
    silently undoes the second, because the generated ``ChargeStatus``
    exists only as long as something declares those values.
    """

    def test_the_generated_class_still_exists(self) -> None:
        """``ChargeStatus`` is public API, exported and taught by the recipe."""
        from tempest_fastapi_sdk.integrations.payment import openpix

        assert "ChargeStatus" in openpix.__all__
        assert openpix.ChargeStatus.COMPLETED.value == "COMPLETED"

    def test_a_status_outside_the_enum_validates(self) -> None:
        """The exact body that used to reach a service as a 500."""
        parsed = GetChargeResponse.model_validate(
            {"charge": {**CAPTURED_CHARGE, "status": "CANCELLED"}}
        )
        assert parsed.charge is not None
        assert parsed.charge.status == "CANCELLED"

    def test_a_known_status_still_arrives_as_the_enum_member(self) -> None:
        """Widening the field must not cost the typing it already gave."""
        from tempest_fastapi_sdk.integrations.payment.openpix import ChargeStatus

        parsed = GetChargeResponse.model_validate({"charge": CAPTURED_CHARGE})
        assert parsed.charge is not None
        assert parsed.charge.status == ChargeStatus.ACTIVE


@pytest.mark.parametrize(
    ("schema_name", "expected"),
    [("Charge", "string"), ("ChargePayload", "number"), ("WebhookCharge", "integer")],
)
def test_the_document_contradicts_itself_about_expires_in(
    schema_name: str, expected: str
) -> None:
    """Pinned so an upstream fix shows up as a failing test, not a surprise.

    Args:
        schema_name (str): The component to read.
        expected (str): The type the document declares today.
    """
    document = json.loads((_REPO_ROOT / "vendor" / "openpix-openapi.json").read_text())
    declared = document["components"]["schemas"][schema_name]["properties"]
    assert declared["expiresIn"].get("type") == expected
