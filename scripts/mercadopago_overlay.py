"""What this repo corrects in the vendored Mercado Pago specification.

Mercado Pago **publishes no OpenAPI document**: checked 2026-08-28,
``api.mercadopago.com/openapi{,.json}`` answer ``404`` and the
``mercadopago`` GitHub organisation carries SDKs, carts and samples but no
specification repository. So ``vendor/mercadopago-openapi.yaml`` has no
upstream to diff against, and no refresh that would carry a provider's fix
in on its own.

**The provider's own Python SDK is the authority instead.** ``mercadopago``
on PyPI is written by Mercado Pago and names the URL of every operation it
calls, so where our document and that SDK disagree, the SDK wins — that is
the rule this module enforces, one named correction at a time.

The rule is *authority on conflict*, not a ceiling on the surface. The SDK
is a thin wrapper over the resources most integrations use; our document
carries 85 operations it never touches — settlement and release reports,
post-purchase claims, in-store QR, terminals, wallet connect, stores and
POS — and probing them answers ``401``/``403``, not ``404``. Silence from
the SDK is not denial, so those stay.

Four kinds of correction, in the order :func:`apply` runs them:

* **Paths the API does not route**, where the SDK spells the same operation
  differently. The SDK's spelling wins.
* **Operations the SDK calls and the document omits.** Added with the SDK's
  own path and verb. Their bodies and responses are ``dict[str, Any]``:
  nobody here has credentials to observe either, and a shape nobody
  measured is worse than no shape.
* **Operations the document declares and the API does not route**, with no
  counterpart in the SDK to correct them towards. Removed.
* Nothing else. An endpoint neither source knows about is not invented
  here — that is the defect v0.259.0 shipped on OpenPix and v0.260.0
  removed.

## How a missing route is told from a guarded one

An unauthenticated request to ``api.mercadopago.com`` answers ``401``,
``403`` or ``400`` when the route exists and the auth or parameter gate
replies first, and ``404`` when it is not routed.

**That probe is per method *and* path, so it only validates the verb it
uses.** Measured 2026-08-28: ``GET /v1/customers`` answers ``404`` while
``POST /v1/customers`` is the endpoint the SDK creates customers with. A
``GET`` probe therefore says nothing about a ``DELETE`` operation — which
is why every removal below is a ``GET``, and why the customer correction
rests on the SDK alone.
"""

from __future__ import annotations

import copy
from dataclasses import dataclass
from typing import Any

OFFICIAL_SDK_VERSION: str = "3.5.0"
"""The release of ``mercadopago`` (PyPI) :data:`OFFICIAL_SDK_CALLS` was read from.

``make mercadopago-diff`` reads the current release and reports the
difference, so a newer SDK shows up as work to do rather than as silence.
"""

