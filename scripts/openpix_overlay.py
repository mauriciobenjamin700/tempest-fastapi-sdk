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

MISTYPED_PROPERTIES: dict[str, dict[str, dict[str, Any]]] = {
    "Charge": {
        "expiresIn": {
            "type": "integer",
            "description": (
                "Seconds until the charge expires. The specification "
                "declares this `string`; the API returns an integer."
            ),
        },
    },
    "PixQrCode": {
        "value": {
            "type": "integer",
            "description": (
                "Value of this QR code, in cents. The specification "
                "declares this `string` on the response while declaring "
                "the same field `number` on `PixQrCodePayload`, the "
                "request for the very same object."
            ),
        },
    },
    "WithdrawTransaction": {
        "value": {
            "type": "integer",
            "description": (
                "Value withdrawn, in cents. The specification declares "
                "this `string` while declaring the same field `number` on "
                "`PixWithdrawTransaction`."
            ),
        },
    },
}
"""Properties the document declares with the wrong type.

Different from :data:`CHARGE_RESPONSE_PROPERTIES`, which adds a field the
document forgot: here the field is declared, and declared wrong, so every
response carrying it fails validation before the caller sees it.

``Charge.expiresIn`` is the measured case (issue #238): the API answers
``{"expiresIn": 3600}`` with HTTP 200 and the generated model demanded a
string, so **every** charge read raised
``Input should be a valid string [input_value=3600, input_type=int]``.

The document contradicts itself about this one field three ways, which is
what makes the correction safe rather than a guess::

    Charge.expiresIn        -> "string"
    ChargePayload.expiresIn -> "number"
    WebhookCharge.expiresIn -> "integer"

``WebhookCharge`` is the same charge object delivered by webhook, and it
already says integer. Evidence and the raw body are in
``vendor/openpix-evidence.md``.

``expiresIn`` was the case somebody tripped over; it was not the only one.
Sweeping the document for the same shape — a property name declared
``string`` in one schema and numeric in another — found two more, both
money, both on responses the client validates: ``PixQrCode.value`` (against
``PixQrCodePayload.value``, the request for the same object) and
``WithdrawTransaction.value`` (against ``PixWithdrawTransaction.value``).
Seven of the client's methods could not read a real answer because of them.
``tests/integrations/payment/openpix/test_spec_type_conflicts.py`` keeps the
sweep running so the next one shows up here instead of in production.

An override retires by itself: :func:`_retype` skips a property the
provider has since declared correctly, so the regeneration log stops
mentioning it the day upstream is fixed.
"""


MISTYPED_POINTERS: dict[str, dict[str, Any]] = {
    "/paths/~1api~1v1~1dispute~1{id}/get/responses/200/content/"
    "application~1json/schema/properties/dispute/properties/value": {
        "type": "integer",
        "description": (
            "The value of the dispute, in cents. Declared `string` on this "
            "inline response schema while the `Dispute` component declares "
            "the same field `number`."
        ),
    },
    "/paths/~1api~1v1~1webhook/post/callbacks/receivedPix/"
    "{$request.body#~1webhook.url}/post/requestBody/content/"
    "application~1json/schema/properties/pix/properties/value": {
        "type": "integer",
        "description": (
            "Value of the received Pix, in cents. Declared `string` on this "
            "callback while every `Webhook*Payload` component declares it "
            "`integer`."
        ),
    },
    "/paths/~1api~1v1~1webhook/post/callbacks/receivedPixDetached/"
    "{$request.body#~1webhook.url}/post/requestBody/content/"
    "application~1json/schema/properties/pix/properties/value": {
        "type": "integer",
        "description": (
            "Value of the received Pix, in cents. Declared `string` on this "
            "callback while every `Webhook*Payload` component declares it "
            "`integer`."
        ),
    },
    "/paths/~1api~1v1~1webhook/post/callbacks/receivedPixQrCode/"
    "{$request.body#~1webhook.url}/post/requestBody/content/"
    "application~1json/schema/properties/pix/properties/value": {
        "type": "integer",
        "description": (
            "Value of the received Pix, in cents. Declared `string` on this "
            "callback while every `Webhook*Payload` component declares it "
            "`integer`."
        ),
    },
}
"""Mistyped properties that do not live under ``components.schemas``.

Sibling of :data:`MISTYPED_PROPERTIES`, which addresses a property by
component name. These four are the same defect in places that name cannot
reach, so they are addressed by JSON pointer instead (issue #244):

* ``GET /api/v1/dispute/{id}`` declares its 200 body **inline**, and types
  ``dispute.value`` ``string`` while the ``Dispute`` component types the
  same field ``number``. This one breaks today — ``get_dispute`` is one of
  the seven methods that could not read a real answer, and the v0.269.0
  correction stops at component schemas.
* The three ``receivedPix*`` callbacks type ``pix.value`` ``string``
  against ``integer`` on every ``Webhook*Payload`` component. These break
  nothing yet, because the generator emits no model for ``callbacks``
  (measured: zero ``Callback`` classes in the generated schemas). They are
  corrected anyway so the day callback models are emitted is not the day
  the defect is discovered.

Escaping follows RFC 6901: ``~1`` for ``/`` and ``~0`` for ``~``. The
callback keys contain a literal ``#``, which is not special to a pointer
and is written as-is.
"""

