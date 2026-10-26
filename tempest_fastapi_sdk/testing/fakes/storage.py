"""An object-storage client that keeps every object in a dict.

Wire it where :class:`~tempest_fastapi_sdk.privacy.SubjectObjectStorage` takes
its client and export, retention and erasure run with no MinIO — including the
branch that is hard to reach against a real store: a key the store refuses to
delete, which is what makes ``delete_all`` raise ``SubjectErasureError``.
"""

from __future__ import annotations

import hashlib
from collections.abc import AsyncIterator, Iterable, Mapping
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from types import MappingProxyType
from typing import BinaryIO
from urllib.parse import urlencode

from tempest_fastapi_sdk.storage.minio_client import ObjectDeleteError, ObjectStat
from tempest_fastapi_sdk.storage.presign import (
    PresignedPost,
    check_post_limits,
    download_overrides,
)
from tempest_fastapi_sdk.testing.fakes._control import _Steerable

DEFAULT_FAKE_BUCKET: str = "fake-bucket"
"""Bucket an operation lands in when it passes no ``bucket``."""

FAKE_STREAM_CHUNK_SIZE: int = 64 * 1024
"""Chunk size of ``stream_object``, the ``AsyncMinIOClient`` default."""


@dataclass(frozen=True, slots=True)
class _StoredObject:
    """What the fake keeps beside an object's bytes.

    Attributes:
        content_type (str): The MIME type given to ``put_object``.
        metadata (dict[str, str]): The user metadata given to ``put_object``.
        last_modified (datetime): When ``put_object`` stored it, in UTC.
    """

    content_type: str
    metadata: dict[str, str]
    last_modified: datetime


