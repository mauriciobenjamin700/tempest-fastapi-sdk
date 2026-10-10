"""What this repo corrects in the vendored Mercado Pago specification.

``vendor/mercadopago-openapi.yaml`` **does** have an upstream —
``spec3.yaml`` in ``github.com/mercadopago/openapi``, refreshed by ``make
mercadopago-fetch`` (see ``vendor/PROVENANCE.md``). What a refresh cannot do
is retire a correction here on its own: measured 2026-08-30, the provider's
document omits seven operations the provider's own SDK calls, and carries
three that answer ``404``.

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
  own path and verb. A body or response is typed only where it was
  observed against the sandbox (:data:`OBSERVED_SCHEMAS`); the rest stay
  ``dict[str, Any]``, because a shape nobody measured is worse than no
  shape.
* **Operations the document declares and the API does not route**, with no
  counterpart in the SDK to correct them towards. Removed — or, for the
  ones the sandbox answered as unrouted (:data:`UNROUTED_OPERATIONS`), kept
  and marked in their docstring, since removing a public method is a
  separate decision.
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
``GET`` probe therefore says nothing about a ``DELETE`` operation, and the
customer correction rests on the SDK alone.

**And a status that is not ``404`` is not proof by itself.** Measured
:data:`SANDBOX_PROBE_DATE`: on several prefixes a policy gate answers
before routing — ``POST /terminals/v1/<anything>`` answers ``401`` and
``POST /post-purchase/v1/claims/<id>/<anything>`` answers ``403``, for
paths that do not exist. :data:`SANDBOX_ROUTED_OPERATIONS` therefore
records an operation only when its answer differs from a made-up path
under the same prefix, and says how.
"""

from __future__ import annotations

import copy
import re
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


SDK_COVERAGE_URL: str = (
    "https://raw.githubusercontent.com/mercadopago/openapi/main/spec3.sdk.yaml"
)
"""The provider's second spec variant, annotated per operation.

``spec3.yaml`` — the document :mod:`regen_mercado_pago` vendors — carries no
statement about which official SDK implements what. ``spec3.sdk.yaml`` does,
as ``x-mp-sdk-coverage`` on every operation::

    x-mp-sdk-coverage: ['php', 'nodejs', 'java', 'python', 'ruby', 'dotnet', 'go']

That is machine-readable, from the provider, about the exact question
:data:`OFFICIAL_SDK_CALLS` answers by hand.
"""

SDK_COVERAGE_DATE: str = "2026-09-05"
"""When the cross-check below was measured."""

SDK_COVERAGE_TOTALS: dict[str, int] = {
    "annotated": 142,
    "python": 44,
    "ours": 65,
    "agreeing": 39,
}
"""What the two inventories look like side by side, on :data:`SDK_COVERAGE_DATE`.

All 142 operations in the document are annotated, 44 of them list
``python``; :data:`OFFICIAL_SDK_CALLS` holds 65 call sites read from the
sdist; 39 appear in both.
"""

SDK_COVERAGE_DISAGREEMENTS: dict[tuple[str, str], str] = {
    ("DELETE", "/v1/customers/{}/delete"): "no such call in mercadopago 3.5.0",
    ("GET", "/preapproval/export"): "no such call in mercadopago 3.5.0",
    ("GET", "/v1/payment_methods/installments"): "no such call in mercadopago 3.5.0",
    ("PUT", "/v1/chargebacks/{}"): "chargeback.py calls only search and get",
    ("PUT", "/v1/payments/{}/cancellations"): "no such call in mercadopago 3.5.0",
}
"""Operations the annotation claims for python that the python SDK does not call.

**This is why the annotation is a third opinion, not a replacement.** The
tempting reading of ``x-mp-sdk-coverage`` is that it retires the hand-read
:data:`OFFICIAL_SDK_CALLS`. Measured on :data:`SDK_COVERAGE_DATE` against
``mercadopago`` :data:`OFFICIAL_SDK_VERSION` — which is also the latest
release on PyPI, so this is not a stale-pin artefact — five of the 44
operations it marks ``python`` have no call site in the SDK's source at
all::

    $ grep -rn 'chargebacks' mercadopago-3.5.0/mercadopago/resources/*.py
    chargeback.py:30:  self._get(uri="/v1/chargebacks/search", ...)
    chargeback.py:43:  self._get(uri="/v1/chargebacks/" + self._path_param(...))

No ``PUT``. The other four spell nothing in the package.

So the annotation is evidence about **the provider's intent**, and the
sdist is evidence about **the code that ships**. Where they disagree the
code wins, for the same reason the SDK beats ``spec3.yaml`` elsewhere in
this module: an integration runs against what is implemented.

``make mercadopago-diff`` re-measures this and reports any entry that has
since appeared or disappeared, so a provider correction shows up as drift
rather than as silence.
"""


