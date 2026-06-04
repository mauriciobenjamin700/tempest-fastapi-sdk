"""WebSocket primitives — authenticated router + connection hub.

Mounted by :func:`tempest_fastapi_sdk.make_websocket_router`. Adds
three concerns FastAPI's bare ``WebSocket`` route doesn't ship:

* **Bearer auth** at the handshake (query param ``?token=`` or
  ``Sec-WebSocket-Protocol: bearer,<jwt>``).
* **Heartbeat ping/pong** with timeout — drops half-open peers.
* **`WebSocketHub`** — in-process registry of live connections
  with broadcast, per-user delivery and topic subscriptions.

Re-exports use the PEP 484 ``from x import Y as Y`` form alongside
``__all__`` so every type-checker accepts
``from tempest_fastapi_sdk.websockets import WebSocketHub`` without a
"private import usage" diagnostic.
"""

from tempest_fastapi_sdk.websockets.hub import (
    WebSocketConnection as WebSocketConnection,
)
from tempest_fastapi_sdk.websockets.hub import WebSocketHub as WebSocketHub
from tempest_fastapi_sdk.websockets.router import (
    make_websocket_router as make_websocket_router,
)
from tempest_fastapi_sdk.websockets.schemas import WSEnvelope as WSEnvelope

__all__: list[str] = [
    "WSEnvelope",
    "WebSocketConnection",
    "WebSocketHub",
    "make_websocket_router",
]
