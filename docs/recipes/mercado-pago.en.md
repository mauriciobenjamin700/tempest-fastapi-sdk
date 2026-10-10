# Mercado Pago: charging on Brazil's most-used gateway

Pix, cards, boleto and in-person payments, with the whole surface already
generated from the provider's own specification.

## Installing and connecting

```python
from tempest_fastapi_sdk import HTTPClient
from tempest_fastapi_sdk.integrations.payment.mercado_pago import (
    DEFAULT_BASE_URL,
    MercadoPagoClient,
)

http: HTTPClient = HTTPClient(
    base_url=DEFAULT_BASE_URL,
    default_headers={"Authorization": "Bearer <seu access token>"},
)
client: MercadoPagoClient = MercadoPagoClient(http)
```

Or through the settings mixin, which settles the prefix for you:

```python
from tempest_fastapi_sdk import HTTPClient, MercadoPagoSettings
from tempest_fastapi_sdk.integrations.payment.mercado_pago import MercadoPagoClient


def build_client(settings: MercadoPagoSettings) -> MercadoPagoClient:
    """Build the client from configuration.

    Args:
        settings (MercadoPagoSettings): The loaded settings.

    Returns:
        MercadoPagoClient: The configured client.
    """
    return MercadoPagoClient(HTTPClient(**settings.mercado_pago_kwargs()))
```

!!! danger "There is no sandbox host"
    Measured on the pinned specification: `servers` has **one** entry,
    `https://api.mercadopago.com`. What separates a test charge from a real
    one is **which token** you are holding, not which host you call.

    This is the opposite of OpenPix, where the environment switches the
    domain. Here a production token pointed at this same URL moves real
    money, and no configuration stops it.

## Money is in reais, not cents

```python
from tempest_fastapi_sdk.integrations.payment.mercado_pago import (
    from_cents,
    to_cents,
)


def example() -> tuple[int, str]:
    """Convert both ways.

    Returns:
        tuple[int, str]: Cents parsed from reais, and reais rendered back.
    """
    cents: int = to_cents(19.9)
    return cents, str(from_cents(cents))
```

!!! warning "The factor-of-100 trap"
    Mercado Pago states money in **reais**, in two ways. Counted in the
    components of the corrected document on 2026-10-10: 21 properties typed
    `number` / `format: float` (among them `PreferenceItem.unit_price`,
    `Refund.amount`, `MerchantOrder.total_amount`) and, on the Orders API, 7
    amount fields as **decimal strings** (`OrderRequest.total_amount`,
    `OrderPayment.amount`, `Order.total_paid_amount`…). `to_cents` accepts
    both.

    OpenPix also uses `number`, but states **cents**. Same wrong type,
    different unit. Swapping one for the other charges R$ 1,990.00 for a
    R$ 19.90 item — and the error surfaces on the customer's statement.

    That is why `to_cents` **refuses** a fraction of a cent instead of
    rounding: rounding would hide the mismatch behind a plausible number.

## Checkout Pro: the preference

The buyer is redirected to a Mercado Pago screen:

```python
from tempest_fastapi_sdk.integrations.payment.mercado_pago import (
    MercadoPagoClient,
    PreferenceItem,
    PreferenceRequest,
)


async def create_preference_for(client: MercadoPagoClient) -> str | None:
    """Create a Checkout Pro preference and return where to send the buyer.

    Args:
        client (MercadoPagoClient): The configured client.

    Returns:
        str | None: The ``init_point`` URL, when the provider returned one.
    """
    preference = await client.create_preference(
        body=PreferenceRequest(
            items=[
                PreferenceItem(
                    title="Order 1042",
                    quantity=1,
                    unit_price=19.9,
                )
            ],
            external_reference="order-1042",
        )
    )
    return preference.init_point
```

## Checkout Transparente: Pix and card through the Orders API

Without redirecting the buyer, a charge goes through the **Orders API**
(`/v1/orders`). The Payments API (`/v1/payments`) shows on Mercado Pago's
dashboard with the warning *"Esta API será descontinuada em breve"* (this API
will be discontinued soon), and this SDK no longer models it — the
[migration guide](../migration.md) says what to change.

Both adapters speak Orders and hand out the canonical contracts of
`integrations.payment`: `MercadoPagoPixProvider` (the `PixProvider`) and
`MercadoPagoCardProvider` (the `CardProvider`). The script below runs against
the sandbox with the credentials from the
[test accounts and credentials](mercado-pago-sandbox.md) recipe:

