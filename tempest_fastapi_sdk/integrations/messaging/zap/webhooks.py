"""Verify a zap-api webhook and hand the route a typed delivery.

The gateway's OpenAPI document describes its webhooks in prose only — no
``webhooks`` block, no ``callbacks`` — so nothing here can be generated.
The contract lives in the gateway's code instead, and this module is ported
from it: ``src/utils/signature.ts`` (header and signature format),
``src/services/webhook.service.ts`` (the inbound body) and
``src/services/callback.service.ts`` (the status body).

Both kinds of notification reach the same URL, signed the same way, and
are told apart by ``event``. Without this module every service re-derives
the same mechanical steps: which header carries the HMAC, that it is
computed over the **raw** body, that the comparison must be constant-time,
how to dispatch on ``event``, and that a status may arrive out of order.
"""

from __future__ import annotations

import json
from collections.abc import Callable, Coroutine
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any

from fastapi import Request
from pydantic import ConfigDict, Field, ValidationError

from tempest_fastapi_sdk.api.webhooks import WebhookSignatureVerifier
from tempest_fastapi_sdk.core.enums import BaseStrEnum
from tempest_fastapi_sdk.exceptions import UnauthorizedException
from tempest_fastapi_sdk.integrations.messaging.zap.schemas import (
    AcceptedResponseStatus,
)
from tempest_fastapi_sdk.schemas import BaseSchema

ZAP_WEBHOOK_SIGNATURE_HEADER: str = "x-zap-signature"
"""Header carrying the HMAC-SHA256 signature of the raw body.

Ported from zap-api's ``SIGNATURE_HEADER`` (``src/utils/signature.ts``).
The gateway's README spells it ``X-Zap-Signature``; HTTP header names are
case-insensitive, and the constant keeps the code's spelling.
"""

ZAP_WEBHOOK_SIGNATURE_PREFIX: str = "sha256="
"""Fixed prefix in front of the hex digest in the signature header.

Ported from zap-api's ``signPayload`` (``src/utils/signature.ts``), which
returns ``sha256=<hex>``.
"""

ZAP_INBOUND_EVENT: str = "message.received"
"""``event`` value of an incoming-message notification.

Ported from zap-api's ``INBOUND_EVENT`` (``src/services/webhook.service.ts``).
"""


class ZapWebhookEvent(BaseStrEnum):
    """The ``event`` value the gateway POSTs to a consumer's webhook.

    One URL receives every event, and this is the field that tells them
    apart. :func:`make_zap_webhook_dependency` parses it into
    :attr:`ZapWebhookDelivery.event`, so a route dispatches with ``is``
    instead of comparing against a string literal. ``message.received``
    carries a :class:`ZapInboundMessage`; the other four carry a
    :class:`ZapStatusCallback` and reach only the consumer that sent the
    message.

    Being a :class:`~enum.StrEnum`, every member still equals its wire
    string, so code that compares ``event_name == "message.read"`` keeps
    working.

    Ported from zap-api's ``INBOUND_EVENT`` and the ``message.${CallbackStatus}``
    template of ``CallbackPayload`` (``src/services/callback.service.ts``),
    whose ``CallbackStatus`` is ``"sent" | "delivered" | "read" | "failed"``.

    Attributes:
        MESSAGE_RECEIVED: A message arrived on the WhatsApp session. Sent to
            every webhook subscribed to ``inbound``.
        MESSAGE_SENT: WhatsApp accepted an outbound message.
        MESSAGE_DELIVERED: The recipient's device received it.
        MESSAGE_READ: The recipient opened it.
        MESSAGE_FAILED: The gateway gave up sending it; the reason is in
            :attr:`ZapStatusCallback.error`.

    Examples:
        >>> ZapWebhookEvent("message.read") is ZapWebhookEvent.MESSAGE_READ
        True
        >>> ZapWebhookEvent.MESSAGE_RECEIVED == "message.received"
        True
    """

    MESSAGE_RECEIVED = "message.received"
    MESSAGE_SENT = "message.sent"
    MESSAGE_DELIVERED = "message.delivered"
    MESSAGE_READ = "message.read"
    MESSAGE_FAILED = "message.failed"


