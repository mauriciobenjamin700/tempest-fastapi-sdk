"""Tests for the corrections applied to the pinned OpenPix specification.

The overlay is the only place this repository disagrees with the document
the provider publishes, so each disagreement is pinned here: what it
changes, what it deliberately leaves alone, and that it retires on its own
when upstream catches up.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import Any

import pytest

REPO_ROOT: Path = Path(__file__).resolve().parents[4]
SCRIPTS: str = str(REPO_ROOT / "scripts")
if SCRIPTS not in sys.path:
    sys.path.insert(0, SCRIPTS)

from openpix_overlay import (  # noqa: E402
    CHARGE_RESPONSE_PROPERTIES,
    LIFTED_ENUMS,
    MISTYPED_POINTERS,
    apply,
)
from regen_openpix import SPEC_PATH  # noqa: E402


def _document(properties: dict[str, Any]) -> dict[str, Any]:
    """Build a minimal document carrying one schema's properties.

    Args:
        properties (dict[str, Any]): Properties for the ``M`` schema.

    Returns:
        dict[str, Any]: A loadable OpenAPI 3 document.
    """
    return {
        "openapi": "3.0.0",
        "info": {"title": "t", "version": "1"},
        "paths": {},
        "components": {"schemas": {"M": {"type": "object", "properties": properties}}},
    }


def _typed(document: dict[str, Any], name: str) -> str:
    """Read back the type of one property of the ``M`` schema.

    Args:
        document (dict[str, Any]): A patched document.
        name (str): The property name.

    Returns:
        str: The declared ``type``.
    """
    return str(document["components"]["schemas"]["M"]["properties"][name]["type"])


def _vendored() -> dict[str, Any]:
    """Load the pinned specification the provider published.

    Returns:
        dict[str, Any]: A fresh copy, safe for a test to mutate.
    """
    return json.loads(SPEC_PATH.read_text(encoding="utf-8"))


_DISPUTE_VALUE_POINTER: str = (
    "/paths/~1api~1v1~1dispute~1{id}/get/responses/200/content"
    "/application~1json/schema/properties/dispute/properties/value"
)
"""The pointer the overlay must apply, spelled out instead of looked up.

