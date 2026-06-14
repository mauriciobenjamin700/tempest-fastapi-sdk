"""Tag / namespace invalidation for the ``@cached`` decorator.

The :func:`tempest_fastapi_sdk.cache.cached` decorator can label each
entry it writes with a **namespace** (one coarse bucket per decorator)
and/or **tags** (fine-grained labels, optionally derived from the call
arguments). Labels are tracked in Redis sets — one set per namespace /
tag holding the entry keys that carry it — so a mutation can drop every
dependent entry at once via :class:`CacheInvalidator`, instead of
waiting out each entry's TTL.

The registry sets are themselves given the entry's TTL on every write,
so they self-prune once their newest member expires; deleting an
already-expired entry key is a harmless no-op.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from tempest_fastapi_sdk.cache.redis_manager import AsyncRedisManager

_NS_REGISTRY_INFIX: str = "__ns__:"
_TAG_REGISTRY_INFIX: str = "__tag__:"


def namespace_registry_key(key_prefix: str, namespace: str) -> str:
    """Return the Redis set key tracking entries in a namespace.

    Args:
        key_prefix (str): The decorator's ``key_prefix``.
        namespace (str): The namespace label.

    Returns:
        str: The registry set key (e.g. ``"users:__ns__:profiles"``).
    """
    return f"{key_prefix}{_NS_REGISTRY_INFIX}{namespace}"


def tag_registry_key(key_prefix: str, tag: str) -> str:
    """Return the Redis set key tracking entries carrying a tag.

    Args:
        key_prefix (str): The decorator's ``key_prefix``.
        tag (str): The tag label.

    Returns:
        str: The registry set key (e.g. ``"users:__tag__:user:42"``).
    """
    return f"{key_prefix}{_TAG_REGISTRY_INFIX}{tag}"


class CacheInvalidator:
    """Drops ``@cached`` entries by namespace, tag or raw key.

    Bind it to the same :class:`AsyncRedisManager` and ``key_prefix``
    the matching ``@cached`` decorators use, then call it from the
    service that performs the mutation::

        invalidator = CacheInvalidator(redis, key_prefix="users:")
        await invalidator.invalidate_tag(f"user:{user_id}")

    Each method returns the number of entry keys deleted.
    """

    def __init__(
        self,
        redis: AsyncRedisManager,
        *,
        key_prefix: str = "",
    ) -> None:
        """Initialize the invalidator.

        Args:
            redis (AsyncRedisManager): The connected Redis manager. It
                must match the manager used by the ``@cached`` decorators
                whose entries are being invalidated.
            key_prefix (str): The same ``key_prefix`` those decorators
                use, so the registry set names line up.
        """
        self._redis: AsyncRedisManager = redis
        self._key_prefix: str = key_prefix

    async def invalidate_namespace(self, namespace: str) -> int:
        """Delete every entry written under ``namespace``.

        Args:
            namespace (str): The namespace label to clear.

        Returns:
            int: The number of entry keys deleted.
        """
        registry = namespace_registry_key(self._key_prefix, namespace)
        return await self._purge((registry,))

    async def invalidate_tag(self, tag: str) -> int:
        """Delete every entry carrying ``tag``.

        Args:
            tag (str): The tag label to clear.

        Returns:
            int: The number of entry keys deleted.
        """
        registry = tag_registry_key(self._key_prefix, tag)
        return await self._purge((registry,))

    async def invalidate_tags(self, *tags: str) -> int:
        """Delete every entry carrying any of ``tags``.

        Entries shared by several tags are deleted once.

        Args:
            *tags (str): The tag labels to clear.

        Returns:
            int: The number of distinct entry keys deleted.
        """
        registries = tuple(tag_registry_key(self._key_prefix, tag) for tag in tags)
        return await self._purge(registries)

    async def invalidate_keys(self, *keys: str) -> int:
        """Delete specific fully-qualified cache keys.

        Args:
            *keys (str): The raw entry keys (including ``key_prefix``).

        Returns:
            int: The number of keys deleted (per Redis ``DEL``).
        """
        if not keys:
            return 0
        deleted: int = await self._redis.client.delete(*keys)
        return deleted

    async def _purge(self, registries: tuple[str, ...]) -> int:
        """Delete every entry referenced by the given registry sets.

        Args:
            registries (tuple[str, ...]): Registry set keys whose members
                are entry keys to delete.

        Returns:
            int: The number of distinct entry keys deleted.
        """
        if not registries:
            return 0
        client = self._redis.client
        members: set[Any] = await client.sunion(list(registries))
        if members:
            await client.delete(*members)
        await client.delete(*registries)
        return len(members)


__all__: list[str] = [
    "CacheInvalidator",
    "namespace_registry_key",
    "tag_registry_key",
]