```python
import asyncio
import os
from datetime import timedelta

from tempest_fastapi_sdk import HTTPClient
from tempest_fastapi_sdk.integrations.payment import (
    CardChargeRequest,
    PixChargeRequest,
    PixPayer,
)
from tempest_fastapi_sdk.integrations.payment.adapters import (
    MercadoPagoCardProvider,
    MercadoPagoPixProvider,
)
from tempest_fastapi_sdk.integrations.payment.mercado_pago import DEFAULT_BASE_URL


async def main() -> None:
    """Open a Pix, then charge, decline and refund a test card."""
    payer: PixPayer = PixPayer(email="buyer@example.com")
    async with HTTPClient(
        base_url=DEFAULT_BASE_URL,
        default_headers={
            "Authorization": f"Bearer {os.environ['MERCADO_PAGO_TEST_SELLER_ACCESS_TOKEN']}"
        },
    ) as http:
        pix = MercadoPagoPixProvider(http)
        charge = await pix.create_pix_charge(
            PixChargeRequest(
                amount_cents=1990,
                reference="pedido-1042",
                expires_in=timedelta(minutes=30),
                payer=payer,
            ),
        )
        print("pix", charge.status.value, bool(charge.br_code))
        print("pix", (await pix.cancel_pix_charge(charge.provider_charge_id)).status.value)

        card = MercadoPagoCardProvider(http)
        token = (
            await http.request(
                "POST",
                "/v1/card_tokens",
                json={
                    "card_number": "4235647728025682",
                    "expiration_month": 11,
                    "expiration_year": 2030,
                    "security_code": "123",
                    "cardholder": {
                        "name": "APRO",
                        "identification": {"type": "CPF", "number": "12345678909"},
                    },
                },
            )
        ).json()["id"]
        paid = await card.create_card_charge(
            CardChargeRequest(
                amount_cents=10000,
                reference="pedido-1043",
                card_token=token,
                payment_method_id="visa",
                payer=payer,
            ),
        )
        print("card", paid.status.value, paid.status_detail)
        refunded = await card.refund_card_charge(paid.provider_charge_id, amount_cents=3000)
        print("card", refunded.status.value, refunded.refunded_cents)


asyncio.run(main())
```

Output measured on 2026-10-09:

```text
pix pending True
pix cancelled
card paid accredited
card paid 3000
```

!!! warning "The card number does not go through your server"
    The script tokenizes the **test** Visa on the server only because it is
    not a card. In production, the frontend tokenizes with the **Public Key**
    (MercadoPago.js or the Card Payment Brick) and sends the backend the
    token, the brand and the installments. Receiving the number on the server
    puts the service in PCI DSS scope.

What the adapters decide for you, each item measured in the sandbox:

- **Money in cents in the contract, decimal strings on the wire.** Orders
  writes `"19.90"`; `from_cents` / `to_cents` convert without going through
  `float`.
- **A card decline is HTTP 402, and comes back as a result.** The body
  carries the reason in `errors` and the order in `data`.
  `create_card_charge` returns a `CardCharge` with status `FAILED` and the
  reason in `status_detail` (`rejected_by_issuer`) instead of raising.
- **Authorize now, capture later.** `capture=False` sends
  `capture_mode: manual`; the charge comes back `AUTHORIZED`
  (`waiting_capture`) and waits for `capture_card_charge` or
  `cancel_card_charge`.
- **A partial refund addresses the payment.** An order has one id (`ORD…`)
  and the payment inside it another (`PAY…`); `refund_card_charge` and
  `refund_pix_charge` find the second on their own. Without an amount, they
  refund what is left. On a card, `refunded_cents` sums the processed
  refunds; on a Pix, they are under `raw["transactions"]["refunds"]`.
- **A Pix is refunded only once paid.** Measured on 2026-10-10: a partial
  refund leaves the charge `PAID` (`partially_refunded`), the rest makes it
  `REFUNDED`. On an unpaid Pix the answer is `409 cannot_refund_order`: it
  is cancelled, not refunded.
- **Capture and refund read the order back.** Both answers carry only the
  id, state and transactions, no `total_amount`; the adapter issues a `GET`
  right after to return the whole charge.
- **"Not yet" is retried.** Right after creating, cancelling an
  authorization answered `409 processor_communication_error` in 3 of 10
  attempts, and refunding an approval answered `422 unprocessable_entity` in
  7 of 10 and `409 post_processing_operation_pending` in 1 of 10 — the
  asynchronous capture was still finishing. The adapter retries
  those answers, and only those, with the same key, after 1, 2 and 4 s
  (`action_retry_delays=` changes or disables it); every measured one went
  through within about five seconds.
