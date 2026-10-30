"""The inbox rules: claim once, status only forward, one row per send.

Every test runs against the in-memory SQLite of the ``session`` fixture,
with the gateway played by an :class:`httpx.MockTransport` that answers
``send-text`` the way zap-api does — ``deduped=True`` when the
``idempotency-key`` repeats — so the rows written are the ones a consumer
would see.
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from typing import Any

import httpx
import pytest
import pytest_asyncio
from sqlalchemy.ext.asyncio import AsyncSession

from tempest_fastapi_sdk import BaseModel, BaseRepository, HTTPClient
from tempest_fastapi_sdk.exceptions import ZapSendFailedException
from tempest_fastapi_sdk.integrations.messaging.zap import (
    ZapClient,
    ZapDeliveryMixin,
    ZapInboundMessage,
    ZapInboundMessageMixin,
    ZapInboundOutcome,
    ZapInbox,
    ZapStatusCallback,
    ZapStatusOutcome,
)

WHEN: str = "2026-04-21T18:30:00.000Z"
"""A fixed ``timestamp`` for every body."""

USER: str = "5511999999999@s.whatsapp.net"
"""A person writing by number."""


class InboxMessageModel(ZapInboundMessageMixin, BaseModel):
    """The consumer's inbound table."""

    __tablename__ = "test_zap_inbox_messages"


class InboxDeliveryModel(ZapDeliveryMixin, BaseModel):
    """The consumer's delivery table."""

    __tablename__ = "test_zap_inbox_deliveries"


class FakeGateway:
    """Plays zap-api's ``send-text``: one outbox row per idempotency key."""

    def __init__(self, status: str = "queued") -> None:
        """Start with no row.

        Args:
            status (str): The ``status`` every ``202`` carries.
        """
        self.status: str = status
        self.rows: dict[str, str] = {}
        self.bodies: list[dict[str, Any]] = []

    def __call__(self, request: httpx.Request) -> httpx.Response:
        """Answer a send.

        Args:
            request (httpx.Request): The call the ``ZapClient`` made.

        Returns:
            httpx.Response: The ``202``, ``deduped`` on a repeated key.
        """
        key: str = request.headers["idempotency-key"]
        deduped: bool = key in self.rows
        if not deduped:
            self.rows[key] = f"out-{len(self.rows) + 1}"
            self.bodies.append(httpx.Response(200, content=request.content).json())
        return httpx.Response(
            202,
            json={"id": self.rows[key], "status": self.status, "deduped": deduped},
        )


def inbound(message_id: str, sender: str = USER, **fields: Any) -> ZapInboundMessage:
    """Build a ``message.received`` body.

    Args:
        message_id (str): The ``messageId``.
        sender (str): The raw ``from`` JID.
        **fields (Any): Extra wire fields (``chatKey``, ``text``, ...).

    Returns:
        ZapInboundMessage: The validated body.
    """
    return ZapInboundMessage.model_validate(
        {
            "event": "message.received",
            "messageId": message_id,
            "from": sender,
            "timestamp": WHEN,
            **fields,
        }
    )


def status(outbound_id: str, value: str, error: str | None = None) -> ZapStatusCallback:
    """Build a status callback body.

    Args:
        outbound_id (str): The outbox row id.
        value (str): The status reported.
        error (str | None): Why it failed.

    Returns:
        ZapStatusCallback: The validated body.
    """
    return ZapStatusCallback.model_validate(
        {
            "event": f"message.{value}",
            "id": outbound_id,
            "consumer": "tests",
            "to": "5511999999999",
            "kind": "text",
            "status": value,
            "error": error,
            "timestamp": WHEN,
        }
    )


@pytest.fixture
def gateway() -> FakeGateway:
    """A gateway accepting every send as ``queued``.

    Returns:
        FakeGateway: The fake.
    """
    return FakeGateway()


@pytest_asyncio.fixture
async def inbox(
    session: AsyncSession, gateway: FakeGateway
) -> AsyncIterator[ZapInbox[InboxMessageModel, InboxDeliveryModel]]:
    """An inbox over the in-memory tables and the fake gateway.

    Args:
        session (AsyncSession): The test session.
        gateway (FakeGateway): The fake gateway.

    Yields:
        ZapInbox[InboxMessageModel, InboxDeliveryModel]: The inbox.
    """
    async with HTTPClient(
        base_url="http://zap", transport=httpx.MockTransport(gateway)
    ) as http:
        yield ZapInbox(
            BaseRepository(session, model=InboxMessageModel),
            BaseRepository(session, model=InboxDeliveryModel),
            ZapClient(http),
        )