PROBE_DATE: str = "2026-08-28"
"""When :data:`PROBED_OPERATIONS` was observed."""

PROBED_OPERATIONS: dict[tuple[str, str], int] = {
    ("GET", "/authorized_payments/search"): 401,
    ("GET", "/authorized_payments/{}"): 401,
    ("GET", "/checkout/preferences/search"): 200,
    ("GET", "/checkout/preferences/{}"): 400,
    ("GET", "/instore/qr/seller/collectors/{}/pos/{}/orders"): 403,
    ("GET", "/merchant_orders/search"): 401,
    ("GET", "/merchant_orders/{}"): 401,
    ("GET", "/point/integration-api/devices"): 403,
    ("GET", "/point/integration-api/payment-intents/{}"): 403,
    ("GET", "/point/integration-api/refund/{}"): 401,
    ("GET", "/pos"): 403,
    ("GET", "/pos/{}"): 403,
    ("GET", "/post-purchase/v1/claims/search"): 403,
    ("GET", "/post-purchase/v1/claims/{}"): 403,
    ("GET", "/post-purchase/v1/claims/{}/attachments/{}"): 403,
    ("GET", "/post-purchase/v1/claims/{}/attachments/{}/download"): 403,
    ("GET", "/post-purchase/v1/claims/{}/evidences"): 403,
    ("GET", "/post-purchase/v1/claims/{}/expected-resolutions"): 403,
    ("GET", "/post-purchase/v1/claims/{}/messages"): 403,
    ("GET", "/post-purchase/v1/claims/{}/status_history"): 403,
    ("GET", "/preapproval/export"): 401,
    ("GET", "/preapproval/search"): 401,
    ("GET", "/preapproval/{}"): 401,
    ("GET", "/preapproval_plan/search"): 401,
    ("GET", "/preapproval_plan/{}"): 401,
    ("GET", "/terminals/v1/actions/{}"): 401,
    ("GET", "/terminals/v1/list"): 401,
    ("GET", "/users/me"): 403,
    ("GET", "/users/{}/pos"): 403,
    ("GET", "/users/{}/stores/search"): 403,
    ("GET", "/v1/account/release_report"): 403,
    ("GET", "/v1/account/release_report/config"): 403,
    ("GET", "/v1/account/release_report/list"): 403,
    ("GET", "/v1/account/release_report/search"): 403,
    ("GET", "/v1/account/release_report/task/{}"): 403,
    ("GET", "/v1/account/release_report/{}"): 403,
    ("GET", "/v1/account/settlement_report"): 403,
    ("GET", "/v1/account/settlement_report/config"): 403,
    ("GET", "/v1/account/settlement_report/list"): 403,
    ("GET", "/v1/account/settlement_report/search"): 403,
    ("GET", "/v1/account/settlement_report/task/{}"): 403,
    ("GET", "/v1/account/settlement_report/{}"): 403,
    ("GET", "/v1/advanced_payments/search"): 401,
    ("GET", "/v1/advanced_payments/{}"): 401,
    ("GET", "/v1/advanced_payments/{}/refunds"): 401,
    ("GET", "/v1/card_tokens/{}"): 401,
    ("GET", "/v1/chargebacks/search"): 400,
    ("GET", "/v1/chargebacks/{}"): 400,
    ("GET", "/v1/customers/search"): 401,
    ("GET", "/v1/customers/{}"): 401,
    ("GET", "/v1/customers/{}/addresses"): 401,
    ("GET", "/v1/customers/{}/addresses/{}"): 401,
    ("GET", "/v1/customers/{}/cards"): 401,
    ("GET", "/v1/customers/{}/cards/{}"): 401,
    ("GET", "/v1/identification_types"): 400,
    ("GET", "/v1/orders"): 403,
    ("GET", "/v1/orders/{}"): 403,
    ("GET", "/v1/payment_methods"): 401,
    ("GET", "/v1/payment_methods/installments"): 401,
    ("GET", "/v1/payments/search"): 401,
    ("GET", "/v1/payments/{}"): 401,
    ("GET", "/v1/payments/{}/refunds"): 401,
    ("GET", "/v1/payments/{}/refunds/{}"): 401,
    ("GET", "/v1/payouts/{}/transactions"): 400,
    ("GET", "/v1/transaction-intents/{}"): 400,
    ("GET", "/v2/wallet_connect/agreements/{}"): 403,
}
"""Status each operation answered to an unauthenticated request.

`401`, `403` and `400` mean the route exists and the auth or parameter gate
replied first. `404` would mean it is not routed — none remain, since the
three that answered it were removed.

**Only `GET` appears here, and that is not an oversight.** The probe is per
method *and* path, so it speaks for the verb it uses and no other: measured
:data:`PROBE_DATE`, ``GET /v1/customers`` answers `404` while
``POST /v1/customers`` is the endpoint the provider's SDK creates customers
with. The non-``GET`` operations were probed later, in the sandbox, with
requests built not to succeed — :data:`SANDBOX_ROUTED_OPERATIONS` and
:data:`UNROUTED_OPERATIONS`.

**Re-evaluated on :data:`SANDBOX_PROBE_DATE`, and not all of it holds.** The
same probe showed that on several prefixes ``401``/``403`` comes before
routing, so a status alone is weaker than this inventory assumed: 11 of the
entries that only this probe vouches for answer the same as a made-up path
under their prefix, and two ``GET`` answer as unrouted. Recorded in
``vendor/mercadopago-evidence.md`` section 8.4 and issue #488; the entries
are unchanged until that issue decides.
"""

