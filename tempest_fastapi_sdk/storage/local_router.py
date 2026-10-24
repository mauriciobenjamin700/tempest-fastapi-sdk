"""Routes that serve the signed links of :class:`LocalObjectStorageClient`.

The client signs ``<route_prefix>/<bucket>/<key>`` for download and for
upload under two different purposes; these two routes are what answers
them. Neither route checks who the caller is: the code that handed out the
link already authorized them, and the signature is the capability.

Mount the router at the **app root** — ``app.include_router(router)`` with
no extra ``prefix`` — because the signature covers the path the app sees,
and the router already carries the client's ``route_prefix``.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from fastapi import APIRouter, Depends, Request
from starlette.responses import Response, StreamingResponse

from tempest_fastapi_sdk.exceptions.not_found import NotFoundException
from tempest_fastapi_sdk.exceptions.upload import FileTooLargeException
from tempest_fastapi_sdk.storage.local_client import (
    LOCAL_STORAGE_GET_PURPOSE,
    LOCAL_STORAGE_PUT_PURPOSE,
    LocalObjectNotFoundError,
)

if TYPE_CHECKING:
    from tempest_fastapi_sdk.storage.local_client import LocalObjectStorageClient


def _validated(
    client: LocalObjectStorageClient,
    bucket: str,
    key: str,
) -> None:
    """Refuse a bucket or key the client would refuse, as ``404``.

    A valid signature over an invalid path cannot be produced by the client,
    so this only fires for a store whose layout changed under the link.

    Args:
        client (LocalObjectStorageClient): The store.
        bucket (str): Bucket from the path.
        key (str): Key from the path.

    Raises:
        NotFoundException: When the bucket or key is invalid.
    """
    try:
        client.object_path(key, bucket=bucket)
    except ValueError:
        raise NotFoundException(message="Object not found") from None


def make_local_storage_router(
    client: LocalObjectStorageClient,
    *,
    as_attachment: bool = False,
    cache_control: str | None = "private, no-store",
) -> APIRouter:
    """Build the ``GET``/``PUT`` routes for a local store's signed links.

    * ``GET {route_prefix}/{bucket}/{key}`` verifies a download signature
      and streams the object with its stored content type, honouring
      ``Range`` and the validators (``206``/``304``/``416``) the same way
      :meth:`AsyncMinIOClient.download_response
      <tempest_fastapi_sdk.storage.AsyncMinIOClient.download_response>`
      does. A missing object answers ``404 NOT_FOUND``.
    * ``PUT {route_prefix}/{bucket}/{key}`` verifies an upload signature
      and stores the raw body with the request's ``Content-Type``. A
      ``Content-Length`` over :attr:`LocalObjectStorageClient.max_upload_bytes`
      is refused before reading; without it the body is counted while it
      streams and cut at the cap — ``413 FILE_TOO_LARGE`` either way, and
      nothing is stored. Success answers ``200`` with the ``ETag``, as S3
      does.

    A tampered, unsigned or wrong-purpose link answers
    ``403 SIGNED_URL_INVALID``, an expired one ``403 SIGNED_URL_EXPIRED``.
    Both routes stay out of the OpenAPI schema.

    Args:
        client (LocalObjectStorageClient): The store whose links to serve.
        as_attachment (bool): ``True`` makes downloads save as a file;
            ``False`` (default) shows inline-safe types (images, PDF) in the
            browser, which is what an ``<img src>`` needs.
        cache_control (str | None): ``Cache-Control`` of downloads. Default
            ``"private, no-store"``: the link is a short-lived capability,
            so no shared cache keeps the bytes. ``None`` sends none.

    Returns:
        APIRouter: Router with ``prefix=client.route_prefix``; include it at
        the app root.
    """
    router = APIRouter(prefix=client.route_prefix, include_in_schema=False)
    can_download = client.signature_dependency(LOCAL_STORAGE_GET_PURPOSE)
    can_upload = client.signature_dependency(LOCAL_STORAGE_PUT_PURPOSE)

    @router.get("/{bucket}/{key:path}", dependencies=[Depends(can_download)])
    async def download_object(
        request: Request,
        bucket: str,
        key: str,
    ) -> StreamingResponse:
        _validated(client, bucket, key)
        try:
            return await client.download_response(
                key,
                request=request,
                bucket=bucket,
                as_attachment=as_attachment,
                cache_control=cache_control,
            )
        except LocalObjectNotFoundError:
            raise NotFoundException(message="Object not found") from None

    @router.put("/{bucket}/{key:path}", dependencies=[Depends(can_upload)])
    async def upload_object(request: Request, bucket: str, key: str) -> Response:
        _validated(client, bucket, key)
        declared = request.headers.get("content-length")
        if (
            declared is not None
            and declared.isdigit()
            and int(declared) > client.max_upload_bytes
        ):
            raise FileTooLargeException(
                details={"max_size_bytes": client.max_upload_bytes},
            )
        etag = await client.write_stream(
            key,
            request.stream(),
            bucket=bucket,
            content_type=request.headers.get(
                "content-type", "application/octet-stream"
            ),
            max_bytes=client.max_upload_bytes,
        )
        return Response(status_code=200, headers={"etag": f'"{etag}"'})

    return router


__all__: list[str] = ["make_local_storage_router"]