class TestClaim:
    """An inbound message is stored once, and only from a person."""

    async def test_text_is_stored(
        self, inbox: ZapInbox[InboxMessageModel, InboxDeliveryModel]
    ) -> None:
        """A first delivery stores the row with every column."""
        outcome = await inbox.claim(
            inbound("A1", chatKey="5511999999999", pushName="Fulano", text="Oi")
        )
        assert outcome is ZapInboundOutcome.STORED
        row = await inbox.messages.get({"message_id": "A1"})
        assert (row.chat_key, row.push_name, row.text) == (
            "5511999999999",
            "Fulano",
            "Oi",
        )

    async def test_redelivery_is_duplicate(
        self, inbox: ZapInbox[InboxMessageModel, InboxDeliveryModel]
    ) -> None:
        """The same ``messageId`` twice is one row and a ``duplicate``."""
        await inbox.claim(inbound("A1", chatKey="5511999999999", text="Oi"))
        outcome = await inbox.claim(inbound("A1", chatKey="5511999999999", text="Oi"))
        assert outcome is ZapInboundOutcome.DUPLICATE
        assert await inbox.messages.count() == 1

    async def test_group_is_skipped(
        self, inbox: ZapInbox[InboxMessageModel, InboxDeliveryModel]
    ) -> None:
        """A group message is not stored."""
        outcome = await inbox.claim(inbound("G1", "120363000000000000@g.us"))
        assert outcome is ZapInboundOutcome.SKIPPED
        assert await inbox.messages.count() == 0

    async def test_media_without_url_is_lost(
        self, inbox: ZapInbox[InboxMessageModel, InboxDeliveryModel]
    ) -> None:
        """Media announced without ``mediaUrl`` is stored as ``media-lost``."""
        outcome = await inbox.claim(
            inbound("A3", chatKey="5511999999999", mediaType="audio")
        )
        assert outcome is ZapInboundOutcome.MEDIA_LOST
        row = await inbox.messages.get({"message_id": "A3"})
        assert (row.media_type, row.text) == ("audio", None)

    async def test_unmapped_lid_is_stored_without_chat_key(
        self, inbox: ZapInbox[InboxMessageModel, InboxDeliveryModel]
    ) -> None:
        """A LID the gateway could not map is stored with ``chat_key`` null."""
        outcome = await inbox.claim(inbound("L1", "145848633667620@lid", text="oi"))
        assert outcome is ZapInboundOutcome.STORED
        row = await inbox.messages.get({"message_id": "L1"})
        assert row.chat_key is None

    async def test_long_text_is_stored_whole(
        self, inbox: ZapInbox[InboxMessageModel, InboxDeliveryModel]
    ) -> None:
        """``text`` has no 255-character ceiling."""
        await inbox.claim(inbound("A5", chatKey="5511999999999", text="a" * 4000))
        row = await inbox.messages.get({"message_id": "A5"})
        assert len(row.text or "") == 4000


