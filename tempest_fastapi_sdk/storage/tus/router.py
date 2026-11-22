"""The tus 1.0.0 server routes ``tempest-react-sdk``'s resumable upload speaks.

:func:`make_tus_router` mounts the subset of tus that
``createResumableUpload`` sends — core plus the *creation*, *termination*
and *expiration* extensions, with the creation ``Idempotency-Key`` it
attaches:

* ``POST {prefix}`` creates an upload from ``Upload-Length`` and
  ``Upload-Metadata`` and answers ``201`` with a relative ``Location``.
* ``HEAD {prefix}/{id}`` answers the stored ``Upload-Offset``.
* ``PATCH {prefix}/{id}`` writes a chunk at ``Upload-Offset``.
* ``DELETE {prefix}/{id}`` discards the upload.

Not implemented, because the client never sends them: ``OPTIONS``
discovery, ``Upload-Defer-Length``, *creation-with-upload* (a body on the
``POST``), *checksum*, *concatenation* and ``X-HTTP-Method-Override``.
"""

from __future__ import annotations

import asyncio
import hashlib
import secrets
import tempfile
from collections.abc import AsyncIterator, Awaitable, Callable
from contextlib import asynccontextmanager
from datetime import UTC, datetime, timedelta
from typing import Any, BinaryIO, Protocol, cast

from fastapi import APIRouter, Depends, Request
from starlette.responses import Response

from tempest_fastapi_sdk.exceptions.base import AppException
from tempest_fastapi_sdk.exceptions.tus import (
    TusContentTypeException,
    TusIdempotencyKeyReusedException,
    TusInvalidHeaderException,
    TusOffsetMismatchException,
    TusUploadExpiredException,
    TusUploadNotFoundException,
    TusUploadTooLargeException,
    TusVersionException,
)
from tempest_fastapi_sdk.storage.tus.models import (
    TUS_VERSION,
    TusUpload,
    encode_upload_metadata,
    http_date,
    parse_upload_metadata,
)
from tempest_fastapi_sdk.storage.tus.store import TusStore

DEFAULT_TUS_MAX_SIZE: int = 1024 * 1024 * 1024
"""Default largest ``Upload-Length`` the router accepts: 1 GiB."""

DEFAULT_TUS_MAX_CHUNK_SIZE: int = 64 * 1024 * 1024
"""Default largest ``PATCH`` body: 64 MiB, past the client's 5 MiB default."""

DEFAULT_TUS_EXPIRATION: timedelta = timedelta(hours=24)
"""Default time an incomplete upload stays resumable after its last write."""

TUS_CHUNK_CONTENT_TYPE: str = "application/offset+octet-stream"
"""The only ``Content-Type`` a ``PATCH`` may carry."""

TUS_CHUNK_SPOOL_BYTES: int = 1024 * 1024
"""Bytes of a ``PATCH`` body held in memory before spooling to disk."""

TUS_CORS_EXPOSE_HEADERS: tuple[str, ...] = (
    "Location",
    "Tus-Max-Size",
    "Tus-Resumable",
    "Tus-Version",
    "Upload-Expires",
    "Upload-Length",
    "Upload-Metadata",
    "Upload-Offset",
)
"""Response headers a cross-origin client must be allowed to read.

Pass to ``CORSMiddleware(expose_headers=...)``. Without ``Location`` and
``Upload-Offset`` there, a browser on another origin gets ``null`` from
``getResponseHeader`` and the client cannot find the upload it created.
"""

TUS_CORS_ALLOW_HEADERS: tuple[str, ...] = (
    "Authorization",
    "Content-Type",
    "Idempotency-Key",
    "Tus-Resumable",
    "Upload-Length",
    "Upload-Metadata",
    "Upload-Offset",
    "X-CSRF-Token",
)
"""Request headers ``createResumableUpload`` sends.

Pass to ``CORSMiddleware(allow_headers=...)``.
"""


class TusCompleteCallback(Protocol):
    """Called once an upload's last byte is stored and finalized.

    Receives the upload and the store, so it can read the file with
    :meth:`TusStore.stream <tempest_fastapi_sdk.storage.tus.TusStore.stream>`
    (or :meth:`LocalTusStore.data_path
    <tempest_fastapi_sdk.storage.tus.LocalTusStore.data_path>` /
    :meth:`S3TusStore.object_key
    <tempest_fastapi_sdk.storage.tus.S3TusStore.object_key>`) and register or
    move it. Runs inside the request that sent the last chunk, before the
    ``204``; whatever it returns is discarded.
    """

    def __call__(self, upload: TusUpload, store: TusStore, /) -> Awaitable[object]:
        """Handle a finished upload.

        Args:
            upload (TusUpload): The complete upload.
            store (TusStore): The store holding its bytes.

        Returns:
            Awaitable[object]: Awaited; the result is ignored.
        """
        ...


