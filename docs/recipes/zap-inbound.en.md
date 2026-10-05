# Receiving WhatsApp messages (zap-api)

The [WhatsApp (zap-api)](zap.md) recipe covers the **outbound** side:
sending and getting a `202` back. This one covers the other side — the
gateway POSTing to your service when a message **arrives** and when one you
sent changes status.

You will build the receiver in four steps, each one a complete program that
runs:

1. **The ready route** — a route that receives the message and answers `401`
   to whatever did not come from the gateway, built by one line.
2. **The enums** — dispatching on event, media and address type without
   comparing against a loose string.
3. **The service layer** — `model` → `repository` → `service` →
   `controller` → `router`, storing each message **once**, replying to the
   sender and never regressing a status.
4. **The test** — deliveries signed the way the gateway signs, with no
   gateway running.

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

## Step 1 — The ready-made route

The ready route: one `POST`, the signature checked, the body validated into the
right model, dispatch by event, and a `200` answer.

```python title="zap_minimal.py" hl_lines="5 15"
from fastapi import FastAPI

from tempest_fastapi_sdk.integrations.messaging.zap import (
    ZapInboundMessage,
    make_zap_webhook_router,
)

app: FastAPI = FastAPI()


async def on_message(message: ZapInboundMessage) -> None:
    """Print the messages that arrive.

    Args:
        message (ZapInboundMessage): The delivery, signature already checked
            and body already validated.
    """
    print(f"{message.push_name}: {message.text}")


app.include_router(
    make_zap_webhook_router(secret="my-secret", on_inbound=on_message)
)
```

Three pieces:

- **`make_zap_webhook_router(secret=..., on_inbound=...)`** builds the whole
  route and returns an `APIRouter` like any other.
- **`on_message`** receives the delivery already validated: a
  `ZapInboundMessage` for `message.received`, a `ZapStatusCallback` for the
  four status events. The latter goes in `on_status`.
