"""The document contradicting itself is a defect class, not a one-off.

Issue #238 was `Charge.expiresIn`: declared `string`, returned as an
integer, so every charge read raised `ValidationError`. Sweeping the whole
document for the same shape — one property name declared `string` in one
schema and numeric in another — found two more, both money, both on
responses the client validates:

    PixQrCode.value            "string"   vs  PixQrCodePayload.value    "number"
    WithdrawTransaction.value  "string"   vs  PixWithdrawTransaction.value "number"

`PixQrCode` against `PixQrCodePayload` is the same object going out and
coming back. Seven of the client's methods could not read a real answer.

So the sweep is the guard: a name that starts contradicting itself fails
here, offline, instead of in production. Every conflict that is **not** a
defect has to be written down with the reason it is not, which is what
keeps this from being silenced by a growing allowlist nobody reads.
"""

from __future__ import annotations

import json
import sys
from collections import defaultdict
from pathlib import Path
from typing import Any

_REPO_ROOT: Path = Path(__file__).resolve().parents[4]
_NUMERIC: frozenset[str] = frozenset({"integer", "number"})
_NOT_SCHEMAS: frozenset[str] = frozenset({"example", "examples", "enum", "default"})

KNOWN_CONFLICTS: dict[str, str] = {
    "status": (
        "Two concepts sharing a name. `Transaction.webhookSent[].status` is "
        "an HTTP status code (integer); the other 114 are the string state "
        "enums. The overlay already tells them apart by description "
        "(STATUS_CODE_PATTERN)."
    ),
    "expiration": (
        "`StablecoinDepositResponse.expiration` is an ISO timestamp — the "
        "example in the document is `2026-06-05T12:00:00.000Z`, so `string` "
        "is right there. `Installment.expiration` being `number` where the "
        "webhooks say `integer` is a separate, lower-severity inconsistency: "
        "it does not break a read, it just types seconds as a float."
    ),
    "value": (
        "The remaining `string` declarations are the `additionalInfo` "
        "key/value pairs, where text is correct. Two real ones are left and "
        "tracked: the inline `dispute.value` under `GET /api/v1/dispute/{id}` "
        "and `pix.value` in the three `receivedPix*` callbacks. Neither is "
        "reachable by `MISTYPED_PROPERTIES`, which addresses "
        "`components.schemas.<Name>.properties.<prop>` — they need an "
        "override addressed by JSON pointer."
    ),
}
"""Property names that contradict themselves and why that is not a bug.

An entry is a claim someone checked, not a silencer: a new name reaching
this test fails until it is either corrected in the overlay or explained
here.
"""

MONEY_VALUE_STRING_TRAILS: frozenset[str] = frozenset(
    {
        "components.schemas.Charge.properties.additionalInfo.items",
        "components.schemas.Charge.properties.paymentMethods.properties.pix"
        ".properties.additionalInfo.items",
        "components.schemas.ChargePayload.properties.additionalInfo.items",
        "components.schemas.Subscription.properties.addtionalInfo.items",
        "components.schemas.SubscriptionPayload.properties.additionalInfo.items",
        "components.schemas.WebhookCharge.properties.additionalInfo.items",
        "paths./api/v1/decode/emv.post.responses.200.content.application/json"
        ".schema.properties.cobLocation.properties.payload.properties"
        ".additionalInfo.items",
        "paths./api/v1/dispute/{id}.get.responses.200.content.application/json"
        ".schema.properties.dispute",
        "paths./api/v1/webhook.post.callbacks.receivedPix"
        ".{$request.body#/webhook.url}.post.requestBody.content.application/json"
        ".schema.properties.pix",
        "paths./api/v1/webhook.post.callbacks.receivedPixDetached"
        ".{$request.body#/webhook.url}.post.requestBody.content.application/json"
        ".schema.properties.pix",
        "paths./api/v1/webhook.post.callbacks.receivedPixQrCode"
        ".{$request.body#/webhook.url}.post.requestBody.content.application/json"
        ".schema.properties.pix",
    }
)
"""Exactly where `value` is still declared `string` after the overlay.

Frozen rather than counted because `value` is the money field: a new place
declaring it as text is the next seven broken methods, and the diff should
name the place.
"""


