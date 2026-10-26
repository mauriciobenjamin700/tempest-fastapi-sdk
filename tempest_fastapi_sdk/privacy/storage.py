"""Object storage partitioned by data subject.

Erasing a data subject has to reach their files too, and listing a bucket to
find "everything that belongs to user 42" only works when the key says so.
:class:`SubjectObjectStorage` puts every object of a subject under one
prefix, ``<prefix>/<subject_id>/<name>``, on top of
:class:`~tempest_fastapi_sdk.storage.AsyncMinIOClient`, so export lists one
prefix and erasure deletes it in S3 batch requests.

The prefix ends in ``/``: subject ``"1"`` never matches the objects of
subject ``"10"``.
"""

from __future__ import annotations

from collections.abc import AsyncIterator, Iterable
from datetime import timedelta
from typing import TYPE_CHECKING, BinaryIO, Protocol

if TYPE_CHECKING:
    from tempest_fastapi_sdk.storage.minio_client import ObjectDeleteError, ObjectStat
    from tempest_fastapi_sdk.storage.presign import PresignedPost


class SubjectStorageClient(Protocol):
    """The object-storage calls :class:`SubjectObjectStorage` makes.

    :class:`~tempest_fastapi_sdk.storage.AsyncMinIOClient` satisfies it, and so
    does :class:`~tempest_fastapi_sdk.testing.fakes.FakeObjectStorageClient`,
    which is what lets a test build the storage without MinIO. The
    signatures are ``AsyncMinIOClient``'s, minus the keyword arguments the
    storage never passes.
    """

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
        """Upload one object.

        Args:
            key (str): Object key.
            data (bytes | BinaryIO): The payload.
            bucket (str | None): Override target bucket.
            content_type (str): MIME type stored with the object.
            metadata (dict[str, str] | None): User metadata.
            length (int | None): Payload size, when ``data`` is a stream.

        Returns:
            str: The object's ETag.
        """
        ...

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
        """Generate a temporary download URL.

        Args:
            key (str): Object key.
            bucket (str | None): Override source bucket.
            expires (timedelta): URL lifetime.
            filename (str | None): Name presented to the client, signed in.
            media_type (str | None): ``Content-Type`` the link answers with,
                signed in.
            as_attachment (bool | None): Download or ``inline``, signed in.

        Returns:
            str: The presigned URL.
        """
        ...

    async def presigned_put_url(
        self,
        key: str,
        *,
        bucket: str | None = None,
        expires: timedelta = timedelta(minutes=15),
    ) -> str:
        """Generate a temporary upload URL.

        Args:
            key (str): Destination object key.
            bucket (str | None): Override target bucket.
            expires (timedelta): URL lifetime.

        Returns:
            str: The presigned URL accepting a ``PUT``.
        """
        ...

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
        """Generate a form upload capped at ``max_bytes``.

        Args:
            key (str): Destination object key.
            max_bytes (int): Largest accepted file.
            content_type (str | None): The only ``Content-Type`` accepted.
            min_bytes (int): Smallest accepted file.
            bucket (str | None): Override target bucket.
            expires (timedelta): Lifetime.

        Returns:
            PresignedPost: URL, form fields and cap.
        """
        ...

    async def stat_object(
        self,
        key: str,
        *,
        bucket: str | None = None,
    ) -> ObjectStat:
        """Fetch an object's metadata without downloading it.

        Args:
            key (str): Object key.
            bucket (str | None): Override source bucket.

        Returns:
            ObjectStat: Size, content type, ETag and user metadata.
        """
        ...

    async def stream_object(
        self,
        key: str,
        *,
        bucket: str | None = None,
        offset: int = 0,
        length: int | None = None,
    ) -> AsyncIterator[bytes]:
        """Open an object, or one byte range of it, as a chunk stream.

        Args:
            key (str): Object key.
            bucket (str | None): Override source bucket.
            offset (int): First byte to read.
            length (int | None): How many bytes to read from ``offset``;
                ``None`` reads to the end.

        Returns:
            AsyncIterator[bytes]: The chunks, in order.
        """
        ...

    async def list_objects(
        self,
        prefix: str = "",
        *,
        bucket: str | None = None,
        recursive: bool = True,
    ) -> list[str]:
        """List object keys under ``prefix``.

        Args:
            prefix (str): Prefix filter.
            bucket (str | None): Override source bucket.
            recursive (bool): Descend into "subdirectories".

        Returns:
            list[str]: The matching keys.
        """
        ...

    async def remove_object(
        self,
        key: str,
        *,
        bucket: str | None = None,
        version_id: str | None = None,
    ) -> None:
        """Delete one object.

        Args:
            key (str): Object key.
            bucket (str | None): Override target bucket.
            version_id (str | None): Version to delete, on a versioned bucket.
        """
        ...

    async def remove_objects(
        self,
        keys: Iterable[str],
        *,
        bucket: str | None = None,
    ) -> list[ObjectDeleteError]:
        """Delete many objects in batch.

        Args:
            keys (Iterable[str]): Object keys.
            bucket (str | None): Override target bucket.

        Returns:
            list[ObjectDeleteError]: The keys the store refused to delete.
        """
        ...


