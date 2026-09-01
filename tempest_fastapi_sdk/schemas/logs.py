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
        examples=[None, "b1ddc2ad-3649-4306-82b9-d442dc8f864b"],
        default=None,
    )
    exception: str | None = Field(
        title="Exception",
        description="Formatted traceback when the record carried exc_info.",
        examples=[
            None,
            "Traceback (most recent call last):\n  ...\nValueError: boom",
        ],
        default=None,
    )


class LogFilesClearedSchema(BaseSchema):
    """What a truncate request actually emptied.

    Naming the files back is what makes the operation checkable: the
    selector is a level name, and which files it covers is a decision of
    the SDK's layout, not of the caller. ``"all"`` in particular reaches
    ``500.log`` too, and a caller that assumed otherwise should be able
    to see it in the response rather than in the next incident.

    Attributes:
        cleared (list[str]): File names that were truncated, in the
            order they were processed. A file that did not exist is
            created empty and still listed — the post-condition is the
            same either way.
    """

    cleared: list[str] = Field(
        default_factory=list,
        title="Cleared files",
        description="Names of the log files that were truncated.",
        examples=[["info.log", "error.log", "500.log"]],
    )


__all__: list[str] = [
    "LogEntrySchema",
    "LogFilesClearedSchema",
]
