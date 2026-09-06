"""Pydantic schemas generated from the zap-api OpenAPI specification.

Do not edit by hand — rerun `tempest openapi-client` to refresh.

Field names are Python-idiomatic; the wire name is attached as a
Pydantic ``alias`` whenever the two differ, and every model enables
``populate_by_name`` so both spellings are accepted on input. Call
``model_dump(by_alias=True)`` to serialize back to the wire shape.
"""

from __future__ import annotations

from datetime import datetime

from pydantic import ConfigDict, Field

from tempest_fastapi_sdk import BaseSchema, BaseStrEnum


class AcceptedResponseStatus(BaseStrEnum):
    """Allowed values for AcceptedResponseStatus."""

    QUEUED = "queued"
    SENDING = "sending"
    SENT = "sent"
    DELIVERED = "delivered"
    READ = "read"
    FAILED = "failed"


class HistoryMessageDirection(BaseStrEnum):
    """Allowed values for HistoryMessageDirection."""

    INCOMING = "incoming"
    OUTGOING = "outgoing"


class SessionStatusResponseStatus(BaseStrEnum):
    """Allowed values for SessionStatusResponseStatus."""

    DISCONNECTED = "disconnected"
    CONNECTING = "connecting"
    CONNECTED = "connected"


class TypingRequestState(BaseStrEnum):
    """Allowed values for TypingRequestState."""

    COMPOSING = "composing"
    RECORDING = "recording"
    PAUSED = "paused"


class AcceptedResponse(BaseSchema):
    """Schema generated for AcceptedResponse.

    Attributes:
        id (str | None): Undocumented in the spec.
        status (AcceptedResponseStatus | None): Undocumented in the spec.
        deduped (bool): Undocumented in the spec.
    """

    model_config = ConfigDict(extra="allow")

    id: str | None
    status: AcceptedResponseStatus | None
    deduped: bool


class CheckNumberResponse(BaseSchema):
    """Schema generated for CheckNumberResponse.

    Attributes:
        number (str): Undocumented in the spec.
        exists (bool): Undocumented in the spec.
        jid (str | None): Undocumented in the spec.
    """

    model_config = ConfigDict(extra="allow")

    number: str
    exists: bool
    jid: str | None


class ErrorResponse(BaseSchema):
    """Schema generated for ErrorResponse.

    Attributes:
        error (str): Undocumented in the spec.
    """

    error: str


class HistoryMessage(BaseSchema):
    """Schema generated for HistoryMessage.

    Attributes:
        message_id (str): Undocumented in the spec.
        direction (HistoryMessageDirection): Undocumented in the spec.
        text (str | None): Undocumented in the spec.
        media_type (str | None): Undocumented in the spec.
        timestamp (datetime): Undocumented in the spec.
    """

    model_config = ConfigDict(populate_by_name=True, extra="allow")

    message_id: str = Field(
        validation_alias="messageId",
        serialization_alias="messageId",
    )
    direction: HistoryMessageDirection
    text: str | None
    media_type: str | None = Field(
        validation_alias="mediaType",
        serialization_alias="mediaType",
    )
    timestamp: datetime


class QrResponse(BaseSchema):
    """Schema generated for QrResponse.

    Attributes:
        qr (str): Undocumented in the spec.
    """

    model_config = ConfigDict(extra="allow")

    qr: str


class ReactionRequest(BaseSchema):
    """Schema generated for ReactionRequest.

    Attributes:
        to (str): Undocumented in the spec.
        message_id (str): Undocumented in the spec.
        emoji (str): Undocumented in the spec.
    """

    model_config = ConfigDict(populate_by_name=True)

    to: str = Field(
        min_length=1,
        max_length=128,
        pattern="^\\d{10,15}(@s\\.whatsapp\\.net)?$",
    )
    message_id: str = Field(
        validation_alias="messageId",
        serialization_alias="messageId",
        min_length=1,
        max_length=128,
    )
    emoji: str = Field(max_length=16)