- **Pix expiry in seconds.** `expires_in` becomes `PT1800S`; without it, the
  order expires in 24 hours. Past the deadline, the order reads
  `canceled` / `expired` and the charge becomes `EXPIRED`, not `CANCELLED`
  (measured with `PT60S`).
- **The payer is required.** An order without `payer` comes back
  `400 '$.payer' - minimum 1 properties allowed`.
- **One idempotency key per call**, which the `HTTPClient` reuses across its
  own retries. The provider honours the key (measured on 2026-10-10): the
  same key with the same body returns the **same** order, and with another
  body answers `409 idempotency_key_already_used`. To collapse two calls for
  the same order, pass `idempotency_key=lambda reference: reference`.
- **Installments depend on the account.** For the test seller, the Visa
  installment options offered only 1x, at R$ 100.00 and at R$ 1,000.00, and
  asking for 3x or 6x came back `400 invalid_transaction_amount`. Query the options
  (`get_installments`) and send one that was offered.

!!! note "The generated client, to go further"
    `MercadoPagoClient` carries the whole Orders API (`create_order`,
    `get_order`, `capture_order`, `refund_order`, `cancel_order`,
    transactions). The states of `Order` and `OrderTransactionPayment` accept
    any string: the sandbox returned `failed`, `refunded`, `waiting_transfer`
    and `rejected_by_issuer`, which the document does not list, and with the
    closed enum `create_order` raised `ValidationError` when creating a Pix.
    There a card decline is a `402` that `raise_for_status()` turns into an
    exception — it is the adapter that reads it as a result.

## Verifying the webhook

```python
from typing import Any

from fastapi import APIRouter, Depends

from tempest_fastapi_sdk.integrations.payment.mercado_pago import (
    MercadoPagoWebhookEvent,
    make_mercado_pago_webhook_dependency,
)

from src.core.settings import settings

router = APIRouter(prefix="/webhooks", tags=["webhooks"])
verified = make_mercado_pago_webhook_dependency(
    settings.MERCADOPAGO_WEBHOOK_SECRET,
    tolerance_seconds=300.0,
)


@router.post("/mercado-pago", include_in_schema=False)
async def mercado_pago_webhook(
    event: MercadoPagoWebhookEvent = Depends(verified),
) -> dict[str, Any]:
    """Receive an already-verified notification."""
    action = str(event.payload.get("action") or "")
    if event.topic == "order" or action.startswith("order."):
        return {"handled": True, "order": event.data_id}
    return {"handled": False, "topic": event.topic}
```

The factory reads `data.id` from the query string and `x-signature` /
`x-request-id` from the headers, runs `verify_signature`, and hands over a
`MercadoPagoWebhookEvent`. You extract nothing from the request by hand, and
you do not decide what to do with a `False`:

- Missing or invalid signature, drift past `tolerance_seconds`, or a
  `data.id` / `x-request-id` the signature covered and the request did not
  carry → **401** (`{"detail": "Invalid Mercado Pago webhook signature",
  "code": "UNAUTHORIZED", "details": {}}`), **before** your handler.
- An empty secret refuses everything — "no secret configured" does not
  become an open route.
- A topic this SDK does not name does **not** fail the route: `event`
  becomes `MercadoPagoEvent.UNKNOWN` and `topic` keeps the string. A body
  that is not JSON does not either: `payload` stays empty and `body` carries
  the bytes.

- An Orders notification should arrive with the `order` topic or an
  `order.*` action: the provider's document lists `order.created` and
  `order.updated`, but a live Orders delivery **has not been observed** here
  yet. `MercadoPagoEvent` only names the topics the spec declares
  (`payment`, `merchant_order`, `point_integration_wh`), so for Orders
  `event.event` is `UNKNOWN` — which is why the example decides on the
  `topic` and the action, as `make_mercado_pago_webhook_delivery_dependency`
  (next section) does, which also re-reads the order.

!!! warning "The signature does not cover the body"
    The signed manifest is `data.id`, `x-request-id` and `ts` — the body is
    left out. That is why `event.data_id` comes from the **query**, the
    value the provider signed, and not from the JSON's `data.id`. `payload`
    and `topic` arrived unsigned: re-read the resource from the API by
    `data_id` before acting on it.

