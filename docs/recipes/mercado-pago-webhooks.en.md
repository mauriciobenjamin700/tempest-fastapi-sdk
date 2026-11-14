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
| `simulated` | an id the API does not know → `404` on the re-read → `200` with no charge | probably what the dashboard sends at level 3 (not yet observed) |

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

    Assumed, not copied: `409 cannot_cancel_order` (and its message *"The
    order cannot be canceled in status …"*), the code and message of the
    `404` for an unknown order (`order_not_found`, *"Order … not found"* —
    only the `404` status was measured) and the `400 invalid_refund_amount`
    for a refund above the balance, with its message. If your code reads
    `code` or `message` from these answers, check them in the sandbox.

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

### The ready-made route: `make_mercado_pago_router`

The webhook route above is the same in every service: verify, re-read, hand
over to your code, answer `200`. `make_mercado_pago_router` mounts that route
on top of the `make_mercado_pago_webhook_delivery_dependency` dependency — the
verification and the re-read are the same — and you write only the handler:

`app.include_router(make_mercado_pago_router(provider, on_delivery=handle, settings=settings, simulate=settings.ENV == "development"))` — the complete service is in the *Front end in development* example, right below.

- `on_delivery(delivery: MercadoPagoOrderDelivery) -> Awaitable[None]`
  receives the verified delivery with the order re-read. It returns: the
  route answers `200` with `{"ok": true, "data_id": ..., "order_status": ...}`.
  It raises: the answer is `500` — and, per the provider's documentation,
  Mercado Pago delivers again (redelivery expected, not observed here).
- The secret comes from `secret=`; without it, from
  `settings.MERCADOPAGO_WEBHOOK_SECRET` (or from the environment and `.env`
  when `settings` is missing too). An empty secret is refused at construction
  (`ValueError`): it would reject every delivery.
- `webhook_path` defaults to `/webhooks/mercado-pago` — the URL you register
  in the dashboard.
- A service that already writes the route by hand with the dependency keeps
  working: nothing changed in it.

#### Front end in development: `simulate=True`

With `simulate=True`, the router also mounts the routes a front end uses to
do what only Mercado Pago does — pay, expire, fail, cancel, capture, refund:

| Route | What it does |
| --- | --- |
| `GET /dev/mercado-pago/orders` | every order of the simulator |
| `GET /dev/mercado-pago/orders/{order_id}` | one order; `404` when unknown |
| `POST /dev/mercado-pago/orders/{order_id}/{transition}` | `pay`, `expire`, `fail`, `cancel`, `capture`, `refund` (`?amount_cents=` for a partial refund) |

Each transition moves the order in the **in-memory**
`FakeMercadoPagoOrdersAPI` your provider runs over, signs the notification
with the route's secret and delivers it to **the service's own webhook
route**, through the whole app (`httpx.ASGITransport(request.app)`):
middleware, signature check, re-read and `on_delivery` all run for real. The
answer carries the order after the transition and what the webhook route
answered. A transition the state does not allow (refund before payment) is
`409`; an unknown order, `404`; an unknown transition, `422`. In Swagger these
routes show under the `mercado-pago (dev)` tag.

`{order_id}` also takes the **`external_reference`** you sent on create — the
`reference` of `PixChargeRequest`, usually your `correlation_id`, which is
what your screens and logs show. When several orders share it, the most
recent one is moved: charging the same reference again after an expiry means
the new order.

The provider is tied to the simulator by `mercado_pago_provider_for`: with
`simulate=False` it returns the `MercadoPagoPixProvider` over
`settings.mercado_pago_kwargs()`; with `simulate=True`, the **same** adapter
over an in-memory `FakeMercadoPagoOrdersAPI`, and the router finds the
simulator on its own (`simulation_api_for(provider)`). For cards,
`provider_class=MercadoPagoCardProvider`; for another simulator,
`simulation_api=`.

```python
from typing import Any

import uvicorn
from fastapi import FastAPI

from tempest_fastapi_sdk.integrations.payment import PixChargeRequest, PixPayer
from tempest_fastapi_sdk.integrations.payment.adapters import (
    MercadoPagoOrderDelivery,
    make_mercado_pago_router,
    mercado_pago_provider_for,
)
from tempest_fastapi_sdk.settings import EnvironmentSettings, MercadoPagoSettings


class Settings(EnvironmentSettings, MercadoPagoSettings):
    """ENV plus the Mercado Pago credentials."""


settings = Settings()
simulate: bool = settings.ENV == "development"
provider = mercado_pago_provider_for(settings, simulate=simulate)


async def handle(delivery: MercadoPagoOrderDelivery) -> None:
    """Release or revert the order the delivery is about."""
    event = provider.parse_webhook(delivery)
    status = event.charge.status.value if event.charge else None
    print(f"on_delivery: {event.type.value} {status}", flush=True)


app = FastAPI()
app.include_router(
    make_mercado_pago_router(
        provider,
        on_delivery=handle,
        settings=settings,
        simulate=simulate,
    )
)


@app.post("/charges")
async def create_charge(reference: str) -> dict[str, Any]:
    """Create a R$ 19,90 Pix charge — the service's normal flow."""
    charge = await provider.create_pix_charge(
        PixChargeRequest(
            amount_cents=1990,
            reference=reference,
            payer=PixPayer(email="comprador@example.com"),
        )
    )
    return {"order_id": charge.provider_charge_id, "status": charge.status.value}


if __name__ == "__main__":
    uvicorn.run(app, host="127.0.0.1", port=8001)
```

```bash
ENV=development MERCADOPAGO_WEBHOOK_SECRET=segredo-local-de-teste python servico.py
```

The front end creates the Pix through the normal flow and calls "pay". Output
measured on 2026-11-14 (the answer's `order` cut down to `id`, `status` and
`status_detail`; the full answer carries the whole order):

```text
$ curl -s -X POST "http://127.0.0.1:8001/charges?reference=pedido-42"
{"order_id":"ORDFAKE4CE323A800000000000002","status":"pending"}

$ curl -s -X POST http://127.0.0.1:8001/dev/mercado-pago/orders/ORDFAKE4CE323A800000000000002/pay
{"order": {"id": "ORDFAKE4CE323A800000000000002", "status": "processed", "status_detail": "accredited"}, "delivery": {"action": "order.processed", "status_code": 200, "body": {"ok": true, "data_id": "ORDFAKE4CE323A800000000000002", "order_status": "processed"}}}

$ curl -s -X POST "http://127.0.0.1:8001/dev/mercado-pago/orders/ORDFAKE4CE323A800000000000002/refund?amount_cents=500"
{"order": {"id": "ORDFAKE4CE323A800000000000002", "status": "processed", "status_detail": "partially_refunded"}, "delivery": {"action": "order.processed", "status_code": 200, "body": {"ok": true, "data_id": "ORDFAKE4CE323A800000000000002", "order_status": "processed"}}}

$ curl -s -w "\nHTTP %{http_code}\n" -X POST http://127.0.0.1:8001/dev/mercado-pago/orders/ORDFAKE4CE323A800000000000002/cancel
{"detail":"409 cannot_cancel_order: The order cannot be canceled in status 'processed'."}
HTTP 409
```

And in the service's log, `on_delivery` called by the webhook route:

```text
on_delivery: charge_paid paid
on_delivery: charge_paid paid
```

!!! danger "Simulation never starts in production"
    `simulate=True` — on the router and on `mercado_pago_provider_for` — only
    builds with `ENV=development` or `ENV=test`. `ENV` comes from
    `settings.ENV` (a `Settings` composing `EnvironmentSettings`) or from the
    environment variable; **a missing `ENV` counts as production**. Anywhere
    else, construction raises and the service does not start:

    ```text
    RuntimeError: mercado_pago_provider_for with simulate=True needs ENV=development or ENV=test (got 'production'); the simulation routes mark orders paid and must never reach production.
    ```

    With `simulate=False` the simulation routes are not mounted, and the
    simulator module is not even imported.

#### API and worker in separate processes: `simulation_url`

The `simulate=True` simulator lives in the memory of **one process**. If your
service has a worker (TaskIQ, a cron) that re-reads the order — the sweep of
pending charges, the refund job — the order created in the API's request
does not exist for it.

The way out is a single simulator outside both: run `tempest mercado-pago
fake-api`, point the API's **and** the worker's `MERCADOPAGO_BASE_URL` at it,
and give the router the same URL in `simulation_url=`. The
`/dev/mercado-pago/orders/...` routes then move the order **on the
fake-api**, through its control prefix (`FAKE_CONTROL_PREFIX`, `/_fake`), and
deliver the signed webhook to the service's own route, as the in-memory mode
does. The fake-api never receives the secret: the router signs.

```python hl_lines="21 38"
from typing import Any

import uvicorn
from fastapi import FastAPI

from tempest_fastapi_sdk.integrations.payment import PixChargeRequest, PixPayer
from tempest_fastapi_sdk.integrations.payment.adapters import (
    MercadoPagoOrderDelivery,
    make_mercado_pago_router,
    mercado_pago_provider_for,
)
from tempest_fastapi_sdk.settings import EnvironmentSettings, MercadoPagoSettings


class Settings(EnvironmentSettings, MercadoPagoSettings):
    """ENV plus the Mercado Pago credentials."""


settings = Settings()
simulate: bool = settings.ENV == "development"
provider = mercado_pago_provider_for(settings)


async def handle(delivery: MercadoPagoOrderDelivery) -> None:
    """Release or revert the order the delivery is about."""
    event = provider.parse_webhook(delivery)
    status = event.charge.status.value if event.charge else None
    print(f"on_delivery: {event.type.value} {status}", flush=True)


app = FastAPI()
app.include_router(
    make_mercado_pago_router(
        provider,
        on_delivery=handle,
        settings=settings,
        simulate=simulate,
        simulation_url=settings.MERCADOPAGO_BASE_URL if simulate else None,
    )
)


@app.post("/charges")
async def create_charge(reference: str) -> dict[str, Any]:
    """Create a R$ 19,90 Pix charge — the service's normal flow."""
    charge = await provider.create_pix_charge(
        PixChargeRequest(
            amount_cents=1990,
            reference=reference,
            payer=PixPayer(email="comprador@example.com"),
        )
    )
    return {"order_id": charge.provider_charge_id, "status": charge.status.value}


if __name__ == "__main__":
    uvicorn.run(app, host="127.0.0.1", port=8001)
```

Two lines changed from the previous example: the provider is the **real**
adapter (`mercado_pago_provider_for(settings)`, no `simulate`), talking to the
host in `MERCADOPAGO_BASE_URL` — the fake-api; and the router gets that same
URL in `simulation_url`. The worker needs nothing from the simulator:

```python
import asyncio
import sys

from tempest_fastapi_sdk.integrations.payment.adapters import (
    mercado_pago_provider_for,
)


async def main(order_id: str) -> None:
    """Re-read a charge, as a sweep of pending charges does."""
    provider = mercado_pago_provider_for()
    charge = await provider.get_pix_charge(order_id)
    print(f"worker: {charge.provider_charge_id} {charge.status.value}")


if __name__ == "__main__":
    asyncio.run(main(sys.argv[1]))
```

In three terminals, all with the same environment:

```bash
tempest mercado-pago fake-api            # http://127.0.0.1:8090

export ENV=development
export MERCADOPAGO_BASE_URL=http://127.0.0.1:8090
export MERCADOPAGO_WEBHOOK_SECRET=segredo-local-de-teste
python servico.py                        # http://127.0.0.1:8001

python worker.py <order_id>              # same environment
```

Output measured on 2026-11-14 — the Pix created by the API, the worker
reading the order in another process, then paying and refunding **by the
`correlation_id`** (the answer's `order` cut to `id`, `status` and
`status_detail`):

```text
$ curl -s -X POST "http://127.0.0.1:8001/charges?reference=compra-7f3a"
{"order_id":"ORDFAKE4627FF3D00000000000002","status":"pending"}
$ python worker.py ORDFAKE4627FF3D00000000000002
worker: ORDFAKE4627FF3D00000000000002 pending
$ curl -s -X POST http://127.0.0.1:8001/dev/mercado-pago/orders/compra-7f3a/pay
{"order": {"id": "ORDFAKE4627FF3D00000000000002", "status": "processed", "status_detail": "accredited"}, "delivery": {"action": "order.processed", "status_code": 200, "body": {"ok": true, "data_id": "ORDFAKE4627FF3D00000000000002", "order_status": "processed"}}}
$ python worker.py ORDFAKE4627FF3D00000000000002
worker: ORDFAKE4627FF3D00000000000002 paid
$ curl -s -X POST http://127.0.0.1:8001/dev/mercado-pago/orders/compra-7f3a/refund
{"order": {"id": "ORDFAKE4627FF3D00000000000002", "status": "refunded", "status_detail": "refunded"}, "delivery": {"action": "order.refunded", "status_code": 200, "body": {"ok": true, "data_id": "ORDFAKE4627FF3D00000000000002", "order_status": "refunded"}}}
$ python worker.py ORDFAKE4627FF3D00000000000002
worker: ORDFAKE4627FF3D00000000000002 refunded
$ curl -s -w "\nHTTP %{http_code}\n" -X POST http://127.0.0.1:8001/dev/mercado-pago/orders/compra-404/pay
{"detail":"order compra-404 not found"}
HTTP 404
```

And in the service's log, `on_delivery: charge_paid paid` and
`on_delivery: charge_refunded refunded` — the signed delivery went through the
signature check and the re-read on the fake-api. With the fake-api stopped,
the route answers `502`:

```text
{"detail":"simulator unavailable: ConnectError('All connection attempts failed')"}
HTTP 502
```

!!! tip "Injected HTTP client"
    Without `simulation_client=`, each call to the fake-api opens and closes
    an `httpx.AsyncClient`. To reuse connections, pass yours
    (`simulation_client=client`); its lifecycle stays with you. The piece the
    router builds is `RemoteMercadoPagoSimulation`, which you can also use
    directly in a test (`await remote.apply("compra-7f3a", "pay")`).

!!! note "`MERCADOPAGO_ACCESS_TOKEN` may stay empty against the fake-api"
    With an empty token, `mercado_pago_kwargs()` sends no `Authorization`
    header — `Bearer ` with nothing after it is a header value `httpx`
    refuses before sending (`httpx.LocalProtocolError`), which is what
    happened on every call up to 0.360.0.

!!! warning "The fake-api's state is still in memory"
    Restarting `tempest mercado-pago fake-api` in the middle of a manual test
    loses the orders. A persistent store (Redis) for the simulator does not
    exist yet.

The control prefix also finds the order by `external_reference`, so
`simulate-webhook` takes the `correlation_id` in `--order-id` when
`--fake-api` is given.

#### Manual withdrawal

Mercado Pago's payout is not enabled for the account (#499). Meanwhile, the
router pays withdrawals through an administrator: with `withdraw=`, it mounts
`POST /api/mercado-pago/withdraw`, which:

1. debits the user's **whole** available balance through
   `WalletService.withdraw` — the route takes no amount, the wallet ends at
   zero;
2. writes the `PENDING` row of the manual payout table
   (`make_manual_payout_model`), with the key **masked**;
3. e-mails the administrator the amount, Pix key and type, `user_id`,
   `correlation_id` and the link to the row in the panel.

The user and the key come from the same dependencies as `make_wallet_router`
(`current_user_id`, `pix_destination` — the key comes from the profile, never
from the body). The e-mail goes through `ManualPayoutProvider`, which takes
any object with `EmailUtils`' `send(to, subject, body)`: in production,
`EmailUtils(**settings.email_kwargs())`; in development, `FakeEmailUtils`
keeps the message in `outbox`.

```python
import asyncio
from collections.abc import AsyncIterator
from uuid import UUID

import httpx
from fastapi import FastAPI, Header
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from tempest_fastapi_sdk import (
    AsyncDatabaseManager,
    BaseRepository,
    BaseUserModel,
    PixKeyType,
    UserModelAuthBackend,
    register_exception_handlers,
)
from tempest_fastapi_sdk.admin import AdminSite, make_admin_router, manual_payout_admin
from tempest_fastapi_sdk.integrations.payment.adapters import (
    MercadoPagoOrderDelivery,
    MercadoPagoWithdrawConfig,
    make_mercado_pago_router,
    mercado_pago_provider_for,
)
from tempest_fastapi_sdk.settings import EnvironmentSettings, MercadoPagoSettings
from tempest_fastapi_sdk.testing.fakes import FakeEmailUtils
from tempest_fastapi_sdk.utils import LocalUploadStorage
from tempest_fastapi_sdk.wallet import (
    PixDestinationSchema,
    WalletBalanceMixin,
    WalletService,
    make_manual_payout_model,
    make_wallet_entry_model,
)


class UserModel(BaseUserModel, WalletBalanceMixin):
    """The service's user, with a wallet."""

    __tablename__ = "users"


WalletEntryModel = make_wallet_entry_model(user_table="users")
ManualPayoutModel = make_manual_payout_model(user_table="users")


class Settings(EnvironmentSettings, MercadoPagoSettings):
    """ENV plus the Mercado Pago credentials."""


settings = Settings()
db = AsyncDatabaseManager("sqlite+aiosqlite:///:memory:")
mailer = FakeEmailUtils()
provider = mercado_pago_provider_for(settings, simulate=settings.ENV == "development")


def wallet_service(session: AsyncSession) -> WalletService:
    """Build the request's wallet service."""
    return WalletService(
        balances=BaseRepository(session, model=UserModel),
        entries=BaseRepository(session, model=WalletEntryModel),
    )


async def sessions() -> AsyncIterator[AsyncSession]:
    """Yield the request's session."""
    async with db.get_session_context() as session:
        yield session


def current_user_id(x_user_id: UUID = Header()) -> UUID:
    """Stand-in for the service's auth dependency."""
    return x_user_id


def pix_destination() -> PixDestinationSchema:
    """Stand-in for reading the key from the user's profile."""
    return PixDestinationSchema(
        pix_key="motorista@example.com", pix_key_type=PixKeyType.EMAIL
    )


async def handle(delivery: MercadoPagoOrderDelivery) -> None:
    """Credit the seller's wallet when an order is paid (omitted here)."""


app = FastAPI()
register_exception_handlers(app)
app.include_router(
    make_mercado_pago_router(
        provider,
        on_delivery=handle,
        settings=settings,
        simulate=settings.ENV == "development",
        withdraw=MercadoPagoWithdrawConfig(
            service_factory=wallet_service,
            session_factory=sessions,
            current_user_id=current_user_id,
            pix_destination=pix_destination,
            mailer=mailer,
            admin_emails=["financeiro@example.com"],
            record_model=ManualPayoutModel,
            record_url="http://127.0.0.1:8000/admin/m/manual_payouts/{id}",
        ),
    )
)
site = AdminSite(title="Admin")
site.register(
    manual_payout_admin(ManualPayoutModel, upload_storage=LocalUploadStorage("receipts"))
)
app.include_router(
    make_admin_router(
        site,
        db=db,
        auth_backend=UserModelAuthBackend(UserModel),
        secret_key="troque-por-um-segredo-de-32-bytes-ou-mais",
    )
)


async def main() -> None:
    """Fund a wallet, withdraw twice, and show what the admin received."""
    await db.connect()
    await db.create_tables()
    async with db.get_session_context() as session:
        user = UserModel(email="motorista@example.com", hashed_password="x")
        session.add(user)
        await session.commit()
        await wallet_service(session).credit(user.id, 12_345, kind="SALE")
        user_id = str(user.id)
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://local"
    ) as client:
        headers = {"X-User-Id": user_id}
        first = await client.post("/api/mercado-pago/withdraw", headers=headers)
        print("saque     ", first.status_code, first.json()["payout"])
        second = await client.post("/api/mercado-pago/withdraw", headers=headers)
        print("de novo   ", second.status_code, second.json()["code"])
    async with db.get_session_context() as session:
        row = (await session.execute(select(ManualPayoutModel))).scalar_one()
        print("registro  ", row.status, row.amount_cents, row.pix_key_masked)
    print(mailer.outbox[0].subject)
    print(mailer.outbox[0].body)


asyncio.run(main())
```

```bash
ENV=development MERCADOPAGO_WEBHOOK_SECRET=segredo-local-de-teste python saque_manual.py
```

Output measured on 2026-11-14 (the e-mail is in Portuguese, as the SDK writes
it):

```text
saque      200 {'correlation_id': 'fee3bada-4405-4265-91d5-ad5d28127a94', 'status': 'pending', 'provider': 'manual', 'provider_status': 'manual_review'}
de novo    409 WALLET_INSUFFICIENT_BALANCE
registro   pending 12345 mo***om
[Saque manual] R$ 123,45 para chave Pix
Um usuário pediu um saque e o valor já foi debitado da carteira.
Faça o Pix manualmente a partir da conta da plataforma:

Valor: R$ 123,45
Chave Pix: motorista@example.com
Tipo da chave: email
Usuário (user_id): 8016e5b7-2036-452a-b503-cfa5cd08ff7f
Identificador (correlation_id): fee3bada-4405-4265-91d5-ad5d28127a94
Comentário: Saque Pix

Use o identificador acima como descrição do Pix, para conciliar.
Se não for possível pagar, estorne o débito na carteira do usuário com o mesmo identificador.

Depois de pagar, anexe o comprovante e marque como pago: http://127.0.0.1:8000/admin/m/manual_payouts/4bcf96aa-c394-45fb-9e88-42e8cbbf3ba7
```

What goes wrong, and what the route answers:

| Case | Answer | The wallet |
| --- | --- | --- |
| nothing available (the second withdrawal included) | `409 WALLET_INSUFFICIENT_BALANCE` | untouched |
| the e-mail to the administrator failed (`EmailDeliveryException`, `IntegrationDisabledException`) | `502 WALLET_PAYOUT_REJECTED`; the row turns `CANCELED` | debit given back |
| the row could not be written | `502 WALLET_PAYOUT_REJECTED`; no e-mail | debit given back |
| any other error while sending | `502 WALLET_PAYOUT_UNCERTAIN`; the row stays `PENDING` | debit kept, `CRITICAL` log |
| user without a Pix key | whatever your `pix_destination` raises | untouched |

!!! note "The row is written after the debit, before the e-mail"
    `WalletService.withdraw` commits the debit before calling the provider —
    that is what stops two concurrent withdrawals from spending the same
    balance. `ManualPayoutProvider` writes the row right after and only then
    sends the e-mail; if the write fails, it refuses and the debit comes
    back. The only window without a row is the process dying between the
    debit and the write: then the ledger's `WITHDRAW` line, whose
    `reference_id` is the `correlation_id`, is what reconciles it.

##### In the panel: attach the receipt and mark as paid

`manual_payout_admin(ManualPayoutModel, upload_storage=...)` registers the
table in the panel the way the job needs:

- list filtered by `status` and searchable by `correlation_id`;
- amount, user, key, type, `correlation_id`, status and who completed it are
  read-only; no create, delete or import;
- `receipt_key` is an upload that takes **PDF only**: the declared type must
  be `application/pdf` **and** the bytes must start with `%PDF-` — a PNG
  renamed `.pdf` is refused with a field error, and nothing is stored;
- the row's page has the receipt's **Download** link;
- the **Marcar como pago** ("mark as paid") action completes only `PENDING`
  rows with a receipt, writing `completed_at` and `completed_by` (the
  signed-in administrator); a row without a receipt stays `PENDING` and the
  message lists its `correlation_id`;
- a `COMPLETED` row never goes back, and its receipt can no longer be
  replaced (the upload is refused with a field error).

!!! info "Refusing a withdrawal gives the amount back to the wallet"
    `CANCELED` only appears when the e-mail failed and the withdrawal itself
    already gave the debit back. For the administrator to refuse a `PENDING`
    row, pass `ledger_factory=` to `manual_payout_admin`: the **Recusar e
    devolver** ("refuse and refund") action turns the row `REJECTED` and
    credits the amount in a `WITHDRAW_REFUND` line, in the same commit — see
    [Wallet » Completing or refusing a withdrawal by hand](wallet.md#completing-or-refusing-a-withdrawal-by-hand-manualpayoutservice).

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
| `simulated` | an order id that does not exist → Mercado Pago answers `404` on the re-read, the route answers `200` with no charge | an id the API does not know (probably what the dashboard's simulation sends; not yet observed); answering an error would make Mercado Pago resend, per the provider's documentation (not observed here) |

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
simulation probably carries an id the API does not know (not yet observed),
and levels 1 and 2 showed what happens then.

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
- `make_mercado_pago_router` mounts the webhook route; with `simulate=True`
  (`development`/`test` only), the front end pays and refunds through the
  `/dev/mercado-pago/...` routes, which deliver the webhook to the real
  route; with `withdraw=`, a withdrawal empties the wallet and becomes a
  manual Pix the administrator completes in the panel, with the PDF receipt.
- `sign_manifest` produces a notification your route accepts: test the whole
  path without a public URL.
- A wrong signature is `401` before any call; an unknown id is `200` with no
  charge, because answering an error would make Mercado Pago resend — that is
  what the provider's documentation describes; the redelivery was not
  observed here.
- For the dashboard and a real delivery, a tunnel (`cloudflared`, `ngrok`)
  gives the public URL.
