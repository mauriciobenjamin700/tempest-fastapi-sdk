"""The service layer every zap-api consumer writes: claim, track and send.

:class:`ZapInbox` owns the three rules a service receiving the gateway's
webhook has to get right, over two tables the consumer declares with
:class:`ZapInboundMessageMixin` and :class:`ZapDeliveryMixin`:

* **an inbound message is stored once.** Both the inbound fan-out and the
  status callbacks go through the gateway's ``callback_deliveries`` queue,
  with retries: the same ``messageId`` arrives again whenever the answer
  to the first ``POST`` was lost. The ``UNIQUE`` on ``message_id`` is the
  claim — the second insert hits it and the delivery is a ``duplicate``,
  with no in-memory set and no race between replicas;
* **a delivery status only moves forward.** Each callback is retried with
  its own backoff, so a ``sent`` can land after the ``delivered``, and the
  gateway sends the callback even when its own row did not move.
  :func:`~tempest_fastapi_sdk.integrations.messaging.zap.is_forward_transition`
  decides, and the row is upserted by the callback's ``id``, so a callback
  that beats the commit of the send that produced it is stored instead of
  answered with a ``404``;
* **a send is recorded once per idempotency key.** The ``202`` names the
  outbox row the callbacks will carry; a retry with the same key answers
  ``deduped=True`` and must not grow a second row.

Every write goes through :class:`~tempest_fastapi_sdk.BaseRepository`, which
commits, so the rule holds under the request-scoped
``AsyncDatabaseManager.session_dependency`` — which does not commit on its
own.

Examples:

    >>> ZapInboundOutcome.MEDIA_LOST.value
    'media-lost'
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Generic, TypeVar

from sqlalchemy import String, Text
from sqlalchemy.orm import Mapped, mapped_column

from tempest_fastapi_sdk.core.enums import BaseStrEnum
from tempest_fastapi_sdk.db.repository import BaseRepository
from tempest_fastapi_sdk.exceptions.integration import ZapSendFailedException
from tempest_fastapi_sdk.integrations.messaging.zap.client import ZapClient
from tempest_fastapi_sdk.integrations.messaging.zap.schemas import (
    AcceptedResponse,
    AcceptedResponseStatus,
    SendTextRequest,
)
from tempest_fastapi_sdk.integrations.messaging.zap.webhooks import (
    ZapInboundMessage,
    ZapJidServer,
    ZapStatusCallback,
    is_forward_transition,
)

if TYPE_CHECKING:
    from tempest_fastapi_sdk.db.model import BaseModel

    _MixinBase = BaseModel
else:
    _MixinBase = object


class ZapInboundMessageMixin(_MixinBase):
    """Columns for an inbound zap-api message, stored once.

    Mix into a concrete :class:`~tempest_fastapi_sdk.BaseModel` table::

        class ZapMessageModel(BaseModel, ZapInboundMessageMixin):
            __tablename__ = "zap_messages"

    For static typing only, the mixin is declared as a ``BaseModel``
    subclass, so ``BaseRepository[...]`` over it type-checks; at runtime it
    is a plain, unmapped mixin.

    ``text`` is ``Text`` and nullable: a media message without a caption
    has none, and a WhatsApp text has no 255-character ceiling.

    Attributes:
        message_id (str): The WhatsApp message id. ``UNIQUE`` — it is the
            claim :meth:`ZapInbox.claim` relies on.
        chat_key (str | None): The sender's number, digits only, resolved
            by the gateway from a LID. ``None`` for groups, broadcasts,
            channels and an unmapped LID — there is no one to answer.
        push_name (str | None): The sender's display name, when sent.
        text (str | None): The text or caption, when there is one.
        media_type (str | None): The
            :class:`~tempest_fastapi_sdk.integrations.messaging.zap.ZapInboundMediaType`
            value, when the message carries media.
    """

    message_id: Mapped[str] = mapped_column(String(128), unique=True)
    chat_key: Mapped[str | None] = mapped_column(String(32), index=True, default=None)
    push_name: Mapped[str | None] = mapped_column(String(255), default=None)
    text: Mapped[str | None] = mapped_column(Text, default=None)
    media_type: Mapped[str | None] = mapped_column(String(16), default=None)


class ZapDeliveryMixin(_MixinBase):
    """Columns for the last known status of one outbound zap-api message.

    Mix into a concrete :class:`~tempest_fastapi_sdk.BaseModel` table::

        class ZapDeliveryModel(BaseModel, ZapDeliveryMixin):
            __tablename__ = "zap_deliveries"

    For static typing only, the mixin is declared as a ``BaseModel``
    subclass; at runtime it is a plain, unmapped mixin.

    Attributes:
        outbound_id (str): The outbox row id the ``202`` returned, which is
            the ``id`` every status callback carries. ``UNIQUE`` — the key
            :meth:`ZapInbox.track` upserts by.
        chat_key (str): The recipient, digits only.
        idempotency_key (str | None): The key the send went out with.
            ``UNIQUE``, so a retry finds the row it already wrote. ``None``
            on a row a callback created before the send recorded it.
        status (str): The
            :class:`~tempest_fastapi_sdk.integrations.messaging.zap.AcceptedResponseStatus`
            value, only ever moved forward.
        error (str | None): Why the send failed, on ``failed``.
    """

    outbound_id: Mapped[str] = mapped_column(String(64), unique=True)
    chat_key: Mapped[str] = mapped_column(String(32), index=True)
    idempotency_key: Mapped[str | None] = mapped_column(
        String(255), unique=True, default=None
    )
    status: Mapped[str] = mapped_column(String(16))
    error: Mapped[str | None] = mapped_column(Text, default=None)


class ZapInboundOutcome(BaseStrEnum):
    """What :meth:`ZapInbox.claim` did with an inbound message.

    Attributes:
        STORED: Stored for the first time.
        DUPLICATE: Already stored — a re-delivery of the same ``messageId``.
        MEDIA_LOST: Stored, but it announced media and came without
            ``mediaUrl``: the gateway failed to download it, or it went over
            ``MEDIA_MAX_BYTES``. WhatsApp does not serve it again.
        SKIPPED: Written from a server outside ``accepted_servers`` (a
            group, a broadcast, a channel by default) and not stored.
    """

    STORED = "stored"
    DUPLICATE = "duplicate"
    MEDIA_LOST = "media-lost"
    SKIPPED = "skipped"


class ZapStatusOutcome(BaseStrEnum):
    """What :meth:`ZapInbox.track` did with a status callback.

    Attributes:
        APPLIED: The status advanced the stored one, or created the row.
        STALE: The status repeats or steps down what is stored, and was
            ignored.
    """

    APPLIED = "applied"
    STALE = "stale"


InboundT = TypeVar("InboundT", bound=ZapInboundMessageMixin)
"""The consumer's inbound message model."""

