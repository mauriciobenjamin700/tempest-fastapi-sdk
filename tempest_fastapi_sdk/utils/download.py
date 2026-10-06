"""Serve files to clients through the API without exposing public URLs.

:class:`DownloadUtils` is the read counterpart to
:class:`~tempest_fastapi_sdk.utils.upload.UploadUtils`: instead of handing
the client a static link, the endpoint streams the bytes itself. Files are
confined to a base directory (path-traversal safe) and served as
``FileResponse`` (disk) or ``StreamingResponse`` (in-memory bytes or
generators).

A download answers **on the API's own origin**, with a ``Content-Type`` that
in the usual flow is the one the client declared at upload. So every
response carries
:data:`~tempest_fastapi_sdk.api.static.DEFAULT_STATIC_SECURITY_HEADERS`
(a header of the same name passed by the caller wins), and ``inline`` is
granted only to a type in :data:`INLINE_SAFE_MEDIA_TYPES` — anything else is
served as ``attachment`` whatever ``as_attachment`` says.

Depends only on Starlette responses, which ship with FastAPI — no optional
extra is required.
"""

from __future__ import annotations

from collections.abc import AsyncIterable, Iterable, Mapping
from pathlib import Path
from typing import TYPE_CHECKING
from urllib.parse import quote

from fastapi.responses import FileResponse, StreamingResponse

from tempest_fastapi_sdk.exceptions.not_found import NotFoundException
from tempest_fastapi_sdk.utils._security_headers import (
    DEFAULT_STATIC_SECURITY_HEADERS,
)
from tempest_fastapi_sdk.utils.media_types import guess_media_type

if TYPE_CHECKING:
    from starlette.requests import Request
    from starlette.responses import Response

    from tempest_fastapi_sdk.storage.minio_client import AsyncMinIOClient

_DEFAULT_MEDIA_TYPE: str = "application/octet-stream"

INLINE_SAFE_MEDIA_TYPES: frozenset[str] = frozenset(
    {
        "application/pdf",
        "audio/aac",
        "audio/flac",
        "audio/mp4",
        "audio/mpeg",
        "audio/ogg",
        "audio/wav",
        "audio/webm",
        "audio/x-wav",
        "image/avif",
        "image/gif",
        "image/jpeg",
        "image/png",
        "image/webp",
        "text/plain",
        "video/mp4",
        "video/ogg",
        "video/quicktime",
        "video/webm",
    }
)
"""Media types a download may serve ``inline`` when the caller asks for it.

A download answers on the API's own origin, and ``inline`` is what lets the
browser render the bytes as a document of that origin. ``text/html`` there
is a page that runs script with the viewer's session on the API;
``image/svg+xml`` is the same risk behind an image type, since SVG can carry
``<script>``. The list is therefore an
allowlist: a type absent from it is served as ``attachment`` even with
``as_attachment=False``, and so is a type the SDK does not know at build time
(``None``).

What is inside is raster images, ``application/pdf``, ``text/plain`` and
common audio and video — the inline uses a download helper is asked for
(``<img>``, ``<video>``, viewing a PDF or a log in the browser). SVG and HTML
are left out on purpose. To serve another type inline, build the response
yourself; the helpers do not take an override, because the whole point is
that an uploader-chosen type cannot reach ``inline``.
"""


def _is_inline_safe(media_type: str | None) -> bool:
    """Tell whether ``media_type`` may be served ``inline``.

    Args:
        media_type (str | None): The type the response will carry, with or
            without parameters (``"text/plain; charset=utf-8"``). ``None``
            means the type is unknown when the header is built.

    Returns:
        bool: ``True`` only for a type in :data:`INLINE_SAFE_MEDIA_TYPES`,
        compared without parameters and case-insensitively.
    """
    if media_type is None:
        return False
    essence: str = media_type.split(";", 1)[0].strip().lower()
    return essence in INLINE_SAFE_MEDIA_TYPES


