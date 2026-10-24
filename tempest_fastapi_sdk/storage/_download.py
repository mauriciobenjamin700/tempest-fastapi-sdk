"""Stream a stored object as an HTTP download, for any object store.

:meth:`AsyncMinIOClient.download_response
<tempest_fastapi_sdk.storage.AsyncMinIOClient.download_response>` and
:meth:`LocalObjectStorageClient.download_response
<tempest_fastapi_sdk.storage.LocalObjectStorageClient.download_response>`
answer the same request the same way — validators, ``Range``, security
headers — because both delegate here. The only thing the response needs from
the store is a ``stat_object`` and a ranged ``stream_object``.
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from typing import TYPE_CHECKING, Protocol

if TYPE_CHECKING:
    from starlette.requests import Request
    from starlette.responses import StreamingResponse

    from tempest_fastapi_sdk.storage.minio_client import ObjectStat


class DownloadableStore(Protocol):
    """The two reads :func:`object_download_response` makes on a store."""

    async def stat_object(
        self,
        key: str,
        *,
        bucket: str | None = None,
    ) -> ObjectStat:
        """Fetch an object's metadata.

        Args:
            key (str): Object key.
            bucket (str | None): Override source bucket.

        Returns:
            ObjectStat: Size, content type, ETag and metadata.
        """
        ...

    async def stream_object(
        self,
        key: str,
        *,
        bucket: str | None = None,
        chunk_size: int = 64 * 1024,
        offset: int = 0,
        length: int | None = None,
    ) -> AsyncIterator[bytes]:
        """Open an object, or one byte range of it, as a chunk stream.

        Args:
            key (str): Object key.
            bucket (str | None): Override source bucket.
            chunk_size (int): Bytes per chunk.
            offset (int): First byte to read.
            length (int | None): Bytes to read from ``offset``; ``None``
                reads to the end.

        Returns:
            AsyncIterator[bytes]: The chunks, in order.
        """
        ...


async def object_download_response(
    store: DownloadableStore,
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
    """Stream ``key`` from ``store`` as a download response.

    The behaviour (validators, ``Range``, ``304``/``206``/``416``, security
    headers, content disposition) is documented on
    :meth:`AsyncMinIOClient.download_response
    <tempest_fastapi_sdk.storage.AsyncMinIOClient.download_response>`, which
    delegates here.

    Args:
        store (DownloadableStore): Where the object lives.
        key (str): Object key.
        request (Request | None): The incoming request, whose ``Range``,
            ``If-Range``, ``If-None-Match`` and ``If-Modified-Since`` headers
            are honoured. ``None`` always serves the whole object.
        bucket (str | None): Override source bucket.
        filename (str | None): Name presented to the client. Defaults to the
            key's basename.
        media_type (str | None): Content type. Defaults to the stored one,
            then a guess from ``filename``, then ``application/octet-stream``.
        as_attachment (bool): ``True`` forces a download; ``False`` asks for
            ``inline``, granted only for inline-safe types.
        chunk_size (int): Bytes per streamed chunk.
        cache_control (str | None): ``Cache-Control`` value, or ``None``.
        headers (dict[str, str] | None): Extra response headers.

    Returns:
        StreamingResponse: ``200``, ``206``, ``304`` or ``416``.
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

    stat = await store.stat_object(key, bucket=bucket)
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
                byte_range = resolve_byte_range(request.headers.get("range"), stat.size)
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
        partial = await store.stream_object(
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
    body = await store.stream_object(key, bucket=bucket, chunk_size=chunk_size)
    return StreamingResponse(
        content=body,
        media_type=resolved_media_type,
        headers=response_headers,
    )


__all__: list[str] = ["DownloadableStore", "object_download_response"]