class TestTrack:
    """A status only moves forward, and an unknown id is created."""

    async def test_forward_status_is_applied(
        self, inbox: ZapInbox[InboxMessageModel, InboxDeliveryModel]
    ) -> None:
        """``delivered`` over the ``queued`` of the send is applied."""
        accepted = await inbox.send_text(
            "5511999999999", "oi", idempotency_key="reply-A1"
        )
        assert accepted.id is not None
        outcome = await inbox.track(status(accepted.id, "delivered"))
        assert outcome is ZapStatusOutcome.APPLIED
        row = await inbox.deliveries.get({"outbound_id": accepted.id})
        assert row.status == "delivered"

    async def test_late_status_is_stale(
        self, inbox: ZapInbox[InboxMessageModel, InboxDeliveryModel]
    ) -> None:
        """A ``sent`` arriving after the ``read`` does not step down."""
        await inbox.track(status("out-9", "read"))
        outcome = await inbox.track(status("out-9", "sent"))
        assert outcome is ZapStatusOutcome.STALE
        row = await inbox.deliveries.get({"outbound_id": "out-9"})
        assert row.status == "read"

    async def test_unknown_id_creates_the_row(
        self, inbox: ZapInbox[InboxMessageModel, InboxDeliveryModel]
    ) -> None:
        """A callback that beats the send's commit is stored, not refused."""
        outcome = await inbox.track(status("out-7", "sent"))
        assert outcome is ZapStatusOutcome.APPLIED
        row = await inbox.deliveries.get({"outbound_id": "out-7"})
        assert (row.status, row.chat_key, row.idempotency_key) == (
            "sent",
            "5511999999999",
            None,
        )

    async def test_failed_keeps_the_error(
        self, inbox: ZapInbox[InboxMessageModel, InboxDeliveryModel]
    ) -> None:
        """``failed`` over ``sent`` stores why."""
        await inbox.track(status("out-3", "sent"))
        await inbox.track(status("out-3", "failed", error="not on WhatsApp"))
        row = await inbox.deliveries.get({"outbound_id": "out-3"})
        assert (row.status, row.error) == ("failed", "not on WhatsApp")

    async def test_failed_does_not_overwrite_delivered(
        self, inbox: ZapInbox[InboxMessageModel, InboxDeliveryModel]
    ) -> None:
        """A ``delivered`` already proves arrival; ``failed`` is stale."""
        await inbox.track(status("out-4", "delivered"))
        outcome = await inbox.track(status("out-4", "failed", error="late"))
        assert outcome is ZapStatusOutcome.STALE
        row = await inbox.deliveries.get({"outbound_id": "out-4"})
        assert (row.status, row.error) == ("delivered", None)


class RacingRepository(BaseRepository[InboxDeliveryModel]):
    """Repository that misses the row on the first lookups, as a race would.

    Stands for the other replica that inserted the same row between this
    one's ``get_or_none`` and its ``add``.
    """

    def __init__(self, session: AsyncSession, misses: int) -> None:
        """Bind the session and how many lookups come back empty.

        Args:
            session (AsyncSession): The test session.
            misses (int): Lookups answered ``None`` before the real ones.
        """
        super().__init__(session, model=InboxDeliveryModel)
        self.misses: int = misses

    async def get_or_none(
        self,
        filters: dict[str, Any],
        for_update: bool = False,
        with_: list[str] | None = None,
        where: Any = None,
    ) -> InboxDeliveryModel | None:
        """Answer ``None`` for the first ``misses`` calls, then look up.

        Args:
            filters (dict[str, Any]): The lookup.
            for_update (bool): Forwarded.
            with_ (list[str] | None): Forwarded.
            where (Any): Forwarded.

        Returns:
            InboxDeliveryModel | None: ``None`` while racing, then the row.
        """
        if self.misses > 0:
            self.misses -= 1
            return None
        return await super().get_or_none(
            filters, for_update=for_update, with_=with_, where=where
        )


async def racing_inbox(
    session: AsyncSession, http: HTTPClient, misses: int
) -> ZapInbox[InboxMessageModel, InboxDeliveryModel]:
    """Build an inbox whose delivery lookups lose the race ``misses`` times.

    Args:
        session (AsyncSession): The test session.
        http (HTTPClient): The transport to the fake gateway.
        misses (int): Lookups answered ``None``.

    Returns:
        ZapInbox[InboxMessageModel, InboxDeliveryModel]: The inbox.
    """
    return ZapInbox(
        BaseRepository(session, model=InboxMessageModel),
        RacingRepository(session, misses),
        ZapClient(http),
    )


class TestRace:
    """The loser of an insert race re-reads the row instead of failing."""

    async def test_track_loser_applies_the_ladder(
        self, session: AsyncSession, gateway: FakeGateway
    ) -> None:
        """A callback that loses the insert still advances the stored row."""
        async with HTTPClient(
            base_url="http://zap", transport=httpx.MockTransport(gateway)
        ) as http:
            await (await racing_inbox(session, http, 0)).track(status("out-1", "sent"))
            outcome = await (await racing_inbox(session, http, 1)).track(
                status("out-1", "delivered")
            )
            row = await BaseRepository(session, model=InboxDeliveryModel).get(
                {"outbound_id": "out-1"}
            )
        assert outcome is ZapStatusOutcome.APPLIED
        assert row.status == "delivered"

    async def test_send_loser_attaches_its_key(
        self, session: AsyncSession, gateway: FakeGateway
    ) -> None:
        """A send that loses the insert to a callback attaches its key."""
        async with HTTPClient(
            base_url="http://zap", transport=httpx.MockTransport(gateway)
        ) as http:
            await (await racing_inbox(session, http, 0)).track(
                status("out-1", "delivered")
            )
            await (await racing_inbox(session, http, 2)).send_text(
                "5511999999999", "oi", idempotency_key="reply-A1"
            )
            repo = BaseRepository(session, model=InboxDeliveryModel)
            row = await repo.get({"outbound_id": "out-1"})
            count = await repo.count()
        assert (row.status, row.idempotency_key, count) == ("delivered", "reply-A1", 1)