class FakeObjectStorageClient(_Steerable):
    """A :class:`~tempest_fastapi_sdk.privacy.SubjectStorageClient` in memory.

    Same signatures and return shapes as the
    :class:`~tempest_fastapi_sdk.storage.AsyncMinIOClient` calls it stands in
    for: ``put_object`` answers an ETag, ``list_objects`` the keys,
    ``remove_objects`` the :class:`~tempest_fastapi_sdk.storage.ObjectDeleteError`
    of each key the store refused.

    Example:

        >>> client = FakeObjectStorageClient()
        >>> storage = SubjectObjectStorage(client, prefix="users")
        >>> await storage.put(42, "avatar.png", b"png")
        'users/42/avatar.png'
        >>> client.fail_delete("users/42/avatar.png")
        >>> await storage.delete_all(42)
        Traceback (most recent call last):
        SubjectErasureError: 1 object(s) of subject '42' were not deleted: ...

    Attributes:
        default_bucket (str): The bucket an operation without ``bucket`` uses.
        deleted (list[str]): Keys actually deleted, in order.
        calls (list[str]): Methods that ran, in order.
    """

    def __init__(self, *, default_bucket: str = DEFAULT_FAKE_BUCKET) -> None:
        """Start with no objects.

        Args:
            default_bucket (str): The bucket used when a call passes none.
        """
        super().__init__()
        self.default_bucket: str = default_bucket
        self.deleted: list[str] = []
        self._buckets: dict[str, dict[str, bytes]] = {}
        self._stored: dict[tuple[str, str], _StoredObject] = {}
        self._refused: set[str] = set()

    @property
    def objects(self) -> Mapping[str, bytes]:
        """Every object in the default bucket, by key.

        Returns:
            Mapping[str, bytes]: A read-only view.
        """
        return self.bucket_objects(self.default_bucket)

    def bucket_objects(self, bucket: str) -> Mapping[str, bytes]:
        """Every object in ``bucket``, by key.

        Args:
            bucket (str): The bucket.

        Returns:
            Mapping[str, bytes]: A read-only view, empty for an unknown
            bucket.
        """
        return MappingProxyType(self._buckets.get(bucket, {}))

    def fail_delete(self, *keys: str) -> None:
        """Make the store refuse to delete ``keys``.

        Args:
            *keys (str): Keys to refuse. ``remove_objects`` reports them as
                ``AccessDenied`` and keeps them; ``remove_object`` raises
                :class:`PermissionError` for them.

        Steering, not a call the service made: it does not consume a queued
        :meth:`fail_next`.
        """
        self._refused.update(keys)

    def _bucket(self, bucket: str | None) -> dict[str, bytes]:
        """Return the dict that holds ``bucket``, creating it on first use.

        Args:
            bucket (str | None): The bucket, ``None`` for the default.

        Returns:
            dict[str, bytes]: The live dict.
        """
        return self._buckets.setdefault(bucket or self.default_bucket, {})

    async def put_object(
        self,
        key: str,
        data: bytes | BinaryIO,
        *,
        bucket: str | None = None,
        content_type: str = "application/octet-stream",
        metadata: dict[str, str] | None = None,
        length: int | None = None,
    ) -> str:
        """Store one object.

        Args:
            key (str): Object key.
            data (bytes | BinaryIO): The payload; a stream is read to the end.
            bucket (str | None): Target bucket.
            content_type (str): MIME type, reported back by
                :meth:`stat_object`.
            metadata (dict[str, str] | None): User metadata, reported back
                by :meth:`stat_object`.
            length (int | None): Accepted for signature parity; the fake
                reads the whole stream.

        Returns:
            str: An ETag — the MD5 of the payload, as S3 answers for a
            single-part upload.

        Raises:
            BaseException: Whatever :meth:`fail_next` queued.
        """
        self._record("put_object")
        payload = data if isinstance(data, bytes) else data.read()
        self._bucket(bucket)[key] = payload
        self._stored[(bucket or self.default_bucket, key)] = _StoredObject(
            content_type=content_type,
            metadata=dict(metadata or {}),
            last_modified=datetime.now(UTC),
        )
        return hashlib.md5(payload, usedforsecurity=False).hexdigest()

    def _payload(self, key: str, bucket: str | None) -> bytes:
        """Return a stored object's bytes, or fail the way S3 does.

        Args:
            key (str): Object key.
            bucket (str | None): Source bucket.

        Returns:
            bytes: The payload.

        Raises:
            FileNotFoundError: When ``key`` is not stored in ``bucket``.
        """
        payload = self._bucket(bucket).get(key)
        if payload is None:
            raise FileNotFoundError(f"NoSuchKey: {key}")
        return payload

    async def presigned_get_url(
        self,
        key: str,
        *,
        bucket: str | None = None,
        expires: timedelta = timedelta(hours=1),
        filename: str | None = None,
        media_type: str | None = None,
        as_attachment: bool | None = None,
    ) -> str:
        """Build a fake download URL.

        The overrides are echoed in the query as the S3 ``response-*``
        parameters, so a test can assert what the real link would sign.

        Args:
            key (str): Object key.
            bucket (str | None): Source bucket.
            expires (timedelta): URL lifetime, echoed in the URL.
            filename (str | None): Name presented to the client.
            media_type (str | None): ``Content-Type`` override.
            as_attachment (bool | None): Download or ``inline``.

        Returns:
            str: ``https://fake-storage/<bucket>/<key>?expires=<seconds>``,
            plus the overrides.

        Raises:
            BaseException: Whatever :meth:`fail_next` queued.
            ValueError: When ``media_type`` is not a bare ``type/subtype``.
        """
        self._record("presigned_get_url")
        overrides = download_overrides(
            key,
            filename=filename,
            media_type=media_type,
            as_attachment=as_attachment,
        )
        seconds = int(expires.total_seconds())
        query = urlencode({"expires": seconds, **overrides})
        return f"https://fake-storage/{bucket or self.default_bucket}/{key}?{query}"

    async def presigned_post_policy(
        self,
        key: str,
        *,
        max_bytes: int,
        content_type: str | None = None,
        min_bytes: int = 0,
        bucket: str | None = None,
        expires: timedelta = timedelta(minutes=15),
    ) -> PresignedPost:
        """Build a fake form upload.

        Nothing listens on the URL, as with :meth:`presigned_put_url`; the
        fields echo the limits a real policy would sign.

        Args:
            key (str): Destination object key.
            max_bytes (int): Largest accepted file.
            content_type (str | None): The only ``Content-Type`` accepted.
            min_bytes (int): Smallest accepted file.
            bucket (str | None): Target bucket.
            expires (timedelta): Lifetime, echoed in the fields.

        Returns:
            PresignedPost: ``https://fake-storage/<bucket>`` with ``key``,
            ``Content-Type`` when given, and the limits as fields.

        Raises:
            BaseException: Whatever :meth:`fail_next` queued.
            ValueError: When the size range is empty or inverted.
        """
        self._record("presigned_post_policy")
        check_post_limits(min_bytes=min_bytes, max_bytes=max_bytes)
        fields: dict[str, str] = {
            "key": key,
            "x-fake-content-length-range": f"{min_bytes},{max_bytes}",
            "x-fake-expires": str(int(expires.total_seconds())),
        }
        if content_type is not None:
            fields["Content-Type"] = content_type
        return PresignedPost(
            url=f"https://fake-storage/{bucket or self.default_bucket}",
            max_bytes=max_bytes,
            fields=fields,
        )

    async def presigned_put_url(
        self,
        key: str,
        *,
        bucket: str | None = None,
        expires: timedelta = timedelta(minutes=15),
    ) -> str:
        """Build a fake upload URL.

        Nothing listens on it: a test that needs the object stored calls
        :meth:`put_object` with the key the URL names, standing in for the
        client's ``PUT``.

        Args:
            key (str): Destination object key.
            bucket (str | None): Target bucket.
            expires (timedelta): URL lifetime, echoed in the URL.

        Returns:
            str: ``https://fake-storage/<bucket>/<key>?method=PUT&expires=<seconds>``.

        Raises:
            BaseException: Whatever :meth:`fail_next` queued.
        """
        self._record("presigned_put_url")
        seconds = int(expires.total_seconds())
        return (
            f"https://fake-storage/{bucket or self.default_bucket}/{key}"
            f"?method=PUT&expires={seconds}"
        )

    async def stat_object(
        self,
        key: str,
        *,
        bucket: str | None = None,
    ) -> ObjectStat:
        """Describe a stored object.

        Args:
            key (str): Object key.
            bucket (str | None): Source bucket.

        Returns:
            ObjectStat: Size, MD5 ETag, the content type and metadata given
            to :meth:`put_object`, and when it was stored. ``raw`` is
            ``None``: there is no ``minio`` object behind the fake.

        Raises:
            FileNotFoundError: When ``key`` is not stored.
            BaseException: Whatever :meth:`fail_next` queued.
        """
        self._record("stat_object")
        target = bucket or self.default_bucket
        payload = self._payload(key, bucket)
        stored = self._stored[(target, key)]
        return ObjectStat(
            bucket=target,
            key=key,
            size=len(payload),
            etag=hashlib.md5(payload, usedforsecurity=False).hexdigest(),
            content_type=stored.content_type,
            last_modified=stored.last_modified,
            metadata=dict(stored.metadata),
            raw=None,  # type: ignore[arg-type]
        )

    async def stream_object(
        self,
        key: str,
        *,
        bucket: str | None = None,
        offset: int = 0,
        length: int | None = None,
    ) -> AsyncIterator[bytes]:
        """Open a stored object, or one byte range of it, as a chunk stream.

        The range is cut before streaming, the way a ``Range`` request makes
        the store send only that slice: a ``length`` past the end yields what
        exists.

        Args:
            key (str): Object key.
            bucket (str | None): Source bucket.
            offset (int): First byte to read.
            length (int | None): How many bytes to read from ``offset``;
                ``None`` reads to the end.

        Returns:
            AsyncIterator[bytes]: Chunks of at most
            :data:`FAKE_STREAM_CHUNK_SIZE` bytes.

        Raises:
            ValueError: When ``offset`` is negative or ``length`` is not
                positive, as ``AsyncMinIOClient.stream_object`` refuses them.
            FileNotFoundError: When ``key`` is not stored.
            BaseException: Whatever :meth:`fail_next` queued.
        """
        self._record("stream_object")
        if offset < 0:
            raise ValueError("offset must be zero or positive")
        if length is not None and length < 1:
            raise ValueError("length must be at least 1; pass None to read to the end")
        payload = self._payload(key, bucket)
        end = len(payload) if length is None else offset + length
        return self._chunks(payload[offset:end])

    @staticmethod
    async def _chunks(payload: bytes) -> AsyncIterator[bytes]:
        """Yield ``payload`` in :data:`FAKE_STREAM_CHUNK_SIZE` pieces.

        Args:
            payload (bytes): The bytes to stream.

        Yields:
            bytes: The next chunk.
        """
        for start in range(0, len(payload), FAKE_STREAM_CHUNK_SIZE):
            yield payload[start : start + FAKE_STREAM_CHUNK_SIZE]

    async def list_objects(
        self,
        prefix: str = "",
        *,
        bucket: str | None = None,
        recursive: bool = True,
    ) -> list[str]:
        """List the keys under ``prefix``, sorted.

        Args:
            prefix (str): Prefix filter.
            bucket (str | None): Source bucket.
            recursive (bool): Accepted for signature parity; the fake
                always lists every key under the prefix.

        Returns:
            list[str]: The matching keys.

        Raises:
            BaseException: Whatever :meth:`fail_next` queued.
        """
        self._record("list_objects")
        return sorted(key for key in self._bucket(bucket) if key.startswith(prefix))

    async def remove_object(
        self,
        key: str,
        *,
        bucket: str | None = None,
        version_id: str | None = None,
    ) -> None:
        """Delete one object; a missing key is not an error, as on S3.

        Args:
            key (str): Object key.
            bucket (str | None): Target bucket.
            version_id (str | None): Accepted for signature parity.

        Raises:
            PermissionError: When :meth:`fail_delete` marked ``key``.
            BaseException: Whatever :meth:`fail_next` queued.
        """
        self._record("remove_object")
        if key in self._refused:
            raise PermissionError(f"AccessDenied: {key}")
        if self._bucket(bucket).pop(key, None) is not None:
            self._stored.pop((bucket or self.default_bucket, key), None)
            self.deleted.append(key)

    async def remove_objects(
        self,
        keys: Iterable[str],
        *,
        bucket: str | None = None,
    ) -> list[ObjectDeleteError]:
        """Delete many objects, reporting the refused ones.

        Args:
            keys (Iterable[str]): Object keys; duplicates collapse.
            bucket (str | None): Target bucket.

        Returns:
            list[ObjectDeleteError]: One ``AccessDenied`` entry per key
            :meth:`fail_delete` marked; those keys stay stored.

        Raises:
            BaseException: Whatever :meth:`fail_next` queued.
        """
        self._record("remove_objects")
        store = self._bucket(bucket)
        errors: list[ObjectDeleteError] = []
        for key in dict.fromkeys(keys):
            if key in self._refused:
                errors.append(
                    ObjectDeleteError(
                        key=key,
                        code="AccessDenied",
                        message="refused by FakeObjectStorageClient",
                    )
                )
            elif store.pop(key, None) is not None:
                self._stored.pop((bucket or self.default_bucket, key), None)
                self.deleted.append(key)
        return errors


__all__: list[str] = [
    "DEFAULT_FAKE_BUCKET",
    "FAKE_STREAM_CHUNK_SIZE",
    "FakeObjectStorageClient",
]