Taking it from `MISTYPED_POINTERS` would make these tests agree with
whatever that table happens to hold — an emptied table included, which is
exactly the regression they exist to catch.
"""


def _at(document: dict[str, Any], pointer: str) -> dict[str, Any]:
    """Read the node one RFC 6901 pointer addresses.

    Args:
        document (dict[str, Any]): The document to read.
        pointer (str): The pointer, ``~1`` standing for a literal ``/``.

    Returns:
        dict[str, Any]: The addressed schema.

    Written out here rather than imported from the overlay so the test does
    not agree with the code it checks by construction: a resolver that
    forgot to unescape would pass against itself.
    """
    node: Any = document
    for token in pointer.split("/")[1:]:
        node = node[token.replace("~1", "/").replace("~0", "~")]
    return dict(node)


class TestPointerOverrides:
    """Corrections for schemas that have no component to name.

    `MISTYPED_PROPERTIES` addresses
    `components.schemas.<Name>.properties.<prop>`. Two of the document's
    self-contradictions are declared inline instead — the `dispute` object
    of `GET /api/v1/dispute/{id}`, and the `pix` object of the three
    `receivedPix*` callbacks — so they are addressed by JSON pointer.
    """

    def test_every_pointer_fires_on_the_document_we_vendor(self) -> None:
        """Proof the table is not passing because nothing resolves.

        A pointer that mis-escaped the `/` inside `/api/v1/dispute/{id}` or
        inside the callback key `{$request.body#/webhook.url}` would
        resolve to nothing and correct nothing, silently.
        """
        _patched, report = apply(_vendored())

        assert report.retyped_pointers == tuple(MISTYPED_POINTERS)
        assert _DISPUTE_VALUE_POINTER in report.retyped_pointers
        assert len(report.retyped_pointers) == 4

    def test_the_dispute_value_stops_being_text(self) -> None:
        """The inline schema that kept `get_dispute` from reading a reply."""
        document = _vendored()
        assert _at(document, _DISPUTE_VALUE_POINTER)["type"] == "string"

        patched, _report = apply(document)

        assert _at(patched, _DISPUTE_VALUE_POINTER)["type"] == "integer"

    def test_an_override_upstream_fixed_retires(self) -> None:
        """The day the provider corrects it, the log stops naming it."""
        document = _vendored()
        dispute = document["paths"]["/api/v1/dispute/{id}"]["get"]["responses"]["200"][
            "content"
        ]["application/json"]["schema"]["properties"]["dispute"]
        dispute["properties"]["value"] = {"type": "integer"}

        _patched, report = apply(document)

        assert _DISPUTE_VALUE_POINTER not in report.retyped_pointers
        assert len(report.retyped_pointers) == 3

    def test_a_pointer_that_no_longer_resolves_is_a_no_op(self) -> None:
        """A restructured document must not crash a regeneration."""
        patched, report = apply(_document({"value": {"type": "number"}}))

        assert report.retyped_pointers == ()
        assert _typed(patched, "value") == "integer"

    def test_a_dispute_value_is_an_integer_number_of_centavos(self) -> None:
        """`get_dispute` validating a real reply, which it could not before.

        Before the pointer override this raised `Input should be a valid
        string [type=string_type, input_value=15000, input_type=int]`.
        """
        from tempest_fastapi_sdk.integrations.payment.openpix import (
            GetDisputeResponse,
        )

        parsed = GetDisputeResponse.model_validate(
            {"dispute": {"status": "CREATED", "value": 15000, "type": "MED"}}
        )

        assert parsed.dispute is not None
        assert parsed.dispute.value == 15000


class TestLiftedEnums:
    """A closed enum on a response is a refused read, not a typed one.

    The correction has two halves and both are load-bearing: the property
    stops being restricted, and the values keep a class of their own. The
    naive form — deleting the `enum` in place — does the first and silently
    undoes the second, because the generated `ChargeStatus` exists only as
    long as something declares those values.
    """

    def test_the_property_stops_being_restricted(self) -> None:
        """`Charge.status` accepts a state the document does not list."""
        document = _vendored()
        declared = document["components"]["schemas"]["Charge"]["properties"]["status"]
        assert declared["enum"] == ["ACTIVE", "COMPLETED", "EXPIRED"]

        patched, report = apply(document)

        corrected = patched["components"]["schemas"]["Charge"]["properties"]["status"]
        assert "enum" not in corrected
        assert report.lifted_enums == ("Charge.status",)

    def test_the_values_survive_as_a_component(self) -> None:
        """Deleting in place would take the public `ChargeStatus` with it."""
        patched, _report = apply(_vendored())

        component = patched["components"]["schemas"]["ChargeStatus"]
        assert component["enum"] == ["ACTIVE", "COMPLETED", "EXPIRED"]

    def test_the_property_still_references_the_component(self) -> None:
        """A component nothing references is pruned by the generator."""
        patched, _report = apply(_vendored())

        corrected = patched["components"]["schemas"]["Charge"]["properties"]["status"]
        refs = [entry.get("$ref") for entry in corrected["anyOf"]]
        assert "#/components/schemas/ChargeStatus" in refs

    def test_a_lift_upstream_already_did_retires(self) -> None:
        """The override disappears when the provider unconstrains the field.

        The first assertion is what keeps this from passing vacuously: an
        emptied `LIFTED_ENUMS` also reports nothing, so "reports nothing"
        only means retirement once the table is known to fire.
        """
        _patched, baseline = apply(_vendored())
        assert baseline.lifted_enums == ("Charge.status",)

        document = _vendored()
        del document["components"]["schemas"]["Charge"]["properties"]["status"]["enum"]

        _patched, report = apply(document)

        assert report.lifted_enums == ()

    def test_the_table_names_the_component_consumers_import(self) -> None:
        """The name is public API, so it is spelled out and not derived."""
        assert LIFTED_ENUMS["Charge"]["status"] == "ChargeStatus"


class TestIntegerUnits:
    """Money and counts stop being `number`, and rates do not."""

    @pytest.mark.parametrize(
        "name",
        ["value", "balance", "fee", "skip", "limit", "installmentsCount"],
    )
    def test_a_whole_unit_property_is_retyped(self, name: str) -> None:
        """Woovi settles in whole centavos, and pages in whole rows.

        Args:
            name (str): The property under test.
        """
        patched, report = apply(_document({name: {"type": "number"}}))

        assert _typed(patched, name) == "integer"
        assert report.integer_fields == 1

    def test_a_description_saying_cents_is_enough(self) -> None:
        """A name the list misses is still decided by the document."""
        patched, _ = apply(
            _document(
                {"somethingNew": {"type": "number", "description": "Value in cents"}}
            )
        )

        assert _typed(patched, "somethingNew") == "integer"

    def test_a_description_saying_not_cents_wins(self) -> None:
        """The stablecoin quote is the one place a fraction is real.

        ``inputAmount`` is centavos on the deposit list and a BRL currency
        amount on the quote — same name, opposite unit — which is why the
        description is read before the name.
        """
        patched, report = apply(
            _document(
                {
                    "inputAmount": {
                        "type": "number",
                        "description": (
                            "Input amount in BRL (currency unit, not cents)."
                        ),
                    }
                }
            )
        )

        assert _typed(patched, "inputAmount") == "number"
        assert report.integer_fields == 0

    def test_an_unlisted_undocumented_number_is_left_alone(self) -> None:
        """Silence is not evidence, so nothing is assumed from it."""
        patched, _ = apply(_document({"refreshRate": {"type": "number"}}))

        assert _typed(patched, "refreshRate") == "number"

    def test_an_example_is_not_read_as_a_schema(self) -> None:
        """A `type` key inside an example describes nothing."""
        patched, report = apply(
            _document({"value": {"type": "string", "example": {"type": "number"}}})
        )

        assert _typed(patched, "value") == "string"
        assert report.integer_fields == 0

    def test_a_query_parameter_is_retyped_by_its_own_name(self) -> None:
        """`skip` and `limit` are parameters, not properties."""
        document = {
            "openapi": "3.0.0",
            "info": {"title": "t", "version": "1"},
            "paths": {
                "/x": {
                    "get": {
                        "parameters": [
                            {
                                "name": "skip",
                                "in": "query",
                                "schema": {"type": "number"},
                            }
                        ],
                        "responses": {"200": {"description": "ok"}},
                    }
                }
            },
            "components": {"schemas": {}},
        }

        patched, report = apply(document)

        parameter = patched["paths"]["/x"]["get"]["parameters"][0]
        assert parameter["schema"]["type"] == "integer"
        assert report.integer_fields == 1


class TestDeclaredFields:
    """Fields the API returns and the document does not declare."""

    def test_charge_gains_the_three_the_api_returns(self) -> None:
        """`fee`, `discount` and `valueWithDiscount`, reported from production."""
        document = {
            "openapi": "3.0.0",
            "info": {"title": "t", "version": "1"},
            "paths": {},
            "components": {
                "schemas": {"Charge": {"type": "object", "properties": {}}},
            },
        }

        patched, report = apply(document)

        declared = patched["components"]["schemas"]["Charge"]["properties"]
        assert set(CHARGE_RESPONSE_PROPERTIES) <= set(declared)
        assert report.added_properties == (
            "Charge.fee",
            "Charge.discount",
            "Charge.valueWithDiscount",
        )

    def test_an_upstream_declaration_wins(self) -> None:
        """The overlay retires quietly instead of overwriting a fix."""
        document = {
            "openapi": "3.0.0",
            "info": {"title": "t", "version": "1"},
            "paths": {},
            "components": {
                "schemas": {
                    "Charge": {
                        "type": "object",
                        "properties": {"fee": {"type": "string"}},
                    }
                },
            },
        }

        patched, report = apply(document)

        assert patched["components"]["schemas"]["Charge"]["properties"]["fee"] == {
            "type": "string"
        }
        assert "Charge.fee" not in report.added_properties


class TestTheDocumentIsNotMutated:
    """The vendored document stays what the provider published."""

    def test_apply_leaves_its_input_untouched(self) -> None:
        """A caller keeps a clean copy to diff the next refresh against."""
        document = _document({"value": {"type": "number"}})

        apply(document)

        assert _typed(document, "value") == "number"

    def test_no_operation_is_invented(self) -> None:
        """The overlay corrects what the document gets wrong, not what it lacks.

        v0.259.0 added a `DELETE /api/v1/payment/{id}` on the reasoning that
        the two-step transfer flow had no documented way back. The document
        the provider publishes carries only `get` on that path, and no
        `delete` on any payment path, so the operation was a guess shipped
        as a public method in a money path. This pins it out.
        """
        document = {
            "openapi": "3.0.0",
            "info": {"title": "t", "version": "1"},
            "paths": {
                "/api/v1/payment/{id}": {
                    "get": {"responses": {"200": {"description": ""}}}
                }
            },
            "components": {"schemas": {}},
        }

        patched, _ = apply(document)

        assert set(patched["paths"]["/api/v1/payment/{id}"]) == {"get"}
        assert patched["paths"] == document["paths"]


class TestTheGeneratedResult:
    """What the consumer actually gets, after the overlay and the generator."""

    def test_money_is_an_integer_number_of_centavos(self) -> None:
        """The wire stops carrying `1000.0` where the API documents `1000`."""
        from tempest_fastapi_sdk.integrations.payment.openpix import (
            Charge,
            ChargePayload,
        )

        assert Charge.model_fields["value"].annotation == int | None

        payload = ChargePayload(correlation_id="abc-1", value=1000)
        dumped = payload.model_dump(by_alias=True, mode="json", exclude_none=True)
        assert dumped["value"] == 1000
        assert not isinstance(dumped["value"], float)

    def test_pagination_is_whole_rows(self) -> None:
        """`skip=0.0` in a query string was asking for leniency."""
        from tempest_fastapi_sdk.integrations.payment.openpix import (
            ListChargesResponsePageInfo,
        )

        assert ListChargesResponsePageInfo.model_fields["skip"].annotation == (
            int | None
        )

    def test_a_stablecoin_quote_is_still_fractional(self) -> None:
        """The one place a fraction is real keeps it."""
        from tempest_fastapi_sdk.integrations.payment.openpix import (
            GetStablecoinQuoteResponseQuote,
        )

        fields = GetStablecoinQuoteResponseQuote.model_fields
        assert fields["base_price"].annotation == float | None
        assert fields["input_amount"].annotation == float | None

    def test_charge_carries_the_three_fields_a_ledger_reads(self) -> None:
        """Undeclared, they were dropped — and a ledger recorded zero."""
        from tempest_fastapi_sdk.integrations.payment.openpix import Charge

        charge = Charge.model_validate(
            {"value": 1000, "fee": 25, "discount": 0, "valueWithDiscount": 1000}
        )

        assert charge.fee == 25
        assert charge.discount == 0
        assert charge.value_with_discount == 1000

    def test_a_charge_refund_can_carry_either_identifier(self) -> None:
        """The document declares `refundId` on `Refund` only; the API sends both."""
        from tempest_fastapi_sdk.integrations.payment.openpix import ChargeRefund

        refund = ChargeRefund.model_validate(
            {"refundId": "11bf5b37", "endToEndId": "E2311444"}
        )

        assert refund.refund_id == "11bf5b37"
        assert refund.end_to_end_id == "E2311444"

    def test_a_response_keeps_a_field_nobody_declared(self) -> None:
        """`extra="allow"` is the difference between absent and lost."""
        from tempest_fastapi_sdk.integrations.payment.openpix import Charge

        charge = Charge.model_validate({"value": 1000, "wooviAddedThis": "later"})

        assert (charge.model_extra or {})["wooviAddedThis"] == "later"

    def test_a_payload_still_drops_what_it_was_not_given(self) -> None:
        """On the way out, an unexpected key is the caller's own typo."""
        from tempest_fastapi_sdk.integrations.payment.openpix import ChargePayload

        payload = ChargePayload.model_validate(
            {"value": 1, "correlationID": "abc-1", "corelationID": "typo"}
        )

        assert not payload.model_extra

    def test_the_client_has_no_invented_payment_delete(self) -> None:
        """A method for an endpoint nobody observed does not ship."""
        from tempest_fastapi_sdk.integrations.payment.openpix import OpenPixClient

        assert not hasattr(OpenPixClient, "delete_api_v1_payment_by_id")


