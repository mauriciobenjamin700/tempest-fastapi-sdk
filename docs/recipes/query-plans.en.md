# Query plans (EXPLAIN)

A development tool for the question "why is this endpoint slow?". A timer
in the application says *how long* it took; the database's plan says
*why*.

## Capturing a block

```python
from sqlalchemy.ext.asyncio import AsyncSession

from src.db.models import OrderModel
from tempest_fastapi_sdk import BaseRepository, explain_queries


async def profile_page(
    session: AsyncSession,
    orders: BaseRepository[OrderModel],
) -> str:
    """Capture the plans of a paginated read and return the report.

    Args:
        session (AsyncSession): The session to observe.
        orders (BaseRepository[OrderModel]): The order repository.

    Returns:
        str: One line per captured statement.
    """
    async with explain_queries(session) as report:
        await orders.paginate(filters={"status": "open"}, page=3)
    return report.report()
```

Everything the session executes inside the block is recorded. On exit,
each statement is explained and the report fills in:

```text
12.75ms cost=431.2 rows=1904 [measured] SELECT "order".id, "order".status ...
0.31ms cost=8.1 rows=1 [measured] SELECT count(*) AS count_1 FROM "order" ...
```

!!! info "The report only fills on exit"
    Statements are recorded during the block and explained **afterwards**.
    Explaining during it would perturb the very thing being measured.

The repository exposes the same block as sugar:

```python
from src.db.models import OrderModel
from tempest_fastapi_sdk import BaseRepository, ExplainReport


async def profile_list(orders: BaseRepository[OrderModel]) -> ExplainReport:
    """Open the block through the repository instead of the session.

    Args:
        orders (BaseRepository[OrderModel]): The order repository.

    Returns:
        ExplainReport: The captured plans.
    """
    async with orders.explain() as report:
        await orders.list(filters={"status": "open"})
    return report
```

## What each backend gives you

| Backend | Command | `detail` | Cost | Measured time |
| --- | --- | --- | --- | --- |
| PostgreSQL (`SELECT`) | `EXPLAIN (ANALYZE, BUFFERS, FORMAT JSON)` | `MEASURED` | yes | yes |
| PostgreSQL (write) | `EXPLAIN (FORMAT JSON)` | `ESTIMATED` | yes | no |
| SQLite | `EXPLAIN QUERY PLAN` | `PLAN_ONLY` | no | no |

SQLite shows which index each step uses — or that it uses none — and
nothing else. The SDK reports `ExplainDetail.PLAN_ONLY` instead of
inventing numbers; `total_cost` and `duration_ms` stay `None`, not zero,
because zero would read as "free".

## Writes are never re-executed

`EXPLAIN ANALYZE` **runs** what it explains. Applying it to the `INSERT`
your block just performed would insert a second row. Only `SELECT` is
analyzed; anything else is explained without `ANALYZE`, which asks the
planner without touching the data.

```python
from sqlalchemy.ext.asyncio import AsyncSession

from src.db.models import OrderModel
from tempest_fastapi_sdk import BaseRepository, explain_queries


async def insert_once(
    session: AsyncSession,
    orders: BaseRepository[OrderModel],
    order: OrderModel,
) -> int:
    """Persist an order inside the block and count the rows.

    Args:
        session (AsyncSession): The session to observe.
        orders (BaseRepository[OrderModel]): The order repository.
        order (OrderModel): The order to persist.

    Returns:
        int: The row count — 1, because the write is not re-executed.
    """
    async with explain_queries(session):
        await orders.add(order)   # explained, not re-executed
    return await orders.count()
```

A raw `text()` is classified by its leading keyword, and anything
unrecognized is treated as a write — the safe direction.

If even the estimated plan is too expensive, turn the analysis off:

```python
from sqlalchemy.ext.asyncio import AsyncSession

from src.db.models import OrderModel
from tempest_fastapi_sdk import BaseRepository, ExplainReport, explain_queries


async def estimate_only(
    session: AsyncSession,
    orders: BaseRepository[OrderModel],
) -> ExplainReport:
    """Collect planner estimates only, running nothing twice.

    Args:
        session (AsyncSession): The session to observe.
        orders (BaseRepository[OrderModel]): The order repository.

    Returns:
        ExplainReport: The plans, without measured time.
    """
    async with explain_queries(session, analyze=False) as report:
        await orders.list()
    return report
```

## Reading the report

```python
from tempest_fastapi_sdk import ExplainReport, QueryPlan


def worst(report: ExplainReport) -> QueryPlan | None:
    """Pick the statement that deserves attention first.

    Args:
        report (ExplainReport): The captured report.

    Returns:
        QueryPlan | None: The costliest plan, or ``None`` when nothing
        was captured.
    """
    slowest = report.slowest
    if slowest is not None:
        print(slowest.summary())
        print(slowest.plan_text)

    print(len(report))                 # how many statements
    print(report.total_duration_ms)    # None when nothing was timed
    return slowest
```

`slowest` uses measured time; where there is no time it falls back to
planner cost — so the property still answers "which one do I look at
first?" on a backend that times nothing.

Each `QueryPlan` also carries `raw`, the database's untouched output, for
whatever the typed fields do not cover (child nodes, buffer counts).

!!! tip "Plans survive an exception"
    If the block raises, whatever was captured before the failure stays in
    the report — and the erroring query is usually exactly the one whose
    plan you want.

## Scope

Only the session you pass is observed, so a concurrent request on another
session does not pollute the report.

!!! warning "Not for the hot path"
    Each analyzed `SELECT` runs twice. This is for development and for a
    deliberate profiling session, not to leave enabled in production.

## Recap

- `explain_queries(session)` captures the block and explains on exit.
- PostgreSQL gives cost, measured time and actual versus estimated rows;
  SQLite gives the plan and the SDK says that is all it is.
- A write is never re-executed — the rule that keeps the tool from being
  destructive.
- `report.slowest` and `report.report()` to find the culprit; `plan.raw`
  for the rest.