OFFICIAL_SDK_CALLS: frozenset[tuple[str, str]] = frozenset(
    {
        ("GET", "/authorized_payments/search"),
        ("GET", "/authorized_payments/{}"),
        ("POST", "/checkout/preferences"),
        ("GET", "/checkout/preferences/search"),
        ("GET", "/checkout/preferences/{}"),
        ("PUT", "/checkout/preferences/{}"),
        ("POST", "/merchant_orders"),
        ("GET", "/merchant_orders/search"),
        ("GET", "/merchant_orders/{}"),
        ("PUT", "/merchant_orders/{}"),
        ("POST", "/oauth/token"),
        ("GET", "/point/integration-api/devices"),
        ("POST", "/point/integration-api/devices/{}/payment-intents"),
        ("DELETE", "/point/integration-api/devices/{}/payment-intents/{}"),
        ("GET", "/point/integration-api/payment-intents/{}"),
        ("POST", "/preapproval"),
        ("GET", "/preapproval/search"),
        ("GET", "/preapproval/{}"),
        ("PUT", "/preapproval/{}"),
        ("POST", "/preapproval_plan"),
        ("GET", "/preapproval_plan/search"),
        ("GET", "/preapproval_plan/{}"),
        ("PUT", "/preapproval_plan/{}"),
        ("GET", "/users/me"),
        ("POST", "/v1/advanced_payments"),
        ("GET", "/v1/advanced_payments/search"),
        ("GET", "/v1/advanced_payments/{}"),
        ("PUT", "/v1/advanced_payments/{}"),
        ("POST", "/v1/advanced_payments/{}/disbursements/{}/refunds"),
        ("POST", "/v1/advanced_payments/{}/disburses"),
        ("GET", "/v1/advanced_payments/{}/refunds"),
        ("POST", "/v1/advanced_payments/{}/refunds"),
        ("POST", "/v1/card_tokens"),
        ("GET", "/v1/card_tokens/{}"),
        ("GET", "/v1/chargebacks/search"),
        ("GET", "/v1/chargebacks/{}"),
        ("POST", "/v1/customers"),
        ("GET", "/v1/customers/search"),
        ("DELETE", "/v1/customers/{}"),
        ("GET", "/v1/customers/{}"),
        ("PUT", "/v1/customers/{}"),
        ("GET", "/v1/customers/{}/cards"),
        ("POST", "/v1/customers/{}/cards"),
        ("DELETE", "/v1/customers/{}/cards/{}"),
        ("GET", "/v1/customers/{}/cards/{}"),
        ("PUT", "/v1/customers/{}/cards/{}"),
        ("GET", "/v1/identification_types"),
        ("GET", "/v1/orders"),
        ("POST", "/v1/orders"),
        ("GET", "/v1/orders/{}"),
        ("POST", "/v1/orders/{}/cancel"),
        ("POST", "/v1/orders/{}/capture"),
        ("POST", "/v1/orders/{}/process"),
        ("POST", "/v1/orders/{}/refund"),
        ("POST", "/v1/orders/{}/transactions"),
        ("DELETE", "/v1/orders/{}/transactions/{}"),
        ("PUT", "/v1/orders/{}/transactions/{}"),
        ("GET", "/v1/payment_methods"),
        ("POST", "/v1/payments"),
        ("GET", "/v1/payments/search"),
        ("GET", "/v1/payments/{}"),
        ("PUT", "/v1/payments/{}"),
        ("GET", "/v1/payments/{}/refunds"),
        ("POST", "/v1/payments/{}/refunds"),
        ("GET", "/v1/payments/{}/refunds/{}"),
    }
)
"""Every ``(METHOD, path)`` the provider's SDK calls, path params as ``{}``.

Pinned so the authority can be checked offline: a test asserts the
generated document covers every entry. Refresh it with
``make mercadopago-diff``, which reads the SDK from PyPI.

Resolved with ``ast``, including URLs the SDK builds through a local
variable — ``disbursement_refund.py`` builds three that way, and reading
only literal arguments hid two real operations on the first pass.
"""


_VERBS: frozenset[str] = frozenset(
    {"get", "post", "put", "patch", "delete", "head", "options"}
)
"""Keys under a path item that denote an operation, not metadata."""

_FREE_OBJECT: dict[str, Any] = {"type": "object", "additionalProperties": True}
"""A body or response nobody here has observed, rendered ``dict[str, Any]``."""


@dataclass(frozen=True)
class PathCorrection:
    """One operation the vendored document routes to the wrong path.

    Attributes:
        method (str): The HTTP verb, lower case.
        wrong (str): The path template as vendored.
        right (str): The path template the SDK calls.
        evidence (str): Why the SDK's spelling wins, in one line.
    """

    method: str
    wrong: str
    right: str
    evidence: str


@dataclass(frozen=True)
class DeadOperation:
    """One operation the document declares and the API does not route.

    Attributes:
        method (str): The HTTP verb, lower case.
        path (str): The path template to drop.
        evidence (str): The measurement, and why no correction replaces it.
    """

    method: str
    path: str
    evidence: str


