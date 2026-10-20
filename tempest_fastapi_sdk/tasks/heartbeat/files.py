"""Heartbeat file writing, reading and the healthcheck command.

A container healthcheck for a worker can only ask the broker whether it
is reachable (:meth:`~tempest_fastapi_sdk.tasks.TaskQueue.health_check`),
and a worker stuck on a hung task, or a scheduler whose loop stopped,
still has a perfectly reachable broker. A heartbeat file closes that gap:
the process proves it is doing its job by rewriting a timestamp, and the
healthcheck asks how old the timestamp is.

:meth:`~tempest_fastapi_sdk.tasks.TaskQueue.enable_heartbeat` wires the
writers. This module holds what the healthcheck needs, and it imports
nothing outside the standard library, so the checker runs with only the
base package installed::

    python -m tempest_fastapi_sdk.tasks.heartbeat /tmp/worker.heartbeat --max-age 90

The command exits ``0`` when the file exists and its timestamp is younger
than ``--max-age`` seconds, ``1`` otherwise, and prints one line saying
why.
"""

from __future__ import annotations

import argparse
import contextlib
import logging
import os
import sys
import tempfile
from collections.abc import Sequence
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Final

logger = logging.getLogger("tempest_fastapi_sdk.tasks.heartbeat")

HEARTBEAT_TASK_NAME: Final[str] = "tempest:heartbeat"
"""Task name of the heartbeat the scheduler sends and the worker runs."""

HEARTBEAT_SCHEDULE_ID: Final[str] = "tempest-heartbeat"
"""Fixed ``schedule_id`` of the heartbeat schedule.

Fixed rather than random so the scheduler source recognises its own
schedule in ``post_send`` and every scheduler start reuses the same id.
"""

DEFAULT_HEARTBEAT_EVERY: Final[timedelta] = timedelta(seconds=30)
"""Default period between two heartbeats."""


def write_heartbeat(path: str | Path, *, now: datetime | None = None) -> None:
    """Atomically write the current UTC time to ``path``.

    The timestamp goes to a temporary file in the same directory, which
    then replaces ``path`` with :func:`os.replace`. A reader therefore
    sees the previous timestamp or the new one, never a half-written
    file — which a healthcheck running every few seconds would otherwise
    hit sooner or later and report as a failure.

    Args:
        path (str | Path): The heartbeat file. Its directory must exist.
        now (datetime | None): The instant to record; defaults to the
            current time. A naive value is read as UTC.

    Raises:
        OSError: When the directory is missing, read-only or full.
    """
    target = Path(path)
    moment = now or datetime.now(UTC)
    if moment.tzinfo is None:
        moment = moment.replace(tzinfo=UTC)
    payload = moment.astimezone(UTC).isoformat().replace("+00:00", "Z") + "\n"
    descriptor, temporary = tempfile.mkstemp(
        prefix=f".{target.name}.", suffix=".tmp", dir=target.parent
    )
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
            handle.write(payload)
        os.replace(temporary, target)
    except BaseException:
        with contextlib.suppress(OSError):
            os.unlink(temporary)
        raise


def read_heartbeat(path: str | Path) -> datetime | None:
    """Read the timestamp a heartbeat file holds.

    Args:
        path (str | Path): The heartbeat file.

    Returns:
        datetime | None: The recorded instant (timezone-aware, UTC), or
        ``None`` when the file is missing, unreadable or does not hold a
        timestamp.
    """
    try:
        raw = Path(path).read_text(encoding="utf-8").strip()
    except (OSError, UnicodeDecodeError):
        return None
    try:
        moment = datetime.fromisoformat(raw.replace("Z", "+00:00"))
    except ValueError:
        return None
    if moment.tzinfo is None:
        moment = moment.replace(tzinfo=UTC)
    return moment


def heartbeat_age(path: str | Path, *, now: datetime | None = None) -> float | None:
    """Return how many seconds ago the heartbeat in ``path`` was written.

    The age comes from the timestamp **inside** the file, not from its
    modification time, so copying or touching the file does not make a
    dead process look alive.

    Args:
        path (str | Path): The heartbeat file.
        now (datetime | None): The reference instant; defaults to the
            current time.

    Returns:
        float | None: The age in seconds (negative when the file is from
        the future, e.g. clock skew between containers sharing a volume),
        or ``None`` when there is no readable heartbeat.
    """
    moment = read_heartbeat(path)
    if moment is None:
        return None
    reference = now or datetime.now(UTC)
    if reference.tzinfo is None:
        reference = reference.replace(tzinfo=UTC)
    return (reference - moment).total_seconds()