class SubjectErasureError(Exception):
    """Raised when the store refuses to delete some of a subject's objects.

    Attributes:
        subject_id (str): The subject whose prefix was being erased.
        errors (list[ObjectDeleteError]): One entry per key that was not
            deleted, with the store's error code and message.
    """

    def __init__(self, subject_id: str, errors: list[ObjectDeleteError]) -> None:
        """Build the error.

        Args:
            subject_id (str): The subject whose prefix was being erased.
            errors (list[ObjectDeleteError]): The keys the store refused.
        """
        self.subject_id: str = subject_id
        self.errors: list[ObjectDeleteError] = errors
        sample = ", ".join(f"{e.key} ({e.code})" for e in errors[:5])
        super().__init__(
            f"{len(errors)} object(s) of subject {subject_id!r} were not "
            f"deleted: {sample}"
        )


def _segment(value: str, what: str) -> str:
    """Validate one path segment of a subject key.

    Args:
        value (str): The candidate segment.
        what (str): What the segment is, for the error message.

    Returns:
        str: ``value`` unchanged.

    Raises:
        ValueError: When the segment is empty, ``.``/``..``, or holds a
            ``/``.
    """
    if not value or value in {".", ".."} or "/" in value:
        raise ValueError(
            f"{what} must be a non-empty path segment without '/', got {value!r}"
        )
    return value


