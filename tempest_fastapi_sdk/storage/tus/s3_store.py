"""Keep tus uploads in an S3-compatible bucket (MinIO, AWS S3, R2).

S3 has no append, and a multipart part must be at least 5 MiB except the
last — while a tus client chooses its own chunk size (``tempest-react-sdk``'s
field-app recipe sends 256 KiB). So each accepted ``PATCH`` becomes one small
object, keyed by the offset it starts at, and the finished file is assembled
once, when the last byte arrives:

* ``<prefix><id>/info.json`` — the record.
* ``<prefix><id>/chunks/<offset, 20 digits>`` — one object per ``PATCH``.
* ``<prefix><id>/data`` — the finished file, written by :meth:`S3TusStore.finalize`.
* ``<prefix>_idempotency/<digest>`` — the upload id a creation key produced.

Everything lives in the bucket, so a restart or another replica sees the
same uploads. The cost: every byte is written to the bucket twice (chunk,
then file) and read back once, and the request that sends the last chunk
waits for the assembly, which spools the file through a temporary file on
the server's disk.
"""

from __future__ import annotations

import tempfile
from collections.abc import AsyncIterator
from typing import BinaryIO, Protocol, cast

from tempest_fastapi_sdk.storage.tus.models import TusUpload
from tempest_fastapi_sdk.storage.tus.store import (
    TUS_STREAM_CHUNK_SIZE,
    TusStore,
    _valid_digest,
    valid_upload_id,
)

TUS_ASSEMBLY_SPOOL_BYTES: int = 8 * 1024 * 1024
"""Bytes :meth:`S3TusStore.finalize` keeps in memory before spooling to disk."""

DEFAULT_S3_TUS_MIN_CHUNK_SIZE: int = 64 * 1024
"""Default smallest non-final chunk :class:`S3TusStore` accepts: 64 KiB.

``tempest-react-sdk`` sends 5 MiB by default and 256 KiB in its field-app
recipe, both above it; a 1 GiB upload is then at most 16 384 chunk objects.
"""

_OFFSET_DIGITS: int = 20
"""Zero-padded width of a chunk key, so lexical order is offset order."""

_MISSING_CODES: frozenset[str] = frozenset({"NoSuchKey", "NoSuchObject"})
"""S3 error codes that mean the object is absent."""


class TusObjectClient(Protocol):
    """The object-storage calls :class:`S3TusStore` makes.

    :class:`~tempest_fastapi_sdk.storage.AsyncMinIOClient`,
    :class:`~tempest_fastapi_sdk.storage.BucketRoutedMinIOClient`,
    :class:`~tempest_fastapi_sdk.storage.LocalObjectStorageClient` and
    :class:`~tempest_fastapi_sdk.testing.fakes.FakeObjectStorageClient` all satisfy
    it. A missing object must raise :class:`FileNotFoundError` or an error
    whose ``code`` is ``"NoSuchKey"``.
    """

    async def put_object(
        self,
        key: str,
        data: bytes | BinaryIO,
        /,
        *,
        bucket: str | None = None,
        content_type: str = "application/octet-stream",
        length: int | None = None,
    ) -> str:
        """Store one object.

        Args:
            key (str): Object key.
            data (bytes | BinaryIO): Payload.
            bucket (str | None): Target bucket.
            content_type (str): MIME type.
            length (int | None): Payload size, required for a stream.

        Returns:
            str: The ETag.
        """
        ...

    async def stream_object(
        self,
        key: str,
        /,
        *,
        bucket: str | None = None,
        chunk_size: int = 64 * 1024,
    ) -> AsyncIterator[bytes]:
        """Read one object in chunks.

        Args:
            key (str): Object key.
            bucket (str | None): Source bucket.
            chunk_size (int): Bytes per chunk.

        Returns:
            AsyncIterator[bytes]: The payload.
        """
        ...

    async def list_objects(
        self,
        prefix: str = "",
        /,
        *,
        bucket: str | None = None,
    ) -> list[str]:
        """List keys under a prefix, recursively.

        Args:
            prefix (str): Key prefix.
            bucket (str | None): Source bucket.

        Returns:
            list[str]: The keys.
        """
        ...

    async def remove_object(self, key: str, /, *, bucket: str | None = None) -> None:
        """Delete one object; absent is not an error.

        Args:
            key (str): Object key.
            bucket (str | None): Target bucket.
        """
        ...


def _is_missing(error: BaseException) -> bool:
    """Whether an object-storage error means "no such object".

    Args:
        error (BaseException): What the client raised.

    Returns:
        bool: ``True`` for :class:`FileNotFoundError` and S3 ``NoSuchKey``.
    """
    if isinstance(error, FileNotFoundError):
        return True
    return getattr(error, "code", None) in _MISSING_CODES


