"""Unified file-storage facade over upload, download and presign.

:class:`FileStoreUtils` bundles the three pieces a service usually wires by
hand — :class:`~tempest_fastapi_sdk.utils.upload.UploadUtils` (validate +
persist), :class:`~tempest_fastapi_sdk.utils.download.DownloadUtils` (serve
bytes through the API) and the presigned-URL helpers of
:class:`~tempest_fastapi_sdk.AsyncMinIOClient` — behind one object that
targets a single storage backend.

The backend is picked once from ``source``:

* a directory path (``str``/``Path``) -> local disk
  (:class:`~tempest_fastapi_sdk.LocalUploadStorage`), or
* an :class:`~tempest_fastapi_sdk.AsyncMinIOClient` -> MinIO / S3
  (:class:`~tempest_fastapi_sdk.MinIOUploadStorage`).

A single :class:`~tempest_fastapi_sdk.UploadStorage` backend is built and
injected into the internal ``UploadUtils`` (so save/delete/exists share it),
while the download half receives the same ``source`` — for MinIO that is the
very same client instance, so the connection pool is shared, not duplicated.

Requires the ``[upload]`` extra for local disk, the ``[minio]`` extra for
MinIO. Both are imported lazily, so ``import tempest_fastapi_sdk`` keeps
working without them — :class:`FileStoreUtils` raises on first use instead.
"""

from __future__ import annotations

from collections.abc import AsyncIterable, Callable, Iterable
from datetime import timedelta
from pathlib import Path
from typing import TYPE_CHECKING

from tempest_fastapi_sdk.utils.download import DownloadUtils
from tempest_fastapi_sdk.utils.upload import UploadUtils

if TYPE_CHECKING:
    from fastapi import UploadFile
    from fastapi.responses import FileResponse, StreamingResponse
    from starlette.responses import Response

    from tempest_fastapi_sdk.storage.minio_client import AsyncMinIOClient
    from tempest_fastapi_sdk.utils.storage_backends import UploadStorage