UNVERIFIED_NOTE: str = (
    "\n\n**Unverified.** Neither the provider's SDK nor an unauthenticated "
    "probe covers this operation, so nothing here confirms the API routes it. "
    "See issue #227."
)
"""Appended to the description of an operation no source vouches for.

The generator renders an operation's description into the generated method's
docstring, so this reaches the consumer reading the client — which is the
point. Without it, an operation backed by the provider's own SDK and one
backed by a document of unrecorded origin (issue #228) look identical.
"""

SANDBOX_PROBE_DATE: str = "2026-10-09"
"""When :data:`SANDBOX_ROUTED_OPERATIONS` and :data:`OBSERVED_SCHEMAS` were observed."""

SANDBOX_ROUTED_OPERATIONS: dict[tuple[str, str], str] = {
    ("PUT", "/checkout/preferences/{}/expire"): (
        "with the sandbox token, 404 'The preference with identifier ... was "
        "not found'; a made-up sibling answers the generic 'resource ... not "
        "found'"
    ),
    ("POST", "/v2/wallet_connect/agreements"): "403 unauthenticated; sibling 404",
    ("DELETE", "/v2/wallet_connect/agreements/{}"): (
        "403 unauthenticated; sibling 404"
    ),
    ("POST", "/v2/wallet_connect/agreements/{}/payer_token"): (
        "403 unauthenticated; sibling 404"
    ),
    ("POST", "/v2/wallet_connect/discounts"): "403 unauthenticated; sibling 404",
    ("POST", "/v2/wallet_connect/coupons"): "403 unauthenticated; sibling 404",
    ("POST", "/v1/payouts"): "400 'Invalid site' unauthenticated; sibling 404",
    ("PUT", "/v1/payouts/{}/transactions/{}/cancel"): (
        "400 'Invalid site' unauthenticated; sibling 404"
    ),
    ("POST", "/v1/transaction-intents/process"): (
        "400 'Invalid site' unauthenticated; sibling 405"
    ),
    ("DELETE", "/instore/qr/seller/collectors/{}/pos/{}/orders"): (
        "with the sandbox token, 400 'pos_obtainment_by_external_id_error'; sibling 404"
    ),
    ("PUT", "/instore/qr/seller/collectors/{}/stores/{}/pos/{}/orders"): (
        "with the sandbox token, 400 'Collector ID and Caller ID must be the "
        "same'; sibling 404"
    ),
    ("POST", "/instore/orders/{}/confirmation"): "403 unauthenticated; sibling 404",
    ("POST", "/instore/orders/qr/seller/collectors/{}/pos/{}/qrs"): (
        "403 unauthenticated; sibling 404"
    ),
    ("PUT", "/instore/orders/qr/seller/collectors/{}/pos/{}/qrs"): (
        "403 unauthenticated; sibling 404"
    ),
    ("DELETE", "/mpmobile/instore/qr/{}/{}"): (
        "403 unauthenticated, 403 'Forbidden' from the service with the "
        "sandbox token; sibling 404 'Route not found'"
    ),
    ("POST", "/users/{}/stores"): (
        "400 'Malformed Json' unauthenticated; sibling 403 from the edge proxy"
    ),
    ("PUT", "/users/{}/stores/{}"): (
        "404 'store_not_found' unauthenticated; sibling 403 from the edge proxy"
    ),
    ("DELETE", "/users/{}/stores/{}"): (
        "404 'store_not_found' unauthenticated; sibling 403 from the edge proxy"
    ),
    ("POST", "/pos"): "403 unauthenticated; sibling 404",
    ("PUT", "/pos/{}"): "403 unauthenticated; sibling 404",
    ("DELETE", "/pos/{}"): "403 unauthenticated; sibling 404",
    ("POST", "/v1/customers/{}/addresses"): (
        "401 unauthenticated; sibling 404 from the customers service"
    ),
    ("PUT", "/v1/customers/{}/addresses/{}"): (
        "401 unauthenticated; sibling 404 from the customers service"
    ),
    ("DELETE", "/v1/customers/{}/addresses/{}"): (
        "401 unauthenticated; sibling 404 from the customers service"
    ),
    ("POST", "/v1/account/release_report/config"): (
        "with the sandbox token, 400; sibling 404 'Resource ... not found'"
    ),
    ("PUT", "/v1/account/release_report/config"): (
        "with the sandbox token, 400; sibling 404 'Resource ... not found'"
    ),
    ("POST", "/v1/account/release_report"): (
        "with the sandbox token, 400; sibling 404 'Resource ... not found'"
    ),
    ("POST", "/v1/account/release_report/schedule"): (
        "with the sandbox token, 400; sibling 404 'Resource ... not found'"
    ),
    ("POST", "/v1/account/settlement_report/config"): (
        "with the sandbox token, 400 'Error binding request'; sibling 404"
    ),
    ("PUT", "/v1/account/settlement_report/config"): (
        "with the sandbox token, 400 'Error binding request'; sibling 404"
    ),
    ("POST", "/v1/account/settlement_report"): (
        "with the sandbox token, 400; sibling 404 'Resource ... not found'"
    ),
    ("POST", "/v1/account/settlement_report/schedule"): (
        "with the sandbox token, 404 'Configuration not found. Please create "
        "a configuration first.'; sibling 404 'Resource ... not found'"
    ),
}
"""Non-``GET`` operations the sandbox showed are routed, and how.

Measured :data:`SANDBOX_PROBE_DATE` with requests that cannot succeed:
every body was malformed JSON (``{``) and every path id was
``999999999999``, sent once without credentials and once with a sandbox
(``TEST-``) token. Each entry is recorded only when its answer differs from
a made-up path under the same prefix (the *sibling*) — a status alone does
not count, because some prefixes answer ``401``/``403`` before routing.

Eleven of the 47 operations nothing vouched for are not here: their answer
matched the sibling's (``/terminals/v1``, ``/post-purchase/v1/claims/{id}``,
two ``/point/integration-api`` refunds), or the only safe probe was the
unauthenticated one and it matched too (the two ``DELETE .../schedule``,
which with a token would switch a real schedule off). Those keep
:data:`UNVERIFIED_NOTE`. Four more answered as unrouted and are in
:data:`UNROUTED_OPERATIONS`.

"Routed" here is an inference from a difference, and the strength of the
difference varies by entry. An answer from the service itself (a body
naming the resource, a binding error) is strong. A bare ``403`` where the
sibling gets ``404`` shows the gateway treats the path differently from a
made-up one, which is what a configured route looks like — but it is not
the service answering. Each entry's text says which kind it is.
"""