def _with_security_headers(headers: Mapping[str, str] | None) -> dict[str, str]:
    """Return the caller's headers plus the missing download security headers.

    :data:`~tempest_fastapi_sdk.api.static.DEFAULT_STATIC_SECURITY_HEADERS`
    is merged with ``setdefault`` semantics, comparing names
    case-insensitively the way HTTP does: a header the caller already set,
    in any casing, is kept as-is and not sent twice.

    Args:
        headers (Mapping[str, str] | None): Headers the caller passed, or
            ``None``.

    Returns:
        dict[str, str]: A new dict; ``headers`` is not mutated.
    """
    merged: dict[str, str] = dict(headers or {})
    present: set[str] = {name.lower() for name in merged}
    for name, value in DEFAULT_STATIC_SECURITY_HEADERS.items():
        if name.lower() not in present:
            merged[name] = value
    return merged


def build_content_disposition(
    filename: str,
    *,
    as_attachment: bool = True,
    media_type: str | None = None,
) -> str:
    """Build an RFC 6266 ``Content-Disposition`` header value.

    Emits both an ASCII ``filename`` (with quotes/backslashes stripped as a
    legacy fallback) and a UTF-8 ``filename*`` parameter (RFC 5987) so
    non-ASCII names survive across clients.

    The name is treated as untrusted, because in the documented usage it is
    ``UploadFile.filename`` — a value the client chose. Taking the basename
    stops a path from being injected, but on its own left the control
    characters in: a name containing ``\\r\\n`` produced a header value with a
    real line break, which an ASGI server that does not validate header
    values (uvicorn on ``httptools``) writes to the socket verbatim, letting
    the caller append headers of their own to your response. Every character
    below ``0x20`` plus ``DEL`` is dropped here, so what comes out is always
    a single header line.

    ``as_attachment=False`` is a request, not a decision: it yields
    ``inline`` only when ``media_type`` is in
    :data:`INLINE_SAFE_MEDIA_TYPES`. Any other type — ``text/html``,
    ``image/svg+xml``, ``application/octet-stream`` — and ``None`` yield
    ``attachment``.

    Args:
        filename (str): The name the client should see for the download.
            Reduced to its basename, with control characters removed, so
            neither a path nor a header break can be injected.
        as_attachment (bool): ``True`` forces a download
            (``attachment``); ``False`` asks for ``inline``, granted only
            to an inline-safe ``media_type``. Default ``True``.
        media_type (str | None): The type the response carries. Parameters
            are ignored in the check. ``None`` (unknown) is not inline-safe.
            Default ``None``.

    Returns:
        str: A ready-to-use header value, e.g.
        ``attachment; filename="a.pdf"; filename*=UTF-8''a.pdf``.
    """
    inline: bool = not as_attachment and _is_inline_safe(media_type)
    disposition: str = "inline" if inline else "attachment"
    sanitized: str = _strip_control_chars(filename)
    safe_name: str = Path(sanitized).name or "download"
    ascii_fallback: str = safe_name.encode("ascii", "ignore").decode("ascii")
    ascii_fallback = ascii_fallback.replace("\\", "_").replace('"', "_")
    if not ascii_fallback:
        ascii_fallback = "download"
    encoded: str = quote(safe_name, safe="")
    return f"{disposition}; filename=\"{ascii_fallback}\"; filename*=UTF-8''{encoded}"


