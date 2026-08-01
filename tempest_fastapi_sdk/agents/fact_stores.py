"""Fact stores that survive a restart.

:class:`~tempest_fastapi_sdk.agents.InMemoryFactStore` is for tests and for
getting started; it loses everything when the process dies, which is the
one thing durable memory must not do. These two keep it:

* :class:`DbFactStore` over :class:`BaseFactModel` — a row per fact. Reach
  for it when facts are part of your domain: you want them in backups, in
  the admin, joined against a user, and readable by something other than
  the agent.
* :class:`RedisFactStore` — a hash per subject. Reach for it when facts are
  ephemeral-ish preferences shared across replicas and a migration is more
  ceremony than the data deserves.

Both implement the same four-method
:class:`~tempest_fastapi_sdk.agents.FactStore` protocol, so swapping one
for another is a constructor change.

The SQLAlchemy pieces import with no extra (the ORM is a base dependency);
Redis needs ``[cache]``.
"""

from __future__ import annotations

import json
import time
from typing import TYPE_CHECKING, Any

from sqlalchemy import Float, String, Text, delete, select
from sqlalchemy.orm import Mapped, mapped_column

from tempest_fastapi_sdk.agents.memory import Fact
from tempest_fastapi_sdk.db.model import BaseModel

if TYPE_CHECKING:
    from tempest_fastapi_sdk.db.connection import AsyncDatabaseManager


class BaseFactModel(BaseModel):
    """One durable fact, as a row.

    Abstract — subclass it (setting ``__tablename__``) in the project, or
    build a concrete class with :func:`make_fact_model`. Inherits the SDK
    base columns; ``updated_at`` on the row and :attr:`Fact.updated_at`
    both track the last write.

    A fact is identified by ``(subject, key)``. There is deliberately no
    unique constraint declared here — the SDK cannot know whether your
    table is partitioned or shared — so **add one in your migration**:

        UNIQUE (subject, key)

    Without it a race between two writes leaves two rows, and reads start
    returning whichever the database feels like.
    """

    __abstract__ = True

    key: Mapped[str] = mapped_column(
        String(255),
        index=True,
        doc="The fact's stable identifier.",
    )
    value: Mapped[str] = mapped_column(
        Text,
        doc="What is believed.",
    )
    subject: Mapped[str | None] = mapped_column(
        String(255),
        nullable=True,
        index=True,
        doc="Who or what the fact is about.",
    )
    written_at: Mapped[float] = mapped_column(
        Float,
        default=0.0,
        doc="Unix timestamp of the last write.",
    )

    def to_fact(self) -> Fact:
        """Return this row as a :class:`~tempest_fastapi_sdk.agents.Fact`.

        Returns:
            Fact: The schema form.
        """
        return Fact(
            key=self.key,
            value=self.value,
            subject=self.subject,
            updated_at=self.written_at,
        )


def make_fact_model(
    *,
    tablename: str = "agent_facts",
    class_name: str = "FactModel",
) -> type[BaseFactModel]:
    """Build a concrete fact model bound to ``tablename``.

    For tests and lightweight scripts; production code should subclass
    :class:`BaseFactModel` by hand so migrations pick it up statically.

    Args:
        tablename (str): The table name.
        class_name (str): The generated class name.

    Returns:
        type[BaseFactModel]: The concrete model class.
    """
    return type(class_name, (BaseFactModel,), {"__tablename__": tablename})


class DbFactStore:
    """A :class:`~tempest_fastapi_sdk.agents.FactStore` backed by a table.

    Example:

        >>> model = make_fact_model()
        >>> store = DbFactStore(db, model)
        >>> agent = Agent(generator, tools=fact_tools(store, subject=user_id))

    Each operation opens its own short transaction, so a fact written mid
    run is visible to the next step even if the run later fails — which is
    what you want from something the agent asserted as true.

    Attributes:
        model (type[BaseFactModel]): The concrete fact table.
    """

    def __init__(
        self,
        db: AsyncDatabaseManager,
        model: type[BaseFactModel],
    ) -> None:
        """Configure the store.

        Args:
            db (AsyncDatabaseManager): The database manager.
            model (type[BaseFactModel]): The concrete fact table.
        """
        self._db = db
        self.model = model

    async def get(self, key: str, *, subject: str | None = None) -> Fact | None:
        """Return one fact, or ``None`` when it was never written.

        Args:
            key (str): The fact's key.
            subject (str | None): Whose fact.

        Returns:
            Fact | None: The stored fact.
        """
        async with self._db.get_session_context() as session:
            result = await session.execute(
                select(self.model)
                .where(self.model.key == key)
                .where(self.model.subject == subject),
            )
            row = result.scalar_one_or_none()
            return row.to_fact() if row is not None else None

    async def put(self, key: str, value: str, *, subject: str | None = None) -> Fact:
        """Write one fact, replacing any previous value.

        Read-then-write rather than a dialect-specific upsert, because the
        SDK targets both PostgreSQL and SQLite. Concurrent writers to the
        same key are what the ``UNIQUE (subject, key)`` constraint on your
        table is for — see :class:`BaseFactModel`.

        Args:
            key (str): The fact's key.
            value (str): What to believe.
            subject (str | None): Whose fact.

        Returns:
            Fact: The stored fact.
        """
        now = time.time()
        async with self._db.get_session_context() as session:
            result = await session.execute(
                select(self.model)
                .where(self.model.key == key)
                .where(self.model.subject == subject),
            )
            row = result.scalar_one_or_none()
            if row is None:
                row = self.model(
                    key=key,
                    value=value,
                    subject=subject,
                    written_at=now,
                )
                session.add(row)
            else:
                row.value = value
                row.written_at = now
            return Fact(key=key, value=value, subject=subject, updated_at=now)

    async def forget(self, key: str, *, subject: str | None = None) -> bool:
        """Delete one fact.

        Args:
            key (str): The fact's key.
            subject (str | None): Whose fact.

        Returns:
            bool: Whether a row was removed.
        """
        async with self._db.get_session_context() as session:
            result: Any = await session.execute(
                delete(self.model)
                .where(self.model.key == key)
                .where(self.model.subject == subject),
            )
            return bool(result.rowcount)

    async def all(self, *, subject: str | None = None) -> list[Fact]:
        """Return every fact for ``subject``, sorted by key.

        Args:
            subject (str | None): Whose facts.

        Returns:
            list[Fact]: The facts; empty when there are none.
        """
        async with self._db.get_session_context() as session:
            result = await session.execute(
                select(self.model)
                .where(self.model.subject == subject)
                .order_by(self.model.key),
            )
            return [row.to_fact() for row in result.scalars().all()]