The algorithm is **ported from Mercado Pago's own validator**
(`mercadopago/sdk-nodejs`, `src/utils/webhook/index.ts`, commit `99857f33`) —
the module their documentation points integrators at. The vendored
specification models none of it: `grep -c "x-signature"
vendor/mercadopago-openapi.yaml` returns `2`, and both hits are prose inside a
`description` — **no** declared parameter or header carries that name, and the
verification algorithm is not there.

The signed manifest **omits absent pairs**. It is not a fixed template:

```text
everything present   id:<data.id>;request-id:<x-request-id>;ts:<ts>;
no data.id           request-id:<x-request-id>;ts:<ts>;
neither one          ts:<ts>;
```

!!! warning "This was a defect until v0.250.0"
    Until then this module rendered a fixed template, so a delivery without
    `data.id` signed `id:;request-id:...;ts:...;` — and no such delivery ever
    verified. If you treated the rejection as "invalid notification", you were
    dropping legitimate ones.

`build_manifest` is exported so you can inspect what would be signed:

```python
from tempest_fastapi_sdk.integrations.payment.mercado_pago import build_manifest


def manifest_of_delivery(data_id: str, request_id: str, ts: str) -> str:
    """Show the exact string the signature covers.

    Args:
        data_id (str): The ``data.id`` query parameter, empty when absent.
        request_id (str): The ``x-request-id`` header, empty when absent.
        ts (str): The ``ts`` component of ``x-signature``.

    Returns:
        str: The manifest, with absent pairs left out.
    """
    return build_manifest(data_id=data_id, request_id=request_id, timestamp=ts)
```