class _KeyedLocks:
    """One :class:`asyncio.Lock` per key, dropped when nobody holds or waits on it."""

    def __init__(self) -> None:
        """Initialize the empty lock table."""
        self._locks: dict[str, tuple[asyncio.Lock, int]] = {}

    @asynccontextmanager
    async def hold(self, key: str) -> AsyncIterator[None]:
        """Hold the lock of ``key`` for the ``async with`` body.

        Args:
            key (str): What to serialize on.

        Yields:
            None: While the lock is held.
        """
        lock, users = self._locks.get(key, (asyncio.Lock(), 0))
        self._locks[key] = (lock, users + 1)
        try:
            async with lock:
                yield
        finally:
            current_lock, current_users = self._locks[key]
            if current_users <= 1:
                del self._locks[key]
            else:
                self._locks[key] = (current_lock, current_users - 1)


def _tus_headers(extra: dict[str, str] | None = None) -> dict[str, str]:
    """Headers every tus response carries, plus ``extra``.

    Args:
        extra (dict[str, str] | None): More headers.

    Returns:
        dict[str, str]: ``Tus-Resumable`` and ``extra``.
    """
    headers: dict[str, str] = {"Tus-Resumable": TUS_VERSION}
    if extra:
        headers.update(extra)
    return headers


def _with_tus_headers(
    error: AppException, extra: dict[str, str] | None = None
) -> AppException:
    """Attach the tus headers to an exception about to be raised.

    Args:
        error (AppException): The exception.
        extra (dict[str, str] | None): More headers.

    Returns:
        AppException: The same exception, with its headers set.
    """
    error.headers = {**(error.headers or {}), **_tus_headers(extra)}
    return error


def _require_version(request: Request) -> None:
    """Refuse a request whose ``Tus-Resumable`` is not ``1.0.0``.

    Args:
        request (Request): The request.

    Raises:
        TusVersionException: With ``Tus-Version: 1.0.0``.
    """
    if request.headers.get("tus-resumable") != TUS_VERSION:
        raise _with_tus_headers(
            TusVersionException(details={"supported": [TUS_VERSION]}),
            {"Tus-Version": TUS_VERSION},
        )


def _non_negative_header(request: Request, name: str) -> int:
    """Read a header that must be a non-negative integer.

    Args:
        request (Request): The request.
        name (str): Header name.

    Returns:
        int: Its value.

    Raises:
        TusInvalidHeaderException: When absent or not made of digits only.
    """
    raw = request.headers.get(name)
    if raw is None or not (raw.isascii() and raw.isdigit()):
        raise _with_tus_headers(TusInvalidHeaderException(details={"header": name}))
    return int(raw)


def _owner_of(user: Any) -> str | None:
    """Turn what ``current_user_id`` resolved into the stored owner.

    Args:
        user (Any): The dependency's value.

    Returns:
        str | None: ``str(user)``, or ``None`` for an anonymous caller.
    """
    return None if user is None else str(user)


async def _anonymous() -> None:
    """Resolve no caller, the default when the router has no ``current_user_id``.

    Returns:
        None: Always.
    """
    return None