class ZapInboundMediaType(BaseStrEnum):
    """The media category of an incoming message.

    The value of :attr:`ZapInboundMessage.media_type`. ``None`` there — not
    a member here — means a plain text message.

    Ported from zap-api's ``NormalizedMediaType``
    (``src/services/message-normalizer.ts``).

    Attributes:
        IMAGE: A photo; :attr:`ZapInboundMessage.text` holds the caption.
        VIDEO: A video; :attr:`ZapInboundMessage.text` holds the caption.
        AUDIO: A voice note or audio file.
        DOCUMENT: Any file sent as a document; the caption, if any, is in
            :attr:`ZapInboundMessage.text`.
        STICKER: A sticker.

    Examples:
        >>> ZapInboundMediaType.AUDIO == "audio"
        True
    """

    IMAGE = "image"
    VIDEO = "video"
    AUDIO = "audio"
    DOCUMENT = "document"
    STICKER = "sticker"


class ZapOutboundKind(BaseStrEnum):
    """What kind of send a status notification is about.

    The value of :attr:`ZapStatusCallback.kind`, one per ``/message/send-*``
    route plus ``reaction``, which goes through the gateway's outbox like
    any other send. Typing presence and read receipts are not here: the
    gateway does not queue them, so they never produce a status.

    Ported from zap-api's ``OutboundKind``
    (``src/db/models/outbound-message.model.ts``).

    Attributes:
        TEXT: ``send_text``.
        IMAGE: ``send_image`` and its base64 / upload variants.
        VIDEO: ``send_video`` and its variants.
        AUDIO: ``send_audio`` and its variants.
        DOCUMENT: ``send_document`` and its variants.
        REACTION: ``react``.

    Examples:
        >>> ZapOutboundKind.REACTION == "reaction"
        True
    """

    TEXT = "text"
    IMAGE = "image"
    VIDEO = "video"
    AUDIO = "audio"
    DOCUMENT = "document"
    REACTION = "reaction"


class ZapJidServer(BaseStrEnum):
    """The server part of a WhatsApp JID — what kind of address it is.

    A JID is ``<user>@<server>``, and the server says whether the address is
    a person's phone number, a privacy-preserving LID, a group or something
    else. :attr:`ZapInboundMessage.from_server` reads it off ``from_``, so a
    route tells a group from a person without ``endswith("@g.us")``.

    The gateway does not filter on the server: its ``publishMessage``
    (``src/services/whatsapp.service.ts``, commit ``d0477d8``) forwards every
    ``notify`` message with content, so a status story
    (``status@broadcast``) or a channel post (``newsletter``) can reach the
    webhook as well. Read in the gateway's code, not measured on a live
    session.

    Ported from Baileys' ``JidServer`` (``WABinary/jid-utils``), read from
    ``@whiskeysockets/baileys`` 7.0.0-rc14 — the version installed under
    zap-api's ``^7.0.0-rc.9`` range at commit ``d0477d8``.

    Attributes:
        USER: A phone number (``<digits>@s.whatsapp.net``) —
            :attr:`ZapInboundMessage.chat_key` holds the digits.
        LID: A LID (``<id>@lid``), WhatsApp's number-hiding address. The
            gateway maps it back to a number when it can.
        GROUP: A group (``<id>@g.us``). ``chat_key`` is ``None``.
        BROADCAST: A broadcast list or a status story
            (``status@broadcast``).
        NEWSLETTER: A WhatsApp channel.
        LEGACY_USER: The legacy ``c.us`` phone address.
        CALL: A call address.
        BOT: A bot account.
        HOSTED: A hosted phone number.
        HOSTED_LID: The LID of a hosted phone number.

    Examples:
        >>> ZapJidServer("g.us") is ZapJidServer.GROUP
        True
    """

    USER = "s.whatsapp.net"
    LID = "lid"
    GROUP = "g.us"
    BROADCAST = "broadcast"
    NEWSLETTER = "newsletter"
    LEGACY_USER = "c.us"
    CALL = "call"
    BOT = "bot"
    HOSTED = "hosted"
    HOSTED_LID = "hosted.lid"