- **The answer** is `200` once the handler returns, with the event name echoed back —
  `{"ok": true, "event": "message.received"}`, a
  [`ZapWebhookAckSchema`](../../../reference/#tempest_fastapi_sdk.integrations.messaging.zap.router.ZapWebhookAckSchema).

The parameters the route takes:

| Parameter | Default | What it does |
| --- | --- | --- |
| `secret` | — | The secret registered on the gateway. Required without `verify`, and never empty. |
| `verify` | — | A dependency of your own, for when the secret is not enough: rotation, two secrets, a secret coming from your settings. |
| `on_inbound` | `None` | Called with the `ZapInboundMessage` of `message.received`. |
| `on_status` | `None` | Called with the `ZapStatusCallback` of `message.sent`, `.delivered`, `.read` and `.failed`. |
| `path` | `"/webhooks/zap"` | Where the route lands. |
| `tags` | `["zap"]` | The tags in the OpenAPI document. |
| `include_in_schema` | `True` | `False` hides the route from OpenAPI without unmounting it. |

!!! danger "Without `secret` and without `verify`, the route is not built"
    The gateway signs only when the webhook was registered with `--secret` —
    without one it POSTs with no `x-zap-signature` header. The factory
    **raises `ValueError`** in that case, instead of building a route that
    would accept any `POST`. And an unsigned delivery is `401`, always.

!!! warning "A handler that raises becomes a `500`, and the gateway redelivers"
    The ready route does **not** swallow your handler's exception: it
    propagates, the answer becomes `500`, and the gateway re-POSTs the same
    bytes with backoff (3 attempts by default). That is what you want for a
    transient failure — database down, lock held — and what you **do not** want
    for a body that will never get through: handle that inside the handler and
    answer `200`. An event with no handler, or an event the gateway invents
    later, is already a `200` with a `debug` log.

### Under the hood: the route the factory builds

???+ "The same route, written by hand"

    When you need the envelope — or when the route lives inside your service,
    with its own controller and dependencies (step 3) — the manual route is
    exactly this:

    ```python title="zap_manual.py" hl_lines="9 14"
    from fastapi import Depends, FastAPI

    from tempest_fastapi_sdk.integrations.messaging.zap import (
        ZapWebhookDelivery,
        make_zap_webhook_dependency,
    )

    app: FastAPI = FastAPI()
    verify_zap = make_zap_webhook_dependency(secret="my-secret")


    @app.post("/webhooks/zap", include_in_schema=False)
    async def receive_zap(
        delivery: ZapWebhookDelivery = Depends(verify_zap),
    ) -> dict[str, str]:
        """Receive every gateway delivery and print the incoming messages.

        Args:
            delivery (ZapWebhookDelivery): The delivery, signature already
                checked and body already validated.

        Returns:
            dict[str, str]: Always ``200``, so the gateway does not redeliver.
        """
        if delivery.inbound is not None:
            print(f"{delivery.inbound.push_name}: {delivery.inbound.text}")
        return {"status": "ok"}
    ```

    The difference is in the body: the factory dispatches on `event` and
    answers on its own, with the event name echoed. Yours does whatever you
    want after receiving the `ZapWebhookDelivery` — and it can still use
    `Depends(verify_zap)`, which is the same dependency the factory builds for
    you.

### What the dependency handles for you

The signature travels in the `x-zap-signature` header, HMAC-SHA256 in hex
with a `sha256=` prefix, computed over the **raw** body before any parsing
and compared with `hmac.compare_digest`. The two classic mistakes become
impossible: verifying over re-serialized JSON (which changes whitespace and
key order) and comparing with `==`. A header without the `sha256=` prefix is
also `401`, even with the right hex.

!!! tip "Answer 2xx to everything that passed the signature"
    The gateway treats any non-2xx answer as a failure and redelivers with
    backoff, up to `CALLBACK_MAX_ATTEMPTS` attempts (3 by default). A `422`
    for a new event protects nothing: it only produces attempts, and the
    gateway gives up on the delivery in the end. That is why the route does
    **not** fail on a body it does not recognize — it answers `200` with the
    event name and logs at `debug` (see step 2).

To see it work without the gateway, sign the body the way the gateway does
(HMAC-SHA256 of the raw body, hex, `sha256=` prefix) and send it through the
`TestClient`. The body is the inbound example from the gateway's README:

```python title="simulate.py"
import hashlib
import hmac

from fastapi.testclient import TestClient

from zap_minimal import app

SECRET: str = "my-secret"
BODY: bytes = (
    '{"event":"message.received","messageId":"ABCD1234",'
    '"from":"5511999999999@s.whatsapp.net","chatKey":"5511999999999",'
    '"pushName":"Fulano","text":"Hello!","mediaType":null,"mediaUrl":null,'
    '"timestamp":"2026-04-21T18:30:00.000Z"}'
).encode()


def sign(body: bytes) -> str:
    """Sign the body the way the gateway does.

    Args:
        body (bytes): The exact bytes of the ``POST``.

    Returns:
        str: The ``x-zap-signature`` header value.
    """
    digest: str = hmac.new(SECRET.encode(), body, hashlib.sha256).hexdigest()
    return f"sha256={digest}"


client: TestClient = TestClient(app)
signed = client.post(
    "/webhooks/zap",
    content=BODY,
    headers={"x-zap-signature": sign(BODY)},
)
print(signed.status_code, signed.json())
unsigned = client.post("/webhooks/zap", content=BODY)
print(unsigned.status_code, unsigned.json())
```

```console
$ python simulate.py
Fulano: Hello!
200 {'ok': True, 'event': 'message.received'}
401 {'detail': 'Invalid zap-api webhook signature'}
```

## Step 2 — Dispatch by type with the enums

Every value the gateway sends as a string has an enum in the SDK. They are
all `StrEnum`: a member **equals** its wire string, so code that already
compares against `"audio"` keeps working — the enum adds autocompletion, type
checking and one place to read what exists.

| Enum | Where it shows up | Values |
| --- | --- | --- |
| `ZapWebhookEvent` | `delivery.event` | `MESSAGE_RECEIVED`, `MESSAGE_SENT`, `MESSAGE_DELIVERED`, `MESSAGE_READ`, `MESSAGE_FAILED` |
| `ZapInboundMediaType` | `message.media_type` | `IMAGE`, `VIDEO`, `AUDIO`, `DOCUMENT`, `STICKER` (`None` is plain text) |
| `ZapJidServer` | `message.from_server` | `USER`, `LID`, `GROUP`, `BROADCAST`, `NEWSLETTER` and five more from Baileys |
| `ZapOutboundKind` | `callback.kind` | `TEXT`, `IMAGE`, `VIDEO`, `AUDIO`, `DOCUMENT`, `REACTION` |
| `AcceptedResponseStatus` | `callback.status` | `SENT`, `DELIVERED`, `READ`, `FAILED` (plus `QUEUED` and `SENDING`, which never reach the webhook) |

Step 1 dispatched for you; here the route is yours again, because `event` is
what picks the path and it lives on `ZapWebhookDelivery` — the factory hands
the handler only the body (`ZapInboundMessage` or `ZapStatusCallback`), already
opened on the right model. To use the factory with these functions, wrap each
in an `async def ... -> None` handler and pass it as `on_inbound` /
`on_status`: they are synchronous and return `str`, and the factory awaits the
handler.

```python title="zap_dispatch.py" hl_lines="27 29 31 34 36 51 53"
from fastapi import Depends, FastAPI

from tempest_fastapi_sdk.integrations.messaging.zap import (
    AcceptedResponseStatus,
    ZapInboundMediaType,
    ZapInboundMessage,
    ZapJidServer,
    ZapOutboundKind,
    ZapStatusCallback,
    ZapWebhookDelivery,
    make_zap_webhook_dependency,
)

app: FastAPI = FastAPI()
verify_zap = make_zap_webhook_dependency(secret="my-secret")


def describe_inbound(message: ZapInboundMessage) -> str:
    """Decide what to do with an incoming message, looking only at enums.

    Args:
        message (ZapInboundMessage): The validated message.

    Returns:
        str: A label for the chosen path.
    """
    if message.from_server is ZapJidServer.BROADCAST:
        return "ignore: status story"
    if message.from_server is ZapJidServer.GROUP:
        return f"group: {message.text}"
    match message.media_type:
        case None:
            return f"text: {message.text}"
        case ZapInboundMediaType.AUDIO:
            return "audio: transcribe"
        case ZapInboundMediaType.IMAGE | ZapInboundMediaType.VIDEO:
            return f"visual, caption: {message.text}"
        case _:
            return f"file: {message.media_type}"


def describe_status(callback: ZapStatusCallback) -> str:
    """Decide what to do with a send status, looking only at enums.

    Args:
        callback (ZapStatusCallback): The validated status.

    Returns:
        str: A label for the chosen path.
    """
    if callback.status == AcceptedResponseStatus.FAILED:
        return f"failed: {callback.error}"
    if callback.kind == ZapOutboundKind.REACTION:
        return "reaction delivered: nothing to show"
    return f"{callback.kind} is now {callback.status}"


@app.post("/webhooks/zap", include_in_schema=False)
async def receive_zap(
    delivery: ZapWebhookDelivery = Depends(verify_zap),
) -> dict[str, str]:
    """Dispatch each delivery to the handler for its kind.

    Args:
        delivery (ZapWebhookDelivery): The verified delivery.

    Returns:
        dict[str, str]: The chosen path, or ``ignored`` for an event the
        SDK does not model.
    """
    if delivery.inbound is not None:
        return {"handled": describe_inbound(delivery.inbound)}
    if delivery.status is not None:
        return {"handled": describe_status(delivery.status)}
    return {"handled": "ignored", "event": delivery.event_name}
```

Sending one delivery of each kind, signed as in step 1:

```python title="simulate_dispatch.py"
import hashlib
import hmac
import json
from typing import Any

from fastapi.testclient import TestClient

from zap_dispatch import app

SECRET: str = "my-secret"
WHEN: str = "2026-04-21T18:30:00.000Z"
DELIVERIES: list[dict[str, Any]] = [
    {
        "event": "message.received",
        "messageId": "M1",
        "text": "Hello!",
        "from": "5511999999999@s.whatsapp.net",
        "timestamp": WHEN,
    },
    {
        "event": "message.received",
        "messageId": "M2",
        "mediaType": "audio",
        "from": "123456789012345@lid",
        "timestamp": WHEN,
    },
    {
        "event": "message.received",
        "messageId": "M3",
        "text": "good morning",
        "from": "120363000000000000@g.us",
        "timestamp": WHEN,
    },
    {
        "event": "message.received",
        "messageId": "M4",
        "mediaType": "image",
        "from": "status@broadcast",
        "timestamp": WHEN,
    },
    {
        "event": "message.read",
        "id": "out-1",
        "consumer": "bot",
        "kind": "text",
        "to": "5511999999999",
        "status": "read",
        "timestamp": WHEN,
    },
    {
        "event": "message.failed",
        "id": "out-2",
        "consumer": "bot",
        "kind": "image",
        "to": "5511999999999",
        "status": "failed",
        "error": "number not on WhatsApp",
        "timestamp": WHEN,
    },
    {"event": "message.edited", "messageId": "M1", "timestamp": WHEN},
]


def sign(body: bytes) -> str:
    """Sign the body the way the gateway does.

    Args:
        body (bytes): The exact bytes of the ``POST``.

    Returns:
        str: The ``x-zap-signature`` header value.
    """
    digest: str = hmac.new(SECRET.encode(), body, hashlib.sha256).hexdigest()
    return f"sha256={digest}"


client: TestClient = TestClient(app)
for delivery in DELIVERIES:
    body: bytes = json.dumps(delivery).encode()
    response = client.post(
        "/webhooks/zap",
        content=body,
        headers={"x-zap-signature": sign(body)},
    )
    print(f"{delivery['event']:<17} {response.status_code} {response.json()}")
```

```console
$ python simulate_dispatch.py
message.received  200 {'handled': 'text: Hello!'}
message.received  200 {'handled': 'audio: transcribe'}
message.received  200 {'handled': 'group: good morning'}
message.received  200 {'handled': 'ignore: status story'}
message.read      200 {'handled': 'text is now read'}
message.failed    200 {'handled': 'failed: number not on WhatsApp'}
message.edited    200 {'handled': 'ignored', 'event': 'message.edited'}
```

Let's go piece by piece.

### Which body arrived: `inbound`, `status` or neither

The same URL receives `message.received` and
`message.{sent,delivered,read,failed}`, with different shapes. The dependency
already returns the validated body in the right field:

| `delivery.event` | Body in |
| --- | --- |
| `ZapWebhookEvent.MESSAGE_RECEIVED` | `delivery.inbound` (`ZapInboundMessage`) |
| `MESSAGE_SENT`, `MESSAGE_DELIVERED`, `MESSAGE_READ`, `MESSAGE_FAILED` | `delivery.status` (`ZapStatusCallback`) |
| `None` | neither — log `event_name` and `payload` and answer `200` |

`event` is set only when the matching body validated, so asking
`delivery.inbound is not None` and `delivery.status is not None` is the
complete dispatch — and it is the form the type checker understands, because
it narrows the `Optional` for you. `None` covers an event this SDK version
does not know (the `message.edited` above) and a known event whose body does
not match the model.

### `from_server`: what kind of address wrote

`from_` is the raw JID, `<user>@<server>`, and the server says whether it is
a person (`s.whatsapp.net` or `lid`), a group (`g.us`), a status story
(`status@broadcast`) or a channel (`newsletter`). `message.from_server` reads
that part and returns the `ZapJidServer` member — `None` when there is no
`@` or the server is unknown.

!!! warning "The gateway does not filter on the JID's server"
    The gateway's `publishMessage` (`src/services/whatsapp.service.ts`,
    commit `d0477d8`) forwards every `notify` message with content, without
    looking at the `remoteJid`'s server — read in the code, not measured on a
    live session. So a status story (`status@broadcast`) or a channel post
    can arrive as `message.received`, and replying to it, or storing it as a
    conversation, is almost never what you want. Filter on `from_server`.