class ReadRequest(BaseSchema):
    """Schema generated for ReadRequest.

    Attributes:
        to (str): Undocumented in the spec.
        message_ids (list[str]): Undocumented in the spec.
    """

    model_config = ConfigDict(populate_by_name=True)

    to: str = Field(
        min_length=1,
        max_length=128,
        pattern="^\\d{10,15}(@s\\.whatsapp\\.net)?$",
    )
    message_ids: list[str] = Field(
        validation_alias="messageIds",
        serialization_alias="messageIds",
        min_length=1,
        max_length=100,
    )


class SendAudioBase64Request(BaseSchema):
    """Schema generated for SendAudioBase64Request.

    Attributes:
        to (str): Undocumented in the spec.
        reply_to (str | None): Undocumented in the spec.
        data (str): File contents, base64-encoded. A `data:` URL prefix is accepted.
    """

    model_config = ConfigDict(populate_by_name=True)

    to: str = Field(pattern="^\\d{10,15}$")
    reply_to: str | None = Field(
        validation_alias="replyTo",
        serialization_alias="replyTo",
        min_length=1,
        max_length=128,
        default=None,
    )
    data: str = Field(
        description="File contents, base64-encoded. A `data:` URL prefix is accepted.",
        min_length=1,
        max_length=14000000,
    )


class SendAudioRequest(BaseSchema):
    """Schema generated for SendAudioRequest.

    Attributes:
        to (str): Undocumented in the spec.
        reply_to (str | None): Undocumented in the spec.
        media (str): Undocumented in the spec.
    """

    model_config = ConfigDict(populate_by_name=True)

    to: str = Field(pattern="^\\d{10,15}$")
    reply_to: str | None = Field(
        validation_alias="replyTo",
        serialization_alias="replyTo",
        min_length=1,
        max_length=128,
        default=None,
    )
    media: str = Field(min_length=1, max_length=8192)


class SendDocumentBase64Request(BaseSchema):
    """Schema generated for SendDocumentBase64Request.

    Attributes:
        to (str): Undocumented in the spec.
        reply_to (str | None): Undocumented in the spec.
        data (str): File contents, base64-encoded. A `data:` URL prefix is accepted.
        file_name (str): Undocumented in the spec.
    """

    model_config = ConfigDict(populate_by_name=True)

    to: str = Field(pattern="^\\d{10,15}$")
    reply_to: str | None = Field(
        validation_alias="replyTo",
        serialization_alias="replyTo",
        min_length=1,
        max_length=128,
        default=None,
    )
    data: str = Field(
        description="File contents, base64-encoded. A `data:` URL prefix is accepted.",
        min_length=1,
        max_length=14000000,
    )
    file_name: str = Field(
        validation_alias="fileName",
        serialization_alias="fileName",
        min_length=1,
        max_length=255,
        pattern="^[^\\\\/\\x00]+$",
    )


class SendDocumentRequest(BaseSchema):
    """Schema generated for SendDocumentRequest.

    Attributes:
        to (str): Undocumented in the spec.
        reply_to (str | None): Undocumented in the spec.
        media (str): Undocumented in the spec.
        file_name (str): Undocumented in the spec.
    """

    model_config = ConfigDict(populate_by_name=True)

    to: str = Field(pattern="^\\d{10,15}$")
    reply_to: str | None = Field(
        validation_alias="replyTo",
        serialization_alias="replyTo",
        min_length=1,
        max_length=128,
        default=None,
    )
    media: str = Field(min_length=1, max_length=8192)
    file_name: str = Field(
        validation_alias="fileName",
        serialization_alias="fileName",
        min_length=1,
        max_length=255,
        pattern="^[^\\\\/\\x00]+$",
    )


class SendImageBase64Request(BaseSchema):
    """Schema generated for SendImageBase64Request.

    Attributes:
        to (str): Undocumented in the spec.
        reply_to (str | None): Undocumented in the spec.
        data (str): File contents, base64-encoded. A `data:` URL prefix is accepted.
        caption (str | None): Undocumented in the spec.
    """

    model_config = ConfigDict(populate_by_name=True)

    to: str = Field(pattern="^\\d{10,15}$")
    reply_to: str | None = Field(
        validation_alias="replyTo",
        serialization_alias="replyTo",
        min_length=1,
        max_length=128,
        default=None,
    )
    data: str = Field(
        description="File contents, base64-encoded. A `data:` URL prefix is accepted.",
        min_length=1,
        max_length=14000000,
    )
    caption: str | None = Field(max_length=4096, default=None)


