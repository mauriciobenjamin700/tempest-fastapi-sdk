"""Unified logging facade — class wrapper around ``configure_logging``."""

from __future__ import annotations

import logging
import sys
from pathlib import Path
from typing import Any, Literal

from tempest_fastapi_sdk.core.context import get_request_id
from tempest_fastapi_sdk.core.logging import (
    DEFAULT_LOG_BACKUP_COUNT,
    DEFAULT_LOG_MAX_BYTES,
    HTTP_500_MARKER,
    configure_logging,
    configure_root_once,
)


def _handling_exception() -> bool:
    """Return whether an exception is currently being handled.

    Args:
        None.

    Returns:
        bool: ``True`` inside an ``except`` block, which is when a
        traceback is worth attaching to the record.
    """
    return sys.exc_info()[0] is not None


class LogUtils:
    """High-level logging facade used across SDK consumers.

    Wraps :func:`tempest_fastapi_sdk.configure_logging` so callers can
    obtain a fully configured JSON logger with one line, and exposes
    structured ``info``/``warning``/``error``/``debug``/``exception``
    methods that forward ``**fields`` as top-level keys on the JSON
    payload via Python's ``logging.LogRecord.extra``.

    The class can be used in two flavors:

    * Instance API — keeps a configured logger as state and exposes
      level methods directly. Recommended for service-wide singletons.
    * Static helpers — :meth:`configure` and :meth:`get_logger` for
      ad-hoc configuration without tying state to an object.

    Attributes:
        logger (logging.Logger): The configured stdlib logger.
        name (str): The logger name.
    """

    def __init__(
        self,
        name: str,
        *,
        level: str | int = "INFO",
        json_output: bool = True,
        log_dir: str | Path | None = "logs",
        stdout: bool = True,
        file_output: bool = True,
        max_bytes: int = DEFAULT_LOG_MAX_BYTES,
        backup_count: int = DEFAULT_LOG_BACKUP_COUNT,
        scope: Literal["root", "logger"] = "root",
    ) -> None:
        """Configure and bind a logger to this instance.

        Mirrors :func:`configure_logging` defaults — stdout *and* file
        output are enabled out of the box, writing under ``logs/``.

        ``scope="root"`` is what makes ``LogUtils(__name__)`` safe to
        write in every module, which is how the class reads and how it
        gets used. The handlers land on the **root** logger, once per
        process, and each module's logger propagates into them. Under
        the old per-logger behavior a service with N modules opened N
        stdout handlers and ``N * 6`` file handlers on the same six
        paths, with that many ``RotatingFileHandler`` instances racing
        to roll one file over.

        Args:
            name (str): Logger name. Typically ``__name__`` of the
                root module, or the service name.
            level (str | int): Minimum log level to emit. Accepts
                stdlib names (``"INFO"``, ``"DEBUG"``) or integers.
            json_output (bool): When ``True`` (default), structured
                JSON output via :class:`JSONFormatter`. When ``False``,
                a human-readable text formatter.
            log_dir (str | Path | None): Directory for per-level files.
                Defaults to ``"logs"``. Pass ``None`` to disable file
                logging.
            stdout (bool): Attach the stdout handler. Defaults to
                ``True``.
            file_output (bool): Attach the per-level + ``500.log`` file
                handlers under ``log_dir``. Defaults to ``True``.
            max_bytes (int): Size at which each file rotates. ``0``
                disables rotation.
            backup_count (int): Rotated files kept per level.
            scope (Literal["root", "logger"]): Which logger receives the
                handlers. ``"root"`` (default) configures the root
                logger once per process and binds ``name`` to propagate
                into it. ``"logger"`` gives ``name`` its own handler set
                with ``propagate = False`` — the pre-0.280.0 behavior,
                for a process that deliberately isolates one logger's
                output.
        """
        self.name: str = name
        options: dict[str, Any] = {
            "level": level,
            "json_output": json_output,
            "log_dir": log_dir,
            "stdout": stdout,
            "file_output": file_output,
            "max_bytes": max_bytes,
            "backup_count": backup_count,
        }
        if scope == "root":
            configure_root_once(**options)
            self.logger = logging.getLogger(name)
        else:
            self.logger = configure_logging(logger_name=name, **options)

    @staticmethod
    def configure(
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
        """Imperative shortcut for :func:`configure_logging`.

        Forwards every keyword to :func:`configure_logging` so the two
        share defaults — stdout *and* file output enabled, ``logs/``
        directory used unless overridden.

        Args:
            level (str | int): Minimum log level.
            json_output (bool): Emit JSON when ``True``.
            logger_name (str | None): Target logger; ``None`` configures
                the root logger.
            log_dir (str | Path | None): Directory for per-level files.
                Defaults to ``"logs"``. Pass ``None`` to disable file
                logging.
            stdout (bool): Attach the stdout handler. Defaults to
                ``True``.
            file_output (bool): Attach the per-level + ``500.log`` file
                handlers under ``log_dir``. Defaults to ``True``.
            max_bytes (int): Size at which each file rotates. ``0``
                disables rotation.
            backup_count (int): Rotated files kept per level.

        Returns:
            logging.Logger: The configured logger.
        """
        return configure_logging(
            level=level,
            json_output=json_output,
            logger_name=logger_name,
            log_dir=log_dir,
            stdout=stdout,
            file_output=file_output,
            max_bytes=max_bytes,
            backup_count=backup_count,
        )

    @staticmethod
    def get_logger(name: str) -> logging.Logger:
        """Return the stdlib logger named ``name`` without reconfiguring.

        Args:
            name (str): The logger name.

        Returns:
            logging.Logger: The (possibly unconfigured) logger.
        """
        return logging.getLogger(name)

    @staticmethod
    def current_request_id() -> str | None:
        """Return the current request ID from the contextvar.

        Useful when callers want to surface the correlation ID outside
        the log line (e.g. in an HTTP response body).

        Returns:
            str | None: The active request ID, or ``None``.
        """
        return get_request_id()

    def info(
        self,
        message: str,
        *args: Any,
        stacklevel: int = 2,
        **fields: Any,
    ) -> None:
        """Emit an INFO record.

        Args:
            message (str): The log message, or a ``%``-style template
                when ``args`` are given.
            *args (Any): Positional arguments for ``%``-style
                interpolation, forwarded to ``logging`` untouched — the
                interpolation stays lazy, and the template stays the same
                string across calls, which is what a log aggregator groups
                on.
            stacklevel (int): How many frames to walk back when resolving
                ``funcName``/``lineno``. The default of ``2`` points the
                record at **your** call site instead of at this facade.
            **fields (Any): Extra structured fields merged into the
                JSON payload.
        """
        self.logger.info(message, *args, extra=fields, stacklevel=stacklevel)

    def debug(
        self,
        message: str,
        *args: Any,
        stacklevel: int = 2,
        **fields: Any,
    ) -> None:
        """Emit a DEBUG record.

        Args:
            message (str): The log message, or a ``%``-style template
                when ``args`` are given.
            *args (Any): Positional arguments for ``%``-style
                interpolation, forwarded to ``logging`` untouched — the
                interpolation stays lazy, and the template stays the same
                string across calls, which is what a log aggregator groups
                on.
            stacklevel (int): How many frames to walk back when resolving
                ``funcName``/``lineno``. The default of ``2`` points the
                record at **your** call site instead of at this facade.
            **fields (Any): Extra structured fields.
        """
        self.logger.debug(message, *args, extra=fields, stacklevel=stacklevel)

    def warning(
        self,
        message: str,
        *args: Any,
        stacklevel: int = 2,
        **fields: Any,
    ) -> None:
        """Emit a WARNING record.

        Args:
            message (str): The log message, or a ``%``-style template
                when ``args`` are given.
            *args (Any): Positional arguments for ``%``-style
                interpolation, forwarded to ``logging`` untouched — the
                interpolation stays lazy, and the template stays the same
                string across calls, which is what a log aggregator groups
                on.
            stacklevel (int): How many frames to walk back when resolving
                ``funcName``/``lineno``. The default of ``2`` points the
                record at **your** call site instead of at this facade.
            **fields (Any): Extra structured fields.
        """
        self.logger.warning(message, *args, extra=fields, stacklevel=stacklevel)

    def error(
        self,
        message: str,
        *args: Any,
        stacklevel: int = 2,
        exc_info: bool | Literal["auto"] = False,
        **fields: Any,
    ) -> None:
        """Emit an ERROR record.

        Args:
            message (str): The log message, or a ``%``-style template
                when ``args`` are given.
            *args (Any): Positional arguments for ``%``-style
                interpolation, forwarded to ``logging`` untouched — the
                interpolation stays lazy, and the template stays the same
                string across calls, which is what a log aggregator groups
                on.
            stacklevel (int): How many frames to walk back when resolving
                ``funcName``/``lineno``. The default of ``2`` points the
                record at **your** call site instead of at this facade.
            exc_info (bool | Literal["auto"]): Whether to attach the
                current traceback. ``"auto"`` attaches it only when an
                exception is being handled, which is what lets one
                helper serve a call site that is sometimes inside an
                ``except`` and sometimes not — :meth:`exception` raises
                the question of the caller's context, and ``True``
                outside an ``except`` writes ``NoneType: None``.
            **fields (Any): Extra structured fields.
        """
        self.logger.error(
            message,
            *args,
            exc_info=_handling_exception() if exc_info == "auto" else exc_info,
            extra=fields,
            stacklevel=stacklevel,
        )

    def error_500(
        self,
        message: str,
        *args: Any,
        stacklevel: int = 2,
        **fields: Any,
    ) -> None:
        """Emit an ERROR record routed to the dedicated ``500.log``.

        ``configure_logging`` writes a ``500.log`` and filters it on the
        :data:`~tempest_fastapi_sdk.core.logging.HTTP_500_MARKER` extra,
        but the only things that set that marker are this SDK's own
        exception handlers — so a service reporting a grave failure of
        its own had to import a private-looking constant from
        ``core.logging`` to reach a file the SDK already writes for it.

        The record also lands in ``error.log`` like any other ERROR. The
        isolated file exists so grave failures are not buried among the
        rest, not to remove them from the level stream.

        The traceback is attached whenever one is being handled, since a
        500 that reports no traceback is the case the file exists for.

        Args:
            message (str): The log message, or a ``%``-style template
                when ``args`` are given.
            *args (Any): Positional arguments for ``%``-style
                interpolation, forwarded to ``logging`` untouched.
            stacklevel (int): How many frames to walk back when resolving
                ``funcName``/``lineno``.
            **fields (Any): Extra structured fields. A ``http_500`` key
                here is overridden by the marker.
        """
        self.logger.error(
            message,
            *args,
            exc_info=_handling_exception(),
            extra={**fields, HTTP_500_MARKER: True},
            stacklevel=stacklevel,
        )

    def critical(
        self,
        message: str,
        *args: Any,
        stacklevel: int = 2,
        **fields: Any,
    ) -> None:
        """Emit a CRITICAL record.

        Args:
            message (str): The log message, or a ``%``-style template
                when ``args`` are given.
            *args (Any): Positional arguments for ``%``-style
                interpolation, forwarded to ``logging`` untouched — the
                interpolation stays lazy, and the template stays the same
                string across calls, which is what a log aggregator groups
                on.
            stacklevel (int): How many frames to walk back when resolving
                ``funcName``/``lineno``. The default of ``2`` points the
                record at **your** call site instead of at this facade.
            **fields (Any): Extra structured fields.
        """
        self.logger.critical(message, *args, extra=fields, stacklevel=stacklevel)

    def exception(
        self,
        message: str,
        *args: Any,
        stacklevel: int = 2,
        **fields: Any,
    ) -> None:
        """Emit an ERROR record with the current exception traceback.

        Must be called from inside an ``except`` block — relies on
        ``logger.exception`` which inspects ``sys.exc_info()``.

        Args:
            message (str): The log message, or a ``%``-style template
                when ``args`` are given.
            *args (Any): Positional arguments for ``%``-style
                interpolation, forwarded to ``logging`` untouched — the
                interpolation stays lazy, and the template stays the same
                string across calls, which is what a log aggregator groups
                on.
            stacklevel (int): How many frames to walk back when resolving
                ``funcName``/``lineno``. The default of ``2`` points the
                record at **your** call site instead of at this facade.
            **fields (Any): Extra structured fields.
        """
        self.logger.exception(message, *args, extra=fields, stacklevel=stacklevel)


__all__: list[str] = [
    "LogUtils",
]
