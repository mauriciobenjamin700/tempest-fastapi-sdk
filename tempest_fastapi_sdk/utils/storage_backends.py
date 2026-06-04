"""Pluggable backends for :class:`UploadUtils`.

Provides an :class:`UploadStorage` protocol with two concrete
implementations:

* :class:`LocalUploadStorage` — writes to disk under a base
  directory using ``aiofiles``. The default when no backend is
  provided to :class:`UploadUtils`.
* :class:`MinIOUploadStorage` — persists to MinIO / S3 via
  :class:`tempest_fastapi_sdk.AsyncMinIOClient`. Requires the
  ``[minio]`` extra.

Validation (extension, MIME, size, magic bytes) stays in
:class:`UploadUtils` so the same checks run against every backend.
Backends only know about the bytes themselves.
"""

from __future__ import annotations

from collections.abc import AsyncIterator, Callable
from dataclasses import dataclass
from datetime import timedelta
from pathlib import Path
from types import ModuleType
from typing import TYPE_CHECKING, Protocol, runtime_checkable

if TYPE_CHECKING:
    from tempest_fastapi_sdk.storage.minio_client import AsyncMinIOClient

ContentValidator = Callable[[bytes], bool]
"""First-chunk predicate. Returning ``False`` aborts the upload."""

try:
    import aiofiles as _aiofiles_mod

    _aiofiles: ModuleType | None = _aiofiles_mod
except ImportError:  # pragma: no cover - guarded by [upload] extra
    _aiofiles = None


@dataclass(frozen=True, slots=True)
class UploadResult:
    """Outcome of an :meth:`UploadStorage.write_stream` call.

    The ``key`` is the canonical identifier for the persisted
    object — a path string for the local backend, an S3 key for
    MinIO. ``path`` is set only when the backend wrote to a real
    filesystem location; ``url`` only when the backend can mint
    a download URL (presigned or static).

    Attributes:
        key (str): Identifier used to read the object back.
        size (int): Bytes written.
        path (Path | None): On-disk path when applicable.
        url (str | None): Public or presigned download URL when
            applicable.
    """

    key: str
    size: int
    path: Path | None = None
    url: str | None = None


@runtime_checkable
class UploadStorage(Protocol):
    """Protocol every upload backend implements.

    Implementations must be safe to call concurrently — FastAPI
    routes share the same instance.
    """

    async def write_stream(
        self,
        key: str,
        chunks: AsyncIterator[bytes],
        *,
        content_type: str = "application/octet-stream",
        metadata: dict[str, str] | None = None,
        max_size_bytes: int | None = None,
        validator: ContentValidator | None = None,
    ) -> UploadResult:
        """Persist a chunked async stream under ``key``.

        Args:
            key (str): Storage key. Backends interpret this as a
                relative path (local) or object key (S3).
            chunks (AsyncIterator[bytes]): Source stream.
            content_type (str): MIME type of the payload.
            metadata (dict[str, str] | None): User metadata.
            max_size_bytes (int | None): When set, the backend
                aborts the write and removes any partial object
                once the limit is exceeded.
            validator (ContentValidator | None): Optional callable invoked with the
                first chunk. Returning ``False`` aborts the write
                before further bytes are persisted.

        Returns:
            UploadResult: Identifier + size + optional path/url.

        Raises:
            FileTooLargeException: When ``max_size_bytes`` is
                exceeded mid-write.
            InvalidFileTypeException: When ``validator`` rejects
                the first chunk.
        """
        ...

    async def delete(self, key: str) -> bool:
        """Delete the object under ``key``.

        Args:
            key (str): Storage key.

        Returns:
            bool: ``True`` when an object was removed, ``False``
            when it was already gone.
        """
        ...

    async def exists(self, key: str) -> bool:
        """Return whether an object lives at ``key``.

        Args:
            key (str): Storage key.

        Returns:
            bool: ``True`` when the object exists.
        """
        ...

    async def presigned_url(
        self,
        key: str,
        *,
        expires: timedelta = timedelta(hours=1),
    ) -> str | None:
        """Return a temporary download URL or ``None`` when unsupported.

        Args:
            key (str): Storage key.
            expires (timedelta): URL lifetime.

        Returns:
            str | None: Download URL. ``None`` when the backend
            cannot mint one (e.g. local disk).
        """
        ...


