"""Pluggable backends for feature flags.

A backend answers three questions for a flag name: its current value
(``get`` → ``True`` / ``False`` / ``None`` for "unset"), how to toggle
it (``set``), and the full set of known flags (``all``).

Four backends ship:

* :class:`MemoryFeatureFlagBackend` — in-process, for tests and dev.
* :class:`EnvFeatureFlagBackend` — read-only, static config from
  environment variables (``FEATURE_<NAME>=true``).
* :class:`RedisFeatureFlagBackend` — runtime-toggleable, stored in a
  Redis hash and shared across replicas.
* :class:`CompositeFeatureFlagBackend` — layers backends so a Redis
  override wins over an env default.
"""

from __future__ import annotations

import os
from collections.abc import Awaitable, Iterable, Mapping
from typing import Any, Protocol, runtime_checkable

_TRUTHY: frozenset[str] = frozenset({"1", "true", "yes", "on", "t", "y"})


def coerce_flag(value: str) -> bool:
    """Interpret a string flag value as a boolean.

    Args:
        value (str): The raw value (e.g. an env var or Redis field).

    Returns:
        bool: ``True`` for ``"1"``/``"true"``/``"yes"``/``"on"``/``"t"``/
        ``"y"`` (case-insensitive), ``False`` otherwise.
    """
    return value.strip().lower() in _TRUTHY


@runtime_checkable
class FeatureFlagBackend(Protocol):
    """Storage backend for boolean feature flags."""

    async def get(self, name: str) -> bool | None:
        """Return the flag value, or ``None`` when it is unset."""
        ...

    async def set(self, name: str, enabled: bool) -> None:
        """Persist ``enabled`` for ``name`` (may raise if read-only)."""
        ...

    async def all(self) -> dict[str, bool]:
        """Return every known flag as a ``{name: enabled}`` mapping."""
        ...


class MemoryFeatureFlagBackend:
    """In-process feature-flag store (tests, single-worker dev)."""

    def __init__(self, initial: Mapping[str, bool] | None = None) -> None:
        """Initialize the store.

        Args:
            initial (Mapping[str, bool] | None): Seed flags.
        """
        self._flags: dict[str, bool] = dict(initial or {})

    async def get(self, name: str) -> bool | None:
        """Return the flag value, or ``None`` when unset."""
        return self._flags.get(name)

    async def set(self, name: str, enabled: bool) -> None:
        """Set the flag value in memory."""
        self._flags[name] = enabled

    async def all(self) -> dict[str, bool]:
        """Return a copy of every flag."""
        return dict(self._flags)


class EnvFeatureFlagBackend:
    """Read-only feature flags sourced from environment variables.

    A flag named ``new_checkout`` maps to the env var
    ``FEATURE_NEW_CHECKOUT`` (the ``prefix`` plus the upper-cased name).
    Best used as the static default layer under a
    :class:`CompositeFeatureFlagBackend`.
    """

    def __init__(
        self,
        *,
        prefix: str = "FEATURE_",
        environ: Mapping[str, str] | None = None,
    ) -> None:
        """Initialize the backend.

        Args:
            prefix (str): Prefix prepended to the upper-cased flag name
                to form the env var.
            environ (Mapping[str, str] | None): Environment mapping to
                read. Defaults to :data:`os.environ`.
        """
        self._prefix: str = prefix
        self._environ: Mapping[str, str] = (
            environ if environ is not None else os.environ
        )

    def _var(self, name: str) -> str:
        """Return the env var name backing a flag."""
        return f"{self._prefix}{name.upper()}"

    async def get(self, name: str) -> bool | None:
        """Return the flag value from the environment, or ``None``."""
        raw = self._environ.get(self._var(name))
        return None if raw is None else coerce_flag(raw)

    async def set(self, name: str, enabled: bool) -> None:
        """Raise — environment-sourced flags are read-only.

        Raises:
            NotImplementedError: Always; env flags are static config.
        """
        raise NotImplementedError("EnvFeatureFlagBackend is read-only")

    async def all(self) -> dict[str, bool]:
        """Return every ``FEATURE_*`` env var as a flag mapping."""
        flags: dict[str, bool] = {}
        for key, value in self._environ.items():
            if key.startswith(self._prefix):
                flags[key[len(self._prefix) :].lower()] = coerce_flag(value)
        return flags