### `is` for the envelope, `==` for the body

`ZapWebhookDelivery` is a dataclass, so `delivery.event` holds the member
itself and `delivery.event is ZapWebhookEvent.MESSAGE_RECEIVED` works.
`from_server` returns the member too. Inside `ZapInboundMessage` and
`ZapStatusCallback`, as in every `BaseSchema`, enum fields hold the
**value** — compare with `==` (or use `match`, which compares with `==`).

`callback.kind` is `str` on purpose, not `ZapOutboundKind`: the gateway
declares it as `string`, and a send kind it gains later must not turn a
message's status into an `event=None` delivery. Compare it with the enum
anyway — `callback.kind == ZapOutboundKind.REACTION`.

## Step 3 — The service layer

So far the rules live in the route. In a real service they go into layers,
each with a single job:

| Layer | Job here |
| --- | --- |
| **model** | the tables: incoming message (`UNIQUE` on `message_id`) and send status (`UNIQUE` on `outbound_id`) |
| **repository** | the SDK's `BaseRepository` — `add`, `get_or_none`, `update` |
| **service** | the rules: filter the sender, download media, store once, reply, apply status only forward |
| **controller** | pick the service method by the body the delivery carries |
| **router** | receive the `POST`, verify through the dependency and delegate — one line |

The file below holds all of them, in order, so the example runs. The
`fake_gateway` stands in for the gateway through an `httpx.MockTransport`:
it accepts the `send-text` (answering `deduped=True` when the
`idempotency-key` repeats, like the real gateway) and serves 2 KiB of media.

