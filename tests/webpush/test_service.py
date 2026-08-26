"""Tests for WebPushSubscriptionService (persist / prune / deliver)."""

from __future__ import annotations

from typing import Any
from uuid import uuid4

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from tempest_fastapi_sdk import (
    BaseRepository,
    BaseUserModel,
    WebPushDispatcher,
    WebPushKeysSchema,
    WebPushSubscriptionSchema,
    WebPushSubscriptionService,
    make_web_push_subscription_model,
)


class _PushUser(BaseUserModel):
    __tablename__ = "push_users"


_PushSubscription = make_web_push_subscription_model(
    user_table="push_users",
    tablename="webpush_subscriptions_test",
    class_name="_PushSubscription",
)


class _FakeDispatcher(WebPushDispatcher):
    """Dispatcher stub that records sends and returns a fixed gone list."""

    def __init__(self, gone: list[str] | None = None) -> None:
        super().__init__("dummy-key", vapid_subject="mailto:ops@example.com")
        self._gone: list[str] = gone or []
        self.sent: list[str] = []
        self.batches: list[list[str]] = []
        self.concurrency_seen: list[int | None] = []

    async def send_many(
        self,
        subscriptions: list[WebPushSubscriptionSchema],
        payload: Any,
        *,
        ttl_seconds: int | None = None,
        headers: dict[str, str] | None = None,
        max_concurrency: int | None = None,
    ) -> list[str]:
        endpoints = [s.endpoint for s in subscriptions]
        self.sent = endpoints
        self.batches.append(endpoints)
        self.concurrency_seen.append(max_concurrency)
        return [e for e in self._gone if e in set(endpoints)]


def _sub(endpoint: str) -> WebPushSubscriptionSchema:
    return WebPushSubscriptionSchema(
        endpoint=endpoint,
        keys=WebPushKeysSchema(p256dh="p256dh-key", auth="auth-secret"),
    )


def _service(
    session: AsyncSession,
    dispatcher: WebPushDispatcher | None = None,
) -> WebPushSubscriptionService[Any]:
    repo: BaseRepository[Any] = BaseRepository(session, model=_PushSubscription)
    return WebPushSubscriptionService(repo, dispatcher or _FakeDispatcher())


class TestSubscribe:
    async def test_creates_row(self, session: AsyncSession) -> None:
        service = _service(session)
        user_id = uuid4()
        row = await service.subscribe(
            user_id, _sub("https://push.example/aaa"), user_agent="Firefox"
        )
        assert row.endpoint == "https://push.example/aaa"
        assert row.user_id == user_id
        assert row.user_agent == "Firefox"

    async def test_is_idempotent_by_endpoint(self, session: AsyncSession) -> None:
        service = _service(session)
        user_id = uuid4()
        first = await service.subscribe(user_id, _sub("https://push.example/dup"))
        second = await service.subscribe(user_id, _sub("https://push.example/dup"))
        assert first.id == second.id
        assert len(await service.list_for_user(user_id)) == 1

    async def test_reassigns_endpoint_to_new_user(self, session: AsyncSession) -> None:
        service = _service(session)
        user_a, user_b = uuid4(), uuid4()
        await service.subscribe(user_a, _sub("https://push.example/move"))
        await service.subscribe(user_b, _sub("https://push.example/move"))
        assert await service.list_for_user(user_a) == []
        assert len(await service.list_for_user(user_b)) == 1


class TestUnsubscribe:
    async def test_removes_existing(self, session: AsyncSession) -> None:
        service = _service(session)
        user_id = uuid4()
        await service.subscribe(user_id, _sub("https://push.example/gone"))
        assert await service.unsubscribe("https://push.example/gone") is True
        assert await service.list_for_user(user_id) == []

    async def test_missing_is_noop(self, session: AsyncSession) -> None:
        service = _service(session)
        assert await service.unsubscribe("https://push.example/never") is False


class TestNotifyUser:
    async def test_delivers_to_all_devices(self, session: AsyncSession) -> None:
        dispatcher = _FakeDispatcher()
        service = _service(session, dispatcher)
        user_id = uuid4()
        await service.subscribe(user_id, _sub("https://push.example/d1"))
        await service.subscribe(user_id, _sub("https://push.example/d2"))
        delivered = await service.notify_user(user_id, {"title": "hi"})
        assert delivered == 2
        assert set(dispatcher.sent) == {
            "https://push.example/d1",
            "https://push.example/d2",
        }

    async def test_prunes_gone_subscriptions(self, session: AsyncSession) -> None:
        dispatcher = _FakeDispatcher(gone=["https://push.example/dead"])
        service = _service(session, dispatcher)
        user_id = uuid4()
        await service.subscribe(user_id, _sub("https://push.example/live"))
        await service.subscribe(user_id, _sub("https://push.example/dead"))
        delivered = await service.notify_user(user_id, {"title": "hi"})
        assert delivered == 1
        remaining = await service.list_for_user(user_id)
        assert [r.endpoint for r in remaining] == ["https://push.example/live"]

    async def test_no_devices_returns_zero(self, session: AsyncSession) -> None:
        service = _service(session)
        assert await service.notify_user(uuid4(), {"title": "hi"}) == 0

    async def test_excludes_given_endpoints(self, session: AsyncSession) -> None:
        dispatcher = _FakeDispatcher()
        service = _service(session, dispatcher)
        user_id = uuid4()
        await service.subscribe(user_id, _sub("https://push.example/self"))
        await service.subscribe(user_id, _sub("https://push.example/other"))
        delivered = await service.notify_user(
            user_id,
            {"title": "hi"},
            exclude_endpoints=["https://push.example/self"],
        )
        assert delivered == 1
        assert dispatcher.sent == ["https://push.example/other"]
        remaining = {r.endpoint for r in await service.list_for_user(user_id)}
        assert remaining == {
            "https://push.example/self",
            "https://push.example/other",
        }


