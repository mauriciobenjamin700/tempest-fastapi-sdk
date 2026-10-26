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

from collections.abc import AsyncIterator
from typing import TYPE_CHECKING

from fastapi import APIRouter, Depends, Request
from starlette.responses import Response, StreamingResponse

from tempest_fastapi_sdk.exceptions.not_found import NotFoundException
from tempest_fastapi_sdk.exceptions.signed_url import InvalidSignedURLException
from tempest_fastapi_sdk.exceptions.upload import (
    FileTooLargeException,
    FileTooSmallException,
)
from tempest_fastapi_sdk.storage.local_client import (
    LOCAL_GET_LINK_PARAMS,
    LOCAL_POST_LINK_PARAMS,
    LOCAL_STORAGE_CHUNK_SIZE,
    LOCAL_STORAGE_GET_PURPOSE,
    LOCAL_STORAGE_POST_PURPOSE,
    LOCAL_STORAGE_PUT_PURPOSE,
    LocalObjectNotFoundError,
)
from tempest_fastapi_sdk.storage.presign import POST_FILE_FIELD

POST_FORM_OVERHEAD_BYTES: int = 64 * 1024
"""Room the multipart framing and form fields get on top of ``max_bytes``.

A ``Content-Length`` above ``max_bytes`` plus this is refused before the
form is read; the file part itself is then held to ``max_bytes`` exactly.
"""

POST_FORM_MAX_FIELDS: int = 32
"""Most non-file fields a form upload may carry."""

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


def _bound_int(params: dict[str, str], name: str) -> int:
    """Read an integer limit the signature already vouched for.

    Args:
        params (dict[str, str]): The verified parameters.
        name (str): The parameter.

    Returns:
        int: Its value.

    Raises:
        InvalidSignedURLException: When it is absent or not a number — only
            a link the client did not sign can look like that.
    """
    raw = params.get(name, "")
    if not (raw.isascii() and raw.isdigit()):
        raise InvalidSignedURLException()
    return int(raw)


def make_local_storage_router(
    client: LocalObjectStorageClient,
    *,
    as_attachment: bool = False,
    cache_control: str | None = "private, no-store",
) -> APIRouter:
    """Build the ``GET``/``PUT``/``POST`` routes for a local store's signed links.

    * ``GET {route_prefix}/{bucket}/{key}`` verifies a download signature
      and streams the object with its stored content type, honouring
      ``Range`` and the validators (``206``/``304``/``416``) the same way
      :meth:`AsyncMinIOClient.download_response
      <tempest_fastapi_sdk.storage.AsyncMinIOClient.download_response>`
      does. A missing object answers ``404 NOT_FOUND``. A link signed with
      ``filename``/``media_type``/``as_attachment`` answers with those,
      the way S3 answers a link with signed ``response-*`` overrides.
    * ``PUT {route_prefix}/{bucket}/{key}`` verifies an upload signature
      and stores the raw body with the request's ``Content-Type``. A
      ``Content-Length`` over :attr:`LocalObjectStorageClient.max_upload_bytes`
      is refused before reading; without it the body is counted while it
      streams and cut at the cap — ``413 FILE_TOO_LARGE`` either way, and
      nothing is stored. Success answers ``200`` with the ``ETag``, as S3
      does.
    * ``POST {route_prefix}/{bucket}/{key}`` answers a
      :meth:`LocalObjectStorageClient.presigned_post_policy` form. The file
      must fit the signed ``[min_bytes, max_bytes]`` — ``413 FILE_TOO_LARGE``
      / ``400 FILE_TOO_SMALL`` otherwise — and the ``key`` and
      ``Content-Type`` fields must match the signed ones (``403
      SIGNED_URL_INVALID``). Success answers ``204`` with the ``ETag``, as
      S3 does. Reading the form needs ``python-multipart`` (``[upload]``).

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
    can_upload = client.signature_dependency(LOCAL_STORAGE_PUT_PURPOSE)

    @router.get("/{bucket}/{key:path}")
    async def download_object(
        request: Request,
        bucket: str,
        key: str,
    ) -> StreamingResponse:
        params = client.verify_link(
            request,
            purpose=LOCAL_STORAGE_GET_PURPOSE,
            names=LOCAL_GET_LINK_PARAMS,
        )
        _validated(client, bucket, key)
        disposition = params.get("disposition")
        try:
            return await client.download_response(
                key,
                request=request,
                bucket=bucket,
                filename=params.get("filename"),
                media_type=params.get("media_type"),
                as_attachment=(
                    as_attachment if disposition is None else disposition != "inline"
                ),
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

    @router.post("/{bucket}/{key:path}")
    async def post_object(request: Request, bucket: str, key: str) -> Response:
        params = client.verify_link(
            request,
            purpose=LOCAL_STORAGE_POST_PURPOSE,
            names=LOCAL_POST_LINK_PARAMS,
        )
        _validated(client, bucket, key)
        max_bytes = _bound_int(params, "max_bytes")
        min_bytes = _bound_int(params, "min_bytes")
        declared = request.headers.get("content-length")
        if (
            declared is not None
            and declared.isdigit()
            and int(declared) > max_bytes + POST_FORM_OVERHEAD_BYTES
        ):
            raise FileTooLargeException(details={"max_size_bytes": max_bytes})
        async with request.form(
            max_files=1,
            max_fields=POST_FORM_MAX_FIELDS,
        ) as form:
            if form.get("key", key) != key:
                raise InvalidSignedURLException()
            expected_type = params.get("content_type")
            sent_type = form.get("Content-Type")
            if expected_type is not None and sent_type != expected_type:
                raise InvalidSignedURLException()
            upload = form.get(POST_FILE_FIELD)
            if upload is None or isinstance(upload, str):
                raise InvalidSignedURLException()
            size = upload.size or 0
            if size > max_bytes:
                raise FileTooLargeException(details={"max_size_bytes": max_bytes})
            if size < min_bytes:
                raise FileTooSmallException(details={"min_size_bytes": min_bytes})

            async def chunks() -> AsyncIterator[bytes]:
                while chunk := await upload.read(LOCAL_STORAGE_CHUNK_SIZE):
                    yield chunk

            etag = await client.write_stream(
                key,
                chunks(),
                bucket=bucket,
                content_type=(
                    sent_type
                    if isinstance(sent_type, str)
                    else upload.content_type or "application/octet-stream"
                ),
                max_bytes=max_bytes,
            )
        return Response(status_code=204, headers={"etag": f'"{etag}"'})

    return router


__all__: list[str] = ["make_local_storage_router"]
