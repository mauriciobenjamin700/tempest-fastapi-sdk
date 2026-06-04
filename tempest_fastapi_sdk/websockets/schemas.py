"""Schemas exchanged over the WebSocket router."""

from __future__ import annotations

from typing import Any

from pydantic import Field

from tempest_fastapi_sdk.schemas.base import BaseSchema


class WSEnvelope(BaseSchema):
    """Canonical message envelope for the bundled WebSocket router.

    Every frame the SDK sends — application data, heartbeats, errors —
    fits this shape so clients can dispatch on ``type`` alone. Senders
    on the consumer side are encouraged to use it too, but the router
    accepts any JSON payload and only the heartbeat frames it owns are
    strictly required to follow this schema.

    Attributes:
        type (str): Event name. Reserved values:
            ``"ping"`` / ``"pong"`` (heartbeat).
        data (dict[str, Any]): Payload — empty dict when none.
        request_id (str | None): Echoes the originating HTTP
            request-id for end-to-end tracing across SSE/HTTP/WS.
    """

    type: str = Field(
        ...,
        title="Event type",
        description=(
            "Stable identifier the client dispatches on. Reserved: "
            "``ping``/``pong`` (heartbeat). Any other value is "
            "application-defined."
        ),
        examples=["chat.message", "order.status_changed", "ping"],
    )
    data: dict[str, Any] = Field(
        default_factory=dict,
        title="Event payload",
        description="Arbitrary JSON-compatible payload.",
        examples=[{"order_id": "01ab…", "status": "paid"}, {}],
    )
    request_id: str | None = Field(
        default=None,
        title="Request ID",
        description=(
            "Optional correlation id mirroring the SDK's "
            "``X-Request-ID`` header so logs across HTTP, SSE and "
            "WebSocket can be stitched together."
        ),
        examples=[None, "d83e4b0c-7c2f-4bd6-aaa1-7d4f6cf5e5e9"],
    )


__all__: list[str] = ["WSEnvelope"]