```python title="zap_service.py" hl_lines="64 76 138 157 162 179 284"
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

import httpx
from fastapi import APIRouter, Depends, FastAPI, Request
from sqlalchemy import String
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import Mapped, mapped_column

from tempest_fastapi_sdk import (
    AsyncDatabaseManager,
    BaseModel,
    BaseRepository,
    BaseStrEnum,
    HTTPClient,
)
from tempest_fastapi_sdk.exceptions import ConflictException
from tempest_fastapi_sdk.integrations.messaging.zap import (
    SendTextRequest,
    ZapClient,
    ZapInboundMessage,
    ZapJidServer,
    ZapStatusCallback,
    ZapWebhookDelivery,
    is_forward_transition,
    make_zap_webhook_dependency,
)
from tempest_fastapi_sdk.schemas import BaseSchema


SENT_KEYS: set[str] = set()


def fake_gateway(request: httpx.Request) -> httpx.Response:
    """Stand in for the gateway: accept the send and serve the media.

    Args:
        request (httpx.Request): The call the ``ZapClient`` made.

    Returns:
        httpx.Response: What the gateway would answer — ``deduped=True``
        when the ``idempotency-key`` was already used.
    """
    if request.url.path == "/message/send-text":
        key: str = request.headers["idempotency-key"]
        deduped: bool = key in SENT_KEYS
        SENT_KEYS.add(key)
        print(f"  gateway: send-text key={key} deduped={deduped}")
        return httpx.Response(
            202, json={"id": "out-1", "status": "queued", "deduped": deduped}
        )
    return httpx.Response(200, content=b"\x00" * 2048)


db: AsyncDatabaseManager = AsyncDatabaseManager("sqlite+aiosqlite:///:memory:")
verify_zap = make_zap_webhook_dependency(secret="my-secret")


class ZapMessageModel(BaseModel):
    """An incoming message, stored exactly once."""

    __tablename__ = "zap_messages"

    message_id: Mapped[str] = mapped_column(String(128), unique=True)
    chat_key: Mapped[str | None] = mapped_column(String(32), default=None)
    text: Mapped[str | None] = mapped_column(default=None)
    media_type: Mapped[str | None] = mapped_column(String(16), default=None)
    media_size: Mapped[int | None] = mapped_column(default=None)


class ZapDeliveryModel(BaseModel):
    """The last known status of a sent message."""

    __tablename__ = "zap_deliveries"

    outbound_id: Mapped[str] = mapped_column(String(64), unique=True)
    status: Mapped[str] = mapped_column(String(16))


class ZapOutcome(BaseStrEnum):
    """What the service did with a delivery — the route's answer."""

    STORED = "stored"
    DUPLICATE = "duplicate"
    MEDIA_LOST = "media-lost"
    SKIPPED = "skipped"
    APPLIED = "applied"
    STALE = "stale"
    IGNORED = "ignored"


class ZapWebhookResponseSchema(BaseSchema):
    """Body of the answer to the gateway.

    Attributes:
        outcome (ZapOutcome): What happened to the delivery.
    """

    outcome: ZapOutcome


class ZapInboxService:
    """WhatsApp business rules: store, reply and track sends."""

    def __init__(
        self,
        messages: BaseRepository[ZapMessageModel],
        deliveries: BaseRepository[ZapDeliveryModel],
        client: ZapClient,
    ) -> None:
        """Store the collaborators.

        Args:
            messages (BaseRepository[ZapMessageModel]): Incoming messages.
            deliveries (BaseRepository[ZapDeliveryModel]): Send statuses.
            client (ZapClient): The gateway client.
        """
        self.messages: BaseRepository[ZapMessageModel] = messages
        self.deliveries: BaseRepository[ZapDeliveryModel] = deliveries
        self.client: ZapClient = client

    async def receive(self, message: ZapInboundMessage) -> ZapOutcome:
        """Store an incoming message and reply to the sender.

        The ``UNIQUE`` on ``message_id`` is the claim: the gateway's
        redelivery hits it and becomes ``duplicate``. The reply goes out
        on both paths, with an ``idempotency_key`` derived from
        ``message_id``: if the send failed on the first delivery, the
        redelivery completes it; if it did not, the gateway answers with
        the original row (``deduped=True``) instead of sending twice.

        Args:
            message (ZapInboundMessage): The validated message.

        Returns:
            ZapOutcome: What happened to it.
        """
        if message.from_server not in (ZapJidServer.USER, ZapJidServer.LID):
            return ZapOutcome.SKIPPED
        if message.media_type is not None and message.media_url is None:
            return ZapOutcome.MEDIA_LOST
        media_size: int | None = None
        if message.media_type is not None:
            media: bytes = await self.client.get_message_media(message.message_id)
            media_size = len(media)
        stored: bool = True
        try:
            await self.messages.add(
                ZapMessageModel(
                    message_id=message.message_id,
                    chat_key=message.chat_key,
                    text=message.text,
                    media_type=message.media_type,
                    media_size=media_size,
                )
            )
        except ConflictException:
            stored = False
        if message.chat_key is not None:
            await self.client.send_text(
                body=SendTextRequest(to=message.chat_key, text="Got it, thanks!"),
                idempotency_key=f"reply-{message.message_id}",
            )
        return ZapOutcome.STORED if stored else ZapOutcome.DUPLICATE

    async def track(self, callback: ZapStatusCallback) -> ZapOutcome:
        """Apply a send status only when it advances the stored one.

        Args:
            callback (ZapStatusCallback): The validated status.

        Returns:
            ZapOutcome: ``applied`` or ``stale``.
        """
        row: ZapDeliveryModel | None = await self.deliveries.get_or_none(
            filters={"outbound_id": callback.id}
        )
        current: str | None = row.status if row is not None else None
        if not is_forward_transition(current, callback.status):
            return ZapOutcome.STALE
        if row is None:
            await self.deliveries.add(
                ZapDeliveryModel(outbound_id=callback.id, status=callback.status)
            )
        else:
            row.status = callback.status
            await self.deliveries.update(row)
        return ZapOutcome.APPLIED


class ZapWebhookController:
    """Route a gateway delivery to the right service method."""

    def __init__(self, service: ZapInboxService) -> None:
        """Store the service.

        Args:
            service (ZapInboxService): The business rules.
        """
        self.service: ZapInboxService = service

    async def handle(self, delivery: ZapWebhookDelivery) -> ZapWebhookResponseSchema:
        """Dispatch on the body the delivery carries.

        Args:
            delivery (ZapWebhookDelivery): The verified delivery.

        Returns:
            ZapWebhookResponseSchema: What happened, always with ``200``.
        """
        if delivery.inbound is not None:
            outcome: ZapOutcome = await self.service.receive(delivery.inbound)
        elif delivery.status is not None:
            outcome = await self.service.track(delivery.status)
        else:
            outcome = ZapOutcome.IGNORED
        return ZapWebhookResponseSchema(outcome=outcome)


def get_zap_client(request: Request) -> ZapClient:
    """Return the gateway client the lifespan opened.

    Args:
        request (Request): The current request.

    Returns:
        ZapClient: The client the application shares.
    """
    client: ZapClient = request.app.state.zap
    return client


def get_zap_inbox_service(
    session: AsyncSession = Depends(db.session_dependency),
    client: ZapClient = Depends(get_zap_client),
) -> ZapInboxService:
    """Build the service over the request's session.

    Args:
        session (AsyncSession): The request's session.
        client (ZapClient): The gateway client.

    Returns:
        ZapInboxService: The service.
    """
    return ZapInboxService(
        messages=BaseRepository(session, model=ZapMessageModel),
        deliveries=BaseRepository(session, model=ZapDeliveryModel),
        client=client,
    )


def get_zap_webhook_controller(
    service: ZapInboxService = Depends(get_zap_inbox_service),
) -> ZapWebhookController:
    """Build the controller over the service.

    Args:
        service (ZapInboxService): The request's service.

    Returns:
        ZapWebhookController: The controller.
    """
    return ZapWebhookController(service)


router: APIRouter = APIRouter(prefix="/webhooks", tags=["webhooks"])


@router.post("/zap", include_in_schema=False)
async def receive_zap(
    delivery: ZapWebhookDelivery = Depends(verify_zap),
    controller: ZapWebhookController = Depends(get_zap_webhook_controller),
) -> ZapWebhookResponseSchema:
    """Receive the gateway's deliveries, inbound and status.

    Args:
        delivery (ZapWebhookDelivery): The verified delivery.
        controller (ZapWebhookController): The request's controller.

    Returns:
        ZapWebhookResponseSchema: What happened to the delivery.
    """
    return await controller.handle(delivery)


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    """Create the tables and open the gateway client with the service.

    Args:
        app (FastAPI): The application.

    Yields:
        None: While the service is up.
    """
    await db.create_tables()
    async with HTTPClient(
        base_url="http://127.0.0.1:3000",
        default_headers={"x-api-key": "<your key>"},
        transport=httpx.MockTransport(fake_gateway),
    ) as http:
        app.state.zap = ZapClient(http)
        yield
    await db.disconnect()


app: FastAPI = FastAPI(lifespan=lifespan)
app.include_router(router)
```