LIFTED_ENUMS: dict[str, dict[str, str]] = {
    "Charge": {"status": "ChargeStatus"},
}
"""Response properties whose declared ``enum`` the provider does not honour.

``Charge.status`` is declared ``{"type": "string", "enum": ["ACTIVE",
"COMPLETED", "EXPIRED"]}``, and the generated model therefore *refuses* any
other value — a ``ValidationError`` raised inside the client, which reaches
a service as a generic 500 with the real state nowhere in the response.

The document contradicts itself here exactly as it does about types:
``WebhookCharge`` is the same charge object delivered by webhook, and it
declares ``status`` as an unconstrained ``{"type": "string"}``. A provider
that leaves itself free to report a fourth state on one path has not
committed to three on the other.

A closed enum is right on a **request**, where the value is ours and a
typo should be refused before the call. On a **response** it makes the
client fail on data it merely does not recognize yet, which is the worse
of the two outcomes: the charge is real either way, and a service that
cannot read it cannot even see what arrived.

The correction is a **lift**, not a deletion, and the difference is the
whole design. Dropping the ``enum`` in place also deletes the generated
``ChargeStatus`` class: it exists only because ``Charge.status`` declares
those three values inline, and it is public API — exported, keyed on by
``STATUS_MAP``, taught by the recipe
(``charge.status == ChargeStatus.COMPLETED``) and named in the migration
guide as a class that did *not* change. Measured: with the ``enum``
simply removed, ``ChargeStatus`` disappears from the generated module and
from ``__all__``.

So the values move into a component schema of their own and the property
becomes an ``anyOf`` of that component and a bare string. The generator
emits the component as the same class under the same name, and the field
annotation becomes ``ChargeStatus | str | None``: known states keep the
enum member, an unrecognized one arrives as the string the provider sent.

A union was rejected for ``Charge.expiresIn`` in v0.269.0 and is right
here, because the two unions are not the same shape. ``int | str`` forces
every consumer into a defensive ``int()`` without knowing which they get.
``ChargeStatus | str`` is a union of a ``str`` enum and ``str``: the value
is a string either way, comparison works either way, and the union adds
information — *this one is a state we know* — rather than ambiguity.

A component that nothing references is pruned by the generator, so the
``anyOf`` is also what keeps the class alive: the reference is what makes
it reachable.

The value of this mapping is that component's name. It is spelled out
rather than derived because it is the name consumers already import:
deriving it would make a rename look like a formatting detail.
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
        retyped_properties (tuple[str, ...]): ``Schema.property`` entries
            whose declared type this overlay corrected.
        retyped_pointers (tuple[str, ...]): JSON pointers whose declared
            type this overlay corrected, for the mistyped properties that
            do not live under ``components.schemas``.
        lifted_enums (tuple[str, ...]): ``Schema.property`` entries whose
            inline ``enum`` this overlay moved into a component of its
            own, leaving the property unconstrained.
    """

    integer_fields: int = 0
    added_properties: tuple[str, ...] = ()
    retyped_properties: tuple[str, ...] = ()
    retyped_pointers: tuple[str, ...] = ()
    lifted_enums: tuple[str, ...] = ()


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


def _retype(
    document: dict[str, Any],
    schema_name: str,
    properties: dict[str, dict[str, Any]],
) -> tuple[str, ...]:
    """Replace the declared schema of properties the document types wrong.

    Args:
        document (dict[str, Any]): The document being patched.
        schema_name (str): The ``components.schemas`` key to correct.
        properties (dict[str, dict[str, Any]]): The corrected schemas, by
            property name.

    Returns:
        tuple[str, ...]: ``Schema.property`` for each one actually
        changed. A property already declared with the corrected ``type``
        is skipped, so the override retires quietly the day the provider
        fixes the document — and the regeneration log says so by no longer
        naming it.
    """
    schemas = document.get("components", {}).get("schemas", {})
    target = schemas.get(schema_name)
    if not isinstance(target, dict):
        return ()
    declared = target.get("properties")
    if not isinstance(declared, dict):
        return ()
    corrected: list[str] = []
    for name, schema in properties.items():
        current = declared.get(name)
        if not isinstance(current, dict):
            continue
        if current.get("type") == schema.get("type"):
            continue
        declared[name] = copy.deepcopy(schema)
        corrected.append(f"{schema_name}.{name}")
    return tuple(corrected)