class TestWhatTheOverlayLeavesAlone:
    """The set it does *not* touch is pinned too.

    Every test above checks a correction the overlay makes. None checked
    what it leaves behind, and that is where it was wrong: v0.259.0 shipped
    prose calling the leftovers "the only ones where the fraction is real"
    while `Transaction.webhookSent[].status` — described in the document as
    "HTTP response status code of the webhook delivery attempt" — sat among
    them as a `float`.

    A new `number` in a refreshed document fails here. That is the point:
    the answer is a judgement call about that field's unit, and it should
    be made by a person, not defaulted to `float` in silence.
    """

    def _remaining(self) -> dict[str, int]:
        """Count the properties still typed `number` after the overlay.

        Returns:
            dict[str, int]: Property name to occurrence count. The entry
            keyed ``""`` counts schemas the walk reached without a name.
        """
        import json

        from openpix_overlay import apply as apply_overlay

        document = json.loads(SPEC_PATH.read_text(encoding="utf-8"))
        patched, _ = apply_overlay(document)
        found: dict[str, int] = {}

        def walk(node: Any, scope: str | None) -> None:
            if isinstance(node, list):
                for entry in node:
                    walk(entry, scope)
                return
            if not isinstance(node, dict):
                return
            if node.get("type") == "number":
                found[scope or ""] = found.get(scope or "", 0) + 1
            for key, value in node.items():
                if key == "properties" and isinstance(value, dict):
                    for property_name, property_schema in value.items():
                        walk(property_schema, str(property_name))
                elif key == "parameters" and isinstance(value, list):
                    for parameter in value:
                        if isinstance(parameter, dict):
                            walk(
                                parameter.get("schema"),
                                str(parameter.get("name") or "") or None,
                            )
                elif key in {"items", "allOf", "anyOf", "oneOf", "not"}:
                    walk(value, scope)
                elif key in {"example", "examples", "enum", "default"}:
                    continue
                else:
                    walk(value, None)

        walk(patched, None)
        return found

    def test_the_fields_left_fractional_are_the_expected_ones(self) -> None:
        """Each name here was looked at, and left `float` on purpose."""
        assert self._remaining() == {
            "": 1,
            "annualRevenue": 2,
            "expiration": 1,
            "inputAmount": 2,
            "maxTokens": 1,
            "monthlyFeePercentage": 1,
            "outputAmount": 3,
            "rate": 2,
            "refreshRate": 1,
            "tokens": 2,
            "tokensAfter": 1,
            "tokensAfterRefresh": 1,
            "tokensBefore": 1,
        }

    def test_a_status_code_is_a_whole_number(self) -> None:
        """The one the previous release's prose claimed was fractional."""
        patched, _ = apply(
            _document(
                {
                    "status": {
                        "type": "number",
                        "description": (
                            "HTTP response status code of the webhook delivery attempt"
                        ),
                    }
                }
            )
        )

        assert _typed(patched, "status") == "integer"

    def test_an_unrelated_status_is_left_alone(self) -> None:
        """`status` is not on the name list, and must not become one."""
        patched, report = apply(_document({"status": {"type": "number"}}))

        assert _typed(patched, "status") == "number"
        assert report.integer_fields == 0
