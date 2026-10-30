"""Revocation must stick against a ``resolve`` already in flight.

``SessionAuth.resolve`` reads the session and writes the refreshed copy
back. Before ``SessionStore.touch`` existed that write was a plain
``set``, so a ``revoke`` / ``revoke_all`` landing between the read and
the write was undone: the key and its user-index entry came back with a
fresh TTL. These tests force that interleaving with a gate (the write
waits until the revocation has returned) and with a client that yields
to the event loop on every command, like a socket round trip.
"""

from __future__ import annotations

import asyncio
import warnings
from collections.abc import Awaitable, Callable
from datetime import timedelta
from typing import Any
from uuid import UUID, uuid4

import pytest

from tempest_fastapi_sdk import (
    MemorySessionStore,
    RedisSessionStore,
    Session,
    SessionAuth,
    SessionSettings,
)
from tempest_fastapi_sdk.utils.datetime import utcnow

fake_aioredis = pytest.importorskip("fakeredis.aioredis")


def _settings() -> SessionSettings:
    """Build session settings with sliding refresh on.

    Returns:
        SessionSettings: Settings for the service under test.
    """
    return SessionSettings(
        SESSION_TTL_SECONDS=60,
        SESSION_COOKIE_SECURE=False,
        SESSION_SLIDING=True,
    )


def _auth(store: Any) -> SessionAuth:
    """Build a credential-backed ``SessionAuth`` over ``store``.

    Args:
        store (Any): The session store under test.

    Returns:
        SessionAuth: The service.
    """
    return SessionAuth.from_credentials("root", "pw", store=store, settings=_settings())


class _GatedRedis:
    """Proxy a fakeredis client and hold every ``SET`` while armed.

    Attributes:
        armed (bool): Whether the next ``SET`` waits for ``gate``.
        gate (asyncio.Event): Released by the test after the revocation.
        reached (asyncio.Event): Set when a ``SET`` is waiting on the gate.
    """

    def __init__(self, inner: Any) -> None:
        """Wrap ``inner``.

        Args:
            inner (Any): The fakeredis client.
        """
        self._inner: Any = inner
        self.armed: bool = False
        self.gate: asyncio.Event = asyncio.Event()
        self.reached: asyncio.Event = asyncio.Event()

    async def set(self, *args: Any, **kwargs: Any) -> Any:
        """Forward ``SET``, waiting on ``gate`` while armed.

        Args:
            *args (Any): Forwarded positional arguments.
            **kwargs (Any): Forwarded keyword arguments.

        Returns:
            Any: The client's reply.
        """
        if self.armed:
            self.reached.set()
            await self.gate.wait()
        return await self._inner.set(*args, **kwargs)

    def __getattr__(self, name: str) -> Any:
        """Delegate every other command to the wrapped client.

        Args:
            name (str): Attribute name.

        Returns:
            Any: The wrapped client's attribute.
        """
        return getattr(self._inner, name)


class _YieldingRedis:
    """Proxy a fakeredis client, yielding to the loop around each command."""

    def __init__(self, inner: Any) -> None:
        """Wrap ``inner``.

        Args:
            inner (Any): The fakeredis client.
        """
        self._inner: Any = inner

    def __getattr__(self, name: str) -> Any:
        """Return an awaitable wrapper around the wrapped client's command.

        Args:
            name (str): Command name.

        Returns:
            Any: The wrapped command, yielding before and after it runs.
        """
        attr = getattr(self._inner, name)
        if not callable(attr):
            return attr

        async def wrapped(*args: Any, **kwargs: Any) -> Any:
            await asyncio.sleep(0)
            result = attr(*args, **kwargs)
            if asyncio.iscoroutine(result):
                result = await result
            await asyncio.sleep(0)
            return result

        return wrapped


class _GatedMemoryStore(MemorySessionStore):
    """``MemorySessionStore`` whose refresh write waits on a gate while armed."""

    def __init__(self) -> None:
        """Initialize the store with the gate disarmed."""
        super().__init__()
        self.armed: bool = False
        self.gate: asyncio.Event = asyncio.Event()
        self.reached: asyncio.Event = asyncio.Event()

    async def _wait(self) -> None:
        """Block on the gate when armed."""
        if self.armed:
            self.reached.set()
            await self.gate.wait()

    async def set(self, session: Session) -> None:
        """Gate, then persist.

        Args:
            session (Session): The session to persist.
        """
        await self._wait()
        await super().set(session)

    async def touch(self, session: Session) -> bool:
        """Gate, then refresh.

        Args:
            session (Session): The refreshed session.

        Returns:
            bool: Whether the session still existed.
        """
        await self._wait()
        return await super().touch(session)


async def _race(
    auth: SessionAuth,
    gated: _GatedRedis | _GatedMemoryStore,
    cookie: str,
    revoke: Callable[[], Awaitable[object]],
) -> Session | None:
    """Run ``resolve`` with its write held until ``revoke`` has returned.

    Args:
        auth (SessionAuth): The service.
        gated (_GatedRedis | _GatedMemoryStore): The gated layer.
        cookie (str): The session cookie.
        revoke (Callable[[], Awaitable[object]]): The revocation to run.

    Returns:
        Session | None: What the in-flight ``resolve`` returned.
    """
    gated.armed = True
    task = asyncio.create_task(auth.resolve(cookie))
    await asyncio.wait_for(gated.reached.wait(), timeout=2)
    gated.armed = False
    await revoke()
    gated.gate.set()
    return await task