def _resolve(document: dict[str, Any], pointer: str) -> dict[str, Any] | None:
    """Walk an RFC 6901 JSON pointer to the container it addresses.

    Args:
        document (dict[str, Any]): The document being patched.
        pointer (str): The pointer, leading ``/`` included.

    Returns:
        dict[str, Any] | None: The addressed mapping, or ``None`` when any
        step is missing. A pointer that no longer resolves means the
        provider restructured the document, and silently patching a
        neighbouring node would be worse than not patching at all.

    Only mappings are traversed. This overlay addresses named properties,
    never array positions, so a numeric token is a key like any other and
    is never read as an index.
    """
    node: Any = document
    for token in pointer.lstrip("/").split("/"):
        key = token.replace("~1", "/").replace("~0", "~")
        if not isinstance(node, dict) or key not in node:
            return None
        node = node[key]
    return node if isinstance(node, dict) else None


def _retype_pointer(
    document: dict[str, Any],
    pointer: str,
    schema: dict[str, Any],
) -> str | None:
    """Replace the schema a JSON pointer addresses.

    Args:
        document (dict[str, Any]): The document being patched.
        pointer (str): The pointer to the mistyped schema.
        schema (dict[str, Any]): The corrected schema.

    Returns:
        str | None: The pointer when it was corrected, else ``None`` —
        for a pointer that does not resolve, or one already declared with
        the corrected ``type``, which is how the override retires the day
        the provider fixes the document.
    """
    target = _resolve(document, pointer)
    if target is None or target.get("type") == schema.get("type"):
        return None
    target.clear()
    target.update(copy.deepcopy(schema))
    return pointer


def _lift_enum(
    document: dict[str, Any],
    schema_name: str,
    properties: dict[str, str],
) -> tuple[str, ...]:
    """Move a property's inline ``enum`` into a component of its own.

    Args:
        document (dict[str, Any]): The document being patched.
        schema_name (str): The ``components.schemas`` key to correct.
        properties (dict[str, str]): Component name to lift into, by
            property name.

    Returns:
        tuple[str, ...]: ``Schema.property`` for each enum actually lifted.
        A property that no longer declares one is skipped, so the override
        retires by itself the day the provider unconstrains the field.

    The values are not discarded. They are declared as a standalone
    component and the property is rewritten as an ``anyOf`` of that
    component and the bare type, so the generator still emits the enum
    class of the same name — a component nothing references is pruned —
    and the field accepts a state the list does not name.
    """
    schemas = document.get("components", {}).get("schemas", {})
    target = schemas.get(schema_name)
    if not isinstance(target, dict):
        return ()
    declared = target.get("properties")
    if not isinstance(declared, dict):
        return ()
    lifted: list[str] = []
    for name, component in properties.items():
        current = declared.get(name)
        if not isinstance(current, dict) or "enum" not in current:
            continue
        schemas.setdefault(
            component,
            {
                "type": current.get("type", "string"),
                "enum": copy.deepcopy(current["enum"]),
                "description": (
                    f"The values `{schema_name}.{name}` is documented with. "
                    f"Declared as a component so the generated class survives "
                    f"the property being unconstrained: the API reports states "
                    f"outside this list, and a closed enum on a response makes "
                    f"an unrecognized state a refused read."
                ),
            },
        )
        declared[name] = {
            "anyOf": [
                {"$ref": f"#/components/schemas/{component}"},
                {"type": current.get("type", "string")},
            ],
            "description": current.get("description", ""),
        }
        lifted.append(f"{schema_name}.{name}")
    return tuple(lifted)


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

    retyped: tuple[str, ...] = ()
    for schema_name, properties in MISTYPED_PROPERTIES.items():
        retyped += _retype(patched, schema_name, properties)

    pointers: tuple[str, ...] = tuple(
        corrected
        for pointer, schema in MISTYPED_POINTERS.items()
        if (corrected := _retype_pointer(patched, pointer, schema)) is not None
    )

    unconstrained: tuple[str, ...] = ()
    for schema_name, properties in LIFTED_ENUMS.items():
        unconstrained += _lift_enum(patched, schema_name, properties)

    return patched, OverlayReport(
        integer_fields=counter.retyped,
        added_properties=added,
        retyped_properties=retyped,
        retyped_pointers=pointers,
        lifted_enums=unconstrained,
    )


__all__: list[str] = [
    "CENTS_PATTERN",
    "CHARGE_REFUND_PROPERTIES",
    "CHARGE_RESPONSE_PROPERTIES",
    "INTEGER_PROPERTY_NAMES",
    "LIFTED_ENUMS",
    "MISTYPED_POINTERS",
    "MISTYPED_PROPERTIES",
    "NOT_CENTS_PATTERN",
    "STATUS_CODE_PATTERN",
    "OverlayReport",
    "apply",
]