class SendImageRequest(BaseSchema):
    """Schema generated for SendImageRequest.

    Attributes:
        to (str): Undocumented in the spec.
        reply_to (str | None): Undocumented in the spec.
        media (str): Undocumented in the spec.
        caption (str | None): Undocumented in the spec.
    """

    model_config = ConfigDict(populate_by_name=True)

    to: str = Field(pattern="^\\d{10,15}$")
    reply_to: str | None = Field(
        validation_alias="replyTo",
        serialization_alias="replyTo",
        min_length=1,
        max_length=128,
        default=None,
    )
    media: str = Field(min_length=1, max_length=8192)
    caption: str | None = Field(max_length=4096, default=None)


class SendTextRequest(BaseSchema):
    """Schema generated for SendTextRequest.

    Attributes:
        to (str): Undocumented in the spec.
        reply_to (str | None): Undocumented in the spec.
        text (str): Undocumented in the spec.
    """

    model_config = ConfigDict(populate_by_name=True)

    to: str = Field(pattern="^\\d{10,15}$")
    reply_to: str | None = Field(
        validation_alias="replyTo",
        serialization_alias="replyTo",
        min_length=1,
        max_length=128,
        default=None,
    )
    text: str = Field(min_length=1, max_length=4096)


class SendVideoBase64Request(BaseSchema):
    """Schema generated for SendVideoBase64Request.

    Attributes:
        to (str): Undocumented in the spec.
        reply_to (str | None): Undocumented in the spec.
        data (str): File contents, base64-encoded. A `data:` URL prefix is accepted.
        caption (str | None): Undocumented in the spec.
    """

    model_config = ConfigDict(populate_by_name=True)

    to: str = Field(pattern="^\\d{10,15}$")
    reply_to: str | None = Field(
        validation_alias="replyTo",
        serialization_alias="replyTo",
        min_length=1,
        max_length=128,
        default=None,
    )
    data: str = Field(
        description="File contents, base64-encoded. A `data:` URL prefix is accepted.",
        min_length=1,
        max_length=14000000,
    )
    caption: str | None = Field(max_length=4096, default=None)


class SendVideoRequest(BaseSchema):
    """Schema generated for SendVideoRequest.

    Attributes:
        to (str): Undocumented in the spec.
        reply_to (str | None): Undocumented in the spec.
        media (str): Undocumented in the spec.
        caption (str | None): Undocumented in the spec.
    """

    model_config = ConfigDict(populate_by_name=True)

    to: str = Field(pattern="^\\d{10,15}$")
    reply_to: str | None = Field(
        validation_alias="replyTo",
        serialization_alias="replyTo",
        min_length=1,
        max_length=128,
        default=None,
    )
    media: str = Field(min_length=1, max_length=8192)
    caption: str | None = Field(max_length=4096, default=None)


class SessionStartResponse(BaseSchema):
    """Schema generated for SessionStartResponse.

    Attributes:
        status (SessionStatusResponseStatus): Undocumented in the spec.
        qr (str | None): Undocumented in the spec.
    """

    model_config = ConfigDict(extra="allow")

    status: SessionStatusResponseStatus
    qr: str | None


class SessionStatusResponse(BaseSchema):
    """Schema generated for SessionStatusResponse.

    Attributes:
        status (SessionStatusResponseStatus): Undocumented in the spec.
    """

    model_config = ConfigDict(extra="allow")

    status: SessionStatusResponseStatus


class TypingRequest(BaseSchema):
    """Schema generated for TypingRequest.

    Attributes:
        to (str): Undocumented in the spec.
        state (TypingRequestState): Undocumented in the spec.
    """

    to: str = Field(
        min_length=1,
        max_length=128,
        pattern="^\\d{10,15}(@s\\.whatsapp\\.net)?$",
    )
    state: TypingRequestState


