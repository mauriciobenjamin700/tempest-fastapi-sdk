"""Log slow SQL statements via SQLAlchemy engine events.

``SlowQueryLogger`` attaches ``before_cursor_execute`` /
``after_cursor_execute`` listeners to a SQLAlchemy engine (sync or
async) and emits a structured log line whenever a statement takes
longer than a configurable threshold. It is the cheapest way to find
the N+1 query or the missing index that is dragging p99 latency,
without reaching for an APM agent.

Design notes:

* **Per-connection timing.** The start timestamp is stashed on the
  DBAPI connection's ``info`` dict (keyed by a private name) so
  concurrent statements on different connections never clobber each
  other. SQLAlchemy guarantees ``before``/``after`` fire on the same
  connection.
* **Async engines too.** ``AsyncEngine`` wraps a sync engine reachable
  via ``.sync_engine`` — the listeners attach there, so the same class
  instruments both. Pass either; the wiring is transparent.
* **Parameters are never logged by default.** Bind parameters often
  carry secrets / PII, so they stay out of the log line unless the
  caller opts in with ``log_parameters=True`` (development only).
* **Optional ``EXPLAIN``.** When ``explain=True`` the logger runs
  ``EXPLAIN <statement>`` on a fresh connection for every slow query
  and appends the plan. This costs an extra round-trip per slow query,
  so keep it off in production unless you are actively debugging.
"""

from __future__ import annotations

import logging
import time
from typing import TYPE_CHECKING, Any, cast

from sqlalchemy import event

if TYPE_CHECKING:
    from sqlalchemy.engine import Engine
    from sqlalchemy.ext.asyncio import AsyncEngine

logger = logging.getLogger("tempest_fastapi_sdk.db.slow_query")

# Key under which the per-statement start time is stashed on the
# connection's ``info`` dict. Private name avoids colliding with any
# other listener that uses ``conn.info``.
_START_KEY: str = "_tempest_slow_query_start"


