# Mercado Pago: testing webhooks

Webhooks are the hardest part of a payment integration to test: Mercado Pago
calls you, not the other way around, and it needs a public URL to call. This
page shows three levels of testing, from what runs on your machine with
nothing exposed to the real delivery:

1. **Signed simulation, local** — you sign a notification with your secret
   and deliver it to your route. The route re-reads a **real** sandbox order.
   No public URL needed. Start here.
2. **The dashboard's "simulate notification"** — Mercado Pago sends a test
   notification to your URL.
3. **Real delivery** — you create an order and Mercado Pago notifies on its
   own.

First, have the credentials from the
[test accounts and credentials](mercado-pago-sandbox.md) recipe at hand, and
know what the route does from
[Mercado Pago »](mercado-pago.md#the-webhook-through-the-contract-re-reading-the-order):
it verifies the signature and **re-reads the order**, because the
notification does not carry the payment's state.

## Level 1 — signed simulation, on your machine

Mercado Pago's signature is an HMAC-SHA256 over `data.id`, `x-request-id`
and `ts`, keyed with the webhook secret. The SDK exposes the same computation
as `sign_manifest`, so you can produce a notification your route accepts —
and test the whole path without exposing anything.

The script creates a real Pix in the sandbox, mounts the route the way your
service does, and delivers four notifications:

```python
import asyncio
import os
import time
import uuid
from typing import Any

from fastapi import Depends, FastAPI
import httpx

from tempest_fastapi_sdk import HTTPClient
from tempest_fastapi_sdk.integrations.payment import PixChargeRequest, PixPayer
from tempest_fastapi_sdk.integrations.payment.adapters import (
    MercadoPagoOrderDelivery,
    MercadoPagoPixProvider,
    make_mercado_pago_webhook_delivery_dependency,
)
from tempest_fastapi_sdk.integrations.payment.mercado_pago import (
    DEFAULT_BASE_URL,
    sign_manifest,
)

SECRET: str = os.environ.get("MERCADO_PAGO_WEBHOOK_SECRET", "local-test-secret")


def build_app(provider: MercadoPagoPixProvider) -> FastAPI:
    """Mount the webhook route exactly as the service would."""
    app = FastAPI()
    dependency = make_mercado_pago_webhook_delivery_dependency(SECRET, provider)

    @app.post("/webhooks/mercado-pago")
    async def webhook(
        delivery: MercadoPagoOrderDelivery = Depends(dependency),
    ) -> dict[str, Any]:
        """Report what the re-read decided."""
        event = provider.parse_webhook(delivery)
        return {
            "type": event.type.value,
            "status": event.charge.status.value if event.charge else None,
        }

    return app


def signed_headers(order_id: str) -> dict[str, str]:
    """Sign a notification for this order the way Mercado Pago does."""
    request_id = str(uuid.uuid4())
    ts = str(int(time.time()))
    digest = sign_manifest(
        secret=SECRET, data_id=order_id, request_id=request_id, timestamp=ts
    )
    return {"x-signature": f"ts={ts},v1={digest}", "x-request-id": request_id}


async def notify(app: FastAPI, order_id: str, headers: dict[str, str]) -> tuple[int, Any]:
    """Deliver one notification to the local route, in-process."""
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://local"
    ) as client:
        answer = await client.post(
            f"/webhooks/mercado-pago?data.id={order_id}&type=order",
            headers=headers,
            json={"action": "order.updated", "type": "order", "data": {"id": order_id}},
        )
    return answer.status_code, answer.json()


async def main() -> None:
    """Create a real sandbox order, then notify the local route about it."""
    token: str = os.environ["MERCADO_PAGO_TEST_SELLER_ACCESS_TOKEN"]
    buyer: str = os.environ["MERCADO_PAGO_TEST_BUYER_EMAIL"]
    async with HTTPClient(
        base_url=DEFAULT_BASE_URL,
        default_headers={"Authorization": f"Bearer {token}"},
    ) as http:
        provider = MercadoPagoPixProvider(http)
        charge = await provider.create_pix_charge(
            PixChargeRequest(
                amount_cents=1990, reference="webhook-test-1", payer=PixPayer(email=buyer)
            )
        )
        app = build_app(provider)
        order_id = charge.provider_charge_id

        print("pending   ", await notify(app, order_id, signed_headers(order_id)))
        await provider.cancel_pix_charge(order_id)
        print("cancelled ", await notify(app, order_id, signed_headers(order_id)))
        print("forged    ", await notify(app, order_id, {"x-signature": "ts=1,v1=00", "x-request-id": "x"}))
        print("simulated ", await notify(app, "ORD00000000000000000000000000", signed_headers("ORD00000000000000000000000000")))


asyncio.run(main())
```

Run it with the variables from the credentials recipe.
`MERCADO_PAGO_WEBHOOK_SECRET` is optional here: the script signs and verifies
with the same value.

```bash
set -a; . ~/.config/my-service/mercadopago-sandbox.env; set +a
python simulate_webhook.py
```

Output measured on 2026-10-09 (printed labels translated):

```text
pending    (200, {'type': 'charge_created', 'status': 'pending'})
cancelled  (200, {'type': 'charge_cancelled', 'status': 'cancelled'})
forged     (401, {'detail': 'Invalid Mercado Pago webhook signature'})
simulated  (200, {'type': 'unknown', 'status': None})
```

Each line is a case the route must get right:

| Line | What happened | Why it matters |
| --- | --- | --- |
| `pending` | valid signature, order re-read as `action_required` → `charge_created` | the event type comes from the **re-read**, not from the body |
| `cancelled` | the same notification after cancelling → `charge_cancelled` | same body, another re-read state, another event |
| `forged` | wrong signature → `401`, with no call to Mercado Pago | without the secret, nobody reaches the re-read |
| `simulated` | an order id that does not exist → Mercado Pago answers `404` on the re-read, the route answers `200` with no charge | that is what the dashboard's simulation sends; answering an error would make Mercado Pago resend forever |

!!! tip "Take this into your test suite"
    The same pattern — `sign_manifest` + `httpx.ASGITransport` — becomes an
    automated test. Swap the real `HTTPClient` for one with
    `httpx.MockTransport` and you test the route without a network, as the
    SDK's own suite does in
    `tests/integrations/payment/adapters/test_mercado_pago_adapter.py`.

## Level 2 — the dashboard's "simulate notification"

For Mercado Pago to call your machine, it needs a public URL. A tunnel does
it: `cloudflared tunnel --url http://127.0.0.1:8000` or `ngrok http 8000`
prints an `https://…` URL pointing at your local server.

Logged in as the **test seller**, in its application:

1. Open the application's **Webhooks** settings and paste the tunnel URL
   followed by the route (`https://…/webhooks/mercado-pago`).
2. Tick the **Order** event.
3. Copy the **secret signature** the dashboard generates and use it as
   `MERCADO_PAGO_WEBHOOK_SECRET` in your service.
4. Use **Simulate notification**.

The expected result is your route answering `200` with no charge: the
simulation carries an id that is none of your orders, and level 1 showed
what happens then.

!!! warning "Not validated here"
    This level's steps describe the dashboard as it usually is; this
    repository has not yet observed a notification from the dashboard or a
    real delivery. The level 1 table is measured; levels 2 and 3 are what is
    expected, to be confirmed on your first delivery. If a real delivery's
    signature is refused, open an issue with the headers (without the
    secret).

## Level 3 — real delivery

With the tunnel and the webhook configured, create an order (the script in
[test accounts and credentials](mercado-pago-sandbox.md#step-7-check-it-worked)
works) and watch your service's log. On every change to the order, Mercado
Pago should call the route with `data.id` equal to the order id.

## Recap

- A notification only says **which** order changed; the route re-reads to
  learn **what**.
- `sign_manifest` produces a notification your route accepts: test the whole
  path without a public URL.
- A wrong signature is `401` before any call; an unknown id is `200` with no
  charge, so Mercado Pago does not resend forever.
- For the dashboard and a real delivery, a tunnel (`cloudflared`, `ngrok`)
  gives the public URL.
