"""Tests for WebPushSubscriptionService (persist / prune / deliver)."""

from __future__ import annotations

from typing import Any
from uuid import uuid4

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

    async def send_many(
        self,
        subscriptions: list[WebPushSubscriptionSchema],
        payload: Any,
        *,
        ttl_seconds: int | None = None,
        headers: dict[str, str] | None = None,
    ) -> list[str]:
        self.sent = [s.endpoint for s in subscriptions]
        return list(self._gone)


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