Running a sequence of deliveries against it:

```python title="run_service.py"
from fastapi.testclient import TestClient

from test_zap_service import inbound, post, status
from zap_service import app

USER: str = "5511999999999@s.whatsapp.net"

with TestClient(app) as client:
    for label, payload in [
        ("text", inbound("A1", USER, chatKey="5511999999999", text="Hi")),
        ("same delivery", inbound("A1", USER, chatKey="5511999999999", text="Hi")),
        (
            "audio",
            inbound(
                "A2",
                USER,
                chatKey="5511999999999",
                mediaType="audio",
                mediaUrl="/message/A2/media",
            ),
        ),
        ("lost audio", inbound("A3", USER, mediaType="audio")),
        ("group", inbound("A4", "120363000000000000@g.us", text="good morning")),
        ("delivered", status("out-1", "delivered")),
        ("late sent", status("out-1", "sent")),
        ("read", status("out-1", "read")),
        ("new event", {"event": "message.edited", "messageId": "A1"}),
    ]:
        response = post(client, payload)
        print(f"{label:<14} {response.status_code} {response.json()}")
```

```console
$ python run_service.py
  gateway: send-text key=reply-A1 deduped=False
text           200 {'outcome': 'stored'}
  gateway: send-text key=reply-A1 deduped=True
same delivery  200 {'outcome': 'duplicate'}
  gateway: send-text key=reply-A2 deduped=False
audio          200 {'outcome': 'stored'}
lost audio     200 {'outcome': 'media-lost'}
group          200 {'outcome': 'skipped'}
delivered      200 {'outcome': 'applied'}
late sent      200 {'outcome': 'stale'}
read           200 {'outcome': 'applied'}
new event      200 {'outcome': 'ignored'}
```