class LocalUploadStorage:
    """Disk-backed :class:`UploadStorage` using ``aiofiles``.

    Writes chunks under ``base_dir`` and refuses keys that resolve
    outside the base — same path-traversal protection
    :class:`UploadUtils` already applied. The ``base_dir`` is
    created (with parents) on instantiation.
    """

    def __init__(self, base_dir: Path | str) -> None:
        """Initialize.

        Args:
            base_dir (Path | str): Root directory for all writes.

        Raises:
            ImportError: When the ``[upload]`` extra is not
                installed.
        """
        if _aiofiles is None:
            raise ImportError(
                "LocalUploadStorage requires the [upload] extra. "
                "Install with `pip install tempest-fastapi-sdk[upload]`."
            )
        self.base_dir: Path = Path(base_dir).resolve()
        self.base_dir.mkdir(parents=True, exist_ok=True)

    def _resolve(self, key: str) -> Path:
        """Resolve ``key`` against ``base_dir`` and reject escapes."""
        from tempest_fastapi_sdk.exceptions.upload import InvalidFileTypeException

        candidate = (self.base_dir / key).resolve()
        if candidate != self.base_dir and self.base_dir not in candidate.parents:
            raise InvalidFileTypeException(
                details={"key": key, "reason": "escapes base_dir"},
            )
        return candidate

    async def write_stream(
        self,
        key: str,
        chunks: AsyncIterator[bytes],
        *,
        content_type: str = "application/octet-stream",
        metadata: dict[str, str] | None = None,
        max_size_bytes: int | None = None,
        validator: ContentValidator | None = None,
    ) -> UploadResult:
        """Persist ``chunks`` to ``base_dir / key``.

        ``content_type`` and ``metadata`` are accepted for protocol
        parity but ignored — the local filesystem has nowhere to
        store them.
        """
        from tempest_fastapi_sdk.exceptions.upload import (
            FileTooLargeException,
            InvalidFileTypeException,
        )

        del content_type, metadata  # not stored locally
        assert _aiofiles is not None, "guarded by __init__"
        target = self._resolve(key)
        target.parent.mkdir(parents=True, exist_ok=True)

        total = 0
        first = True
        try:
            async with _aiofiles.open(target, "wb") as out:
                async for chunk in chunks:
                    if first:
                        first = False
                        if validator is not None and not validator(chunk):
                            raise InvalidFileTypeException(
                                details={
                                    "reason": "validator rejected first chunk",
                                },
                            )
                    total += len(chunk)
                    if max_size_bytes is not None and total > max_size_bytes:
                        raise FileTooLargeException(
                            details={"max_size_bytes": max_size_bytes},
                        )
                    await out.write(chunk)
        except Exception:
            target.unlink(missing_ok=True)
            raise

        return UploadResult(
            key=key,
            size=total,
            path=target,
            url=None,
        )

    async def delete(self, key: str) -> bool:
        """Delete ``base_dir / key`` when present."""
        target = self._resolve(key)
        if not target.exists():
            return False
        target.unlink()
        return True

    async def exists(self, key: str) -> bool:
        """Return ``True`` when ``base_dir / key`` exists."""
        return self._resolve(key).exists()

    async def presigned_url(
        self,
        key: str,
        *,
        expires: timedelta = timedelta(hours=1),
    ) -> str | None:
        """Local storage cannot mint URLs — always ``None``."""
        del key, expires
        return None


class MinIOUploadStorage:
    """:class:`UploadStorage` backed by :class:`AsyncMinIOClient`.

    Reuses an existing client instance — typically the one wired
    on the FastAPI app — so the connection pool is shared. The
    bucket falls back to the client's ``default_bucket``.
    """

    def __init__(
        self,
        client: AsyncMinIOClient,
        *,
        bucket: str | None = None,
    ) -> None:
        """Initialize.

        Args:
            client (AsyncMinIOClient): A configured MinIO client.
            bucket (str | None): Target bucket. ``None`` uses the
                client's ``default_bucket``.
        """
        self.client: AsyncMinIOClient = client
        self.bucket: str | None = bucket

    async def write_stream(
        self,
        key: str,
        chunks: AsyncIterator[bytes],
        *,
        content_type: str = "application/octet-stream",
        metadata: dict[str, str] | None = None,
        max_size_bytes: int | None = None,
        validator: ContentValidator | None = None,
    ) -> UploadResult:
        """Buffer ``chunks``, validate, then ``put_object``.

        S3 needs the content length upfront, so the stream is
        materialized in memory before the upload starts. For
        very large objects, prefer ``presigned_put_url`` and let
        the client upload directly.
        """
        from tempest_fastapi_sdk.exceptions.upload import (
            FileTooLargeException,
            InvalidFileTypeException,
        )

        buffer = bytearray()
        first = True
        async for chunk in chunks:
            if first:
                first = False
                if validator is not None and not validator(chunk):
                    raise InvalidFileTypeException(
                        details={"reason": "validator rejected first chunk"},
                    )
            buffer.extend(chunk)
            if max_size_bytes is not None and len(buffer) > max_size_bytes:
                raise FileTooLargeException(
                    details={"max_size_bytes": max_size_bytes},
                )

        await self.client.put_object(
            key,
            bytes(buffer),
            bucket=self.bucket,
            content_type=content_type,
            metadata=metadata,
        )
        url = await self.client.presigned_get_url(key, bucket=self.bucket)
        return UploadResult(
            key=key,
            size=len(buffer),
            path=None,
            url=url,
        )

    async def delete(self, key: str) -> bool:
        """Delete the object — always returns ``True`` (S3 idempotent)."""
        await self.client.remove_object(key, bucket=self.bucket)
        return True

    async def exists(self, key: str) -> bool:
        """Stat-probe the object."""
        try:
            await self.client.stat_object(key, bucket=self.bucket)
            return True
        except Exception:
            return False

    async def presigned_url(
        self,
        key: str,
        *,
        expires: timedelta = timedelta(hours=1),
    ) -> str | None:
        """Return a presigned GET URL."""
        return await self.client.presigned_get_url(
            key,
            bucket=self.bucket,
            expires=expires,
        )


__all__: list[str] = [
    "ContentValidator",
    "LocalUploadStorage",
    "MinIOUploadStorage",
    "UploadResult",
    "UploadStorage",
]
