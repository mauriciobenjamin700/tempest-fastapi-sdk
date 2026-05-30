"""429 Too Many Requests exception."""

from typing import Any

from tempest_fastapi_sdk.exceptions.base import AppException


class TooManyRequestsException(AppException):
    """Raised when a client exceeds a rate limit or attempt budget.

    Carries an optional ``Retry-After`` header (seconds) and mirrors
    the same value under ``details["retry_after_seconds"]`` so clients
    can back off without parsing headers. Used by
    :class:`tempest_fastapi_sdk.utils.AttemptThrottle` and suitable for
    any throttled flow (login, OTP, code verification).
    """

    status_code: int = 429
    message: str = "Too many requests"
    code: str = "TOO_MANY_REQUESTS"

    def __init__(
        self,
        message: str | None = None,
        *,
        retry_after_seconds: int | None = None,
        details: dict[str, Any] | None = None,
        headers: dict[str, str] | None = None,
    ) -> None:
        """Initialize the exception.

        Args:
            message (str | None): Override the class-level message.
            retry_after_seconds (int | None): Cooldown in seconds. When
                given, sets the ``Retry-After`` header and adds
                ``retry_after_seconds`` to ``details`` (unless already
                provided by the caller).
            details (dict[str, Any] | None): Structured context.
            headers (dict[str, str] | None): Extra response headers;
                merged with the ``Retry-After`` header when applicable.
        """
        merged_details: dict[str, Any] = dict(details or {})
        merged_headers: dict[str, str] = dict(headers or {})
        if retry_after_seconds is not None:
            merged_details.setdefault(
                "retry_after_seconds",
                retry_after_seconds,
            )
            merged_headers.setdefault(
                "Retry-After",
                str(retry_after_seconds),
            )
        super().__init__(
            message=message,
            details=merged_details,
            headers=merged_headers or None,
        )


__all__: list[str] = [
    "TooManyRequestsException",
]
