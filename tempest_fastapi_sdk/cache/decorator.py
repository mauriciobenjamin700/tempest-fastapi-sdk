"""``@cached`` decorator built on top of :class:`AsyncRedisManager`."""

from __future__ import annotations

import functools
import hashlib
import json
import logging
from collections.abc import Awaitable, Callable, Sequence
from typing import TYPE_CHECKING, Any, TypeVar

from tempest_fastapi_sdk.cache.invalidation import (
    namespace_registry_key,
    tag_registry_key,
)

if TYPE_CHECKING:
    from tempest_fastapi_sdk.cache.redis_manager import AsyncRedisManager

logger = logging.getLogger(__name__)

T = TypeVar("T")
F = Callable[..., Awaitable[T]]

TagSpec = (
    Sequence[str] | Callable[[tuple[Any, ...], dict[str, Any]], Sequence[str]] | None
)
"""Static tags, a per-call tag builder, or ``None`` for no tags."""


def _default_key_builder(
    func_name: str,
    args: tuple[Any, ...],
    kwargs: dict[str, Any],
) -> str:
    """Hash positional + keyword args into a stable cache key fragment.

    Args:
        func_name (str): The decorated function's qualified name.
        args (tuple[Any, ...]): Positional arguments.
        kwargs (dict[str, Any]): Keyword arguments.

    Returns:
        str: A deterministic ``<func_name>:<sha256>`` fragment.
    """
    payload: dict[str, Any] = {
        "args": [_to_jsonable(a) for a in args],
        "kwargs": {k: _to_jsonable(v) for k, v in sorted(kwargs.items())},
    }
    digest = hashlib.sha256(
        json.dumps(payload, separators=(",", ":"), default=str).encode(),
    ).hexdigest()[:32]
    return f"{func_name}:{digest}"


def _to_jsonable(value: Any) -> Any:
    """Best-effort JSON-able representation used for cache key hashing.

    Args:
        value (Any): Any positional / keyword argument value.

    Returns:
        Any: A JSON-serializable representation (falls back to ``str``).
    """
    try:
        json.dumps(value)
    except TypeError:
        return str(value)
    return value


