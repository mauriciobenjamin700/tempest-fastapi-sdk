# WhatsApp through zap-api

`zap-api` is the in-house WhatsApp gateway. The SDK ships its whole client
— 28 schemas and 27 operations — generated from the OpenAPI specification
and checked in, so you import it and use it without running codegen in your
own service.

```python
import asyncio

from tempest_fastapi_sdk import HTTPClient
from tempest_fastapi_sdk.integrations.messaging.zap import (
    SendTextRequest,
    ZapClient,
)


async def main() -> None:
    """Send one text message."""
    http: HTTPClient = HTTPClient(
        base_url="http://127.0.0.1:3000",
        default_headers={"x-api-key": "<your key>"},
    )
    async with http:
        client: ZapClient = ZapClient(http)
        accepted = await client.send_text(
            body=SendTextRequest(
                to="5511999999999", text="your order is out for delivery"
            ),
            idempotency_key="4f1c9a2e-...",
        )
        print(accepted.id, accepted.status, accepted.deduped)


asyncio.run(main())
```

Authentication is the `x-api-key` header, and only that. Set it once as an
`HTTPClient` default — there is no query-string fallback, and
`idempotency_key` authenticates **nothing**.

## Sending is not delivering

This is the part that changes how you write the service. A send answers
`202` with the **row that was enqueued**, not with a confirmation that
WhatsApp got it:

```python
import asyncio

from tempest_fastapi_sdk import HTTPClient
from tempest_fastapi_sdk.integrations.messaging.zap import (
    AcceptedResponseStatus,
    SendTextRequest,
    ZapClient,
)


async def main() -> None:
    """Read the status the 202 carries."""
    http: HTTPClient = HTTPClient(
        base_url="http://127.0.0.1:3000",
        default_headers={"x-api-key": "<your key>"},
    )
    async with http:
        client: ZapClient = ZapClient(http)
        accepted = await client.send_text(
            body=SendTextRequest(to="5511999999999", text="hi"),
        )
        assert accepted.status == AcceptedResponseStatus.QUEUED


asyncio.run(main())
```

`status` walks `queued → sending → sent → delivered → read`, or `failed`.
Those transitions arrive on the gateway's **status webhook**.

!!! info "`id` and `status` are nullable, in exactly one case"
    A gateway running **without persistence** (`DATABASE_URL` unset) sends
    inline instead of enqueueing, and answers the same `202` with `id` and
    `status` null — there is no outbox row to name or to report a status
    for. It is the only case where they come back null.

!!! warning "The webhook has no contract yet"
    The specification describes the webhook in prose and declares neither a
    `webhooks` block nor `callbacks`, so there is nothing to generate and
    this package does not model it. A service that needs delivery state
    reads the webhook on its own, for now.

    Measured on 2026-09-06 against the document this package generated
    from: 27 operations, zero `callbacks`, no `webhooks`.

## Retry needs the idempotency key

Because sending is asynchronous, a lost `202` leaves you unsure whether the
message was enqueued. Blind resending sends two.

Pass a fresh `idempotency_key` per message, and **reuse the same one** when
you repeat that message:

```python
import asyncio
from uuid import uuid4

from tempest_fastapi_sdk import HTTPClient
from tempest_fastapi_sdk.integrations.messaging.zap import (
    SendTextRequest,
    ZapClient,
)


async def main() -> None:
    """Repeat a send without sending the message twice."""
    http: HTTPClient = HTTPClient(
        base_url="http://127.0.0.1:3000",
        default_headers={"x-api-key": "<your key>"},
    )
    async with http:
        client: ZapClient = ZapClient(http)
        key: str = str(uuid4())

        accepted = await client.send_text(
            body=SendTextRequest(to="5511999999999", text="hi"),
            idempotency_key=key,
        )

        again = await client.send_text(
            body=SendTextRequest(to="5511999999999", text="hi"),
            idempotency_key=key,
        )
        assert again.deduped is True
        assert again.id == accepted.id


asyncio.run(main())
```

The second call is the retry: the network dropped and you never saw the
response. Repeating with the **same** key returns the original row
(`deduped: true`) and sends nothing new.

!!! danger "The key belongs to the message, not to the attempt"
    It is scoped to your consumer and **stays claimed for as long as the
    row exists**. Reusing an old key for a **new** message answers
    `deduped: true` and **sends nothing** — the silence looks like success.
    Generate one key per message.

## What else you can do

| Method | What it does |
| --- | --- |
| `send_text`, `send_image`, `send_video`, `send_audio`, `send_document` | Enqueue a message. All accept `reply_to` |
| `upload_image`, `upload_video`, `upload_audio`, `upload_document` | Send the file in the body, as `multipart/form-data` |
| `send_image_base64`, `send_video_base64`, `send_audio_base64`, `send_document_base64` | Send the file inline as base64, in JSON |
| `react` | Enqueues an emoji reaction to a message |
| `set_typing`, `mark_read` | Presence signals. They answer `204` |
| `check_number` | Whether the number exists on WhatsApp, and its JID |
| `get_history` | A chat's latest messages (`limit` from 1 to 200) |
| `get_message_media` | The media bytes of a stored message |
| `start_session`, `get_session_status`, `get_session_qr`, `get_session_qr_image`, `disconnect_session` | Connection lifecycle |
| `health`, `ready`, `metrics` | Probes and the Prometheus exposition |

