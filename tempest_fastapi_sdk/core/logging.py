"""Structured JSON logging with request-ID correlation."""

import json
import logging
import sys
from datetime import UTC, datetime
from typing import Any

from tempest_fastapi_sdk.core.context import get_request_id

_RESERVED_LOG_FIELDS: frozenset[str] = frozenset(
    {
        "args",
        "created",
        "exc_info",
        "exc_text",
        "filename",
        "funcName",
        "levelname",
        "levelno",
        "lineno",
        "message",
        "module",
        "msecs",
        "msg",
        "name",
        "pathname",
        "process",
        "processName",
        "relativeCreated",
        "stack_info",
        "taskName",
        "thread",
        "threadName",
    }
)


class JSONFormatter(logging.Formatter):
    """Render every log record as a single-line JSON object.

    Standard ``LogRecord`` fields are mapped to ``timestamp``,
    ``level``, ``logger`` and ``message``. The current request ID
    (when present) is attached as ``request_id``. Any additional
    keyword passed to the logger via ``extra={...}`` becomes a
    top-level key in the JSON payload.
    """

    def format(self, record: logging.LogRecord) -> str:
        """Serialize ``record`` to JSON.

        Args:
            record (logging.LogRecord): The record to format.

        Returns:
            str: A JSON document as a single line.
        """
        timestamp = (
            datetime.fromtimestamp(record.created, tz=UTC)
            .isoformat()
            .replace("+00:00", "Z")
        )
        payload: dict[str, Any] = {
            "timestamp": timestamp,
            "level": record.levelname,
            "logger": record.name,
            "message": record.getMessage(),
        }
        request_id = get_request_id()
        if request_id is not None:
            payload["request_id"] = request_id
        if record.exc_info:
            payload["exception"] = self.formatException(record.exc_info)
        for key, value in record.__dict__.items():
            if key in _RESERVED_LOG_FIELDS:
                continue
            if key.startswith("_"):
                continue
            payload[key] = value
        return json.dumps(payload, default=str, ensure_ascii=False)


def configure_logging(
    level: str | int = "INFO",
    *,
    json_output: bool = True,
    logger_name: str | None = None,
) -> logging.Logger:
    """Install a structured stdout handler on the root (or named) logger.

    Replaces existing handlers on the target logger so this can be
    called safely from ``create_app`` without stacking duplicates.

    Args:
        level (str | int): The minimum level to emit (e.g. ``"INFO"``,
            ``logging.DEBUG``).
        json_output (bool): When ``True`` (default), emit JSON via
            :class:`JSONFormatter`. When ``False``, fall back to a
            human-readable text formatter — useful in local dev where
            JSON noise overwhelms the terminal.
        logger_name (str | None): The logger to configure. ``None``
            (default) configures the root logger.

    Returns:
        logging.Logger: The configured logger instance.
    """
    logger = logging.getLogger(logger_name)
    logger.setLevel(level)
    for handler in list(logger.handlers):
        logger.removeHandler(handler)

    handler = logging.StreamHandler(sys.stdout)
    if json_output:
        handler.setFormatter(JSONFormatter())
    else:
        handler.setFormatter(
            logging.Formatter(
                fmt="%(asctime)s %(levelname)s %(name)s %(message)s",
                datefmt="%Y-%m-%dT%H:%M:%S",
            )
        )
    logger.addHandler(handler)
    logger.propagate = False
    return logger


__all__: list[str] = [
    "JSONFormatter",
    "configure_logging",
]
