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

from datetime import timedelta
from typing import TYPE_CHECKING, BinaryIO

if TYPE_CHECKING:
    from tempest_fastapi_sdk.storage.minio_client import (
        AsyncMinIOClient,
        ObjectDeleteError,
    )


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
        >>> await storage.delete_all(user_id)
        1

    Attributes:
        client (AsyncMinIOClient): The underlying object-storage client.
        prefix (str): Root prefix every subject lives under, without
            surrounding slashes.
        bucket (str | None): Bucket override; ``None`` uses the client's
            ``default_bucket``.
    """

    def __init__(
        self,
        client: AsyncMinIOClient,
        *,
        prefix: str = "subjects",
        bucket: str | None = None,
    ) -> None:
        """Wrap ``client``.

        Args:
            client (AsyncMinIOClient): The object-storage client.
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
        self.client: AsyncMinIOClient = client
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
    ) -> str:
        """Generate a temporary download URL for one subject's object.

        Args:
            subject_id (object): The subject id.
            name (str): Object name relative to the subject's prefix.
            expires (timedelta): URL lifetime; at most 7 days.

        Returns:
            str: Presigned GET URL.

        Raises:
            ValueError: When the subject id or name is invalid.
        """
        return await self.client.presigned_get_url(
            self.key(subject_id, name),
            bucket=self.bucket,
            expires=expires,
        )

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
]