class SlowQueryLogger:
    """Emit a log line for every SQL statement slower than a threshold.

    Attach once per engine at application boot, before the engine
    serves traffic. The listeners stay registered until
    :meth:`detach` is called (or the engine is disposed).

    Example:
        >>> from tempest_fastapi_sdk.db import AsyncDatabaseManager
        >>> from tempest_fastapi_sdk.db.slow_query import SlowQueryLogger
        >>> db = AsyncDatabaseManager("sqlite+aiosqlite://")
        >>> await db.connect()
        >>> slow = SlowQueryLogger(db.engine, threshold_ms=100.0)
        >>> slow.attach()

    Attributes:
        threshold_ms (float): Statements at or above this wall-clock
            duration (in milliseconds) are logged.
        level (int): The logging level used for slow-query lines.
        log_parameters (bool): Whether to include bind parameters in
            the log line. Off by default (parameters may carry PII).
        explain (bool): Whether to run ``EXPLAIN`` and append the plan.
    """

    def __init__(
        self,
        engine: Engine | AsyncEngine,
        *,
        threshold_ms: float = 500.0,
        level: int = logging.WARNING,
        log_parameters: bool = False,
        explain: bool = False,
    ) -> None:
        """Initialize the logger (does not attach listeners yet).

        Args:
            engine (Engine | AsyncEngine): The engine to instrument.
                ``AsyncEngine`` is unwrapped to its ``.sync_engine``.
            threshold_ms (float): Duration in milliseconds at or above
                which a statement is logged. Defaults to ``500.0``.
            level (int): Logging level for slow-query lines. Defaults
                to :data:`logging.WARNING`.
            log_parameters (bool): When ``True``, append the bind
                parameters to the log line. Development only — bind
                parameters may carry secrets / PII. Defaults to
                ``False``.
            explain (bool): When ``True``, run ``EXPLAIN <statement>``
                on a fresh connection per slow query and append the
                plan. Adds a round-trip per slow query. Defaults to
                ``False``.

        Raises:
            ValueError: If ``threshold_ms`` is negative.
        """
        if threshold_ms < 0:
            raise ValueError("threshold_ms must be >= 0")

        self.threshold_ms: float = threshold_ms
        self.level: int = level
        self.log_parameters: bool = log_parameters
        self.explain: bool = explain
        self._sync_engine: Engine = _to_sync_engine(engine)
        self._attached: bool = False

    def attach(self) -> None:
        """Register the timing listeners on the engine.

        Idempotent — calling twice is a no-op.
        """
        if self._attached:
            return
        event.listen(self._sync_engine, "before_cursor_execute", self._before)
        event.listen(self._sync_engine, "after_cursor_execute", self._after)
        self._attached = True

    def detach(self) -> None:
        """Remove the timing listeners from the engine.

        Idempotent — calling twice (or before :meth:`attach`) is a
        no-op.
        """
        if not self._attached:
            return
        event.remove(self._sync_engine, "before_cursor_execute", self._before)
        event.remove(self._sync_engine, "after_cursor_execute", self._after)
        self._attached = False

    def _before(
        self,
        conn: Any,
        cursor: Any,
        statement: str,
        parameters: Any,
        context: Any,
        executemany: bool,
    ) -> None:
        """Stash the start time on the connection's ``info`` dict.

        Args:
            conn (Any): The DBAPI connection wrapper.
            cursor (Any): The DBAPI cursor (unused).
            statement (str): The SQL text (unused here).
            parameters (Any): The bind parameters (unused here).
            context (Any): The execution context (unused).
            executemany (bool): Whether this is an ``executemany`` call.
        """
        conn.info[_START_KEY] = time.perf_counter()

    def _after(
        self,
        conn: Any,
        cursor: Any,
        statement: str,
        parameters: Any,
        context: Any,
        executemany: bool,
    ) -> None:
        """Compute elapsed time and log when above the threshold.

        Args:
            conn (Any): The DBAPI connection wrapper.
            cursor (Any): The DBAPI cursor (unused).
            statement (str): The SQL text that executed.
            parameters (Any): The bind parameters.
            context (Any): The execution context (unused).
            executemany (bool): Whether this was an ``executemany`` call.
        """
        start = conn.info.pop(_START_KEY, None)
        if start is None:
            return
        elapsed_ms = (time.perf_counter() - start) * 1000.0
        if elapsed_ms < self.threshold_ms:
            return

        message = "slow query: %.1fms >= %.1fms threshold | %s"
        args: list[Any] = [elapsed_ms, self.threshold_ms, _collapse(statement)]
        if self.log_parameters:
            message += " | params=%r"
            args.append(parameters)
        if self.explain:
            plan = self._run_explain(conn, statement, parameters)
            if plan:
                message += " | plan=%s"
                args.append(plan)

        logger.log(self.level, message, *args)

    def _run_explain(self, conn: Any, statement: str, parameters: Any) -> str | None:
        """Run ``EXPLAIN`` for a slow statement and return the plan text.

        Swallows every error (a failed EXPLAIN must never break the
        request that triggered it) and returns ``None`` on failure.

        Args:
            conn (Any): The DBAPI connection wrapper to borrow.
            statement (str): The SQL text to explain.
            parameters (Any): The bind parameters to pass through.

        Returns:
            str | None: The collapsed plan text, or ``None`` on failure.
        """
        try:
            cursor = conn.connection.cursor()
            try:
                cursor.execute(f"EXPLAIN {statement}", parameters or ())
                rows = cursor.fetchall()
            finally:
                cursor.close()
        except Exception:  # pragma: no cover - backend-specific failure
            return None
        return _collapse(" | ".join(str(row) for row in rows))


def _to_sync_engine(engine: Engine | AsyncEngine) -> Engine:
    """Return the underlying sync engine for sync or async engines.

    Args:
        engine (Engine | AsyncEngine): The engine to unwrap.

    Returns:
        Engine: ``engine.sync_engine`` for an ``AsyncEngine``, else
        ``engine`` unchanged.
    """
    sync_engine = getattr(engine, "sync_engine", None)
    if sync_engine is not None:
        return cast("Engine", sync_engine)
    return cast("Engine", engine)


def _collapse(statement: str) -> str:
    """Collapse whitespace in a SQL statement into single spaces.

    Args:
        statement (str): The raw SQL text.

    Returns:
        str: The statement with runs of whitespace collapsed.
    """
    return " ".join(statement.split())


__all__: list[str] = [
    "SlowQueryLogger",
]
