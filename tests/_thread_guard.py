"""Detect ``aiosqlite`` worker threads a test leaves running (#650).

Every ``aiosqlite`` connection runs its own worker thread. An engine that
is never disposed keeps its pooled connections — and their threads — past
the test's event loop; a later ``gc`` pass closes them inside some other
test, whose worker thread then raises ``RuntimeError: Event loop is
closed``. The failure lands on the wrong test and only when ``gc`` happens
to run there, so it reads as a flake. Measured on
``tests/auth/test_auth_pages.py`` + ``test_refresh_db.py`` +
``test_activation_resend.py``: 61 of 94 tests left threads alive.
"""

from __future__ import annotations

import threading

AIOSQLITE_THREAD_MARKER: str = "_connection_worker_thread"
"""What ``aiosqlite``'s worker threads carry in their default name."""

GRACE_SECONDS: float = 1.0
"""How long a thread that is already shutting down may take to finish."""


def aiosqlite_threads() -> set[threading.Thread]:
    """Return the live ``aiosqlite`` worker threads.

    Returns:
        set[threading.Thread]: Threads whose name carries
        :data:`AIOSQLITE_THREAD_MARKER`.
    """
    return {
        thread
        for thread in threading.enumerate()
        if thread.is_alive() and AIOSQLITE_THREAD_MARKER in thread.name
    }


def leaked_since(
    before: set[threading.Thread], *, grace: float = GRACE_SECONDS
) -> list[threading.Thread]:
    """Return the worker threads started since ``before`` that stay alive.

    A connection closed during teardown stops its thread a moment later,
    so each new thread gets up to ``grace`` seconds to finish first.

    Args:
        before (set[threading.Thread]): The threads alive when the test
            started.
        grace (float): Seconds a new thread may take to finish.

    Returns:
        list[threading.Thread]: The new threads still alive after the
        grace period.
    """
    new = aiosqlite_threads() - before
    for thread in new:
        thread.join(timeout=grace)
    return [thread for thread in new if thread.is_alive()]


__all__: list[str] = [
    "AIOSQLITE_THREAD_MARKER",
    "GRACE_SECONDS",
    "aiosqlite_threads",
    "leaked_since",
]