@dataclass(frozen=True)
class AddedOperation:
    """One operation the SDK calls and the document omits.

    Attributes:
        method (str): The HTTP verb, lower case.
        path (str): The path template, in this document's parameter names.
        operation_id (str): Drives the generated method name.
        summary (str): One line, as the generator renders it.
        description (str): What it does, and what is not modelled.
        source (str): The SDK module and method that calls it.
        query (tuple[str, ...]): Query parameters to declare.
        has_body (bool): Whether the operation takes a request body.
    """

    method: str
    path: str
    operation_id: str
    summary: str
    description: str
    source: str
    query: tuple[str, ...] = ()
    has_body: bool = False


PATH_CORRECTIONS: tuple[PathCorrection, ...] = (
    PathCorrection(
        method="delete",
        wrong="/v1/customers/{id}/delete",
        right="/v1/customers/{id}",
        evidence=(
            "mercadopago 3.5.0 resources/customer.py:delete calls "
            "DELETE /v1/customers/<id>. The SDK is the only evidence here: "
            "an unauthenticated GET probe cannot speak for a DELETE "
            "operation, since 404 is returned per method and path"
        ),
    ),
    PathCorrection(
        method="get",
        wrong="/authorized_payments",
        right="/authorized_payments/search",
        evidence=(
            "the operation is a search — its parameters are preapproval_id, "
            "status, limit, offset — and mercadopago 3.5.0 "
            "resources/authorized_payment.py:search calls "
            "GET /authorized_payments/search; measured 2026-08-28, that GET "
            "answers 401 and GET /authorized_payments answers 404"
        ),
    ),
)
"""Operations whose path the SDK spells differently, and correctly."""

DEAD_OPERATIONS: tuple[DeadOperation, ...] = (
    DeadOperation(
        method="get",
        path="/instore/integrator",
        evidence=(
            "measured 2026-08-28, GET /instore/integrator answers 404 while "
            "every other /instore path in this document answers 401 or 403. "
            "The SDK does not cover it, so there is no second source to "
            "correct it towards. The PATCH on the same path stays: 404 is "
            "per method, and no probe speaks for it"
        ),
    ),
    DeadOperation(
        method="get",
        path="/stores/{id}",
        evidence=(
            "measured 2026-08-28, GET /stores/123 answers 404 while "
            "GET /users/123/stores/search answers 403. Turning one into the "
            "other would be a guess, so the operation is dropped rather "
            "than moved"
        ),
    ),
    DeadOperation(
        method="get",
        path="/post-purchase/v1/claims/reasons/{reason_id}",
        evidence=(
            "measured 2026-08-28, it answers 404 while every other "
            "/post-purchase path in this document answers 403"
        ),
    ),
)
"""Operations removed because the API does not route them."""