class ZapInboundMessage(BaseSchema):
    """The body of a ``message.received`` notification — one incoming message.

    You do not build this yourself: :func:`make_zap_webhook_dependency`
    validates it and hands it over as :attr:`ZapWebhookDelivery.inbound`.
    What a route usually does with it, in order: dedupe on
    :attr:`message_id`, file it under :attr:`chat_key`, branch on
    :attr:`media_type`, and fetch the bytes with
    :meth:`ZapClient.get_message_media` when there are any.

    Ported from zap-api's ``InboundPayload``
    (``src/services/webhook.service.ts``). Every nullable field defaults to
    ``None`` so a field the gateway stops sending does not reject the
    delivery, and unknown fields are kept (``extra="allow"``).

    Like every :class:`BaseSchema`, enum fields hold the enum's **value**:
    compare ``message.media_type == ZapInboundMediaType.AUDIO``, not with
    ``is``. A :class:`~enum.StrEnum` member equals its string, so both
    spellings of the comparison work.

    ``str_strip_whitespace`` is turned off on purpose: :class:`BaseSchema`
    strips every string, which would rewrite what the person typed
    (leading indentation, a trailing newline) before the service sees it.

    Attributes:
        event (ZapWebhookEvent): Always ``message.received``.
        message_id (str): The WhatsApp message id. The key to dedupe on —
            the gateway retries a delivery until it gets a 2xx, so the same
            message can arrive more than once. It is also the argument
            :meth:`ZapClient.get_message_media` takes.
        from_ (str): The raw remote JID (``<digits>@s.whatsapp.net``, a
            ``@lid``, a ``@g.us`` group, ``status@broadcast``…). An address,
            not a conversation key; :attr:`from_server` says which kind.
        chat_key (str | None): The phone digits that identify the
            conversation, resolved from the LID when the message arrived
            addressed to one. Group by this, never by ``from_``. ``None``
            for a group, a broadcast, a channel, or a LID the gateway could
            not map.
        push_name (str | None): The name the sender set on their own
            profile. Not a verified identity.
        text (str | None): The text, or the media caption.
        media_type (ZapInboundMediaType | None): The media category, or
            ``None`` for a plain text message.
        media_url (str | None): Link to ``GET /message/{messageId}/media``
            on the gateway — absolute when the gateway has
            ``PUBLIC_BASE_URL`` set, root-relative otherwise. ``None`` with
            ``media_type`` set means the download failed or exceeded
            ``MEDIA_MAX_BYTES``, and the media cannot be fetched again.
        timestamp (datetime): When the message was sent, per WhatsApp.

    Examples:
        >>> message = ZapInboundMessage.model_validate(
        ...     {
        ...         "event": "message.received",
        ...         "messageId": "ABCD1234",
        ...         "from": "5511999999999@s.whatsapp.net",
        ...         "chatKey": "5511999999999",
        ...         "text": "Olá!",
        ...         "timestamp": "2026-04-21T18:30:00.000Z",
        ...     }
        ... )
        >>> message.from_server is ZapJidServer.USER
        True
        >>> message.media_type is None
        True
    """

    model_config = ConfigDict(
        populate_by_name=True,
        extra="allow",
        str_strip_whitespace=False,
    )

    event: ZapWebhookEvent
    message_id: str = Field(
        validation_alias="messageId",
        serialization_alias="messageId",
    )
    from_: str = Field(
        validation_alias="from",
        serialization_alias="from",
    )
    chat_key: str | None = Field(
        default=None,
        validation_alias="chatKey",
        serialization_alias="chatKey",
    )
    push_name: str | None = Field(
        default=None,
        validation_alias="pushName",
        serialization_alias="pushName",
    )
    text: str | None = None
    media_type: ZapInboundMediaType | None = Field(
        default=None,
        validation_alias="mediaType",
        serialization_alias="mediaType",
    )
    media_url: str | None = Field(
        default=None,
        validation_alias="mediaUrl",
        serialization_alias="mediaUrl",
    )
    timestamp: datetime

    @property
    def from_server(self) -> ZapJidServer | None:
        """The kind of address :attr:`from_` is, read from its server part.

        Returns:
            ZapJidServer | None: The server after the last ``@`` of
            ``from_``, or ``None`` when there is no ``@`` or the server is
            one Baileys does not define.

        Examples:
            >>> ZapInboundMessage.model_validate(
            ...     {
            ...         "event": "message.received",
            ...         "messageId": "G1",
            ...         "from": "120363000000000000@g.us",
            ...         "timestamp": "2026-04-21T18:30:00.000Z",
            ...     }
            ... ).from_server is ZapJidServer.GROUP
            True
        """
        _, separator, server = self.from_.rpartition("@")
        if not separator or not ZapJidServer.has_value(server):
            return None
        return ZapJidServer(server)