`to` accepts two spellings, and the difference is real: the `send_*`
operations require digits (`^\d{10,15}$`), while `react`, `mark_read` and
`set_typing` also accept the full JID the inbound webhook delivers
(`^\d{10,15}(@s\.whatsapp\.net)?$`).

## Three ways to send media

The same image reaches WhatsApp by three routes, and the choice is about
where the file lives:

| Route | When to use it |
| --- | --- |
| `send_image(body=...)` | The file already has a public URL; the gateway fetches it |
| `upload_image(file=...)` | The file exists only on the caller's disk |
| `send_image_base64(body=...)` | You already hold the bytes and want a single JSON body |

The `upload_*` routes are `multipart/form-data`, so the client exposes
them with the form's fields flattened — the file on `files`, the scalars
on `data`:

```python
import asyncio
from pathlib import Path

from tempest_fastapi_sdk import HTTPClient
from tempest_fastapi_sdk.integrations.messaging.zap import ZapClient


async def main() -> None:
    """Upload an image from disk."""
    http: HTTPClient = HTTPClient(
        base_url="http://127.0.0.1:3000",
        default_headers={"x-api-key": "<your key>"},
    )
    async with http:
        client: ZapClient = ZapClient(http)
        accepted = await client.upload_image(
            file=Path("invoice.png").read_bytes(),
            to="5511999999999",
            caption="your invoice",
        )
        print(accepted.id, accepted.status)


asyncio.run(main())
```

!!! warning "The file is `bytes`, and the retry is why"
    `HTTPClient` repeats the request on network errors and on `5xx`, and a
    retry re-sends the same arguments. A file object is already consumed by
    the first attempt, so the second would upload a truncated body — which
    the server cannot tell from a short file. That is why the part is
    `bytes`.

    Hand `HTTPClient` a stream directly and it **does not retry**: one
    attempt, and its response is what you get. That is the right trade
    between not retrying and retrying wrong.

!!! note "The cap is `MEDIA_MAX_BYTES`"
    16MB by default, enforced while the body streams — an oversized upload
    is cut off mid-flight rather than buffered in full and refused
    afterwards. The client sees `413`.

## A body that is not JSON arrives as `bytes`

Three operations answer a success body that is not JSON:
`get_message_media` (`application/octet-stream`), `get_session_qr_image`
(`image/png`) and `metrics` (`text/plain`). The client types them
`-> bytes` and hands the body over **undecoded** — the specification
carries no charset, and guessing one corrupts silently.

```python
import asyncio
from pathlib import Path

from tempest_fastapi_sdk import HTTPClient
from tempest_fastapi_sdk.integrations.messaging.zap import ZapClient


async def main() -> None:
    """Save the media of an inbound message."""
    http: HTTPClient = HTTPClient(
        base_url="http://127.0.0.1:3000",
        default_headers={"x-api-key": "<your key>"},
    )
    async with http:
        client: ZapClient = ZapClient(http)

        media: bytes = await client.get_message_media("3EB0C767D26B8E3C1A2B")
        Path("received.bin").write_bytes(media)

        scrape: bytes = await client.metrics()
        print(scrape.decode("utf-8"))


asyncio.run(main())
```

!!! danger "The media cannot be fetched again"
    The gateway downloads media while the message is still in memory, and
    WhatsApp **will not serve it again**. A message whose download failed
    or exceeded `MEDIA_MAX_BYTES` is stored with a media type and no file,
    and answers `404` here. This endpoint is the only route to those bytes
    — fetch them when the inbound webhook tells you, not later.

!!! note "`health` and `ready` are still `-> None`"
    These two are not the case above: the specification declares both with
    **no content**, so there is no declared body to type. They do answer
    real JSON, though — measured on 2026-09-06 against a running gateway:

    ```console
    $ curl -s http://127.0.0.1:3000/health
    {"status":"ok","session":"disconnected","ready":false,
     "reconnectAttempts":0,"queue":{"queued":0,"oldestQueuedSeconds":null}}
    $ curl -s -o /dev/null -w '%{http_code}' http://127.0.0.1:3000/ready
    503
    ```

    `/ready` answers the same object without `status`. **Nothing in this
    repository reproduces it**: the gateway is external, and there is no
    fixture or cassette here — the command above is the evidence, and it
    needs a gateway running. Declaring the schema on the gateway makes
    `make zap-regen` expose it, and it becomes verifiable offline like the
    rest.

## Regenerating

The client is generated from `vendor/zap-openapi.yaml` and checked in.
Hand-editing `schemas.py` or `client.py` is reverted by the next
regeneration — and
`tests/integrations/messaging/zap/test_generated_drift.py` fails before
that.

```bash
make zap-fetch    # re-reads the document from the gateway (needs it up)
make zap-regen    # regenerates from the vendored file, offline
```

`zap-fetch` reads `ZAP_OPENAPI_URL`, or `http://127.0.0.1:3000` by default.
Unlike OpenPix and Mercado Pago, there is **no canonical public URL** for
this specification — it is ours, and it is served from wherever the gateway
runs. That is why the vendored file is the authority, and why
`SPEC_SHA256` records which bytes this checkout generated from.

## Recap

- `ZapClient` over `HTTPClient`, with `x-api-key` as a default header.
- A send answers `202` and an enqueued row — **not** a delivery.
- One `idempotency_key` per message, reused only to retry that message.
- A non-JSON body arrives as `bytes`; media exists exactly once.
- The status webhook has no schema yet; the package does not model it.
- `make zap-regen` is what edits the generated code, never you.