ADDED_OPERATIONS: tuple[AddedOperation, ...] = (
    AddedOperation(
        method="get",
        path="/users/me",
        operation_id="getAuthenticatedUser",
        summary="Get the authenticated user",
        description=(
            "Returns the account the credentials belong to.\n\n"
            "Absent from the vendored document. The response is not "
            "modelled — this repository has no Mercado Pago credentials to "
            "observe its shape — so the method answers `dict[str, Any]` and "
            "drops nothing."
        ),
        source="resources/user.py:get",
    ),
    AddedOperation(
        method="get",
        path="/v1/advanced_payments/search",
        operation_id="searchAdvancedPayments",
        summary="Search advanced payments",
        description=(
            "Searches advanced payments matching the given filters.\n\n"
            "Absent from the vendored document. `limit` and `offset` are "
            "declared because every other search in this document declares "
            "them — that is this document's convention, not a measurement. "
            "The remaining filters and the response are not modelled."
        ),
        source="resources/advanced_payment.py:search",
        query=("limit", "offset"),
    ),
    AddedOperation(
        method="get",
        path="/v1/advanced_payments/{advanced_payment_id}/refunds",
        operation_id="listDisbursementRefunds",
        summary="List the refunds of an advanced payment",
        description=(
            "Lists every disbursement refund of one advanced payment.\n\n"
            "Absent from the vendored document, and invisible to a reader "
            "that only follows literal arguments: the SDK builds this URL "
            "through a local variable. The response is not modelled."
        ),
        source="resources/disbursement_refund.py:list_all",
    ),
    AddedOperation(
        method="post",
        path="/v1/advanced_payments/{advanced_payment_id}/refunds",
        operation_id="createDisbursementRefunds",
        summary="Refund an advanced payment",
        description=(
            "Creates a refund covering the advanced payment's "
            "disbursements.\n\n"
            "Absent from the vendored document. Neither the body nor the "
            "response is modelled — nobody here has credentials to observe "
            "either — so both are `dict[str, Any]`."
        ),
        source="resources/disbursement_refund.py:create_all",
        has_body=True,
    ),
    AddedOperation(
        method="post",
        path=(
            "/v1/advanced_payments/{advanced_payment_id}"
            "/disbursements/{disbursement_id}/refunds"
        ),
        operation_id="createDisbursementRefund",
        summary="Refund one disbursement of an advanced payment",
        description=(
            "Refunds a single disbursement, in full or by amount.\n\n"
            "Absent from the vendored document, and built through a local "
            "variable in the SDK. Neither the body nor the response is "
            "modelled."
        ),
        source="resources/disbursement_refund.py:create",
        has_body=True,
    ),
    AddedOperation(
        method="post",
        path="/v1/advanced_payments/{advanced_payment_id}/disburses",
        operation_id="updateAdvancedPaymentReleaseDate",
        summary="Update the release date of a disbursement",
        description=(
            "Moves the money release date of an advanced payment's "
            "disbursements.\n\n"
            "Absent from the vendored document. Neither the body nor the "
            "response is modelled."
        ),
        source="resources/advanced_payment.py:update_release_date",
        has_body=True,
    ),
    AddedOperation(
        method="get",
        path="/v1/chargebacks/search",
        operation_id="searchChargebacks",
        summary="Search chargebacks",
        description=(
            "Searches chargebacks matching the given filters.\n\n"
            "Absent from the vendored document. `limit` and `offset` follow "
            "this document's convention for a search; the remaining filters "
            "and the response are not modelled."
        ),
        source="resources/chargeback.py:search",
        query=("limit", "offset"),
    ),
)
"""Operations the provider's SDK calls and the vendored document omits.

Every one is confirmed twice: the SDK calls it, and an unauthenticated
request to the path answers ``401`` or ``400`` rather than ``404``.

Their bodies and responses are ``dict[str, Any]`` on purpose. The path and
the verb are measured; the shape is not, and this repository has no Mercado
Pago credentials to observe it. Declaring a shape nobody measured is the
defect v0.259.0 shipped on OpenPix — with the difference that there, not
even the endpoint had a source.
"""


@dataclass(frozen=True)
class OverlayReport:
    """What :func:`apply` changed.

    Attributes:
        moved_paths (tuple[str, ...]): ``METHOD wrong -> right`` per
            operation rewritten.
        added_operations (tuple[str, ...]): ``METHOD path`` per operation
            declared from the SDK.
        removed_operations (tuple[str, ...]): ``METHOD path`` per operation
            dropped as unrouted.
        collisions (tuple[str, ...]): Corrections left in place because the
            destination already declares that verb. Reported rather than
            resolved: which of the two is right is a question about the
            API, not about this file.
    """

    moved_paths: tuple[str, ...] = ()
    added_operations: tuple[str, ...] = ()
    removed_operations: tuple[str, ...] = ()
    collisions: tuple[str, ...] = ()