def _overlay() -> Any:
    """Import the overlay module the way the regeneration script does.

    Returns:
        Any: The imported ``openpix_overlay`` module.
    """
    scripts = str(_REPO_ROOT / "scripts")
    if scripts not in sys.path:
        sys.path.insert(0, scripts)
    import openpix_overlay

    return openpix_overlay


def _declared_types() -> dict[str, dict[str, set[str]]]:
    """Walk every property declaration in the corrected document.

    Nested schemas count: an inline object under a path, a list's ``items``,
    a nested ``properties`` — the money field that broke seven methods lives
    one level down in some of them, and a shallow walk would report the
    document as clean.

    Returns:
        dict[str, dict[str, set[str]]]: Property name to declared type to
        the trails that declare it.
    """
    document = json.loads((_REPO_ROOT / "vendor" / "openpix-openapi.json").read_text())
    patched, _report = _overlay().apply(document)
    seen: dict[str, dict[str, set[str]]] = defaultdict(lambda: defaultdict(set))

    def walk(node: Any, trail: str) -> None:
        if isinstance(node, list):
            for index, entry in enumerate(node):
                walk(entry, f"{trail}[{index}]")
            return
        if not isinstance(node, dict):
            return
        properties = node.get("properties")
        if isinstance(properties, dict):
            for prop, declared in properties.items():
                if isinstance(declared, dict) and isinstance(declared.get("type"), str):
                    seen[prop][declared["type"]].add(trail or "<root>")
        for key, value in node.items():
            if key not in _NOT_SCHEMAS:
                walk(value, f"{trail}.{key}" if trail else key)

    walk(patched, "")
    return seen


def _conflicting_names(seen: dict[str, dict[str, set[str]]]) -> set[str]:
    """Return the names declared both as text and as a number.

    Args:
        seen (dict[str, dict[str, set[str]]]): The walk's result.

    Returns:
        set[str]: Property names in conflict.
    """
    return {
        prop
        for prop, by_type in seen.items()
        if "string" in by_type and set(by_type) & _NUMERIC
    }


class TestTheDocumentDoesNotContradictItself:
    def test_every_conflict_is_either_corrected_or_explained(self) -> None:
        """A new self-contradiction fails here, not in production."""
        unexplained = sorted(
            _conflicting_names(_declared_types()) - set(KNOWN_CONFLICTS)
        )
        assert not unexplained, (
            "these property names are declared `string` in one schema and "
            f"numeric in another: {unexplained}. Correct them in "
            "`MISTYPED_PROPERTIES` (scripts/openpix_overlay.py) or add an "
            "entry to KNOWN_CONFLICTS saying why the difference is real."
        )

    def test_an_explanation_without_a_conflict_is_stale(self) -> None:
        """The provider fixing the document should retire the entry."""
        conflicting = _conflicting_names(_declared_types())
        stale = sorted(set(KNOWN_CONFLICTS) - conflicting)
        assert not stale, (
            f"{stale} no longer contradict themselves — delete the "
            "KNOWN_CONFLICTS entries so the table keeps meaning something."
        )

    def test_the_money_field_is_text_only_where_it_should_be(self) -> None:
        """`value` is the field that broke seven client methods."""
        seen = _declared_types()
        assert seen["value"]["string"] == set(MONEY_VALUE_STRING_TRAILS)

    def test_the_corrected_money_fields_are_numeric(self) -> None:
        """The three the overlay corrects, pinned by name."""
        seen = _declared_types()
        for trail in (
            "components.schemas.PixQrCode",
            "components.schemas.WithdrawTransaction",
        ):
            assert trail in seen["value"]["integer"], trail
        assert "components.schemas.Charge" in seen["expiresIn"]["integer"]