`run_service.py` reuses the `inbound`, `status` and `post` helpers from the
step 4 test. The second delivery's `ConflictException` also shows up in the
log, as a `WARNING` from `BaseRepository`
(`IntegrityError on ZapMessageModel.add: unique violation; table=zap_messages;
columns=message_id; ...`) — that is the claim working, not an error.

Now each layer.

### The service: all the rules in one place

**Idempotency by `messageId`.** Deliveries go through the gateway's
`callback_deliveries` queue, with retries: the same message arrives more than
once when your answer is lost, or when the gateway restarts in the middle of
a `POST`. The `UNIQUE` on `message_id` is the claim — the second insert hits
it, `BaseRepository.add` raises `ConflictException`, and the service returns
`duplicate`. No in-memory `set`, no race between replicas. See
[Idempotency](idempotency.md) for the same reasoning applied to your own
routes.

**The reply goes out on both paths.** If the service replied only on
`stored`, a send that failed after the insert would never be retried: the
redelivery would land on `duplicate`. So the reply always goes out, with
`idempotency_key=f"reply-{message_id}"` — on the redelivery the gateway
answers with the original row and `deduped=True` instead of sending twice
(the second `gateway:` line of the output above).

**`chatKey`, not `from`.** `from_` is the raw address, and the same person
can arrive through a number and later through a LID — grouping by `from_`
splits one conversation in two. `chat_key` is the number's digits, already
resolved by the gateway from the LID; it is also the `to` that `send_text`
accepts. It is `None` for a group, a broadcast, a channel and a LID the
gateway could not map — then there is nobody to reply to, and the service
only stores.

