# WhatsApp through zap-api

`zap-api` is the in-house WhatsApp gateway. The SDK ships its whole client
— 20 schemas and 18 operations — generated from the OpenAPI specification
and checked in, so you import it and use it without running codegen in your
own service.

```python
from tempest_fastapi_sdk import HTTPClient
from tempest_fastapi_sdk.integrations.messaging.zap import (
    SendTextRequest,
    ZapClient,
)

http: HTTPClient = HTTPClient(
    base_url="http://127.0.0.1:3000",
    default_headers={"x-api-key": "<your key>"},
)
client: ZapClient = ZapClient(http)

accepted = await client.send_text(
    body=SendTextRequest(to="5511999999999", text="your order is on its way"),
    idempotency_key="4f1c9a2e-...",
)
print(accepted.id, accepted.status, accepted.deduped)
```

Authentication is the `x-api-key` header, and only that header. Set it once
as a default on the `HTTPClient` — there is no query-parameter fallback,
and `idempotency_key` authenticates **nothing**.

## Sending is not delivering

This is the part that changes how you write the service. A send answers
`202` with the **row that was enqueued**, not with a confirmation that
WhatsApp has the message:

```python
accepted = await client.send_text(body=SendTextRequest(to=..., text=...))
accepted.status  # AcceptedResponseStatus.QUEUED
```

`status` walks `queued → sending → sent → delivered → read`, or `failed`.
Those transitions arrive on the gateway's **status webhook**.

!!! warning "The webhook has no contract yet"
    The specification describes the webhook in prose and declares **no**
    `webhooks` block and no `callbacks`, so there is nothing to generate
    from and this package does not model it. A service that needs delivery
    state reads the webhook itself, for now.

    Measured 2026-09-06 against the document this package was generated
    from: 18 operations, zero `callbacks`, no `webhooks`.

## A retry needs the idempotency key

Because the send is asynchronous, a lost `202` leaves you unable to tell
whether the message was enqueued. Resending blindly sends two.

Pass a fresh `idempotency_key` per message, and **reuse that same one**
when retrying that message:

```python
from uuid import uuid4

key = str(uuid4())

accepted = await client.send_text(
    body=SendTextRequest(to="5511999999999", text="hi"),
    idempotency_key=key,
)

# The network dropped and you never saw the response. Retry with the SAME key:
again = await client.send_text(
    body=SendTextRequest(to="5511999999999", text="hi"),
    idempotency_key=key,
)
assert again.deduped is True      # the original row came back
assert again.id == accepted.id    # and nothing was sent twice
```

!!! danger "The key belongs to the message, not to the attempt"
    It is scoped to your consumer and **stays claimed for as long as the
    row exists**. Reusing an old key for a **new** message answers
    `deduped: true` and **sends nothing** — the silence looks like success.
    Generate one key per message.

## What else it does

| Method | What it does |
| --- | --- |
| `send_text`, `send_image`, `send_video`, `send_audio`, `send_document` | Enqueue a message. All accept `reply_to` |
| `react` | Enqueue an emoji reaction to a message |
| `set_typing`, `mark_read` | Presence signals. Both answer `204` |
| `check_number` | Whether a number is on WhatsApp, and its JID |
| `get_history` | A chat's latest messages (`limit` 1 to 200) |
| `start_session`, `get_session_status`, `get_session_qr`, `disconnect_session` | Connection life cycle |
| `health` | Liveness, unauthenticated |

`to` accepts two spellings, and the difference is real: the `send_*`
operations require digits (`^\d{10,15}$`), while `react`, `mark_read` and
`set_typing` also accept the full JID the inbound webhook delivers
(`^\d{10,15}(@s\.whatsapp\.net)?$`).

## Two routes the client types as `None`

`metrics` answers `text/plain` (Prometheus exposition) and
`get_session_qr_image` answers `image/png`. The generator models
`application/json` only, reports both, and types them `-> None`.

For the QR, use the JSON route, which carries the same value:

```python
qr = await client.get_session_qr()
print(qr.qr)   # the pairing code as a string
```

!!! note "`health` and `ready` also return `None`"
    The specification declares both with **no content**, so the body never
    reaches the typed client. They do answer real JSON, though — measured
    2026-09-06 against a running gateway:

    ```console
    $ curl -s http://127.0.0.1:3000/health
    {"status":"ok","session":"disconnected","ready":false,
     "reconnectAttempts":0,"queue":{"queued":0,"oldestQueuedSeconds":null}}
    $ curl -s -o /dev/null -w '%{http_code}' http://127.0.0.1:3000/ready
    503
    ```

    `/ready` returns the same object without `status`. **Nothing in the
    repository reproduces this**: the gateway is external, and there is no
    fixture or cassette here — the command above is the evidence, and it
    needs a gateway running. Declaring the schema on the gateway makes
    `make zap-regen` expose it, and then it becomes offline-verifiable like
    everything else.

## Regenerating

The client is generated from `vendor/zap-openapi.yaml` and checked in.
Hand-editing `schemas.py` or `client.py` is reverted by the next
regeneration — and
`tests/integrations/messaging/zap/test_generated_drift.py` fails before
that happens.

```bash
make zap-fetch    # re-read the gateway's document (needs it running)
make zap-regen    # regenerate from the vendored copy, offline
```

`zap-fetch` reads `ZAP_OPENAPI_URL`, defaulting to `http://127.0.0.1:3000`.
Unlike OpenPix and Mercado Pago there is **no canonical public URL** for
this specification — it is ours, and it is served from wherever the gateway
runs. That is why the vendored file is the authority, and why `SPEC_SHA256`
records which bytes this checkout generated from.

## Recap

- `ZapClient` over `HTTPClient`, with `x-api-key` as a default header.
- A send answers `202` and an enqueued row — **not** a delivery.
- One `idempotency_key` per message, reused only to retry that message.
- The status webhook has no schema yet; the package does not model it.
- `make zap-regen` edits the generated code, never you.
