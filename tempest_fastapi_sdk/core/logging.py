"""Structured JSON logging with request-ID correlation."""

import json
import logging
import sys
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from tempest_fastapi_sdk.core.context import get_request_id

HTTP_500_MARKER: str = "http_500"
"""``extra`` key the 500 catch-all handler sets so grave failures can
be routed to a dedicated ``500.log`` file. See
:func:`tempest_fastapi_sdk.api.handlers.make_unhandled_exception_handler`.
"""

LEVEL_LOG_FILES: dict[int, str] = {
    logging.DEBUG: "debug.log",
    logging.INFO: "info.log",
    logging.WARNING: "warning.log",
    logging.ERROR: "error.log",
    logging.CRITICAL: "critical.log",
}
"""Maps each standard level to its dedicated per-level log filename.

Each file receives **only** records whose level matches exactly (an
``ERROR`` never lands in ``warning.log``), so every severity has an
isolated, greppable stream.
"""

HTTP_500_LOG_FILE: str = "500.log"
"""Filename for the isolated 500 stream — only records carrying the
:data:`HTTP_500_MARKER` extra are written here."""


class _ExactLevelFilter(logging.Filter):
    """Allow only records whose level matches ``levelno`` exactly.

    Standard logging filters by ``level >= threshold``; per-level files
    need exact equality so each severity stays in its own file.

    Attributes:
        levelno (int): The single level number this filter admits.
    """

    def __init__(self, levelno: int) -> None:
        """Initialize the filter.

        Args:
            levelno (int): The exact level number to admit.
        """
        super().__init__()
        self.levelno: int = levelno

    def filter(self, record: logging.LogRecord) -> bool:
        """Return ``True`` only when ``record.levelno`` matches.

        Args:
            record (logging.LogRecord): The record under evaluation.

        Returns:
            bool: ``True`` when the record's level matches exactly.
        """
        return record.levelno == self.levelno


class _Http500Filter(logging.Filter):
    """Allow only records flagged with the :data:`HTTP_500_MARKER` extra."""

    def filter(self, record: logging.LogRecord) -> bool:
        """Return ``True`` only for records carrying the 500 marker.

        Args:
            record (logging.LogRecord): The record under evaluation.

        Returns:
            bool: ``True`` when the record was emitted by the 500
            catch-all handler.
        """
        return bool(getattr(record, HTTP_500_MARKER, False))


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


def _build_file_handlers(log_dir: Path) -> list[logging.Handler]:
    """Build the per-level and 500-isolation file handlers.

    Creates ``log_dir`` (and parents) if missing, then wires one
    :class:`logging.FileHandler` per standard level — each gated by an
    :class:`_ExactLevelFilter` so a record only lands in its own file —
    plus a dedicated ``500.log`` handler gated by :class:`_Http500Filter`.
    Every file handler always uses :class:`JSONFormatter` (independent of
    the stdout ``json_output`` choice) so the ``/logs`` endpoint can parse
    them back as structured records.

    Args:
        log_dir (Path): Directory to hold the log files. Created if it
            does not exist.

    Returns:
        list[logging.Handler]: The configured file handlers.
    """
    log_dir.mkdir(parents=True, exist_ok=True)
    formatter = JSONFormatter()
    handlers: list[logging.Handler] = []

    for levelno, filename in LEVEL_LOG_FILES.items():
        file_handler = logging.FileHandler(
            log_dir / filename,
            encoding="utf-8",
        )
        file_handler.setLevel(logging.DEBUG)
        file_handler.addFilter(_ExactLevelFilter(levelno))
        file_handler.setFormatter(formatter)
        handlers.append(file_handler)

    http_500_handler = logging.FileHandler(
        log_dir / HTTP_500_LOG_FILE,
        encoding="utf-8",
    )
    http_500_handler.setLevel(logging.DEBUG)
    http_500_handler.addFilter(_Http500Filter())
    http_500_handler.setFormatter(formatter)
    handlers.append(http_500_handler)

    return handlers


def configure_logging(
    level: str | int = "INFO",
    *,
    json_output: bool = True,
    logger_name: str | None = None,
    log_dir: str | Path | None = None,
) -> logging.Logger:
    """Install a structured stdout handler on the root (or named) logger.

    Replaces existing handlers on the target logger so this can be
    called safely from ``create_app`` without stacking duplicates.

    When ``log_dir`` is provided, the stdout handler is kept **and**
    per-level files are written under that directory:

    * ``debug.log`` / ``info.log`` / ``warning.log`` / ``error.log`` /
      ``critical.log`` — each receives only its own level (exact match),
      so every severity has an isolated, greppable stream.
    * ``500.log`` — only uncaught-500 records (flagged by the catch-all
      exception handler) so grave failures are never buried among the
      rest. A 500 therefore appears in both ``error.log`` and
      ``500.log``.

    File handlers always emit JSON regardless of ``json_output`` so the
    :func:`tempest_fastapi_sdk.make_logs_router` endpoint can parse them.

    Args:
        level (str | int): The minimum level to emit (e.g. ``"INFO"``,
            ``logging.DEBUG``).
        json_output (bool): When ``True`` (default), emit JSON via
            :class:`JSONFormatter` to stdout. When ``False``, fall back
            to a human-readable text formatter — useful in local dev
            where JSON noise overwhelms the terminal. Only affects
            stdout; files are always JSON.
        logger_name (str | None): The logger to configure. ``None``
            (default) configures the root logger.
        log_dir (str | Path | None): When set (and non-empty), enable
            per-level + ``500.log`` file logging under this directory
            (created if missing). ``None`` or empty disables file
            logging — stdout only.

    Returns:
        logging.Logger: The configured logger instance.
    """
    logger = logging.getLogger(logger_name)
    logger.setLevel(level)
    for handler in list(logger.handlers):
        logger.removeHandler(handler)

    stream_handler = logging.StreamHandler(sys.stdout)
    if json_output:
        stream_handler.setFormatter(JSONFormatter())
    else:
        stream_handler.setFormatter(
            logging.Formatter(
                fmt="%(asctime)s %(levelname)s %(name)s %(message)s",
                datefmt="%Y-%m-%dT%H:%M:%S",
            )
        )
    logger.addHandler(stream_handler)

    if log_dir:
        for file_handler in _build_file_handlers(Path(log_dir)):
            logger.addHandler(file_handler)

    logger.propagate = False
    return logger


__all__: list[str] = [
    "HTTP_500_LOG_FILE",
    "HTTP_500_MARKER",
    "LEVEL_LOG_FILES",
    "JSONFormatter",
    "configure_logging",
]
