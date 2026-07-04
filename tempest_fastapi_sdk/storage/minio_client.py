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
import mimetypes
from collections.abc import AsyncIterator
from dataclasses import dataclass
from datetime import datetime, timedelta
from io import BytesIO
from pathlib import Path
from types import TracebackType
from typing import TYPE_CHECKING, BinaryIO

if TYPE_CHECKING:
    from minio import Minio
    from minio.datatypes import Object as _MinioObject
    from starlette.responses import StreamingResponse


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

        Raises:
            ImportError: When the ``minio`` package is not
                installed. Install the ``[minio]`` extra:
                ``pip install tempest-fastapi-sdk[minio]``.
        """
        try:
            from minio import Minio
        except ImportError as exc:  # pragma: no cover - exercised via extras
            raise ImportError(
                "AsyncMinIOClient requires the 'minio' package. "
                "Install with: pip install tempest-fastapi-sdk[minio]"
            ) from exc

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

        # Split-endpoint: a second client, same credentials, whose only job
        # is to sign presigned URLs against the public host. SigV4 signs the
        # Host header, so the URL must be *signed* with the public endpoint —
        # rewriting the host afterwards would invalidate the signature.
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
    ) -> AsyncIterator[bytes]:
        """Stream an object in fixed-size chunks.

        The whole network read still runs in a worker thread —
        each ``chunk_size`` read is one ``asyncio.to_thread``
        round-trip — but the event loop yields between chunks so
        other requests progress.

        Args:
            key (str): Object key.
            bucket (str | None): Override source bucket.
            chunk_size (int): Bytes per chunk. Default 64 KiB.

        Returns:
            AsyncIterator[bytes]: Async generator yielding chunks
            until the stream ends.

        Raises:
            S3Error: When the object is missing or the request
                fails.
        """
        target = self._bucket(bucket)
        response = await asyncio.to_thread(self.client.get_object, target, key)

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
        bucket: str | None = None,
        filename: str | None = None,
        media_type: str | None = None,
        as_attachment: bool = True,
        chunk_size: int = 64 * 1024,
        headers: dict[str, str] | None = None,
    ) -> StreamingResponse:
        """Stream an object straight to the client as a download response.

        Reads the object's metadata (for the content type + length) and
        streams its bytes **through the app** — the file never lands on the
        app's disk nor loads fully into memory. Reach for this when the
        download must be auth-gated or the MinIO endpoint is not publicly
        reachable; prefer :meth:`presigned_get_url` to offload the transfer
        to MinIO directly when the client can hit it.

        Args:
            key (str): Object key.
            bucket (str | None): Override source bucket.
            filename (str | None): Name presented to the client. Defaults
                to the object key's basename.
            media_type (str | None): Content type. Defaults to the object's
                stored content type, then a guess from ``filename``, then
                ``application/octet-stream``.
            as_attachment (bool): ``True`` forces a download; ``False``
                serves inline (e.g. view a PDF in-browser). Default ``True``.
            chunk_size (int): Bytes per streamed chunk. Default 64 KiB.
            headers (dict[str, str] | None): Extra response headers.

        Returns:
            StreamingResponse: Response ready to return from a router.

        Raises:
            S3Error: When the object is missing or the request fails.
        """
        from starlette.responses import StreamingResponse

        from tempest_fastapi_sdk.utils.download import build_content_disposition

        stat = await self.stat_object(key, bucket=bucket)
        download_name = filename or key.rsplit("/", 1)[-1]
        resolved_media_type = (
            media_type
            or stat.content_type
            or mimetypes.guess_type(download_name)[0]
            or "application/octet-stream"
        )
        response_headers: dict[str, str] = dict(headers or {})
        response_headers["content-disposition"] = build_content_disposition(
            download_name, as_attachment=as_attachment
        )
        if stat.size:
            response_headers["content-length"] = str(stat.size)
        body = await self.stream_object(key, bucket=bucket, chunk_size=chunk_size)
        return StreamingResponse(
            content=body,
            media_type=resolved_media_type,
            headers=response_headers,
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


__all__: list[str] = [
    "AsyncMinIOClient",
    "ObjectStat",
]