OBSERVED_SCHEMAS: dict[str, dict[str, Any]] = {
    "AuthenticatedUserIdentification": {
        "type": "object",
        "properties": {
            "type": {"type": "string"},
            "number": {"type": "string"},
        },
    },
    "AuthenticatedUserPhone": {
        "type": "object",
        "properties": {
            "area_code": {"type": "string"},
            "number": {"type": "string"},
            "extension": {"type": "string"},
            "verified": {"type": "boolean"},
        },
    },
    "AuthenticatedUserThumbnail": {
        "type": "object",
        "properties": {
            "picture_id": {"type": "string"},
            "picture_url": {"type": "string"},
        },
    },
    "AuthenticatedUserCompany": {
        "type": "object",
        "properties": {
            "brand_name": {"type": "string"},
            "corporate_name": {"type": "string"},
            "identification": {"type": "string"},
            "soft_descriptor": {"type": "string"},
            "city_tax_id": {"type": "string"},
            "state_tax_id": {"type": "string"},
            "cust_type_id": {"type": "string"},
        },
    },
    "AuthenticatedUser": {
        "type": "object",
        "description": (
            "The account an access token belongs to. Every field was present "
            f"in the response observed on {SANDBOX_PROBE_DATE}; none is "
            "declared required, because one observation cannot say which "
            "the provider always sends. Fields observed only as `null`, and "
            "the nested reputation and status blocks, are not declared and "
            "are kept as extra fields."
        ),
        "properties": {
            "id": {"type": "integer"},
            "nickname": {"type": "string"},
            "registration_date": {"type": "string", "format": "date-time"},
            "first_name": {"type": "string"},
            "last_name": {"type": "string"},
            "country_id": {"type": "string"},
            "site_id": {"type": "string"},
            "email": {"type": "string"},
            "secure_email": {"type": "string"},
            "user_type": {"type": "string"},
            "tags": {"type": "array", "items": {"type": "string"}},
            "points": {"type": "integer"},
            "permalink": {"type": "string"},
            "seller_experience": {"type": "string"},
            "identification": {
                "$ref": "#/components/schemas/AuthenticatedUserIdentification"
            },
            "phone": {"$ref": "#/components/schemas/AuthenticatedUserPhone"},
            "thumbnail": {"$ref": "#/components/schemas/AuthenticatedUserThumbnail"},
            "company": {"$ref": "#/components/schemas/AuthenticatedUserCompany"},
        },
    },
}
"""Schemas read off responses the sandbox returned on :data:`SANDBOX_PROBE_DATE`.

Each field is one that appeared with a non-null value; its type is the JSON
type observed. The redacted payloads they were read from are the fixtures
under ``tests/integrations/payment/mercado_pago/fixtures/``, and a test
validates each fixture against the generated model, so a schema here that
stops matching its observation fails offline.
"""


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
        response_schema (str | None): Name of the :data:`OBSERVED_SCHEMAS`
            entry the ``200`` response renders as, or ``None`` when the
            response was never observed and stays ``dict[str, Any]``.
    """

    method: str
    path: str
    operation_id: str
    summary: str
    description: str
    source: str
    query: tuple[str, ...] = ()
    has_body: bool = False
    response_schema: str | None = None


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

UNROUTED_OPERATIONS: tuple[DeadOperation, ...] = (
    DeadOperation(
        method="put",
        path="/v1/chargebacks/{id}",
        evidence=(
            f"measured {SANDBOX_PROBE_DATE}, PUT /v1/chargebacks/<id> answers "
            "404 'Request method 'PUT' is not supported' with and without the "
            "sandbox token — the chargebacks service itself names the verb. "
            "mercadopago 3.5.0 chargeback.py calls only search and get"
        ),
    ),
    DeadOperation(
        method="put",
        path="/v1/payments/{id}/cancellations",
        evidence=(
            f"measured {SANDBOX_PROBE_DATE}, it answers the edge's 404 "
            "'resource not found' with and without the sandbox token, the "
            "same body a made-up path gets, while PUT /v1/payments/<id> "
            "reaches the payments service (400 'Bad JSON format')"
        ),
    ),
    DeadOperation(
        method="patch",
        path="/instore/integrator",
        evidence=(
            f"measured {SANDBOX_PROBE_DATE}, PATCH, POST and PUT on "
            "/instore/integrator all answer the edge's 404 'resource not "
            "found' with and without the sandbox token. The GET on the same "
            "path was removed on 2026-08-28"
        ),
    ),
    DeadOperation(
        method="put",
        path="/mpmobile/instore/qr/{user_id}/{external_id}",
        evidence=(
            f"measured {SANDBOX_PROBE_DATE}, PUT answers 405 'Not Allowed' "
            "from the edge proxy with and without the sandbox token, as does "
            "PATCH. POST on the same path reaches the service (400 "
            "'invalid_caller_id' with the token), but turning the PUT into a "
            "POST would be a guess about the operation, so it is dropped "
            "rather than moved"
        ),
    ),
)
"""Operations the sandbox answered as unrouted, kept and marked rather than removed.

