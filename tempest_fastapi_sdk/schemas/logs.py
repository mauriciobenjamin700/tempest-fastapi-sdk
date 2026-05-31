"""Schemas for the structured log-reading endpoint."""

from __future__ import annotations

from pydantic import ConfigDict, Field

from tempest_fastapi_sdk.schemas.base import BaseSchema


class LogEntrySchema(BaseSchema):
    """A single structured log record parsed from a JSON log file.

    The SDK's :class:`tempest_fastapi_sdk.JSONFormatter` writes one JSON
    object per line. This schema mirrors its core fields and accepts any
    additional ``extra={...}`` keys (e.g. ``path``, ``request_id``,
    ``http_500``) via ``extra="allow"`` so nothing is silently dropped
    by the ``/logs`` endpoint.

    Attributes:
        timestamp (str): ISO-8601 UTC timestamp (``...Z``).
        level (str): Log level name (``"INFO"``, ``"ERROR"``, ...).
        logger (str): Name of the logger that emitted the record.
        message (str): The formatted log message.
        request_id (str | None): Correlation ID when present.
        exception (str | None): Formatted traceback when the record
            carried ``exc_info``.
    """

    model_config = ConfigDict(
        extra="allow",
        from_attributes=True,
        use_enum_values=True,
        str_strip_whitespace=True,
        validate_assignment=True,
        arbitrary_types_allowed=True,
    )

    timestamp: str = Field(
        title="Timestamp",
        description="ISO-8601 UTC timestamp of the record.",
        examples=["2026-05-31T15:27:31.193Z"],
    )
    level: str = Field(
        title="Level",
        description="Log level name.",
        examples=["INFO", "WARNING", "ERROR", "CRITICAL"],
    )
    logger: str = Field(
        title="Logger",
        description="Name of the logger that emitted the record.",
        examples=["tempest_fastapi_sdk.api.handlers"],
    )
    message: str = Field(
        title="Message",
        description="The formatted log message.",
        examples=["Unhandled exception during GET /api/items"],
    )
    request_id: str | None = Field(
        title="Request ID",
        description="Correlation ID attached to the request, if any.",
        default=None,
    )
    exception: str | None = Field(
        title="Exception",
        description="Formatted traceback when the record carried exc_info.",
        default=None,
    )


__all__: list[str] = [
    "LogEntrySchema",
]
