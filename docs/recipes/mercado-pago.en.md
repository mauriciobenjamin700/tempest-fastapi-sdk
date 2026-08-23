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
    Mercado Pago types money as `number` and states it in **reais** — 39
    monetary properties in the specification, among them
    `transaction_amount`, `unit_price` and `Refund.amount`.

    OpenPix also uses `number`, but states **cents**. Same wrong type,
    different unit. Swapping one for the other charges R$ 1,990.00 for a
    R$ 19.90 item — and the error surfaces on the customer's statement.

    That is why `to_cents` **refuses** a fraction of a cent instead of
    rounding: rounding would hide the mismatch behind a plausible number.

## Checkout Pro: the preference

The buyer is redirected to a Mercado Pago screen:

```python
from tempest_fastapi_sdk.integrations.payment.mercado_pago import MercadoPagoClient


async def create_preference_for(client: MercadoPagoClient) -> str | None:
    """Create a Checkout Pro preference and return where to send the buyer.

    Args:
        client (MercadoPagoClient): The configured client.

    Returns:
        str | None: The ``init_point`` URL, when the provider returned one.
    """
    preference = await client.create_preference(
        body={
            "items": [
                {
                    "title": "Order 1042",
                    "quantity": 1,
                    "unit_price": 19.9,
                }
            ],
            "external_reference": "order-1042",
        }
    )
    return preference.init_point
```

## Transparent checkout: charging with no redirect

Pix and boleto are **entirely server-side** — no redirect at all:

```python
import uuid

from tempest_fastapi_sdk.integrations.payment.mercado_pago import MercadoPagoClient


async def charge_pix(client: MercadoPagoClient) -> str | None:
    """Charge over Pix without sending the buyer anywhere.

    Args:
        client (MercadoPagoClient): The configured client.

    Returns:
        str | None: The payment URL for the offline method, when present.
    """
    payment = await client.create_payment(
        body={
            "transaction_amount": 19.9,
            "payment_method_id": "pix",
            "payer": {"email": "buyer@example.com"},
            "external_reference": "order-1042",
        },
        x_idempotency_key=uuid.uuid4(),
    )
    details = payment.transaction_details
    return details.external_resource_url if details is not None else None
```

!!! tip "`x_idempotency_key` is a call argument"
    One key per attempt. If the network drops after Mercado Pago received
    the request, retrying **with the same key** returns the original payment
    instead of creating a second one.

    It is an argument — rather than a default header on `HTTPClient` —
    precisely because of that: a default header would send the same key on
    every charge, and the second sale would be deduplicated onto the
    first.

!!! warning "Cards have a mandatory client-side step"
    `create_payment` takes the card as a `token`, never as a number. That
    token is issued by `POST /v1/card_tokens`, which the specification
    declares with `security: publicKey` — a **public** key, meant to run in
    the browser or the app.

    Calling that route from your server is technically possible and puts
    your service in PCI DSS scope. Use Mercado Pago's JavaScript or mobile
    SDK to obtain the token, and send only the token to your backend.

## The Pix QR, and why it disappears

The generated `create_payment` returns the `Payment` the specification
declares — and the specification does **not** declare
`point_of_interaction`, which is exactly where the copy-and-paste code and
the QR image arrive. Since the SDK's `BaseSchema` is `extra="ignore"`, that
object is discarded during validation: the QR arrives in the HTTP body and
vanishes in the model, with no error and nothing in a log.

That is why `create_pix_payment` exists. It issues the **same** request and
returns a model with somewhere to keep the QR:

```python
import uuid

from tempest_fastapi_sdk import HTTPClient
from tempest_fastapi_sdk.integrations.payment.mercado_pago import (
    DEFAULT_BASE_URL,
    PixPayment,
    create_pix_payment,
)


async def charge_pix_with_qr(access_token: str) -> PixPayment:
    """Charge over Pix and keep the QR the generated model drops.

    Args:
        access_token (str): The Mercado Pago access token.

    Returns:
        PixPayment: The pending payment, carrying ``qr_code`` and
        ``qr_code_base64``.
    """
    http: HTTPClient = HTTPClient(
        base_url=DEFAULT_BASE_URL,
        default_headers={"Authorization": f"Bearer {access_token}"},
    )
    return await create_pix_payment(
        http,
        body={
            "transaction_amount": 19.9,
            "payment_method_id": "pix",
            "payer": {"email": "buyer@example.com"},
            "external_reference": "order-1042",
        },
        idempotency_key=uuid.uuid4(),
    )
```

