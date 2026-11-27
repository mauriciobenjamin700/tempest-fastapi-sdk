# Online game rooms (TCP)

Some clients do not speak WebSocket, or already have their whole multiplayer
written on a TCP socket — PokeBudokai's `NetSession`, for one, reads and
writes on a pair of `InputStream`/`OutputStream`. For them, the room server
of [Online game rooms (WebSocket)](https://mauriciobenjamin700.github.io/tempest-fastapi-sdk/en/recipes/game-rooms-websocket.md) also answers on
raw TCP, with the **same protocol** and the **same rooms**: a player on TCP
and another in the browser can play together.

Read the WebSocket recipe first: the messages (`create`, `join`, `resume`,
`send`, `welcome`, `player_left`…), the slots, the host, reconnection and the
limits are the same. Only the transport changes here.

## Why frames, not loose bytes

TCP is a byte stream with no message boundaries, and PokeBudokai's
`NetProto` does not carry each message's length: the reader has to know
every type's format. The room server does not know the game's protocol, and
still needs to know where a message ends to relay it whole to the right
slot. So every message travels in a frame:

```text
[u32 tamanho, big-endian][u8 tipo][payload: tamanho bytes]
```

| Kind | Constant | Payload |
| --- | --- | --- |
| `0` | `FRAME_JSON` | control message, UTF-8 JSON |
| `1` | `FRAME_BINARY` | binary relay: `[u8 slot][game bytes]` |

`length` counts the payload only. A frame above `max_frame_bytes` (64 KiB by
default) closes the connection before the server reads its body.

## The server, next to the WebSocket

```python
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

import uvicorn
from fastapi import FastAPI

from tempest_fastapi_sdk.rooms import (
    RoomPolicy,
    RoomRegistry,
    make_game_room_router,
    serve_game_rooms_tcp,
)
from tempest_fastapi_sdk.settings import WebSocketSettings

registry = RoomRegistry(RoomPolicy(max_players=4, protocol_version="6"))


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    """Open the TCP port next to the WebSocket endpoint, same rooms."""
    tcp = await serve_game_rooms_tcp(registry, host="127.0.0.1", port=47200)
    yield
    tcp.close()
    await tcp.wait_closed()


app = FastAPI(lifespan=lifespan)
app.include_router(make_game_room_router(registry, settings=WebSocketSettings()))

if __name__ == "__main__":
    uvicorn.run(app, host="127.0.0.1", port=8000)
```

`serve_game_rooms_tcp` opens port 47200 on FastAPI's event loop, with the
**same `registry`**: a room a phone created over TCP shows up for whoever
joins through the WebSocket. Without FastAPI, it runs alone in an
`asyncio.run`.

What it does on every connection:

- turns on **`TCP_NODELAY`**: lockstep sends a small frame every tick, and
  Nagle's algorithm would hold that frame back waiting for more data;
- sends `{"type": "ping"}` every `heartbeat_seconds` (15 s by default); any
  frame the client sends counts as a sign of life, and `{"type": "pong"}` is
  swallowed;
- closes a client silent for `heartbeat_timeout_seconds` (45 s), without a
  word — their seat waits for the `resume`, as on the WebSocket;
- raw TCP has no close code, so when the server ends a connection on
  purpose it says so first with a control frame. Today that happens in one
  case only: the player resumed their seat on another connection, and the
  old one gets `{"type": "close", "code": 4409, "reason": "replaced"}` and
  then the end of the stream;
- refuses an unknown frame kind or invalid JSON with `error`
  (`bad_request`), without closing.

## Two phones in a room

The Python client below plays both devices:

```python
import asyncio
import json
from typing import Any

from tempest_fastapi_sdk.rooms import FRAME_BINARY, FRAME_JSON, encode_frame, read_frame


class Player:
    """One TCP player speaking the framed room protocol."""

    def __init__(self, reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
        self.reader = reader
        self.writer = writer

    async def send(self, message: dict[str, Any]) -> None:
        """Send a control message."""
        self.writer.write(encode_frame(FRAME_JSON, json.dumps(message).encode()))
        await self.writer.drain()

    async def send_bytes(self, data: bytes) -> None:
        """Send a binary relay frame."""
        self.writer.write(encode_frame(FRAME_BINARY, data))
        await self.writer.drain()

    async def until(self, kind: str) -> dict[str, Any]:
        """Read frames until a control message of ``kind`` arrives."""
        while True:
            frame_kind, payload = await read_frame(self.reader)
            if frame_kind == FRAME_JSON:
                message: dict[str, Any] = json.loads(payload)
                if message["type"] == kind:
                    return message

    async def binary(self) -> bytes:
        """Read frames until a binary one arrives."""
        while True:
            frame_kind, payload = await read_frame(self.reader)
            if frame_kind == FRAME_BINARY:
                return payload


async def main() -> None:
    """Two phones meet over TCP and swap one tick of input."""
    host = Player(*await asyncio.open_connection("127.0.0.1", 47200))
    guest = Player(*await asyncio.open_connection("127.0.0.1", 47200))
    print("hello:", await host.until("hello"))
    await host.send({"type": "create", "name": "Ana", "version": "6"})
    code = (await host.until("welcome"))["room"]["code"]
    await guest.send({"type": "join", "code": code, "name": "Bia", "version": "6"})
    print("convidado no slot", (await guest.until("welcome"))["slot"], "da sala", code)
    await guest.send_bytes(bytes([0]) + (7).to_bytes(4, "big") + (16).to_bytes(2, "big"))
    print("anfitrião recebeu:", (await host.binary()).hex(" "))


asyncio.run(main())
```

Measured output (against the server above):

```text
hello: {'type': 'hello', 'protocol_version': '6', 'max_players': 4, 'reconnect_grace_seconds': 10.0, 'heartbeat_seconds': 15.0, 'requires_identity': False}
convidado no slot 1 da sala W5ED
anfitrião recebeu: 01 00 00 00 07 00 10
```

The guest sent tick 7's input to slot `0` (the host), and the host received
it with the first byte swapped for the sender's slot (`01`).

!!! tip "The same client is a test double"
    PokeBudokai's backlog mentions a "fake guest in Python" to test the room
    without a second device. The `Player` class above is that guest: it
    speaks the whole protocol in 30 lines with the SDK's `encode_frame` /
    `read_frame`.

## On Android (PokeBudokai's `NetSession`)

`NetSession` already splits reading and writing into two threads over a pair
of streams. Online, it keeps writing `NetProto`; what changes is the
channel, which wraps each message in a frame and opens each frame received:

```kotlin
class RoomChannel(host: String, port: Int) {
    private val socket = Socket().apply {
        tcpNoDelay = true
        connect(InetSocketAddress(host, port), 5_000)
    }
    private val input = DataInputStream(BufferedInputStream(socket.getInputStream()))
    private val output = DataOutputStream(BufferedOutputStream(socket.getOutputStream()))

    @Synchronized
    fun sendControl(json: String) = writeFrame(0, json.toByteArray(Charsets.UTF_8))

    // Uma mensagem do NetProto para um slot (0xFF = todos os outros).
    @Synchronized
    fun sendGame(toSlot: Int, netProtoMessage: ByteArray) =
        writeFrame(1, byteArrayOf(toSlot.toByte()) + netProtoMessage)

    private fun writeFrame(kind: Int, payload: ByteArray) {
        output.writeInt(payload.size)   // big-endian, como o servidor espera
        output.writeByte(kind)
        output.write(payload)
        output.flush()
    }

    // Bloqueia até o próximo frame: (tipo, payload).
    fun readFrame(): Pair<Int, ByteArray> {
        val size = input.readInt()
        val kind = input.readUnsignedByte()
        val payload = ByteArray(size).also { input.readFully(it) }
        return kind to payload
    }
}
```

On the reading thread, a kind `1` frame carries `[sender slot][NetProto]` —
the slot says which player the `INPUT` came from, which the ALL vs ALL room
needs to build its `FRAME`. A kind `0` frame is control: handle `welcome`
(keep `player_id`, `resume_token` and the code for `resume`),
`player_disconnected` (hand the slot to the CPU), `ping` (answer
`{"type":"pong"}` while the game sits in a menu) and `close` (another
connection took the seat over: do not `resume` again).

!!! warning "Raw TCP has no TLS"
    The TCP server talks in the clear. Over the internet, put a TCP proxy
    that terminates TLS in front (nginx `stream` with `ssl`, or HAProxy) and
    connect the app with `SSLSocket`, or prefer the
    [WebSocket](https://mauriciobenjamin700.github.io/tempest-fastapi-sdk/en/recipes/game-rooms-websocket.md) with `wss://`, which goes through
    any HTTP proxy.

## Accounts and reconnection

- **Accounts:** with an `identity_resolver` on the `RoomRegistry`, the token
  goes in the `token` field of `create` / `join` (TCP has no query string).
  A missing or invalid token answers `error` with `code: "unauthorized"`.
- **Reconnection:** open another connection and send `resume` with the
  `code`, `player_id` and `resume_token` from `welcome`. A player can come
  back over TCP or over WebSocket — measured: a TCP player that dropped
  resumed its slot through the WebSocket.

## Recap

- `serve_game_rooms_tcp(registry, ...)` serves the same room protocol on raw
  TCP, in FastAPI's lifespan or alone.
- Every message goes in a frame `[u32 length][u8 kind][payload]`: kind `0`
  is JSON control, kind `1` is binary relay with the slot in front.
- Same `registry` = same rooms: TCP and WebSocket play together.
- `TCP_NODELAY`, server ping and drop on silence come built in; TLS belongs
  to a TCP proxy in front.
- `encode_frame` / `read_frame` build a test client in a few lines.
