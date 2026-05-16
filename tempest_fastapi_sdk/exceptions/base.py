"""Base application exception integrated with FastAPI."""

from typing import Any, ClassVar

from fastapi import HTTPException


class AppException(HTTPException):
    """Base exception for all application-level errors.

    Subclasses override ``status_code``, ``message`` and ``code`` as
    class attributes. The constructor allows overriding the message
    for dynamic error cases and attaching structured ``details`` that
    are surfaced to API clients alongside the human-readable message.

    The matching exception handler (see
    :mod:`tempest_fastapi_sdk.api.handlers`) emits the JSON shape::

        {
            "detail": "<message>",
            "code": "<code>",
            "details": {"<any>": "<context>"}
        }

    Class attributes:
        status_code (int): The HTTP status code returned to clients.
        message (str): The default human-readable error message.
        code (ClassVar[str]): Stable, machine-readable identifier.
            Defaults to ``INTERNAL_SERVER_ERROR``; subclasses set
            their own.

    Instance attributes:
        details (dict[str, Any]): Free-form context attached to the
            response payload.
    """

    status_code: int = 500
    message: str = "Internal server error"
    code: ClassVar[str] = "INTERNAL_SERVER_ERROR"

    def __init__(
        self,
        message: str | None = None,
        details: dict[str, Any] | None = None,
        headers: dict[str, str] | None = None,
    ) -> None:
        """Initialize the exception.

        Args:
            message (str | None): Override the class-level message.
            details (dict[str, Any] | None): Structured context to
                attach to the JSON response.
            headers (dict[str, str] | None): Optional HTTP headers
                to include in the response.
        """
        self.details: dict[str, Any] = details or {}
        super().__init__(
            status_code=self.status_code,
            detail=message or self.message,
            headers=headers,
        )


__all__: list[str] = [
    "AppException",
]