def is_heartbeat_fresh(
    path: str | Path,
    max_age: float,
    *,
    now: datetime | None = None,
) -> bool:
    """Return whether the heartbeat in ``path`` is younger than ``max_age``.

    Args:
        path (str | Path): The heartbeat file.
        max_age (float): The oldest acceptable heartbeat, in seconds. Pick
            about three times the heartbeat period, so one late beat does
            not flip the container to unhealthy.
        now (datetime | None): The reference instant; defaults to the
            current time.

    Returns:
        bool: ``True`` when the file holds a timestamp at most ``max_age``
        seconds old; ``False`` when it is older, missing or unreadable.
    """
    age = heartbeat_age(path, now=now)
    return age is not None and age <= max_age


class HeartbeatWriter:
    """Write a heartbeat file without ever failing the caller.

    The writer runs inside a task body or the scheduler loop, where an
    exception would fail the heartbeat task (filling the dead-letter
    sink) or surface in the scheduler's logs every period. A failed
    write is logged as a warning **once**, then silenced until a write
    succeeds again: the healthcheck reports the stale file, which is the
    signal an operator acts on.

    Attributes:
        path (Path): The heartbeat file.
    """

    def __init__(self, path: str | Path) -> None:
        """Initialize the writer.

        Args:
            path (str | Path): The heartbeat file. Its directory must
                exist when the first beat is written.
        """
        self.path: Path = Path(path)
        self._failing: bool = False

    def write(self) -> bool:
        """Write the current time to :attr:`path`.

        Returns:
            bool: ``True`` when the file was written, ``False`` when the
            write failed (already logged).
        """
        try:
            write_heartbeat(self.path)
        except OSError as exc:
            if not self._failing:
                logger.warning(
                    "Cannot write heartbeat file %s: %s. The healthcheck that "
                    "reads it will report this process as unhealthy; further "
                    "failures are not logged until a write succeeds.",
                    self.path,
                    exc,
                )
            self._failing = True
            return False
        self._failing = False
        return True


def main(argv: Sequence[str] | None = None) -> int:
    """Check a heartbeat file, for a container ``HEALTHCHECK``.

    Args:
        argv (Sequence[str] | None): Command-line arguments without the
            program name; defaults to ``sys.argv[1:]``.

    Returns:
        int: ``0`` when the heartbeat is fresh, ``1`` when it is stale,
        missing or unreadable — and also on a usage error, which
        ``argparse`` would report as ``2``: Docker reserves exit code
        ``2`` in a ``HEALTHCHECK``, so a mistyped command must still read
        as "unhealthy". One line explaining the verdict goes to stdout
        (fresh) or stderr (otherwise).
    """
    parser = argparse.ArgumentParser(
        prog="python -m tempest_fastapi_sdk.tasks.heartbeat",
        description="Exit 0 when a heartbeat file is fresh, 1 otherwise.",
    )
    parser.add_argument("file", type=Path, help="the heartbeat file to check")
    parser.add_argument(
        "--max-age",
        type=float,
        required=True,
        help="oldest acceptable heartbeat, in seconds",
    )
    try:
        args = parser.parse_args(argv)
    except SystemExit as exc:
        return 0 if exc.code in (0, None) else 1
    path: Path = args.file
    max_age: float = args.max_age
    if not path.exists():
        print(f"unhealthy: heartbeat file {path} does not exist", file=sys.stderr)
        return 1
    age = heartbeat_age(path)
    if age is None:
        print(
            f"unhealthy: heartbeat file {path} holds no readable timestamp",
            file=sys.stderr,
        )
        return 1
    if age > max_age:
        print(
            f"unhealthy: heartbeat {path} is {age:.1f}s old (max {max_age:g}s)",
            file=sys.stderr,
        )
        return 1
    print(f"healthy: heartbeat {path} is {age:.1f}s old (max {max_age:g}s)")
    return 0


__all__: list[str] = [
    "DEFAULT_HEARTBEAT_EVERY",
    "HEARTBEAT_SCHEDULE_ID",
    "HEARTBEAT_TASK_NAME",
    "HeartbeatWriter",
    "heartbeat_age",
    "is_heartbeat_fresh",
    "main",
    "read_heartbeat",
    "write_heartbeat",
]
