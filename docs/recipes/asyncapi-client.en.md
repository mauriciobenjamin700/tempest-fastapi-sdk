# WebSocket client (AsyncAPI)

The [integration client (OpenAPI)](openapi-client.md) recipe generates an
HTTP client from a specification. This one does the same for what OpenAPI
**cannot** describe: a connection that stays open, carries messages both
ways, and where the server speaks without being asked.

That is not a tooling limitation but a format one. OpenAPI models one
request and its response; there is nowhere to put a socket. So a WebSocket
route usually becomes a paragraph of prose in the documentation, and prose
generates no client at all.

The format that does describe it is **AsyncAPI 3.0**, and the SDK reads it.

## Generating

```bash
uv run tempest asyncapi-client http://127.0.0.1:3000/asyncapi.json \
    --name zap \
    --out src/integrations/zap_ws
```

Three files come out, as with the OpenAPI generator:

| File | What it holds |
| --- | --- |
| `schemas.py` | One Pydantic class per frame payload |
| `stream.py` | The client, plus both unions and the unknown-frame error |
| `__init__.py` | Re-exports both, in the double form (`as` + `__all__`) |

It needs the `[websocket]` extra, which the SDK already uses on the server
side — no new dependency enters because of this.

## Using it

```python
import asyncio

from src.integrations.zap_ws import (
    AckFrame,
    ErrorFrame,
    ServerMessageFrame,
    SubscribeFrame,
    ZapStream,
)


async def main() -> None:
    """Subscribe to everything and print what arrives."""
    async with ZapStream(x_api_key="<your key>") as stream:
        await stream.send(SubscribeFrame(action="subscribe", room="*"))

        async for frame in stream:
            match frame:
                case ServerMessageFrame():
                    print(frame.payload.remote_jid, frame.payload.text)
                case AckFrame():
                    print("ack:", frame.event)
                case ErrorFrame():
                    print("error:", frame.message)


asyncio.run(main())
```

`url` gets a default when the document declares `servers`, and a handshake
header becomes a **required argument** when the document marks it required —
`x-api-key` became `x_api_key`.

## Two unions, not one

What you send and what you receive are different types:

```python
from src.integrations.zap_ws import (
    AckFrame,
    ErrorFrame,
    SendFrame,
    ServerMessageFrame,
    SubscribeFrame,
    UnsubscribeFrame,
)

ZapStreamClientFrame = SubscribeFrame | UnsubscribeFrame | SendFrame
ZapStreamServerFrame = AckFrame | ErrorFrame | ServerMessageFrame
```

`send` accepts only the first; `receive` and iteration return only the
second. A `match` over the inbound union is exhaustive for the type-checker,
so a new frame in the document becomes a type error in your service rather
than a silent `else`.

!!! tip "Each variant is recognized by its discriminant"
    The generator looks, in each payload, for a property whose `enum` holds
    exactly one value — which is how a literal renders in JSON Schema. That
    pair becomes the key of a dispatch table.

    A frame arriving with a discriminant the document does not declare
    raises `ZapStreamFrameError`, carrying the tag and the body. The common
    cause is a server ahead of the checked-in document, and the fix is to
    regenerate.

## The part that most often goes wrong: direction

AsyncAPI's `action` is relative to **whoever published the document**. Since
the publisher is the server, a frame the client sends appears in the
document as `action: "receive"` — the server is the one receiving.

A client generator has to **invert** all of them.

!!! danger "A wrong inversion breaks nothing visible"
    The client compiles, passes the type-check, and sends what it should be
    listening for. No signal appears until the first message goes missing.

    That is why the generator **refuses** a document that does not declare
    `x-tempest-perspective: "server"` at the root:

    ```console
    $ uv run tempest asyncapi-client ./doc.json --name x --out /tmp/x
    error: ./doc.json does not declare `x-tempest-perspective`. AsyncAPI's
    `action` is relative to whoever published the document, and a client has
    to invert it — so a document that does not say which end wrote it cannot
    be generated from.
    ```

    A document produced by `tempest-express-sdk` already carries the field.
    In a hand-written one it is a single line.

The inversion happens **once**, in the parser. The IR speaks only the
client's point of view (`outbound`/`inbound`), so neither the emitter nor
any reader of the IR has to remember whose `action` it is holding.

## One channel, always

The specification is explicit: in WebSocket *"the channel represents the
connection [...] there's only one channel"*. Unlike Kafka or MQTT, where a
channel is a topic. A document with more than one is refused — it is
describing another transport, and this generator emits a WebSocket client.

Rooms, where they exist, are a concept of the **frames**, not of the channel.

## Validation comes along

A constraint in the document becomes Pydantic validation in the generated
model:

```python
from src.integrations.zap_ws import SubscribeFrame

# docs-guard: skip — the refused call below is the subject of the section
SubscribeFrame(action="subscribe", room="not@valid@here")
# pydantic_core.ValidationError: String should match pattern
# '^(\*|\d{10,15}|[A-Za-z0-9._-]+@[A-Za-z0-9.-]+)$'
```

The invalid frame never reaches the network. It is the same regex the server
applies, because both come from the same schema.

## Keeping it current

As with the OpenAPI generator, the document is vendored and the generated
files are checked in, so a consumer does not run codegen:

```bash
make zap-ws-fetch    # re-reads the document from the gateway (needs it up)
make zap-ws-regen    # regenerates from the vendored file, offline
```

`SPEC_SHA256` records which bytes this checkout generated from, and
`tests/integrations/messaging/zap_ws/test_generated_drift.py` fails if
anyone hand-edits the generated files or forgets to update the digest.

!!! note "Two surfaces, two documents"
    A service that speaks HTTP **and** WebSocket publishes both:
    `/openapi.json` for the routes and `/asyncapi.json` for the socket. They
    coexist because no single format describes both.

## Recap

- OpenAPI does not describe sockets; AsyncAPI does, and the SDK reads 3.x.
- `tempest asyncapi-client` writes `schemas.py`, `stream.py` and the barrel.
- One union for what you send, another for what you receive; exhaustive
  `match`.
- Direction is inverted once, in the parser — and the document has to say
  whose point of view it is, or it is refused.
- One channel per connection, because WebSocket has no virtual channels.
- Document constraints become validation, so an invalid frame never leaves.
- Vendored document, checked-in output, a drift test guarding both.