class TestListAll:
    async def test_returns_every_users_rows(self, session: AsyncSession) -> None:
        service = _service(session)
        await service.subscribe(uuid4(), _sub("https://push.example/u1"))
        await service.subscribe(uuid4(), _sub("https://push.example/u2"))
        assert {row.endpoint for row in await service.list_all()} == {
            "https://push.example/u1",
            "https://push.example/u2",
        }

    async def test_empty_table_is_empty_list(self, session: AsyncSession) -> None:
        assert await _service(session).list_all() == []


class TestNotifyAll:
    async def test_delivers_across_users(self, session: AsyncSession) -> None:
        dispatcher = _FakeDispatcher()
        service = _service(session, dispatcher)
        await service.subscribe(uuid4(), _sub("https://push.example/a"))
        await service.subscribe(uuid4(), _sub("https://push.example/b"))
        delivered = await service.notify_all({"title": "maintenance"})
        assert delivered == 2
        assert {e for batch in dispatcher.batches for e in batch} == {
            "https://push.example/a",
            "https://push.example/b",
        }

    async def test_prunes_gone_and_discounts_them(self, session: AsyncSession) -> None:
        dispatcher = _FakeDispatcher(gone=["https://push.example/dead"])
        service = _service(session, dispatcher)
        await service.subscribe(uuid4(), _sub("https://push.example/live"))
        await service.subscribe(uuid4(), _sub("https://push.example/dead"))
        delivered = await service.notify_all({"title": "hi"})
        assert delivered == 1
        assert [row.endpoint for row in await service.list_all()] == [
            "https://push.example/live"
        ]

    async def test_walks_every_row_in_batches(self, session: AsyncSession) -> None:
        """A base larger than one page is fully reached, page by page."""
        dispatcher = _FakeDispatcher()
        service = _service(session, dispatcher)
        for index in range(7):
            await service.subscribe(uuid4(), _sub(f"https://push.example/n{index}"))
        delivered = await service.notify_all({"title": "hi"}, page_size=3)
        assert delivered == 7
        assert [len(batch) for batch in dispatcher.batches] == [3, 3, 1]
        assert {e for batch in dispatcher.batches for e in batch} == {
            f"https://push.example/n{index}" for index in range(7)
        }

    async def test_reaches_every_row_while_pruning(self, session: AsyncSession) -> None:
        """Deleting as it walks must not make the cursor skip a live row."""
        dead = [f"https://push.example/d{index}" for index in range(4)]
        dispatcher = _FakeDispatcher(gone=dead)
        service = _service(session, dispatcher)
        for index in range(4):
            await service.subscribe(uuid4(), _sub(f"https://push.example/d{index}"))
            await service.subscribe(uuid4(), _sub(f"https://push.example/l{index}"))
        delivered = await service.notify_all({"title": "hi"}, page_size=2)
        assert delivered == 4
        assert {row.endpoint for row in await service.list_all()} == {
            f"https://push.example/l{index}" for index in range(4)
        }

    async def test_forwards_concurrency_bound(self, session: AsyncSession) -> None:
        dispatcher = _FakeDispatcher()
        service = _service(session, dispatcher)
        await service.subscribe(uuid4(), _sub("https://push.example/one"))
        await service.notify_all({"title": "hi"})
        assert dispatcher.concurrency_seen == [32]
        await service.notify_all({"title": "hi"}, max_concurrency=None)
        assert dispatcher.concurrency_seen[-1] is None

    async def test_excludes_given_endpoints(self, session: AsyncSession) -> None:
        dispatcher = _FakeDispatcher()
        service = _service(session, dispatcher)
        await service.subscribe(uuid4(), _sub("https://push.example/keep"))
        await service.subscribe(uuid4(), _sub("https://push.example/skip"))
        delivered = await service.notify_all(
            {"title": "hi"},
            exclude_endpoints=["https://push.example/skip"],
        )
        assert delivered == 1
        assert [e for batch in dispatcher.batches for e in batch] == [
            "https://push.example/keep"
        ]
        assert len(await service.list_all()) == 2

    async def test_empty_table_returns_zero(self, session: AsyncSession) -> None:
        dispatcher = _FakeDispatcher()
        service = _service(session, dispatcher)
        assert await service.notify_all({"title": "hi"}) == 0
        assert dispatcher.batches == []

    async def test_page_size_below_one_raises(self, session: AsyncSession) -> None:
        service = _service(session)
        with pytest.raises(ValueError, match="page_size must be >= 1"):
            await service.notify_all({"title": "hi"}, page_size=0)
