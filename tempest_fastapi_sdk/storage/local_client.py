"""Object storage on the local filesystem, with signed links to the app's routes.

:class:`LocalObjectStorageClient` speaks the same calls
:class:`~tempest_fastapi_sdk.privacy.SubjectObjectStorage` makes on
:class:`~tempest_fastapi_sdk.storage.AsyncMinIOClient` — put, stat, stream,
list, remove, presign — but keeps every object in a directory of the server.
A bucket is a subdirectory; a key is a path below it.

What an S3 store gives by itself has to be rebuilt on a disk:

* **Presigned URLs.** A disk has no endpoint the browser can reach, so a
  "presigned" URL points at the **app**: ``<public_url><route_prefix>/
  <bucket>/<key>?expires=...&signature=...``, signed with
  :func:`~tempest_fastapi_sdk.utils.sign_path`.
  :func:`~tempest_fastapi_sdk.storage.make_local_storage_router` serves those
  routes. Download and upload links are signed for different purposes, so a
  download link never accepts a ``PUT``.
* **Atomic writes.** Every write lands in a temporary file under the base
  directory and is moved into place with :func:`os.replace`, so a reader
  sees the old object or the new one, never half of one.
* **Object metadata.** Content type, user metadata and the ETag (MD5 of the
  bytes, what S3 answers for a single-part upload) live in a JSON file in a
  separate tree, so listing a bucket never returns them.

Layout under ``base_dir``::

    objects/<bucket>/<key>              the bytes
    meta/<bucket>/<key>.meta.json       content type, metadata, ETag
    tmp/                                in-flight writes

A missing object raises :class:`LocalObjectNotFoundError`, a
:class:`FileNotFoundError` carrying ``code = "NoSuchKey"`` — the code S3
answers — so code that branches on the S3 code keeps working.

Every blocking call runs in :func:`asyncio.to_thread`; no extra is needed.
The store is for **one host**: two app instances on different machines do
not see each other's files. Processes on the same host (an API and a worker
container) share it by mounting the same directory.
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import os
import tempfile
from collections.abc import AsyncIterator, Callable, Coroutine, Iterable
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import IO, TYPE_CHECKING, Any, BinaryIO
from urllib.parse import urlencode

from tempest_fastapi_sdk.exceptions.integration import IntegrationDisabledException
from tempest_fastapi_sdk.settings.mixins import (
    DEFAULT_LOCAL_MAX_UPLOAD_BYTES,
    DEFAULT_LOCAL_STORAGE_ROUTE_PREFIX,
)
from tempest_fastapi_sdk.storage.minio_client import ObjectDeleteError, ObjectStat
from tempest_fastapi_sdk.storage.presign import (
    MEDIA_TYPE_PATTERN,
    PresignedPost,
    check_post_limits,
)
from tempest_fastapi_sdk.utils.signed_url import sign_path

if TYPE_CHECKING:
    from starlette.requests import Request
    from starlette.responses import StreamingResponse

LOCAL_STORAGE_GET_PURPOSE: str = "tempest-local-storage-get"
"""Signing purpose of download links; a ``PUT`` link never verifies for it."""

LOCAL_STORAGE_PUT_PURPOSE: str = "tempest-local-storage-put"
"""Signing purpose of upload links; a ``GET`` link never verifies for it."""

LOCAL_STORAGE_POST_PURPOSE: str = "tempest-local-storage-post"
"""Signing purpose of size-capped form uploads (:meth:`presigned_post_policy`)."""

LOCAL_GET_LINK_PARAMS: tuple[str, ...] = ("filename", "media_type", "disposition")
"""Query parameters a download link may carry, bound into its signature."""

LOCAL_POST_LINK_PARAMS: tuple[str, ...] = ("max_bytes", "min_bytes", "content_type")
"""Query parameters a form-upload link carries, bound into its signature."""


def bound_purpose(purpose: str, params: dict[str, str]) -> str:
    """Bind a link's extra query parameters into its signing purpose.

    :func:`~tempest_fastapi_sdk.utils.signed_url.sign_path` covers the path
    and the expiry only. The download overrides and the upload limits ride
    in the query, so they are folded into the purpose instead: the route
    rebuilds the purpose from the parameters it received, and any change to
    them — a bigger ``max_bytes``, another ``media_type`` — verifies as a
    different purpose and fails. With no parameters the purpose is
    unchanged, so a plain link signed before these existed still verifies.

    Args:
        purpose (str): The base purpose.
        params (dict[str, str]): The parameters the link carries.

    Returns:
        str: ``purpose`` alone, or followed by NUL and the parameters,
        sorted and URL-encoded.
    """
    if not params:
        return purpose
    return f"{purpose}\x00{urlencode(sorted(params.items()))}"


LOCAL_STORAGE_CHUNK_SIZE: int = 64 * 1024
"""Chunk size of reads and stream copies, the ``AsyncMinIOClient`` default."""

LOCAL_PRESIGN_MAX_EXPIRES: timedelta = timedelta(days=7)
"""Longest link lifetime accepted, the S3 limit ``AsyncMinIOClient`` has."""

LOCAL_META_SUFFIX: str = ".meta.json"
"""Suffix of the metadata file beside each object in the ``meta/`` tree.

