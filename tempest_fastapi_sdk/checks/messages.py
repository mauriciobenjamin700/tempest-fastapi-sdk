"""Check severity levels and the message a check emits."""

from __future__ import annotations

from dataclasses import dataclass

from tempest_fastapi_sdk.core.enums import BaseIntEnum


class CheckLevel(BaseIntEnum):
    """Severity of a system-check message (mirrors logging levels).

    A run "fails" when any message reaches the configured threshold
    (``ERROR`` by default), so ``WARNING`` surfaces advice without
    blocking startup while ``ERROR`` / ``CRITICAL`` do block it.
    """

    DEBUG = 10
    INFO = 20
    WARNING = 30
    ERROR = 40
    CRITICAL = 50


@dataclass(frozen=True, slots=True)
class CheckMessage:
    """One finding produced by a system check.

    Attributes:
        level (CheckLevel): The severity.
        message (str): What is wrong (or noteworthy).
        hint (str | None): How to fix it, shown after the message.
        id (str | None): A stable identifier (e.g. ``"security.W001"``)
            for allow-listing or documentation.
    """

    level: CheckLevel
    message: str
    hint: str | None = None
    id: str | None = None

    def is_serious(self, threshold: CheckLevel = CheckLevel.ERROR) -> bool:
        """Return whether this message reaches ``threshold``.

        Args:
            threshold (CheckLevel): The level at or above which a
                message is considered serious. Defaults to ``ERROR``.

        Returns:
            bool: ``True`` when ``self.level >= threshold``.
        """
        return self.level >= threshold

    def __str__(self) -> str:
        """Render as ``LEVEL: message [id]`` plus an indented hint.

        Returns:
            str: The human-readable one-to-two line rendering.
        """
        label = f"({self.id}) " if self.id else ""
        head = f"{self.level.name}: {label}{self.message}"
        if self.hint:
            return f"{head}\n\tHINT: {self.hint}"
        return head


def debug(
    message: str, *, hint: str | None = None, id: str | None = None
) -> CheckMessage:
    """Build a ``DEBUG`` message. See :class:`CheckMessage`."""
    return CheckMessage(CheckLevel.DEBUG, message, hint=hint, id=id)


def info(
    message: str, *, hint: str | None = None, id: str | None = None
) -> CheckMessage:
    """Build an ``INFO`` message. See :class:`CheckMessage`."""
    return CheckMessage(CheckLevel.INFO, message, hint=hint, id=id)


def warning(
    message: str, *, hint: str | None = None, id: str | None = None
) -> CheckMessage:
    """Build a ``WARNING`` message. See :class:`CheckMessage`."""
    return CheckMessage(CheckLevel.WARNING, message, hint=hint, id=id)


def error(
    message: str, *, hint: str | None = None, id: str | None = None
) -> CheckMessage:
    """Build an ``ERROR`` message. See :class:`CheckMessage`."""
    return CheckMessage(CheckLevel.ERROR, message, hint=hint, id=id)


def critical(
    message: str, *, hint: str | None = None, id: str | None = None
) -> CheckMessage:
    """Build a ``CRITICAL`` message. See :class:`CheckMessage`."""
    return CheckMessage(CheckLevel.CRITICAL, message, hint=hint, id=id)


__all__: list[str] = [
    "CheckLevel",
    "CheckMessage",
    "critical",
    "debug",
    "error",
    "info",
    "warning",
]