DeliveryT = TypeVar("DeliveryT", bound=ZapDeliveryMixin)
"""The consumer's delivery model."""

DEFAULT_ACCEPTED_SERVERS: frozenset[ZapJidServer] = frozenset(
    {ZapJidServer.USER, ZapJidServer.LID}
)
"""The servers a one-to-one conversation is written from."""


class ZapInbox(Generic[InboundT, DeliveryT]):
    """Store inbound messages once, track delivery status, record sends.

    Build one per request, over repositories bound to the request's session
    and the :class:`ZapClient` the ``lifespan`` opened. Deciding *whether*
    to answer a message stays with the consumer: ``claim`` only says what
    happened to it.
    """

    def __init__(
        self,
        messages: BaseRepository[InboundT],
        deliveries: BaseRepository[DeliveryT],
        client: ZapClient,
        *,
        accepted_servers: frozenset[ZapJidServer] = DEFAULT_ACCEPTED_SERVERS,
    ) -> None:
        """Bind the inbox to its tables and to the gateway.

        Args:
            messages (BaseRepository[InboundT]): Repository over the table
                mixing in :class:`ZapInboundMessageMixin`.
            deliveries (BaseRepository[DeliveryT]): Repository over the
                table mixing in :class:`ZapDeliveryMixin`.
            client (ZapClient): The gateway client.
            accepted_servers (frozenset[ZapJidServer]): The address kinds
                :meth:`claim` stores. Defaults to a person writing by number
                or by LID.
        """
        self.messages: BaseRepository[InboundT] = messages
        self.deliveries: BaseRepository[DeliveryT] = deliveries
        self.client: ZapClient = client
        self.accepted_servers: frozenset[ZapJidServer] = accepted_servers

    async def claim(self, message: ZapInboundMessage) -> ZapInboundOutcome:
        """Store an inbound message once, and say what happened to it.

        No ``exists`` check precedes the insert: two replicas checking at
        the same time would both see nothing and both store. The ``UNIQUE``
        on ``message_id`` decides, and the loser gets ``duplicate``. A
        conflict whose ``message_id`` is not stored is not a duplicate
        (another constraint on the consumer's table refused the row), so it
        propagates.

        The media is not downloaded here: it stays on the gateway, and
        ``client.get_message_media(message.message_id)`` reads it when the
        consumer needs the bytes. ``chat_key`` is stored even when ``None``.

        Args:
            message (ZapInboundMessage): The verified inbound body.

        Returns:
            ZapInboundOutcome: ``stored``, ``duplicate``, ``media-lost`` or
            ``skipped``.

        Raises:
            AppException: The repository's conflict exception, when the
                insert broke a constraint other than the claim.
        """
        if message.from_server not in self.accepted_servers:
            return ZapInboundOutcome.SKIPPED
        row: InboundT = self.messages.map_to_model(
            {
                "message_id": message.message_id,
                "chat_key": message.chat_key,
                "push_name": message.push_name,
                "text": message.text,
                "media_type": message.media_type,
            }
        )
        try:
            await self.messages.add(row)
        except self.messages.create_conflict_exception:
            if await self.messages.exists({"message_id": message.message_id}):
                return ZapInboundOutcome.DUPLICATE
            raise
        if message.media_type is not None and message.media_url is None:
            return ZapInboundOutcome.MEDIA_LOST
        return ZapInboundOutcome.STORED

    async def track(self, callback: ZapStatusCallback) -> ZapStatusOutcome:
        """Apply a status callback when it advances the stored status.

        Upserts by ``callback.id``. A callback for an id with no row creates
        it: the gateway can call back before the consumer committed the
        send, and a ``404`` would only spend the gateway's retries. Two
        callbacks racing to create the same row collide on the ``UNIQUE``;
        the loser re-reads the row and applies the ladder against it.

        Args:
            callback (ZapStatusCallback): The verified status body.

        Returns:
            ZapStatusOutcome: ``applied`` or ``stale``.

        Raises:
            AppException: The repository's conflict exception, when the
                insert broke a constraint other than ``outbound_id``.
        """
        row: DeliveryT | None = await self.deliveries.get_or_none(
            {"outbound_id": callback.id}
        )
        if row is None:
            try:
                await self.deliveries.add(
                    self.deliveries.map_to_model(
                        {
                            "outbound_id": callback.id,
                            "chat_key": callback.to,
                            "status": callback.status,
                            "error": callback.error,
                        }
                    )
                )
                return ZapStatusOutcome.APPLIED
            except self.deliveries.create_conflict_exception:
                row = await self.deliveries.get_or_none({"outbound_id": callback.id})
                if row is None:
                    raise
        return await self._advance(row, callback.status, callback.error)

    async def send_text(
        self,
        to: str,
        text: str,
        *,
        idempotency_key: str,
        reply_to: str | None = None,
    ) -> AcceptedResponse:
        """Send a text and record its delivery row, once per key.

        Reuse the same ``idempotency_key`` when retrying the same message:
        the gateway answers the original row with ``deduped=True``, and the
        row found by the key here is updated instead of duplicated. When
        :meth:`track` already created the row from a callback, the send
        attaches its key and never steps the status down.

        A gateway running without persistence answers ``202`` with ``id``
        and ``status`` null; the row is then keyed by ``idempotency_key``
        and starts at ``queued``, and no callback will ever move it.

        Args:
            to (str): The recipient, digits only (``chat_key``, not
                ``from_``).
            text (str): The message.
            idempotency_key (str): Fresh per message, reused on retry.
            reply_to (str | None): The ``messageId`` being quoted.

        Returns:
            AcceptedResponse: The gateway's ``202``.

        Raises:
            ZapSendFailedException: When the ``202`` already says
                ``failed``. Nothing is recorded.
            httpx.HTTPStatusError: For a non-2xx answer from the gateway.
        """
        accepted: AcceptedResponse = await self.client.send_text(
            body=SendTextRequest(to=to, text=text, reply_to=reply_to),
            idempotency_key=idempotency_key,
        )
        if accepted.status == AcceptedResponseStatus.FAILED:
            raise ZapSendFailedException(idempotency_key)
        outbound_id: str = accepted.id or idempotency_key
        status: str = accepted.status or AcceptedResponseStatus.QUEUED
        row: DeliveryT | None = await self.deliveries.get_or_none(
            {"idempotency_key": idempotency_key}
        )
        if row is None:
            row = await self.deliveries.get_or_none({"outbound_id": outbound_id})
        if row is None:
            try:
                await self.deliveries.add(
                    self.deliveries.map_to_model(
                        {
                            "outbound_id": outbound_id,
                            "chat_key": to,
                            "idempotency_key": idempotency_key,
                            "status": status,
                        }
                    )
                )
                return accepted
            except self.deliveries.create_conflict_exception:
                row = await self.deliveries.get_or_none({"outbound_id": outbound_id})
                if row is None:
                    raise
        if row.idempotency_key is None:
            row.idempotency_key = idempotency_key
            await self.deliveries.update(row)
        await self._advance(row, status, None)
        return accepted

    async def _advance(
        self, row: DeliveryT, status: str, error: str | None
    ) -> ZapStatusOutcome:
        """Move ``row`` to ``status`` when the ladder allows, and commit.

        Args:
            row (DeliveryT): The stored delivery.
            status (str): The status reported.
            error (str | None): Why it failed, kept only on ``failed``.

        Returns:
            ZapStatusOutcome: ``applied`` or ``stale``.
        """
        if not is_forward_transition(row.status, status):
            return ZapStatusOutcome.STALE
        row.status = status
        if status == AcceptedResponseStatus.FAILED:
            row.error = error
        await self.deliveries.update(row)
        return ZapStatusOutcome.APPLIED


__all__: list[str] = [
    "DEFAULT_ACCEPTED_SERVERS",
    "DeliveryT",
    "InboundT",
    "ZapDeliveryMixin",
    "ZapInboundMessageMixin",
    "ZapInboundOutcome",
    "ZapInbox",
    "ZapStatusOutcome",
]