class UploadAudioForm(BaseSchema):
    """Schema generated for UploadAudioForm.

    Attributes:
        to (str): Recipient phone number, digits only
        file (bytes): The file itself — the form's only file part
        reply_to (str | None): Message id being replied to, quoted above this one
    """

    model_config = ConfigDict(populate_by_name=True)

    to: str = Field(
        description="Recipient phone number, digits only",
        pattern="^\\d{10,15}$",
    )
    file: bytes = Field(description="The file itself — the form's only file part")
    reply_to: str | None = Field(
        validation_alias="replyTo",
        serialization_alias="replyTo",
        description="Message id being replied to, quoted above this one",
        max_length=128,
        default=None,
    )


class UploadDocumentForm(BaseSchema):
    """Schema generated for UploadDocumentForm.

    Attributes:
        to (str): Recipient phone number, digits only
        file (bytes): The file itself — the form's only file part
        file_name (str | None): Name the recipient sees. Falls back to the uploaded
            part's own file name.
        reply_to (str | None): Message id being replied to, quoted above this one
    """

    model_config = ConfigDict(populate_by_name=True)

    to: str = Field(
        description="Recipient phone number, digits only",
        pattern="^\\d{10,15}$",
    )
    file: bytes = Field(description="The file itself — the form's only file part")
    file_name: str | None = Field(
        validation_alias="fileName",
        serialization_alias="fileName",
        description=(
            "Name the recipient sees. Falls back to the uploaded part's own file name."
        ),
        max_length=255,
        default=None,
    )
    reply_to: str | None = Field(
        validation_alias="replyTo",
        serialization_alias="replyTo",
        description="Message id being replied to, quoted above this one",
        max_length=128,
        default=None,
    )


class UploadImageForm(BaseSchema):
    """Schema generated for UploadImageForm.

    Attributes:
        to (str): Recipient phone number, digits only
        file (bytes): The file itself — the form's only file part
        caption (str | None): Undocumented in the spec.
        reply_to (str | None): Message id being replied to, quoted above this one
    """

    model_config = ConfigDict(populate_by_name=True)

    to: str = Field(
        description="Recipient phone number, digits only",
        pattern="^\\d{10,15}$",
    )
    file: bytes = Field(description="The file itself — the form's only file part")
    caption: str | None = Field(max_length=4096, default=None)
    reply_to: str | None = Field(
        validation_alias="replyTo",
        serialization_alias="replyTo",
        description="Message id being replied to, quoted above this one",
        max_length=128,
        default=None,
    )


class UploadVideoForm(BaseSchema):
    """Schema generated for UploadVideoForm.

    Attributes:
        to (str): Recipient phone number, digits only
        file (bytes): The file itself — the form's only file part
        caption (str | None): Undocumented in the spec.
        reply_to (str | None): Message id being replied to, quoted above this one
    """

    model_config = ConfigDict(populate_by_name=True)

    to: str = Field(
        description="Recipient phone number, digits only",
        pattern="^\\d{10,15}$",
    )
    file: bytes = Field(description="The file itself — the form's only file part")
    caption: str | None = Field(max_length=4096, default=None)
    reply_to: str | None = Field(
        validation_alias="replyTo",
        serialization_alias="replyTo",
        description="Message id being replied to, quoted above this one",
        max_length=128,
        default=None,
    )


class HistoryResponse(BaseSchema):
    """Schema generated for HistoryResponse.

    Attributes:
        chat (str): Undocumented in the spec.
        messages (list[HistoryMessage]): Undocumented in the spec.
    """

    model_config = ConfigDict(extra="allow")

    chat: str
    messages: list[HistoryMessage]


__all__: list[str] = [
    "AcceptedResponse",
    "AcceptedResponseStatus",
    "CheckNumberResponse",
    "ErrorResponse",
    "HistoryMessage",
    "HistoryMessageDirection",
    "HistoryResponse",
    "QrResponse",
    "ReactionRequest",
    "ReadRequest",
    "SendAudioBase64Request",
    "SendAudioRequest",
    "SendDocumentBase64Request",
    "SendDocumentRequest",
    "SendImageBase64Request",
    "SendImageRequest",
    "SendTextRequest",
    "SendVideoBase64Request",
    "SendVideoRequest",
    "SessionStartResponse",
    "SessionStatusResponse",
    "SessionStatusResponseStatus",
    "TypingRequest",
    "TypingRequestState",
    "UploadAudioForm",
    "UploadDocumentForm",
    "UploadImageForm",
    "UploadVideoForm",
]
