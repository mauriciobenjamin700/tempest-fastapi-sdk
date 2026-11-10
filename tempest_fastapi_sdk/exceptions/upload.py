"""File-upload related exceptions."""

from tempest_fastapi_sdk.exceptions.base import AppException


class FileTooLargeException(AppException):
    """Raised when an uploaded file exceeds the configured size limit."""

    status_code: int = 413
    message: str = "File too large"
    code: str = "FILE_TOO_LARGE"


class UploadTooLargeException(FileTooLargeException):
    """Raised when ``read_upload_capped`` stops at its byte ceiling.

    The request body is past the size the route accepts, so the answer is
    ``413 Payload Too Large``, the status an HTTP client and the OpenAI wire
    expect for it. A subclass of :class:`FileTooLargeException`, so
    ``except FileTooLargeException`` covers it too. ``details["max_bytes"]``
    carries the ceiling.
    """

    status_code: int = 413
    message: str = "Upload too large"
    code: str = "UPLOAD_TOO_LARGE"


class FileTooSmallException(AppException):
    """Raised when an uploaded file is below the signed minimum size."""

    status_code: int = 400
    message: str = "File too small"
    code: str = "FILE_TOO_SMALL"


class InvalidFileTypeException(AppException):
    """Raised when an uploaded file's extension or MIME is not allowed."""

    status_code: int = 415
    message: str = "Invalid file type"
    code: str = "INVALID_FILE_TYPE"


__all__: list[str] = [
    "FileTooLargeException",
    "FileTooSmallException",
    "InvalidFileTypeException",
    "UploadTooLargeException",
]