A key segment ending in it is refused, so the metadata file of one key can
never sit where another key needs a directory.
"""

_OBJECTS_DIR: str = "objects"
_META_DIR: str = "meta"
_TMP_DIR: str = "tmp"


class LocalObjectNotFoundError(FileNotFoundError):
    """Raised when a key is not stored in the bucket.

    A :class:`FileNotFoundError` — so ``except OSError`` catches it — with
    the S3 error code beside it, so code written against ``S3Error.code``
    can branch the same way.

    Attributes:
        code (str): Always ``"NoSuchKey"``.
        bucket (str): The bucket that was read.
        key (str): The key that is missing.
    """

    code: str = "NoSuchKey"

    def __init__(self, bucket: str, key: str) -> None:
        """Build the error.

        Args:
            bucket (str): The bucket that was read.
            key (str): The key that is missing.
        """
        self.bucket: str = bucket
        self.key: str = key
        super().__init__(f"NoSuchKey: {bucket}/{key}")


@dataclass(frozen=True, slots=True)
class _ObjectMeta:
    """What is stored beside an object's bytes.

    Attributes:
        content_type (str): MIME type given at write time.
        metadata (dict[str, str]): User metadata given at write time.
        etag (str): MD5 of the bytes, hex.
    """

    content_type: str
    metadata: dict[str, str]
    etag: str


def _segment_ok(segment: str) -> bool:
    """Tell whether one path segment of a bucket or key is acceptable.

    Args:
        segment (str): The candidate segment.

    Returns:
        bool: ``False`` for an empty, ``.``/``..``, hidden-control or
        metadata-suffixed segment, or one holding a backslash or NUL.
    """
    return (
        bool(segment)
        and segment not in {".", ".."}
        and "\\" not in segment
        and "\x00" not in segment
        and not segment.endswith(LOCAL_META_SUFFIX)
    )


class LocalObjectStorageClient:
    """Object storage in a local directory, with signed links to the app.

    Satisfies :class:`~tempest_fastapi_sdk.privacy.SubjectStorageClient`, so
    it drops in where :class:`~tempest_fastapi_sdk.storage.AsyncMinIOClient`
    goes under :class:`~tempest_fastapi_sdk.privacy.SubjectObjectStorage`.

    Example:

        >>> client = LocalObjectStorageClient(
        ...     "/var/lib/app/storage",
        ...     public_url="https://api.example.com",
        ...     signing_secret=settings.JWT_SECRET,
        ... )
        >>> await client.put_object("a.txt", b"hi", content_type="text/plain")
        '49f68a5c8493ec2c0bf489821c21fc3b'
        >>> await client.presigned_get_url("a.txt")
        'https://api.example.com/storage/uploads/a.txt?expires=...&signature=...'

    Attributes:
        base_dir (Path): Resolved root of the store.
        public_url (str): Scheme and host the links start with, no trailing
            slash.
        route_prefix (str): Path the signed routes live under.
        default_bucket (str): Bucket used when a call passes none.
        max_upload_bytes (int): Largest body the signed ``PUT`` accepts.
        enabled (bool): ``False`` makes every operation raise
            :class:`~tempest_fastapi_sdk.exceptions.IntegrationDisabledException`.
    """

    def __init__(
        self,
        base_dir: Path | str,
        *,
        public_url: str,
        signing_secret: str,
        default_bucket: str = "uploads",
        route_prefix: str = DEFAULT_LOCAL_STORAGE_ROUTE_PREFIX,
        max_upload_bytes: int = DEFAULT_LOCAL_MAX_UPLOAD_BYTES,
        enabled: bool = True,
    ) -> None:
        """Create the store's directories and keep the signing material.

        Args:
            base_dir (Path | str): Root directory of the store. Created with
                its parents when missing.
            public_url (str): Scheme and host the browser reaches the app
                at (``"https://api.example.com"``). A trailing slash is
                stripped.
            signing_secret (str): Secret the links are signed with. Domain
                separation makes reusing ``JWT_SECRET`` safe.
            default_bucket (str): Bucket used when a call passes none.
                Default ``"uploads"``.
            route_prefix (str): Path the signed routes are mounted at, as the
                app sees it in ``request.scope["path"]`` — include any parent
                prefix (``"/api/storage"``). Must start with ``/``.
            max_upload_bytes (int): Largest body the signed ``PUT`` accepts.
                Default 25 MiB.
            enabled (bool): ``False`` builds a client for an integration
                switched off by ``STORAGE_ENABLED=false``: no directory is
                created and every operation raises
                :class:`~tempest_fastapi_sdk.exceptions.IntegrationDisabledException`
                (``503``). Default ``True``.

        Raises:
            ValueError: When ``public_url`` has no ``http(s)://`` scheme,
                ``route_prefix`` does not start with ``/``, the default
                bucket is not a single valid segment, ``max_upload_bytes``
                is not positive, or ``signing_secret`` is empty (only
                checked when ``enabled``).
        """
        cleaned_url = public_url.rstrip("/")
        if not cleaned_url.startswith(("http://", "https://")):
            raise ValueError(
                f"public_url must start with http:// or https://, got {public_url!r}"
            )
        cleaned_prefix = route_prefix.rstrip("/")
        if not cleaned_prefix.startswith("/"):
            raise ValueError(f"route_prefix must start with '/', got {route_prefix!r}")
        if max_upload_bytes < 1:
            raise ValueError("max_upload_bytes must be at least 1")
        self._check_bucket(default_bucket)
        self.base_dir: Path = Path(base_dir).resolve()
        self.public_url: str = cleaned_url
        self.route_prefix: str = cleaned_prefix
        self.default_bucket: str = default_bucket
        self.max_upload_bytes: int = max_upload_bytes
        self.enabled: bool = enabled
        self._secret: str = signing_secret
        if not enabled:
            return
        if not signing_secret:
            raise ValueError("signing_secret must be a non-empty string")
        for name in (_OBJECTS_DIR, _META_DIR, _TMP_DIR):
            (self.base_dir / name).mkdir(parents=True, exist_ok=True)

    def _check_enabled(self) -> None:
        """Refuse the call when the integration is switched off.

        Raises:
            IntegrationDisabledException: When built with ``enabled=False``.
        """
        if not self.enabled:
            raise IntegrationDisabledException("storage")

    @staticmethod
    def _check_bucket(bucket: str) -> str:
        """Validate a bucket name.

        Args:
            bucket (str): The candidate bucket.

        Returns:
            str: ``bucket`` unchanged.

        Raises:
            ValueError: When it is not a single valid path segment, or starts
                with ``.``.
        """
        if "/" in bucket or bucket.startswith(".") or not _segment_ok(bucket):
            raise ValueError(f"invalid bucket name {bucket!r}")
        return bucket

    @staticmethod
    def check_key(key: str) -> str:
        """Validate an object key.

        Args:
            key (str): The candidate key, ``/``-separated.

        Returns:
            str: ``key`` unchanged.

        Raises:
            ValueError: When the key is empty, starts with ``/``, or holds an
                empty, ``.``, ``..`` or metadata-suffixed segment, a
                backslash or a NUL — anything that could leave the bucket.
        """
        if (
            not key
            or key.startswith("/")
            or not all(_segment_ok(part) for part in key.split("/"))
        ):
            raise ValueError(f"invalid object key {key!r}")
        return key

    def _bucket(self, bucket: str | None) -> str:
        """Resolve and validate the bucket of a call.

        Args:
            bucket (str | None): The bucket, ``None`` for the default.

        Returns:
            str: The bucket name.
        """
        return self._check_bucket(bucket or self.default_bucket)

    def _inside(self, root: Path, relative: str) -> Path:
        """Join ``relative`` under ``root`` and refuse anything that escapes.

        The candidate is resolved, which follows symlinks, so a symlink
        pointing out of the store is refused too.

        Args:
            root (Path): The tree (``objects/`` or ``meta/``).
            relative (str): Already-validated ``<bucket>/<key>`` path.

        Returns:
            Path: The resolved path.

        Raises:
            ValueError: When the resolved path is not under ``root``.
        """
        resolved_root = root.resolve()
        candidate = (resolved_root / relative).resolve()
        if resolved_root not in candidate.parents:
            raise ValueError(f"object path escapes the store: {relative!r}")
        return candidate

    def object_path(self, key: str, *, bucket: str | None = None) -> Path:
        """Return where the bytes of ``key`` live on disk.

        Args:
            key (str): Object key.
            bucket (str | None): Bucket; ``None`` for the default.

        Returns:
            Path: The file path (it may not exist).

        Raises:
            ValueError: When the bucket or key is invalid, or resolves out of
                the store.
        """
        target = self._bucket(bucket)
        return self._inside(
            self.base_dir / _OBJECTS_DIR, f"{target}/{self.check_key(key)}"
        )

    def _meta_path(self, key: str, bucket: str | None) -> Path:
        """Return where the metadata of ``key`` lives on disk.

        Args:
            key (str): Object key.
            bucket (str | None): Bucket; ``None`` for the default.

        Returns:
            Path: The metadata file path (it may not exist).
        """
        target = self._bucket(bucket)
        return self._inside(
            self.base_dir / _META_DIR,
            f"{target}/{self.check_key(key)}{LOCAL_META_SUFFIX}",
        )

    def _temp_file(self) -> tuple[IO[bytes], Path]:
        """Open a fresh temporary file on the store's filesystem.

        Returns:
            tuple[IO[bytes], Path]: The open binary handle and its path.
        """
        handle, name = tempfile.mkstemp(dir=self.base_dir / _TMP_DIR)
        return os.fdopen(handle, "wb"), Path(name)

    def _commit(
        self,
        temp: Path,
        *,
        key: str,
        bucket: str | None,
        meta: _ObjectMeta,
    ) -> None:
        """Move a finished temporary file into place and write its metadata.

        Both files are replaced atomically: a reader sees the old object or
        the new one, never a partial file.

        Args:
            temp (Path): The temporary file holding the whole payload.
            key (str): Object key.
            bucket (str | None): Bucket; ``None`` for the default.
            meta (_ObjectMeta): What to store beside the bytes.
        """
        target = self.object_path(key, bucket=bucket)
        meta_target = self._meta_path(key, bucket)
        target.parent.mkdir(parents=True, exist_ok=True)
        meta_target.parent.mkdir(parents=True, exist_ok=True)
        handle, meta_temp_name = tempfile.mkstemp(dir=self.base_dir / _TMP_DIR)
        meta_temp = Path(meta_temp_name)
        try:
            with os.fdopen(handle, "w", encoding="utf-8") as out:
                json.dump(
                    {
                        "content_type": meta.content_type,
                        "metadata": meta.metadata,
                        "etag": meta.etag,
                    },
                    out,
                )
            os.replace(temp, target)
            os.replace(meta_temp, meta_target)
        finally:
            temp.unlink(missing_ok=True)
            meta_temp.unlink(missing_ok=True)

    def _put_sync(
        self,
        key: str,
        data: bytes | BinaryIO,
        *,
        bucket: str | None,
        content_type: str,
        metadata: dict[str, str] | None,
        length: int | None,
    ) -> str:
        """Write a payload atomically; the blocking half of :meth:`put_object`.

        Args:
            key (str): Object key.
            data (bytes | BinaryIO): The payload.
            bucket (str | None): Bucket; ``None`` for the default.
            content_type (str): MIME type.
            metadata (dict[str, str] | None): User metadata.
            length (int | None): Bytes to read from a stream; ``None`` or
                ``-1`` reads to the end.

        Returns:
            str: The ETag.
        """
        self.object_path(key, bucket=bucket)
        digest = hashlib.md5(usedforsecurity=False)
        out, temp = self._temp_file()
        try:
            with out:
                if isinstance(data, bytes):
                    digest.update(data)
                    out.write(data)
                else:
                    remaining = None if length is None or length < 0 else length
                    while remaining is None or remaining > 0:
                        size = LOCAL_STORAGE_CHUNK_SIZE
                        if remaining is not None:
                            size = min(size, remaining)
                        chunk = data.read(size)
                        if not chunk:
                            break
                        digest.update(chunk)
                        out.write(chunk)
                        if remaining is not None:
                            remaining -= len(chunk)
            etag = digest.hexdigest()
            self._commit(
                temp,
                key=key,
                bucket=bucket,
                meta=_ObjectMeta(content_type, dict(metadata or {}), etag),
            )
        finally:
            temp.unlink(missing_ok=True)
        return etag

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
        """Store one object, replacing any previous one atomically.

        Args:
            key (str): Object key.
            data (bytes | BinaryIO): The payload; a stream is read up to
                ``length`` bytes, or to its end.
            bucket (str | None): Target bucket.
            content_type (str): MIME type, reported back by
                :meth:`stat_object` and served by the download route.
            metadata (dict[str, str] | None): User metadata, reported back by
                :meth:`stat_object`.
            length (int | None): Bytes to read from a stream; ``None`` or
                ``-1`` reads to the end.

        Returns:
            str: The ETag — the MD5 of the payload, hex.

        Raises:
            ValueError: When the bucket or key is invalid.
            OSError: When the write fails (disk full, permissions, a key
                that needs a directory where a file is).
            IntegrationDisabledException: When built with ``enabled=False``.
        """
        self._check_enabled()
        return await asyncio.to_thread(
            self._put_sync,
            key,
            data,
            bucket=bucket,
            content_type=content_type,
            metadata=metadata,
            length=length,
        )

    async def write_stream(
        self,
        key: str,
        chunks: AsyncIterator[bytes],
        *,
        bucket: str | None = None,
        content_type: str = "application/octet-stream",
        metadata: dict[str, str] | None = None,
        max_bytes: int | None = None,
    ) -> str:
        """Store an object from an async chunk stream, capped at ``max_bytes``.

        The stream is written to a temporary file as it arrives and moved
        into place only when it ended within the cap; going past the cap
        stops reading and leaves nothing behind.

        Args:
            key (str): Object key.
            chunks (AsyncIterator[bytes]): The payload, in order.
            bucket (str | None): Target bucket.
            content_type (str): MIME type.
            metadata (dict[str, str] | None): User metadata.
            max_bytes (int | None): Largest payload accepted; ``None`` for no
                cap.

        Returns:
            str: The ETag — the MD5 of the payload, hex.

        Raises:
            FileTooLargeException: When the stream passes ``max_bytes``
                (``413``).
            ValueError: When the bucket or key is invalid.
            OSError: When the write fails.
            IntegrationDisabledException: When built with ``enabled=False``.
        """
        from tempest_fastapi_sdk.exceptions.upload import FileTooLargeException

        self._check_enabled()
        self.object_path(key, bucket=bucket)
        digest = hashlib.md5(usedforsecurity=False)
        out, temp = await asyncio.to_thread(self._temp_file)
        total = 0
        try:
            try:
                async for chunk in chunks:
                    total += len(chunk)
                    if max_bytes is not None and total > max_bytes:
                        raise FileTooLargeException(
                            details={"max_size_bytes": max_bytes},
                        )
                    digest.update(chunk)
                    await asyncio.to_thread(out.write, chunk)
            finally:
                await asyncio.to_thread(out.close)
            etag = digest.hexdigest()
            await asyncio.to_thread(
                self._commit,
                temp,
                key=key,
                bucket=bucket,
                meta=_ObjectMeta(content_type, dict(metadata or {}), etag),
            )
        finally:
            await asyncio.to_thread(temp.unlink, missing_ok=True)
        return etag

    def _signed_url(
        self,
        key: str,
        *,
        bucket: str | None,
        expires: timedelta,
        purpose: str,
        params: dict[str, str] | None = None,
    ) -> str:
        """Sign the route path of one object for ``purpose``.

        Args:
            key (str): Object key.
            bucket (str | None): Bucket; ``None`` for the default.
            expires (timedelta): Link lifetime.
            purpose (str): :data:`LOCAL_STORAGE_GET_PURPOSE`,
                :data:`LOCAL_STORAGE_PUT_PURPOSE` or
                :data:`LOCAL_STORAGE_POST_PURPOSE`.
            params (dict[str, str] | None): Extra query parameters, bound
                into the signature through :func:`bound_purpose`.

        Returns:
            str: The absolute signed URL.

        Raises:
            ValueError: When the key is invalid, or ``expires`` is not
                positive or longer than :data:`LOCAL_PRESIGN_MAX_EXPIRES`.
        """
        if expires > LOCAL_PRESIGN_MAX_EXPIRES:
            raise ValueError("expires must be at most 7 days")
        self.object_path(key, bucket=bucket)
        path = f"{self.route_prefix}/{self._bucket(bucket)}/{key}"
        extra = params or {}
        signed = sign_path(
            path,
            secret=self._secret,
            expires_in=expires,
            purpose=bound_purpose(purpose, extra),
        )
        if extra:
            signed = f"{signed}&{urlencode(extra)}"
        return f"{self.public_url}{signed}"

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
        """Build a temporary download link served by the app.

        The object does not need to exist yet; the route answers ``404``
        if it still does not when the link is opened.

        ``filename``, ``media_type`` and ``as_attachment`` travel in the
        query and are bound into the signature (:func:`bound_purpose`), the
        way S3 signs its ``response-*`` overrides: the route answers with
        them, and editing one breaks the link. Unset, the route serves the
        stored type with its own ``as_attachment`` default.

        Args:
            key (str): Object key.
            bucket (str | None): Source bucket.
            expires (timedelta): Link lifetime; at most 7 days.
            filename (str | None): Name presented to the client.
            media_type (str | None): ``Content-Type`` the route answers
                with, a bare ``type/subtype``.
            as_attachment (bool | None): ``True`` forces a download;
                ``False`` asks for ``inline``, granted only to inline-safe
                types. ``None`` keeps the router's default.

        Returns:
            str: ``<public_url><route_prefix>/<bucket>/<key>``, with the
            ``expires`` and ``signature`` query parameters.

        Raises:
            ValueError: When the key is invalid or ``expires`` is out of
                range.
            IntegrationDisabledException: When built with ``enabled=False``.
        """
        self._check_enabled()
        params: dict[str, str] = {}
        if filename is not None:
            params["filename"] = filename
        if media_type is not None:
            if not MEDIA_TYPE_PATTERN.fullmatch(media_type):
                raise ValueError(f"media_type must be type/subtype, got {media_type!r}")
            params["media_type"] = media_type
        if as_attachment is not None:
            params["disposition"] = "attachment" if as_attachment else "inline"
        return self._signed_url(
            key,
            bucket=bucket,
            expires=expires,
            purpose=LOCAL_STORAGE_GET_PURPOSE,
            params=params,
        )

    async def presigned_put_url(
        self,
        key: str,
        *,
        bucket: str | None = None,
        expires: timedelta = timedelta(minutes=15),
    ) -> str:
        """Build a temporary upload link served by the app.

        The client ``PUT``s the raw bytes to it with a ``Content-Type``
        header; the route caps the body at :attr:`max_upload_bytes`. As with
        S3, nothing about the payload is signed — verify it with
        :meth:`stat_object` before trusting it.

        Args:
            key (str): Destination object key.
            bucket (str | None): Target bucket.
            expires (timedelta): Link lifetime; at most 7 days.

        Returns:
            str: The signed URL accepting a ``PUT``.

        Raises:
            ValueError: When the key is invalid or ``expires`` is out of
                range.
            IntegrationDisabledException: When built with ``enabled=False``.
        """
        self._check_enabled()
        return self._signed_url(
            key, bucket=bucket, expires=expires, purpose=LOCAL_STORAGE_PUT_PURPOSE
        )

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
        """Build a form upload the route caps at ``max_bytes``.

        The same call, and the same :class:`PresignedPost`, as
        :meth:`AsyncMinIOClient.presigned_post_policy
        <tempest_fastapi_sdk.storage.AsyncMinIOClient.presigned_post_policy>`:
        the browser posts ``multipart/form-data`` with the fields and then
        the file, and the code that sends it does not know which backend
        signed. The limits ride in the URL, bound into the signature; the
        route refuses a file outside them (``413 FILE_TOO_LARGE`` /
        ``400 FILE_TOO_SMALL``), a different ``Content-Type`` field or a
        different ``key`` field (``403``), and stores nothing.

        Reading the form needs ``python-multipart`` (the ``[upload]``
        extra); it is checked here, so a missing extra fails where the link
        is issued instead of on the uploader's request.

        Args:
            key (str): Destination object key.
            max_bytes (int): Largest accepted file; at most
                :attr:`max_upload_bytes`.
            content_type (str | None): The only ``Content-Type`` accepted.
                ``None`` leaves it free.
            min_bytes (int): Smallest accepted file. Default ``0``.
            bucket (str | None): Target bucket.
            expires (timedelta): Link lifetime; at most 7 days.

        Returns:
            PresignedPost: The URL, the form fields and the cap.

        Raises:
            ValueError: When the size range is empty, inverted or above
                :attr:`max_upload_bytes`, the key is invalid, or
                ``content_type`` is not a bare ``type/subtype``.
            ImportError: When ``python-multipart`` is not installed.
            IntegrationDisabledException: When built with ``enabled=False``.
        """
        self._check_enabled()
        check_post_limits(min_bytes=min_bytes, max_bytes=max_bytes)
        if max_bytes > self.max_upload_bytes:
            raise ValueError(
                f"max_bytes must be at most max_upload_bytes ({self.max_upload_bytes})"
            )
        if content_type is not None and not MEDIA_TYPE_PATTERN.fullmatch(content_type):
            raise ValueError(f"content_type must be type/subtype, got {content_type!r}")
        try:
            import python_multipart  # noqa: F401
        except ImportError as exc:
            raise ImportError(
                "Form uploads to the local store need python-multipart. "
                "Install with: pip install tempest-fastapi-sdk[upload]"
            ) from exc
        params: dict[str, str] = {
            "max_bytes": str(max_bytes),
            "min_bytes": str(min_bytes),
        }
        fields: dict[str, str] = {"key": key}
        if content_type is not None:
            params["content_type"] = content_type
            fields["Content-Type"] = content_type
        url = self._signed_url(
            key,
            bucket=bucket,
            expires=expires,
            purpose=LOCAL_STORAGE_POST_PURPOSE,
            params=params,
        )
        return PresignedPost(url=url, max_bytes=max_bytes, fields=fields)

    def verify_link(
        self,
        request: Request,
        *,
        purpose: str,
        names: tuple[str, ...] = (),
    ) -> dict[str, str]:
        """Verify a link whose extra query parameters are bound to it.

        The check behind the routes of
        :func:`~tempest_fastapi_sdk.storage.make_local_storage_router`: the
        purpose is rebuilt with :func:`bound_purpose` from the parameters in
        ``names`` that the request carries, so a link whose
        limits or overrides were edited verifies as another purpose and
        fails.

        Args:
            request (Request): The incoming request.
            purpose (str): The route's base purpose.
            names (tuple[str, ...]): The parameters this route binds —
                :data:`LOCAL_GET_LINK_PARAMS` or :data:`LOCAL_POST_LINK_PARAMS`.

        Returns:
            dict[str, str]: The bound parameters the link carried.

        Raises:
            InvalidSignedURLException: ``403`` when the signature is
                missing, malformed or does not match.
            ExpiredSignedURLException: ``403`` when the link expired.
        """
        from tempest_fastapi_sdk.exceptions.signed_url import (
            InvalidSignedURLException,
        )
        from tempest_fastapi_sdk.utils.signed_url import (
            SIGNED_URL_EXPIRES_PARAM,
            SIGNED_URL_SIGNATURE_PARAM,
            verify_path,
        )

        query = request.query_params
        expires = query.get(SIGNED_URL_EXPIRES_PARAM, "")
        signature = query.get(SIGNED_URL_SIGNATURE_PARAM, "")
        if not (expires.isascii() and expires.isdigit()) or not signature:
            raise InvalidSignedURLException()
        params = {name: query[name] for name in names if name in query}
        verify_path(
            request.scope["path"],
            expires=int(expires),
            signature=signature,
            secret=self._secret,
            purpose=bound_purpose(purpose, params),
        )
        return params

    def signature_dependency(
        self,
        purpose: str,
    ) -> Callable[..., Coroutine[Any, Any, None]]:
        """Build the FastAPI dependency that verifies this store's links.

        :func:`~tempest_fastapi_sdk.storage.make_local_storage_router` uses
        it; call it yourself only to serve the links from a route of your
        own.

        Args:
            purpose (str): :data:`LOCAL_STORAGE_GET_PURPOSE` or
                :data:`LOCAL_STORAGE_PUT_PURPOSE`.

        Returns:
            Callable[..., Coroutine[Any, Any, None]]: The dependency from
            :func:`~tempest_fastapi_sdk.api.make_signed_path_dependency`,
            bound to this store's secret.

        Raises:
            ValueError: When the store was built with an empty secret.
        """
        from tempest_fastapi_sdk.api.dependencies.signed_url import (
            make_signed_path_dependency,
        )

        return make_signed_path_dependency(secret=self._secret, purpose=purpose)

    def _read_meta(self, key: str, bucket: str | None) -> _ObjectMeta | None:
        """Load the metadata stored beside an object.

        Args:
            key (str): Object key.
            bucket (str | None): Bucket; ``None`` for the default.

        Returns:
            _ObjectMeta | None: The metadata, or ``None`` when the file is
            missing or unreadable (an object copied into the tree by hand).
        """
        try:
            raw: Any = json.loads(self._meta_path(key, bucket).read_text("utf-8"))
        except (OSError, ValueError):
            return None
        if not isinstance(raw, dict):
            return None
        metadata = raw.get("metadata")
        return _ObjectMeta(
            content_type=str(raw.get("content_type") or "application/octet-stream"),
            metadata=(
                {str(k): str(v) for k, v in metadata.items()}
                if isinstance(metadata, dict)
                else {}
            ),
            etag=str(raw.get("etag") or ""),
        )

    def _stat_sync(self, key: str, bucket: str | None) -> ObjectStat:
        """Describe one object; the blocking half of :meth:`stat_object`.

        Args:
            key (str): Object key.
            bucket (str | None): Bucket; ``None`` for the default.

        Returns:
            ObjectStat: The description.

        Raises:
            LocalObjectNotFoundError: When the object is not stored.
        """
        target = self._bucket(bucket)
        path = self.object_path(key, bucket=bucket)
        if not path.is_file():
            raise LocalObjectNotFoundError(target, key)
        info = path.stat()
        meta = self._read_meta(key, bucket)
        return ObjectStat(
            bucket=target,
            key=key,
            size=info.st_size,
            etag=(meta.etag or None) if meta is not None else None,
            content_type=meta.content_type if meta is not None else None,
            last_modified=datetime.fromtimestamp(info.st_mtime, tz=UTC),
            metadata=dict(meta.metadata) if meta is not None else {},
            raw=None,  # type: ignore[arg-type]
        )

    async def stat_object(
        self,
        key: str,
        *,
        bucket: str | None = None,
    ) -> ObjectStat:
        """Describe a stored object without reading it.

        Args:
            key (str): Object key.
            bucket (str | None): Source bucket.

        Returns:
            ObjectStat: Size and modification time from the file; content
            type, metadata and ETag from the metadata file. ``raw`` is
            ``None``: there is no ``minio`` object behind a disk.

        Raises:
            LocalObjectNotFoundError: When the object is not stored.
            ValueError: When the bucket or key is invalid.
            IntegrationDisabledException: When built with ``enabled=False``.
        """
        self._check_enabled()
        return await asyncio.to_thread(self._stat_sync, key, bucket)

    def _open_sync(self, key: str, bucket: str | None, offset: int) -> BinaryIO:
        """Open an object for reading at ``offset``.

        Args:
            key (str): Object key.
            bucket (str | None): Bucket; ``None`` for the default.
            offset (int): First byte to read.

        Returns:
            BinaryIO: The open handle, positioned at ``offset``.

        Raises:
            LocalObjectNotFoundError: When the object is not stored.
        """
        path = self.object_path(key, bucket=bucket)
        try:
            handle = path.open("rb")
        except (FileNotFoundError, IsADirectoryError, NotADirectoryError):
            raise LocalObjectNotFoundError(self._bucket(bucket), key) from None
        handle.seek(offset)
        return handle

    @staticmethod
    async def _chunks(
        handle: BinaryIO,
        length: int | None,
        chunk_size: int,
    ) -> AsyncIterator[bytes]:
        """Read ``handle`` in chunks, up to ``length`` bytes, then close it.

        Args:
            handle (BinaryIO): The open file.
            length (int | None): Bytes to read; ``None`` reads to the end.
            chunk_size (int): Bytes per chunk.

        Yields:
            bytes: The next chunk.
        """
        remaining = length
        try:
            while remaining is None or remaining > 0:
                size = chunk_size if remaining is None else min(chunk_size, remaining)
                chunk = await asyncio.to_thread(handle.read, size)
                if not chunk:
                    return
                if remaining is not None:
                    remaining -= len(chunk)
                yield chunk
        finally:
            await asyncio.to_thread(handle.close)

    async def stream_object(
        self,
        key: str,
        *,
        bucket: str | None = None,
        chunk_size: int = LOCAL_STORAGE_CHUNK_SIZE,
        offset: int = 0,
        length: int | None = None,
    ) -> AsyncIterator[bytes]:
        """Open a stored object, or one byte range of it, as a chunk stream.

        The file is opened before this returns, so a missing object fails
        here and not at the first chunk. A ``length`` past the end yields
        what exists.

        Args:
            key (str): Object key.
            bucket (str | None): Source bucket.
            chunk_size (int): Bytes per chunk. Default 64 KiB.
            offset (int): First byte to read.
            length (int | None): Bytes to read from ``offset``; ``None``
                reads to the end.

        Returns:
            AsyncIterator[bytes]: The chunks, in order; the file closes when
            the stream ends or is closed.

        Raises:
            ValueError: When ``offset`` is negative, ``length`` or
                ``chunk_size`` is not positive, or the bucket or key is
                invalid.
            LocalObjectNotFoundError: When the object is not stored.
            IntegrationDisabledException: When built with ``enabled=False``.
        """
        self._check_enabled()
        if offset < 0:
            raise ValueError("offset must be zero or positive")
        if length is not None and length < 1:
            raise ValueError("length must be at least 1; pass None to read to the end")
        if chunk_size < 1:
            raise ValueError("chunk_size must be at least 1")
        handle = await asyncio.to_thread(self._open_sync, key, bucket, offset)
        return self._chunks(handle, length, chunk_size)

    def _list_sync(self, prefix: str, bucket: str | None, recursive: bool) -> list[str]:
        """List keys under ``prefix``; the blocking half of :meth:`list_objects`.

        Args:
            prefix (str): Key prefix.
            bucket (str | None): Bucket; ``None`` for the default.
            recursive (bool): Descend into "subdirectories".

        Returns:
            list[str]: The matching keys, sorted. A non-recursive prefix
            whose directory part holds an empty, ``.``, ``..``, backslash or
            NUL segment, or that resolves outside the bucket (a symlink
            included), matches nothing — like a string prefix on S3, where
            ``../`` is just characters no key starts with.
        """
        root = self.base_dir / _OBJECTS_DIR / self._bucket(bucket)
        if not root.is_dir():
            return []
        found: list[str] = []
        if recursive:
            for path in root.rglob("*"):
                if path.is_file():
                    key = path.relative_to(root).as_posix()
                    if key.startswith(prefix):
                        found.append(key)
            return sorted(found)
        directory, _, _ = prefix.rpartition("/")
        if directory and (
            directory.startswith("/")
            or not all(_segment_ok(part) for part in directory.split("/"))
        ):
            return []
        resolved_root = root.resolve()
        level = (resolved_root / directory).resolve() if directory else resolved_root
        if level != resolved_root and resolved_root not in level.parents:
            return []
        if not level.is_dir():
            return []
        for path in level.iterdir():
            key = path.relative_to(resolved_root).as_posix()
            if path.is_dir():
                key += "/"
            if key.startswith(prefix):
                found.append(key)
        return sorted(found)

    async def list_objects(
        self,
        prefix: str = "",
        *,
        bucket: str | None = None,
        recursive: bool = True,
    ) -> list[str]:
        """List object keys under ``prefix``, sorted.

        Args:
            prefix (str): Prefix filter.
            bucket (str | None): Source bucket.
            recursive (bool): ``True`` (default) lists every key under the
                prefix; ``False`` lists one level, with each "subdirectory"
                as a key ending in ``/`` — as S3 answers.

        Returns:
            list[str]: The matching keys; ``[]`` for an empty or unknown
            bucket.

        Raises:
            ValueError: When the bucket is invalid.
            IntegrationDisabledException: When built with ``enabled=False``.
        """
        self._check_enabled()
        return await asyncio.to_thread(self._list_sync, prefix, bucket, recursive)

    def _prune(self, path: Path, stop: Path) -> None:
        """Remove empty parent directories of ``path`` up to ``stop``.

        Args:
            path (Path): A file that was just removed.
            stop (Path): Directory never removed (the bucket root).
        """
        parent = path.parent
        while parent != stop and stop in parent.parents:
            try:
                parent.rmdir()
            except OSError:
                return
            parent = parent.parent

    def _remove_sync(self, key: str, bucket: str | None) -> None:
        """Delete one object and its metadata; missing is not an error.

        Args:
            key (str): Object key.
            bucket (str | None): Bucket; ``None`` for the default.
        """
        target = self._bucket(bucket)
        path = self.object_path(key, bucket=bucket)
        meta = self._meta_path(key, bucket)
        path.unlink(missing_ok=True)
        meta.unlink(missing_ok=True)
        self._prune(path, (self.base_dir / _OBJECTS_DIR / target).resolve())
        self._prune(meta, (self.base_dir / _META_DIR / target).resolve())

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
            version_id (str | None): Accepted for signature parity; a disk
                keeps no versions.

        Raises:
            ValueError: When the bucket or key is invalid.
            OSError: When the file cannot be removed (permissions).
            IntegrationDisabledException: When built with ``enabled=False``.
        """
        del version_id
        self._check_enabled()
        await asyncio.to_thread(self._remove_sync, key, bucket)

    def _remove_many_sync(
        self,
        keys: Iterable[str],
        bucket: str | None,
    ) -> list[ObjectDeleteError]:
        """Delete many objects; the blocking half of :meth:`remove_objects`.

        Args:
            keys (Iterable[str]): Object keys; duplicates collapse.
            bucket (str | None): Bucket; ``None`` for the default.

        Returns:
            list[ObjectDeleteError]: One entry per key that was not deleted.
        """
        errors: list[ObjectDeleteError] = []
        for key in dict.fromkeys(keys):
            try:
                self._remove_sync(key, bucket)
            except PermissionError as exc:
                errors.append(ObjectDeleteError(key, "AccessDenied", str(exc)))
            except (OSError, ValueError) as exc:
                errors.append(ObjectDeleteError(key, "InternalError", str(exc)))
        return errors

    async def remove_objects(
        self,
        keys: Iterable[str],
        *,
        bucket: str | None = None,
    ) -> list[ObjectDeleteError]:
        """Delete many objects, reporting the ones that could not go.

        Args:
            keys (Iterable[str]): Object keys; duplicates collapse.
            bucket (str | None): Target bucket.

        Returns:
            list[ObjectDeleteError]: ``AccessDenied`` for a permission error,
            ``InternalError`` for any other failure (an invalid key
            included); those objects stay. Empty when all were deleted.

        Raises:
            IntegrationDisabledException: When built with ``enabled=False``.
        """
        self._check_enabled()
        return await asyncio.to_thread(self._remove_many_sync, list(keys), bucket)

    async def download_response(
        self,
        key: str,
        *,
        request: Request | None = None,
        bucket: str | None = None,
        filename: str | None = None,
        media_type: str | None = None,
        as_attachment: bool = True,
        chunk_size: int = LOCAL_STORAGE_CHUNK_SIZE,
        cache_control: str | None = None,
        headers: dict[str, str] | None = None,
    ) -> StreamingResponse:
        """Stream an object as a download response, ``Range`` included.

        Same behaviour as
        :meth:`AsyncMinIOClient.download_response
        <tempest_fastapi_sdk.storage.AsyncMinIOClient.download_response>`:
        both delegate to the same function.

        Args:
            key (str): Object key.
            request (Request | None): The incoming request, whose ``Range``
                and validator headers are honoured.
            bucket (str | None): Source bucket.
            filename (str | None): Name presented to the client.
            media_type (str | None): Content type; defaults to the stored
                one.
            as_attachment (bool): ``True`` forces a download; ``False`` asks
                for ``inline``, granted only for inline-safe types.
            chunk_size (int): Bytes per streamed chunk.
            cache_control (str | None): ``Cache-Control`` value.
            headers (dict[str, str] | None): Extra response headers.

        Returns:
            StreamingResponse: ``200``, ``206``, ``304`` or ``416``.

        Raises:
            LocalObjectNotFoundError: When the object is not stored.
            IntegrationDisabledException: When built with ``enabled=False``.
        """
        from tempest_fastapi_sdk.storage._download import object_download_response

        return await object_download_response(
            self,
            key,
            request=request,
            bucket=bucket,
            filename=filename,
            media_type=media_type,
            as_attachment=as_attachment,
            chunk_size=chunk_size,
            cache_control=cache_control,
            headers=headers,
        )


__all__: list[str] = [
    "DEFAULT_LOCAL_MAX_UPLOAD_BYTES",
    "DEFAULT_LOCAL_STORAGE_ROUTE_PREFIX",
    "LOCAL_GET_LINK_PARAMS",
    "LOCAL_META_SUFFIX",
    "LOCAL_POST_LINK_PARAMS",
    "LOCAL_PRESIGN_MAX_EXPIRES",
    "LOCAL_STORAGE_CHUNK_SIZE",
    "LOCAL_STORAGE_GET_PURPOSE",
    "LOCAL_STORAGE_POST_PURPOSE",
    "LOCAL_STORAGE_PUT_PURPOSE",
    "LocalObjectNotFoundError",
    "LocalObjectStorageClient",
    "bound_purpose",
]