def _strip_control_chars(value: str) -> str:
    """Return ``value`` without C0/C1 control characters or ``DEL``.

    Args:
        value (str): The raw, client-supplied name.

    Returns:
        str: The same text with every non-printable character removed.
    """
    return "".join(
        char for char in value if not (ord(char) < 0x20 or 0x7F <= ord(char) <= 0x9F)
    )


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
        request: Request | None = None,
        cache_control: str | None = None,
        headers: dict[str, str] | None = None,
    ) -> Response:
        """Build a download response for ``key`` from the configured backend.

        Works the same for both backends: local disk returns a streamed
        ``FileResponse`` (range-aware), MinIO returns a ``StreamingResponse``
        proxied from the bucket — range- and validator-aware when
        ``request`` is passed.

        Args:
            key (str): File path (local, relative to ``base_dir``) or object
                key (MinIO).
            subdir (str): Optional sub-directory / key prefix.
            filename (str | None): Name presented to the client. Defaults to
                the basename of ``key``.
            media_type (str | None): MIME type. Guessed/derived when omitted.
            as_attachment (bool): ``True`` forces a download; ``False`` asks
                for ``inline``, granted only to a type in
                :data:`INLINE_SAFE_MEDIA_TYPES`.
            request (Request | None): The incoming request. MinIO mode needs
                it to answer ``Range`` with ``206`` and validators with
                ``304`` (see :meth:`AsyncMinIOClient.download_response`);
                local mode ignores it, because ``FileResponse`` already reads
                ``Range`` from the ASGI scope.
            cache_control (str | None): ``Cache-Control`` value for the
                response. ``None`` sends none.
            headers (dict[str, str] | None): Extra response headers. One
                named like a security default (``X-Content-Type-Options``,
                ``Content-Security-Policy``, ``Cross-Origin-Resource-Policy``)
                replaces that default.

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
                request=request,
                filename=filename,
                media_type=media_type,
                as_attachment=as_attachment,
                cache_control=cache_control,
                headers=headers,
            )
        local_headers: dict[str, str] = dict(headers or {})
        if cache_control is not None:
            local_headers["cache-control"] = cache_control
        return self.file_response(
            key,
            subdir=subdir,
            filename=filename,
            media_type=media_type,
            as_attachment=as_attachment,
            headers=local_headers,
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
            as_attachment (bool): ``True`` forces a download; ``False`` asks
                for ``inline`` (e.g. view a PDF in-browser), granted only to
                a type in :data:`INLINE_SAFE_MEDIA_TYPES`. Default ``True``.
            headers (dict[str, str] | None): Extra response headers. One
                named like a security default (``X-Content-Type-Options``,
                ``Content-Security-Policy``, ``Cross-Origin-Resource-Policy``)
                replaces that default.

        Returns:
            FileResponse: The response to return from a router.

        Raises:
            NotFoundException: If the file cannot be located (see
                :meth:`resolve`).
        """
        target: Path = self.resolve(relative_path, subdir=subdir)
        download_name: str = filename or target.name
        resolved_media_type: str = (
            media_type or guess_media_type(download_name) or _DEFAULT_MEDIA_TYPE
        )
        response_headers: dict[str, str] = _with_security_headers(headers)
        response_headers["content-disposition"] = build_content_disposition(
            download_name, as_attachment=as_attachment, media_type=resolved_media_type
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
            as_attachment (bool): ``True`` forces a download; ``False`` asks
                for ``inline``, granted only to a type in
                :data:`INLINE_SAFE_MEDIA_TYPES`. Default ``True``.
            headers (dict[str, str] | None): Extra response headers. One
                named like a security default (``X-Content-Type-Options``,
                ``Content-Security-Policy``, ``Cross-Origin-Resource-Policy``)
                replaces that default.

        Returns:
            StreamingResponse: The response to return from a router.
        """
        body: Iterable[bytes] | AsyncIterable[bytes] = (
            iter((content,)) if isinstance(content, bytes) else content
        )
        resolved_media_type: str = (
            media_type or guess_media_type(filename) or _DEFAULT_MEDIA_TYPE
        )
        response_headers: dict[str, str] = _with_security_headers(headers)
        response_headers["content-disposition"] = build_content_disposition(
            filename, as_attachment=as_attachment, media_type=resolved_media_type
        )
        return StreamingResponse(
            content=body,
            media_type=resolved_media_type,
            headers=response_headers,
        )


__all__: list[str] = [
    "INLINE_SAFE_MEDIA_TYPES",
    "DownloadUtils",
    "build_content_disposition",
]