class RedisFactStore:
    """A :class:`~tempest_fastapi_sdk.agents.FactStore` backed by Redis.

    One hash per subject, so listing a subject's facts is a single
    ``HGETALL`` and every operation is O(1) — the access pattern facts
    actually have.

    Example:

        >>> store = RedisFactStore(redis, prefix="agent:facts")
        >>> agent = Agent(generator, tools=fact_tools(store, subject=user_id))

    Attributes:
        prefix (str): Key prefix for the hashes.
    """

    def __init__(self, redis: Any, *, prefix: str = "agent:facts") -> None:
        """Configure the store.

        Args:
            redis (Any): A ``redis.asyncio.Redis`` client.
            prefix (str): Key prefix; the hash for a subject is
                ``"{prefix}:{subject}"``.
        """
        self._redis = redis
        self.prefix = prefix

    def _key(self, subject: str | None) -> str:
        """Return the hash key for ``subject``.

        Args:
            subject (str | None): Whose facts.

        Returns:
            str: The Redis key. ``None`` maps to a literal ``"_"`` bucket
            rather than an empty segment, so a shared namespace cannot
            collide with a subject whose id happens to be empty.
        """
        return f"{self.prefix}:{subject or '_'}"

    @staticmethod
    def _decode(raw: Any) -> str:
        """Return ``raw`` as text, whatever the client's decode setting.

        Args:
            raw (Any): A value from Redis.

        Returns:
            str: The decoded text.
        """
        return raw.decode("utf-8") if isinstance(raw, bytes) else str(raw)

    async def get(self, key: str, *, subject: str | None = None) -> Fact | None:
        """Return one fact, or ``None``.

        Args:
            key (str): The fact's key.
            subject (str | None): Whose fact.

        Returns:
            Fact | None: The stored fact.
        """
        raw = await self._redis.hget(self._key(subject), key)
        if raw is None:
            return None
        payload = json.loads(self._decode(raw))
        return Fact(
            key=key,
            value=payload["value"],
            subject=subject,
            updated_at=payload.get("updated_at", 0.0),
        )

    async def put(self, key: str, value: str, *, subject: str | None = None) -> Fact:
        """Write one fact, replacing any previous value.

        Args:
            key (str): The fact's key.
            value (str): What to believe.
            subject (str | None): Whose fact.

        Returns:
            Fact: The stored fact.
        """
        now = time.time()
        await self._redis.hset(
            self._key(subject),
            key,
            json.dumps({"value": value, "updated_at": now}),
        )
        return Fact(key=key, value=value, subject=subject, updated_at=now)

    async def forget(self, key: str, *, subject: str | None = None) -> bool:
        """Delete one fact.

        Args:
            key (str): The fact's key.
            subject (str | None): Whose fact.

        Returns:
            bool: Whether the field existed.
        """
        removed = await self._redis.hdel(self._key(subject), key)
        return bool(removed)

    async def all(self, *, subject: str | None = None) -> list[Fact]:
        """Return every fact for ``subject``, sorted by key.

        Args:
            subject (str | None): Whose facts.

        Returns:
            list[Fact]: The facts; empty when there are none.
        """
        raw = await self._redis.hgetall(self._key(subject))
        facts: list[Fact] = []
        for field, blob in (raw or {}).items():
            payload = json.loads(self._decode(blob))
            facts.append(
                Fact(
                    key=self._decode(field),
                    value=payload["value"],
                    subject=subject,
                    updated_at=payload.get("updated_at", 0.0),
                ),
            )
        return sorted(facts, key=lambda fact: fact.key)


__all__: list[str] = [
    "BaseFactModel",
    "DbFactStore",
    "RedisFactStore",
    "make_fact_model",
]
