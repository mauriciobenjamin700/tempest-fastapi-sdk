"""Capture the query plans of everything a block of code runs.

A development tool for the question "why is this endpoint slow?", where
the honest answer usually needs the database's own opinion rather than a
wall-clock timing. Wrap the code, get one plan per statement:

```python
async with explain_queries(session) as report:
    await repository.paginate(filters={"status": "open"}, page=3)

for plan in report.plans:
    print(plan.summary())
```

Two backends, two levels of detail, both reported honestly through
:attr:`ExplainReport.detail`:

* **PostgreSQL** — ``EXPLAIN (ANALYZE, BUFFERS, FORMAT JSON)``: planner
  cost, measured time, actual versus estimated row counts. This is the
  one that tells you a sequential scan is reading 400k rows to return 20.
* **SQLite** — ``EXPLAIN QUERY PLAN``: which index (or none) each step
  uses. No costs, no timings; the SDK reports
  :attr:`ExplainDetail.PLAN_ONLY` rather than inventing numbers.

**Writes are never re-executed.** ``EXPLAIN ANALYZE`` runs the statement
it explains, so applying it to the ``INSERT`` your block just performed
would insert a second row. Only ``SELECT`` statements are analyzed;
everything else is explained without ``ANALYZE``, which asks the planner
without touching the data.
"""

from __future__ import annotations

import json
import logging
from collections.abc import AsyncIterator, Sequence
from contextlib import asynccontextmanager
from dataclasses import dataclass, field
from typing import Any, Final

from sqlalchemy import event
from sqlalchemy.engine.interfaces import Dialect
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.sql.elements import ClauseElement, TextClause

from tempest_fastapi_sdk.core.enums import BaseStrEnum
from tempest_fastapi_sdk.db.enum_migrations import SQLITE_DIALECT
from tempest_fastapi_sdk.db.search import POSTGRESQL_DIALECT

POSTGRESQL_ANALYZE_PREFIX: Final[str] = "EXPLAIN (ANALYZE, BUFFERS, FORMAT JSON)"
"""What a ``SELECT`` is explained with on PostgreSQL.

``ANALYZE`` executes the statement, which is what produces measured
times and real row counts instead of estimates. ``BUFFERS`` adds the
page counts that separate "slow because it computed a lot" from "slow
because it read a lot from disk".
"""

POSTGRESQL_PLAN_PREFIX: Final[str] = "EXPLAIN (FORMAT JSON)"
"""What a non-``SELECT`` is explained with: planning only, no execution."""

SQLITE_PLAN_PREFIX: Final[str] = "EXPLAIN QUERY PLAN"
"""SQLite's only plan output. Never executes the statement."""

logger = logging.getLogger(__name__)


class ExplainDetail(BaseStrEnum):
    """How much a backend was able to report.

    Attributes:
        MEASURED: Costs *and* measured execution time — the statement
            was run under ``ANALYZE``.
        ESTIMATED: Planner costs only; the statement was not executed.
            This is what a write gets, deliberately.
        PLAN_ONLY: Step descriptions with neither cost nor timing, which
            is everything SQLite offers.
    """

    MEASURED = "measured"
    ESTIMATED = "estimated"
    PLAN_ONLY = "plan_only"


@dataclass(frozen=True)
class QueryPlan:
    """One statement's plan, as the database described it.

    Attributes:
        sql (str): The statement as compiled for the backend.
        detail (ExplainDetail): How much the backend reported.
        plan_text (str): The plan, rendered for reading.
        total_cost (float | None): The planner's cost estimate for the
            whole statement. ``None`` when the backend has no cost model.
        duration_ms (float | None): Measured execution time. ``None``
            unless the statement was analyzed.
        rows (int | None): Rows the top node actually produced (measured)
            or is expected to produce (estimated).
        raw (Any): The backend's untouched output, for anything the
            fields above do not carry.
    """

    sql: str
    detail: ExplainDetail
    plan_text: str
    total_cost: float | None = None
    duration_ms: float | None = None
    rows: int | None = None
    raw: Any = None

    def summary(self) -> str:
        """Render a one-line summary suitable for a log or a console.

        Returns:
            str: The available metrics followed by the truncated SQL.
        """
        parts: list[str] = []
        if self.duration_ms is not None:
            parts.append(f"{self.duration_ms:.2f}ms")
        if self.total_cost is not None:
            parts.append(f"cost={self.total_cost:.1f}")
        if self.rows is not None:
            parts.append(f"rows={self.rows}")
        parts.append(f"[{self.detail.value}]")
        statement = " ".join(self.sql.split())
        if len(statement) > 120:
            statement = f"{statement[:117]}..."
        return f"{' '.join(parts)} {statement}"