class SubjectObjectStorage:
    """Store objects under a per-subject prefix and erase them in batch.

    Example:

        >>> storage = SubjectObjectStorage(client, prefix="users")
        >>> await storage.put(user_id, "avatar.png", data, content_type="image/png")
        'users/42/avatar.png'
        >>> await storage.presign(user_id, "avatar.png")
        'https://...'
        >>> await storage.presign_put(user_id, "upload.jpg")
        'https://...'
        >>> await storage.read(user_id, "upload.jpg", max_bytes=1_048_577)
        b'...'
        >>> await storage.delete_all(user_id)
        1

    Attributes:
        client (SubjectStorageClient): The underlying object-storage client
            — an :class:`~tempest_fastapi_sdk.storage.AsyncMinIOClient`, or
            :class:`~tempest_fastapi_sdk.testing.fakes.FakeObjectStorageClient`
            in a test.
        prefix (str): Root prefix every subject lives under, without
            surrounding slashes.
        bucket (str | None): Bucket override; ``None`` uses the client's
            ``default_bucket``.
    """

    def __init__(
        self,
        client: SubjectStorageClient,
        *,
        prefix: str = "subjects",
        bucket: str | None = None,
    ) -> None:
        """Wrap ``client``.

        Args:
            client (SubjectStorageClient): The object-storage client.
            prefix (str): Root prefix every subject lives under. Leading and
                trailing slashes are stripped; inner slashes are allowed
                (``"tenants/acme/users"``). Default ``"subjects"``.
            bucket (str | None): Bucket for every operation; ``None`` uses
                the client's ``default_bucket``.

        Raises:
            ValueError: When ``prefix`` is empty after stripping slashes or
                holds an empty, ``.`` or ``..`` segment.
        """
        cleaned = prefix.strip("/")
        for part in cleaned.split("/"):
            _segment(part, "prefix segment")
        self.client: SubjectStorageClient = client
        self.prefix: str = cleaned
        self.bucket: str | None = bucket

    def subject_prefix(self, subject_id: object) -> str:
        """Return the key prefix holding every object of a subject.

        Args:
            subject_id (object): The subject id; converted with ``str()``.

        Returns:
            str: ``"<prefix>/<subject_id>/"``, trailing slash included.

        Raises:
            ValueError: When ``str(subject_id)`` is empty, ``.``/``..``, or
                holds a ``/``.
        """
        return f"{self.prefix}/{_segment(str(subject_id), 'subject_id')}/"

    def key(self, subject_id: object, name: str) -> str:
        """Return the full object key of one subject's object.

        Args:
            subject_id (object): The subject id; converted with ``str()``.
            name (str): Object name relative to the subject's prefix. Inner
                slashes are allowed (``"exports/2026.zip"``).

        Returns:
            str: ``"<prefix>/<subject_id>/<name>"``.

        Raises:
            ValueError: When the subject id is not a valid segment, or
                ``name`` is empty, starts with ``/``, or holds an empty,
                ``.`` or ``..`` segment — anything that could step outside
                the subject's prefix.
        """
        for part in name.split("/"):
            _segment(part, "name segment")
        return f"{self.subject_prefix(subject_id)}{name}"

    async def put(
        self,
        subject_id: object,
        name: str,
        data: bytes | BinaryIO,
        *,
        content_type: str = "application/octet-stream",
        metadata: dict[str, str] | None = None,
        length: int | None = None,
    ) -> str:
        """Upload an object under the subject's prefix.

        Args:
            subject_id (object): The subject id.
            name (str): Object name relative to the subject's prefix.
            data (bytes | BinaryIO): Payload; file-like data needs
                ``length`` (``-1`` for unknown-length multipart).
            content_type (str): MIME type.
            metadata (dict[str, str] | None): User metadata.
            length (int | None): Payload size for file-like data.

        Returns:
            str: The full object key written.

        Raises:
            ValueError: When the subject id or name is invalid, or file-like
                data omits ``length``.
            S3Error: When the upload fails.
        """
        key = self.key(subject_id, name)
        await self.client.put_object(
            key,
            data,
            bucket=self.bucket,
            content_type=content_type,
            metadata=metadata,
            length=length,
        )
        return key

    async def presign(
        self,
        subject_id: object,
        name: str,
        *,
        expires: timedelta = timedelta(hours=1),
        filename: str | None = None,
        media_type: str | None = None,
        as_attachment: bool | None = None,
    ) -> str:
        """Generate a temporary download URL for one subject's object.

        Pass ``media_type`` with the type the server checked — from
        :meth:`stat` and the bytes, not the uploader's declaration — and the
        link answers with it: S3 signs it as a ``response-content-type``
        override, the local backend binds it into its signature.

        Args:
            subject_id (object): The subject id.
            name (str): Object name relative to the subject's prefix.
            expires (timedelta): URL lifetime; at most 7 days.
            filename (str | None): Name presented to the client.
            media_type (str | None): ``Content-Type`` the link answers
                with, a bare ``type/subtype``.
            as_attachment (bool | None): ``True`` forces a download;
                ``False`` asks for ``inline``, granted only to inline-safe
                types.

        Returns:
            str: Presigned GET URL.

        Raises:
            ValueError: When the subject id or name is invalid, or
                ``media_type`` is not a bare ``type/subtype``.
        """
        return await self.client.presigned_get_url(
            self.key(subject_id, name),
            bucket=self.bucket,
            expires=expires,
            filename=filename,
            media_type=media_type,
            as_attachment=as_attachment,
        )

    async def presign_put(
        self,
        subject_id: object,
        name: str,
        *,
        expires: timedelta = timedelta(minutes=15),
    ) -> str:
        """Generate a temporary upload URL for one subject's object.

        The client ``PUT``s the bytes straight to the store, so they never
        cross the API process. The URL only reaches
        ``key(subject_id, name)``: the subject prefix is fixed by the server
        when it signs, never by the uploader. Nothing about the payload is
        signed — size and content type are whatever the client sends — so
        verify the object with :meth:`stat` and :meth:`read` before trusting
        it, or issue :meth:`presign_post`, whose size and type the store
        enforces.

        Args:
            subject_id (object): The subject id.
            name (str): Object name relative to the subject's prefix.
            expires (timedelta): URL lifetime; at most 7 days. Default 15
                minutes, the same as
                :meth:`~tempest_fastapi_sdk.storage.AsyncMinIOClient.presigned_put_url`.

        Returns:
            str: Presigned PUT URL.

        Raises:
            ValueError: When the subject id or name is invalid.
            IntegrationDisabledException: When the client was built with
                ``enabled=False``.
        """
        return await self.client.presigned_put_url(
            self.key(subject_id, name),
            bucket=self.bucket,
            expires=expires,
        )

    async def presign_post(
        self,
        subject_id: object,
        name: str,
        *,
        max_bytes: int,
        content_type: str | None = None,
        min_bytes: int = 0,
        expires: timedelta = timedelta(minutes=15),
    ) -> PresignedPost:
        """Generate a form upload for one subject's object, capped in size.

        The store refuses a file outside ``[min_bytes, max_bytes]``, another
        key and, when ``content_type`` is given, another type — S3 through
        the ``POST`` policy conditions, the local backend through its
        route. The cap is enforced before the bytes are stored, which
        :meth:`presign_put` cannot do. Still read the object before
        trusting its content: the declared type is not the bytes.

        Args:
            subject_id (object): The subject id.
            name (str): Object name relative to the subject's prefix.
            max_bytes (int): Largest accepted file.
            content_type (str | None): The only ``Content-Type`` accepted.
                ``None`` leaves it free.
            min_bytes (int): Smallest accepted file. Default ``0``.
            expires (timedelta): Lifetime; at most 7 days.

        Returns:
            PresignedPost: The URL, the form fields to send before the file,
            and the cap.

        Raises:
            ValueError: When the subject id or name is invalid, or the
                limits or ``content_type`` are invalid.
            IntegrationDisabledException: When the client was built with
                ``enabled=False``.
        """
        return await self.client.presigned_post_policy(
            self.key(subject_id, name),
            max_bytes=max_bytes,
            content_type=content_type,
            min_bytes=min_bytes,
            bucket=self.bucket,
            expires=expires,
        )

    async def stat(self, subject_id: object, name: str) -> ObjectStat:
        """Fetch one subject's object metadata without downloading it.

        Args:
            subject_id (object): The subject id.
            name (str): Object name relative to the subject's prefix.

        Returns:
            ObjectStat: Size, content type, ETag and user metadata, as the
            store reports them.

        Raises:
            ValueError: When the subject id or name is invalid.
            S3Error: When the object is missing or the request fails;
                propagated unchanged from the client
                (``FakeObjectStorageClient`` raises
                :class:`FileNotFoundError` instead).
            IntegrationDisabledException: When the client was built with
                ``enabled=False``.
        """
        return await self.client.stat_object(
            self.key(subject_id, name), bucket=self.bucket
        )

    async def read(
        self,
        subject_id: object,
        name: str,
        *,
        max_bytes: int | None = None,
    ) -> bytes:
        """Read one subject's object into memory, optionally capped.

        With ``max_bytes``, only the first ``max_bytes`` bytes are requested
        from the store (a ``Range`` read through
        :meth:`~tempest_fastapi_sdk.storage.AsyncMinIOClient.stream_object`),
        and reading stops there even if the store answers with more. An
        object that was swapped for a larger one after a :meth:`stat` never
        enters memory whole.

        To detect "too large", pass ``limit + 1`` and compare the length:
        ``len(data) > limit`` means the object exceeds ``limit``, without
        reading the rest of it.

        Args:
            subject_id (object): The subject id.
            name (str): Object name relative to the subject's prefix.
            max_bytes (int | None): Read at most this many bytes. ``None``
                (default) reads the whole object.

        Returns:
            bytes: The object's bytes, or its first ``max_bytes`` bytes.

        Raises:
            ValueError: When the subject id or name is invalid, or
                ``max_bytes`` is not positive.
            S3Error: When the object is missing or the request fails;
                propagated unchanged from the client
                (``FakeObjectStorageClient`` raises
                :class:`FileNotFoundError` instead).
            IntegrationDisabledException: When the client was built with
                ``enabled=False``.
        """
        if max_bytes is not None and max_bytes < 1:
            raise ValueError(f"max_bytes must be at least 1, got {max_bytes}")
        stream = await self.client.stream_object(
            self.key(subject_id, name),
            bucket=self.bucket,
            length=max_bytes,
        )
        buffer = bytearray()
        async for chunk in stream:
            buffer.extend(chunk)
            if max_bytes is not None and len(buffer) >= max_bytes:
                break
        if max_bytes is not None:
            del buffer[max_bytes:]
        return bytes(buffer)

    async def names(self, subject_id: object) -> list[str]:
        """List every object name of a subject.

        Args:
            subject_id (object): The subject id.

        Returns:
            list[str]: Names relative to the subject's prefix, sorted. Empty
            list when the subject has no objects.

        Raises:
            ValueError: When the subject id is invalid.
        """
        prefix = self.subject_prefix(subject_id)
        keys = await self.client.list_objects(prefix, bucket=self.bucket)
        return sorted(key.removeprefix(prefix) for key in keys)

    async def delete(self, subject_id: object, name: str) -> None:
        """Delete one object of a subject.

        Args:
            subject_id (object): The subject id.
            name (str): Object name relative to the subject's prefix.

        Raises:
            ValueError: When the subject id or name is invalid.
        """
        await self.client.remove_object(self.key(subject_id, name), bucket=self.bucket)

    async def delete_all(self, subject_id: object) -> int:
        """Erase every object under the subject's prefix.

        Lists the prefix and removes the keys with
        :meth:`~tempest_fastapi_sdk.storage.AsyncMinIOClient.remove_objects`
        (one S3 ``DeleteObjects`` request per 1000 keys). Objects of other
        subjects are never listed, so never deleted. Calling it again is a
        no-op that returns ``0``.

        Args:
            subject_id (object): The subject id.

        Returns:
            int: Number of objects deleted.

        Raises:
            ValueError: When the subject id is invalid.
            SubjectErasureError: When the store refused to delete some keys;
                the others are deleted.
        """
        prefix = self.subject_prefix(subject_id)
        keys = await self.client.list_objects(prefix, bucket=self.bucket)
        if not keys:
            return 0
        errors = await self.client.remove_objects(keys, bucket=self.bucket)
        if errors:
            raise SubjectErasureError(str(subject_id), errors)
        return len(keys)


__all__: list[str] = [
    "SubjectErasureError",
    "SubjectObjectStorage",
    "SubjectStorageClient",
]
