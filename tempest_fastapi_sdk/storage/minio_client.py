"""Async wrapper around the official ``minio`` SDK.

Exposes the operations a typical FastAPI service actually needs:
bucket lifecycle (ensure / exists / list / remove), object I/O
(put / get / stream / stat / list / remove / copy) and presigned
URLs (GET / PUT). Everything else (versioning, lifecycle XML,
SSE-KMS, multipart tuning) is available via the underlying
``client`` attribute — drop down when you need it.

The official ``minio`` package is *synchronous*. To honor the SDK's
async-first convention we wrap every blocking call in
``asyncio.to_thread`` — the calling coroutine yields while the
upload/download runs in the default executor, so the event loop
stays responsive under load.

The ``minio`` import is **lazy**: the dependency only loads when
:class:`AsyncMinIOClient` is instantiated, so projects that don't
use object storage are not forced to install the extra.
"""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator, Awaitable, Iterable, Sequence
from dataclasses import dataclass
from datetime import datetime, timedelta
from io import BytesIO
from pathlib import Path
from types import TracebackType
from typing import TYPE_CHECKING, BinaryIO, TypeVar

if TYPE_CHECKING:
    from minio import Minio
    from minio.datatypes import Object as _MinioObject
    from starlette.requests import Request
    from starlette.responses import Response, StreamingResponse

_T = TypeVar("_T")


@dataclass(frozen=True, slots=True)
class ObjectStat:
    """Subset of object metadata returned by :meth:`AsyncMinIOClient.stat_object`.

    The full ``minio.datatypes.Object`` instance is also reachable via
    the ``raw`` attribute when you need the long tail of fields
    (version id, owner, restoration state, etc.).

    Attributes:
        bucket (str): Bucket the object lives in.
        key (str): Object key (S3 path).
        size (int): Size in bytes.
        etag (str | None): Server-side ETag (quotes stripped).
        content_type (str | None): MIME type recorded at upload.
        last_modified (datetime | None): Last modification timestamp
            in UTC.
        metadata (dict[str, str]): User metadata keyed without the
            ``x-amz-meta-`` prefix.
        raw (minio.datatypes.Object): Underlying ``minio`` ``Object``
            for advanced use (versioning id, owner, restore state, …).
    """

    bucket: str
    key: str
    size: int
    etag: str | None
    content_type: str | None
    last_modified: datetime | None
    metadata: dict[str, str]
    raw: _MinioObject


@dataclass(frozen=True, slots=True)
class PutObjectItem:
    """One object to upload in an :meth:`AsyncMinIOClient.put_objects` batch.

    Mirrors the per-object arguments of :meth:`AsyncMinIOClient.put_object`
    so a single batch can carry heterogeneous payloads — different content
    types, metadata, or unknown-length streams.

    Attributes:
        key (str): Destination object key.
        data (bytes | BinaryIO): Payload. ``bytes`` is wrapped in a
            ``BytesIO``; file-like objects are forwarded as-is and require
            ``length``.
        content_type (str): MIME type. ``"application/octet-stream"`` by
            default.
        metadata (dict[str, str] | None): User metadata stored under the
            ``x-amz-meta-`` namespace by ``minio``; pass plain names.
        length (int | None): Payload size in bytes. Required for file-like
            data; pass ``-1`` for unknown-length streams to trigger
            multipart upload. Computed automatically for ``bytes``.
        part_size (int): Multipart chunk size in bytes. At least 5 MiB.
            Default 10 MiB.
    """

    key: str
    data: bytes | BinaryIO
    content_type: str = "application/octet-stream"
    metadata: dict[str, str] | None = None
    length: int | None = None
    part_size: int = 10 * 1024 * 1024


@dataclass(frozen=True, slots=True)
class ObjectDeleteError:
    """One key that :meth:`AsyncMinIOClient.remove_objects` failed to delete.

    Attributes:
        key (str): Object key the store refused to delete.
        code (str): S3 error code reported for the key (``AccessDenied``,
            ``InternalError``, ...).
        message (str): Human-readable message reported by the store.
    """

    key: str
    code: str
    message: str


