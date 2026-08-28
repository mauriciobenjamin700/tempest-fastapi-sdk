"""What this repo corrects in the pinned OpenPix specification.

``vendor/openpix-openapi.json`` stays byte for byte the document the
provider publishes, so refreshing it is a reviewable diff of *their*
changes. Everything we know the document gets wrong lives here instead,
one named correction at a time, each carrying the evidence that justifies
it. Editing the vendored file would blend the two and make the next
refresh unreadable.

Two kinds of correction, in the order :func:`apply` runs them:

* **Integer units.** The specification types money and counts as
  ``number``. Woovi settles in whole centavos, and says so in the field's
  own description on 58 numeric schemas (the counting method is written out
  in ``vendor/openpix-evidence.md``, because the number is not reproducible
  without it) — so the generated client was
  sending ``{"value": 1000.0}`` where the API documents ``1000``, and every
  arithmetic a consumer did on a charge value started from a binary
  approximation. The provider has been fixing this at the source: 45
  ``value`` fields already arrive typed ``integer``, against 52 that still
  need the correction.
* **Fields the response carries and the document omits.** ``Charge`` has
  no ``fee``, ``discount`` or ``valueWithDiscount``; the API returns all
  three at the top level of the charge object.

An operation was corrected here too, and removed in v0.260.0: a ``DELETE``
for a payment, added on the reasoning that the two-step transfer flow had
no documented way back. Checked against the document the provider publishes
today, ``/api/v1/payment/{id}`` carries only ``get`` and no payment path
carries a ``delete`` — the operation was never theirs to omit. Correcting a
document is for what it gets *wrong*; an endpoint nobody observed is a
guess, and a guess does not belong in a money path.

Every correction is exercised by ``tests/integrations/payment/openpix``,
and the generated output is pinned by the drift test — so a correction
that stops matching upstream shows up as a failure, not as silence.
"""

from __future__ import annotations

import copy
import re
from dataclasses import dataclass, field
from typing import Any

INTEGER_PROPERTY_NAMES: frozenset[str] = frozenset(
    {
        "value",
        "balance",
        "fee",
        "discount",
        "valueWithDiscount",
        "total",
        "totalValue",
        "minimumValue",
        "available",
        "blocked",
        "blockedBySecurity",
        "blockedByWithdrawSafety",
        "skip",
        "limit",
        "expiresIn",
        "dayDue",
        "dayGenerateCharge",
        "daysForDueDate",
        "daysAfterDueDate",
        "installmentCount",
        "installmentsCount",
        "installmentNumber",
        "boletoEmissionLimit",
        "boletoMaximumValueLimit",
        "pixDayLimit",
        "pixNightLimit",
        "pixInSameHolderDayLimit",
        "pixInSameHolderNightLimit",
        "pixInDifferentHolderDayLimit",
        "pixInDifferentHolderNightLimit",
        "pixOutSameHolderDayLimit",
        "pixOutSameHolderNightLimit",
        "pixOutDifferentHolderDayLimit",
        "pixOutDifferentHolderNightLimit",
    }
)
"""Properties this API measures in whole units, whatever the spec types them.

Money is in centavos and never fractional — the specification says so
itself on ``balance`` ("Number in cents that represent the balance"), on
every ``pix*Limit``, and on many of the 52 ``value`` fields it still types
``number``. The ones whose description is silent are the same quantity on a
neighbouring schema, which is a judgement and not a measurement: it is why
this list is reviewed by name, one entry at a time, rather than inferred.

``value`` stays on this list even where the description reads "basis
points" rather than centavos (the advance-day discount modalities): the
specification's own example is ``100 = 1.00%``, an integer either way.

The rest are counts and day offsets. ``skip`` and ``limit`` are the
pagination pair: 32 occurrences each still typed ``number``, most of them
the echo in a ``pageInfo`` response, and 7 operations that take them as a
query parameter — where a client sending ``skip=0.0`` is asking the
provider to be lenient.
"""

