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

import hashlib
import logging
import secrets
import tempfile
from collections.abc import Awaitable, Callable, Coroutine
from datetime import UTC, datetime, timedelta
from typing import Any, BinaryIO, Protocol, cast
from uuid import UUID

from fastapi import APIRouter, Depends, Request
from fastapi.routing import APIRoute
from starlette.exceptions import HTTPException
from starlette.responses import Response

from tempest_fastapi_sdk.exceptions.base import AppException
from tempest_fastapi_sdk.exceptions.tus import (
    TusChunkTooSmallException,
    TusContentTypeException,
    TusIdempotencyKeyReusedException,
    TusInvalidHeaderException,
    TusOffsetMismatchException,
    TusQuotaExceededException,
    TusUploadCompletedException,
    TusUploadExpiredException,
    TusUploadLockedException,
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

logger: logging.Logger = logging.getLogger(__name__)

TUS_MAX_INTEGER_HEADER_DIGITS: int = 20
"""Most digits ``Upload-Length``/``Upload-Offset`` may carry.

Past it the header is refused with ``400`` before ``int()`` runs: Python
refuses to convert more than 4300 digits and would answer ``500``. Twenty
digits already exceed any size a store can hold.
"""

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
    """Called exactly once per upload, after its last byte is stored and finalized.

    Receives the upload and the store, so it can read the file with
    :meth:`TusStore.stream <tempest_fastapi_sdk.storage.tus.TusStore.stream>`
    (or :meth:`LocalTusStore.data_path
    <tempest_fastapi_sdk.storage.tus.LocalTusStore.data_path>` /
    :meth:`S3TusStore.object_key
    <tempest_fastapi_sdk.storage.tus.S3TusStore.object_key>`) and register,
    move or delete it. Runs inside the request that sent the last chunk,
    under the upload's lock, before the ``204``; a later ``PATCH`` to the
    complete upload answers ``409`` without calling it. Whatever it returns
    is discarded, and it may call :meth:`TusStore.delete
    <tempest_fastapi_sdk.storage.tus.TusStore.delete>` — the router then
    leaves the record deleted.
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


class _TusRoute(APIRoute):
    """A route whose responses, refusals included, carry ``Tus-Resumable``.

    The header is added to the response the endpoint returns and to every
    :class:`~starlette.exceptions.HTTPException` raised while handling it —
    the router's own errors and a ``401``/``403`` from the
    ``current_user_id`` dependency alike. An unhandled exception (a ``500``)
    is answered by the app's handlers, outside the route, without it.
    """

    def get_route_handler(self) -> Callable[[Request], Coroutine[Any, Any, Response]]:
        """Wrap FastAPI's handler to stamp the tus header.

        Returns:
            Callable[[Request], Coroutine[Any, Any, Response]]: The handler.
        """
        original = super().get_route_handler()

        async def handler(request: Request) -> Response:
            """Run the endpoint and stamp ``Tus-Resumable``.

            Args:
                request (Request): The request.

            Returns:
                Response: The endpoint's response, with the header.
            """
            try:
                response = await original(request)
            except HTTPException as error:
                error.headers = {
                    **(error.headers or {}),
                    "Tus-Resumable": TUS_VERSION,
                }
                raise
            response.headers.setdefault("Tus-Resumable", TUS_VERSION)
            return response

        return handler


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


def _with_headers(error: AppException, extra: dict[str, str]) -> AppException:
    """Attach extra headers to an exception about to be raised.

    Args:
        error (AppException): The exception.
        extra (dict[str, str]): Headers to add.

    Returns:
        AppException: The same exception, with its headers set.
    """
    error.headers = {**(error.headers or {}), **extra}
    return error


def _require_version(request: Request) -> None:
    """Refuse a request whose ``Tus-Resumable`` is not ``1.0.0``.

    Args:
        request (Request): The request.

    Raises:
        TusVersionException: With ``Tus-Version: 1.0.0``.
    """
    if request.headers.get("tus-resumable") != TUS_VERSION:
        raise _with_headers(
            TusVersionException(details={"supported": [TUS_VERSION]}),
            {"Tus-Version": TUS_VERSION},
        )


def _digits(raw: str | None) -> int | None:
    """Parse a non-negative integer header of at most twenty digits.

    Args:
        raw (str | None): The header value.

    Returns:
        int | None: Its value, or ``None`` when absent or malformed.
    """
    if (
        raw is None
        or not raw
        or len(raw) > TUS_MAX_INTEGER_HEADER_DIGITS
        or not (raw.isascii() and raw.isdigit())
    ):
        return None
    return int(raw)


def _non_negative_header(request: Request, name: str) -> int:
    """Read a header that must be a non-negative integer.

    Args:
        request (Request): The request.
        name (str): Header name.

    Returns:
        int: Its value.

    Raises:
        TusInvalidHeaderException: When absent, not digits only, or longer
            than :data:`TUS_MAX_INTEGER_HEADER_DIGITS`.
    """
    value = _digits(request.headers.get(name))
    if value is None:
        raise TusInvalidHeaderException(details={"header": name})
    return value


def _owner_of(user: Any) -> str | None:
    """Turn what ``current_user_id`` resolved into the stored owner.

    Only a stable identifier is accepted: a ``str()`` of an arbitrary object
    (a user model, a dict of claims) can change between requests or collide
    between users, and the owner check would then fail open or closed at
    random.

    Args:
        user (Any): The dependency's value.

    Returns:
        str | None: ``str(user)``, or ``None`` for an anonymous caller.

    Raises:
        TypeError: When the value is not ``None``, ``str``, ``int`` or
            ``UUID`` (``bool`` included, which is an ``int`` by accident).
    """
    if user is None:
        return None
    if isinstance(user, bool) or not isinstance(user, str | int | UUID):
        raise TypeError(
            "current_user_id must resolve to the user's id as str, int or "
            f"UUID (or None), got {type(user).__name__}",
        )
    return str(user)


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
    max_uploads_per_owner: int | None = None,
    max_bytes_per_owner: int | None = None,
    tags: list[str] | None = None,
    include_in_schema: bool = True,
) -> APIRouter:
    """Build the tus routes ``createResumableUpload`` talks to.

    Every response of these routes carries ``Tus-Resumable: 1.0.0``, and so
    does every refusal raised while handling them (the router's errors and a
    ``401``/``403`` from ``current_user_id``); an unhandled ``500`` is
    answered by the app without it. Errors use the SDK envelope
    (``detail``/``code``/``details``) through
    :class:`~tempest_fastapi_sdk.AppException`, so mount the app with
    :func:`~tempest_fastapi_sdk.register_exception_handlers` as usual.

    A ``PATCH`` takes the upload's lock **before** reading its body; a second
    ``PATCH`` or ``DELETE`` arriving meanwhile answers ``409
    TUS_UPLOAD_LOCKED`` at once, so one upload never has two bodies spooled.
    Under the lock: owner (``404``), expiry (``410``), already complete
    (``409 TUS_UPLOAD_COMPLETED``), offset (``409``), size (``413``),
    the store's ``min_chunk_size`` (``400``). The ``PATCH`` that stores the
    last byte finalizes the upload and calls ``on_complete`` — exactly once
    per upload. An empty ``PATCH`` writes nothing and does not renew the
    expiry.

    The lock lives on the store instance, in this process. Two replicas
    behind a load balancer can still race on one upload; route an upload's
    requests to one replica (sticky sessions on the upload path) when you
    run more than one.

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
            resumable after creation or its last ``PATCH`` that stored bytes,
            sent as ``Upload-Expires``. ``None`` never expires uploads.
            Expired ones answer ``410`` and are deleted by
            :meth:`TusStore.purge_expired
            <tempest_fastapi_sdk.storage.tus.TusStore.purge_expired>`.
        current_user_id (Callable[..., Any] | None): FastAPI dependency
            resolving the caller's id as ``str``, ``int`` or ``UUID`` (any
            other type raises ``TypeError``). Its ``str()`` is stored as the
            upload's owner, and only the same value can probe, continue or
            delete it; anyone else gets ``404``. ``None`` makes uploads
            anonymous: the unguessable URL is then the only capability, and
            ``Idempotency-Key`` is ignored (there is no owner to scope it by).
        on_complete (TusCompleteCallback | None): Awaited once with the
            upload and the store when the last byte is stored. If it raises,
            the request answers ``500``, the upload stays complete with
            ``notified`` false, and the client — whose next ``HEAD`` sees
            every byte — does not retry; find those with
            :meth:`TusStore.list_uploads
            <tempest_fastapi_sdk.storage.tus.TusStore.list_uploads>`.
        max_uploads_per_owner (int | None): Most unfinished, unexpired uploads
            one owner may hold; a creation past it answers ``403
            TUS_QUOTA_EXCEEDED``. ``None`` (default) sets no limit. Checking
            reads every record in the store (:meth:`TusStore.list_uploads
            <tempest_fastapi_sdk.storage.tus.TusStore.list_uploads>`) on each
            creation, so keep the store swept with ``purge_expired``.
        max_bytes_per_owner (int | None): Most declared bytes
            (``Upload-Length``) one owner's unfinished uploads may add up to;
            same answer and same cost. ``None`` (default) sets no limit.
        tags (list[str] | None): OpenAPI tags. Defaults to ``["uploads"]``.
        include_in_schema (bool): Whether the routes appear in OpenAPI.

    Returns:
        APIRouter: Ready for ``app.include_router``.
    """
    router = APIRouter(
        prefix=prefix,
        tags=list(tags or ["uploads"]),
        route_class=_TusRoute,
    )
    locks = store._upload_locks()
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
            raise TusUploadNotFoundException()
        if not allow_expired and upload.is_expired():
            raise TusUploadExpiredException()
        return upload

    async def _complete(upload: TusUpload) -> None:
        """Finalize a complete upload, save it, and run ``on_complete`` once.

        The complete record is saved before the store drops its parts and
        before the callback runs, so a failure in either never leaves an
        upload that cannot finish. A record the callback deleted stays
        deleted.

        Args:
            upload (TusUpload): An upload whose last byte is stored.
        """
        await store.finalize(upload)
        upload.notified = on_complete is None
        await store.save(upload)
        try:
            await store.discard_parts(upload)
        except Exception:
            logger.warning(
                "tus upload %s is complete but its parts were not discarded",
                upload.id,
                exc_info=True,
            )
        if on_complete is None:
            return
        await on_complete(upload, store)
        current = await store.load(upload.id)
        if current is not None:
            current.notified = True
            await store.save(current)

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

    async def _check_quota(owner: str | None, length: int) -> None:
        """Refuse a creation past the owner's active-upload quota.

        Args:
            owner (str | None): The caller.
            length (int): The new upload's ``Upload-Length``.

        Raises:
            TusQuotaExceededException: When a limit would be passed.
        """
        if max_uploads_per_owner is None and max_bytes_per_owner is None:
            return
        active = [
            upload
            for upload in await store.list_uploads()
            if upload.owner == owner
            and not upload.completed
            and not upload.is_expired()
        ]
        if (
            max_uploads_per_owner is not None
            and len(active) + 1 > max_uploads_per_owner
        ):
            raise TusQuotaExceededException(
                details={"limit": "uploads", "max": max_uploads_per_owner},
            )
        if (
            max_bytes_per_owner is not None
            and sum(upload.length for upload in active) + length > max_bytes_per_owner
        ):
            raise TusQuotaExceededException(
                details={"limit": "bytes", "max": max_bytes_per_owner},
            )

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
        await _check_quota(owner, length)
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
            async with locks.hold(upload.id):
                await _complete(upload)
        return upload

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
            raise _with_headers(
                TusUploadTooLargeException(details={"max_bytes": max_size}),
                {"Tus-Max-Size": str(max_size)},
            )
        metadata = parse_upload_metadata(request.headers.get("upload-metadata"))
        owner = _owner_of(user)
        key = request.headers.get("idempotency-key") if owner is not None else None
        async with locks.hold(f"creation:{owner or ''}"):
            if not key:
                return _created(
                    request, await _new_upload(length, metadata, owner, None)
                )
            digest = hashlib.sha256(f"{owner}\x00{key}".encode()).hexdigest()
            existing_id = await store.find_idempotent(digest)
            existing = None if existing_id is None else await store.load(existing_id)
            if (
                existing is not None
                and existing.owner == owner
                and not existing.is_expired()
            ):
                if existing.length != length:
                    raise TusIdempotencyKeyReusedException()
                return _created(request, existing)
            upload = await _new_upload(length, metadata, owner, digest)
            await store.remember_idempotent(digest, upload.id)
            return _created(request, upload)

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
        if declared is not None and (
            len(declared) > TUS_MAX_INTEGER_HEADER_DIGITS
            or (declared.isdigit() and int(declared) > limit)
        ):
            raise TusUploadTooLargeException(details={"max_bytes": limit})
        spool = cast(
            BinaryIO,
            tempfile.SpooledTemporaryFile(max_size=TUS_CHUNK_SPOOL_BYTES),  # noqa: SIM115
        )
        size = 0
        try:
            async for block in request.stream():
                size += len(block)
                if size > limit:
                    raise TusUploadTooLargeException(details={"max_bytes": limit})
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
            TusUploadLockedException: When another request holds the upload.
            TusUploadCompletedException: When every byte is already stored.
            TusOffsetMismatchException: When ``Upload-Offset`` is not the
                stored offset.
            TusUploadTooLargeException: When the body passes the declared
                length or ``max_chunk_size``.
            TusChunkTooSmallException: When a non-final chunk is under the
                store's ``min_chunk_size``.
        """
        _require_version(request)
        content_type = (
            request.headers.get("content-type", "").split(";")[0].strip().lower()
        )
        if content_type != TUS_CHUNK_CONTENT_TYPE:
            raise TusContentTypeException()
        offset = _non_negative_header(request, "upload-offset")
        owner = _owner_of(user)
        await _owned(upload_id, owner)
        async with locks.try_hold(upload_id) as held:
            if not held:
                raise TusUploadLockedException()
            upload = await _owned(upload_id, owner)
            if upload.completed:
                raise TusUploadCompletedException(details={"offset": upload.offset})
            if offset != upload.offset:
                raise TusOffsetMismatchException(details={"offset": upload.offset})
            remaining = upload.length - upload.offset
            spool, size = await _read_chunk(request, min(remaining, max_chunk_size))
            try:
                if size < store.min_chunk_size and size < remaining:
                    raise TusChunkTooSmallException(
                        details={"min_bytes": store.min_chunk_size},
                    )
                if size > 0:
                    await store.write(upload, spool, size)
                    upload.offset += size
                    if upload.offset >= upload.length:
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

        Raises:
            TusUploadLockedException: When a ``PATCH`` holds the upload.
        """
        _require_version(request)
        owner = _owner_of(user)
        await _owned(upload_id, owner, allow_expired=True)
        async with locks.try_hold(upload_id) as held:
            if not held:
                raise TusUploadLockedException()
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
    "TUS_MAX_INTEGER_HEADER_DIGITS",
    "TusCompleteCallback",
    "make_tus_router",
]