class ZapStatusCallback(BaseSchema):
    """The body of a ``message.{sent,delivered,read,failed}`` notification.

    One step in the life of a message **you** sent. The gateway answers a
    send with ``202`` and an ``id``; every later status of that message
    comes here, carrying the same ``id``. Only the consumer that sent the
    message receives it, and only when its webhook subscribes to
    ``status``.

    Two things a route must assume: the same notification can arrive more
    than once (retried until a 2xx), and notifications can arrive out of
    order (each is retried with its own backoff). Store a status only when
    :func:`is_forward_transition` says it advances what you have.

    Ported from zap-api's ``CallbackPayload``
    (``src/services/callback.service.ts``). Enum fields hold the enum's
    **value**, as in every :class:`BaseSchema` — compare with ``==``.

    Attributes:
        event (ZapWebhookEvent): ``message.sent``, ``message.delivered``,
            ``message.read`` or ``message.failed``.
        id (str): The outbound row id — the ``id`` the ``202`` of the send
            answered. The key to correlate and dedupe on.
        consumer (str): The consumer name the message was sent under.
        to (str): The recipient as the send stored it.
        kind (str): Which send produced the message, one of
            :class:`ZapOutboundKind` (compare
            ``callback.kind == ZapOutboundKind.IMAGE``). Typed ``str``, not
            the enum, on purpose: the gateway types it ``string`` on the
            wire, and a kind it adds later must not turn the status of a
            message into an ``event=None`` delivery.
        status (AcceptedResponseStatus): The status the event reports. The
            gateway only ever sends ``sent``, ``delivered``, ``read`` or
            ``failed`` here; ``queued`` and ``sending`` never arrive on the
            webhook.
        wa_message_id (str | None): The WhatsApp message id, when known.
        error (str | None): Why the send failed, on ``message.failed``.
        timestamp (datetime): When the gateway built the notification — not
            when WhatsApp reported the receipt.

    Examples:
        >>> callback = ZapStatusCallback.model_validate(
        ...     {
        ...         "event": "message.delivered",
        ...         "id": "outbound-uuid",
        ...         "consumer": "billing-api",
        ...         "to": "5511999999999",
        ...         "kind": "text",
        ...         "status": "delivered",
        ...         "timestamp": "2026-06-27T18:30:00.000Z",
        ...     }
        ... )
        >>> callback.status == AcceptedResponseStatus.DELIVERED
        True
        >>> callback.kind == ZapOutboundKind.TEXT
        True
    """

    model_config = ConfigDict(populate_by_name=True, extra="allow")

    event: ZapWebhookEvent
    id: str
    consumer: str
    to: str
    kind: str
    status: AcceptedResponseStatus
    wa_message_id: str | None = Field(
        default=None,
        validation_alias="waMessageId",
        serialization_alias="waMessageId",
    )
    error: str | None = None
    timestamp: datetime


@dataclass(frozen=True, slots=True)
class ZapWebhookDelivery:
    """A verified zap-api webhook delivery — what your route receives.

    :func:`make_zap_webhook_dependency` returns one of these after the
    signature checked out. It holds the event and, already validated, the
    body that event carries, so a route dispatches on ``event`` and reads
    the matching field:

    - ``ZapWebhookEvent.MESSAGE_RECEIVED`` — the message is in ``inbound``;
    - ``MESSAGE_SENT``, ``MESSAGE_DELIVERED``, ``MESSAGE_READ`` and
      ``MESSAGE_FAILED`` — the status is in ``status``;
    - ``None`` — neither is set; see below.

    One invariant makes dispatch total: ``event`` is set **only** when the
    matching typed body parsed. ``MESSAGE_RECEIVED`` always comes with
    ``inbound``, the four status events always come with ``status``, and
    ``event is None`` covers both an event name this SDK does not know and
    a known event whose body did not match the model. Either way, answer
    2xx and log ``event_name`` and ``payload`` — a non-2xx makes the
    gateway retry the same bytes until it gives up.

    A dataclass rather than a :class:`BaseSchema`, for the same reason as
    :class:`OpenPixWebhookEvent`: ``BaseSchema`` stores enums as their
    value, and then ``delivery.event is ZapWebhookEvent.MESSAGE_RECEIVED``
    would be false on every delivery. Here ``event`` is the member itself.

    Attributes:
        event_name (str): The ``event`` field exactly as delivered, kept
            even when it is not a known value.
        event (ZapWebhookEvent | None): The parsed event, or ``None`` as
            described above.
        inbound (ZapInboundMessage | None): The parsed body of a
            ``message.received`` notification.
        status (ZapStatusCallback | None): The parsed body of a status
            notification.
        payload (dict[str, Any]): The whole decoded body.
        body (bytes): The raw body, byte-for-byte as received.

    Examples:
        >>> delivery = ZapWebhookDelivery(event_name="message.edited")
        >>> delivery.event is None
        True
    """

    event_name: str
    event: ZapWebhookEvent | None = None
    inbound: ZapInboundMessage | None = None
    status: ZapStatusCallback | None = None
    payload: dict[str, Any] = field(default_factory=dict)
    body: bytes = b""