def _operation(added: AddedOperation) -> dict[str, Any]:
    """Render one added operation as an OpenAPI operation object.

    Args:
        added (AddedOperation): The operation to render.

    Returns:
        dict[str, Any]: The operation object, ready to attach to a path.
    """
    parameters: list[dict[str, Any]] = []
    for name in _path_parameters(added.path):
        parameters.append(
            {
                "name": name,
                "in": "path",
                "required": True,
                "schema": {"type": "string"},
            }
        )
    for name in added.query:
        parameters.append(
            {
                "name": name,
                "in": "query",
                "required": False,
                "schema": {"type": "integer"},
            }
        )
    operation: dict[str, Any] = {
        "operationId": added.operation_id,
        "summary": added.summary,
        "description": (
            f"{added.description}\n\n"
            f"Declared by `scripts/mercadopago_overlay.py` from "
            f"mercadopago {OFFICIAL_SDK_VERSION} `{added.source}`."
        ),
        "parameters": parameters,
        "responses": {
            "200": {
                "description": "The provider's response, unmodelled.",
                "content": {"application/json": {"schema": dict(_FREE_OBJECT)}},
            }
        },
    }
    if added.has_body:
        operation["requestBody"] = {
            "required": True,
            "content": {"application/json": {"schema": dict(_FREE_OBJECT)}},
        }
    return operation


def _path_parameters(path: str) -> list[str]:
    """Read the placeholders a path template interpolates.

    Args:
        path (str): The path template.

    Returns:
        list[str]: The placeholder names, in template order.
    """
    names: list[str] = []
    for chunk in path.split("{")[1:]:
        head, _, _ = chunk.partition("}")
        if head:
            names.append(head)
    return names


def apply(document: dict[str, Any]) -> tuple[dict[str, Any], OverlayReport]:
    """Return a corrected copy of the specification.

    Args:
        document (dict[str, Any]): The loaded vendored specification.

    Returns:
        tuple[dict[str, Any], OverlayReport]: The patched document and a
        summary of what changed. The input is not mutated.

    Corrections move one verb at a time, because a destination that already
    exists is the normal case rather than the exception: ``/v1/customers/
    {id}`` already carries ``get`` and ``put``, and the correction only adds
    the ``delete`` that was misspelled. Moving the whole path item would
    drop the two that were already right.

    Every family retires on its own — a correction whose source path is
    gone, an addition the document already declares, a removal of an
    operation nobody declares any more — so a future document that no
    longer needs this file produces an empty report instead of an error.
    """
    patched = copy.deepcopy(document)
    paths = patched.get("paths")
    if not isinstance(paths, dict):
        return patched, OverlayReport()

    moved: list[str] = []
    collisions: list[str] = []
    for correction in PATH_CORRECTIONS:
        source = paths.get(correction.wrong)
        if not isinstance(source, dict) or correction.method not in source:
            continue
        target = paths.setdefault(correction.right, {})
        if not isinstance(target, dict):
            continue
        verb = correction.method
        if verb in target:
            collisions.append(f"{verb.upper()} {correction.right} already declared")
            continue
        target[verb] = source.pop(verb)
        moved.append(f"{verb.upper()} {correction.wrong} -> {correction.right}")
        if not any(key in _VERBS for key in source):
            paths.pop(correction.wrong)

    removed: list[str] = []
    for dead in DEAD_OPERATIONS:
        item = paths.get(dead.path)
        if not isinstance(item, dict) or dead.method not in item:
            continue
        item.pop(dead.method)
        removed.append(f"{dead.method.upper()} {dead.path}")
        if not any(key in _VERBS for key in item):
            paths.pop(dead.path)

    added: list[str] = []
    for operation in ADDED_OPERATIONS:
        item = paths.setdefault(operation.path, {})
        if not isinstance(item, dict) or operation.method in item:
            continue
        item[operation.method] = _operation(operation)
        added.append(f"{operation.method.upper()} {operation.path}")

    return patched, OverlayReport(
        moved_paths=tuple(moved),
        added_operations=tuple(added),
        removed_operations=tuple(removed),
        collisions=tuple(collisions),
    )


__all__: list[str] = [
    "ADDED_OPERATIONS",
    "DEAD_OPERATIONS",
    "OFFICIAL_SDK_CALLS",
    "OFFICIAL_SDK_VERSION",
    "PATH_CORRECTIONS",
    "AddedOperation",
    "DeadOperation",
    "OverlayReport",
    "PathCorrection",
    "apply",
]
