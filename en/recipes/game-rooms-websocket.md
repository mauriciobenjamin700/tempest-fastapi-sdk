# Online game rooms (WebSocket)

An online multiplayer game needs a meeting point on the internet: players
join a **room** with a short code, know who the host is, and swap messages
(each tick's input, the world state, an invite) with little latency. This
recipe builds that server with `tempest_fastapi_sdk.rooms`, on a FastAPI
WebSocket endpoint.

The server **does not simulate the game**. It groups players, relays their
messages, and takes care of what every online game would write the same way
— and get wrong in a different way each time:

- every message's **`from`** is the player the server knows, never a field
  the client sends;
- a room has a **player cap**, refuses joins **during a match**, and checks
  the **protocol version** on join;
- the **host** is elected and, when they leave, the next one takes over and
  everyone is told;
- a player who drops (Wi-Fi to 4G, a tunnel) **keeps their seat** for a
  window and comes back with `resume`, in the same slot;
- **rate and size limits** per player, and a silent connection is really
  closed.

Authority stays with the clients: a lockstep simulation everyone runs
(PokeBudokai), or a host that runs the world and sends snapshots (Jomasio's
co-op). For a client that does not speak WebSocket, the same rooms are in
[Online game rooms (TCP)](https://mauriciobenjamin700.github.io/tempest-fastapi-sdk/en/recipes/game-rooms-tcp.md).

## The server

```python
import uvicorn
from fastapi import FastAPI

from tempest_fastapi_sdk.rooms import RoomPolicy, RoomRegistry, make_game_room_router
from tempest_fastapi_sdk.settings import WebSocketSettings

registry = RoomRegistry(
    RoomPolicy(max_players=4, protocol_version="6", reconnect_grace_seconds=10)
)
app = FastAPI()
app.include_router(make_game_room_router(registry, settings=WebSocketSettings()))

if __name__ == "__main__":
    uvicorn.run(app, host="127.0.0.1", port=8000)
```

Three pieces:

- **`RoomRegistry`** holds the rooms, in this process' memory. One per game.
- **`RoomPolicy`** is the rules: up to 4 players, protocol `"6"` (a client on
  another version is refused), seat kept for 10 s after a drop.
- **`make_game_room_router`** mounts the endpoint at `/rooms/ws`. The
  `WebSocketSettings` gives the heartbeat (`WS_HEARTBEAT_SECONDS`,
  `WS_HEARTBEAT_TIMEOUT_SECONDS`) and the largest frame
  (`WS_MAX_MESSAGE_BYTES`; above it the connection closes with `1009`).

!!! tip "Phones on the network"
    `127.0.0.1` only accepts connections from the same machine. For phones
    to reach the server, put a proxy in front (nginx or Caddy, with
    `wss://`) or run with `host="0.0.0.0"` on a network you control.

## Two players in a room

A Python client that plays both phones: the host creates the room, the guest
joins by code, and the two swap one tick of input in binary and one message
in JSON.

```python
import asyncio
import json
from typing import Any

from websockets.asyncio.client import ClientConnection, connect

URL = "ws://127.0.0.1:8000/rooms/ws"


async def until(ws: ClientConnection, kind: str) -> dict[str, Any]:
    """Read control messages until one of ``kind`` arrives."""
    while True:
        frame = await ws.recv()
        if isinstance(frame, str):
            message: dict[str, Any] = json.loads(frame)
            if message["type"] == kind:
                return message


async def main() -> None:
    """A host and a guest meet in a room and swap one tick of input."""
    async with connect(URL) as host, connect(URL) as guest:
        print("hello:", await until(host, "hello"))
        await host.send(json.dumps({"type": "create", "name": "Ana", "version": "6"}))
        welcome = await until(host, "welcome")
        code = welcome["room"]["code"]
        print("sala criada:", code, "slot", welcome["slot"])

        await guest.send(
            json.dumps({"type": "join", "code": code, "name": "Bia", "version": "6"})
        )
        print("convidado no slot", (await until(guest, "welcome"))["slot"])
        print("anfitrião viu:", (await until(host, "player_joined"))["player"])

        await guest.send(bytes([0xFF]) + (7).to_bytes(4, "big") + (16).to_bytes(2, "big"))
        frame = await host.recv()
        assert isinstance(frame, bytes)
        print("anfitrião recebeu:", frame.hex(" "))

        await host.send(json.dumps({"type": "send", "data": {"type": "start", "seed": 42}}))
        print("convidado recebeu:", await until(guest, "message"))


asyncio.run(main())
```

Measured output (against the server above; ids and the code change on every
run):

```text
hello: {'type': 'hello', 'protocol_version': '6', 'max_players': 4, 'reconnect_grace_seconds': 10, 'heartbeat_seconds': 30, 'requires_identity': False}
sala criada: 3PJN slot 0
convidado no slot 1
anfitrião viu: {'player_id': 'a288dacbb8b60115', 'name': 'Bia', 'slot': 1, 'connected': True}
anfitrião recebeu: 01 00 00 00 07 00 10
convidado recebeu: {'type': 'message', 'from': '5bf6a98642dea04a', 'data': {'type': 'start', 'seed': 42}}
```

Note two things:

- The guest sent `ff 00 00 00 07 00 10` (destination `0xFF` = everyone else,
  plus 6 game bytes) and the host received `01 …`: the server swaps the first
  byte for the **sender's slot**.
- The JSON message's `from` was filled in by the server. A client that sent
  `"from": "someone"` inside `data` would arrive with the real `from` next to
  it.

## The protocol

**Text** frames are JSON control messages, always with a `type`. **Binary**
frames are game bytes the server relays without reading.

### Client → server

| `type` | Fields | What it does |
| --- | --- | --- |
| `create` | `name?`, `version?` | Opens a room, with you as host in slot 0 |
| `join` | `code`, `name?`, `version?` | Joins a room (code in any case) |
| `resume` | `code`, `player_id`, `resume_token` | Back to your seat after a drop |
| `send` | `data`, `to?` | Relays `data` to everyone else, or only to player `to` |
| `start` / `end` | — | Host moves the room to `playing` / back to `lobby` |
| `leave` | — | Leaves the room at once, freeing the slot |
| `echo` | `t?` | The server returns `t` at once: measure RTT with it |
| *(binary)* | `[u8 destination slot][bytes]` | `0xFF` = everyone else |

### Server → client

| `type` | When |
| --- | --- |
| `hello` | On connect: version, room size, reconnect window, heartbeat cadence |
| `welcome` | After `create`/`join`/`resume`: `player_id`, `resume_token`, `slot`, `resumed`, `room` |
| `left` | Reply to your `leave`: the `code` of the room you left |
| `player_joined` / `player_left` | Someone joined / left (or their reconnect window ran out) |
| `player_disconnected` / `player_reconnected` | Someone dropped (with `grace_seconds`) / came back |
| `host_changed` | The host left and another took over (the connected one with the lowest slot): `host_id` |
| `room_state` | The room moved to `playing` or `lobby`: `state` |
| `message` | A relayed message: `from` + `data` |
| `echo` | Reply to your `echo`, with the same `t` |
| `error` | Refusal: stable `code`, readable `message` and `request` (the refused `type`); the connection stays open |
| `ping` | Server heartbeat, `{"type": "ping"}` — the same object as over TCP: answer `{"type": "pong"}` when idle |
| *(binary)* | `[u8 sender slot][bytes]` |

Error `code`s: `room_not_found`, `room_full`, `room_in_match`,
`version_mismatch`, `not_host`, `not_in_room`, `already_in_room`,
`player_not_found`, `resume_rejected`, `rate_limited`,
`message_not_allowed`, `unauthorized`, `server_full`, `bad_request`.

!!! info "Any frame proves the client alive"
    A real-time game sends dozens of frames a second, and each counts as a
    sign of life. `pong` is only needed from an idle client (in the lobby,
    say). One silent past `WS_HEARTBEAT_TIMEOUT_SECONDS` is closed with
    `4408` at the first ping after the deadline — with a ping every 1 s
    and a 2 s deadline, the connection dropped 2.0 s after the last frame —
    and their seat waits for the `resume`.

## Binary lockstep (PokeBudokai)

PokeBudokai already has its own binary protocol (`NetProto`) and a
`NetSession` that reads and writes on a pair of streams. Online, the app only
swaps the channel: each `NetProto` message becomes a binary WebSocket frame,
with the destination slot in front. A sketch with OkHttp, on Android:

```kotlin
val client = OkHttpClient()
val socket = client.newWebSocket(
    Request.Builder().url("wss://jogo.example.com/rooms/ws").build(),
    object : WebSocketListener() {
        override fun onMessage(webSocket: WebSocket, text: String) {
            // hello, welcome, player_joined, host_changed, error...
            handleControl(JSONObject(text))
        }
        override fun onMessage(webSocket: WebSocket, bytes: ByteString) {
            val fromSlot = bytes[0].toInt() and 0xFF
            netSession.deliver(fromSlot, bytes.substring(1).toByteArray())
        }
    },
)
socket.send("""{"type":"create","name":"${Build.MODEL}","version":"6"}""")

// INPUT do tick: [destino 0xFF][tipo 4][int tick][short bits]
fun sendInput(tick: Int, bits: Int) {
    val buffer = ByteBuffer.allocate(8).put(0xFF.toByte()).put(4)
        .putInt(tick).putShort(bits.toShort())
    socket.send(ByteString.of(*buffer.array()))
}
```

Mapping what the game already does:

- **`HELLO` with version and `rosterHash`**: put both in `version`
  (`"6:<rosterHash>"`) **and** on the server
  (`RoomPolicy(protocol_version="6:<rosterHash>")`); the server refuses a
  mismatch with `version_mismatch` before any game frame. Both sides change
  together with every new roster.
- **ALL vs ALL room**: whoever creates the room starts in slot 0, but the
  host can change (`host_changed`). Find the host's slot by matching
  `room.host_id` against `room.players` instead of hard-coding 0; guests
  send their `INPUT` to that slot, and the host builds the `FRAME` and sends
  it to `0xFF`.
- **Drop**: `player_disconnected` arrives as soon as the server notices the
  drop; the host can hand that slot to the CPU on the same tick, without
  waiting for the 8 s stall.
- **Adaptive delay**: measure RTT with `echo` and pick the input delay (6 to
  10 ticks) from it.

## JSON with an authoritative host (Jomasio)

In Jomasio's co-op, the leader runs the NPC AI and sends `battleSnapshot` at
20 Hz; the partner sends `battlePlayerState`. With the SDK server, each game
message becomes the `data` of a `send`:

```typescript
const ws = new WebSocket(`wss://${location.host}/rooms/ws?token=${jwt}`);

ws.onmessage = (event) => {
  if (typeof event.data !== "string") return;
  const message = JSON.parse(event.data);
  if (message.type === "message") {
    handleGameMessage(message.from, message.data); // from is trustworthy
  } else if (message.type === "error") {
    console.warn(message.code, message.message);
  }
};

function sendSnapshot(snapshot: BattleSnapshot): void {
  ws.send(JSON.stringify({ type: "send", data: { type: "battleSnapshot", ...snapshot } }));
}

function invite(partnerId: string): void {
  ws.send(JSON.stringify({ type: "send", to: partnerId, data: { type: "pvpInvite", bestOf: 3 } }));
}
```

Three things the server now does for the game:

- `from` belongs to the server on **every** message — on the Node server,
  the battle relay stamps `from` from the socket, but invites
  (`guildInvite`, `pvpInvite` and their responses) forward the `from` the
  client wrote, and the socket's `playerId` comes from the `joinRoom` the
  client itself sends, so a client can invite on someone else's behalf;
- `to` delivers **only** to the addressee, instead of sending the invite to
  the whole room and letting the client filter;
- with `RoomPolicy(allowed_message_types=frozenset({...}))`, a `data.type`
  off the list is refused with `message_not_allowed` — the allow list the
  Node server keeps by hand. The list covers the JSON of `send`; binary
  frames pass unread.

## An account instead of a guest

With no configuration, every player is a guest: the server draws the
`player_id` and uses the `name` the client sent. To require an account, pass
an `identity_resolver`. The token comes in `?token=` and, missing or
invalid, the connection closes with `4401` before the handshake. For the
`UserAuthService` tokens, use the ready one:

```python
from fastapi import FastAPI

from tempest_fastapi_sdk import JWTUtils
from tempest_fastapi_sdk.rooms import (
    RoomPolicy,
    RoomRegistry,
    make_game_room_router,
    make_jwt_room_identity_resolver,
)
from tempest_fastapi_sdk.settings import JWTSettings, WebSocketSettings

tokens = JWTUtils(**JWTSettings().jwt_kwargs())
registry = RoomRegistry(
    RoomPolicy(max_players=2),
    identity_resolver=make_jwt_room_identity_resolver(tokens),
)
app = FastAPI()
app.include_router(make_game_room_router(registry, settings=WebSocketSettings()))
```

The `player_id` becomes the token's `sub` (the user id) and the name comes
from the `name` claim, when the token has one.

!!! danger "Do not write the resolver as `decode` + `sub`"
    `UserAuthService` signs the access token, the refresh token and the
    MFA-pending token with the same secret — all three carry `sub`. A
    resolver that only decodes and returns `sub` lets in someone who has not
    passed the second factor. `make_jwt_room_identity_resolver` is built on
    `make_jwt_bearer_resolver` and accepts access tokens only (measured in
    `tests/rooms/test_websocket.py`: refresh, MFA-pending and untyped tokens
    are refused).

With an account, the same player joining the room again (another tab,
another device) **takes over their seat**, and the old connection closes
with `4409`.

## Reconnection

`welcome` carries `player_id` and `resume_token`. Keep both (and the room
code) until the match ends. If the connection drops, open another and send:

```json
{"type": "resume", "code": "3PJN", "player_id": "a288dacbb8b60115", "resume_token": "…"}
```

Within `reconnect_grace_seconds`, you are back in the **same slot**, with
`"resumed": true`, and the others get `player_reconnected`. After that the
seat is freed (`player_left`) and `resume` answers `resume_rejected`. If the
old connection is still open (the server has not noticed the drop), it is
closed with `4409`.

## Latency

The server adds very little to the network's latency. Measured on loopback
(same machine, 12 cores, Python 3.12.7, uvicorn 0.52.4), `echo` round trip,
N=2000 per run, three runs:

| Transport | Median | p99 |
| --- | --- | --- |
| WebSocket | 0.22–0.24 ms | 0.34–0.41 ms |
| TCP | 0.15–0.16 ms | 0.25–0.30 ms |

Over the internet, the real RTT comes from the network (tens of ms on 4G),
not from the server.

## One process

!!! warning "Rooms live in one process' memory"
    Like `WebSocketHub`, `RoomRegistry` keeps everything in this process.
    Every player of a room must reach the **same replica**: run one replica
    of the room server, or use sticky sessions by room code. Restarting the
    process ends the open rooms.

## Recap

- `RoomRegistry` + `RoomPolicy` + `make_game_room_router` build the room
  server on a WebSocket endpoint.
- The server groups players by code, elects the host, relays JSON (`send`)
  and game bytes (binary frame with the slot in front) and stamps the sender
  — game authority stays with the clients.
- Version, player cap, room in a match, rate and size limits and heartbeat
  belong to the server, with stable-`code` refusals.
- A player who drops comes back to the same slot with `resume` within the
  reconnect window.
- With `identity_resolver`, the room requires an account; without it,
  everyone is a guest.
- Everything lives in one process: one replica (or sticky by room) for the
  room server.
