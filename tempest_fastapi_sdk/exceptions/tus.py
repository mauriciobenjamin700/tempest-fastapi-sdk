"""Errors answered by the tus resumable-upload router.

:func:`~tempest_fastapi_sdk.storage.tus.make_tus_router` raises these so a
refused tus request carries the same JSON envelope as every other SDK error
(``detail``/``code``/``details``). The router attaches ``Tus-Resumable`` to
every one of them, which the tus 1.0.0 spec asks of every response except to
``OPTIONS``.
"""

from typing import Any, ClassVar

from tempest_fastapi_sdk.exceptions.base import AppException


class TusVersionException(AppException):
    """Raised when ``Tus-Resumable`` is missing or names another version.

    tus 1.0.0 answers ``412 Precondition Failed`` with the ``Tus-Version``
    header listing what the server speaks.
    """

    status_code: int = 412
    message: str = "Unsupported tus protocol version"
    code: str = "TUS_VERSION_UNSUPPORTED"
    details_example: ClassVar[dict[str, Any]] = {"supported": ["1.0.0"]}


class TusUploadNotFoundException(AppException):
    """Raised when an upload does not exist or belongs to someone else.

    Both cases answer the same ``404``: telling them apart would confirm to a
    caller that an upload id it does not own is real.
    """

    status_code: int = 404
    message: str = "Upload not found"
    code: str = "TUS_UPLOAD_NOT_FOUND"


class TusUploadExpiredException(AppException):
    """Raised when an incomplete upload is past its ``Upload-Expires``.

    ``410 Gone``, which the tus expiration extension allows next to ``404``.
    The client restarts the upload from zero.
    """

    status_code: int = 410
    message: str = "Upload expired"
    code: str = "TUS_UPLOAD_EXPIRED"


class TusOffsetMismatchException(AppException):
    """Raised when a ``PATCH`` writes at an offset the server does not hold.

    ``409 Conflict``, as the tus core protocol asks. ``details["offset"]`` is
    the offset the server holds, so a client can continue from there without a
    ``HEAD``.
    """

    status_code: int = 409
    message: str = "Upload-Offset does not match the stored offset"
    code: str = "TUS_OFFSET_MISMATCH"
    details_example: ClassVar[dict[str, Any]] = {"offset": 5242880}


class TusContentTypeException(AppException):
    """Raised when a ``PATCH`` is not ``application/offset+octet-stream``.

    ``415 Unsupported Media Type``, as the tus core protocol asks.
    """

    status_code: int = 415
    message: str = "PATCH requires Content-Type: application/offset+octet-stream"
    code: str = "TUS_CONTENT_TYPE_INVALID"


class TusInvalidHeaderException(AppException):
    """Raised when a tus header is missing or malformed.

    Covers ``Upload-Length`` (absent, not a non-negative integer),
    ``Upload-Offset`` (same) and ``Upload-Metadata`` (a pair whose value is
    not base64 of UTF-8 text). ``details["header"]`` names the header.
    """

    status_code: int = 400
    message: str = "Invalid tus header"
    code: str = "TUS_HEADER_INVALID"
    details_example: ClassVar[dict[str, Any]] = {"header": "Upload-Length"}


class TusUploadTooLargeException(AppException):
    """Raised when an upload or a chunk is bigger than allowed.

    ``413``: either ``Upload-Length`` is over the router's ``max_size`` (the
    response then carries ``Tus-Max-Size``), or a ``PATCH`` body would carry
    the upload past its declared ``Upload-Length``, or past the router's
    ``max_chunk_size``. ``details["max_bytes"]`` is the limit that was hit.
    """

    status_code: int = 413
    message: str = "Upload exceeds the allowed size"
    code: str = "TUS_UPLOAD_TOO_LARGE"
    details_example: ClassVar[dict[str, Any]] = {"max_bytes": 104857600}


class TusIdempotencyKeyReusedException(AppException):
    """Raised when an ``Idempotency-Key`` is replayed for another upload.

    The same caller sent the key before with a different ``Upload-Length``:
    replaying the first upload would hand back a URL for a file of the wrong
    size, so the router answers ``422`` instead, as Stripe does for a reused
    key.
    """

    status_code: int = 422
    message: str = "Idempotency-Key was already used for a different upload"
    code: str = "TUS_IDEMPOTENCY_KEY_REUSED"


__all__: list[str] = [
    "TusContentTypeException",
    "TusIdempotencyKeyReusedException",
    "TusInvalidHeaderException",
    "TusOffsetMismatchException",
    "TusUploadExpiredException",
    "TusUploadNotFoundException",
    "TusUploadTooLargeException",
    "TusVersionException",
]