class S3TusStore(TusStore):
    """Keep tus uploads in an S3-compatible bucket.

    See the module docstring for the layout and why chunks are separate
    objects. Install the ``[minio]`` extra for
    :class:`~tempest_fastapi_sdk.storage.AsyncMinIOClient`.

    Attributes:
        client (TusObjectClient): The object-storage client.
        bucket (str | None): Bucket override; ``None`` uses the client's
            default bucket.
        prefix (str): Key prefix every object of this store lives under.
        min_chunk_size (int): Smallest non-final chunk the router accepts.
    """

    def __init__(
        self,
        client: TusObjectClient,
        *,
        bucket: str | None = None,
        prefix: str = "tus/",
        min_chunk_size: int = DEFAULT_S3_TUS_MIN_CHUNK_SIZE,
    ) -> None:
        """Initialize the store.

        Args:
            client (TusObjectClient): The object-storage client, e.g. an
                :class:`~tempest_fastapi_sdk.storage.AsyncMinIOClient`.
            bucket (str | None): Bucket override.
            prefix (str): Key prefix, ``"tus/"`` by default. A trailing
                ``/`` is added when missing.
            min_chunk_size (int): Smallest ``PATCH`` body the router accepts,
                except the one that ends the upload — every chunk is an
                object, and this bounds how many one upload can create
                (``length / min_chunk_size``). ``0`` accepts any size.
        """
        self.client: TusObjectClient = client
        self.bucket: str | None = bucket
        self.min_chunk_size: int = min_chunk_size
        self.prefix: str = (
            prefix if not prefix or prefix.endswith("/") else f"{prefix}/"
        )

    def _info_key(self, upload_id: str) -> str:
        """Key of an upload's record.

        Args:
            upload_id (str): A valid id.

        Returns:
            str: ``<prefix><id>/info.json``.
        """
        return f"{self.prefix}{upload_id}/info.json"

    def _chunk_prefix(self, upload_id: str) -> str:
        """Key prefix of an upload's chunks.

        Args:
            upload_id (str): A valid id.

        Returns:
            str: ``<prefix><id>/chunks/``.
        """
        return f"{self.prefix}{upload_id}/chunks/"

    def _idempotency_key(self, digest: str) -> str:
        """Key of an idempotency entry.

        Args:
            digest (str): A valid digest.

        Returns:
            str: ``<prefix>_idempotency/<digest>``.
        """
        return f"{self.prefix}_idempotency/{digest}"

    def object_key(self, upload_id: str) -> str:
        """Key of a finished upload's file, for the service to copy or serve.

        Args:
            upload_id (str): The upload id.

        Returns:
            str: ``<prefix><id>/data``.

        Raises:
            ValueError: When ``upload_id`` is not a valid id.
        """
        if not valid_upload_id(upload_id):
            raise ValueError("invalid tus upload id")
        return f"{self.prefix}{upload_id}/data"

    async def _read(self, key: str) -> bytes | None:
        """Read a small object whole.

        Args:
            key (str): Object key.

        Returns:
            bytes | None: The payload, or ``None`` when absent.
        """
        try:
            stream = await self.client.stream_object(key, bucket=self.bucket)
            return b"".join([block async for block in stream])
        except Exception as error:
            if _is_missing(error):
                return None
            raise

    async def create(self, upload: TusUpload) -> None:
        """Write the record of a new upload.

        Args:
            upload (TusUpload): The record.
        """
        await self.save(upload)

    async def load(self, upload_id: str) -> TusUpload | None:
        """Read a record from the bucket.

        Args:
            upload_id (str): The id.

        Returns:
            TusUpload | None: The record, or ``None``.
        """
        if not valid_upload_id(upload_id):
            return None
        raw = await self._read(self._info_key(upload_id))
        return None if raw is None else TusUpload.from_json(raw)

    async def save(self, upload: TusUpload) -> None:
        """Overwrite a record in the bucket.

        Args:
            upload (TusUpload): The record.

        Raises:
            ValueError: When ``upload.id`` is not a valid id.
        """
        if not valid_upload_id(upload.id):
            raise ValueError("invalid tus upload id")
        await self.client.put_object(
            self._info_key(upload.id),
            upload.to_json().encode("utf-8"),
            bucket=self.bucket,
            content_type="application/json",
        )

    async def write(self, upload: TusUpload, data: BinaryIO, size: int) -> None:
        """Store a chunk as its own object, keyed by its offset.

        A chunk an earlier attempt wrote at the same offset is replaced.

        Args:
            upload (TusUpload): The upload.
            data (BinaryIO): The chunk stream.
            size (int): Bytes in it.
        """
        if size == 0:
            return
        key = f"{self._chunk_prefix(upload.id)}{upload.offset:0{_OFFSET_DIGITS}d}"
        await self.client.put_object(key, data, bucket=self.bucket, length=size)

    async def finalize(self, upload: TusUpload) -> None:
        """Concatenate the chunks into ``<prefix><id>/data``.

        Only the chain of chunks that starts at ``0`` and ends at
        ``upload.length`` is read; a chunk outside it (an earlier attempt at
        an offset that a later write superseded) is skipped. The chunks stay
        until :meth:`discard_parts`, which the router calls after saving the
        complete record: a failure here, or before that save, leaves every
        chunk in place, and the retried last ``PATCH`` assembles again.

        Args:
            upload (TusUpload): The complete upload.

        Raises:
            RuntimeError: When the stored chunks do not cover the upload.
        """
        chunk_prefix = self._chunk_prefix(upload.id)
        keys = sorted(await self.client.list_objects(chunk_prefix, bucket=self.bucket))
        by_offset: dict[int, str] = {}
        for key in keys:
            suffix = key[len(chunk_prefix) :]
            if suffix.isdigit():
                by_offset[int(suffix)] = key
        with tempfile.SpooledTemporaryFile(max_size=TUS_ASSEMBLY_SPOOL_BYTES) as spool:
            position = 0
            while position < upload.length:
                chunk_key = by_offset.get(position)
                if chunk_key is None:
                    raise RuntimeError(
                        f"tus upload {upload.id} is missing bytes at {position}"
                    )
                stream = await self.client.stream_object(chunk_key, bucket=self.bucket)
                async for block in stream:
                    spool.write(block)
                    position += len(block)
            if position != upload.length:
                raise RuntimeError(f"tus upload {upload.id} chunks overrun its length")
            spool.seek(0)
            await self.client.put_object(
                self.object_key(upload.id),
                cast(BinaryIO, spool),
                bucket=self.bucket,
                length=upload.length,
            )

    async def discard_parts(self, upload: TusUpload) -> None:
        """Delete the chunk objects of a complete upload.

        Runs after the router saved the complete record, so a failure here
        leaves stray chunks — removed by :meth:`delete` — but never an upload
        stuck before completion.

        Args:
            upload (TusUpload): The complete upload.
        """
        keys = await self.client.list_objects(
            self._chunk_prefix(upload.id), bucket=self.bucket
        )
        for key in keys:
            await self.client.remove_object(key, bucket=self.bucket)

    async def stream(
        self,
        upload_id: str,
        *,
        chunk_size: int = TUS_STREAM_CHUNK_SIZE,
    ) -> AsyncIterator[bytes]:
        """Read a finished upload's file from the bucket.

        Args:
            upload_id (str): The upload id.
            chunk_size (int): Bytes per chunk.

        Yields:
            bytes: The next chunk.
        """
        source = await self.client.stream_object(
            self.object_key(upload_id),
            bucket=self.bucket,
            chunk_size=chunk_size,
        )
        async for block in source:
            yield block

    async def delete(self, upload_id: str) -> None:
        """Remove every object of the upload and its idempotency entry.

        Args:
            upload_id (str): The id.
        """
        if not valid_upload_id(upload_id):
            return
        upload = await self.load(upload_id)
        if upload is not None and upload.idempotency_digest is not None:
            key = self._idempotency_key(upload.idempotency_digest)
            current = await self._read(key)
            if current is not None and current.decode("utf-8") == upload_id:
                await self.client.remove_object(key, bucket=self.bucket)
        keys = await self.client.list_objects(
            f"{self.prefix}{upload_id}/", bucket=self.bucket
        )
        for key in keys:
            await self.client.remove_object(key, bucket=self.bucket)

    async def list_ids(self) -> list[str]:
        """List the ids that have a record object.

        Returns:
            list[str]: The ids, sorted; ``[]`` when there are none.
        """
        keys = await self.client.list_objects(self.prefix, bucket=self.bucket)
        ids: set[str] = set()
        for key in keys:
            relative = key[len(self.prefix) :]
            upload_id, _, rest = relative.partition("/")
            if rest == "info.json" and valid_upload_id(upload_id):
                ids.add(upload_id)
        return sorted(ids)

    async def find_idempotent(self, digest: str) -> str | None:
        """Read an idempotency entry from the bucket.

        Args:
            digest (str): SHA-256 hex digest.

        Returns:
            str | None: The upload id, or ``None``.
        """
        if not _valid_digest(digest):
            return None
        raw = await self._read(self._idempotency_key(digest))
        return None if raw is None else raw.decode("utf-8").strip() or None

    async def remember_idempotent(self, digest: str, upload_id: str) -> None:
        """Write an idempotency entry to the bucket.

        Args:
            digest (str): SHA-256 hex digest.
            upload_id (str): The upload it created.

        Raises:
            ValueError: When the digest or the id is malformed.
        """
        if not _valid_digest(digest) or not valid_upload_id(upload_id):
            raise ValueError("invalid idempotency digest or upload id")
        await self.client.put_object(
            self._idempotency_key(digest),
            upload_id.encode("utf-8"),
            bucket=self.bucket,
            content_type="text/plain",
        )


__all__: list[str] = [
    "DEFAULT_S3_TUS_MIN_CHUNK_SIZE",
    "TUS_ASSEMBLY_SPOOL_BYTES",
    "S3TusStore",
    "TusObjectClient",
]