def webhook_verifier(secret: str | bytes) -> WebhookSignatureVerifier:
    """Build a verifier configured for zap-api.

    Args:
        secret (str | bytes): The secret registered with the consumer's
            webhook (``npm run webhook:set -- <consumer> <url> --secret
            <secret>`` on the gateway).

    Returns:
        WebhookSignatureVerifier: An HMAC-SHA256 verifier reading
        :data:`ZAP_WEBHOOK_SIGNATURE_HEADER`, hex-encoded, with the
        ``sha256=`` prefix stripped. The comparison is
        :func:`hmac.compare_digest`.
    """
    return WebhookSignatureVerifier(
        secret,
        algorithm="sha256",
        header_name=ZAP_WEBHOOK_SIGNATURE_HEADER,
        encoding="hex",
        prefix=ZAP_WEBHOOK_SIGNATURE_PREFIX,
    )


def _parse(event_name: str, payload: dict[str, Any], body: bytes) -> ZapWebhookDelivery:
    """Turn a verified, decoded body into a delivery.

    Args:
        event_name (str): The ``event`` field as delivered.
        payload (dict[str, Any]): The decoded body (empty when it was not a
            JSON object).
        body (bytes): The raw body.

    Returns:
        ZapWebhookDelivery: The delivery, with ``event`` set only when the
        matching typed body validated.
    """
    if not ZapWebhookEvent.has_value(event_name):
        return ZapWebhookDelivery(event_name=event_name, payload=payload, body=body)
    event: ZapWebhookEvent = ZapWebhookEvent(event_name)
    try:
        if event is ZapWebhookEvent.MESSAGE_RECEIVED:
            inbound = ZapInboundMessage.model_validate(payload)
            return ZapWebhookDelivery(
                event_name=event_name,
                event=event,
                inbound=inbound,
                payload=payload,
                body=body,
            )
        status = ZapStatusCallback.model_validate(payload)
    except ValidationError:
        return ZapWebhookDelivery(event_name=event_name, payload=payload, body=body)
    return ZapWebhookDelivery(
        event_name=event_name,
        event=event,
        status=status,
        payload=payload,
        body=body,
    )