class TestSendText:
    """A send is recorded once per idempotency key."""

    async def test_send_records_the_delivery(
        self,
        inbox: ZapInbox[InboxMessageModel, InboxDeliveryModel],
        gateway: FakeGateway,
    ) -> None:
        """The row is keyed by the ``202``'s id and carries the key."""
        await inbox.send_text(
            "5511999999999", "oi", idempotency_key="reply-A1", reply_to="A1"
        )
        row = await inbox.deliveries.get({"idempotency_key": "reply-A1"})
        assert (row.outbound_id, row.chat_key, row.status) == (
            "out-1",
            "5511999999999",
            "queued",
        )
        assert gateway.bodies == [
            {"to": "5511999999999", "text": "oi", "replyTo": "A1"}
        ]

    async def test_retry_is_one_row(
        self,
        inbox: ZapInbox[InboxMessageModel, InboxDeliveryModel],
        gateway: FakeGateway,
    ) -> None:
        """The retry is ``deduped`` by the gateway and adds no row here."""
        await inbox.send_text("5511999999999", "oi", idempotency_key="reply-A1")
        accepted = await inbox.send_text(
            "5511999999999", "oi", idempotency_key="reply-A1"
        )
        assert accepted.deduped is True
        assert len(gateway.bodies) == 1
        assert await inbox.deliveries.count() == 1

    async def test_failed_send_raises_and_records_nothing(
        self, session: AsyncSession
    ) -> None:
        """A ``202`` already ``failed`` raises and writes no row."""
        async with HTTPClient(
            base_url="http://zap",
            transport=httpx.MockTransport(FakeGateway(status="failed")),
        ) as http:
            inbox: ZapInbox[InboxMessageModel, InboxDeliveryModel] = ZapInbox(
                BaseRepository(session, model=InboxMessageModel),
                BaseRepository(session, model=InboxDeliveryModel),
                ZapClient(http),
            )
            with pytest.raises(ZapSendFailedException) as raised:
                await inbox.send_text("5511999999999", "oi", idempotency_key="k1")
        assert raised.value.status_code == 502
        assert raised.value.details == {"idempotency_key": "k1"}
        assert await inbox.deliveries.count() == 0

    async def test_inline_send_starts_at_sent(self, session: AsyncSession) -> None:
        """A ``202`` with null ``id``/``status`` keys by the key, at ``sent``."""

        def inline(_: httpx.Request) -> httpx.Response:
            """Answer as a gateway without persistence.

            Returns:
                httpx.Response: A ``202`` with ``id`` and ``status`` null.
            """
            return httpx.Response(
                202, json={"id": None, "status": None, "deduped": False}
            )

        async with HTTPClient(
            base_url="http://zap", transport=httpx.MockTransport(inline)
        ) as http:
            inbox: ZapInbox[InboxMessageModel, InboxDeliveryModel] = ZapInbox(
                BaseRepository(session, model=InboxMessageModel),
                BaseRepository(session, model=InboxDeliveryModel),
                ZapClient(http),
            )
            await inbox.send_text("5511999999999", "oi", idempotency_key="k1")
        row = await inbox.deliveries.get({"idempotency_key": "k1"})
        assert (row.outbound_id, row.status) == ("k1", "sent")

    async def test_callback_before_send_is_not_stepped_down(
        self, inbox: ZapInbox[InboxMessageModel, InboxDeliveryModel]
    ) -> None:
        """A ``delivered`` stored first survives the ``queued`` of the send."""
        await inbox.track(status("out-1", "delivered"))
        await inbox.send_text("5511999999999", "oi", idempotency_key="reply-A1")
        row = await inbox.deliveries.get({"outbound_id": "out-1"})
        assert (row.status, row.idempotency_key) == ("delivered", "reply-A1")
        assert await inbox.deliveries.count() == 1
