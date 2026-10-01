# Receiving WhatsApp messages (zap-api)

The [WhatsApp (zap-api)](zap.md) recipe covers the **outbound** side:
sending and getting a `202` back. This one covers the other side — the
gateway POSTing to your service when a message **arrives** and when one you
sent changes status.

By the end you will have a route that:

- answers `401` to every delivery that did not come from the gateway;
- tells an incoming message from a status callback by `event`;
- stores each message **once**, even while the gateway redelivers;
- groups the conversation by `chatKey`, not by `from`;
- downloads media through the gateway, the only place it exists;
- never regresses a status because of a late callback.

## Register the webhook on the gateway

Registration is a gateway admin operation, not something your service
does. One URL receives both kinds of event:

```bash
npm run webhook:set -- my-bot https://my-bot.test/webhooks/zap \
  --secret my-secret --events status,inbound
```

`--events` takes `status` (the delivery lifecycle of what **this** consumer
sent), `inbound` (every message that arrives) or both. Without the flag the
default is `status` only.

!!! danger "Without `--secret`, the gateway delivers unsigned"
    The gateway signs only when the webhook has a `secret` — without one it
    POSTs with no `x-zap-signature` header. That is why
    `make_zap_webhook_dependency` **refuses to be built** without a
    non-empty secret: with it, an unsigned delivery is always `401`.

## The complete receiver

```python
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import Depends, FastAPI

from tempest_fastapi_sdk import HTTPClient
from tempest_fastapi_sdk.integrations.messaging.zap import (
    ZapClient,
    ZapInboundMessage,
    ZapStatusCallback,
    ZapWebhookDelivery,
    ZapWebhookEvent,
    is_forward_transition,
    make_zap_webhook_dependency,
)

ZAP_BASE_URL: str = "http://127.0.0.1:3000"
ZAP_API_KEY: str = "<your key>"
ZAP_WEBHOOK_SECRET: str = "my-secret"

http: HTTPClient = HTTPClient(
    base_url=ZAP_BASE_URL,
    default_headers={"x-api-key": ZAP_API_KEY},
)
zap: ZapClient = ZapClient(http)
verify_zap = make_zap_webhook_dependency(secret=ZAP_WEBHOOK_SECRET)

processed_messages: set[str] = set()
conversations: dict[str, list[str]] = {}
delivery_status: dict[str, str] = {}


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    """Open the gateway client together with the service.

    Args:
        app (FastAPI): The application.

    Yields:
        None: While the service is up.
    """
    async with http:
        yield


app: FastAPI = FastAPI(lifespan=lifespan)


async def handle_inbound(message: ZapInboundMessage) -> str:
    """Store an incoming message, once.

    Args:
        message (ZapInboundMessage): The validated message.

    Returns:
        str: What happened to it.
    """
    if message.message_id in processed_messages:
        return "duplicate"
    processed_messages.add(message.message_id)

    if message.chat_key is None:
        return "no-chat-key"
    conversations.setdefault(message.chat_key, []).append(message.text or "")

    if message.media_type is None:
        return "stored"
    if message.media_url is None:
        return "media-lost"
    media: bytes = await zap.get_message_media(message.message_id)
    Path(f"{message.message_id}.bin").write_bytes(media)
    return "stored-with-media"


def handle_status(callback: ZapStatusCallback) -> str:
    """Apply a delivery status only when it moves the state forward.

    Args:
        callback (ZapStatusCallback): The validated callback.

    Returns:
        str: ``applied`` or ``stale``.
    """
    current: str | None = delivery_status.get(callback.id)
    if not is_forward_transition(current, callback.status):
        return "stale"
    delivery_status[callback.id] = callback.status
    return "applied"


@app.post("/webhooks/zap", include_in_schema=False)
async def receive_zap(
    delivery: ZapWebhookDelivery = Depends(verify_zap),
) -> dict[str, str]:
    """Receive the gateway's deliveries, inbound and status.

    Args:
        delivery (ZapWebhookDelivery): The verified, decoded delivery.

    Returns:
        dict[str, str]: Always 200, so the gateway stops redelivering.
    """
    if delivery.event is ZapWebhookEvent.MESSAGE_RECEIVED and delivery.inbound:
        return {"status": await handle_inbound(delivery.inbound)}
    if delivery.status is not None:
        return {"status": handle_status(delivery.status)}
    return {"status": "ignored", "event": delivery.event_name}
```

