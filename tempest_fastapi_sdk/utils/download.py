"""Serve files to clients through the API without exposing public URLs.

:class:`DownloadUtils` is the read counterpart to
:class:`~tempest_fastapi_sdk.utils.upload.UploadUtils`: instead of handing
the client a static link, the endpoint streams the bytes itself. Files are
confined to a base directory (path-traversal safe) and served as
``FileResponse`` (disk) or ``StreamingResponse`` (in-memory bytes or
generators).

Depends only on Starlette responses, which ship with FastAPI — no optional
extra is required.
"""

from __future__ import annotations

import mimetypes
from collections.abc import AsyncIterable, Iterable
from pathlib import Path
from typing import TYPE_CHECKING
from urllib.parse import quote

from fastapi.responses import FileResponse, StreamingResponse

from tempest_fastapi_sdk.exceptions.not_found import NotFoundException

if TYPE_CHECKING:
    from starlette.responses import Response

    from tempest_fastapi_sdk.storage.minio_client import AsyncMinIOClient

_DEFAULT_MEDIA_TYPE: str = "application/octet-stream"


def build_content_disposition(filename: str, *, as_attachment: bool = True) -> str:
    """Build an RFC 6266 ``Content-Disposition`` header value.

    Emits both an ASCII ``filename`` (with quotes/backslashes stripped as a
    legacy fallback) and a UTF-8 ``filename*`` parameter (RFC 5987) so
    non-ASCII names survive across clients.

    Args:
        filename (str): The name the client should see for the download.
            Reduced to its basename so a path can never be injected.
        as_attachment (bool): ``True`` forces a download
            (``attachment``); ``False`` lets the browser render inline
            (``inline``). Default ``True``.

    Returns:
        str: A ready-to-use header value, e.g.
        ``attachment; filename="a.pdf"; filename*=UTF-8''a.pdf``.
    """
    disposition: str = "attachment" if as_attachment else "inline"
    safe_name: str = Path(filename).name or "download"
    ascii_fallback: str = safe_name.encode("ascii", "ignore").decode("ascii")
    ascii_fallback = ascii_fallback.replace("\\", "_").replace('"', "_")
    if not ascii_fallback:
        ascii_fallback = "download"
    encoded: str = quote(safe_name, safe="")
    return f"{disposition}; filename=\"{ascii_fallback}\"; filename*=UTF-8''{encoded}"


