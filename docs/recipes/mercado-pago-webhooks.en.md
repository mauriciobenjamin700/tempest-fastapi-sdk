# Mercado Pago: testing webhooks

Webhooks are the hardest part of a payment integration to test: Mercado Pago
calls you, not the other way around, and it needs a public URL to call. This
page shows four levels of testing, from what runs with no network at all to
the real delivery:

1. **Simulated by the SDK, no network** — the Orders API and the
   notification are simulated; the real adapter creates, re-reads and
   refunds over them. No credential, no sandbox. Start here — in the test
   suite **and** for manual testing.
2. **Signed simulation over the sandbox** — you sign a notification with your
   secret and deliver it to your route. The route re-reads a **real** sandbox
   order. No public URL needed.
3. **The dashboard's "simulate notification"** — Mercado Pago sends a test
   notification to your URL.
4. **Real delivery** — you create an order and Mercado Pago notifies on its
   own.

First, have the credentials from the
[test accounts and credentials](mercado-pago-sandbox.md) recipe at hand, and
know what the route does from
[Mercado Pago »](mercado-pago.md#the-webhook-through-the-contract-re-reading-the-order):
it verifies the signature and **re-reads the order**, because the
notification does not carry the payment's state.

## Level 1 — simulated by the SDK, no network

The SDK ships Mercado Pago simulated in `tempest_fastapi_sdk.testing.fakes`:

- `FakeMercadoPagoOrdersAPI` answers `/v1/orders` with state — create
  (idempotent on `X-Idempotency-Key`), read, cancel, capture and refund. It is
  an `httpx` handler, so the **real** `MercadoPagoPixProvider` runs over it
  unchanged: `MercadoPagoPixProvider(api.http_client())`.
- The moves only the provider makes are methods — `pay`, `expire`, `fail`,
  `cancel`, `capture`, `refund` — and each returns the **signed
  notification** announcing the new state, ready for
  `delivery.send(client, route)`.
- `build_mercado_pago_delivery(order, secret=...)` builds the notification on
  its own, in the shape of the real delivery observed in production, signed by
  the same `sign_manifest` the SDK's verifier uses.

The script creates charges through the adapter, moves each one and delivers
the notification to the route mounted the way your service mounts it:

```python
import asyncio
from typing import Any

import httpx
from fastapi import Depends, FastAPI

from tempest_fastapi_sdk.integrations.payment import PixChargeRequest, PixPayer
from tempest_fastapi_sdk.integrations.payment.adapters import (
    MercadoPagoOrderDelivery,
    MercadoPagoPixProvider,
    make_mercado_pago_webhook_delivery_dependency,
)
from tempest_fastapi_sdk.testing.fakes import (
    FakeMercadoPagoOrdersAPI,
    MercadoPagoDelivery,
)

SECRET: str = "local-test-secret"


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


async def deliver(app: FastAPI, delivery: MercadoPagoDelivery) -> tuple[int, Any]:
    """Send one signed delivery to the route, in-process."""
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://local"
    ) as client:
        answer = await delivery.send(client, "/webhooks/mercado-pago")
    return answer.status_code, answer.json()


async def main() -> None:
    """Run payment, refund, cancellation and expiry with no network."""
    api = FakeMercadoPagoOrdersAPI(webhook_secret=SECRET)
    provider = MercadoPagoPixProvider(api.http_client(), action_retry_delays=())
    app = build_app(provider)
    payer = PixPayer(email="buyer@example.com")

    async def new_charge(reference: str) -> str:
        """Create a R$ 19,90 Pix charge through the real adapter."""
        charge = await provider.create_pix_charge(
            PixChargeRequest(amount_cents=1990, reference=reference, payer=payer)
        )
        return charge.provider_charge_id

    paid = await new_charge("order-1")
    print("created   ", await deliver(app, api.delivery(paid)))
    print("paid      ", await deliver(app, api.pay(paid)))
    await provider.refund_pix_charge(paid, 500, refund_reference="refund-1")
    print("partial   ", await deliver(app, api.delivery(paid)))
    await provider.refund_pix_charge(paid, refund_reference="refund-2")
    print("refunded  ", await deliver(app, api.delivery(paid)))

    cancelled = await new_charge("order-2")
    await provider.cancel_pix_charge(cancelled)
    print("cancelled ", await deliver(app, api.delivery(cancelled)))
    print("expired   ", await deliver(app, api.expire(await new_charge("order-3"))))

    stranger = FakeMercadoPagoOrdersAPI(webhook_secret="another-secret")
    await MercadoPagoPixProvider(stranger.http_client()).create_pix_charge(
        PixChargeRequest(amount_cents=1990, reference="x", payer=payer)
    )
    print("forged    ", await deliver(app, stranger.delivery(stranger.order_ids[0])))
    other = FakeMercadoPagoOrdersAPI(webhook_secret=SECRET)
    await MercadoPagoPixProvider(other.http_client()).create_pix_charge(
        PixChargeRequest(amount_cents=1990, reference="y", payer=payer)
    )
    print("simulated ", await deliver(app, other.delivery(other.order_ids[0])))


asyncio.run(main())
```

No environment variable, no network:

```bash
python simulate_offline.py
```

Output measured on 2026-11-14:

```text
created    (200, {'type': 'charge_created', 'status': 'pending'})
paid       (200, {'type': 'charge_paid', 'status': 'paid'})
partial    (200, {'type': 'charge_paid', 'status': 'paid'})
refunded   (200, {'type': 'charge_refunded', 'status': 'refunded'})
cancelled  (200, {'type': 'charge_cancelled', 'status': 'cancelled'})
expired    (200, {'type': 'charge_expired', 'status': 'expired'})
forged     (401, {'detail': 'Invalid Mercado Pago webhook signature'})
simulated  (200, {'type': 'unknown', 'status': None})
```

| Line | What happened | Why it matters |
| --- | --- | --- |
| `created` | a fresh order, re-read as `action_required` → `charge_created` | the event type comes from the **re-read**, not from the body |
| `paid` | `api.pay` → re-read `processed` → `charge_paid` | the path that releases the order; now it runs in the suite |
| `partial` | R$ 5.00 refunded → still `processed` → `charge_paid` | a partial refund is not a full refund |
| `refunded` | the rest refunded → `refunded` → `charge_refunded` | the adapter sums the refunds from the re-read |
| `cancelled` | cancelled through the adapter → `charge_cancelled` | |
| `expired` | `api.expire` → `canceled` / `expired` → `charge_expired` | expiring is not cancelling: two events in the contract |
| `forged` | another secret → `401`, no call to the API | without the secret, nobody reaches the re-read |
| `simulated` | an id the API does not know → `404` on the re-read → `200` with no charge | the case of an id that is not yours, like the one the dashboard signs (level 3) |

!!! tip "In your test suite"
    The same code becomes a test: swap the `print`s for `assert`s. The SDK's
    suite does exactly that in
    `tests/integrations/payment/adapters/test_mercado_pago_simulated_flow.py`
    — Pix paid, refunded in two steps, cancelled, expired, and card approved,
    authorized then captured, and declined (`api.decline_next_card()`).

!!! warning "What the simulation copies, and what it assumes"
    The state of each transition, the status codes (`201`, `200`, `402`,
    `404`), the idempotency rule and `409 cannot_refund_order` were copied
    from the sandbox. The notification's shape was copied from **one** real
    delivery, `order.canceled`. The other actions (`order.processed`,
    `order.refunded`, `order.failed`, `order.action_required`) **were not
    observed** and are not in the document the SDK vendors either, which only
    lists `order.created` / `order.updated`: the simulator names them after
    the order's status. The SDK's route does not decide on the action — it
    re-reads the order — so this does not change the outcome; it only matters
    if **your** code reads `action`.

### Manual testing: the service running, Mercado Pago simulated

The same simulator runs as a server, and the service points at it through
`MERCADOPAGO_BASE_URL` (a `MercadoPagoSettings` field, default
`https://api.mercadopago.com`). A minimal service, with a route that creates
a charge and the webhook route:

```python
from typing import Any

import uvicorn
from fastapi import Depends, FastAPI

from tempest_fastapi_sdk import HTTPClient
from tempest_fastapi_sdk.integrations.payment import PixChargeRequest, PixPayer
from tempest_fastapi_sdk.integrations.payment.adapters import (
    MercadoPagoOrderDelivery,
    MercadoPagoPixProvider,
    make_mercado_pago_webhook_delivery_dependency,
)
from tempest_fastapi_sdk.settings import MercadoPagoSettings

settings = MercadoPagoSettings()
provider = MercadoPagoPixProvider(HTTPClient(**settings.mercado_pago_kwargs()))
app = FastAPI()
webhook_dependency = make_mercado_pago_webhook_delivery_dependency(
    settings.MERCADOPAGO_WEBHOOK_SECRET, provider
)


@app.post("/charges")
async def create_charge(reference: str) -> dict[str, Any]:
    """Create a R$ 19,90 Pix charge."""
    charge = await provider.create_pix_charge(
        PixChargeRequest(
            amount_cents=1990,
            reference=reference,
            payer=PixPayer(email="comprador@example.com"),
        )
    )
    return {"order_id": charge.provider_charge_id, "status": charge.status.value}


@app.post("/webhooks/mercado-pago")
async def webhook(
    delivery: MercadoPagoOrderDelivery = Depends(webhook_dependency),
) -> dict[str, Any]:
    """Report what the re-read decided."""
    event = provider.parse_webhook(delivery)
    return {
        "type": event.type.value,
        "status": event.charge.status.value if event.charge else None,
    }


if __name__ == "__main__":
    uvicorn.run(app, host="127.0.0.1", port=8001)
```

In three terminals (port `8001` here only because `8000` was taken when this
was measured; `--url` defaults to
`http://127.0.0.1:8000/webhooks/mercado-pago`):

```bash
tempest mercado-pago fake-api            # http://127.0.0.1:8090

export MERCADOPAGO_BASE_URL=http://127.0.0.1:8090
export MERCADOPAGO_WEBHOOK_SECRET=segredo-local-de-teste
export MERCADOPAGO_ACCESS_TOKEN=anything
python servico.py                        # http://127.0.0.1:8001

export MERCADOPAGO_WEBHOOK_SECRET=segredo-local-de-teste
```

then create the charge through the service and move the order with
`--transition` (`pay`, `expire`, `fail`, `cancel`, `capture`, `refund` with
an optional `--amount-cents`). The command applies the transition on
`fake-api`, signs the notification and delivers it to the route. Output
measured on 2026-11-14:

```text
$ curl -s -X POST "http://127.0.0.1:8001/charges?reference=pedido-42"
{"order_id":"ORDFAKE5EBD2F1800000000000002","status":"pending"}

$ tempest mercado-pago simulate-webhook --url http://127.0.0.1:8001/webhooks/mercado-pago \
    --fake-api http://127.0.0.1:8090 --order-id ORDFAKE5EBD2F1800000000000002 --transition pay
POST http://127.0.0.1:8001/webhooks/mercado-pago?data.external_reference=pedido-42&data.id=ORDFAKE5EBD2F1800000000000002&type=order
action: order.processed
HTTP 200
{"type":"charge_paid","status":"paid"}

$ tempest mercado-pago simulate-webhook --url http://127.0.0.1:8001/webhooks/mercado-pago \
    --fake-api http://127.0.0.1:8090 --order-id ORDFAKE5EBD2F1800000000000002 --transition refund --amount-cents 500
POST http://127.0.0.1:8001/webhooks/mercado-pago?data.external_reference=pedido-42&data.id=ORDFAKE5EBD2F1800000000000002&type=order
action: order.processed
HTTP 200
{"type":"charge_paid","status":"paid"}

$ tempest mercado-pago simulate-webhook --url http://127.0.0.1:8001/webhooks/mercado-pago \
    --fake-api http://127.0.0.1:8090 --order-id ORDFAKE5EBD2F1800000000000002 --transition refund
POST http://127.0.0.1:8001/webhooks/mercado-pago?data.external_reference=pedido-42&data.id=ORDFAKE5EBD2F1800000000000002&type=order
action: order.refunded
HTTP 200
{"type":"charge_refunded","status":"refunded"}
```

With another secret, the route refuses and the command exits with code `1`:

```text
$ MERCADOPAGO_WEBHOOK_SECRET=wrong tempest mercado-pago simulate-webhook --url http://127.0.0.1:8001/webhooks/mercado-pago \
    --fake-api http://127.0.0.1:8090 --order-id ORDFAKE5EBD2F1800000000000002
POST http://127.0.0.1:8001/webhooks/mercado-pago?data.external_reference=pedido-42&data.id=ORDFAKE5EBD2F1800000000000002&type=order
action: order.refunded
HTTP 401
{"detail":"Invalid Mercado Pago webhook signature"}
```

!!! danger "The secret never goes on the command line"
    `simulate-webhook` has no `--secret` option: the value would land in the
    shell history and in `ps`. It reads from a variable (`--secret-env`,
    default `MERCADOPAGO_WEBHOOK_SECRET`) or a file (`--secret-file`), and
    never prints the value — without it, it exits with code `2` naming
    **which** variable was missing.

!!! warning "`MERCADOPAGO_BASE_URL` off the default is a production violation"
    The access token goes to the host this field names. In a `Settings`
    composing `EnvironmentSettings`, with `ENV=production`, any value other
    than `https://api.mercadopago.com` enters `production_violations()` and
    building the `Settings` raises `ValidationError`
    (`MERCADOPAGO_BASE_URL: not the Mercado Pago API host`).

Without `--fake-api`, the command signs just the id (`--order-id`) or the
order from a file (`--order-json`). Against a service that re-reads the order
from the real Mercado Pago, a made-up id gets `404` on the re-read — the
`simulated` case above.

## Level 2 — signed simulation over the sandbox

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
    async with HTTPClient(
        base_url=DEFAULT_BASE_URL,
        default_headers={"Authorization": f"Bearer {token}"},
    ) as http:
        provider = MercadoPagoPixProvider(http)
        charge = await provider.create_pix_charge(
            PixChargeRequest(
                amount_cents=1990, reference="webhook-test-1", payer=PixPayer(email="buyer@example.com")
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
    For the suite, swap the sandbox for level 1: `FakeMercadoPagoOrdersAPI`
    in place of the real `HTTPClient` and `api.pay(...)` in place of waiting
    for the payment — the same path, with no network and no credential.

## Level 3 — the dashboard's "simulate notification"

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
simulation carries an id that is none of your orders, and levels 1 and 2
showed what happens then.

!!! warning "The dashboard was not validated here"
    This level's steps describe the dashboard as it usually is; this
    repository has not yet observed what **Simulate notification** sends. A
    real delivery was observed in production on 2026-11-13 (level 4). If a
    delivery's signature is refused, open an issue with the headers (without
    the secret).

## Level 4 — real delivery

With the tunnel and the webhook configured, create an order (the script in
[test accounts and credentials](mercado-pago-sandbox.md#step-6-check-it-worked)
works) and watch your service's log. On every change to the order, Mercado
Pago calls the route with `data.id` equal to the order id.

!!! check "Observed on 2026-11-13"
    A real production delivery arrived as
    `POST …?data.id=ORD01…&type=order`, with `"action": "order.canceled"` in
    the body, and only verified with the `data.id` lowercased in the
    manifest, which is what the SDK does since v0.358.0. Not yet observed:
    the `order.updated` / `order.processed` actions of a paid order.

## Recap

- A notification only says **which** order changed; the route re-reads to
  learn **what**.
- `FakeMercadoPagoOrdersAPI` plus the real adapter test payment, refund,
  cancellation and expiry with no network; `tempest mercado-pago fake-api`
  and `simulate-webhook` do the same with the service running.
- `sign_manifest` produces a notification your route accepts: test the whole
  path without a public URL.
- A wrong signature is `401` before any call; an unknown id is `200` with no
  charge, so Mercado Pago does not resend forever.
- For the dashboard and a real delivery, a tunnel (`cloudflared`, `ngrok`)
  gives the public URL.
