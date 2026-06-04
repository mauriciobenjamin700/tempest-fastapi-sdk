"""``SessionStore`` Protocol plus in-memory and Redis implementations.

The store is the persistence layer behind every session-backed
flow — it keeps :class:`Session` rows keyed by the SHA-256 hash of
the cookie value so a leak of the store does not yield reusable
sessions. The protocol is intentionally narrow:

* ``get(session_id_hash)`` → returns the live :class:`Session`,
  ``None`` if missing / expired.
* ``set(session)`` → persists or replaces a session.
* ``delete(session_id_hash)`` → removes one session
  (revocation / logout).
* ``delete_by_user(user_id)`` → removes every session a user
  owns (global logout).
* ``list_by_user(user_id)`` → enumerates the user's sessions for
  the "active devices" UI.

The async surface is identical across implementations; consumer
code targets the protocol, never the concrete class.
"""

from __future__ import annotations

import asyncio
import json
from typing import TYPE_CHECKING, Any, Protocol, runtime_checkable
from uuid import UUID

from tempest_fastapi_sdk.sessions.schemas import Session
from tempest_fastapi_sdk.utils.datetime import utcnow

if TYPE_CHECKING:
    from redis.asyncio import Redis


@runtime_checkable
class SessionStore(Protocol):
    """Persistence protocol every session backend implements."""

    async def get(self, session_id_hash: str) -> Session | None:
        """Return the live session for ``session_id_hash`` or ``None``."""
        ...

    async def set(self, session: Session) -> None:
        """Persist (or overwrite) ``session``."""
        ...

    async def delete(self, session_id_hash: str) -> None:
        """Remove a single session. Idempotent."""
        ...

    async def delete_by_user(self, user_id: UUID) -> int:
        """Remove every session for ``user_id``. Returns count deleted."""
        ...

    async def list_by_user(self, user_id: UUID) -> list[Session]:
        """Return every live session for ``user_id`` (oldest first)."""
        ...


class MemorySessionStore:
    """In-process :class:`SessionStore` for dev, tests, single-replica.

    Stores sessions in a dict keyed by their hashed id; a secondary
    index by ``user_id`` powers ``list_by_user`` /
    ``delete_by_user`` without scanning. Expired rows are pruned on
    access — no background task needed.
    """

    def __init__(self) -> None:
        """Initialize the in-memory store."""
        self._sessions: dict[str, Session] = {}
        self._by_user: dict[UUID, set[str]] = {}
        self._lock: asyncio.Lock = asyncio.Lock()

    async def get(self, session_id_hash: str) -> Session | None:
        """Return the live session for ``session_id_hash`` or ``None``."""
        async with self._lock:
            session = self._sessions.get(session_id_hash)
            if session is None:
                return None
            if self._is_expired(session):
                self._evict_locked(session)
                return None
            return session

    async def set(self, session: Session) -> None:
        """Persist (or overwrite) ``session``."""
        async with self._lock:
            existing = self._sessions.get(session.session_id)
            if existing is not None and existing.user_id != session.user_id:
                # Same id but new owner: keep indexes consistent.
                self._by_user.get(existing.user_id, set()).discard(
                    session.session_id,
                )
            self._sessions[session.session_id] = session
            self._by_user.setdefault(session.user_id, set()).add(
                session.session_id,
            )

    async def delete(self, session_id_hash: str) -> None:
        """Remove a single session. Idempotent."""
        async with self._lock:
            session = self._sessions.pop(session_id_hash, None)
            if session is None:
                return
            self._by_user.get(session.user_id, set()).discard(session_id_hash)
            if not self._by_user.get(session.user_id):
                self._by_user.pop(session.user_id, None)

    async def delete_by_user(self, user_id: UUID) -> int:
        """Remove every session for ``user_id``. Returns count deleted."""
        async with self._lock:
            ids = list(self._by_user.pop(user_id, set()))
            for session_id in ids:
                self._sessions.pop(session_id, None)
            return len(ids)

    async def list_by_user(self, user_id: UUID) -> list[Session]:
        """Return every live session for ``user_id`` (oldest first)."""
        async with self._lock:
            ids = list(self._by_user.get(user_id, set()))
            sessions = []
            for session_id in ids:
                session = self._sessions.get(session_id)
                if session is None:
                    continue
                if self._is_expired(session):
                    self._evict_locked(session)
                    continue
                sessions.append(session)
            sessions.sort(key=lambda s: s.created_at)
            return sessions

    @staticmethod
    def _is_expired(session: Session) -> bool:
        now = utcnow().replace(tzinfo=None)
        expires = (
            session.expires_at.replace(tzinfo=None)
            if session.expires_at.tzinfo is not None
            else session.expires_at
        )
        return expires < now

    def _evict_locked(self, session: Session) -> None:
        """Drop ``session`` from both indexes — caller already holds the lock."""
        self._sessions.pop(session.session_id, None)
        self._by_user.get(session.user_id, set()).discard(session.session_id)
        if not self._by_user.get(session.user_id):
            self._by_user.pop(session.user_id, None)


