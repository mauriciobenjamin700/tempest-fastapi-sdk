"""Where a tus upload's bytes and record live between requests.

:class:`TusStore` is the contract the router writes through;
:class:`LocalTusStore` keeps both on the server's disk. The S3/MinIO
backend is :class:`~tempest_fastapi_sdk.storage.tus.S3TusStore`.

Every method reads and writes the persisted state, never a copy in memory,
so a store rebuilt after a restart (or by another process pointing at the
same directory) sees every upload the previous one accepted.
"""

from __future__ import annotations

import asyncio
import os
import tempfile
from abc import ABC, abstractmethod
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from datetime import UTC, datetime
from pathlib import Path
from typing import BinaryIO

from tempest_fastapi_sdk.storage.tus.models import TUS_UPLOAD_ID_PATTERN, TusUpload

TUS_STREAM_CHUNK_SIZE: int = 64 * 1024
"""Bytes per chunk :meth:`TusStore.stream` yields by default."""

_DIGEST_LENGTH: int = 64
"""Length of a SHA-256 hex digest, the only idempotency key a store accepts."""


def valid_upload_id(upload_id: str) -> bool:
    """Whether ``upload_id`` has the shape of an id the router generates.

    Args:
        upload_id (str): Candidate id, usually a path parameter.

    Returns:
        bool: ``True`` when it matches :data:`TUS_UPLOAD_ID_PATTERN`.
    """
    return bool(TUS_UPLOAD_ID_PATTERN.match(upload_id))


def _valid_digest(digest: str) -> bool:
    """Whether ``digest`` is a lowercase SHA-256 hex digest.

    Args:
        digest (str): Candidate digest.

    Returns:
        bool: ``True`` for 64 lowercase hex characters.
    """
    return len(digest) == _DIGEST_LENGTH and all(
        c in "0123456789abcdef" for c in digest
    )


def _atomic_write(target: Path, text: str) -> None:
    """Replace ``target`` with ``text`` through a uniquely named temporary file.

    The temporary name is unique per call (``mkstemp``), so two writers of
    the same record — two processes on one directory, or a sweep beside a
    request — never rename each other's half-written file.

    Args:
        target (Path): The file to replace.
        text (str): Its new content.
    """
    descriptor, temporary = tempfile.mkstemp(
        dir=target.parent,
        prefix=f".{target.name}.",
        suffix=".tmp",
    )
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
            handle.write(text)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, target)
    except BaseException:
        Path(temporary).unlink(missing_ok=True)
        raise


class _KeyedLocks:
    """One :class:`asyncio.Lock` per key, dropped when nobody holds or waits on it."""

    def __init__(self) -> None:
        """Initialize the empty lock table."""
        self._locks: dict[str, tuple[asyncio.Lock, int]] = {}

    @asynccontextmanager
    async def hold(self, key: str) -> AsyncIterator[None]:
        """Hold the lock of ``key`` for the ``async with`` body, waiting for it.

        Args:
            key (str): What to serialize on.

        Yields:
            None: While the lock is held.
        """
        lock, users = self._locks.get(key, (asyncio.Lock(), 0))
        self._locks[key] = (lock, users + 1)
        try:
            async with lock:
                yield
        finally:
            current_lock, current_users = self._locks[key]
            if current_users <= 1:
                del self._locks[key]
            else:
                self._locks[key] = (current_lock, current_users - 1)

    @asynccontextmanager
    async def try_hold(self, key: str) -> AsyncIterator[bool]:
        """Hold the lock of ``key`` only if nobody holds or waits on it.

        Args:
            key (str): What to serialize on.

        Yields:
            bool: ``True`` while the lock is held; ``False``, without waiting,
            when someone else has it.
        """
        if key in self._locks:
            yield False
            return
        async with self.hold(key):
            yield True