The in-memory `set` and `dict` stand in for your database so the example
fits on one page. Piece by piece.

### The signature

`make_zap_webhook_dependency(secret=...)` builds a `WebhookSignatureVerifier`
with what the gateway uses: header `x-zap-signature`, HMAC-SHA256 in hex,
prefix `sha256=`. It reads the **raw** body before any parsing and compares
with `hmac.compare_digest`.

The two classic mistakes become impossible: verifying over re-serialized
JSON (which changes whitespace and key order) and comparing with `==`. A
header without the `sha256=` prefix is also `401`, even with the right hex.

### Dispatching on `event`

The same URL receives `message.received` and
`message.{sent,delivered,read,failed}`, in different shapes. The dependency
hands back the validated body in the right field:

| `delivery.event` | Body in |
| --- | --- |
| `ZapWebhookEvent.MESSAGE_RECEIVED` | `delivery.inbound` (`ZapInboundMessage`) |
| `MESSAGE_SENT`, `MESSAGE_DELIVERED`, `MESSAGE_READ`, `MESSAGE_FAILED` | `delivery.status` (`ZapStatusCallback`) |
| `None` | none — see below |

`event` is set only when the matching body validated. `None` covers two
cases: an event this SDK version does not know, and a known event whose
body does not match the model. In both, `event_name` keeps the string as it
arrived and `payload` keeps the dict — log it and answer `200`.

!!! tip "Answer 2xx to what you ignore"
    The gateway treats any non-2xx as a failure and redelivers with backoff,
    up to `CALLBACK_MAX_ATTEMPTS` attempts (3 by default). A `422` for a new
    event protects nothing: it only produces attempts, and the gateway gives
    up on the delivery in the end.

!!! note "`delivery.event` is an enum; the schema field is a value"
    `ZapWebhookDelivery` is a dataclass, so `delivery.event is
    ZapWebhookEvent.MESSAGE_RECEIVED` works. Inside `ZapInboundMessage` and
    `ZapStatusCallback`, as in every `BaseSchema`, enum fields hold the
    **value** — compare with `==`.

### Idempotency by `messageId`