def cached(
    redis: AsyncRedisManager,
    *,
    ttl: int = 300,
    key_prefix: str = "",
    key_builder: Callable[[str, tuple[Any, ...], dict[str, Any]], str] | None = None,
    serializer: Callable[[Any], str] = json.dumps,
    deserializer: Callable[[str | bytes], Any] = json.loads,
    skip_cache: Callable[[tuple[Any, ...], dict[str, Any]], bool] | None = None,
    namespace: str | None = None,
    tags: TagSpec = None,
) -> Callable[[F[T]], F[T]]:
    """Cache the result of an async function in Redis.

    Cache keys are ``<key_prefix><func_qualname>:<sha256>``. On miss
    the wrapped function runs and its result is stored with TTL
    ``ttl``. On hit the cached value is deserialized and returned
    without re-running the function.

    **Tag / namespace invalidation.** When ``namespace`` and/or ``tags``
    are set, every stored entry's key is also added to a Redis set per
    label (see :func:`tempest_fastapi_sdk.cache.namespace_registry_key`
    / :func:`tag_registry_key`). A mutation then drops every dependent
    entry at once via
    :class:`tempest_fastapi_sdk.cache.CacheInvalidator`, instead of
    waiting out each TTL::

        @cached(redis, key_prefix="users:", namespace="profiles",
                tags=lambda args, kwargs: [f"user:{kwargs['user_id']}"])
        async def get_profile(*, user_id: int) -> dict[str, Any]: ...

        # On update:
        await CacheInvalidator(redis, key_prefix="users:").invalidate_tag(
            f"user:{user_id}",
        )

    Args:
        redis (AsyncRedisManager): The connected Redis manager. Calling
            the decorated function before
            :meth:`AsyncRedisManager.connect` raises ``RuntimeError``.
        ttl (int): Cache TTL in seconds. ``0`` disables expiration
            (Redis ``SET`` without ``EX``). The registry sets share the
            same TTL, so they self-prune after their newest member.
        key_prefix (str): Prefix prepended to every cache key (e.g.
            ``"users:"`` to make invalidation easier). Pass the same
            prefix to :class:`CacheInvalidator`.
        key_builder (Callable[[str, tuple, dict], str] | None): Custom
            cache key builder. Defaults to a SHA-256 of args/kwargs.
        serializer (Callable[[Any], str]): How to encode the result
            before storing. Defaults to :func:`json.dumps`.
        deserializer (Callable[[str | bytes], Any]): How to decode the
            cached payload (Redis returns ``bytes`` unless the client
            sets ``decode_responses=True``). Defaults to
            :func:`json.loads`, which accepts both.
        skip_cache (Callable[[tuple, dict], bool] | None): Optional
            predicate. When it returns ``True``, the decorator bypasses
            cache for that call (read **and** write).
        namespace (str | None): Coarse label grouping every entry this
            decorator writes, cleared via
            :meth:`CacheInvalidator.invalidate_namespace`.
        tags (TagSpec): Static tags (a sequence) or a per-call builder
            ``(args, kwargs) -> Sequence[str]`` returning the tags for
            that entry (e.g. ``f"user:{id}"``), cleared via
            :meth:`CacheInvalidator.invalidate_tag`.

    Returns:
        Callable[[F[T]], F[T]]: A decorator preserving the signature
        of the wrapped async callable.
    """
    builder = key_builder or _default_key_builder

    def decorator(func: F[T]) -> F[T]:
        @functools.wraps(func)
        async def wrapper(*args: Any, **kwargs: Any) -> T:
            if skip_cache is not None and skip_cache(args, kwargs):
                return await func(*args, **kwargs)

            key = key_prefix + builder(func.__qualname__, args, kwargs)
            client = redis.client
            cached_value = await client.get(key)
            if cached_value is not None:
                try:
                    decoded: T = deserializer(cached_value)
                    return decoded
                except (TypeError, ValueError) as exc:
                    logger.warning(
                        "cached_deserialization_failed",
                        extra={"key": key, "error": str(exc)},
                    )

            result = await func(*args, **kwargs)
            try:
                payload = serializer(result)
            except (TypeError, ValueError) as exc:
                logger.warning(
                    "cached_serialization_failed",
                    extra={"key": key, "error": str(exc)},
                )
                return result

            if ttl > 0:
                await client.set(key, payload, ex=ttl)
            else:
                await client.set(key, payload)
            await _register_labels(
                client,
                key=key,
                key_prefix=key_prefix,
                namespace=namespace,
                tag_list=_resolve_tags(tags, args, kwargs),
                ttl=ttl,
            )
            return result

        return wrapper

    return decorator


def _resolve_tags(
    tags: TagSpec,
    args: tuple[Any, ...],
    kwargs: dict[str, Any],
) -> list[str]:
    """Resolve the tag spec into a concrete list of tags for one call.

    Args:
        tags (TagSpec): Static tags, a per-call builder, or ``None``.
        args (tuple[Any, ...]): The call's positional arguments.
        kwargs (dict[str, Any]): The call's keyword arguments.

    Returns:
        list[str]: The tags for this entry (empty when ``tags`` is
        ``None``).
    """
    if tags is None:
        return []
    if callable(tags):
        return list(tags(args, kwargs))
    return list(tags)


async def _register_labels(
    client: Any,
    *,
    key: str,
    key_prefix: str,
    namespace: str | None,
    tag_list: list[str],
    ttl: int,
) -> None:
    """Add the entry key to its namespace / tag registry sets.

    Args:
        client (Any): The live Redis client.
        key (str): The entry key just written.
        key_prefix (str): The decorator's key prefix.
        namespace (str | None): Namespace label, if any.
        tag_list (list[str]): Resolved tags, if any.
        ttl (int): Entry TTL; the registry sets get the same expiry
            (``0`` leaves them persistent).
    """
    if namespace is None and not tag_list:
        return
    registries: list[str] = []
    if namespace is not None:
        registries.append(namespace_registry_key(key_prefix, namespace))
    registries.extend(tag_registry_key(key_prefix, tag) for tag in tag_list)
    pipe = client.pipeline()
    for registry in registries:
        pipe.sadd(registry, key)
        if ttl > 0:
            pipe.expire(registry, ttl)
    await pipe.execute()


__all__: list[str] = [
    "cached",
]