class RedisSessionStore:
    """:class:`SessionStore` backed by an async ``redis`` client.

    Schema:

    * Each session is stored at ``{prefix}sess:{hash}`` as JSON with
      a TTL set to ``(expires_at - now)`` so Redis evicts the key
      on its own — no janitor process needed.
    * The user → session index lives at ``{prefix}user:{user_id}``
      as a Redis SET of session hashes. Entries are removed on
      ``delete`` and the whole SET is dropped on ``delete_by_user``.

    Requires the ``[cache]`` extra so the ``redis`` async client is
    available.
    """

    def __init__(
        self,
        client: Redis,
        *,
        prefix: str = "tempest:",
    ) -> None:
        """Initialize the Redis-backed store.

        Args:
            client (Redis): Async Redis client (e.g.
                ``AsyncRedisManager.client``).
            prefix (str): Key prefix so session keys do not collide
                with other cached data.
        """
        self.client: Redis = client
        self.prefix: str = prefix

    def _session_key(self, session_id_hash: str) -> str:
        return f"{self.prefix}sess:{session_id_hash}"

    def _user_key(self, user_id: UUID) -> str:
        return f"{self.prefix}user:{user_id}"

    async def get(self, session_id_hash: str) -> Session | None:
        """Return the live session for ``session_id_hash`` or ``None``."""
        raw = await self.client.get(self._session_key(session_id_hash))
        if raw is None:
            return None
        payload: dict[str, Any] = json.loads(raw)
        session = Session.model_validate(payload)
        if self._is_expired(session):
            await self.delete(session_id_hash)
            return None
        return session

    async def set(self, session: Session) -> None:
        """Persist (or overwrite) ``session`` with a TTL matching ``expires_at``."""
        ttl_seconds = max(
            1,
            int(
                (
                    session.expires_at.replace(tzinfo=None)
                    if session.expires_at.tzinfo is not None
                    else session.expires_at
                ).timestamp()
                - utcnow().replace(tzinfo=None).timestamp()
            ),
        )
        payload = session.model_dump(mode="json")
        await self.client.set(
            self._session_key(session.session_id),
            json.dumps(payload),
            ex=ttl_seconds,
        )
        await self._sadd(self._user_key(session.user_id), session.session_id)
        # User index TTL grows with the longest-lived session — bump it.
        await self.client.expire(self._user_key(session.user_id), ttl_seconds)

    async def delete(self, session_id_hash: str) -> None:
        """Remove a single session. Idempotent."""
        raw = await self.client.get(self._session_key(session_id_hash))
        if raw is not None:
            payload = json.loads(raw)
            user_id = payload.get("user_id")
            if user_id:
                await self._srem(self._user_key(UUID(user_id)), session_id_hash)
        await self.client.delete(self._session_key(session_id_hash))

    async def delete_by_user(self, user_id: UUID) -> int:
        """Remove every session for ``user_id``. Returns count deleted."""
        decoded = await self._smembers(self._user_key(user_id))
        if decoded:
            await self.client.delete(
                *[self._session_key(sid) for sid in decoded],
            )
        await self.client.delete(self._user_key(user_id))
        return len(decoded)

    async def list_by_user(self, user_id: UUID) -> list[Session]:
        """Return every live session for ``user_id`` (oldest first)."""
        decoded = await self._smembers(self._user_key(user_id))
        sessions: list[Session] = []
        stale: list[str] = []
        for session_id in decoded:
            raw = await self.client.get(self._session_key(session_id))
            if raw is None:
                stale.append(session_id)
                continue
            session = Session.model_validate(json.loads(raw))
            if self._is_expired(session):
                stale.append(session_id)
                continue
            sessions.append(session)
        if stale:
            await self._srem(self._user_key(user_id), *stale)
        sessions.sort(key=lambda s: s.created_at)
        return sessions

    async def _smembers(self, key: str) -> list[str]:
        """Read a Redis SET, normalize bytes/str entries to ``list[str]``.

        ``redis.asyncio.Redis.smembers`` is typed as
        ``Awaitable[set[Any]] | set[Any]`` on the upstream stubs;
        the helper centralizes the ``await`` + decode dance so the
        rest of the store stays type-clean.
        """
        raw: Any = self.client.smembers(key)
        if hasattr(raw, "__await__"):
            raw = await raw
        return [(m.decode("utf-8") if isinstance(m, bytes) else m) for m in raw]

    async def _srem(self, key: str, *members: str) -> int:
        """Awaitable-or-int wrapper for ``Redis.srem`` (same shape mismatch)."""
        raw: Any = self.client.srem(key, *members)
        if hasattr(raw, "__await__"):
            raw = await raw
        return int(raw)

    async def _sadd(self, key: str, *members: str) -> int:
        """Awaitable-or-int wrapper for ``Redis.sadd`` (same shape mismatch)."""
        raw: Any = self.client.sadd(key, *members)
        if hasattr(raw, "__await__"):
            raw = await raw
        return int(raw)

    @staticmethod
    def _is_expired(session: Session) -> bool:
        now = utcnow().replace(tzinfo=None)
        expires = (
            session.expires_at.replace(tzinfo=None)
            if session.expires_at.tzinfo is not None
            else session.expires_at
        )
        return expires < now


__all__: list[str] = [
    "MemorySessionStore",
    "RedisSessionStore",
    "SessionStore",
]