Measured on :data:`SANDBOX_PROBE_DATE` with their own verb, by requests that
cannot succeed (malformed body, an id that does not exist); each answered
the way a made-up path does. They stay in the client because removing a
public method is a breaking change this measurement alone does not
justify; :data:`UNROUTED_NOTE` puts the measurement in the docstring a
consumer reads instead.
"""

UNROUTED_NOTE: str = (
    "\n\n**Not routed.** Probed against the sandbox and answered the way a "
    "path the API does not route answers — {evidence}."
)
"""Appended to an operation in :data:`UNROUTED_OPERATIONS`, filled per entry."""

ADDED_OPERATIONS: tuple[AddedOperation, ...] = (
    AddedOperation(
        method="get",
        path="/users/me",
        operation_id="getAuthenticatedUser",
        summary="Get the authenticated user",
        description=(
            "Returns the account the credentials belong to.\n\n"
            "Absent from the vendored document. The response is modelled "
            f"from the one the sandbox returned on {SANDBOX_PROBE_DATE}: "
            "every declared field was observed, none is required, and "
            "fields not declared are kept as extra fields rather than "
            "dropped."
        ),
        source="resources/user.py:get",
        response_schema="AuthenticatedUser",
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
        unverified_operations (tuple[str, ...]): ``METHOD path`` per
            operation no source vouches for, each marked in its own
            description.
        unrouted_operations (tuple[str, ...]): ``METHOD path`` per
            operation marked with :data:`UNROUTED_NOTE`.
        collisions (tuple[str, ...]): Corrections left in place because the
            destination already declares that verb. Reported rather than
            resolved: which of the two is right is a question about the
            API, not about this file.
    """

    moved_paths: tuple[str, ...] = ()
    added_operations: tuple[str, ...] = ()
    removed_operations: tuple[str, ...] = ()
    unverified_operations: tuple[str, ...] = ()
    unrouted_operations: tuple[str, ...] = ()
    collisions: tuple[str, ...] = ()