class FileStoreUtils:
    """Single entry point for storing, serving and signing files.

    Composes an :class:`~tempest_fastapi_sdk.utils.upload.UploadUtils` and a
    :class:`~tempest_fastapi_sdk.utils.download.DownloadUtils` over one
    storage backend, plus presign shortcuts for the MinIO backend. Use it
    when a service both persists and serves the same files and you want one
    configured object instead of three.

    The individual pieces stay reachable for escape hatches:
    :attr:`uploader`, :attr:`downloader`, :attr:`backend` and
    :attr:`client`.

    Attributes:
        uploader (UploadUtils): Validation + persistence half.
        downloader (DownloadUtils): Read + serve half.
        backend (UploadStorage): The shared storage backend
            (``LocalUploadStorage`` or ``MinIOUploadStorage``).
        client (AsyncMinIOClient | None): The MinIO client when
            MinIO-backed, otherwise ``None`` (local disk).
    """

    def __init__(
        self,
        source: str | Path | AsyncMinIOClient,
        *,
        max_size_bytes: int | None = None,
        allowed_extensions: set[str] | None = None,
        allowed_mimetypes: set[str] | None = None,
        verify_magic_bytes: bool = False,
        chunk_size: int = 1024 * 1024,
    ) -> None:
        """Build the facade, selecting the backend from ``source``.

        Args:
            source (str | Path | AsyncMinIOClient): A directory path for
                local disk, or an ``AsyncMinIOClient`` for MinIO / S3. To
                target a non-default bucket, set the client's
                ``default_bucket`` — both halves read it.
            max_size_bytes (int | None): Reject uploads larger than this.
                ``None`` disables the size check.
            allowed_extensions (set[str] | None): Whitelist of file
                extensions (leading dots and case normalized internally).
            allowed_mimetypes (set[str] | None): Whitelist of MIME types
                (case-insensitive).
            verify_magic_bytes (bool): Sniff the first bytes of each upload
                and reject content that mismatches its declared type /
                allow-list. Default ``False``.
            chunk_size (int): Stream read chunk in bytes. Default 1 MiB.

        Raises:
            ImportError: When the required extra (``[upload]`` for local,
                ``[minio]`` for MinIO) is not installed.
        """
        from tempest_fastapi_sdk.utils.storage_backends import (
            LocalUploadStorage,
            MinIOUploadStorage,
        )

        backend: UploadStorage
        if isinstance(source, (str, Path)):
            self.client: AsyncMinIOClient | None = None
            backend = LocalUploadStorage(source)
        else:
            self.client = source
            backend = MinIOUploadStorage(source)

        self.backend: UploadStorage = backend
        self.uploader: UploadUtils = UploadUtils(
            source,
            backend=backend,
            max_size_bytes=max_size_bytes,
            allowed_extensions=allowed_extensions,
            allowed_mimetypes=allowed_mimetypes,
            verify_magic_bytes=verify_magic_bytes,
            chunk_size=chunk_size,
        )
        self.downloader: DownloadUtils = DownloadUtils(source)

    def validate(self, file: UploadFile) -> None:
        """Validate extension and MIME before reading the stream.

        Args:
            file (UploadFile): The FastAPI upload to validate.

        Raises:
            InvalidFileTypeException: If the extension or MIME type is not
                in the configured whitelist.
        """
        self.uploader.validate(file)

    async def save(
        self,
        file: UploadFile,
        *,
        subdir: str = "",
        filename: str | None = None,
        keep_original_name: bool = False,
        content_validator: Callable[[bytes], bool] | None = None,
    ) -> Path:
        """Validate and persist ``file`` to the configured backend.

        Args:
            file (UploadFile): The FastAPI upload.
            subdir (str): Optional sub-directory / key prefix.
            filename (str | None): Explicit final filename (sanitized to
                its basename). Takes precedence over ``keep_original_name``.
            keep_original_name (bool): Preserve the original filename when
                ``filename`` is not given. Default ``False``.
            content_validator (Callable[[bytes], bool] | None): Optional
                predicate run on the first chunk; ``False`` aborts the save.

        Returns:
            Path: ``Path(storage_key)`` to read the file back with
            :meth:`download` / :meth:`presigned_get_url`.

        Raises:
            InvalidFileTypeException: On a whitelist / content violation.
            FileTooLargeException: If the stream exceeds ``max_size_bytes``.
        """
        return await self.uploader.save(
            file,
            subdir=subdir,
            filename=filename,
            keep_original_name=keep_original_name,
            content_validator=content_validator,
        )

    async def replace(
        self,
        old_key: Path | str | None,
        file: UploadFile,
        *,
        subdir: str = "",
        filename: str | None = None,
        keep_original_name: bool = False,
        content_validator: Callable[[bytes], bool] | None = None,
    ) -> Path:
        """Save a new object and delete the one it replaces.

        Args:
            old_key (Path | str | None): Key of the object being replaced;
                ``None``/empty skips the delete (first upload).
            file (UploadFile): The new FastAPI upload.
            subdir (str): Optional sub-directory / key prefix.
            filename (str | None): Explicit final filename. Same rules as
                :meth:`save`.
            keep_original_name (bool): Preserve the upload's original
                filename when ``filename`` is not given.
            content_validator (Callable[[bytes], bool] | None): Optional
                first-chunk predicate; see :meth:`save`.

        Returns:
            Path: ``Path(storage_key)`` of the newly saved object.

        Raises:
            InvalidFileTypeException: If validation rejects the new file
                (the old object is left intact).
            FileTooLargeException: If the new stream exceeds
                ``max_size_bytes`` (the old object is left intact).
        """
        return await self.uploader.replace(
            old_key,
            file,
            subdir=subdir,
            filename=filename,
            keep_original_name=keep_original_name,
            content_validator=content_validator,
        )

    async def delete(self, key: Path | str) -> bool:
        """Delete a previously saved object via the shared backend.

        Args:
            key (Path | str): The storage key returned by :meth:`save`.

        Returns:
            bool: ``True`` if the object existed and was deleted, ``False``
            when it was already missing.

        Raises:
            InvalidFileTypeException: Local backend, when ``key`` resolves
                outside the base directory (path traversal attempt).
        """
        return await self.uploader.delete(key)

    async def exists(self, key: Path | str) -> bool:
        """Return whether an object lives at ``key``.

        Args:
            key (Path | str): The storage key.

        Returns:
            bool: ``True`` when the object exists.
        """
        return await self.backend.exists(str(key))

    async def download(
        self,
        key: str,
        *,
        subdir: str = "",
        filename: str | None = None,
        media_type: str | None = None,
        as_attachment: bool = True,
        headers: dict[str, str] | None = None,
    ) -> Response:
        """Build a download response for ``key`` from the configured backend.

        Args:
            key (str): File path (local, relative to the base dir) or object
                key (MinIO).
            subdir (str): Optional sub-directory / key prefix.
            filename (str | None): Name presented to the client. Defaults to
                the basename of ``key``.
            media_type (str | None): MIME type. Guessed/derived when omitted.
            as_attachment (bool): ``True`` forces a download; ``False`` inline.
            headers (dict[str, str] | None): Extra response headers.

        Returns:
            Response: A ``FileResponse`` (local) or ``StreamingResponse``
            (MinIO) ready to return from a router.

        Raises:
            NotFoundException: Local mode, when the path escapes the base
                directory or the file is missing.
            S3Error: MinIO mode, when the object is missing.
        """
        return await self.downloader.download(
            key,
            subdir=subdir,
            filename=filename,
            media_type=media_type,
            as_attachment=as_attachment,
            headers=headers,
        )

    def file_response(
        self,
        relative_path: Path | str,
        *,
        subdir: str = "",
        filename: str | None = None,
        media_type: str | None = None,
        as_attachment: bool = True,
        headers: dict[str, str] | None = None,
    ) -> FileResponse:
        """Build a ``FileResponse`` streaming a file from local disk.

        Args:
            relative_path (Path | str): Path to the file, relative to the
                base directory (resolved safely).
            subdir (str): Optional sub-directory under the base directory.
            filename (str | None): Name presented to the client. Defaults to
                the file's own basename.
            media_type (str | None): MIME type. Guessed from the filename
                when omitted.
            as_attachment (bool): ``True`` forces a download; ``False``
                serves inline. Default ``True``.
            headers (dict[str, str] | None): Extra response headers.

        Returns:
            FileResponse: The response to return from a router.

        Raises:
            NotFoundException: If the file cannot be located.
            RuntimeError: When this store is MinIO-backed — use
                :meth:`download` instead.
        """
        return self.downloader.file_response(
            relative_path,
            subdir=subdir,
            filename=filename,
            media_type=media_type,
            as_attachment=as_attachment,
            headers=headers,
        )

    def stream(
        self,
        content: bytes | Iterable[bytes] | AsyncIterable[bytes],
        *,
        filename: str,
        media_type: str | None = None,
        as_attachment: bool = True,
        headers: dict[str, str] | None = None,
    ) -> StreamingResponse:
        """Build a ``StreamingResponse`` from in-memory bytes or a generator.

        Use for payloads produced on the fly (a generated report, an
        in-memory zip) rather than read from storage.

        Args:
            content (bytes | Iterable[bytes] | AsyncIterable[bytes]): The
                payload. Raw ``bytes`` are wrapped in a single-chunk
                iterator; iterables are streamed as-is.
            filename (str): Name presented to the client.
            media_type (str | None): MIME type. Guessed from ``filename``
                when omitted.
            as_attachment (bool): ``True`` forces a download; ``False``
                serves inline. Default ``True``.
            headers (dict[str, str] | None): Extra response headers.

        Returns:
            StreamingResponse: The response to return from a router.
        """
        return self.downloader.stream(
            content,
            filename=filename,
            media_type=media_type,
            as_attachment=as_attachment,
            headers=headers,
        )

    def resolve(self, relative_path: Path | str, *, subdir: str = "") -> Path:
        """Resolve a client-supplied path safely under the local base dir.

        Args:
            relative_path (Path | str): Path relative to the base directory
                (absolute inputs and escaping ``..`` segments are rejected).
            subdir (str): Optional sub-directory between the base directory
                and ``relative_path``.

        Returns:
            Path: The resolved, existing file path.

        Raises:
            NotFoundException: If the path escapes the base directory, does
                not exist, or is not a regular file.
            RuntimeError: When this store is MinIO-backed — use
                :meth:`download` instead.
        """
        return self.downloader.resolve(relative_path, subdir=subdir)

    async def presigned_get_url(
        self,
        key: str,
        *,
        bucket: str | None = None,
        expires: timedelta = timedelta(hours=1),
    ) -> str | None:
        """Return a presigned GET URL, or ``None`` for the local backend.

        Args:
            key (str): The object key.
            bucket (str | None): Target bucket. ``None`` uses the client's
                ``default_bucket``.
            expires (timedelta): URL lifetime. Default 1 hour.

        Returns:
            str | None: A signed download URL, or ``None`` when the backend
            cannot mint one (local disk).
        """
        if self.client is None:
            return None
        return await self.client.presigned_get_url(
            key,
            bucket=bucket,
            expires=expires,
        )

    async def presigned_put_url(
        self,
        key: str,
        *,
        bucket: str | None = None,
        expires: timedelta = timedelta(minutes=15),
    ) -> str | None:
        """Return a presigned PUT URL, or ``None`` for the local backend.

        Lets the client upload directly to MinIO / S3 without streaming the
        bytes through the API.

        Args:
            key (str): The object key to upload to.
            bucket (str | None): Target bucket. ``None`` uses the client's
                ``default_bucket``.
            expires (timedelta): URL lifetime. Default 15 minutes.

        Returns:
            str | None: A signed upload URL, or ``None`` when the backend
            cannot mint one (local disk).
        """
        if self.client is None:
            return None
        return await self.client.presigned_put_url(
            key,
            bucket=bucket,
            expires=expires,
        )


__all__: list[str] = [
    "FileStoreUtils",
]