@dataclass
class ExplainReport:
    """Every plan captured inside one :func:`explain_queries` block.

    Populated when the block exits, not while it runs — the statements
    are recorded during the block and explained afterwards, so the
    explaining never interferes with what is being measured.

    Attributes:
        plans (list[QueryPlan]): One entry per captured statement, in
            execution order.
        backend (str): The dialect the plans came from.
    """

    plans: list[QueryPlan] = field(default_factory=list)
    backend: str = ""

    def __len__(self) -> int:
        """Return how many statements were captured.

        Returns:
            int: The plan count.
        """
        return len(self.plans)

    def __iter__(self) -> Any:
        """Iterate the plans in execution order.

        Returns:
            Any: An iterator over :class:`QueryPlan`.
        """
        return iter(self.plans)

    @property
    def slowest(self) -> QueryPlan | None:
        """Return the plan with the highest measured time, if any.

        Falls back to the highest planner cost when nothing was measured,
        so the property still answers "which statement should I look at
        first?" on a backend that cannot time anything.

        Returns:
            QueryPlan | None: The costliest plan, or ``None`` when
            nothing was captured or nothing carries a comparable metric.
        """
        timed = [plan for plan in self.plans if plan.duration_ms is not None]
        if timed:
            return max(timed, key=lambda plan: plan.duration_ms or 0.0)
        costed = [plan for plan in self.plans if plan.total_cost is not None]
        if costed:
            return max(costed, key=lambda plan: plan.total_cost or 0.0)
        return None

    @property
    def total_duration_ms(self) -> float | None:
        """Return the summed measured time across every analyzed plan.

        Returns:
            float | None: The total, or ``None`` when nothing was timed.
        """
        timed = [
            plan.duration_ms for plan in self.plans if plan.duration_ms is not None
        ]
        return sum(timed) if timed else None

    def report(self) -> str:
        """Render every plan as one summary line each.

        Returns:
            str: The lines, newline-joined; a placeholder when empty.
        """
        if not self.plans:
            return "no statements captured"
        return "\n".join(plan.summary() for plan in self.plans)


def _compile_for_driver(
    statement: ClauseElement,
    dialect: Dialect,
    prefix: str,
) -> tuple[str, Any]:
    """Compile a statement into driver-ready ``EXPLAIN`` SQL and parameters.

    The plan has to be fetched with the driver directly rather than
    through ``session.execute``, because SQLAlchemy would apply the
    *wrapped* statement's result mapping to the plan rows: explaining a
    ``select(OrderModel)`` hands the ``id`` column's UUID processor the
    integer node id from the plan output, and the whole capture dies with
    an unrelated-looking ``AttributeError``.

    Bind parameters are kept as parameters instead of being inlined, so
    the plan describes the query as the application actually issues it.
    The dialect's own bind processors are applied first, since bypassing
    ``session.execute`` also bypasses the conversion a driver expects
    (a ``UUID`` object reaching SQLite, for instance).

    Args:
        statement (ClauseElement): The statement to explain.
        dialect (Dialect): The bound dialect, which decides both the SQL
            and whether parameters are positional or named.
        prefix (str): The backend's ``EXPLAIN`` prefix.

    Returns:
        tuple[str, Any]: The SQL, and the parameters shaped the way the
        driver expects them.
    """
    compiled = statement.compile(dialect=dialect)
    values: dict[str, Any] = dict(compiled.params)
    processors: dict[str, Any] = getattr(compiled, "_bind_processors", {}) or {}
    for key, processor in processors.items():
        if key in values and processor is not None:
            values[key] = processor(values[key])

    sql = f"{prefix} {compiled.string}"
    if dialect.positional:
        order: Sequence[str] = getattr(compiled, "positiontup", None) or ()
        return sql, tuple(values[key] for key in order)
    return sql, values


