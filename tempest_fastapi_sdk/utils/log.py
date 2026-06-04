"""Unified logging facade — class wrapper around ``configure_logging``."""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Any

from tempest_fastapi_sdk.core.context import get_request_id
from tempest_fastapi_sdk.core.logging import configure_logging


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
    ) -> None:
        """Configure and bind a logger to this instance.

        Mirrors :func:`configure_logging` defaults — stdout *and* file
        output are enabled out of the box, writing under ``logs/``.

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
        """
        self.name: str = name
        self.logger: logging.Logger = configure_logging(
            level=level,
            json_output=json_output,
            logger_name=name,
            log_dir=log_dir,
            stdout=stdout,
            file_output=file_output,
        )

    @staticmethod
    def configure(
        level: str | int = "INFO",
        *,
        json_output: bool = True,
        logger_name: str | None = None,
        log_dir: str | Path | None = "logs",
        stdout: bool = True,
        file_output: bool = True,
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

    def info(self, message: str, **fields: Any) -> None:
        """Emit an INFO record.

        Args:
            message (str): The log message.
            **fields (Any): Extra structured fields merged into the
                JSON payload.
        """
        self.logger.info(message, extra=fields)

    def debug(self, message: str, **fields: Any) -> None:
        """Emit a DEBUG record.

        Args:
            message (str): The log message.
            **fields (Any): Extra structured fields.
        """
        self.logger.debug(message, extra=fields)

    def warning(self, message: str, **fields: Any) -> None:
        """Emit a WARNING record.

        Args:
            message (str): The log message.
            **fields (Any): Extra structured fields.
        """
        self.logger.warning(message, extra=fields)

    def error(self, message: str, **fields: Any) -> None:
        """Emit an ERROR record.

        Args:
            message (str): The log message.
            **fields (Any): Extra structured fields.
        """
        self.logger.error(message, extra=fields)

    def critical(self, message: str, **fields: Any) -> None:
        """Emit a CRITICAL record.

        Args:
            message (str): The log message.
            **fields (Any): Extra structured fields.
        """
        self.logger.critical(message, extra=fields)

    def exception(self, message: str, **fields: Any) -> None:
        """Emit an ERROR record with the current exception traceback.

        Must be called from inside an ``except`` block — relies on
        ``logger.exception`` which inspects ``sys.exc_info()``.

        Args:
            message (str): The log message.
            **fields (Any): Extra structured fields.
        """
        self.logger.exception(message, extra=fields)


__all__: list[str] = [
    "LogUtils",
]
