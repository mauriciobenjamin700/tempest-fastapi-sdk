"""Domain exceptions raised by the host bridge.

Each one subclasses an SDK envelope exception, so a service that already
calls :func:`register_exception_handlers` answers a host failure in the
standard ``{detail, code, details}`` shape with no extra wiring. Every
``code`` below ships a PT-BR and an en-US sentence in
:func:`default_message_catalog`, which is what keeps a raise site from
having to build prose it cannot translate.
"""

from tempest_fastapi_sdk.exceptions.base import AppException
from tempest_fastapi_sdk.exceptions.not_found import NotFoundException
from tempest_fastapi_sdk.exceptions.upload import FileTooLargeException
from tempest_fastapi_sdk.exceptions.validation import ValidationException


class InvalidHostPathError(ValidationException):
    """Path resolves outside every allowed base path, or cannot be translated."""

    message: str = "Path is not allowed."
    code: str = "HOST_INVALID_PATH"
    status_code: int = 400


class HostFileNotFoundError(NotFoundException):
    """Target does not exist on the host filesystem."""

    message: str = "File not found on host."
    code: str = "HOST_FILE_NOT_FOUND"


class HostFileTooLargeError(FileTooLargeException):
    """File is larger than the configured read ceiling."""

    message: str = "File exceeds maximum allowed size."
    code: str = "HOST_FILE_TOO_LARGE"


class HostFileDecodeError(ValidationException):
    """File bytes do not decode with the requested text encoding.

    This is what pointing the text reader at a PDF or an image looks like.
    Left to propagate as a ``UnicodeDecodeError`` it would surface as an
    opaque 500, when it is a client error the caller can act on.
    """

    message: str = (
        "File is not decodable as text with the requested encoding. "
        "Read PDFs through the PDF reader, or pass the file's real encoding."
    )
    code: str = "HOST_FILE_DECODE_FAILED"
    status_code: int = 400


class HostCommandError(AppException):
    """A host command exited with a non-zero status."""

    message: str = "Command execution failed."
    code: str = "HOST_COMMAND_FAILED"
    status_code: int = 500


class HostCommandTimeoutError(AppException):
    """A host command outlived its timeout and was killed."""

    message: str = "Command execution timed out."
    code: str = "HOST_COMMAND_TIMEOUT"
    status_code: int = 504


class HostUnavailableError(AppException):
    """The host control surface is not reachable from this process.

    Raised when the configured shell or ``wslpath`` is missing — which is
    what this package looks like on a plain Linux box, or on a Windows host
    whose PowerShell is not on ``PATH``. It is distinct from
    :class:`HostCommandError` on purpose: the command never ran, so a
    caller can degrade instead of reporting a failed action.
    """

    message: str = "Host control is not available in this environment."
    code: str = "HOST_UNAVAILABLE"
    status_code: int = 503


__all__: list[str] = [
    "HostCommandError",
    "HostCommandTimeoutError",
    "HostFileDecodeError",
    "HostFileNotFoundError",
    "HostFileTooLargeError",
    "HostUnavailableError",
    "InvalidHostPathError",
]