class DownloadUtils:
    """Serve files for download — from a local directory **or** MinIO.

    Pick the backend **once at construction**:

    * pass a directory (``DownloadUtils("var/uploads")``) to serve files
      from local disk, or
    * pass an :class:`~tempest_fastapi_sdk.AsyncMinIOClient`
      (``DownloadUtils(minio_client)``) to stream objects straight from a
      bucket.

    Then call :meth:`download` with the file's path/key for either backend.
    For local disk, all reads are confined to ``base_dir``: a path that
    resolves outside it (``../`` traversal, absolute paths, symlink
    escapes) raises :class:`NotFoundException` rather than leaking
    arbitrary files — the same 404 as a missing file.

    Attributes:
        base_dir (Path | None): Resolved local root, or ``None`` in MinIO
            mode.
    """

    def __init__(self, source: str | Path | AsyncMinIOClient) -> None:
        """Initialize with a local directory or a MinIO client.

        Args:
            source (str | Path | AsyncMinIOClient): A directory path to
                serve files from local disk, or an ``AsyncMinIOClient`` to
                stream objects from a bucket. The local directory is
                resolved to an absolute path; it need not exist yet.
        """
        if isinstance(source, (str, Path)):
            self.base_dir: Path | None = Path(source).resolve()
            self._minio: AsyncMinIOClient | None = None
        else:
            self.base_dir = None
            self._minio = source

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

        Works the same for both backends: local disk returns a streamed
        ``FileResponse`` (range-aware), MinIO returns a ``StreamingResponse``
        proxied from the bucket.

        Args:
            key (str): File path (local, relative to ``base_dir``) or object
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
            NotFoundException: Local mode, when the path escapes ``base_dir``
                or the file is missing.
            S3Error: MinIO mode, when the object is missing.
        """
        if self._minio is not None:
            object_key = f"{subdir.rstrip('/')}/{key}" if subdir else key
            return await self._minio.download_response(
                object_key,
                filename=filename,
                media_type=media_type,
                as_attachment=as_attachment,
                headers=headers,
            )
        return self.file_response(
            key,
            subdir=subdir,
            filename=filename,
            media_type=media_type,
            as_attachment=as_attachment,
            headers=headers,
        )

    def resolve(self, relative_path: Path | str, *, subdir: str = "") -> Path:
        """Resolve a client-supplied path safely under ``base_dir``.

        Args:
            relative_path (Path | str): Path relative to ``base_dir`` (or
                ``base_dir/subdir``). Absolute inputs and ``..`` segments
                that escape the base are rejected.
            subdir (str): Optional sub-directory between ``base_dir`` and
                ``relative_path`` (e.g. ``"invoices"``).

        Returns:
            Path: The resolved, existing file path.

        Raises:
            NotFoundException: If the path escapes ``base_dir``, does not
                exist, or is not a regular file.
            RuntimeError: When this ``DownloadUtils`` was built with a MinIO
                client (no local ``base_dir``) — use :meth:`download`.
        """
        if self.base_dir is None:
            raise RuntimeError(
                "resolve()/file_response() need a local DownloadUtils; "
                "this one is MinIO-backed — call download(key) instead."
            )
        root: Path = (self.base_dir / subdir).resolve() if subdir else self.base_dir
        target: Path = (root / relative_path).resolve()

        if target != root and root not in target.parents:
            raise NotFoundException(details={"path": str(relative_path)})

        if not target.is_file():
            raise NotFoundException(details={"path": str(relative_path)})

        return target

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
        """Build a ``FileResponse`` streaming a file from disk.

        The response is streamed in chunks by Starlette and supports HTTP
        range requests, so large files never load fully into memory.

        Args:
            relative_path (Path | str): Path to the file, relative to
                ``base_dir`` (resolved via :meth:`resolve`).
            subdir (str): Optional sub-directory under ``base_dir``.
            filename (str | None): Name presented to the client. Defaults
                to the file's own basename.
            media_type (str | None): MIME type. Guessed from the filename
                extension when omitted, falling back to
                ``application/octet-stream``.
            as_attachment (bool): ``True`` forces a download; ``False``
                serves inline (e.g. view a PDF in-browser). Default
                ``True``.
            headers (dict[str, str] | None): Extra response headers.

        Returns:
            FileResponse: The response to return from a router.

        Raises:
            NotFoundException: If the file cannot be located (see
                :meth:`resolve`).
        """
        target: Path = self.resolve(relative_path, subdir=subdir)
        download_name: str = filename or target.name
        resolved_media_type: str = (
            media_type or mimetypes.guess_type(download_name)[0] or _DEFAULT_MEDIA_TYPE
        )
        response_headers: dict[str, str] = dict(headers or {})
        response_headers["content-disposition"] = build_content_disposition(
            download_name, as_attachment=as_attachment
        )
        return FileResponse(
            path=target,
            media_type=resolved_media_type,
            headers=response_headers,
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

        Use when the payload is produced on the fly (a generated report, a
        zip built in memory, decrypted bytes) rather than read from
        ``base_dir``. ``base_dir`` is not consulted here.

        Args:
            content (bytes | Iterable[bytes] | AsyncIterable[bytes]): The
                payload. Raw ``bytes`` are wrapped in a single-chunk
                iterator; sync and async byte iterables are streamed as-is.
            filename (str): Name presented to the client.
            media_type (str | None): MIME type. Guessed from ``filename``
                when omitted, falling back to ``application/octet-stream``.
            as_attachment (bool): ``True`` forces a download; ``False``
                serves inline. Default ``True``.
            headers (dict[str, str] | None): Extra response headers.

        Returns:
            StreamingResponse: The response to return from a router.
        """
        body: Iterable[bytes] | AsyncIterable[bytes] = (
            iter((content,)) if isinstance(content, bytes) else content
        )
        resolved_media_type: str = (
            media_type or mimetypes.guess_type(filename)[0] or _DEFAULT_MEDIA_TYPE
        )
        response_headers: dict[str, str] = dict(headers or {})
        response_headers["content-disposition"] = build_content_disposition(
            filename, as_attachment=as_attachment
        )
        return StreamingResponse(
            content=body,
            media_type=resolved_media_type,
            headers=response_headers,
        )


__all__: list[str] = [
    "DownloadUtils",
    "build_content_disposition",
]