!!! tip "Turn the tolerance window on"
    Without `tolerance_seconds`, a delivery captured off the wire verifies
    forever: the signature covers a timestamp nobody checks. Upstream leaves
    the window opt-in and so do we, but `300.0` is what makes the manifest's
    `ts` do any work. The unit of `ts` is read by magnitude — the provider's
    own artifacts disagree between seconds and milliseconds, and
    [their issue #458](https://github.com/mercadopago/sdk-nodejs/issues/458)
    was exactly that confusion.

!!! info "A `v2` migration needs no release"
    The header can carry more than one hash (`ts=..,v1=..,v2=..`). The verifier
    uses the first version you accept, so `versions=("v2", "v1")` adopts a new
    one before this package changes. The default is `("v1",)` — failing closed
    is the right behaviour for a version the provider has not sent yet.

!!! danger "Still not measured against a live delivery"
    Ported from the provider's implementation is not the same as verified
    against a notification the provider sent. What is measured: the manifests,
    byte for byte, against the rules upstream encodes; and the digests,
    against vectors computed with `openssl dgst -sha256 -hmac`, a different
    HMAC implementation than Python's.

    What is still unmeasured: whether the live deliveries follow their own
    SDK. Run **one** real notification through `verify_signature` before this
    guards money, and open an issue if it is rejected.

!!! warning "QR Code notifications are not signed"
    Upstream states it outright: those deliveries carry no signature and will
    always fail. Do not route QR Code through here — gate that path some other
    way.

## The webhook through the contract: re-reading the order

A Mercado Pago notification signs only `data.id` — the order id — and does
not say whether it was paid. A `parse_webhook` reading only the notification
would have one possible event, `UNKNOWN`, and a service that releases orders
on `CHARGE_PAID` would never release anything. That is why the dependency
verifies the signature **and** re-reads the order before handing it to your
handler:

```python
from typing import Any

from fastapi import Depends, FastAPI

from tempest_fastapi_sdk import HTTPClient
from tempest_fastapi_sdk.integrations.payment import (
    PixEventType,
    confirm_pix_payment,
)
from tempest_fastapi_sdk.integrations.payment.adapters import (
    MercadoPagoCardProvider,
    MercadoPagoOrderDelivery,
    MercadoPagoPixProvider,
    make_mercado_pago_webhook_delivery_dependency,
)
from tempest_fastapi_sdk.integrations.payment.mercado_pago import DEFAULT_BASE_URL

http: HTTPClient = HTTPClient(
    base_url=DEFAULT_BASE_URL,
    default_headers={"Authorization": "Bearer <the seller's access token>"},
)
pix: MercadoPagoPixProvider = MercadoPagoPixProvider(http)
delivery_dependency = make_mercado_pago_webhook_delivery_dependency(
    "<webhook secret>",
    pix,
    tolerance_seconds=300.0,
)

app: FastAPI = FastAPI()


@app.post("/webhooks/mercado-pago", include_in_schema=False)
async def webhook(
    delivery: MercadoPagoOrderDelivery = Depends(delivery_dependency),
) -> dict[str, Any]:
    """Release the order when the re-read order is paid."""
    card = MercadoPagoCardProvider.charge_from_delivery(delivery)
    if card is not None:
        return {"card": card.status.value, "detail": card.status_detail}
    event = pix.parse_webhook(delivery)
    if event.type is not PixEventType.CHARGE_PAID or event.charge is None:
        return {"settled": None}
    confirmation = await confirm_pix_payment(
        pix,
        event.charge.provider_charge_id,
        reference=event.charge.reference,
        amount_cents=event.charge.amount_cents,
    )
    return {"settled": confirmation.paid}
```

!!! danger "In your service, the id and the amount come from your database"
    The example confirms against the re-read order's own data, to fit on a
    page. In a service, `confirm_pix_payment` takes the `provider_charge_id`
    and the amount **you** stored when opening the charge — that is what
    stops another order's charge from releasing this one. The
    [Pix protocol](pix-protocol.md#step-4-the-service-which-speaks-only-the-contract)
    builds the full service.

What the dependency does, in this order:

1. **Verifies the signature** (`x-signature` over `data.id`, `x-request-id`
   and `ts`). A missing or wrong signature is `401` before the handler, with
   no request to Mercado Pago.
2. **Decides whether the notification is about an order**: topic `order`, or
   an action starting with `order.`. The provider's document lists
   `order.created` and `order.updated`; a live Orders delivery has not been
   observed here yet.
3. **Checks the id's shape** (`[A-Za-z0-9]+`) before putting it in a path.
4. **Re-reads the order** by that id. `404` is an answer, not a failure: the
   dashboard's "simulate notification" signs a made-up id, and raising there
   would make Mercado Pago resend forever. Other errors propagate, the route
   answers 5xx and Mercado Pago tries again.

Then `parse_webhook` takes the event type from the **re-read state**
(`processed` → `CHARGE_PAID`, `canceled` → `CHARGE_CANCELLED`, `refunded` →
`CHARGE_REFUNDED`, pending → `CHARGE_CREATED`). Passing the bare notification
raises `TypeError` with a hint about the dependency. States without a
canonical event (`failed`, chargeback) become `UNKNOWN`, and the state is on
`event.charge.status`.

To test all of this locally, with simulated, signed notifications, see
[Mercado Pago: testing webhooks](mercado-pago-webhooks.md).

## Telling a trustworthy operation from an unverified one

The document this SDK generates from comes from the provider: it is,
byte for byte, the `spec3.yaml` of
[`github.com/mercadopago/openapi`](https://github.com/mercadopago/openapi),
the company's own specification repository. `make mercadopago-fetch` refreshes
it.

But that document is **not complete**: measured 2026-08-30, it omits seven
operations Mercado Pago's own SDK calls, and three operations it does carry
answered `404` when probed. Refreshing answers *"did the document move?"*, not
*"does this operation exist?"*.

The client also **does not carry what the provider is retiring**: the 8
Payments API operations and the 7 in-store QR ones the spec itself marks
`deprecated: true`. The official SDK still calls 7 of them (the Payments
ones), and that is the only gap allowed in the rule "what the SDK calls, we
model".

So not every `MercadoPagoClient` operation rests on the same evidence. Of 132:

| Bucket | Count | What vouches for it |
| --- | --- | --- |
| The official SDK calls it | 58 | The provider, in its own `mercadopago` on PyPI (65 call sites in 3.5.0 and in 3.6.0, minus the 7 Payments API ones) |
| Probed live | 34 | An unauthenticated `GET` answered `401`/`403`/`400` (2026-08-28); 11 of them do not hold up and 2 answer as unrouted, see the note below |
| Told apart in the sandbox | 27 | A request that cannot succeed answered differently from a made-up path under the same prefix (2026-10-09) |
| Not routed | 2 | The sandbox answered the way it answers a path that does not exist |
| Nothing vouches | 11 | Same answer as the made-up path: no probe tells them apart |

!!! warning "The probed-live operations were re-evaluated"
    The "probed live" bucket rests on a rule the 2026-10-09 probe showed is
    weak: on several prefixes `401`/`403` comes before routing. Re-evaluated
    with `GET` against a made-up path under the same prefix, 11 of the 34
    answer the same as the made-up path (`/terminals/v1`, refunds under
    `/point/integration-api`, `/users/{id}/pos`, six subpaths of
    `/post-purchase/v1/claims/{id}` and `GET /v1/account/release_report/{id}`)
    and two answer as unrouted (`GET /v1/account/release_report` and
    `GET /v1/account/settlement_report`). They carry no marker in their
    docstring yet; the decision is in issue #488.

**The 11 say so in their own docstring:**

```
**Unverified.** Neither the provider's SDK nor an unauthenticated probe
covers this operation, so nothing here confirms the API routes it.
```

**So do the 2 unrouted ones**, with the measurement: `update_chargeback` and
`create_qr_integrator_config` carry `**Not routed.**` and what the sandbox
answered. They stay in the client, because removing a public method is a
separate decision, but do not expect them to work.

!!! warning "A status other than `404` does not prove a route"
    Measured in the sandbox on 2026-10-09: on several prefixes a policy gate
    answers **before** routing. `POST /terminals/v1/<anything>` answers `401`
    and `POST /post-purchase/v1/claims/<id>/<anything>` answers `403`, for
    paths that do not exist. That is why the "told apart in the sandbox"
    bucket only counts an operation whose answer differs from a made-up path
    under the same prefix, and the 11 that did not differ stay marked.

    The probe is also per **method and path**: `GET /v1/customers` answers
    `404` while `POST /v1/customers` is where the official SDK creates
    customers.

If you use one of the 11 and it works, that is evidence this repository does
not have. An issue with what you observed is welcome.

### `get_authenticated_user` returns a model

`GET /users/me` was observed in the sandbox, and the method answers
`AuthenticatedUser` instead of `dict[str, Any]`. Every declared field carried
a value in the observed response, and none is required. What is not declared
(reputation, `status`, fields that came back `null`) stays in `model_extra`,
nothing dropped:

```python
import asyncio

from tempest_fastapi_sdk import HTTPClient
from tempest_fastapi_sdk.integrations.payment.mercado_pago import (
    DEFAULT_BASE_URL,
    AuthenticatedUser,
    MercadoPagoClient,
)


async def main() -> None:
    """Show the account the token belongs to."""
    http: HTTPClient = HTTPClient(
        base_url=DEFAULT_BASE_URL,
        default_headers={"Authorization": "Bearer <your access token>"},
    )
    async with http:
        user: AuthenticatedUser = await MercadoPagoClient(http).get_authenticated_user()
    print(user.id, user.site_id, user.tags)


asyncio.run(main())
```

`search_chargebacks(payment_id=...)` returns `ChargebackSearchResponse`
(`paging` + `results`), also observed: measured on 2026-10-10 with the test
seller's token, the search **requires** `payment_id` — without it, even with
only `limit` and `offset`, the answer is `400 Wrong parameters in Search
Cases`. The items of `results` were never seen (every search came back
empty) and stay `dict[str, Any]`.

The five advanced payments operations stay `dict[str, Any]`: with the
`TEST-` token and with the test seller's, all of them answered `403` from the
PolicyAgent — and so did an invented path under the same prefix, so the
policy refuses before routing and there was no response to observe.

To see the buckets:

```bash
make mercadopago-diff
```


## Recap

- One host: what separates test from production is the token.
- Money in reais; convert at the boundary with `to_cents` / `from_cents`.
- Pix and card go through the Orders API; the Payments API left the SDK
  because the provider is discontinuing it.
- `MercadoPagoPixProvider` and `MercadoPagoCardProvider` hand out the
  canonical contracts: cents, canonical states, a card decline as a result
  (`402`), authorize and capture, partial card and Pix refunds, an expired
  Pix as `EXPIRED`.
- Cards require client-side tokenization, with the Public Key.
- Webhook verification is ported from the provider's validator, with the
  manifest omitting absent pairs and digests checked against `openssl`;
  only a live delivery is still missing. Turn `tolerance_seconds` on.
- `make_mercado_pago_webhook_dependency` builds the route: it reads
  `data.id`, `x-signature` and `x-request-id`, refuses with 401 before the
  handler, and hands over the signed `data_id` — the body is not signed.
- QR Code notifications are not signed — do not run them through
  `verify_signature`.
- Not every operation rests on the same evidence: 11 say `**Unverified.**`
  and 2 say `**Not routed.**` in their docstring. `get_authenticated_user`
  returns `AuthenticatedUser`, observed in the sandbox.
- The webhook goes through `make_mercado_pago_webhook_delivery_dependency`,
  which verifies the signature and re-reads the order before it becomes an
  event.
