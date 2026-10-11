"""An object-storage client that keeps every object in a dict.

Wire it where :class:`~tempest_fastapi_sdk.privacy.SubjectObjectStorage` takes
its client and export, retention and erasure run with no MinIO — including the
branch that is hard to reach against a real store: a key the store refuses to
delete, which is what makes ``delete_all`` raise ``SubjectErasureError``.
"""

from __future__ import annotations

import hashlib
from collections.abc import Iterable, Mapping
from datetime import timedelta
from types import MappingProxyType
from typing import BinaryIO

from tempest_fastapi_sdk.storage.minio_client import ObjectDeleteError
from tempest_fastapi_sdk.testing.fakes._control import _Steerable

DEFAULT_FAKE_BUCKET: str = "fake-bucket"
"""Bucket an operation lands in when it passes no ``bucket``."""


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
            content_type (str): Accepted for signature parity; not stored.
            metadata (dict[str, str] | None): Accepted for signature parity;
                not stored.
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
        return hashlib.md5(payload, usedforsecurity=False).hexdigest()

    async def presigned_get_url(
        self,
        key: str,
        *,
        bucket: str | None = None,
        expires: timedelta = timedelta(hours=1),
    ) -> str:
        """Build a fake download URL.

        Args:
            key (str): Object key.
            bucket (str | None): Source bucket.
            expires (timedelta): URL lifetime, echoed in the URL.

        Returns:
            str: ``https://fake-storage/<bucket>/<key>?expires=<seconds>``.

        Raises:
            BaseException: Whatever :meth:`fail_next` queued.
        """
        self._record("presigned_get_url")
        seconds = int(expires.total_seconds())
        return (
            f"https://fake-storage/{bucket or self.default_bucket}/{key}"
            f"?expires={seconds}"
        )

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
                self.deleted.append(key)
        return errors


__all__: list[str] = [
    "DEFAULT_FAKE_BUCKET",
    "FakeObjectStorageClient",
]
