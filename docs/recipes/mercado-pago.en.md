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

## The Pix QR is not where you would expect

To show the copy-and-paste code and the QR image, use the **Orders API**:

```python
from tempest_fastapi_sdk.integrations.payment.mercado_pago import MercadoPagoClient


async def pix_qr(client: MercadoPagoClient, order_id: str) -> object:
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

!!! info "Why Orders and not `/v1/payments`"
    Measured on the specification: `qr_code`, `qr_code_base64`,
    `digitable_line` and `e2e_id` appear in **one single schema**,
    `OrderTransactionPayment`. The `Payment` schema does not declare
    `point_of_interaction` — only `transaction_details`, with
    `external_resource_url`.

    Because the SDK's `BaseSchema` is `extra="ignore"`, a Pix created
    through `/v1/payments` would have the `point_of_interaction` the API
    does return **dropped during validation**, with no error: the QR arrives
    in the body and vanishes in the model.

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
- The Pix QR lives in the Orders API, not in `/v1/payments`.
- Webhook verification is implemented and tested as an HMAC, but the
  manifest is still waiting on a real notification to be confirmed.