class TusStore(ABC):
    """Persistence contract of :func:`~tempest_fastapi_sdk.storage.tus.make_tus_router`.

    Writes to one upload are serialized by a lock table that lives on the
    store instance and is shared by every router built over it and by
    :meth:`purge_expired`; it covers one process. A method given an id that
    fails :func:`valid_upload_id` treats it as absent instead of building a
    path or key from it.

    Subclass it to keep uploads elsewhere; :meth:`purge_expired` and
    :meth:`list_uploads` come for free from the abstract methods.
    """

    min_chunk_size: int = 0
    """Smallest chunk the router accepts, except the one that ends the upload."""

    def _upload_locks(self) -> _KeyedLocks:
        """Return this store's lock table, creating it on first use.

        Returns:
            _KeyedLocks: The table shared by the router and the sweep.
        """
        table = self.__dict__.get("_tus_locks")
        if not isinstance(table, _KeyedLocks):
            table = _KeyedLocks()
            self.__dict__["_tus_locks"] = table
        return table

    @abstractmethod
    async def create(self, upload: TusUpload) -> None:
        """Persist a new upload with no bytes.

        Args:
            upload (TusUpload): The record, ``offset`` ``0``.
        """

    @abstractmethod
    async def load(self, upload_id: str) -> TusUpload | None:
        """Read an upload's record.

        Args:
            upload_id (str): The id.

        Returns:
            TusUpload | None: The record, or ``None`` when absent or the id
            is malformed.
        """

    @abstractmethod
    async def save(self, upload: TusUpload) -> None:
        """Overwrite an upload's record.

        Args:
            upload (TusUpload): The record to persist.
        """

    @abstractmethod
    async def write(self, upload: TusUpload, data: BinaryIO, size: int) -> None:
        """Store ``size`` bytes of ``data`` at ``upload.offset``.

        Does not change the record: the router saves the new offset after
        this returns, so a failure here leaves the upload where it was.

        Args:
            upload (TusUpload): The upload, with the offset the bytes start at.
            data (BinaryIO): A stream positioned at the first byte.
            size (int): How many bytes to take from it.
        """

    @abstractmethod
    async def finalize(self, upload: TusUpload) -> None:
        """Turn the stored chunks into the finished file.

        Called once ``offset == length``, before ``on_complete``.

        Args:
            upload (TusUpload): The complete upload.
        """

    async def discard_parts(self, upload: TusUpload) -> None:
        """Drop whatever :meth:`finalize` assembled from, once the record says complete.

        Called after the complete record is saved, so a failure here never
        leaves an upload that cannot finish; a store with nothing to drop
        keeps this no-op.

        Args:
            upload (TusUpload): The complete upload.
        """
        return None

    @abstractmethod
    def stream(
        self,
        upload_id: str,
        *,
        chunk_size: int = TUS_STREAM_CHUNK_SIZE,
    ) -> AsyncIterator[bytes]:
        """Read a finished upload's bytes.

        Args:
            upload_id (str): The id of a finalized upload.
            chunk_size (int): Bytes per yielded chunk.

        Returns:
            AsyncIterator[bytes]: The file, in order.
        """

    @abstractmethod
    async def delete(self, upload_id: str) -> None:
        """Remove an upload's bytes, record and idempotency entry.

        Idempotent: an absent upload is not an error.

        Args:
            upload_id (str): The id.
        """

    @abstractmethod
    async def list_ids(self) -> list[str]:
        """List the ids of every stored upload.

        Returns:
            list[str]: The ids; ``[]`` when there are none.
        """

    @abstractmethod
    async def find_idempotent(self, digest: str) -> str | None:
        """Look up the upload a creation ``Idempotency-Key`` produced.

        Args:
            digest (str): SHA-256 hex of owner and key.

        Returns:
            str | None: The upload id, or ``None``.
        """

    @abstractmethod
    async def remember_idempotent(self, digest: str, upload_id: str) -> None:
        """Record which upload a creation ``Idempotency-Key`` produced.

        Args:
            digest (str): SHA-256 hex of owner and key.
            upload_id (str): The upload it created.
        """

    async def list_uploads(self) -> list[TusUpload]:
        """Load every stored upload's record.

        Returns:
            list[TusUpload]: The records; ``[]`` when there are none. An id
            whose record vanished between the listing and the read is skipped.
        """
        uploads: list[TusUpload] = []
        for upload_id in await self.list_ids():
            upload = await self.load(upload_id)
            if upload is not None:
                uploads.append(upload)
        return uploads

    async def purge_expired(self, *, now: datetime | None = None) -> list[str]:
        """Delete every incomplete upload past its ``expires_at``.

        Run it on a schedule (a TaskIQ periodic task, a cron). Each upload is
        re-read under the same per-upload lock the router writes under, and one
        with a request in progress is skipped. A complete
        upload is never purged, notified or not: its bytes belong to the
        service, which deletes them with :meth:`delete` when done.

        Args:
            now (datetime | None): The moment to compare with. Defaults to
                the current UTC time.

        Returns:
            list[str]: The ids deleted; ``[]`` when none had expired.
        """
        moment = now or datetime.now(UTC)
        locks = self._upload_locks()
        purged: list[str] = []
        for candidate in await self.list_uploads():
            if not candidate.is_expired(moment):
                continue
            async with locks.try_hold(candidate.id) as held:
                if not held:
                    continue
                upload = await self.load(candidate.id)
                if upload is not None and upload.is_expired(moment):
                    await self.delete(upload.id)
                    purged.append(upload.id)
        return purged


