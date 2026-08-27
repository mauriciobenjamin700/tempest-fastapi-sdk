"""Structured JSON logging with request-ID correlation."""

import contextlib
import json
import logging
import sys
from datetime import UTC, datetime
from logging.handlers import RotatingFileHandler
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

DEFAULT_LOG_MAX_BYTES: int = 10_000_000
"""Size at which each log file rotates — about 10 MB.

Rotation is on by default because the alternative is an outage: the
per-level files never stop growing, and on a service that logs a line per
request `info.log` fills the host's disk. `0` opts out, for a host where
`logrotate` or a sidecar already owns the ceiling.
"""

DEFAULT_LOG_BACKUP_COUNT: int = 5
"""Rotated files kept per level.

Five rotated files plus the one being written is roughly 60 MB per level
at the default :data:`DEFAULT_LOG_MAX_BYTES`.
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


def _rotating_handler(
    path: Path,
    *,
    max_bytes: int,
    backup_count: int,
) -> logging.Handler:
    """Open one log file, rotating unless the caller opted out.

    Args:
        path (Path): The file to write.
        max_bytes (int): Rotation threshold; ``0`` means never rotate.
        backup_count (int): Rotated files to keep.

    Returns:
        logging.Handler: A ``RotatingFileHandler``, or a plain
        ``FileHandler`` when rotation is disabled. ``RotatingFileHandler``
        with ``maxBytes=0`` never rotates either, but the plain handler is
        what a caller reading ``logging.root.handlers`` expects to find
        when they asked for no rotation.
    """
    if max_bytes <= 0:
        return logging.FileHandler(path, encoding="utf-8")
    return RotatingFileHandler(
        path,
        maxBytes=max_bytes,
        backupCount=backup_count,
        encoding="utf-8",
    )


def _build_file_handlers(
    log_dir: Path,
    *,
    max_bytes: int = DEFAULT_LOG_MAX_BYTES,
    backup_count: int = DEFAULT_LOG_BACKUP_COUNT,
) -> list[logging.Handler]:
    """Build the per-level and 500-isolation file handlers.

    Creates ``log_dir`` (and parents) if missing, then wires one
    :class:`~logging.handlers.RotatingFileHandler` per standard level —
    each gated by an :class:`_ExactLevelFilter` so a record only lands in
    its own file — plus a dedicated ``500.log`` handler gated by
    :class:`_Http500Filter`. Every file handler always uses
    :class:`JSONFormatter` (independent of the stdout ``json_output``
    choice) so the ``/logs`` endpoint can parse them back as structured
    records.

    Rotation is on by default because the failure it prevents is an
    outage, not an inconvenience: a plain ``FileHandler`` grows without
    bound, and ``info.log`` on a service that logs one line per request
    fills the disk of a long-lived host — taking down whatever else shares
    it. The reader side of this pair already had the ceiling
    (:data:`DEFAULT_MAX_RECORDS_PER_FILE` in the logs router, added
    because a service whose log directory had grown to gigabytes answered
    with a dead worker); this is the writer side of the same story.

    Args:
        log_dir (Path): Directory to hold the log files. Created if it
            does not exist.
        max_bytes (int): Size at which a file rotates. ``0`` disables
            rotation, for hosts where ``logrotate`` or a sidecar owns it.
        backup_count (int): How many rotated files to keep per level.

    Returns:
        list[logging.Handler]: The configured file handlers.

    Raises:
        OSError: If ``log_dir`` cannot be created or a log file cannot be
            opened (e.g. a read-only or non-writable filesystem). Any
            handlers already opened before the failure are closed before
            the error propagates so no file descriptors leak. Callers that
            want file logging to be best-effort should catch this — see
            :func:`configure_logging`.
    """
    log_dir.mkdir(parents=True, exist_ok=True)
    formatter = JSONFormatter()
    handlers: list[logging.Handler] = []

    try:
        for levelno, filename in LEVEL_LOG_FILES.items():
            file_handler = _rotating_handler(
                log_dir / filename,
                max_bytes=max_bytes,
                backup_count=backup_count,
            )
            file_handler.setLevel(logging.DEBUG)
            file_handler.addFilter(_ExactLevelFilter(levelno))
            file_handler.setFormatter(formatter)
            handlers.append(file_handler)

        http_500_handler = _rotating_handler(
            log_dir / HTTP_500_LOG_FILE,
            max_bytes=max_bytes,
            backup_count=backup_count,
        )
        http_500_handler.setLevel(logging.DEBUG)
        http_500_handler.addFilter(_Http500Filter())
        http_500_handler.setFormatter(formatter)
        handlers.append(http_500_handler)
    except OSError:
        for handler in handlers:
            with contextlib.suppress(Exception):
                handler.close()
        raise

    return handlers


def configure_logging(
    level: str | int = "INFO",
    *,
    json_output: bool = True,
    logger_name: str | None = None,
    log_dir: str | Path | None = "logs",
    stdout: bool = True,
    file_output: bool = True,
    max_bytes: int = DEFAULT_LOG_MAX_BYTES,
    backup_count: int = DEFAULT_LOG_BACKUP_COUNT,
) -> logging.Logger:
    """Install a structured stdout handler on the root (or named) logger.

    Replaces existing handlers on the target logger so this can be
    called safely from ``create_app`` without stacking duplicates.

    **Default behavior** (no extra kwargs): emits to **both** stdout
    and a ``logs/`` directory next to the service root, one file per
    level plus a dedicated ``500.log``:

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
        log_dir (str | Path | None): Directory for per-level + ``500.log``
            files. Defaults to ``"logs"`` so file logging works out of
            the box. Pass ``None`` or an empty string to disable file
            logging (equivalent to ``file_output=False``). Ignored when
            ``file_output=False``.
        stdout (bool): When ``True`` (default), attach the stdout
            handler. Pass ``False`` to suppress terminal output
            entirely (e.g. when a sidecar collects logs from disk).
        file_output (bool): When ``True`` (default), attach the
            per-level + ``500.log`` file handlers under ``log_dir``.
            Pass ``False`` to disable file logging — useful in
            ephemeral environments (tests, serverless) where the
            filesystem is read-only or short-lived.
        max_bytes (int): Size at which each file rotates, ~10 MB by
            default. ``0`` turns rotation off, leaving plain
            ``FileHandler``s for a host where ``logrotate`` or a sidecar
            owns retention. Rotating by default is the safe end of that
            choice: the service that never thought about log growth is
            exactly the one that fills the disk.
        backup_count (int): Rotated files kept per level (default ``5``,
            so roughly 60 MB per level at the default size).

    File logging is **best-effort**: if ``log_dir`` cannot be created or
    its files cannot be opened (read-only mount, missing write
    permission, etc.), the file handlers are skipped, a warning is
    emitted, and the application keeps running with stdout logging — it
    does **not** crash at startup. Pass ``file_output=False`` to opt out
    of file logging explicitly when you know the filesystem is unusable.

    Returns:
        logging.Logger: The configured logger instance.

    Raises:
        ValueError: When both ``stdout=False`` and ``file_output=False``
            are passed — that would silence every handler and leave
            the application blind, which is almost always a mistake.

    Notes:
        Existing handlers are **closed** before being removed, not just
        detached. This runs once per application boot and once per test that
        wants a clean state, and ``FileHandler`` / ``RotatingFileHandler``
        hold a file descriptor each — without the close they would
        accumulate across calls.

        File logging is best-effort. A read-only or non-writable filesystem
        is normal in hardened containers, serverless runtimes and CI, and
        must never crash the application at startup, so the failure degrades
        to stdout-only and the reason is surfaced once for an operator who
        did want files. When there is no stdout handler to carry that
        warning, it goes straight to stderr rather than being swallowed.
    """
    if not stdout and not file_output:
        raise ValueError(
            "configure_logging: stdout=False and file_output=False "
            "would silence every handler. Pick at least one."
        )

    logger = logging.getLogger(logger_name)
    logger.setLevel(level)
    for handler in list(logger.handlers):
        with contextlib.suppress(Exception):
            handler.close()
        logger.removeHandler(handler)

    if stdout:
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

    if file_output and log_dir:
        try:
            file_handlers = _build_file_handlers(
                Path(log_dir),
                max_bytes=max_bytes,
                backup_count=backup_count,
            )
        except OSError as exc:
            msg = (
                "tempest_fastapi_sdk: file logging disabled — could not "
                f"prepare log_dir {str(log_dir)!r}: {exc}. Continuing "
                "with stdout logging only."
            )
            if stdout:
                logger.warning(msg)
            else:
                print(msg, file=sys.stderr)
        else:
            for file_handler in file_handlers:
                logger.addHandler(file_handler)

    logger.propagate = False
    return logger


__all__: list[str] = [
    "DEFAULT_LOG_BACKUP_COUNT",
    "DEFAULT_LOG_MAX_BYTES",
    "HTTP_500_LOG_FILE",
    "HTTP_500_MARKER",
    "LEVEL_LOG_FILES",
    "JSONFormatter",
    "configure_logging",
]