class TestRedisRevocationSticks:
    async def test_revoke_during_inflight_resolve(self) -> None:
        inner = fake_aioredis.FakeRedis()
        client = _GatedRedis(inner)
        store = RedisSessionStore(client)  # type: ignore[arg-type]
        auth = _auth(store)
        session, cookie = await auth.login_with_credentials("root", "pw")

        inflight = await _race(auth, client, cookie, lambda: auth.revoke(cookie))

        assert inflight is None
        assert await auth.resolve(cookie) is None
        assert await inner.keys("tempest:sess:*") == []
        assert await store.list_by_user(session.user_id) == []

    async def test_revoke_all_during_inflight_resolve(self) -> None:
        inner = fake_aioredis.FakeRedis()
        client = _GatedRedis(inner)
        store = RedisSessionStore(client)  # type: ignore[arg-type]
        auth = _auth(store)
        session, cookie = await auth.login_with_credentials("root", "pw")
        user_id: UUID = session.user_id

        inflight = await _race(auth, client, cookie, lambda: auth.revoke_all(user_id))

        assert inflight is None
        assert await auth.resolve(cookie) is None
        assert await inner.keys("tempest:*") == []

    async def test_staggered_requests_never_survive_revoke_all(self) -> None:
        survived = 0
        for _ in range(20):
            store = RedisSessionStore(_YieldingRedis(fake_aioredis.FakeRedis()))  # type: ignore[arg-type]
            auth = _auth(store)
            session, cookie = await auth.login_with_credentials("root", "pw")

            async def one(
                delay: int, cookie: str = cookie, auth: SessionAuth = auth
            ) -> None:
                for _ in range(delay):
                    await asyncio.sleep(0)
                await auth.resolve(cookie)

            tasks = [asyncio.create_task(one(i)) for i in range(8)]
            await asyncio.sleep(0)
            await auth.revoke_all(session.user_id)
            await asyncio.gather(*tasks)
            survived += (await auth.resolve(cookie)) is not None
        assert survived == 0

    async def test_resolve_still_slides_a_live_session(self) -> None:
        inner = fake_aioredis.FakeRedis()
        store = RedisSessionStore(inner)
        auth = _auth(store)
        session, cookie = await auth.login_with_credentials("root", "pw")

        resolved = await auth.resolve(cookie)

        assert resolved is not None
        assert resolved.last_seen_at >= session.last_seen_at
        assert len(await store.list_by_user(session.user_id)) == 1

    async def test_touch_does_not_create_missing_key(self) -> None:
        inner = fake_aioredis.FakeRedis()
        store = RedisSessionStore(inner)
        now = utcnow()
        session = Session(
            session_id="b" * 64,
            user_id=uuid4(),
            created_at=now,
            expires_at=now + timedelta(seconds=60),
            last_seen_at=now,
        )

        assert await store.touch(session) is False
        assert await inner.keys("tempest:*") == []


class TestMemoryRevocationSticks:
    async def test_revoke_during_inflight_resolve(self) -> None:
        store = _GatedMemoryStore()
        auth = _auth(store)
        session, cookie = await auth.login_with_credentials("root", "pw")

        inflight = await _race(auth, store, cookie, lambda: auth.revoke(cookie))

        assert inflight is None
        assert await auth.resolve(cookie) is None
        assert await store.list_by_user(session.user_id) == []

    async def test_revoke_all_during_inflight_resolve(self) -> None:
        store = _GatedMemoryStore()
        auth = _auth(store)
        session, cookie = await auth.login_with_credentials("root", "pw")
        user_id: UUID = session.user_id

        inflight = await _race(auth, store, cookie, lambda: auth.revoke_all(user_id))

        assert inflight is None
        assert await auth.resolve(cookie) is None
        assert await store.list_by_user(user_id) == []


class _LegacyStore:
    """A store written against the protocol before ``touch`` existed."""

    def __init__(self) -> None:
        """Wrap a memory store, hiding its ``touch``."""
        self._inner: MemorySessionStore = MemorySessionStore()

    async def get(self, session_id_hash: str) -> Session | None:
        """Delegate.

        Args:
            session_id_hash (str): Hash of the session identifier.

        Returns:
            Session | None: The live session.
        """
        return await self._inner.get(session_id_hash)

    async def set(self, session: Session) -> None:
        """Delegate.

        Args:
            session (Session): The session to persist.
        """
        await self._inner.set(session)

    async def delete(self, session_id_hash: str) -> None:
        """Delegate.

        Args:
            session_id_hash (str): Hash of the session identifier.
        """
        await self._inner.delete(session_id_hash)

    async def delete_by_user(self, user_id: UUID) -> int:
        """Delegate.

        Args:
            user_id (UUID): Owner of the sessions.

        Returns:
            int: How many sessions were removed.
        """
        return await self._inner.delete_by_user(user_id)

    async def list_by_user(self, user_id: UUID) -> list[Session]:
        """Delegate.

        Args:
            user_id (UUID): Owner of the sessions.

        Returns:
            list[Session]: The user's live sessions.
        """
        return await self._inner.list_by_user(user_id)


async def test_store_without_touch_warns_and_still_resolves() -> None:
    auth = _auth(_LegacyStore())
    _, cookie = await auth.login_with_credentials("root", "pw")

    with warnings.catch_warnings(record=True) as caught:
        warnings.simplefilter("always")
        resolved = await auth.resolve(cookie)

    assert resolved is not None
    assert any(
        issubclass(w.category, DeprecationWarning) and "touch()" in str(w.message)
        for w in caught
    )
