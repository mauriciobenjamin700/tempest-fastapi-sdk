"""Streamed, memoized SHA-256 digests for immutable artifact sources.

An artifact's ``file_key`` (and a bundled on-disk fallback path) is
immutable for its identity, so the ``(sha256, size)`` pair only has to
be computed once. Both helpers stream the bytes in 1 MiB chunks — the
whole payload is never held in memory — and memoize the result in a
process-local dict keyed by the immutable identity (the file path, or
the ``(bucket, key)`` pair).
"""

from __future__ import annotations

import asyncio
import hashlib
from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from tempest_fastapi_sdk.storage import AsyncMinIOClient

_CHUNK_SIZE: int = 1024 * 1024

_file_cache: dict[str, tuple[str, int]] = {}
_object_cache: dict[tuple[str, str], tuple[str, int]] = {}


def _read_file_digest(path: Path) -> tuple[str, int]:
    """Compute ``(sha256, size)`` for a file by streaming it.

    Args:
        path (Path): The file to digest.

    Returns:
        tuple[str, int]: The hex SHA-256 and the size in bytes.
    """
    digest = hashlib.sha256()
    size = 0
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(_CHUNK_SIZE), b""):
            digest.update(chunk)
            size += len(chunk)
    return digest.hexdigest(), size


async def file_digest(path: str | Path) -> tuple[str, int]:
    """Return the streamed ``(sha256, size)`` of an on-disk file, memoized.

    The file read runs in a worker thread (``asyncio.to_thread``) so the
    event loop stays responsive; the result is cached by the resolved
    path string, so repeat calls never re-read the file.

    Args:
        path (str | Path): Path to the file to digest.

    Returns:
        tuple[str, int]: The hex SHA-256 and the size in bytes.
    """
    key = str(path)
    cached = _file_cache.get(key)
    if cached is None:
        cached = await asyncio.to_thread(_read_file_digest, Path(path))
        _file_cache[key] = cached
    return cached


async def object_digest(
    client: AsyncMinIOClient,
    bucket: str,
    key: str,
) -> tuple[str, int]:
    """Return the streamed ``(sha256, size)`` of a stored object, memoized.

    Streams the object from MinIO/S3 chunk by chunk (never loading the
    whole payload into memory) and caches the result by ``(bucket,
    key)`` — safe because an object key is immutable for its content.

    Args:
        client (AsyncMinIOClient): The object-storage client.
        bucket (str): The bucket the object lives in.
        key (str): The object key.

    Returns:
        tuple[str, int]: The hex SHA-256 and the size in bytes.
    """
    cache_key = (bucket, key)
    cached = _object_cache.get(cache_key)
    if cached is None:
        digest = hashlib.sha256()
        size = 0
        stream = await client.stream_object(key, bucket=bucket, chunk_size=_CHUNK_SIZE)
        async for chunk in stream:
            digest.update(chunk)
            size += len(chunk)
        cached = (digest.hexdigest(), size)
        _object_cache[cache_key] = cached
    return cached


__all__: list[str] = [
    "file_digest",
    "object_digest",
]