def make_zap_webhook_dependency(
    *,
    secret: str | bytes | None = None,
    verifier: WebhookSignatureVerifier | None = None,
    error_message: str = "Invalid zap-api webhook signature",
) -> Callable[..., Coroutine[Any, Any, ZapWebhookDelivery]]:
    """Build a FastAPI dependency yielding a verified, parsed delivery.

    Args:
        secret (str | bytes | None): The webhook secret. Builds the verifier
            with :func:`webhook_verifier`.
        verifier (WebhookSignatureVerifier | None): A verifier to use instead
            of ``secret`` — for a rotated secret checked by a custom
            verifier, or a test double.
        error_message (str): Message on the raised
            :class:`UnauthorizedException`.

    Returns:
        Callable[..., Coroutine[Any, Any, ZapWebhookDelivery]]: An async
        dependency that verifies the signature over the raw body, decodes
        it, and returns a :class:`ZapWebhookDelivery`.

    Raises:
        ValueError: If both ``secret`` and ``verifier`` are given, or
            neither is, or ``secret`` is empty. The gateway signs a delivery
            only when the webhook was registered with a secret — without
            one it POSTs **unsigned** — and this dependency refuses to be
            built in a shape that would accept that.
        UnauthorizedException: Raised by the returned dependency when the
            signature header is missing or does not verify.

    An **unrecognized event**, a body that does not match its event's model,
    and a body that is **not JSON** do not fail the request: the signature
    verified, so the gateway sent it, and a non-2xx only makes it retry the
    same bytes. Each comes back with ``event`` left ``None``.
    """
    if secret is not None and verifier is not None:
        raise ValueError("pass `secret` or `verifier`, not both")
    if verifier is not None:
        active: WebhookSignatureVerifier = verifier
    elif secret:
        active = webhook_verifier(secret)
    else:
        raise ValueError("a non-empty `secret` (or a `verifier`) is required")

    async def dependency(request: Request) -> ZapWebhookDelivery:
        """Verify and parse the inbound delivery.

        Args:
            request (Request): The inbound request.

        Returns:
            ZapWebhookDelivery: The verified, decoded delivery.

        Raises:
            UnauthorizedException: If the signature is absent or invalid.
        """
        body = await request.body()
        signature = request.headers.get(active.header_name)
        if (
            not signature
            or not signature.startswith(active.prefix)
            or not active.verify(body, signature)
        ):
            raise UnauthorizedException(error_message)

        payload: dict[str, Any] = {}
        try:
            decoded = json.loads(body)
        except (ValueError, UnicodeDecodeError):
            decoded = None
        if isinstance(decoded, dict):
            payload = decoded

        return _parse(str(payload.get("event", "")), payload, body)

    return dependency


_STATUS_RANK: dict[AcceptedResponseStatus, int] = {
    AcceptedResponseStatus.QUEUED: 0,
    AcceptedResponseStatus.SENDING: 1,
    AcceptedResponseStatus.SENT: 2,
    AcceptedResponseStatus.DELIVERED: 3,
    AcceptedResponseStatus.READ: 4,
}
"""Position of each non-failed status on the delivery ladder."""

_FAILABLE: frozenset[AcceptedResponseStatus] = frozenset(
    {
        AcceptedResponseStatus.QUEUED,
        AcceptedResponseStatus.SENDING,
        AcceptedResponseStatus.SENT,
    }
)
"""Statuses a ``failed`` may overwrite."""


def is_forward_transition(
    current: AcceptedResponseStatus | str | None,
    new: AcceptedResponseStatus | str,
) -> bool:
    """Tell whether moving from ``current`` to ``new`` advances the state.

    Args:
        current (AcceptedResponseStatus | str | None): The status the
            service has stored, or ``None`` when it has none yet. A plain
            ``str`` is accepted because a :class:`BaseSchema` field stores
            the enum's value.
        new (AcceptedResponseStatus | str): The status a callback reports.

    Returns:
        bool: ``True`` when the service should apply ``new``.

    Raises:
        ValueError: If either value is not a status.

    The ladder is ``queued < sending < sent < delivered < read``; repeating
    the current status, or stepping down it, is not forward. ``failed`` is
    terminal — nothing overwrites it — and it overwrites only ``queued``,
    ``sending`` and ``sent``, because a ``delivered`` or ``read`` already
    proves the message arrived.

    Ported from zap-api's ``OUTRANKED_BY``
    (``src/db/repositories/outbound-message.repository.ts``), the guard the
    gateway applies to its **own** row. It sends the callback whether or
    not that guard moved the row, so a late ``delivered`` after a ``read``
    still reaches the consumer — and each delivery is retried with its own
    backoff, so a ``sent`` whose first POST failed can land after the
    ``delivered``.
    """
    new_status = AcceptedResponseStatus(new)
    if current is None:
        return True
    current_status = AcceptedResponseStatus(current)
    if current_status is AcceptedResponseStatus.FAILED:
        return False
    if new_status is AcceptedResponseStatus.FAILED:
        return current_status in _FAILABLE
    return _STATUS_RANK[new_status] > _STATUS_RANK[current_status]


__all__: list[str] = [
    "ZAP_INBOUND_EVENT",
    "ZAP_WEBHOOK_SIGNATURE_HEADER",
    "ZAP_WEBHOOK_SIGNATURE_PREFIX",
    "ZapInboundMediaType",
    "ZapInboundMessage",
    "ZapJidServer",
    "ZapOutboundKind",
    "ZapStatusCallback",
    "ZapWebhookDelivery",
    "ZapWebhookEvent",
    "is_forward_transition",
    "make_zap_webhook_dependency",
    "webhook_verifier",
]