**Media only through the gateway.** The gateway downloads the media when the
message arrives, and WhatsApp does not serve it again later. `media_url`
points at `GET /message/{messageId}/media` on the gateway, and
`ZapClient.get_message_media(message_id)` reads those bytes with your
`x-api-key`. When `media_type` is set and `media_url` is `None`, the download
failed or exceeded `MEDIA_MAX_BYTES`: the media is lost, retrying does not
help, and the service returns `media-lost`. The download comes **before** the
insert: if it fails, the route answers an error, the gateway redelivers, and
the message has not been claimed yet.

**Out-of-order statuses.** Each delivery has its own retry and backoff, so a
`sent` whose first `POST` failed can land after the `delivered`. And the
gateway protects its **own** row against regression but sends the callback
anyway: a late `delivered` after a `read` reaches you.
`is_forward_transition(current, new)` tells whether the new status advances
what you stored:

- the ladder is `queued < sending < sent < delivered < read`;
- repeating the current status, or stepping down the ladder, is not forward;
- `failed` is terminal — nothing overwrites it;
- `failed` overwrites only `queued`, `sending` and `sent`, because
  `delivered` and `read` already prove the message arrived.

`current` accepts `str` because that is what the column stores. Key on the
callback's `id` — the outbox row the send's `202` answered.

### The controller: pick the path, nothing else

`ZapWebhookController.handle` looks at which body the delivery carries and
calls `receive` or `track`. It opens no session, decides no rule, knows
nothing about the gateway. If tomorrow the same event must trigger two
things (store and notify another service), this is where both calls meet.

### The dependencies and the router

`get_zap_inbox_service` builds the service over the request's session
(`db.session_dependency`) and over the `ZapClient` the `lifespan` opened and
kept in `app.state`; `get_zap_webhook_controller` builds the controller over
the service. The router receives both through `Depends` and is left with a
single line, `return await controller.handle(delivery)`.

!!! note "Why the `ZapClient` is born in the `lifespan`"
    A closed `HTTPClient` does not reopen. Created at module level, it
    survives a single `lifespan`: the suite's second `TestClient(app)` finds
    the client closed and fails with `RuntimeError: Cannot send a request, as
    the client has been closed`. Created inside the `lifespan`, every
    start-up of the application gets its own.

### Where each piece lives in your service

The single file is there so the example runs. In the Tempest service layout,
each class goes to its layer:

```text
src/
├── api/
│   ├── dependencies/
│   │   ├── controllers.py   # get_zap_webhook_controller
│   │   └── services.py      # get_zap_client, get_zap_inbox_service
│   └── routers/
│       └── webhooks.py      # router + receive_zap
├── controllers/
│   └── zap_webhook.py       # ZapWebhookController
├── services/
│   └── zap_inbox.py         # ZapInboxService
├── schemas/
│   └── zap.py               # ZapOutcome, ZapWebhookResponseSchema
└── db/
    └── models/
        └── zap.py           # ZapMessageModel, ZapDeliveryModel
```

`verify_zap` sits next to the other authentication dependencies
(`api/dependencies/auth.py`), reading the secret from settings.

## Step 4 — Testing without the gateway

Sign the body the way the gateway does and send it through the `TestClient`.
The fixture opens the `TestClient` as a context manager so the `lifespan`
runs — without it neither `create_tables` nor the `ZapClient` run, and the
first delivery fails with
`AttributeError: 'State' object has no attribute 'zap'`.

