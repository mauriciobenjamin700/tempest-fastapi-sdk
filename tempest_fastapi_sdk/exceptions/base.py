"""Base application exception integrated with FastAPI."""

from typing import Any

from fastapi import HTTPException


class AppException(HTTPException):
    """Base exception for all application-level errors.

    Concrete projects raise either a domain-specific subclass (kept
    around for ``except DomainError`` matching) or the base directly,
    passing ``code`` / ``status_code`` / ``message`` via constructor
    keyword arguments. Class-level attributes are the defaults each
    constructor argument falls back to, never required overrides::

        class UserNotFoundError(NotFoundException):
            \"\"\"Subclass exists only for isinstance/except matching.\"\"\"

        raise UserNotFoundError(
            "Usuário não encontrado",
            code="USER_NOT_FOUND",
            details={"email": email},
        )

    The matching exception handler (see
    :mod:`tempest_fastapi_sdk.api.handlers`) emits the JSON shape::

        {
            "detail": "<message>",
            "code": "<code>",
            "details": {"<any>": "<context>"}
        }

    Class attributes (defaults the constructor falls back to):
        status_code (int): HTTP status code.
        message (str): Default human-readable message.
        code (str): Stable, machine-readable identifier.

    Instance attributes:
        status_code (int): The status code attached to this instance.
        code (str): The error code attached to this instance.
        details (dict[str, Any]): Free-form context attached to the
            response payload.
        message_key (str | None): Catalog key used to localize the
            ``detail`` when a ``MessageCatalog`` is registered. ``None``
            falls back to ``code`` at resolution time.
        message_params (dict[str, Any]): Values interpolated into the
            localized template via :meth:`str.format`.

    Localization:
        When :func:`register_exception_handlers` is given a ``catalog``,
        the handler resolves ``message_key`` (or ``code``) against the
        request's negotiated locale and replaces ``detail`` with the
        localized string. Without a catalog the behavior is unchanged —
        ``detail`` is the literal ``message``.
    """

    status_code: int = 500
    message: str = "Internal server error"
    code: str = "INTERNAL_SERVER_ERROR"
    message_key: str | None = None

    def __init__(
        self,
        message: str | None = None,
        *,
        code: str | None = None,
        status_code: int | None = None,
        details: dict[str, Any] | None = None,
        headers: dict[str, str] | None = None,
        message_key: str | None = None,
        message_params: dict[str, Any] | None = None,
    ) -> None:
        """Initialize the exception.

        Args:
            message (str | None): Override the class-level message. Used
                verbatim as ``detail`` when no catalog localizes it.
            code (str | None): Override the class-level error code on
                this instance only — leaves other instances of the
                same class untouched.
            status_code (int | None): Override the class-level HTTP
                status code on this instance only.
            details (dict[str, Any] | None): Structured context to
                attach to the JSON response.
            headers (dict[str, str] | None): Optional HTTP headers
                to include in the response.
            message_key (str | None): Catalog key to localize against.
                Defaults to the class-level ``message_key`` (and, at
                resolution time, to ``code`` when both are ``None``).
            message_params (dict[str, Any] | None): Values interpolated
                into the localized message template.
        """
        cls = type(self)
        self.code: str = code if code is not None else cls.code
        self.message_key: str | None = (
            message_key if message_key is not None else cls.message_key
        )
        self.message_params: dict[str, Any] = message_params or {}
        effective_status: int = (
            status_code if status_code is not None else cls.status_code
        )
        self.details: dict[str, Any] = details or {}
        super().__init__(
            status_code=effective_status,
            detail=message or cls.message,
            headers=headers,
        )


__all__: list[str] = [
    "AppException",
]
