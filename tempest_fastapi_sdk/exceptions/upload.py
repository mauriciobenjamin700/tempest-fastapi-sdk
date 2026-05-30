"""File-upload related exceptions."""

from tempest_fastapi_sdk.exceptions.base import AppException


class FileTooLargeException(AppException):
    """Raised when an uploaded file exceeds the configured size limit."""

    status_code: int = 413
    message: str = "File too large"
    code: str = "FILE_TOO_LARGE"


class InvalidFileTypeException(AppException):
    """Raised when an uploaded file's extension or MIME is not allowed."""

    status_code: int = 415
    message: str = "Invalid file type"
    code: str = "INVALID_FILE_TYPE"


__all__: list[str] = [
    "FileTooLargeException",
    "InvalidFileTypeException",
]