def make_tus_router(
    store: TusStore,
    *,
    prefix: str = "/api/uploads",
    max_size: int = DEFAULT_TUS_MAX_SIZE,
    max_chunk_size: int = DEFAULT_TUS_MAX_CHUNK_SIZE,
    expiration: timedelta | None = DEFAULT_TUS_EXPIRATION,
    current_user_id: Callable[..., Any] | None = None,
    on_complete: TusCompleteCallback | None = None,
    tags: list[str] | None = None,
    include_in_schema: bool = True,
) -> APIRouter:
    """Build the tus routes ``createResumableUpload`` talks to.

    Every response, errors included, carries ``Tus-Resumable: 1.0.0``. Errors
    use the SDK envelope (``detail``/``code``/``details``) through
    :class:`~tempest_fastapi_sdk.AppException`, so mount the app with
    :func:`~tempest_fastapi_sdk.register_exception_handlers` as usual.

    Checks, in the order a ``PATCH`` meets them: ``Tus-Resumable`` (``412``),
    ``Content-Type`` (``415``), ``Upload-Offset`` well-formed (``400``), the
    upload exists and belongs to the caller (``404`` for both), not expired
    (``410``), the offset matches (``409``), the body fits the declared length
    and ``max_chunk_size`` (``413``). The offset check runs again after the
    body is read, under a per-upload lock: of two concurrent ``PATCH`` at the
    same offset, one writes and the other answers ``409``.

    The lock lives in this process. Two replicas behind a load balancer can
    still race on one upload; route an upload's requests to one replica
    (sticky sessions on the upload path) when you run more than one.

    Args:
        store (TusStore): Where uploads live — :class:`LocalTusStore
            <tempest_fastapi_sdk.storage.tus.LocalTusStore>` or
            :class:`S3TusStore <tempest_fastapi_sdk.storage.tus.S3TusStore>`.
        prefix (str): The creation endpoint; uploads live under
            ``{prefix}/{id}``. Defaults to ``"/api/uploads"``, the path the
            ``tempest-react-sdk`` examples post to.
        max_size (int): Largest ``Upload-Length`` accepted; past it the
            creation answers ``413`` with ``Tus-Max-Size``.
        max_chunk_size (int): Largest ``PATCH`` body accepted.
        expiration (timedelta | None): How long an incomplete upload stays
            resumable after creation or its last accepted ``PATCH``, sent as
            ``Upload-Expires``. ``None`` never expires uploads. Expired ones
            answer ``410`` and are deleted by :meth:`TusStore.purge_expired
            <tempest_fastapi_sdk.storage.tus.TusStore.purge_expired>`.
        current_user_id (Callable[..., Any] | None): FastAPI dependency
            resolving the caller. Its ``str()`` is stored as the upload's
            owner, and only the same value can probe, continue or delete it;
            anyone else gets ``404``. ``None`` makes uploads anonymous: the
            unguessable URL is then the only capability.
        on_complete (TusCompleteCallback | None): Awaited with the upload and
            the store once the last byte is stored. If it raises, the request
            answers ``500``, the upload stays complete with ``notified``
            false, and the client — whose next ``HEAD`` sees every byte —
            does not retry; find those with :meth:`TusStore.list_uploads
            <tempest_fastapi_sdk.storage.tus.TusStore.list_uploads>`.
        tags (list[str] | None): OpenAPI tags. Defaults to ``["uploads"]``.
        include_in_schema (bool): Whether the routes appear in OpenAPI.

    Returns:
        APIRouter: Ready for ``app.include_router``.
    """
    router = APIRouter(prefix=prefix, tags=list(tags or ["uploads"]))
    locks = _KeyedLocks()
    owner_dependency: Callable[..., Any] = current_user_id or _anonymous

    def _expiry_from_now() -> datetime | None:
        """Compute a fresh ``expires_at``.

        Returns:
            datetime | None: Now plus ``expiration``, or ``None``.
        """
        return None if expiration is None else datetime.now(UTC) + expiration

    def _expiry_headers(upload: TusUpload) -> dict[str, str]:
        """``Upload-Expires`` for an incomplete upload that has one.

        Args:
            upload (TusUpload): The upload.

        Returns:
            dict[str, str]: The header, or nothing.
        """
        if upload.completed or upload.expires_at is None:
            return {}
        return {"Upload-Expires": http_date(upload.expires_at)}

    async def _owned(
        upload_id: str,
        owner: str | None,
        *,
        allow_expired: bool = False,
    ) -> TusUpload:
        """Load an upload the caller may act on.

        Args:
            upload_id (str): The id from the path.
            owner (str | None): The caller.
            allow_expired (bool): Return an expired upload instead of ``410``.

        Returns:
            TusUpload: The upload.

        Raises:
            TusUploadNotFoundException: When absent or someone else's.
            TusUploadExpiredException: When expired and not allowed.
        """
        upload = await store.load(upload_id)
        if upload is None or upload.owner != owner:
            raise _with_tus_headers(TusUploadNotFoundException())
        if not allow_expired and upload.is_expired():
            raise _with_tus_headers(TusUploadExpiredException())
        return upload

    async def _complete(upload: TusUpload) -> None:
        """Finalize a complete upload, save it, and run ``on_complete``.

        Args:
            upload (TusUpload): An upload whose last byte is stored.
        """
        await store.finalize(upload)
        upload.notified = on_complete is None
        await store.save(upload)
        if on_complete is not None:
            await on_complete(upload, store)
            upload.notified = True
            await store.save(upload)

    def _created(request: Request, upload: TusUpload) -> Response:
        """Answer a creation with the upload's relative URL.

        Args:
            request (Request): The creation request.
            upload (TusUpload): The upload.

        Returns:
            Response: ``201`` with ``Location``.
        """
        location = f"{request.url.path.rstrip('/')}/{upload.id}"
        return Response(
            status_code=201,
            headers=_tus_headers({"Location": location, **_expiry_headers(upload)}),
        )

    @router.post("", status_code=201, include_in_schema=include_in_schema)
    async def create_upload(
        request: Request,
        user: Any = Depends(owner_dependency),
    ) -> Response:
        """Create a tus upload (tus *creation*).

        Args:
            request (Request): Carries ``Upload-Length``, ``Upload-Metadata``
                and an optional ``Idempotency-Key``.
            user (Any): The caller, from ``current_user_id``.

        Returns:
            Response: ``201`` with ``Location``.

        Raises:
            TusUploadTooLargeException: When ``Upload-Length`` is over
                ``max_size``.
            TusIdempotencyKeyReusedException: When the key created an upload
                of another length.
        """
        _require_version(request)
        length = _non_negative_header(request, "upload-length")
        if length > max_size:
            raise _with_tus_headers(
                TusUploadTooLargeException(details={"max_bytes": max_size}),
                {"Tus-Max-Size": str(max_size)},
            )
        try:
            metadata = parse_upload_metadata(request.headers.get("upload-metadata"))
        except TusInvalidHeaderException as error:
            raise _with_tus_headers(error) from None
        owner = _owner_of(user)
        key = request.headers.get("idempotency-key")
        if not key:
            return _created(request, await _new_upload(length, metadata, owner, None))
        digest = hashlib.sha256(f"{owner or ''}\x00{key}".encode()).hexdigest()
        async with locks.hold(f"idempotency:{digest}"):
            existing_id = await store.find_idempotent(digest)
            existing = None if existing_id is None else await store.load(existing_id)
            if (
                existing is not None
                and existing.owner == owner
                and not existing.is_expired()
            ):
                if existing.length != length:
                    raise _with_tus_headers(TusIdempotencyKeyReusedException())
                return _created(request, existing)
            upload = await _new_upload(length, metadata, owner, digest)
            await store.remember_idempotent(digest, upload.id)
        return _created(request, upload)

    async def _new_upload(
        length: int,
        metadata: dict[str, str],
        owner: str | None,
        digest: str | None,
    ) -> TusUpload:
        """Persist a new upload, completing it at once when it is empty.

        Args:
            length (int): ``Upload-Length``.
            metadata (dict[str, str]): Decoded ``Upload-Metadata``.
            owner (str | None): The caller.
            digest (str | None): Idempotency digest, if any.

        Returns:
            TusUpload: The stored upload.
        """
        upload = TusUpload(
            id=secrets.token_urlsafe(24),
            length=length,
            metadata=metadata,
            owner=owner,
            expires_at=_expiry_from_now() if length > 0 else None,
            idempotency_digest=digest,
        )
        await store.create(upload)
        if upload.completed:
            await _complete(upload)
        return upload

    @router.head("/{upload_id}", include_in_schema=include_in_schema)
    async def upload_offset(
        upload_id: str,
        request: Request,
        user: Any = Depends(owner_dependency),
    ) -> Response:
        """Report how many bytes the server holds (tus core ``HEAD``).

        Args:
            upload_id (str): The upload id.
            request (Request): The request.
            user (Any): The caller.

        Returns:
            Response: ``200`` with ``Upload-Offset`` and ``Upload-Length``.
        """
        _require_version(request)
        upload = await _owned(upload_id, _owner_of(user))
        headers: dict[str, str] = {
            "Upload-Offset": str(upload.offset),
            "Upload-Length": str(upload.length),
            "Cache-Control": "no-store",
            **_expiry_headers(upload),
        }
        if upload.metadata:
            headers["Upload-Metadata"] = encode_upload_metadata(upload.metadata)
        return Response(status_code=200, headers=_tus_headers(headers))

    async def _read_chunk(request: Request, limit: int) -> tuple[BinaryIO, int]:
        """Read a ``PATCH`` body into a spooled file, refusing more than ``limit``.

        Args:
            request (Request): The request.
            limit (int): Most bytes accepted.

        Returns:
            tuple[BinaryIO, int]: The file, rewound, and its size.

        Raises:
            TusUploadTooLargeException: When the body passes ``limit``.
        """
        declared = request.headers.get("content-length")
        if declared is not None and declared.isdigit() and int(declared) > limit:
            raise _with_tus_headers(
                TusUploadTooLargeException(details={"max_bytes": limit})
            )
        spool = cast(
            BinaryIO,
            tempfile.SpooledTemporaryFile(max_size=TUS_CHUNK_SPOOL_BYTES),  # noqa: SIM115
        )
        size = 0
        try:
            async for block in request.stream():
                size += len(block)
                if size > limit:
                    raise _with_tus_headers(
                        TusUploadTooLargeException(details={"max_bytes": limit}),
                    )
                spool.write(block)
        except BaseException:
            spool.close()
            raise
        spool.seek(0)
        return spool, size

    @router.patch("/{upload_id}", status_code=204, include_in_schema=include_in_schema)
    async def write_chunk(
        upload_id: str,
        request: Request,
        user: Any = Depends(owner_dependency),
    ) -> Response:
        """Write one chunk at ``Upload-Offset`` (tus core ``PATCH``).

        Args:
            upload_id (str): The upload id.
            request (Request): Carries ``Upload-Offset`` and the chunk.
            user (Any): The caller.

        Returns:
            Response: ``204`` with the new ``Upload-Offset``.

        Raises:
            TusContentTypeException: When the body is not
                ``application/offset+octet-stream``.
            TusOffsetMismatchException: When ``Upload-Offset`` is not the
                stored offset.
            TusUploadTooLargeException: When the body passes the declared
                length or ``max_chunk_size``.
        """
        _require_version(request)
        content_type = (
            request.headers.get("content-type", "").split(";")[0].strip().lower()
        )
        if content_type != TUS_CHUNK_CONTENT_TYPE:
            raise _with_tus_headers(TusContentTypeException())
        offset = _non_negative_header(request, "upload-offset")
        owner = _owner_of(user)
        upload = await _owned(upload_id, owner)
        if offset != upload.offset:
            raise _with_tus_headers(
                TusOffsetMismatchException(details={"offset": upload.offset}),
            )
        limit = min(upload.length - upload.offset, max_chunk_size)
        spool, size = await _read_chunk(request, limit)
        try:
            async with locks.hold(upload_id):
                upload = await _owned(upload_id, owner)
                if offset != upload.offset:
                    raise _with_tus_headers(
                        TusOffsetMismatchException(details={"offset": upload.offset}),
                    )
                if upload.offset + size > upload.length:
                    raise _with_tus_headers(
                        TusUploadTooLargeException(
                            details={"max_bytes": upload.length - upload.offset},
                        ),
                    )
                await store.write(upload, spool, size)
                upload.offset += size
                if upload.completed:
                    await _complete(upload)
                else:
                    upload.expires_at = _expiry_from_now()
                    await store.save(upload)
        finally:
            spool.close()
        return Response(
            status_code=204,
            headers=_tus_headers(
                {"Upload-Offset": str(upload.offset), **_expiry_headers(upload)},
            ),
        )

    @router.delete("/{upload_id}", status_code=204, include_in_schema=include_in_schema)
    async def discard_upload(
        upload_id: str,
        request: Request,
        user: Any = Depends(owner_dependency),
    ) -> Response:
        """Discard an upload (tus *termination*).

        An expired upload can still be discarded.

        Args:
            upload_id (str): The upload id.
            request (Request): The request.
            user (Any): The caller.

        Returns:
            Response: ``204``.
        """
        _require_version(request)
        owner = _owner_of(user)
        await _owned(upload_id, owner, allow_expired=True)
        async with locks.hold(upload_id):
            await _owned(upload_id, owner, allow_expired=True)
            await store.delete(upload_id)
        return Response(status_code=204, headers=_tus_headers())

    return router


__all__: list[str] = [
    "DEFAULT_TUS_EXPIRATION",
    "DEFAULT_TUS_MAX_CHUNK_SIZE",
    "DEFAULT_TUS_MAX_SIZE",
    "TUS_CHUNK_CONTENT_TYPE",
    "TUS_CHUNK_SPOOL_BYTES",
    "TUS_CORS_ALLOW_HEADERS",
    "TUS_CORS_EXPOSE_HEADERS",
    "TusCompleteCallback",
    "make_tus_router",
]