@dataclass(frozen=True)
class _Captured:
    """A statement recorded during the block, waiting to be explained.

    Attributes:
        statement (ClauseElement): The statement as executed.
        parameters (Any): The parameters it was executed with.
    """

    statement: ClauseElement
    parameters: Any


@asynccontextmanager
async def explain_queries(
    session: AsyncSession,
    *,
    analyze: bool = True,
) -> AsyncIterator[ExplainReport]:
    """Record every statement the block runs, then explain each one.

    Intended for development and for a deliberate profiling run — it
    doubles the work for each ``SELECT`` it analyzes, so it does not
    belong in a hot request path.

    Args:
        session (AsyncSession): The session whose statements to capture.
            Only this session is observed, so a concurrent request on
            another session does not pollute the report.
        analyze (bool): Whether ``SELECT`` statements may be executed a
            second time to collect measured timings. ``False`` keeps
            everything to planner estimates, which is the right choice
            when a statement is expensive enough that running it twice
            distorts the very thing being investigated.

    Yields:
        ExplainReport: Empty while the block runs; filled on exit.

    Raises:
        BaseException: Re-raises whatever the body raised. The plans
            captured before the failure are still in the report, which is
            what makes the tool usable on the query that is erroring.

    Notes:
        Statements that are not ``SELECT`` are explained **without**
        ``ANALYZE`` on PostgreSQL and are therefore never re-executed;
        see the module docstring.
    """
    report = ExplainReport(backend=session.get_bind().dialect.name)
    captured: list[_Captured] = []

    def _record(orm_execute_state: Any) -> None:
        """Append the statement about to run to the capture list.

        Args:
            orm_execute_state (Any): SQLAlchemy's ORM execute state.
        """
        captured.append(
            _Captured(
                statement=orm_execute_state.statement,
                parameters=orm_execute_state.parameters,
            ),
        )

    sync_session = session.sync_session
    event.listen(sync_session, "do_orm_execute", _record)
    try:
        yield report
    finally:
        event.remove(sync_session, "do_orm_execute", _record)
        report.plans.extend(await _explain_all(session, captured, analyze=analyze))


async def _explain_all(
    session: AsyncSession,
    captured: Sequence[_Captured],
    *,
    analyze: bool,
) -> list[QueryPlan]:
    """Explain every captured statement, skipping those that cannot be.

    A statement the backend refuses to explain is dropped rather than
    raising: the tool exists to diagnose a problem, and failing the whole
    report because one statement was unexplainable would hide the plans
    that did work.

    Args:
        session (AsyncSession): The session to explain through.
        captured (Sequence[_Captured]): The recorded statements.
        analyze (bool): Whether ``SELECT`` statements may be analyzed.

    Returns:
        list[QueryPlan]: One plan per statement that could be explained.
    """
    dialect = session.get_bind().dialect.name
    plans: list[QueryPlan] = []
    for item in captured:
        plan = await _explain_one(session, item, dialect=dialect, analyze=analyze)
        if plan is not None:
            plans.append(plan)
    return plans


async def _explain_one(
    session: AsyncSession,
    item: _Captured,
    *,
    dialect: str,
    analyze: bool,
) -> QueryPlan | None:
    """Explain a single captured statement.

    Args:
        session (AsyncSession): The session to explain through.
        item (_Captured): The recorded statement.
        dialect (str): The backend's dialect name.
        analyze (bool): Whether a ``SELECT`` may be analyzed.

    Returns:
        QueryPlan | None: The plan, or ``None`` when this backend or
        statement cannot be explained.
    """
    prefix, detail = _prefix_for(item.statement, dialect=dialect, analyze=analyze)
    if prefix is None:
        return None

    try:
        connection = await session.connection()
        sql, parameters = _compile_for_driver(
            item.statement, session.get_bind().dialect, prefix
        )
        result = await connection.exec_driver_sql(sql, parameters)
        rows = result.fetchall()
    except Exception:
        logger.warning(
            "Could not explain a captured statement; it is omitted from the "
            "report. Statement: %s",
            str(item.statement).replace("\n", " ")[:200],
            exc_info=True,
        )
        return None

    sql = str(item.statement)
    if dialect == POSTGRESQL_DIALECT:
        return _postgresql_plan(sql, rows, detail)
    return _sqlite_plan(sql, rows)