CENTS_PATTERN: re.Pattern[str] = re.compile(r"\b(cents?|centavos?)\b", re.IGNORECASE)
"""Description that proves the unit, for a property the list above misses."""

STATUS_CODE_PATTERN: re.Pattern[str] = re.compile(r"\bstatus code\b", re.IGNORECASE)
"""Description that proves a whole count for something that is not money.

Added in v0.260.0 for ``Transaction.webhookSent[].status``, typed
``number`` and described as "HTTP response status code of the webhook
delivery attempt". An HTTP status code is an integer by definition, and it
sat outside both rules: no name on the list, no "cents" in the description.

It is a description rule and not another name on
:data:`INTEGER_PROPERTY_NAMES` on purpose — ``status`` is one of the most
reused names in any API, and claiming every ``status`` is a whole number
would be the kind of guess this module exists to avoid.
"""

NOT_CENTS_PATTERN: re.Pattern[str] = re.compile(
    r"\bnot cents\b|\bstablecoin\b|\bexchange rate\b",
    re.IGNORECASE,
)
"""Description that proves the opposite, and wins over both rules above.

The stablecoin quote endpoints are the only place in this specification
where a fractional amount is real: ``inputAmount`` there is documented as
"Input amount in BRL (currency unit, **not cents**)", ``outputAmount`` is
an amount of stablecoin, and ``basePrice`` is the rate between them. The
same ``inputAmount`` name on the deposit list *is* centavos, which is why
this decision reads the description and not only the name.
"""

CHARGE_RESPONSE_PROPERTIES: dict[str, dict[str, Any]] = {
    "fee": {
        "type": "integer",
        "description": (
            "Fee charged on this charge, in cents. Returned by the API at the "
            "top level of the charge object; absent from the specification, "
            "which models a fee only under `paymentMethods.pix`."
        ),
    },
    "discount": {
        "type": "integer",
        "description": (
            "Discount applied to this charge, in cents. Returned by the API "
            "and absent from the specification."
        ),
    },
    "valueWithDiscount": {
        "type": "integer",
        "description": (
            "Charge value after the discount, in cents. Returned by the API "
            "and absent from the specification."
        ),
    },
}
"""Charge fields the API returns and the document does not declare.

Reported from production by a consumer persisting them into a ledger
(issue #223): with the fields undeclared, the generated model dropped them
and every row would have recorded a zero fee — an error that surfaces at
reconciliation, not at the call. They are declared optional, so a response
without them still validates.
"""

CHARGE_REFUND_PROPERTIES: dict[str, dict[str, Any]] = {
    "refundId": {
        "type": "string",
        "description": (
            "Unique refund ID for this refund. The specification declares "
            "this field on `Refund` (a Pix transaction refund) but not on "
            "`ChargeRefund`, while the API returns it on both."
        ),
    },
}
"""The refund identifier ``ChargeRefund`` is missing.

Declared as its own property rather than as a second name for
``endToEndId``: the two are different identifiers on ``Refund``, where the
specification declares only ``refundId``, and collapsing them would make
the model claim something the document contradicts. Both are optional, so
a response carrying either one validates.
"""


@dataclass(frozen=True)
class OverlayReport:
    """What :func:`apply` changed, for the regeneration log and the tests.

    Attributes:
        integer_fields (int): Schemas retyped from ``number`` to
            ``integer``.
        added_properties (tuple[str, ...]): ``Schema.property`` entries
            declared by this overlay.
    """

    integer_fields: int = 0
    added_properties: tuple[str, ...] = ()


@dataclass
class _Counter:
    """Mutable tally threaded through the recursive walk.

    Attributes:
        retyped (int): How many schemas became ``integer``.
    """

    retyped: int = field(default=0)