class LocalTusStore(TusStore):
    """Keep tus uploads in a directory of the server.

    Layout under ``root``: ``<id>.json`` holds the record (replaced
    atomically with ``os.replace``), ``<id>.bin`` the bytes, written in place
    at the offset with ``fsync``, and ``_idempotency/<digest>`` the upload id a
    creation key produced. File names come from the generated id only, never
    from ``Upload-Metadata``, so a client name cannot escape ``root``.

    Every call runs its blocking I/O in ``asyncio.to_thread``.

    Attributes:
        root (Path): The directory, created on first use.
    """

    def __init__(self, root: str | Path) -> None:
        """Initialize the store.

        Args:
            root (str | Path): Directory to keep uploads in. Created with its
                parents when missing.
        """
        self.root: Path = Path(root)

    def _record_path(self, upload_id: str) -> Path:
        """Path of an upload's record file.

        Args:
            upload_id (str): A valid id.

        Returns:
            Path: ``root/<id>.json``.
        """
        return self.root / f"{upload_id}.json"

    def data_path(self, upload_id: str) -> Path:
        """Path of an upload's bytes on disk.

        After the upload completes this is the finished file, ready for the
        service to move or read.

        Args:
            upload_id (str): The upload id.

        Returns:
            Path: ``root/<id>.bin``.

        Raises:
            ValueError: When ``upload_id`` is not a valid id.
        """
        if not valid_upload_id(upload_id):
            raise ValueError("invalid tus upload id")
        return self.root / f"{upload_id}.bin"

    def _idempotency_path(self, digest: str) -> Path:
        """Path of an idempotency entry.

        Args:
            digest (str): A valid SHA-256 hex digest.

        Returns:
            Path: ``root/_idempotency/<digest>``.
        """
        return self.root / "_idempotency" / digest

    def _write_record(self, upload: TusUpload) -> None:
        """Write a record atomically.

        Args:
            upload (TusUpload): The record.
        """
        self.root.mkdir(parents=True, exist_ok=True)
        target = self._record_path(upload.id)
        _atomic_write(target, upload.to_json())

    async def create(self, upload: TusUpload) -> None:
        """Create the empty data file and the record.

        Args:
            upload (TusUpload): The new record.

        Raises:
            ValueError: When ``upload.id`` is not a valid id.
        """
        path = self.data_path(upload.id)

        def _create() -> None:
            self.root.mkdir(parents=True, exist_ok=True)
            path.touch(exist_ok=False)
            self._write_record(upload)

        await asyncio.to_thread(_create)

    async def load(self, upload_id: str) -> TusUpload | None:
        """Read a record from disk.

        Args:
            upload_id (str): The id.

        Returns:
            TusUpload | None: The record, or ``None``.
        """
        if not valid_upload_id(upload_id):
            return None
        path = self._record_path(upload_id)

        def _load() -> TusUpload | None:
            try:
                return TusUpload.from_json(path.read_text(encoding="utf-8"))
            except FileNotFoundError:
                return None

        return await asyncio.to_thread(_load)

    async def save(self, upload: TusUpload) -> None:
        """Replace a record on disk.

        Args:
            upload (TusUpload): The record.

        Raises:
            ValueError: When ``upload.id`` is not a valid id.
        """
        if not valid_upload_id(upload.id):
            raise ValueError("invalid tus upload id")
        await asyncio.to_thread(self._write_record, upload)

    async def write(self, upload: TusUpload, data: BinaryIO, size: int) -> None:
        """Write bytes at the offset, cutting anything a crashed write left past it.

        Args:
            upload (TusUpload): The upload.
            data (BinaryIO): The chunk stream.
            size (int): Bytes to copy.
        """
        path = self.data_path(upload.id)

        def _write() -> None:
            with path.open("r+b") as handle:
                handle.truncate(upload.offset)
                handle.seek(upload.offset)
                remaining = size
                while remaining > 0:
                    block = data.read(min(remaining, TUS_STREAM_CHUNK_SIZE))
                    if not block:
                        raise ValueError("chunk stream ended before its declared size")
                    handle.write(block)
                    remaining -= len(block)
                handle.flush()
                os.fsync(handle.fileno())

        await asyncio.to_thread(_write)

    async def finalize(self, upload: TusUpload) -> None:
        """Nothing to assemble: the data file already is the finished file.

        Args:
            upload (TusUpload): The complete upload.
        """
        return None

    async def stream(
        self,
        upload_id: str,
        *,
        chunk_size: int = TUS_STREAM_CHUNK_SIZE,
    ) -> AsyncIterator[bytes]:
        """Read the data file in chunks.

        Args:
            upload_id (str): The upload id.
            chunk_size (int): Bytes per chunk.

        Yields:
            bytes: The next chunk.
        """
        path = self.data_path(upload_id)
        handle = await asyncio.to_thread(path.open, "rb")
        try:
            while True:
                block = await asyncio.to_thread(handle.read, chunk_size)
                if not block:
                    break
                yield block
        finally:
            await asyncio.to_thread(handle.close)

    async def delete(self, upload_id: str) -> None:
        """Remove the data file, the record and the idempotency entry.

        Args:
            upload_id (str): The id.
        """
        upload = await self.load(upload_id)
        if not valid_upload_id(upload_id):
            return

        def _delete() -> None:
            if upload is not None and upload.idempotency_digest is not None:
                entry = self._idempotency_path(upload.idempotency_digest)
                try:
                    if entry.read_text(encoding="utf-8") == upload_id:
                        entry.unlink(missing_ok=True)
                except FileNotFoundError:
                    pass
            self.data_path(upload_id).unlink(missing_ok=True)
            self._record_path(upload_id).unlink(missing_ok=True)

        await asyncio.to_thread(_delete)

    async def list_ids(self) -> list[str]:
        """List the ids that have a record file.

        Returns:
            list[str]: The ids, sorted; ``[]`` when the directory is empty or
            missing.
        """

        def _list() -> list[str]:
            if not self.root.is_dir():
                return []
            return sorted(
                path.stem
                for path in self.root.glob("*.json")
                if valid_upload_id(path.stem)
            )

        return await asyncio.to_thread(_list)

    async def find_idempotent(self, digest: str) -> str | None:
        """Read an idempotency entry.

        Args:
            digest (str): SHA-256 hex digest.

        Returns:
            str | None: The upload id, or ``None``.
        """
        if not _valid_digest(digest):
            return None
        path = self._idempotency_path(digest)

        def _find() -> str | None:
            try:
                return path.read_text(encoding="utf-8").strip() or None
            except FileNotFoundError:
                return None

        return await asyncio.to_thread(_find)

    async def remember_idempotent(self, digest: str, upload_id: str) -> None:
        """Write an idempotency entry atomically.

        Args:
            digest (str): SHA-256 hex digest.
            upload_id (str): The upload it created.

        Raises:
            ValueError: When the digest or the id is malformed.
        """
        if not _valid_digest(digest) or not valid_upload_id(upload_id):
            raise ValueError("invalid idempotency digest or upload id")
        path = self._idempotency_path(digest)

        def _remember() -> None:
            path.parent.mkdir(parents=True, exist_ok=True)
            _atomic_write(path, upload_id)

        await asyncio.to_thread(_remember)


__all__: list[str] = [
    "TUS_STREAM_CHUNK_SIZE",
    "LocalTusStore",
    "TusStore",
    "valid_upload_id",
]