Deliveries go through the gateway's `callback_deliveries` queue, with
retry. The same message arrives more than once — when your response is
lost, or when the gateway restarts in the middle of a POST and puts the row
back in the queue. Key an incoming message on `message_id`, and a status
callback on `id` (the outbox row the send's `202` returned).

In production the `set` becomes a `UNIQUE` constraint on `message_id` and
the insert is the claim: whoever loses the race gets `IntegrityError` and
answers `200` without doing anything. See [Idempotency](idempotency.md).

### `chatKey`, not `from`

`from_` is the raw address: `<digits>@s.whatsapp.net`, a `@lid` or a
`@g.us` group. The same person can arrive through a number and later
through a LID — grouping by `from_` splits one conversation in two.

`chat_key` is the phone digits, already resolved by the gateway from the
LID. It is `None` for a group and for a LID the gateway could not map —
there is no number to link to, and the example just records the case.

### Media only through the gateway

The gateway downloads the media when the message arrives, and WhatsApp does
not serve it again later. `media_url` points at `GET /message/{messageId}/media`
on the gateway, and `ZapClient.get_message_media(message_id)` is the call
that reads those bytes with your `x-api-key`.

When `media_type` is set and `media_url` is `None`, the download failed or
exceeded `MEDIA_MAX_BYTES`. The media is lost — retrying does not help, and
the example returns `media-lost`.

### Out-of-order status

Every delivery has its own retry and backoff, so a `sent` whose first POST
failed can land after the `delivered`. And the gateway guards its **own**
row against regression but sends the callback anyway: a late `delivered`
after a `read` does reach you.

`is_forward_transition(current, new)` tells whether the new status moves
what you stored forward:

- the ladder is `queued < sending < sent < delivered < read`;
- repeating the current status, or stepping down the ladder, is not
  forward;
- `failed` is terminal — nothing overwrites it;
- `failed` only overwrites `queued`, `sending` and `sent`, because
  `delivered` and `read` already prove the message arrived.

`current` accepts a `str` because that is what a `BaseSchema` field (or a
column) holds.

## Measured

The receiver above ran under `uvicorn`, with a fake gateway serving media
on `127.0.0.1:3000`. Each delivery was built and signed by the gateway's
**own** `signPayload` (`src/utils/signature.ts`), with the body serialized
by `JSON.stringify`, as the callback worker does. In order:

| Delivery | Response |
| --- | --- |
| No signature header | `401 {"detail":"Invalid zap-api webhook signature"}` |
| Signed with another secret | `401` |
| The gateway README's inbound example | `200 {"status":"stored"}` |
| The same delivery again | `200 {"status":"duplicate"}` |
| Audio with `mediaUrl` | `200 {"status":"stored-with-media"}`, bytes written to disk |
| Audio with `mediaUrl: null` | `200 {"status":"media-lost"}` |
| Group (`chatKey: null`) | `200 {"status":"no-chat-key"}` |
| `message.delivered` | `200 {"status":"applied"}` |
| `message.sent` after the `delivered` | `200 {"status":"stale"}` |
| `message.read` | `200 {"status":"applied"}` |
| `message.delivered` after the `read` | `200 {"status":"stale"}` |
| `message.edited` (an event the SDK does not know) | `200 {"status":"ignored","event":"message.edited"}` |

## Testing without the gateway

Sign the body the way the gateway does and send it through the
`ASGITransport`. The standard library's `hmac` is enough:

```python
import hashlib
import hmac

import httpx
from fastapi import Depends, FastAPI

from tempest_fastapi_sdk.integrations.messaging.zap import (
    ZAP_WEBHOOK_SIGNATURE_HEADER,
    ZapWebhookDelivery,
    make_zap_webhook_dependency,
)

SECRET: str = "my-secret"
app: FastAPI = FastAPI()
verify_zap = make_zap_webhook_dependency(secret=SECRET)


@app.post("/webhooks/zap")
async def receive_zap(
    delivery: ZapWebhookDelivery = Depends(verify_zap),
) -> dict[str, str]:
    """Echo the received event.

    Args:
        delivery (ZapWebhookDelivery): The verified delivery.

    Returns:
        dict[str, str]: The event name.
    """
    return {"event": delivery.event_name}


async def test_signed_delivery_is_accepted() -> None:
    """A delivery signed the way the gateway signs passes."""
    body: bytes = b'{"event":"message.read","id":"x"}'
    digest: str = hmac.new(SECRET.encode(), body, hashlib.sha256).hexdigest()
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://t") as client:
        response = await client.post(
            "/webhooks/zap",
            content=body,
            headers={ZAP_WEBHOOK_SIGNATURE_HEADER: f"sha256={digest}"},
        )
    assert response.status_code == 200
    assert response.json() == {"event": "message.read"}
```

This test's body lacks a status callback's fields, so the delivery arrives
with `event=None` — and still answers `200`, which is the behavior described
above.

## Recap

- Register the webhook **with** `--secret`; without one the gateway does
  not sign.
- `make_zap_webhook_dependency(secret=...)` verifies the HMAC over the raw
  body and returns a `ZapWebhookDelivery`.
- Dispatch on `delivery.event`: `inbound` for an incoming message, `status`
  for a callback, `None` for what the SDK does not model — and answer `200`
  to all of them.
- Idempotency by `message_id` (inbound) and by `id` (status).
- Group by `chat_key`; `None` is a group or a LID without a number.
- Media only through `get_message_media`, and `media_url: None` with
  `media_type` means lost.
- `is_forward_transition` before storing any status.