def _prefix_for(
    statement: ClauseElement,
    *,
    dialect: str,
    analyze: bool,
) -> tuple[str | None, ExplainDetail]:
    """Pick the ``EXPLAIN`` prefix for a statement on a backend.

    Args:
        statement (ClauseElement): The statement to explain.
        dialect (str): The backend's dialect name.
        analyze (bool): Whether a ``SELECT`` may be analyzed.

    Returns:
        tuple[str | None, ExplainDetail]: The prefix — ``None`` when the
        backend is unsupported — and the detail level it will yield.
    """
    if dialect == SQLITE_DIALECT:
        return SQLITE_PLAN_PREFIX, ExplainDetail.PLAN_ONLY
    if dialect != POSTGRESQL_DIALECT:
        return None, ExplainDetail.PLAN_ONLY
    if analyze and _is_select(statement):
        return POSTGRESQL_ANALYZE_PREFIX, ExplainDetail.MEASURED
    return POSTGRESQL_PLAN_PREFIX, ExplainDetail.ESTIMATED


def _is_select(statement: ClauseElement) -> bool:
    """Whether the statement only reads, and is therefore safe to analyze.

    ``EXPLAIN ANALYZE`` runs what it explains, so this guard is what
    stops a captured ``INSERT`` from being performed twice.

    Args:
        statement (ClauseElement): The statement to classify.

    Returns:
        bool: ``True`` only when the statement is known to be a read. A
        raw ``text()`` is classified by its leading keyword, and anything
        unrecognized is treated as a write — the safe direction.
    """
    if isinstance(statement, TextClause):
        return statement.text.lstrip().lower().startswith("select")
    return bool(getattr(statement, "is_select", False))


def _postgresql_plan(
    sql: str,
    rows: Sequence[Any],
    detail: ExplainDetail,
) -> QueryPlan:
    """Build a plan from PostgreSQL's ``FORMAT JSON`` output.

    Args:
        sql (str): The statement that was explained.
        rows (Sequence[Any]): The result rows; the JSON sits in the first
            cell of the first row.
        detail (ExplainDetail): Whether the statement was analyzed.

    Returns:
        QueryPlan: The parsed plan. Metrics the output does not carry
        stay ``None`` rather than defaulting to zero, which would read as
        "free".
    """
    payload: Any = rows[0][0] if rows else []
    if isinstance(payload, str):
        payload = json.loads(payload)
    root: dict[str, Any] = {}
    if isinstance(payload, list) and payload and isinstance(payload[0], dict):
        root = payload[0].get("Plan", {})

    return QueryPlan(
        sql=sql,
        detail=detail,
        plan_text=json.dumps(payload, indent=2, ensure_ascii=False),
        total_cost=root.get("Total Cost"),
        duration_ms=root.get("Actual Total Time"),
        rows=root.get("Actual Rows", root.get("Plan Rows")),
        raw=payload,
    )


def _sqlite_plan(sql: str, rows: Sequence[Any]) -> QueryPlan:
    """Build a plan from SQLite's ``EXPLAIN QUERY PLAN`` rows.

    Args:
        sql (str): The statement that was explained.
        rows (Sequence[Any]): The result rows; the description is the
            last column of each.

    Returns:
        QueryPlan: The plan, carrying step descriptions and no metrics —
        SQLite reports neither cost nor timing.
    """
    steps = [str(row[-1]) for row in rows]
    return QueryPlan(
        sql=sql,
        detail=ExplainDetail.PLAN_ONLY,
        plan_text="\n".join(steps),
        raw=steps,
    )


__all__: list[str] = [
    "POSTGRESQL_ANALYZE_PREFIX",
    "POSTGRESQL_PLAN_PREFIX",
    "SQLITE_PLAN_PREFIX",
    "ExplainDetail",
    "ExplainReport",
    "QueryPlan",
    "explain_queries",
]
