"""Pydantic schemas generated from the zap-api WebSocket OpenAPI specification.

Do not edit by hand — rerun `tempest openapi-client` to refresh.

Field names are Python-idiomatic; the wire name is attached as a
Pydantic ``alias`` whenever the two differ, and every model enables
``populate_by_name`` so both spellings are accepted on input. Call
``model_dump(by_alias=True)`` to serialize back to the wire shape.
"""

from __future__ import annotations

from pydantic import ConfigDict, Field

from tempest_fastapi_sdk import BaseSchema, BaseStrEnum


class AckFrameEvent(BaseStrEnum):
    """Allowed values for AckFrameEvent."""

    SUBSCRIBED = "subscribed"
    UNSUBSCRIBED = "unsubscribed"
    ACCEPTED = "accepted"


class AckFrameType(BaseStrEnum):
    """Allowed values for AckFrameType."""

    ACK = "ack"


class ErrorFrameType(BaseStrEnum):
    """Allowed values for ErrorFrameType."""

    ERROR = "error"


class SendFrameAction(BaseStrEnum):
    """Allowed values for SendFrameAction."""

    SEND = "send"


class ServerMessageFramePayloadDirection(BaseStrEnum):
    """Allowed values for ServerMessageFramePayloadDirection."""

    INCOMING = "incoming"
    OUTGOING = "outgoing"


class ServerMessageFrameType(BaseStrEnum):
    """Allowed values for ServerMessageFrameType."""

    MESSAGE = "message"


class SubscribeFrameAction(BaseStrEnum):
    """Allowed values for SubscribeFrameAction."""

    SUBSCRIBE = "subscribe"


class UnsubscribeFrameAction(BaseStrEnum):
    """Allowed values for UnsubscribeFrameAction."""

    UNSUBSCRIBE = "unsubscribe"


class AckFrame(BaseSchema):
    """Schema generated for AckFrame.

    Attributes:
        type (AckFrameType): Undocumented in the spec.
        event (AckFrameEvent): Undocumented in the spec.
        room (str): Undocumented in the spec.
    """

    type: AckFrameType
    event: AckFrameEvent
    room: str = Field(
        min_length=1,
        max_length=128,
        pattern="^(\\*|\\d{10,15}|[A-Za-z0-9._-]+@[A-Za-z0-9.-]+)$",
    )


class ErrorFrame(BaseSchema):
    """Schema generated for ErrorFrame.

    Attributes:
        type (ErrorFrameType): Undocumented in the spec.
        message (str): Undocumented in the spec.
    """

    type: ErrorFrameType
    message: str = Field(min_length=1)


class SendFrame(BaseSchema):
    """Schema generated for SendFrame.

    Attributes:
        action (SendFrameAction): Undocumented in the spec.
        room (str): Undocumented in the spec.
        text (str): Undocumented in the spec.
    """

    action: SendFrameAction
    room: str = Field(
        min_length=1,
        max_length=128,
        pattern="^(\\d{10,15}|[A-Za-z0-9._-]+@[A-Za-z0-9.-]+)$",
    )
    text: str = Field(min_length=1, max_length=4096)


class ServerMessageFramePayload(BaseSchema):
    """Schema generated for ServerMessageFramePayload.

    Attributes:
        remote_jid (str): Undocumented in the spec.
        message_id (str): Undocumented in the spec.
        direction (ServerMessageFramePayloadDirection): Undocumented in the spec.
        text (str | None): Undocumented in the spec.
        media_type (str | None): Undocumented in the spec.
        timestamp (str): Undocumented in the spec.
    """

    model_config = ConfigDict(populate_by_name=True)

    remote_jid: str = Field(
        validation_alias="remoteJid",
        serialization_alias="remoteJid",
    )
    message_id: str = Field(
        validation_alias="messageId",
        serialization_alias="messageId",
    )
    direction: ServerMessageFramePayloadDirection
    text: str | None
    media_type: str | None = Field(
        validation_alias="mediaType",
        serialization_alias="mediaType",
    )
    timestamp: str


class SocketHeaders(BaseSchema):
    """Schema generated for SocketHeaders.

    Attributes:
        x_api_key (str): Consumer API key. Same credential the HTTP routes take.
    """

    model_config = ConfigDict(populate_by_name=True)

    x_api_key: str = Field(
        validation_alias="x-api-key",
        serialization_alias="x-api-key",
        description="Consumer API key. Same credential the HTTP routes take.",
    )


class SubscribeFrame(BaseSchema):
    """Schema generated for SubscribeFrame.

    Attributes:
        action (SubscribeFrameAction): Undocumented in the spec.
        room (str): Undocumented in the spec.
    """

    action: SubscribeFrameAction
    room: str = Field(
        min_length=1,
        max_length=128,
        pattern="^(\\*|\\d{10,15}|[A-Za-z0-9._-]+@[A-Za-z0-9.-]+)$",
    )


class UnsubscribeFrame(BaseSchema):
    """Schema generated for UnsubscribeFrame.

    Attributes:
        action (UnsubscribeFrameAction): Undocumented in the spec.
        room (str): Undocumented in the spec.
    """

    action: UnsubscribeFrameAction
    room: str = Field(
        min_length=1,
        max_length=128,
        pattern="^(\\*|\\d{10,15}|[A-Za-z0-9._-]+@[A-Za-z0-9.-]+)$",
    )


class ServerMessageFrame(BaseSchema):
    """Schema generated for ServerMessageFrame.

    Attributes:
        type (ServerMessageFrameType): Undocumented in the spec.
        payload (ServerMessageFramePayload): Undocumented in the spec.
    """

    type: ServerMessageFrameType
    payload: ServerMessageFramePayload


__all__: list[str] = [
    "AckFrame",
    "AckFrameEvent",
    "AckFrameType",
    "ErrorFrame",
    "ErrorFrameType",
    "SendFrame",
    "SendFrameAction",
    "ServerMessageFrame",
    "ServerMessageFramePayload",
    "ServerMessageFramePayloadDirection",
    "ServerMessageFrameType",
    "SocketHeaders",
    "SubscribeFrame",
    "SubscribeFrameAction",
    "UnsubscribeFrame",
    "UnsubscribeFrameAction",
]