def _wants_integer(name: str | None, schema: dict[str, Any]) -> bool:
    """Decide whether one ``type: number`` schema is really an integer.

    Args:
        name (str | None): The property or parameter name the schema
            belongs to, or ``None`` when the walk reached it without one.
        schema (dict[str, Any]): The schema fragment.

    Returns:
        bool: ``True`` when the description proves a whole unit — centavos,
        or an HTTP status code — or the name is one this API measures in
        whole units. A description that proves the opposite wins over both.
    """
    if schema.get("type") != "number":
        return False
    description = str(schema.get("description") or "")
    if NOT_CENTS_PATTERN.search(description):
        return False
    if CENTS_PATTERN.search(description) or STATUS_CODE_PATTERN.search(description):
        return True
    return name is not None and name in INTEGER_PROPERTY_NAMES


def _walk(node: Any, name: str | None, counter: _Counter) -> None:
    """Retype every numeric schema the rules claim, in place.

    Args:
        node (Any): The document fragment to visit.
        name (str | None): The property or parameter name in scope.
        counter (_Counter): Tally of retyped schemas.

    The name in scope is what makes the decision possible, so it is carried
    down through the constructs that keep describing the *same* value —
    ``items``, ``allOf``/``anyOf``/``oneOf``, and a parameter's ``schema``
    — and dropped everywhere else, which is what keeps an example or a code
    sample from being read as a schema.
    """
    if isinstance(node, list):
        for entry in node:
            _walk(entry, name, counter)
        return
    if not isinstance(node, dict):
        return

    if _wants_integer(name, node):
        node["type"] = "integer"
        counter.retyped += 1

    for key, value in node.items():
        if key == "properties" and isinstance(value, dict):
            for property_name, property_schema in value.items():
                _walk(property_schema, str(property_name), counter)
        elif key == "parameters" and isinstance(value, list):
            for parameter in value:
                if isinstance(parameter, dict):
                    _walk(
                        parameter.get("schema"),
                        str(parameter.get("name") or "") or None,
                        counter,
                    )
        elif key in {"items", "allOf", "anyOf", "oneOf", "not"}:
            _walk(value, name, counter)
        elif key in {"example", "examples", "enum", "default"}:
            continue
        else:
            _walk(value, None, counter)


def _declare(
    document: dict[str, Any],
    schema_name: str,
    properties: dict[str, dict[str, Any]],
) -> tuple[str, ...]:
    """Add properties to a component schema, leaving existing ones alone.

    Args:
        document (dict[str, Any]): The document being patched.
        schema_name (str): The ``components.schemas`` key to extend.
        properties (dict[str, dict[str, Any]]): Properties to declare.

    Returns:
        tuple[str, ...]: ``Schema.property`` for each one actually added.
        A property the provider has since declared itself is skipped, so
        the overlay retires quietly instead of overwriting an upstream fix.
    """
    schemas = document.get("components", {}).get("schemas", {})
    target = schemas.get(schema_name)
    if not isinstance(target, dict):
        return ()
    declared = target.setdefault("properties", {})
    added: list[str] = []
    for name, schema in properties.items():
        if name in declared:
            continue
        declared[name] = copy.deepcopy(schema)
        added.append(f"{schema_name}.{name}")
    return tuple(added)


def apply(document: dict[str, Any]) -> tuple[dict[str, Any], OverlayReport]:
    """Return a corrected copy of the specification.

    Args:
        document (dict[str, Any]): The loaded vendored specification.

    Returns:
        tuple[dict[str, Any], OverlayReport]: The patched document and a
        summary of what changed. The input is not mutated — the caller
        keeps a clean copy of what the provider published.
    """
    patched = copy.deepcopy(document)
    counter = _Counter()
    _walk(patched, None, counter)

    added = _declare(patched, "Charge", CHARGE_RESPONSE_PROPERTIES)
    added += _declare(patched, "ChargeRefund", CHARGE_REFUND_PROPERTIES)

    return patched, OverlayReport(
        integer_fields=counter.retyped,
        added_properties=added,
    )


__all__: list[str] = [
    "CENTS_PATTERN",
    "CHARGE_REFUND_PROPERTIES",
    "CHARGE_RESPONSE_PROPERTIES",
    "INTEGER_PROPERTY_NAMES",
    "NOT_CENTS_PATTERN",
    "STATUS_CODE_PATTERN",
    "OverlayReport",
    "apply",
]
