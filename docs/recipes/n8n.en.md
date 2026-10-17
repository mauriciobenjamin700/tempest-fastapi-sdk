# n8n (workflow automation)

[n8n](https://n8n.io) builds *workflows* by wiring nodes together: a
**Webhook node** that receives a call, an **HTTP Request node** that calls an
API, and so on. Your service talks to it in **two directions**, and both
already fit the SDK's building blocks:

- **Service → n8n:** the service calls the Webhook node with the
  [`HTTPClient`](http-client.md) and an authentication header.
- **n8n → service:** the HTTP Request node calls a service route with
  *Header Auth*, and the route is protected by
  [`make_token_dependency`](http.md).

And since triggering a workflow from a database write is a *dual write*, the
finish is the [outbox](outbox.md) (delivery to n8n) + `WebhookInbox`
(receives the result exactly once) pair.

!!! info "No new SDK module, on purpose"
    The contract is "POST with a header" in both directions, and `HTTPClient`
    already brings retry, circuit breaker and `default_headers`. An
    `N8nClient` would add almost nothing over `HTTPClient.post`.

!!! note "Why not `WebhookSender`"
    `WebhookSender` signs the delivery with HMAC, but n8n's Webhook node
    authenticates with Basic, Header or JWT — it does not verify HMAC. The fit
    is Header Auth.

!!! check "Everything here was executed"
    Every command and every output on this page came from a real run against
    **n8n 2.43.7** (`n8nio/n8n:2.43.7`, digest
    `sha256:3db21366be1d3c77c1708963129c051948573019659e8873e0294be035ffc1b2`)
    with a local FastAPI service, Python 3.12. The response messages below are
    that version's.

## 1. Start n8n for testing

```bash
docker run -d --name lab-n8n -p 15678:5678 \
  --add-host=host.docker.internal:host-gateway \
  -e N8N_SECURE_COOKIE=false \
  n8nio/n8n:2.43.7
```

`--add-host=host.docker.internal:host-gateway` lets the workflow (inside the
container) reach your service, which runs on the host. Create the instance
owner and check that it is up:

```bash
curl -s -X POST localhost:15678/rest/owner/setup -H 'content-type: application/json' \
  -d '{"email":"owner@example.com","firstName":"Lab","lastName":"Owner","password":"Lab12345678"}'
curl -s -o /dev/null -w '%{http_code}\n' localhost:15678/healthz
```

```text
{"data":{"createdAt":"2026-10-17T19:16:18.597Z","id":"3d49df4b-8f1a-409e-b39f-3007a19ccd30","email":"owner@example.com","firstName":"Lab","lastName":"Owner", ... "role":"global:owner", ...
200
```

## 2. The service

A single file with everything the next sections use: the tool route the
workflow calls, the callback it sends when it finishes, and the order that
writes **the order and the event in the same transaction**.

```python title="n8n_service.py"
import asyncio
import os
from collections.abc import AsyncGenerator
from contextlib import asynccontextmanager, suppress
from typing import Annotated, Any
from uuid import UUID, uuid4

from fastapi import Depends, FastAPI
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import Mapped, mapped_column

from tempest_fastapi_sdk import (
    AsyncDatabaseManager,
    BaseModel,
    BaseOutboxModel,
    BaseRepository,
    BaseSchema,
    BaseWebhookEventModel,
    HTTPClient,
    OutboxRelay,
    WebhookInbox,
    make_token_dependency,
    register_exception_handlers,
)

TOKEN_SECRET: str = os.environ.get("TOKEN_SECRET", "tool-secret-123")
N8N_URL: str = os.environ.get("N8N_URL", "http://127.0.0.1:15678")
N8N_WEBHOOK_PATH: str = os.environ.get("N8N_WEBHOOK_PATH", "/webhook/order-created")
N8N_WEBHOOK_TOKEN: str = os.environ.get("N8N_WEBHOOK_TOKEN", "n8n-hook-secret-456")

db: AsyncDatabaseManager = AsyncDatabaseManager("sqlite+aiosqlite:///./n8n_demo.db")
require_token = make_token_dependency(TOKEN_SECRET)
Session = Annotated[AsyncSession, Depends(db.session_dependency)]


class OrderModel(BaseModel):
    """Service order."""

    __tablename__ = "orders"

    reference: Mapped[str] = mapped_column()
    status: Mapped[str] = mapped_column(default="created")


class OutboxModel(BaseOutboxModel):
    """Events waiting to be delivered to n8n."""

    __tablename__ = "outbox"


class WebhookEventModel(BaseWebhookEventModel):
    """Callbacks received from n8n, deduplicated per execution."""

    __tablename__ = "webhook_events"


class OrderCreateSchema(BaseSchema):
    """Body of `POST /api/orders`."""

    reference: str


class EnrichRequestSchema(BaseSchema):
    """Body the workflow sends to the tool route."""

    order_id: str
    source: str


class CallbackSchema(BaseSchema):
    """Body the workflow sends when it finishes."""

    execution_id: str
    order_id: str
    enriched: dict[str, Any]


inbox: WebhookInbox[WebhookEventModel] = WebhookInbox(
    db, model=WebhookEventModel, backoff_base_seconds=1.0
)
n8n: HTTPClient = HTTPClient(
    base_url=N8N_URL,
    timeout=15.0,
    default_headers={"X-Webhook-Token": N8N_WEBHOOK_TOKEN},
)


async def publish(event: BaseOutboxModel) -> None:
    """Deliver an outbox event to n8n's Webhook node.

    Args:
        event (BaseOutboxModel): The outbox row.

    Raises:
        httpx.HTTPStatusError: If n8n does not answer 2xx; the relay schedules a retry.
    """
    response = await n8n.post(N8N_WEBHOOK_PATH, json=event.payload)
    response.raise_for_status()


async def apply_callback(event: WebhookEventModel, session: AsyncSession) -> None:
    """Apply n8n's callback: mark the order as enriched.

    Args:
        event (WebhookEventModel): The claimed event.
        session (AsyncSession): The session of the transaction that also closes the row.
    """
    order = await session.get(OrderModel, UUID(event.payload["order_id"]))
    if order is not None:
        order.status = "enriched"


async def loop_forever(step: Any) -> None:
    """Run `step()` every half second until cancelled.

    Args:
        step (Any): Coroutine function without arguments to repeat.
    """
    while True:
        await step()
        await asyncio.sleep(0.5)


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncGenerator[None, None]:
    """Create the tables, start the outbox relay and the inbox sweep."""
    await db.connect()
    await db.create_tables()
    relay = OutboxRelay(db, model=OutboxModel, publish=publish, backoff_base_seconds=1.0)

    async def sweep() -> None:
        await inbox.drain_once(apply_callback)

    tasks = [
        asyncio.create_task(relay.run(poll_interval=0.5)),
        asyncio.create_task(loop_forever(sweep)),
    ]
    yield
    for task in tasks:
        task.cancel()
    for task in tasks:
        with suppress(asyncio.CancelledError):
            await task
    await n8n.aclose()
    await db.disconnect()


app = FastAPI(lifespan=lifespan)
register_exception_handlers(app)


@app.post("/api/orders", status_code=201)
async def create_order(data: OrderCreateSchema, session: Session) -> dict[str, str]:
    """Create the order and the `orders.created` event in the same transaction."""
    repo: BaseRepository[OrderModel] = BaseRepository(session, model=OrderModel)
    order_id = uuid4()
    event = OutboxModel.new_event(
        "orders.created", {"order_id": str(order_id), "reference": data.reference}
    )
    order = await repo.save_with_outbox(
        OrderModel(id=order_id, reference=data.reference), event
    )
    return {"id": str(order.id), "status": order.status}


@app.get("/api/orders/{order_id}")
async def get_order(order_id: UUID, session: Session) -> dict[str, str]:
    """Return the order's current state."""
    order = await BaseRepository(session, model=OrderModel).get_by_id(order_id)
    return {"id": str(order.id), "status": order.status}


@app.post("/api/tools/enrich-order", dependencies=[Depends(require_token)])
async def enrich_order(data: EnrichRequestSchema) -> dict[str, str]:
    """Tool route the workflow calls, protected by `X-Token`."""
    return {"order_id": data.order_id, "caller": data.source, "tier": "gold"}


@app.post("/api/callbacks/n8n", dependencies=[Depends(require_token)])
async def n8n_callback(data: CallbackSchema) -> dict[str, bool]:
    """Record the callback in the inbox and answer 200, even for a repeat."""
    new_id = await inbox.record(
        "n8n", data.execution_id, "workflow.finished", data.model_dump()
    )
    return {"recorded": new_id is not None}
```

Piece by piece:

- `make_token_dependency(TOKEN_SECRET)` compares the `X-Token` header with
  `hmac.compare_digest`. The tool route and the callback share the same
  dependency — it is the counterpart of the HTTP Request node with *Header
  Auth*.
- `n8n` is the outbound `HTTPClient`, with the Webhook node's header in
  `default_headers`.
- `publish` is the `publish(event)` adapter `OutboxRelay` requires: a single
  argument, and `raise_for_status()` so a non-2xx becomes a retry.
- `n8n_callback` writes to the `WebhookInbox` keyed by
  `(provider, external_id)` and answers `200` **even for a repeat**. The
  `external_id` is n8n's `$execution.id`.

Start the service (bound to `0.0.0.0` here because the caller is a container,
a separate origin):

```bash
uvicorn n8n_service:app --host 0.0.0.0 --port 18001
```

## 3. Credentials and workflows

Two *Header Auth* (`httpHeaderAuth`) credentials: one the Webhook node
**requires** from callers, another the HTTP Request node **sends** to the
service.

```json title="credentials.json"
[
  {
    "id": "credWebhookIn",
    "name": "Webhook de entrada (Header Auth)",
    "type": "httpHeaderAuth",
    "data": {
      "name": "X-Webhook-Token",
      "value": "n8n-hook-secret-456"
    }
  },
  {
    "id": "credServiceOut",
    "name": "Servico FastAPI (Header Auth)",
    "type": "httpHeaderAuth",
    "data": {
      "name": "X-Token",
      "value": "tool-secret-123"
    }
  }
]
```

The first workflow answers **right away** (`responseMode: onReceived`) and
ends by notifying the service:

```json title="order_created.json"
{
  "id": "orderCreated01",
  "name": "order-created",
  "nodes": [
    {
      "id": "0b1a0000-0000-4000-8000-000000000001",
      "name": "Webhook",
      "type": "n8n-nodes-base.webhook",
      "typeVersion": 2.1,
      "position": [0, 0],
      "webhookId": "5d1f0000-0000-4000-8000-000000000001",
      "parameters": {
        "httpMethod": "POST",
        "path": "order-created",
        "authentication": "headerAuth",
        "responseMode": "onReceived"
      },
      "credentials": {
        "httpHeaderAuth": { "id": "credWebhookIn", "name": "Webhook de entrada (Header Auth)" }
      }
    },
    {
      "id": "0b1a0000-0000-4000-8000-000000000002",
      "name": "Enriquecer pedido",
      "type": "n8n-nodes-base.httpRequest",
      "typeVersion": 4.2,
      "position": [260, 0],
      "parameters": {
        "method": "POST",
        "url": "http://host.docker.internal:18001/api/tools/enrich-order",
        "authentication": "genericCredentialType",
        "genericAuthType": "httpHeaderAuth",
        "sendBody": true,
        "specifyBody": "json",
        "jsonBody": "={{ { order_id: $('Webhook').item.json.body.order_id, source: 'n8n' } }}"
      },
      "credentials": {
        "httpHeaderAuth": { "id": "credServiceOut", "name": "Servico FastAPI (Header Auth)" }
      }
    },
    {
      "id": "0b1a0000-0000-4000-8000-000000000003",
      "name": "Avisar o servico",
      "type": "n8n-nodes-base.httpRequest",
      "typeVersion": 4.2,
      "position": [520, 0],
      "parameters": {
        "method": "POST",
        "url": "http://host.docker.internal:18001/api/callbacks/n8n",
        "authentication": "genericCredentialType",
        "genericAuthType": "httpHeaderAuth",
        "sendBody": true,
        "specifyBody": "json",
        "jsonBody": "={{ { execution_id: $execution.id, order_id: $('Webhook').item.json.body.order_id, enriched: $json } }}"
      },
      "credentials": {
        "httpHeaderAuth": { "id": "credServiceOut", "name": "Servico FastAPI (Header Auth)" }
      }
    }
  ],
  "connections": {
    "Webhook": { "main": [[{ "node": "Enriquecer pedido", "type": "main", "index": 0 }]] },
    "Enriquecer pedido": { "main": [[{ "node": "Avisar o servico", "type": "main", "index": 0 }]] }
  },
  "settings": { "executionOrder": "v1" },
  "active": true
}
```

The second uses **Respond to Webhook** (`responseMode: responseNode`): the
caller waits and receives what the workflow returns.

```json title="order_sync.json"
{
  "id": "orderSync0001",
  "name": "order-sync",
  "nodes": [
    {
      "id": "0b1a0000-0000-4000-8000-000000000011",
      "name": "Webhook",
      "type": "n8n-nodes-base.webhook",
      "typeVersion": 2.1,
      "position": [0, 0],
      "webhookId": "5d1f0000-0000-4000-8000-000000000011",
      "parameters": {
        "httpMethod": "POST",
        "path": "order-sync",
        "authentication": "headerAuth",
        "responseMode": "responseNode"
      },
      "credentials": {
        "httpHeaderAuth": { "id": "credWebhookIn", "name": "Webhook de entrada (Header Auth)" }
      }
    },
    {
      "id": "0b1a0000-0000-4000-8000-000000000012",
      "name": "Enriquecer pedido",
      "type": "n8n-nodes-base.httpRequest",
      "typeVersion": 4.2,
      "position": [260, 0],
      "parameters": {
        "method": "POST",
        "url": "http://host.docker.internal:18001/api/tools/enrich-order",
        "authentication": "genericCredentialType",
        "genericAuthType": "httpHeaderAuth",
        "sendBody": true,
        "specifyBody": "json",
        "jsonBody": "={{ { order_id: $('Webhook').item.json.body.order_id, source: 'n8n' } }}"
      },
      "credentials": {
        "httpHeaderAuth": { "id": "credServiceOut", "name": "Servico FastAPI (Header Auth)" }
      }
    },
    {
      "id": "0b1a0000-0000-4000-8000-000000000013",
      "name": "Responder",
      "type": "n8n-nodes-base.respondToWebhook",
      "typeVersion": 1.5,
      "position": [520, 0],
      "parameters": {
        "respondWith": "json",
        "responseBody": "={{ $json }}"
      }
    }
  ],
  "connections": {
    "Webhook": { "main": [[{ "node": "Enriquecer pedido", "type": "main", "index": 0 }]] },
    "Enriquecer pedido": { "main": [[{ "node": "Responder", "type": "main", "index": 0 }]] }
  },
  "settings": { "executionOrder": "v1" },
  "active": true
}
```

Import through the container's own CLI (order matters: credentials first):

```bash
docker cp n8n lab-n8n:/tmp/wf
docker exec lab-n8n n8n import:credentials --input=/tmp/wf/credentials.json
docker exec lab-n8n sh -c 'mkdir -p /tmp/wfs && cp /tmp/wf/order_*.json /tmp/wfs/'
docker exec lab-n8n n8n import:workflow --separate --input=/tmp/wfs
```

```text
Successfully imported 2 credentials.
Deactivating workflow "order-created".
Deactivating workflow "order-sync".
Successfully imported 2 workflows.
```

The import **deactivates** the workflows. I tried `--activeState=fromJson` and
n8n refused:

```text
The "--activeState=fromJson" flag can only be used when n8n is running in queue or multi-main mode. In regular deployment mode, workflow activation is not supported.
```

In regular mode, publish by id and **restart** — the CLI itself warns
"Changes will not take effect if n8n is running":

```bash
docker exec lab-n8n n8n publish:workflow --id=orderCreated01
docker exec lab-n8n n8n publish:workflow --id=orderSync0001
docker restart lab-n8n
```

After the restart, the log ends with
`Processed 0 draft workflows, 2 published workflows.`

## 4. Trigger from the service

```python title="n8n_trigger.py"
import asyncio
from typing import Any

from tempest_fastapi_sdk import HTTPClient, RetryPolicy

PRODUCTION_PREFIX: str = "/webhook"
TEST_PREFIX: str = "/webhook-test"


def make_n8n_client(base_url: str, header_name: str, header_value: str) -> HTTPClient:
    """Build the n8n client with the Webhook node's Header Auth.

    Args:
        base_url (str): n8n root, without a trailing slash.
        header_name (str): Header name set in the Header Auth credential.
        header_value (str): Header value (the secret).

    Returns:
        HTTPClient: Client with retry, circuit breaker and the auth header.
    """
    return HTTPClient(
        base_url=base_url,
        timeout=30.0,
        retry_policy=RetryPolicy(max_attempts=3),
        default_headers={header_name: header_value},
    )


async def call_webhook(
    client: HTTPClient,
    path: str,
    payload: dict[str, Any],
    *,
    test: bool = False,
) -> dict[str, Any]:
    """Call an n8n Webhook node.

    Args:
        client (HTTPClient): The n8n client.
        path (str): The `path` set on the Webhook node.
        payload (dict[str, Any]): JSON body, which the workflow reads in `body`.
        test (bool): Use the test URL (`/webhook-test/...`) instead of the production one.

    Returns:
        dict[str, Any]: The response JSON. In immediate mode it is only a notice
        that the workflow started; with "Respond to Webhook" it is what the workflow returns.

    Raises:
        httpx.HTTPStatusError: If n8n does not answer 2xx.
    """
    prefix: str = TEST_PREFIX if test else PRODUCTION_PREFIX
    response = await client.post(f"{prefix}/{path}", json=payload)
    response.raise_for_status()
    return dict(response.json())


async def main() -> None:
    """Call both response modes against a local n8n and print the result."""
    client = make_n8n_client("http://127.0.0.1:15678", "X-Webhook-Token", "n8n-hook-secret-456")
    try:
        immediate = await call_webhook(
            client, "order-created", {"order_id": "22222222-2222-4222-8222-222222222222"}
        )
        print("immediate:", immediate)
        synced = await call_webhook(client, "order-sync", {"order_id": "demo-2"})
        print("respond-to-webhook:", synced)
    finally:
        await client.aclose()


if __name__ == "__main__":
    asyncio.run(main())
```

```bash
python n8n_trigger.py
```

```text
immediate: {'message': 'Workflow was started'}
respond-to-webhook: {'order_id': 'demo-2', 'caller': 'n8n', 'tier': 'gold'}
```

### The Webhook node's authentication

Without the header, or with a wrong one, n8n refuses the call:

```bash
curl -s -X POST localhost:15678/webhook/order-sync -H 'content-type: application/json' \
  -d '{"order_id":"1"}' -w ' HTTP %{http_code}\n'
curl -s -X POST localhost:15678/webhook/order-sync -H 'X-Webhook-Token: x' \
  -H 'content-type: application/json' -d '{"order_id":"1"}' -w ' HTTP %{http_code}\n'
```

```text
Authorization data is wrong! HTTP 403
Authorization data is wrong! HTTP 403
```

### Immediate response or "Respond to Webhook"

| Mode | Webhook node | Measured response |
| --- | --- | --- |
| Immediate | `responseMode: onReceived` | `200` `{"message":"Workflow was started"}` |
| Respond to Webhook | `responseMode: responseNode` | `200` with the workflow's JSON, e.g. `{"order_id":"1","caller":"n8n","tier":"gold"}` |

Immediate is what the outbox wants: delivery ends when n8n accepts, and the
result comes back through the callback. Respond to Webhook holds the
connection until the last node — and the workflow's error becomes the call's
error. With the service still down, `order-sync` answered:

```text
{"message":"Error in workflow"} HTTP 500
```

`HTTPClient` retries 5xx responses (see the [recipe](http-client.md)), so
that `500` may repeat the call; a workflow that is not idempotent will run
again.

### Test URL and production URL

The Webhook node has two URLs: `/webhook/<path>` (production, valid for the
**published** workflow) and `/webhook-test/<path>` (only works while the
editor is listening for a test event). Calling the test one with the editor
not listening:

```text
{"code":404,"message":"The requested webhook \"order-created\" is not registered.","hint":"Click the 'Execute workflow' button on the canvas, then try again. (In test mode, the webhook only works for one call after you click this button)"} HTTP 404
```

That is why `call_webhook` has the `test` parameter and defaults to
production.

## 5. Guaranteed delivery: outbox → n8n, inbox ← n8n

Create an order. The service answers `201` and does **not** wait for n8n:

```bash
curl -s -X POST localhost:18001/api/orders -H 'content-type: application/json' \
  -d '{"reference":"PED-N8N-1"}'
sleep 4
curl -s localhost:18001/api/orders/c492aece-3554-460b-945f-0b871237c6e7
```

```text
{"id":"c492aece-3554-460b-945f-0b871237c6e7","status":"created"}
{"id":"c492aece-3554-460b-945f-0b871237c6e7","status":"enriched"}
```

In the service log, the workflow called the tool route and the callback, both
with the *Header Auth* token:

```text
INFO:     172.17.0.3:57964 - "POST /api/tools/enrich-order HTTP/1.1" 200 OK
INFO:     172.17.0.3:57966 - "POST /api/callbacks/n8n HTTP/1.1" 200 OK
```

### n8n down

With the container frozen (`docker pause lab-n8n`), a new order answers `201`
all the same. Twenty-five seconds later, the outbox row is still `pending`
and the order `created`; unfrozen (`docker unpause lab-n8n`), the row becomes
`sent` and the order `enriched`:

```text
{"id":"5352a463-d1ab-422a-a3dd-646bf0ae1f6b","status":"created"}
('orders.created', 'pending', 0, None)          # the new order's row, frozen, 25 s later
{"id":"5352a463-d1ab-422a-a3dd-646bf0ae1f6b","status":"created"}
('orders.created', 'sent', 0)                   # after the unpause
{"id":"5352a463-d1ab-422a-a3dd-646bf0ae1f6b","status":"enriched"}
```

!!! note "What this test measures and what it does not"
    `docker pause` freezes the process; the connection hangs instead of being
    refused, and at 25 s the relay had not yet recorded a failure
    (`attempts` still `0`). This test shows the event **waits, durable, in the
    table** and goes out when n8n is back. The path with `attempts` climbing,
    `last_error` and exponential backoff is the one for fast failures,
    described in the [outbox recipe](outbox.md#failures-and-retry).

### The repeated callback

The inbox answers `200` and stores once. Two deliveries with the same
`execution_id`:

```text
{"recorded":true}
{"recorded":false}
```

```text
('n8n', 'dup-1', 'received')     # a single row, before the sweep processed it
```

!!! danger "It is at-least-once in both directions"
    The outbox may publish the same event twice (worker killed between the
    send and the `commit`), so the **workflow** may run twice for the same
    order. Make it idempotent on `body.order_id`. On our side, the inbox
    already deduplicates the callback by `(provider, external_id)`.

## Tear the environment down

```bash
docker rm -f lab-n8n
```

## Recap

- **Service → n8n:** `HTTPClient` with the *Header Auth* header in
  `default_headers` + `POST /webhook/<path>`. Without the header (or with a
  wrong one): `403`.
- **Two response modes:** `onReceived` (`{"message":"Workflow was
  started"}`) for the outbox; *Respond to Webhook* when the caller needs the
  result — and then the workflow's error is a `500` on the call.
- **Production vs test:** `/webhook/...` requires a published workflow;
  `/webhook-test/...` gives `404` without the editor listening.
- **n8n → service:** HTTP Request node with a *Header Auth* credential
  (`X-Token`) against a route with `make_token_dependency`.
- **Guaranteed delivery:** `save_with_outbox` + `OutboxRelay` on the way out;
  `WebhookInbox.record` keyed by `$execution.id` on the way back.
- No new module: all of this is `HTTPClient`, `make_token_dependency`,
  `OutboxRelay` and `WebhookInbox`.