```python title="test_zap_service.py"
import hashlib
import hmac
import json
from collections.abc import Iterator
from typing import Any

import pytest
from fastapi.testclient import TestClient

from zap_service import app

SECRET: str = "my-secret"
WHEN: str = "2026-04-21T18:30:00.000Z"


def inbound(message_id: str, sender: str, **fields: Any) -> dict[str, Any]:
    """Build a ``message.received`` body the way the gateway sends it.

    Args:
        message_id (str): The ``messageId``.
        sender (str): The raw JID in ``from``.
        **fields (Any): Extra fields (``text``, ``chatKey``, ``mediaType``…).

    Returns:
        dict[str, Any]: The body, ready to sign.
    """
    return {
        "event": "message.received",
        "messageId": message_id,
        "from": sender,
        "timestamp": WHEN,
        **fields,
    }


def status(outbound_id: str, value: str) -> dict[str, Any]:
    """Build a status callback body the way the gateway sends it.

    Args:
        outbound_id (str): The ``id`` the send's ``202`` answered.
        value (str): ``sent``, ``delivered``, ``read`` or ``failed``.

    Returns:
        dict[str, Any]: The body, ready to sign.
    """
    return {
        "event": f"message.{value}",
        "id": outbound_id,
        "consumer": "bot",
        "to": "5511999999999",
        "kind": "text",
        "status": value,
        "timestamp": WHEN,
    }


def post(client: TestClient, payload: dict[str, Any], secret: str = SECRET) -> Any:
    """Sign and deliver a body, like the gateway's callback worker.

    Args:
        client (TestClient): The application's client.
        payload (dict[str, Any]): The body.
        secret (str): The secret to sign with.

    Returns:
        Any: The route's response.
    """
    body: bytes = json.dumps(payload).encode()
    digest: str = hmac.new(secret.encode(), body, hashlib.sha256).hexdigest()
    return client.post(
        "/webhooks/zap",
        content=body,
        headers={"x-zap-signature": f"sha256={digest}"},
    )


@pytest.fixture
def client() -> Iterator[TestClient]:
    """Start the application with its lifespan (tables + gateway client).

    Yields:
        TestClient: The application's client.
    """
    with TestClient(app) as test_client:
        yield test_client


def test_wrong_secret_is_401(client: TestClient) -> None:
    """A delivery signed with another secret is refused."""
    response = post(client, inbound("A1", "5511999999999@s.whatsapp.net"), "outro")
    assert response.status_code == 401


def test_message_is_stored_once(client: TestClient) -> None:
    """The gateway's redelivery becomes ``duplicate``, not a second row."""
    payload = inbound(
        "A2", "5511999999999@s.whatsapp.net", chatKey="5511999999999", text="Hi"
    )
    assert post(client, payload).json() == {"outcome": "stored"}
    assert post(client, payload).json() == {"outcome": "duplicate"}


def test_group_is_skipped(client: TestClient) -> None:
    """A group message is neither stored nor answered."""
    payload = inbound("A3", "120363000000000000@g.us", text="good morning")
    assert post(client, payload).json() == {"outcome": "skipped"}


def test_lost_media(client: TestClient) -> None:
    """``mediaType`` without ``mediaUrl`` is lost media."""
    payload = inbound("A4", "5511999999999@s.whatsapp.net", mediaType="audio")
    assert post(client, payload).json() == {"outcome": "media-lost"}


def test_late_status_is_stale(client: TestClient) -> None:
    """A ``delivered`` after the ``read`` does not move the status back."""
    assert post(client, status("out-9", "read")).json() == {"outcome": "applied"}
    assert post(client, status("out-9", "delivered")).json() == {"outcome": "stale"}


def test_unknown_event_is_200(client: TestClient) -> None:
    """An event the SDK does not model answers ``200``, so it is not retried."""
    payload = {"event": "message.edited", "messageId": "A2"}
    assert post(client, payload).json() == {"outcome": "ignored"}
```

```console
$ pytest test_zap_service.py -v
test_zap_service.py::test_wrong_secret_is_401 PASSED
test_zap_service.py::test_message_is_stored_once PASSED
test_zap_service.py::test_group_is_skipped PASSED
test_zap_service.py::test_lost_media PASSED
test_zap_service.py::test_late_status_is_stale PASSED
test_zap_service.py::test_unknown_event_is_200 PASSED
============================== 6 passed in 0.68s ===============================
```

!!! check "Signature cross-checked with the gateway"
    The standard library's `hmac` produces the same header as the gateway's
    `signPayload` (`src/utils/signature.ts`): the SDK's suite pins two
    signatures produced by the gateway **itself** and checks that they pass
    (`tests/integrations/messaging/zap/test_webhooks.py`).

## Recap

- Register the webhook **with** `--secret`; without it the gateway does not
  sign.
- `make_zap_webhook_dependency(secret=...)` verifies the HMAC over the raw
  body and returns a `ZapWebhookDelivery`.
- Dispatch on `delivery.inbound` / `delivery.status`; whatever has neither
  answers `200` and goes to the log.
- Compare against the enums — `ZapWebhookEvent`, `ZapInboundMediaType`,
  `ZapJidServer`, `ZapOutboundKind`, `AcceptedResponseStatus` — not loose
  strings; `is` on the envelope and on `from_server`, `==` on body fields.
- Filter on `from_server`: groups, status stories and channels arrive too.
- Idempotency through `UNIQUE` on `message_id` (inbound) and `id` (status);
  the reply to the sender uses an `idempotency_key` derived from
  `message_id`.
- Group and reply by `chat_key`; `None` is a group or a LID with no number.
- Media only through `get_message_media`, and `media_url: None` with
  `media_type` means lost.
- `is_forward_transition` before writing any status.
- Router → controller → service → repository: the route keeps one line, and
  the rules are testable without HTTP.

