"""Typed WebSocket client generated from the zap-api WebSocket AsyncAPI document.

Do not edit by hand — rerun the generator to refresh.

The connection is /ws. Directions are the client's: `send` puts a frame on the wire, and
iterating the stream yields what the server sends.
"""

from __future__ import annotations

import json
from collections.abc import AsyncIterator, Mapping
from types import TracebackType
from typing import Any

from websockets.asyncio.client import ClientConnection, connect

from .schemas import (
    AckFrame,
    ErrorFrame,
    SendFrame,
    ServerMessageFrame,
    SubscribeFrame,
    UnsubscribeFrame,
)

DEFAULT_URL: str = "ws://127.0.0.1:3000/ws"
"""Connection URL the document's first server entry declares."""

ZapStreamClientFrame = SubscribeFrame | UnsubscribeFrame | SendFrame

ZapStreamServerFrame = AckFrame | ErrorFrame | ServerMessageFrame

_INBOUND_FRAMES: dict[str, type[ZapStreamServerFrame]] = {
    "ack": AckFrame,
    "error": ErrorFrame,
    "message": ServerMessageFrame,
}


class ZapStreamFrameError(ValueError):
    """Raised for an inbound frame the document does not declare.

    Carries the payload so a caller can log what actually arrived. A
    server that grows a frame the checked-in document predates is the
    common cause, and the fix is to regenerate.
    """

    def __init__(self, tag: object, payload: Any) -> None:
        """Initialize.

        Args:
            tag (object): The discriminant value that matched nothing.
            payload (Any): The decoded frame.
        """
        super().__init__(f"unknown inbound frame {tag!r}")
        self.tag: object = tag
        self.payload: Any = payload


class ZapStream:
    """Typed client for zap-api WebSocket.

    One connection per client. WebSocket has no virtual channels, so rooms are a concept
    of the frames below, not of the transport: a client subscribes to a `remoteJid` (or
    to `*`) and receives the messages fanned out to it.

    Use it as an async context manager; iterating yields the frames
    the server sends, already parsed into their generated types.
    """

    def __init__(
        self,
        *,
        url: str = DEFAULT_URL,
        x_api_key: str,
        extra_headers: Mapping[str, str] | None = None,
    ) -> None:
        """Initialize.

        Args:
            url (str): Connection URL.
            x_api_key (str): Sent as the `x-api-key` header on the upgrade.
            extra_headers (Mapping[str, str] | None): Extra headers merged onto the
                upgrade request.
        """
        self.url: str = url
        headers: dict[str, str] = {}
        headers["x-api-key"] = x_api_key
        if extra_headers is not None:
            headers.update(extra_headers)
        self.headers: dict[str, str] = headers
        self._socket: ClientConnection | None = None

    async def __aenter__(self) -> ZapStream:
        """Open the connection.

        Returns:
            ZapStream: This client, connected.
        """
        self._socket = await connect(self.url, additional_headers=self.headers)
        return self

    async def __aexit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        traceback: TracebackType | None,
    ) -> None:
        """Close the connection.

        Args:
            exc_type (type[BaseException] | None): Raised type, if any.
            exc (BaseException | None): Raised exception, if any.
            traceback (TracebackType | None): Its traceback, if any.
        """
        if self._socket is not None:
            await self._socket.close()
            self._socket = None

    @property
    def socket(self) -> ClientConnection:
        """The live connection.

        Returns:
            ClientConnection: The open socket.

        Raises:
            RuntimeError: When used outside the context manager.
        """
        if self._socket is None:
            raise RuntimeError(
                "ZapStream is not connected — use it as an async context manager."
            )
        return self._socket

    async def send(self, frame: ZapStreamClientFrame) -> None:
        """Put one frame on the wire.

        Args:
            frame (ZapStreamClientFrame): The frame to send.
        """
        payload = frame.model_dump(mode="json", by_alias=True)
        await self.socket.send(json.dumps(payload))

    async def receive(self) -> ZapStreamServerFrame:
        """Read the next frame the server sends.

        Returns:
            ZapStreamServerFrame: The parsed frame.

        Raises:
            ZapStreamFrameError: For a frame whose
                `type` the document does not declare.
        """
        raw: Any = json.loads(await self.socket.recv())
        tag = raw.get("type") if isinstance(raw, dict) else None
        model = _INBOUND_FRAMES.get(tag) if isinstance(tag, str) else None
        if model is None:
            raise ZapStreamFrameError(tag, raw)
        return model.model_validate(raw)

    async def __aiter__(self) -> AsyncIterator[ZapStreamServerFrame]:
        """Iterate the frames the server sends.

        Yields:
            ZapStreamServerFrame: Each parsed frame, until the socket
                closes.
        """
        async for _ in self.socket:
            raw: Any = json.loads(_)
            tag = raw.get("type") if isinstance(raw, dict) else None
            model = _INBOUND_FRAMES.get(tag) if isinstance(tag, str) else None
            if model is None:
                raise ZapStreamFrameError(tag, raw)
            yield model.model_validate(raw)


__all__: list[str] = [
    "DEFAULT_URL",
    "ZapStream",
    "ZapStreamClientFrame",
    "ZapStreamFrameError",
    "ZapStreamServerFrame",
]