What the returned `PixPayment` carries:

```text
payment.qr_code         "00020126580014br.gov.bcb.pix0136..."   the copy-and-paste code
payment.qr_code_base64  "iVBORw0KGgoAAAANSUhEUg..."             PNG, for <img src="data:...">
payment.ticket_url      "https://www.mercadopago.com.br/..."    page that already draws the QR
payment.status          "pending"                               until the payer pays
```

All three are **None-safe** properties: a card payment, or a Pix already
paid, returns `None` instead of raising — that is how the provider answers
after settlement.

!!! tip "Already holding the body? Use `parse_pix_payment`"
    A webhook tells you to fetch the payment; if you already called through
    the generated client and kept the raw JSON, `parse_pix_payment(payload)`
    builds the same `PixPayment` without repeating the request. To re-read it
    by id there is `get_pix_payment(http, payment_id)`.

!!! info "Where these field names come from"
    Not from the specification, which omits them: from Mercado Pago's own
    Node SDK (`mercadopago/sdk-nodejs`,
    `src/clients/payment/commonTypes.ts`, commit `c2d3c6ae`), where
    `PointOfInteraction` and `TransactionData` are modelled. The field set is
    pinned by a test, so a change upstream shows up here as a failure rather
    than as a value that went missing.

!!! note "`PixPayment` is a view, not a replacement"
    For everything the specification declares, use the generated `Payment`.
    `PixPayment` carries only what a Pix flow reads — id, status, amount,
    expiration — plus the QR object. It deliberately does not import the
    generated schemas: reading a QR should not pay the 0.76 s that building
    the 324 models costs.

### The alternative route: Orders API

The specification models the QR in one single place,
`OrderTransactionPayment`, from the Orders API — there `qr_code`,
`qr_code_base64`, `digitable_line` and `e2e_id` are genuinely declared:

```python
from tempest_fastapi_sdk.integrations.payment.mercado_pago import MercadoPagoClient


async def order_qr(client: MercadoPagoClient, order_id: str) -> object:
    """Read the Pix QR data of an order.

    Args:
        client (MercadoPagoClient): The configured client.
        order_id (str): The order identifier.

    Returns:
        object: The order, whose transactions carry ``qr_code`` and
        ``qr_code_base64``.
    """
    return await client.get_order(order_id)
```

Use Orders for a new integration — it is the provider's own recommendation,
and the typed path straight through the specification. Use
`create_pix_payment` when the charge already runs on `/v1/payments` and
switching APIs is not on the table.

## Verifying the webhook

```python
from tempest_fastapi_sdk.integrations.payment.mercado_pago import verify_signature


def notification_is_authentic(
    secret: str, signature: str, data_id: str, request_id: str
) -> bool:
    """Check that a notification really came from Mercado Pago.

    Args:
        secret (str): The webhook secret from the dashboard.
        signature (str): The ``x-signature`` header.
        data_id (str): The ``data.id`` query parameter.
        request_id (str): The ``x-request-id`` header.

    Returns:
        bool: Whether the signature matches.
    """
    return verify_signature(
        secret=secret,
        signature_header=signature,
        data_id=data_id,
        request_id=request_id,
        tolerance_seconds=300.0,
    )
```

The algorithm is **ported from Mercado Pago's own validator**
(`mercadopago/sdk-nodejs`, `src/utils/webhook/index.ts`, commit `99857f33`) —
the module their documentation points integrators at. The vendored
specification describes none of it: `grep -c "x-signature"
vendor/mercadopago-openapi.yaml` returns `0`.

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

## Recap

- One host: what separates test from production is the token.
- Money in reais; convert at the boundary with `to_cents` / `from_cents`.
- Pix and boleto are server-side; cards require client-side tokenization.
- `x_idempotency_key` is a per-call argument, never a default header.
- The generated `Payment` drops the Pix QR silently; use
  `create_pix_payment` / `parse_pix_payment`, or the Orders API.
- Webhook verification is ported from the provider's validator, with the
  manifest omitting absent pairs and digests checked against `openssl`;
  only a live delivery is still missing. Turn `tolerance_seconds` on.
- QR Code notifications are not signed — do not run them through
  `verify_signature`.