@runtime_checkable
class _RedisHashClient(Protocol):
    """Minimal async Redis hash surface used by the Redis backend."""

    def hget(self, name: str, key: str) -> Awaitable[Any]:
        """Return a hash field value."""
        ...

    def hset(self, name: str, key: str, value: str) -> Awaitable[Any]:
        """Set a hash field value."""
        ...

    def hgetall(self, name: str) -> Awaitable[Any]:
        """Return every field/value in a hash."""
        ...


class RedisFeatureFlagBackend:
    """Runtime-toggleable feature flags stored in a single Redis hash.

    All flags live as fields of one hash (``key``), so a toggle is
    instantly visible to every replica without a redeploy.
    """

    def __init__(
        self,
        redis: _RedisHashClient,
        *,
        key: str = "feature_flags",
    ) -> None:
        """Initialize the backend.

        Args:
            redis (_RedisHashClient): An async Redis client (e.g.
                ``redis.asyncio.Redis`` or
                ``AsyncRedisManager.client``).
            key (str): The Redis hash key holding every flag.
        """
        self._redis: _RedisHashClient = redis
        self._key: str = key

    @staticmethod
    def _decode(value: Any) -> str:
        """Return ``value`` as ``str`` whether the client decodes or not."""
        return value.decode() if isinstance(value, bytes) else str(value)

    async def get(self, name: str) -> bool | None:
        """Return the flag value from the hash, or ``None`` when unset."""
        raw = await self._redis.hget(self._key, name)
        return None if raw is None else coerce_flag(self._decode(raw))

    async def set(self, name: str, enabled: bool) -> None:
        """Persist the flag as ``"1"`` / ``"0"`` in the hash."""
        await self._redis.hset(self._key, name, "1" if enabled else "0")

    async def all(self) -> dict[str, bool]:
        """Return every field in the hash as a flag mapping."""
        data: Mapping[Any, Any] = await self._redis.hgetall(self._key)
        return {
            self._decode(field): coerce_flag(self._decode(value))
            for field, value in data.items()
        }


class CompositeFeatureFlagBackend:
    """Layer several backends — first non-``None`` value wins.

    The canonical setup is ``[redis, env]``: a runtime Redis override
    takes precedence, falling back to the static env default. Writes go
    to the first backend that accepts them (skipping read-only ones).
    """

    def __init__(self, backends: Iterable[FeatureFlagBackend]) -> None:
        """Initialize the composite.

        Args:
            backends (Iterable[FeatureFlagBackend]): Backends in priority
                order (highest priority first).
        """
        self._backends: list[FeatureFlagBackend] = list(backends)

    async def get(self, name: str) -> bool | None:
        """Return the first non-``None`` value across the layers."""
        for backend in self._backends:
            value = await backend.get(name)
            if value is not None:
                return value
        return None

    async def set(self, name: str, enabled: bool) -> None:
        """Write to the first backend that accepts writes.

        Raises:
            NotImplementedError: When no layered backend is writable.
        """
        for backend in self._backends:
            try:
                await backend.set(name, enabled)
            except NotImplementedError:
                continue
            else:
                return
        raise NotImplementedError("no writable backend in the composite")

    async def all(self) -> dict[str, bool]:
        """Merge every layer, with higher-priority backends winning."""
        merged: dict[str, bool] = {}
        for backend in reversed(self._backends):
            merged.update(await backend.all())
        return merged


__all__: list[str] = [
    "CompositeFeatureFlagBackend",
    "EnvFeatureFlagBackend",
    "FeatureFlagBackend",
    "MemoryFeatureFlagBackend",
    "RedisFeatureFlagBackend",
    "coerce_flag",
]