class AsyncMinIOClient:
    """Async-friendly facade over ``minio.Minio``.

    Use as an async context manager when you want explicit cleanup,
    or hold a long-lived instance on the FastAPI app — the
    underlying ``Minio`` client is thread-safe and reuses its
    connection pool.

    Example:

        >>> from tempest_fastapi_sdk import AsyncMinIOClient
        >>> storage = AsyncMinIOClient(
        ...     endpoint="localhost:9000",
        ...     access_key="minioadmin",
        ...     secret_key="minioadmin",
        ...     default_bucket="uploads",
        ... )
        >>> await storage.ensure_bucket()
        >>> await storage.put_object("hello.txt", b"world")
        >>> body = await storage.get_object_bytes("hello.txt")
        >>> assert body == b"world"
    """

    def __init__(
        self,
        endpoint: str,
        access_key: str,
        secret_key: str,
        *,
        default_bucket: str = "uploads",
        secure: bool = False,
        region: str = "us-east-1",
        session_token: str | None = None,
        public_endpoint: str | None = None,
        public_secure: bool | None = None,
        accel_redirect: bool = False,
        accel_prefix: str = "/_bucket/",
    ) -> None:
        """Initialize the client.

        Args:
            endpoint (str): ``host[:port]`` without scheme. Used for
                every server-side operation.
            access_key (str): S3 access key.
            secret_key (str): S3 secret key.
            default_bucket (str): Bucket used by object operations
                when no explicit ``bucket`` keyword is passed.
                Created by :meth:`ensure_bucket`.
            secure (bool): Use HTTPS when ``True``.
            region (str): S3 region. Match the bucket region for
                AWS S3; any value works for MinIO.
            session_token (str | None): Optional STS session token
                for temporary credentials.
            public_endpoint (str | None): Split-endpoint mode. When set,
                presigned URLs (:meth:`presigned_get_url` /
                :meth:`presigned_put_url`) are signed against this host
                instead of ``endpoint`` — for deployments where the
                backend reaches MinIO over a private network but the
                browser must hit a public, TLS-terminated host. A
                ``scheme://`` prefix and any trailing path are stripped,
                and ``https://`` implies ``public_secure=True``. ``None``
                signs presigned URLs with ``endpoint`` (unchanged).
            public_secure (bool | None): HTTPS for ``public_endpoint``.
                ``None`` falls back to ``secure`` (unless a ``https://``
                scheme on ``public_endpoint`` forces it).
            accel_redirect (bool): Delivery mode of :meth:`serve_object`.
                ``True`` answers with an ``X-Accel-Redirect`` for nginx to
                fetch the object (:meth:`accel_redirect_response`);
                ``False`` (default) streams the bytes through the app
                (:meth:`download_response`).
            accel_prefix (str): Internal nginx location the
                ``X-Accel-Redirect`` points into, and the default
                ``internal_prefix`` of :meth:`accel_redirect_response`.
                Must start with ``/``. Default ``"/_bucket/"``.

        Raises:
            ValueError: When ``accel_prefix`` does not start with ``/``.
            ImportError: When the ``minio`` package is not
                installed. Install the ``[minio]`` extra:
                ``pip install tempest-fastapi-sdk[minio]``.

        Notes:
            In split-endpoint mode a second client is built with the
            same credentials, whose only job is signing presigned URLs
            against the public host. SigV4 signs the ``Host`` header,
            so the URL has to be *signed* with the public endpoint —
            rewriting the host afterwards would invalidate the
            signature.
        """
        try:
            from minio import Minio
        except ImportError as exc:  # pragma: no cover - exercised via extras
            raise ImportError(
                "AsyncMinIOClient requires the 'minio' package. "
                "Install with: pip install tempest-fastapi-sdk[minio]"
            ) from exc

        self.accel_redirect: bool = accel_redirect
        self.accel_prefix: str = self._check_accel_prefix(accel_prefix)
        self.endpoint: str = endpoint
        self.default_bucket: str = default_bucket
        self.region: str = region
        self.secure: bool = secure
        self.client: Minio = Minio(
            endpoint,
            access_key=access_key,
            secret_key=secret_key,
            secure=secure,
            region=region,
            session_token=session_token,
        )

        self.public_endpoint: str | None = None
        self._presign_client: Minio = self.client
        if public_endpoint:
            host, resolved_secure = self._split_public_endpoint(
                public_endpoint, default_secure=secure, override=public_secure
            )
            self.public_endpoint = host
            self.public_secure: bool = resolved_secure
            self._presign_client = Minio(
                host,
                access_key=access_key,
                secret_key=secret_key,
                secure=resolved_secure,
                region=region,
                session_token=session_token,
            )
        else:
            self.public_secure = secure

    @staticmethod
    def _check_accel_prefix(prefix: str) -> str:
        """Validate an internal nginx prefix for ``X-Accel-Redirect``.

        nginx resolves the header value as a URI on the same server, so a
        value without the leading ``/`` never matches the internal location.

        Args:
            prefix (str): The configured prefix, e.g. ``"/_bucket/"``.

        Returns:
            str: The same prefix.

        Raises:
            ValueError: When ``prefix`` does not start with ``/``.
        """
        if not prefix.startswith("/"):
            raise ValueError(
                f"accel prefix must start with '/', got {prefix!r} "
                "(nginx matches X-Accel-Redirect against its own locations)"
            )
        return prefix

    @staticmethod
    def _split_public_endpoint(
        endpoint: str,
        *,
        default_secure: bool,
        override: bool | None,
    ) -> tuple[str, bool]:
        """Parse a public endpoint into ``(host[:port], secure)``.

        Accepts a bare ``host[:port]`` or a ``scheme://host[:port]/path``
        value (``minio-py`` rejects scheme/path, so both are stripped).
        The ``secure`` flag is resolved as: explicit ``override`` →
        ``https`` scheme → ``default_secure``.

        Args:
            endpoint (str): The configured public endpoint.
            default_secure (bool): Fallback when no scheme / override.
            override (bool | None): Explicit ``public_secure`` value.

        Returns:
            tuple[str, bool]: The bare host and the resolved secure flag.
        """
        value = endpoint.strip()
        scheme_secure: bool | None = None
        if "://" in value:
            scheme, _, value = value.partition("://")
            scheme_secure = scheme.lower() == "https"
        host = value.split("/", 1)[0].rstrip("/")
        if override is not None:
            secure = override
        elif scheme_secure is not None:
            secure = scheme_secure
        else:
            secure = default_secure
        return host, secure

    async def __aenter__(self) -> AsyncMinIOClient:
        """Enter the async context — no-op, returns self.

        Returns:
            AsyncMinIOClient: This instance.
        """
        return self

    async def __aexit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        tb: TracebackType | None,
    ) -> None:
        """Exit the async context — no-op (``minio`` has no close)."""
        del exc_type, exc, tb
        return None

    def _bucket(self, bucket: str | None) -> str:
        """Return ``bucket`` when provided, else ``default_bucket``."""
        return bucket or self.default_bucket

    # ------------------------------------------------------------------
    # Bucket lifecycle
    # ------------------------------------------------------------------

    async def bucket_exists(self, bucket: str | None = None) -> bool:
        """Check whether ``bucket`` exists.

        Args:
            bucket (str | None): Target bucket; defaults to
                ``default_bucket``.

        Returns:
            bool: ``True`` when the bucket exists and is reachable
            with the configured credentials.
        """
        target = self._bucket(bucket)
        return await asyncio.to_thread(self.client.bucket_exists, target)

    async def ensure_bucket(self, bucket: str | None = None) -> bool:
        """Create the bucket if it does not exist yet.

        Args:
            bucket (str | None): Target bucket; defaults to
                ``default_bucket``.

        Returns:
            bool: ``True`` when a bucket was created, ``False``
            when it already existed.
        """
        target = self._bucket(bucket)

        def _ensure() -> bool:
            if self.client.bucket_exists(target):
                return False
            self.client.make_bucket(target, location=self.region)
            return True

        return await asyncio.to_thread(_ensure)

    async def list_buckets(self) -> list[str]:
        """Return every bucket reachable with the current credentials.

        Returns:
            list[str]: Bucket names. Empty list when none exist.
        """

        def _list() -> list[str]:
            return [b.name for b in self.client.list_buckets()]

        return await asyncio.to_thread(_list)

    async def remove_bucket(self, bucket: str | None = None) -> None:
        """Delete an empty bucket.

        Args:
            bucket (str | None): Target bucket; defaults to
                ``default_bucket``.

        Raises:
            S3Error: When the bucket is missing or non-empty.
        """
        target = self._bucket(bucket)
        await asyncio.to_thread(self.client.remove_bucket, target)

    # ------------------------------------------------------------------
    # Object I/O
    # ------------------------------------------------------------------

    async def put_object(
        self,
        key: str,
        data: bytes | BinaryIO,
        *,
        bucket: str | None = None,
        content_type: str = "application/octet-stream",
        metadata: dict[str, str] | None = None,
        length: int | None = None,
        part_size: int = 10 * 1024 * 1024,
    ) -> str:
        """Upload an object.

        Accepts both raw ``bytes`` and any binary file-like object
        (``open("file", "rb")``, ``BytesIO``, ``UploadFile.file``).
        For unknown-length streams pass ``length=-1`` to enable
        multipart upload via ``part_size`` chunks.

        Args:
            key (str): Destination object key.
            data (bytes | BinaryIO): Payload. ``bytes`` is wrapped
                in a ``BytesIO``; file-like objects are forwarded
                as-is.
            bucket (str | None): Override target bucket.
            content_type (str): MIME type. ``"application/octet-stream"``
                by default.
            metadata (dict[str, str] | None): User metadata. Keys
                are stored under the ``x-amz-meta-`` namespace by
                ``minio`` automatically — pass plain names.
            length (int | None): Payload size in bytes. Required
                for unknown-length streams; computed automatically
                when ``data`` is ``bytes``.
            part_size (int): Chunk size for multipart upload (when
                ``length`` is ``-1`` or larger than 5 GiB). Must be
                at least 5 MiB. Default 10 MiB.

        Returns:
            str: ETag of the uploaded object (quotes stripped).

        Raises:
            S3Error: When the upload fails (auth, network, bucket
                missing, content rejected).
        """
        target_bucket = self._bucket(bucket)
        if isinstance(data, bytes | bytearray):
            stream: BinaryIO = BytesIO(bytes(data))
            payload_length: int = len(data) if length is None else length
        else:
            stream = data
            if length is None:
                raise ValueError(
                    "length must be provided for file-like data; pass -1 "
                    "for unknown-length streams to trigger multipart upload"
                )
            payload_length = length

        def _put() -> str:
            result = self.client.put_object(
                target_bucket,
                key,
                stream,
                payload_length,
                content_type=content_type,
                metadata=metadata,  # type: ignore[arg-type]
                part_size=part_size,
            )
            return (result.etag or "").strip('"')

        return await asyncio.to_thread(_put)

    async def fput_object(
        self,
        key: str,
        file_path: str | Path,
        *,
        bucket: str | None = None,
        content_type: str = "application/octet-stream",
        metadata: dict[str, str] | None = None,
    ) -> str:
        """Upload a file from disk.

        Args:
            key (str): Destination object key.
            file_path (str | Path): Source path on disk.
            bucket (str | None): Override target bucket.
            content_type (str): MIME type.
            metadata (dict[str, str] | None): User metadata.

        Returns:
            str: ETag of the uploaded object (quotes stripped).

        Raises:
            FileNotFoundError: When ``file_path`` does not exist.
            S3Error: When the upload fails.
        """
        target_bucket = self._bucket(bucket)
        path = Path(file_path)

        def _fput() -> str:
            result = self.client.fput_object(
                target_bucket,
                key,
                str(path),
                content_type=content_type,
                metadata=metadata,  # type: ignore[arg-type]
            )
            return (result.etag or "").strip('"')

        return await asyncio.to_thread(_fput)

    async def get_object_bytes(
        self,
        key: str,
        *,
        bucket: str | None = None,
    ) -> bytes:
        """Download an object as bytes.

        Suitable for small objects. For large payloads prefer
        :meth:`stream_object` to avoid loading everything in memory.

        Args:
            key (str): Object key.
            bucket (str | None): Override source bucket.

        Returns:
            bytes: Object payload.

        Raises:
            S3Error: When the object is missing or the request
                fails.
        """
        target = self._bucket(bucket)

        def _get() -> bytes:
            response = self.client.get_object(target, key)
            try:
                return response.read()
            finally:
                response.close()
                response.release_conn()

        return await asyncio.to_thread(_get)

    async def fget_object(
        self,
        key: str,
        file_path: str | Path,
        *,
        bucket: str | None = None,
    ) -> Path:
        """Download an object straight to disk.

        Args:
            key (str): Object key.
            file_path (str | Path): Destination path. Parent
                directories are created if missing.
            bucket (str | None): Override source bucket.

        Returns:
            Path: The path the object was written to.

        Raises:
            S3Error: When the object is missing or the request
                fails.
        """
        target = self._bucket(bucket)
        path = Path(file_path)
        path.parent.mkdir(parents=True, exist_ok=True)
        await asyncio.to_thread(
            self.client.fget_object,
            target,
            key,
            str(path),
        )
        return path

    async def stream_object(
        self,
        key: str,
        *,
        bucket: str | None = None,
        chunk_size: int = 64 * 1024,
        offset: int = 0,
        length: int | None = None,
    ) -> AsyncIterator[bytes]:
        """Stream an object (or one byte range of it) in fixed-size chunks.

        The whole network read still runs in a worker thread —
        each ``chunk_size`` read is one ``asyncio.to_thread``
        round-trip — but the event loop yields between chunks so
        other requests progress.

        ``offset`` and ``length`` are forwarded to ``Minio.get_object``,
        which turns them into a ``Range`` header on the request to the
        store, so only that slice crosses the network — what a ``206``
        response needs. A whole-object read (the defaults) still calls
        ``get_object(bucket, key)`` without them, so a test double written
        against the older two-argument call keeps working.

        Args:
            key (str): Object key.
            bucket (str | None): Override source bucket.
            chunk_size (int): Bytes per chunk. Default 64 KiB.
            offset (int): First byte to read. Default ``0``.
            length (int | None): How many bytes to read from ``offset``.
                ``None`` (default) reads to the end of the object.

        Returns:
            AsyncIterator[bytes]: Async generator yielding chunks
            until the stream ends.

        Raises:
            ValueError: When ``offset`` is negative or ``length`` is not
                positive.
            S3Error: When the object is missing or the request
                fails.
        """
        if offset < 0:
            raise ValueError("offset must be zero or positive")
        if length is not None and length < 1:
            raise ValueError("length must be at least 1; pass None to read to the end")
        target = self._bucket(bucket)
        if offset == 0 and length is None:
            response = await asyncio.to_thread(self.client.get_object, target, key)
        else:
            response = await asyncio.to_thread(
                self.client.get_object,
                target,
                key,
                offset=offset,
                length=length or 0,
            )

        async def _iter() -> AsyncIterator[bytes]:
            try:
                while True:
                    chunk = await asyncio.to_thread(response.read, chunk_size)
                    if not chunk:
                        return
                    yield chunk
            finally:
                await asyncio.to_thread(response.close)
                await asyncio.to_thread(response.release_conn)

        return _iter()

    async def download_response(
        self,
        key: str,
        *,
        request: Request | None = None,
        bucket: str | None = None,
        filename: str | None = None,
        media_type: str | None = None,
        as_attachment: bool = True,
        chunk_size: int = 64 * 1024,
        cache_control: str | None = None,
        headers: dict[str, str] | None = None,
    ) -> StreamingResponse:
        """Stream an object straight to the client as a download response.

        Reads the object's metadata (for the content type, length and
        validators) and streams its bytes **through the app** — the file
        never lands on the app's disk nor loads fully into memory. Reach for
        this when the download must be auth-gated or the MinIO endpoint is
        not publicly reachable; prefer :meth:`presigned_get_url` to offload
        the transfer to MinIO directly when the client can hit it.

        Every response carries ``Accept-Ranges: bytes`` plus the object's
        ``ETag`` (quoted) and ``Last-Modified``, and the download security
        headers of
        :data:`~tempest_fastapi_sdk.api.static.DEFAULT_STATIC_SECURITY_HEADERS`,
        since the bytes reach the client on the API's origin with the type
        stored at upload. Pass ``request`` to let the client use the
        validators:

        * ``If-None-Match`` matching the ETag — or, when no
          ``If-None-Match`` is sent, ``If-Modified-Since`` not older than
          the object — answers ``304`` with no body, without reading the
          object.
        * ``Range: bytes=a-b``, ``bytes=a-`` or ``bytes=-n`` answers ``206``
          with ``Content-Range``, reading **only** that slice from the store
          (``offset``/``length`` on ``get_object``). This is what lets a
          ``<video>`` seek and a download manager resume.
        * A range starting at or past the end answers ``416`` with
          ``Content-Range: bytes */<size>``.
        * Several ranges (``bytes=0-1,5-6``), an unparseable ``Range``, or an
          ``If-Range`` that no longer matches the object serve the whole
          object with ``200`` — RFC 9110 allows ignoring ``Range``, and it
          avoids a ``multipart/byteranges`` body.

        Without ``request`` no request header is read and the response is
        always ``200`` with the whole object.

        Args:
            key (str): Object key.
            request (Request | None): The incoming request, whose ``Range``,
                ``If-Range``, ``If-None-Match`` and ``If-Modified-Since``
                headers are honoured. ``None`` always serves the whole
                object.
            bucket (str | None): Override source bucket.
            filename (str | None): Name presented to the client. Defaults
                to the object key's basename.
            media_type (str | None): Content type. Defaults to the object's
                stored content type, then a guess from ``filename``, then
                ``application/octet-stream``.
            as_attachment (bool): ``True`` forces a download; ``False`` asks
                for ``inline`` (e.g. view a PDF in-browser), granted only
                when the resolved type is in
                :data:`~tempest_fastapi_sdk.utils.download.INLINE_SAFE_MEDIA_TYPES`.
                Default ``True``.
            chunk_size (int): Bytes per streamed chunk. Default 64 KiB.
            cache_control (str | None): ``Cache-Control`` value, for example
                ``"private, max-age=3600"``. ``None`` sends none.
            headers (dict[str, str] | None): Extra response headers. One
                named like a security default replaces that default.

        Returns:
            StreamingResponse: Response ready to return from a router —
            ``200``, ``206``, ``304`` or ``416``; the last two carry an empty
            body.

        Raises:
            S3Error: When the object is missing or the request fails.
        """
        from starlette.responses import StreamingResponse

        from tempest_fastapi_sdk.utils._http_cache import (
            ByteRange,
            RangeNotSatisfiableError,
            format_http_date,
            if_range_allows,
            is_not_modified,
            resolve_byte_range,
        )
        from tempest_fastapi_sdk.utils.download import (
            _with_security_headers,
            build_content_disposition,
        )
        from tempest_fastapi_sdk.utils.media_types import guess_media_type

        stat = await self.stat_object(key, bucket=bucket)
        download_name = filename or key.rsplit("/", 1)[-1]
        resolved_media_type = (
            media_type
            or stat.content_type
            or guess_media_type(download_name)
            or "application/octet-stream"
        )
        etag = f'"{stat.etag}"' if stat.etag else None
        response_headers: dict[str, str] = _with_security_headers(headers)
        response_headers["accept-ranges"] = "bytes"
        if etag is not None:
            response_headers["etag"] = etag
        if stat.last_modified is not None:
            response_headers["last-modified"] = format_http_date(stat.last_modified)
        if cache_control is not None:
            response_headers["cache-control"] = cache_control

        byte_range: ByteRange | None = None
        if request is not None:
            if is_not_modified(
                if_none_match=request.headers.get("if-none-match"),
                if_modified_since=request.headers.get("if-modified-since"),
                etag=etag,
                last_modified=stat.last_modified,
            ):
                return StreamingResponse(
                    content=iter(()), status_code=304, headers=response_headers
                )
            if if_range_allows(
                request.headers.get("if-range"),
                etag=etag,
                last_modified=stat.last_modified,
            ):
                try:
                    byte_range = resolve_byte_range(
                        request.headers.get("range"), stat.size
                    )
                except RangeNotSatisfiableError as exc:
                    response_headers["content-range"] = exc.content_range()
                    return StreamingResponse(
                        content=iter(()), status_code=416, headers=response_headers
                    )

        response_headers["content-disposition"] = build_content_disposition(
            download_name, as_attachment=as_attachment, media_type=resolved_media_type
        )
        if byte_range is not None:
            response_headers["content-range"] = byte_range.content_range()
            response_headers["content-length"] = str(byte_range.length)
            partial = await self.stream_object(
                key,
                bucket=bucket,
                chunk_size=chunk_size,
                offset=byte_range.start,
                length=byte_range.length,
            )
            return StreamingResponse(
                content=partial,
                status_code=206,
                media_type=resolved_media_type,
                headers=response_headers,
            )
        if stat.size:
            response_headers["content-length"] = str(stat.size)
        body = await self.stream_object(key, bucket=bucket, chunk_size=chunk_size)
        return StreamingResponse(
            content=body,
            media_type=resolved_media_type,
            headers=response_headers,
        )

    async def accel_redirect_response(
        self,
        key: str,
        *,
        bucket: str | None = None,
        internal_prefix: str | None = None,
        expires: timedelta = timedelta(minutes=5),
        filename: str | None = None,
        media_type: str | None = None,
        as_attachment: bool = True,
        cache_control: str | None = None,
        headers: dict[str, str] | None = None,
    ) -> Response:
        """Hand the transfer to nginx with an ``X-Accel-Redirect`` header.

        The route keeps the authorisation decision and nginx moves the
        bytes: the response is empty and carries
        ``X-Accel-Redirect: <internal_prefix><bucket>/<key>?X-Amz-...``, a
        URL presigned against the **internal** ``endpoint`` — never
        ``public_endpoint`` — so the bucket stays private and the client
        only ever sees the backend's domain. nginx follows the header into
        an ``internal`` location that proxies to the bucket; the client's
        ``Range`` and ``If-None-Match`` travel with that request, so ``206``
        and ``304`` come from the store itself.

        SigV4 signs the ``Host`` header and the path, so the internal
        location must strip the prefix (``proxy_pass`` with a trailing
        ``/``) and send ``Host`` equal to ``endpoint``; otherwise the store
        answers ``403 SignatureDoesNotMatch``. The storage recipe carries
        the full nginx block.

        ``Content-Disposition``, ``Content-Type`` and ``Cache-Control`` are
        not set on this response but signed into the URL as the S3
        ``response-*`` overrides, so the store returns them with the bytes.
        Measured on nginx 1.27.5: the upstream's ``Content-Type`` wins over
        the one the app sets, and a ``Content-Disposition`` set on both
        reaches the client twice.

        The download security headers
        (:data:`~tempest_fastapi_sdk.api.static.DEFAULT_STATIC_SECURITY_HEADERS`)
        are **not** set here: S3 has no ``response-*`` override for them, and
        nginx answers with the store's response — measured on nginx 1.22.1,
        1.27.5 and 1.29.8, a ``Content-Security-Policy`` the app sets on the
        empty response does not reach the client. They belong in the
        internal location, as ``add_header ... always``; the storage
        recipe's nginx block carries them.

        No ``stat`` is made: a missing object surfaces as the store's
        ``404`` through nginx instead of an ``S3Error`` here.

        Args:
            key (str): Object key.
            bucket (str | None): Override source bucket.
            internal_prefix (str | None): The nginx ``internal`` location
                the header points into, starting with ``/``. ``None`` uses
                ``accel_prefix`` from the constructor (``"/_bucket/"`` by
                default).
            expires (timedelta): Lifetime of the presigned URL. nginx uses
                it at once, so it only has to outlive the hop. Default five
                minutes.
            filename (str | None): Name presented to the client. Defaults to
                the object key's basename.
            media_type (str | None): Content type the store should answer
                with. ``None`` keeps the type stored with the object.
            as_attachment (bool): ``True`` (default) forces a download.
                ``False`` asks for ``inline`` — what ``<video>`` and
                ``<img>`` want — and is granted only when ``media_type`` is
                passed and is in
                :data:`~tempest_fastapi_sdk.utils.download.INLINE_SAFE_MEDIA_TYPES`.
                No ``stat`` is made, so with ``media_type=None`` the type the
                store will answer with is unknown here and the object is
                served as ``attachment``; a type passed in is signed into the
                URL, so it is also the type the client receives.
            cache_control (str | None): ``Cache-Control`` the store should
                answer with. ``None`` sends none.
            headers (dict[str, str] | None): Extra headers on the app's
                response.

        Returns:
            Response: An empty ``200`` carrying ``X-Accel-Redirect``, for
            nginx to replace with the object.

        Raises:
            ValueError: When ``internal_prefix`` does not start with ``/``.
        """
        from urllib.parse import urlsplit

        from starlette.responses import Response

        from tempest_fastapi_sdk.utils.download import build_content_disposition

        prefix = (
            self.accel_prefix
            if internal_prefix is None
            else self._check_accel_prefix(internal_prefix)
        )
        download_name = filename or key.rsplit("/", 1)[-1]
        overrides: dict[str, str | list[str] | tuple[str]] = {
            "response-content-disposition": build_content_disposition(
                download_name, as_attachment=as_attachment, media_type=media_type
            ),
        }
        if media_type is not None:
            overrides["response-content-type"] = media_type
        if cache_control is not None:
            overrides["response-cache-control"] = cache_control
        target = self._bucket(bucket)
        url = await asyncio.to_thread(
            self.client.presigned_get_object,
            target,
            key,
            expires,
            response_headers=overrides,
        )
        signed = urlsplit(url)
        response_headers: dict[str, str] = dict(headers or {})
        response_headers["x-accel-redirect"] = (
            f"{prefix.rstrip('/')}{signed.path}?{signed.query}"
        )
        return Response(status_code=200, headers=response_headers)

    async def serve_object(
        self,
        key: str,
        *,
        request: Request | None = None,
        bucket: str | None = None,
        filename: str | None = None,
        media_type: str | None = None,
        as_attachment: bool = True,
        cache_control: str | None = None,
        headers: dict[str, str] | None = None,
    ) -> Response:
        """Serve an object the way the constructor's ``accel_redirect`` says.

        One route, two delivery modes chosen by configuration
        (``STORAGE_ACCEL_REDIRECT`` via :meth:`MinIOSettings.minio_kwargs`):
        ``accel_redirect=False`` proxies the bytes through the app with
        :meth:`download_response`; ``True`` answers with
        :meth:`accel_redirect_response` into ``accel_prefix``. Both honour
        ``Range`` and revalidation — the first in the app, the second in the
        store behind nginx.

        Args:
            key (str): Object key.
            request (Request | None): The incoming request. Proxy mode needs
                it for ``206``/``304``/``416``; redirect mode ignores it,
                because nginx forwards the client's headers itself.
            bucket (str | None): Override source bucket.
            filename (str | None): Name presented to the client. Defaults to
                the object key's basename.
            media_type (str | None): Content type. ``None`` uses the type
                stored with the object.
            as_attachment (bool): ``True`` (default) forces a download;
                ``False`` asks for ``inline``, granted only to a type in
                :data:`~tempest_fastapi_sdk.utils.download.INLINE_SAFE_MEDIA_TYPES`.
                Proxy mode checks the type stored with the object; redirect
                mode makes no ``stat`` and checks only ``media_type``, so
                pass it there to serve inline.
            cache_control (str | None): ``Cache-Control`` value. ``None``
                sends none.
            headers (dict[str, str] | None): Extra headers on the app's
                response. In proxy mode, one named like a security default
                replaces that default.

        Returns:
            Response: A :class:`StreamingResponse` (proxy mode) or an empty
            ``X-Accel-Redirect`` response (redirect mode).

        Raises:
            S3Error: Proxy mode, when the object is missing or the request
                fails.
        """
        if self.accel_redirect:
            return await self.accel_redirect_response(
                key,
                bucket=bucket,
                filename=filename,
                media_type=media_type,
                as_attachment=as_attachment,
                cache_control=cache_control,
                headers=headers,
            )
        return await self.download_response(
            key,
            request=request,
            bucket=bucket,
            filename=filename,
            media_type=media_type,
            as_attachment=as_attachment,
            cache_control=cache_control,
            headers=headers,
        )

    async def stat_object(
        self,
        key: str,
        *,
        bucket: str | None = None,
    ) -> ObjectStat:
        """Fetch metadata for an object without downloading it.

        Args:
            key (str): Object key.
            bucket (str | None): Override source bucket.

        Returns:
            ObjectStat: Subset of fields commonly needed by callers.
            Use ``.raw`` for the full ``minio`` ``Object``.

        Raises:
            S3Error: When the object is missing.
        """
        target = self._bucket(bucket)
        raw = await asyncio.to_thread(self.client.stat_object, target, key)
        metadata: dict[str, str] = {
            k.removeprefix("x-amz-meta-"): v
            for k, v in (raw.metadata or {}).items()
            if k.lower().startswith("x-amz-meta-")
        }
        return ObjectStat(
            bucket=target,
            key=key,
            size=int(raw.size or 0),
            etag=(raw.etag or "").strip('"') or None,
            content_type=raw.content_type,
            last_modified=raw.last_modified,
            metadata=metadata,
            raw=raw,
        )

    async def list_objects(
        self,
        prefix: str = "",
        *,
        bucket: str | None = None,
        recursive: bool = True,
    ) -> list[str]:
        """List object keys under a prefix.

        Args:
            prefix (str): Prefix filter. Empty string returns
                everything.
            bucket (str | None): Override source bucket.
            recursive (bool): Walk into pseudo-directories.
                ``False`` returns only the immediate level.

        Returns:
            list[str]: Object keys. Empty list when no matches —
            matching the SDK convention of "no rows is not an
            error".
        """
        target = self._bucket(bucket)

        def _list() -> list[str]:
            return [
                obj.object_name or ""
                for obj in self.client.list_objects(
                    target,
                    prefix=prefix,
                    recursive=recursive,
                )
            ]

        return await asyncio.to_thread(_list)

    async def remove_object(
        self,
        key: str,
        *,
        bucket: str | None = None,
        version_id: str | None = None,
    ) -> None:
        """Delete an object (or a specific version).

        Args:
            key (str): Object key.
            bucket (str | None): Override target bucket.
            version_id (str | None): When the bucket has
                versioning enabled, the specific version to delete.

        Raises:
            S3Error: When the delete fails for a reason other than
                "already gone" (deletes are idempotent on S3).
        """
        target = self._bucket(bucket)
        await asyncio.to_thread(
            self.client.remove_object,
            target,
            key,
            version_id=version_id,
        )

    async def remove_objects(
        self,
        keys: Iterable[str],
        *,
        bucket: str | None = None,
    ) -> list[ObjectDeleteError]:
        """Delete many objects with S3 batch deletes.

        Uses ``Minio.remove_objects``, which sends one ``DeleteObjects``
        request per 1000 keys instead of one ``DELETE`` per key. Duplicate
        keys are collapsed. Keys that do not exist count as deleted (S3
        deletes are idempotent), so they never appear in the result.

        Args:
            keys (Iterable[str]): Object keys to delete.
            bucket (str | None): Override target bucket.

        Returns:
            list[ObjectDeleteError]: The keys the store refused to delete,
            with the reported error. Empty list when every key was deleted.

        Raises:
            S3Error: When a whole batch request fails (auth, network,
                bucket missing).
        """
        from minio.deleteobjects import DeleteObject

        target = self._bucket(bucket)
        unique = list(dict.fromkeys(keys))
        if not unique:
            return []

        def _remove() -> list[ObjectDeleteError]:
            return [
                ObjectDeleteError(
                    key=error.name or "",
                    code=error.code or "",
                    message=error.message or "",
                )
                for error in self.client.remove_objects(
                    target,
                    [DeleteObject(key) for key in unique],
                )
            ]

        return await asyncio.to_thread(_remove)

    async def copy_object(
        self,
        source_key: str,
        dest_key: str,
        *,
        source_bucket: str | None = None,
        dest_bucket: str | None = None,
    ) -> str:
        """Copy an object inside the same store.

        Args:
            source_key (str): Source object key.
            dest_key (str): Destination object key.
            source_bucket (str | None): Source bucket; defaults to
                ``default_bucket``.
            dest_bucket (str | None): Destination bucket; defaults
                to ``default_bucket``.

        Returns:
            str: ETag of the copied object (quotes stripped).

        Raises:
            S3Error: When the source is missing or the copy fails.
        """
        from minio.commonconfig import CopySource

        src_bucket = source_bucket or self.default_bucket
        dst_bucket = dest_bucket or self.default_bucket

        def _copy() -> str:
            result = self.client.copy_object(
                dst_bucket,
                dest_key,
                CopySource(src_bucket, source_key),
            )
            return (result.etag or "").strip('"')

        return await asyncio.to_thread(_copy)

    # ------------------------------------------------------------------
    # Presigned URLs
    # ------------------------------------------------------------------

    async def presigned_get_url(
        self,
        key: str,
        *,
        bucket: str | None = None,
        expires: timedelta = timedelta(hours=1),
    ) -> str:
        """Generate a temporary download URL.

        Args:
            key (str): Object key.
            bucket (str | None): Override source bucket.
            expires (timedelta): URL lifetime. Maximum is 7 days
                (S3 hard limit).

        Returns:
            str: Pre-signed HTTPS URL that anyone with the link
            can ``GET`` until expiry.
        """
        target = self._bucket(bucket)
        return await asyncio.to_thread(
            self._presign_client.presigned_get_object,
            target,
            key,
            expires,
        )

    async def presigned_put_url(
        self,
        key: str,
        *,
        bucket: str | None = None,
        expires: timedelta = timedelta(minutes=15),
    ) -> str:
        """Generate a temporary upload URL.

        Lets the browser ``PUT`` directly to MinIO/S3 without the
        bytes touching the FastAPI process — ideal for large
        files.

        Args:
            key (str): Destination object key.
            bucket (str | None): Override target bucket.
            expires (timedelta): URL lifetime. Maximum is 7 days.

        Returns:
            str: Pre-signed HTTPS URL accepting a ``PUT`` with the
            object body until expiry.
        """
        target = self._bucket(bucket)
        return await asyncio.to_thread(
            self._presign_client.presigned_put_object,
            target,
            key,
            expires,
        )

    async def _gather_bounded(
        self,
        awaitables: Sequence[Awaitable[_T]],
        max_concurrency: int,
    ) -> list[_T]:
        """Await ``awaitables`` concurrently under an in-flight ceiling.

        Every object operation is dispatched to a worker thread; scheduling
        thousands at once would saturate the default executor and spike
        memory. A semaphore bounds how many run at the same time while
        preserving result order. Semantics are fail-fast — the first
        exception cancels the gather and propagates, matching the behaviour
        of awaiting the operations one by one.

        Args:
            awaitables (Sequence[Awaitable[_T]]): The operations to run.
            max_concurrency (int): Maximum awaited at once; at least 1.

        Returns:
            list[_T]: Results in the same order as ``awaitables``.

        Raises:
            ValueError: If ``max_concurrency`` is less than 1.
        """
        if max_concurrency < 1:
            raise ValueError("max_concurrency must be at least 1")
        semaphore = asyncio.Semaphore(max_concurrency)

        async def _run(awaitable: Awaitable[_T]) -> _T:
            async with semaphore:
                return await awaitable

        return await asyncio.gather(*(_run(a) for a in awaitables))

    async def presigned_get_urls(
        self,
        keys: Iterable[str],
        *,
        bucket: str | None = None,
        expires: timedelta = timedelta(hours=1),
        max_concurrency: int = 16,
    ) -> dict[str, str]:
        """Generate presigned download URLs for many keys concurrently.

        Duplicate keys are collapsed, so each object is signed once and
        appears a single time in the result. Signing is fail-fast: the
        first failure aborts the batch and propagates.

        Args:
            keys (Iterable[str]): Object keys to sign.
            bucket (str | None): Override source bucket for every key.
            expires (timedelta): URL lifetime for every key. Maximum is
                7 days (S3 hard limit).
            max_concurrency (int): Maximum signings awaited at once.

        Returns:
            dict[str, str]: Mapping of each unique key to its presigned
            GET URL.

        Raises:
            ValueError: If ``max_concurrency`` is less than 1.
            S3Error: When signing a key fails.
        """
        unique = list(dict.fromkeys(keys))
        urls = await self._gather_bounded(
            [
                self.presigned_get_url(key, bucket=bucket, expires=expires)
                for key in unique
            ],
            max_concurrency,
        )
        return dict(zip(unique, urls, strict=True))

    async def put_objects(
        self,
        items: Iterable[PutObjectItem],
        *,
        bucket: str | None = None,
        max_concurrency: int = 16,
    ) -> dict[str, str]:
        """Upload many objects concurrently.

        Uploads are fail-fast: the first failure aborts the batch and
        propagates. When two items share a key the later one wins in the
        returned mapping, matching last-write-wins on the store.

        Args:
            items (Iterable[PutObjectItem]): The objects to upload, each
                carrying its key, payload and per-object options.
            bucket (str | None): Override target bucket for every item.
            max_concurrency (int): Maximum uploads awaited at once.

        Returns:
            dict[str, str]: Mapping of each item's key to the uploaded
            object's ETag (quotes stripped).

        Raises:
            ValueError: If ``max_concurrency`` is less than 1, or an item
                with file-like data omits ``length``.
            S3Error: When an upload fails.
        """
        batch = list(items)
        etags = await self._gather_bounded(
            [
                self.put_object(
                    item.key,
                    item.data,
                    bucket=bucket,
                    content_type=item.content_type,
                    metadata=item.metadata,
                    length=item.length,
                    part_size=item.part_size,
                )
                for item in batch
            ],
            max_concurrency,
        )
        return dict(zip((item.key for item in batch), etags, strict=True))

    async def get_objects_bytes(
        self,
        keys: Iterable[str],
        *,
        bucket: str | None = None,
        max_concurrency: int = 16,
    ) -> dict[str, bytes]:
        """Download many objects as bytes concurrently.

        Duplicate keys are collapsed, so each object is fetched once.
        Downloads are fail-fast: the first failure aborts the batch and
        propagates. Suitable for small objects — stream large payloads
        individually via :meth:`stream_object`.

        Args:
            keys (Iterable[str]): Object keys to download.
            bucket (str | None): Override source bucket for every key.
            max_concurrency (int): Maximum downloads awaited at once.

        Returns:
            dict[str, bytes]: Mapping of each unique key to its payload.

        Raises:
            ValueError: If ``max_concurrency`` is less than 1.
            S3Error: When a download fails.
        """
        unique = list(dict.fromkeys(keys))
        blobs = await self._gather_bounded(
            [self.get_object_bytes(key, bucket=bucket) for key in unique],
            max_concurrency,
        )
        return dict(zip(unique, blobs, strict=True))


__all__: list[str] = [
    "AsyncMinIOClient",
    "ObjectDeleteError",
    "ObjectStat",
    "PutObjectItem",
]