def normalise(method: str, path: str) -> tuple[str, str]:
    """Spell one operation the way the pinned inventories spell it.

    Args:
        method (str): The HTTP verb, any case.
        path (str): The path template, with named placeholders.

    Returns:
        tuple[str, str]: Upper-case verb, and the path with every
        placeholder collapsed to ``{}`` and no trailing slash.
    """
    collapsed = re.sub(r"\{[^}]*\}", "{}", path).rstrip("/") or "/"
    return method.upper(), collapsed


def _mark_unverified(paths: dict[str, Any]) -> tuple[str, ...]:
    """Append :data:`UNVERIFIED_NOTE` to every operation nothing vouches for.

    Args:
        paths (dict[str, Any]): The document's ``paths`` block, patched in
            place.

    Returns:
        tuple[str, ...]: ``METHOD path`` for each operation marked.

    Three sources can vouch for an operation: the provider's own SDK calls
    it, an unauthenticated ``GET`` probe found it routed, or the sandbox
    probe told it apart from a made-up sibling path. Everything else is
    carried on the word of a document whose origin is unrecorded, and
    saying so in the generated docstring is the difference between an
    operation a consumer can rely on and one they should verify before
    building on.
    """
    unrouted = {normalise(entry.method, entry.path) for entry in UNROUTED_OPERATIONS}
    marked: list[str] = []
    for path, item in paths.items():
        if not isinstance(item, dict):
            continue
        for method, operation in item.items():
            if method not in _VERBS or not isinstance(operation, dict):
                continue
            key = normalise(method, str(path))
            if (
                key in OFFICIAL_SDK_CALLS
                or key in PROBED_OPERATIONS
                or key in SANDBOX_ROUTED_OPERATIONS
                or key in unrouted
            ):
                continue
            description = str(operation.get("description") or "")
            if UNVERIFIED_NOTE.strip() in description:
                continue
            operation["description"] = description + UNVERIFIED_NOTE
            marked.append(f"{method.upper()} {path}")
    return tuple(marked)


