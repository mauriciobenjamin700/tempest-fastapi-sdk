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
    )
```

!!! danger "This part has not been measured against the provider yet"
    The vendored specification **does not describe** the webhook signature:
    there is no `webhooks` section and no notification security scheme, and
    `grep -c "x-signature" vendor/mercadopago-openapi.yaml` returns `0`.

    What is tested is the HMAC: signing and verifying agree, and tampering
    with the `data_id`, the timestamp or the secret breaks verification.
    What is **not** tested is whether the manifest we sign is byte-for-byte
    the one Mercado Pago signs.

    Before this guards money in production, run **one** real notification
    through `verify_signature` and confirm it is accepted. If it is
    rejected, the manifest is what needs to change — pass yours through
    `manifest_template=`, and open an issue so the default can be fixed.

## Recap

- One host: what separates test from production is the token.
- Money in reais; convert at the boundary with `to_cents` / `from_cents`.
- Pix and boleto are server-side; cards require client-side tokenization.
- `x_idempotency_key` is a per-call argument, never a default header.
- The generated `Payment` drops the Pix QR silently; use
  `create_pix_payment` / `parse_pix_payment`, or the Orders API.
- Webhook verification is implemented and tested as an HMAC, but the
  manifest is still waiting on a real notification to be confirmed.
