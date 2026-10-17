# Kestra (workflow orchestrator)

[Kestra](https://kestra.io) runs declarative YAML *flows* — call an API,
wait, retry, chain steps. Your service talks to it in **two directions**, and
both already fit the SDK's building blocks:

- **Service → Kestra:** the service triggers a flow (webhook or execution
  API) with the [`HTTPClient`](http-client.md).
- **Kestra → service:** the flow calls a service route through the
  `io.kestra.plugin.core.http.Request` task, protected by
  [`make_token_dependency`](http.md).

And since triggering a flow from a database write is the classic *dual
write*, the finish is the [outbox](outbox.md) (delivery to Kestra) +
`WebhookInbox` (receives the result exactly once) pair.

!!! info "No new SDK module, on purpose"
    The whole contract is "POST with a header" in both directions, and
    `HTTPClient` already brings retry, circuit breaker and `default_headers`.
    A `KestraClient` would only pay off if two services copied the same call.
    There is no new dependency either: not even Kestra's Python SDK, which
    would be an HTTP wrapper carrying its version bounds into your lock.

!!! check "Everything here was executed"
    Every command and every output on this page came from a real run against
    **Kestra 2.0.6 OSS** (`kestra/kestra:v2.0.6-slim`, digest
    `sha256:82f7c84f00fb883a6ff93c6d07e31f30eaabd0c84d229fb56a80edeeecd8dbba`)
    with a local FastAPI service, Python 3.12. Another Kestra version may
    answer differently — the [tenant section](#2-the-tenant-path-measured)
    exists because of that.

## 1. Start Kestra for testing

```bash
docker run -d --name lab-kestra -p 18080:8080 \
  --add-host=host.docker.internal:host-gateway \
  -e SECRET_SERVICE_TOKEN=dG9vbC1zZWNyZXQtMTIz \
  -e SECRET_WEBHOOK_KEY=aG9vay1rZXktYWJjMTIz \
  -e KESTRA_CONFIGURATION='
kestra:
  server:
    basic-auth:
      username: admin@example.com
      password: Kestra12345!
' \
  kestra/kestra:v2.0.6-slim server local
```

What each piece does:

- `--add-host=host.docker.internal:host-gateway` lets the flow (inside the
  container) reach your service, which runs on the host.
- `SECRET_<NAME>` becomes `{{ secret('<NAME>') }}` in the flow. The value is
  **base64** (`echo -n 'tool-secret-123' | base64`). That is how the
  protected route's token and the webhook key stay out of the YAML.
- `basic-auth` turns on API authentication; without credentials it answers
  `401` (measured in section 5).

Wait for the server to come up and check the version:

```bash
curl -s -u admin@example.com:Kestra12345! localhost:18080/api/v1/configs
```

```text
{"uuid":"2lrxrR7eSFQ8QtCWti1TDa","version":"2.0.6","edition":"OSS","commitId":"b59444f", ...
```

!!! tip "Pin the image version"
    The `latest-slim` tag points to a *nightly* (the
    `org.opencontainers.image.version` label of the image I pulled said
    `nightly`). Use `v2.0.6-slim`, or whichever you validate.

## 2. The tenant path (measured)

Kestra's documentation is inconsistent: some examples use
`/api/v1/main/...`, others `/api/v1/...`. Measured on 2.0.6, with Basic Auth:

| Call | With `/main` | Without `/main` |
| --- | --- | --- |
| `GET  /flows/search` | `200` | `200` |
| `POST /executions/{namespace}/{flow}` | `200` | `200` |
| `POST /executions/webhook/{namespace}/{flow}/{key}` | `200` | `200` |
| `GET  /outputs/executions/{id}` | `200` | **`400`** |
| `GET  /configs` | **`404`** | `200` |

The `400` says why, and it holds for any segment that is not `main`:

```text
{"type":"https://kestra.io/docs/api-reference/problems/bad-request","title":"Bad request","status":400,"detail":"Tenant must be 'main' for OSS version", ...
```

The `400` for `/api/v1/outputs/executions/<id>` is the same one a tenant other
than `main` gets, which suggests that, without the segment, `outputs` was
read as the tenant. Some routes tolerate the omission, others do not — so
**always use `/api/v1/main/...`** on tenant routes. The only measured
exception is `/api/v1/configs`, which only answered without `/main`.

## 3. The service

A single file with everything the next sections use: the tool route the flow
calls, the callback the flow sends when it finishes, and the order that
writes **the order and the event in the same transaction**.

```python title="kestra_service.py"
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
KESTRA_URL: str = os.environ.get("KESTRA_URL", "http://127.0.0.1:18080")
KESTRA_WEBHOOK_PATH: str = os.environ.get(
    "KESTRA_WEBHOOK_PATH",
    "/api/v1/main/executions/webhook/tempest/order_created/hook-key-abc123",
)

db: AsyncDatabaseManager = AsyncDatabaseManager("sqlite+aiosqlite:///./kestra_demo.db")
require_token = make_token_dependency(TOKEN_SECRET)
Session = Annotated[AsyncSession, Depends(db.session_dependency)]


class OrderModel(BaseModel):
    """Service order."""

    __tablename__ = "orders"

    reference: Mapped[str] = mapped_column()
    status: Mapped[str] = mapped_column(default="created")


class OutboxModel(BaseOutboxModel):
    """Events waiting to be delivered to Kestra."""

    __tablename__ = "outbox"


class WebhookEventModel(BaseWebhookEventModel):
    """Callbacks received from Kestra, deduplicated per execution."""

    __tablename__ = "webhook_events"


class OrderCreateSchema(BaseSchema):
    """Body of `POST /api/orders`."""

    reference: str


class EnrichRequestSchema(BaseSchema):
    """Body the flow sends to the tool route."""

    order_id: str
    source: str


class CallbackSchema(BaseSchema):
    """Body the flow sends when it finishes."""

    execution_id: str
    order_id: str
    enriched: dict[str, Any]


inbox: WebhookInbox[WebhookEventModel] = WebhookInbox(
    db, model=WebhookEventModel, backoff_base_seconds=1.0
)
kestra: HTTPClient = HTTPClient(base_url=KESTRA_URL, timeout=15.0)


async def publish(event: BaseOutboxModel) -> None:
    """Deliver an outbox event to Kestra's webhook.

    Args:
        event (BaseOutboxModel): The outbox row.

    Raises:
        httpx.HTTPStatusError: If Kestra does not answer 2xx; the relay schedules a retry.
    """
    response = await kestra.post(KESTRA_WEBHOOK_PATH, json=event.payload)
    response.raise_for_status()


async def apply_callback(event: WebhookEventModel, session: AsyncSession) -> None:
    """Apply Kestra's callback: mark the order as enriched.

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
    await kestra.aclose()
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
    """Tool route the flow calls, protected by `X-Token`."""
    return {"order_id": data.order_id, "caller": data.source, "tier": "gold"}


@app.post("/api/callbacks/kestra", dependencies=[Depends(require_token)])
async def kestra_callback(data: CallbackSchema) -> dict[str, bool]:
    """Record the callback in the inbox and answer 200, even for a repeat."""
    new_id = await inbox.record(
        "kestra", data.execution_id, "flow.finished", data.model_dump()
    )
    return {"recorded": new_id is not None}
```

Piece by piece:

- `make_token_dependency(TOKEN_SECRET)` compares the `X-Token` header with
  `hmac.compare_digest`. The tool route and the callback share the same
  dependency; `POST /api/orders` stays open because the service itself is
  the one calling it.
- `create_order` writes `OrderModel` and the outbox row in one transaction
  (`save_with_outbox`) — see the [outbox recipe](outbox.md).
- `publish` is the `publish(event)` adapter `OutboxRelay` requires: a single
  argument, and `raise_for_status()` so a non-2xx becomes a retry.
- `kestra_callback` writes to the `WebhookInbox` keyed by
  `(provider, external_id)` and answers `200` **even for a repeat** — the
  sender only needs to stop retrying. The `external_id` is Kestra's execution
  id.
- `apply_callback` runs inside the transaction that also marks the inbox row
  as processed.

Start the service (bound to `0.0.0.0` here because the caller is a container,
a separate origin):

```bash
uvicorn kestra_service:app --host 0.0.0.0 --port 18000
```

Check the tool route's protection, with and without the token:

```bash
curl -s -X POST localhost:18000/api/tools/enrich-order \
  -H 'X-Token: tool-secret-123' -H 'content-type: application/json' \
  -d '{"order_id":"1","source":"curl"}'
curl -s -X POST localhost:18000/api/tools/enrich-order \
  -H 'content-type: application/json' -d '{"order_id":"1","source":"curl"}' -w ' %{http_code}\n'
```

```text
{"order_id":"1","caller":"curl","tier":"gold"}
{"detail":"Invalid or missing token","code":"UNAUTHORIZED","details":{}} 401
```

## 4. The flows

Two flows, both calling the protected route with the token coming from
`secret(...)`. The first is started through the **execution API**, with an
input:

```yaml title="flows/report.yml"
id: report
namespace: tempest

inputs:
  - id: order_id
    type: STRING

tasks:
  - id: enrich
    type: io.kestra.plugin.core.http.Request
    uri: http://host.docker.internal:18000/api/tools/enrich-order
    method: POST
    contentType: application/json
    headers:
      X-Token: "{{ secret('SERVICE_TOKEN') }}"
    body: |
      {"order_id": "{{ inputs.order_id }}", "source": "kestra"}

outputs:
  - id: enriched
    type: STRING
    value: "{{ outputs.enrich.body }}"
```

The second is started by **webhook**; the body arrives in `trigger.body`, and
the trigger's `key` also comes from a secret. When it finishes it notifies
the service (`callback`), sending `execution.id` — the id the inbox uses to
deduplicate:

```yaml title="flows/order_created.yml"
id: order_created
namespace: tempest

tasks:
  - id: enrich
    type: io.kestra.plugin.core.http.Request
    uri: http://host.docker.internal:18000/api/tools/enrich-order
    method: POST
    contentType: application/json
    headers:
      X-Token: "{{ secret('SERVICE_TOKEN') }}"
    body: |
      {"order_id": "{{ trigger.body.order_id }}", "source": "kestra"}

  - id: callback
    type: io.kestra.plugin.core.http.Request
    uri: http://host.docker.internal:18000/api/callbacks/kestra
    method: POST
    contentType: application/json
    headers:
      X-Token: "{{ secret('SERVICE_TOKEN') }}"
    body: |
      {
        "execution_id": "{{ execution.id }}",
        "order_id": "{{ trigger.body.order_id }}",
        "enriched": {{ outputs.enrich.body }}
      }

outputs:
  - id: callback_status
    type: STRING
    value: "{{ outputs.callback.code }}"

triggers:
  - id: webhook
    type: io.kestra.plugin.core.trigger.Webhook
    key: "{{ secret('WEBHOOK_KEY') }}"
```

Import both (on 2.0.6 the response was `200`):

```bash
curl -s -u admin@example.com:Kestra12345! -X POST localhost:18080/api/v1/main/flows \
  -H 'Content-Type: application/x-yaml' --data-binary @flows/report.yml -o /dev/null -w '%{http_code}\n'
curl -s -u admin@example.com:Kestra12345! -X POST localhost:18080/api/v1/main/flows \
  -H 'Content-Type: application/x-yaml' --data-binary @flows/order_created.yml -o /dev/null -w '%{http_code}\n'
```

```text
200
200
```

## 5. Trigger from the service

```python title="kestra_trigger.py"
import asyncio
from base64 import b64encode
from typing import Any

from tempest_fastapi_sdk import HTTPClient, RetryPolicy

TENANT: str = "main"


def make_kestra_client(base_url: str, username: str, password: str) -> HTTPClient:
    """Build the Kestra client with Basic Auth, retry and circuit breaker.

    Args:
        base_url (str): Kestra root, without a trailing slash.
        username (str): Basic Auth user.
        password (str): Basic Auth password.

    Returns:
        HTTPClient: Client ready for the API and the webhooks.
    """
    credentials: str = b64encode(f"{username}:{password}".encode()).decode()
    return HTTPClient(
        base_url=base_url,
        timeout=30.0,
        retry_policy=RetryPolicy(max_attempts=3),
        default_headers={"Authorization": f"Basic {credentials}"},
    )


async def trigger_webhook(
    client: HTTPClient,
    namespace: str,
    flow_id: str,
    key: str,
    payload: dict[str, Any],
) -> dict[str, Any]:
    """Trigger a flow through its Webhook trigger.

    Args:
        client (HTTPClient): The Kestra client.
        namespace (str): Flow namespace.
        flow_id (str): Flow id.
        key (str): The trigger's `key`; it is the webhook's only check.
        payload (dict[str, Any]): JSON body, which the flow reads in `trigger.body`.

    Returns:
        dict[str, Any]: The created execution (`id`, `state`, ...).

    Raises:
        httpx.HTTPStatusError: If Kestra does not answer 2xx.
    """
    response = await client.post(
        f"/api/v1/{TENANT}/executions/webhook/{namespace}/{flow_id}/{key}",
        json=payload,
    )
    response.raise_for_status()
    return dict(response.json())


async def start_execution(
    client: HTTPClient,
    namespace: str,
    flow_id: str,
    inputs: dict[str, str],
    *,
    wait: bool = False,
) -> dict[str, Any]:
    """Create an execution through the API, with the flow's inputs.

    Args:
        client (HTTPClient): The Kestra client.
        namespace (str): Flow namespace.
        flow_id (str): Flow id.
        inputs (dict[str, str]): Flow inputs; sent as `multipart/form-data`.
        wait (bool): Hold the connection until the execution finishes.

    Returns:
        dict[str, Any]: The execution (`id`, `state`, ...).

    Raises:
        httpx.HTTPStatusError: If Kestra does not answer 2xx.
    """
    response = await client.post(
        f"/api/v1/{TENANT}/executions/{namespace}/{flow_id}",
        params={"wait": str(wait).lower()},
        files={name: (None, value) for name, value in inputs.items()},
    )
    response.raise_for_status()
    return dict(response.json())


async def read_outputs(client: HTTPClient, execution_id: str) -> dict[str, Any]:
    """Read the flow outputs of a finished execution.

    Args:
        client (HTTPClient): The Kestra client.
        execution_id (str): Execution id.

    Returns:
        dict[str, Any]: The outputs declared under `outputs:` in the flow.

    Raises:
        httpx.HTTPStatusError: If Kestra does not answer 2xx.
    """
    response = await client.get(f"/api/v1/{TENANT}/outputs/executions/{execution_id}")
    response.raise_for_status()
    return dict(response.json())


async def main() -> None:
    """Call the webhook and the API against a local Kestra and print the result."""
    client = make_kestra_client("http://127.0.0.1:18080", "admin@example.com", "Kestra12345!")
    try:
        created = await trigger_webhook(
            client, "tempest", "order_created", "hook-key-abc123", {"order_id": "demo-1"}
        )
        print("webhook:", created["id"], created["state"]["current"])

        execution = await start_execution(
            client, "tempest", "report", {"order_id": "demo-2"}, wait=True
        )
        print("api:", execution["id"], execution["state"]["current"])
        print("outputs:", await read_outputs(client, execution["id"]))
    finally:
        await client.aclose()


if __name__ == "__main__":
    asyncio.run(main())
```

```bash
python kestra_trigger.py
```

```text
webhook: 49vM41z1Hsqrd2r0umfjdl CREATED
api: 2EvSyvxiQWdVrzX3Co2MqN SUCCESS
outputs: {'enriched': '{"order_id":"demo-2","caller":"kestra","tier":"gold"}'}
```

What the measurement showed, and what changes how you write the client:

- **The webhook answers immediately**, with the freshly created execution
  (`state.current` = `CREATED`); the flow keeps running behind it. To learn
  the result, use the callback from section 6 — or `?wait=true` on the API.
- **The `key` is the webhook's only check.** A call without Basic Auth, with
  just the right URL, got `200`; with the wrong key it got `404`
  (`"detail":"Webhook not found"`). Treat the key as a secret (that is why it
  comes from `secret('WEBHOOK_KEY')`) and use a long one.
- **The execution API takes inputs as `multipart/form-data`.**
  `-d order_id=44` (urlencoded) got `415` with
  `Content Type [application/x-www-form-urlencoded] not allowed. Allowed types: [multipart/form-data]`.
  That is why `start_execution` uses httpx's `files={name: (None, value)}`,
  which forces `multipart` without attaching a file.
- **Without credentials the API answers `401`.**
- **`wait=true` holds the connection** and returns the finished execution
  (`SUCCESS`); without it the state comes back as `RUNNING`.
- **The flow outputs are not in the execution JSON** (the `outputs` field is
  absent on 2.0.6). They come from `GET /api/v1/main/outputs/executions/{id}`,
  which is what `read_outputs` calls.

## 6. Guaranteed delivery: outbox → Kestra, inbox ← Kestra

Create an order. The service answers `201` and does **not** wait for Kestra:

```bash
curl -s -X POST localhost:18000/api/orders -H 'content-type: application/json' \
  -d '{"reference":"PED-001"}'
sleep 1
curl -s localhost:18000/api/orders/a88f7992-d764-4415-b2da-a045d7ee6647
```

```text
{"id":"a88f7992-d764-4415-b2da-a045d7ee6647","status":"created"}
{"id":"a88f7992-d764-4415-b2da-a045d7ee6647","status":"enriched"}
```

In the service log, the full order — the relay delivered the event, the flow
called the tool route with the token and then the callback:

```text
INFO:     127.0.0.1:47757 - "POST /api/orders HTTP/1.1" 201 Created
INFO:     172.17.0.2:58240 - "POST /api/tools/enrich-order HTTP/1.1" 200 OK
INFO:     172.17.0.2:58254 - "POST /api/callbacks/kestra HTTP/1.1" 200 OK
```

### Kestra down

With the container frozen (`docker pause lab-kestra`), a new order answers
`201` all the same. Twenty-five seconds later, the outbox row is still
`pending` and the order `created`; unfrozen (`docker unpause lab-kestra`),
the row becomes `sent` and the order `enriched`:

```text
{"id":"906c4c47-21b4-486f-af8d-6e308d53b19d","status":"created"}
('orders.created', 'pending', 0, None)          # the new order's row, frozen, 25 s later
{"id":"906c4c47-21b4-486f-af8d-6e308d53b19d","status":"created"}
('orders.created', 'sent', 0)                   # after the unpause
{"id":"906c4c47-21b4-486f-af8d-6e308d53b19d","status":"enriched"}
```

!!! note "What this test measures and what it does not"
    `docker pause` freezes the process; the connection hangs instead of being
    refused, and at 25 s the relay had not yet recorded a failure
    (`attempts` still `0`). This test shows the event **waits, durable, in the
    table** and goes out when Kestra is back. The path with `attempts`
    climbing, `last_error` and exponential backoff is the one for fast
    failures, described in the [outbox recipe](outbox.md#failures-and-retry).

### The repeated callback

The inbox answers `200` and stores once. Two deliveries with the same
`execution_id`:

```text
{"recorded":true}
{"recorded":false}
```

```text
('kestra', 'dup-1', 'received')     # a single row, before the sweep processed it
```

!!! danger "It is at-least-once in both directions"
    The outbox may publish the same event twice (worker killed between the
    send and the `commit`), so the **flow** may run twice for the same order.
    Make the flow idempotent on `trigger.body.order_id`, or keep the order's
    idempotency key. On our side, the inbox already deduplicates the callback
    by `(provider, external_id)`.

## Tear the environment down

```bash
docker rm -f lab-kestra
```

## Recap

- **Service → Kestra:** `HTTPClient` + `POST /api/v1/main/executions/webhook/{ns}/{flow}/{key}`
  (immediate answer, the `key` is the only check) or
  `POST /api/v1/main/executions/{ns}/{flow}` with `multipart/form-data`
  (`?wait=true` to wait).
- **Tenant:** always use `/api/v1/main/...`; on 2.0.6 any other segment gets
  `400`, and omitting `/main` only works on some routes.
- **Kestra → service:** `http.Request` task with
  `X-Token: "{{ secret('...') }}"` against a route with
  `make_token_dependency`.
- **Guaranteed delivery:** `save_with_outbox` + `OutboxRelay` on the way out;
  `WebhookInbox.record` keyed by the execution id on the way back.
- No new module: all of this is `HTTPClient`, `make_token_dependency`,
  `OutboxRelay` and `WebhookInbox`.