def _mark_unrouted(paths: dict[str, Any]) -> tuple[str, ...]:
    """Append :data:`UNROUTED_NOTE` to every operation in :data:`UNROUTED_OPERATIONS`.

    Args:
        paths (dict[str, Any]): The document's ``paths`` block, patched in
            place.

    Returns:
        tuple[str, ...]: ``METHOD path`` for each operation marked. An
        operation already carrying the note is skipped, so applying twice
        does not stack it.
    """
    marked: list[str] = []
    for unrouted in UNROUTED_OPERATIONS:
        item = paths.get(unrouted.path)
        if not isinstance(item, dict):
            continue
        operation = item.get(unrouted.method)
        if not isinstance(operation, dict):
            continue
        description = str(operation.get("description") or "")
        if "**Not routed.**" in description:
            continue
        note = UNROUTED_NOTE.format(evidence=unrouted.evidence)
        operation["description"] = description + note
        marked.append(f"{unrouted.method.upper()} {unrouted.path}")
    return tuple(marked)


def _operation(added: AddedOperation) -> dict[str, Any]:
    """Render one added operation as an OpenAPI operation object.

    Args:
        added (AddedOperation): The operation to render.

    Returns:
        dict[str, Any]: The operation object, ready to attach to a path.
    """
    response: dict[str, Any] = dict(_FREE_OBJECT)
    description = "The provider's response, unmodelled."
    if added.response_schema is not None:
        response = {"$ref": f"#/components/schemas/{added.response_schema}"}
        description = f"The provider's response, as observed on {SANDBOX_PROBE_DATE}."
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
                "description": description,
                "content": {"application/json": {"schema": response}},
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

    schemas = patched.setdefault("components", {}).setdefault("schemas", {})
    if isinstance(schemas, dict):
        for name, schema in OBSERVED_SCHEMAS.items():
            schemas.setdefault(name, copy.deepcopy(schema))

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
        unverified_operations=_mark_unverified(paths),
        unrouted_operations=_mark_unrouted(paths),
        collisions=tuple(collisions),
    )


__all__: list[str] = [
    "ADDED_OPERATIONS",
    "DEAD_OPERATIONS",
    "OBSERVED_SCHEMAS",
    "OFFICIAL_SDK_CALLS",
    "OFFICIAL_SDK_VERSION",
    "PATH_CORRECTIONS",
    "PROBED_OPERATIONS",
    "PROBE_DATE",
    "SANDBOX_PROBE_DATE",
    "SANDBOX_ROUTED_OPERATIONS",
    "SDK_COVERAGE_DATE",
    "SDK_COVERAGE_DISAGREEMENTS",
    "SDK_COVERAGE_TOTALS",
    "SDK_COVERAGE_URL",
    "UNROUTED_NOTE",
    "UNROUTED_OPERATIONS",
    "UNVERIFIED_NOTE",
    "AddedOperation",
    "DeadOperation",
    "OverlayReport",
    "PathCorrection",
    "apply",
]
