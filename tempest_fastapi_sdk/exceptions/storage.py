"""Errors raised by the object stores of :mod:`tempest_fastapi_sdk.storage`."""

from typing import Any, ClassVar

from tempest_fastapi_sdk.exceptions.base import AppException


class StorageIntegrityException(AppException):
    """Raised when an encrypted object cannot be authenticated or parsed.

    :class:`~tempest_fastapi_sdk.storage.LocalObjectStorageClient`, built
    with ``encryption_keys``, raises it instead of returning bytes it could
    not authenticate: a block whose tag does not verify (wrong key, a byte
    changed on disk, a block moved, a file copied to another key), a file
    cut short, a header it cannot parse, a key id it does not hold, or a
    plaintext file while ``allow_plaintext_read`` is off.

    It is a ``500``: the request was fine, the stored object is not. The
    response carries ``details["reason"]`` only — never the bucket, the key
    or any byte of the file — and the handler logs it like any other server
    error.

    Attributes:
        reason (str): ``"authentication"``, ``"format"``, ``"unknown_key"``
            or ``"plaintext"``.
    """

    status_code: int = 500
    message: str = "The stored object failed its integrity check"
    code: str = "STORAGE_INTEGRITY_ERROR"
    details_example: ClassVar[dict[str, Any]] = {"reason": "authentication"}

    def __init__(self, reason: str, *, message: str | None = None) -> None:
        """Initialize the exception.

        Args:
            reason (str): Which check failed — ``"authentication"``,
                ``"format"``, ``"unknown_key"`` or ``"plaintext"``.
            message (str | None): Override the message, for the log. It is
                never built from the object's name or contents.
        """
        self.reason: str = reason
        super().__init__(message=message, details={"reason": reason})


__all__: list[str] = ["StorageIntegrityException"]
