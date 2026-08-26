# App errors: when the client breaks in the user's hand

Your mobile app threw a `TypeError` on the payment screen. The user closed
it and left. You find out from a screenshot on WhatsApp three days later —
no version, no device, no stack trace.

This recipe builds the place that report lands in by itself.

## What you will build

A public endpoint that accepts the report, a table where it becomes
queryable, and a filterable listing to investigate with.

```python
from tempest_fastapi_sdk.app_errors import (
    AppErrorService,
    make_app_error_model,
    make_app_error_router,
)
```

## The table

The SDK ships the abstract row; your project creates the concrete table,
because the name of your users table belongs to your project:

```python
# src/db/models/app_error.py
from tempest_fastapi_sdk.app_errors import make_app_error_model

AppErrorModel = make_app_error_model(
    user_table="users",
    tablename="app_errors",
)
```

Three decisions come baked in, and each exists because the alternative
fails silently:

!!! info "`user_id` is nullable, and the FK is `SET NULL`"
    An error in the login flow happens **before** an authenticated user
    exists — and that is precisely the error hardest to debug from the app.
    Requiring a user would drop the most valuable case.

    The FK uses `ON DELETE SET NULL` rather than the `CASCADE` the rest of
    the schema uses: the report describes a defect of the **application**,
    not of the user. Deleting the account must not delete the evidence of
    the bug.

!!! tip "`created_at` gets its own index"
    The standard read is "newest first", paginated, and this is the table
    that grows with no natural bound. Without the index, every page costs a
    sort of the whole table.

## Receiving the report

```python
# src/api/app.py
from collections.abc import AsyncIterator
from uuid import UUID

from fastapi import FastAPI
from sqlalchemy.ext.asyncio import AsyncSession

from tempest_fastapi_sdk import BaseRepository
from tempest_fastapi_sdk.app_errors import AppErrorService, make_app_error_router

from src.db.models.app_error import AppErrorModel
from src.db.session import get_session


def service_factory(session: AsyncSession) -> AppErrorService:
    """Build the service for one request.

    Args:
        session (AsyncSession): The request-scoped session.

    Returns:
        AppErrorService: The service.
    """
    return AppErrorService(BaseRepository(session, model=AppErrorModel))


async def session_factory() -> AsyncIterator[AsyncSession]:
    """Yield a request-scoped session.

    Yields:
        AsyncSession: The session.
    """
    async for session in get_session():
        yield session


app: FastAPI = FastAPI()
app.include_router(
    make_app_error_router(
        service_factory=service_factory,
        session_factory=session_factory,
    )
)
```

The app sends:

```json
{
  "code": "PLAN_ACTIVATION_FAILED",
  "message": "TypeError: null is not an object (evaluating 'user.id')",
  "platform": "ios",
  "os_version": "18.2",
  "app_version": "1.4.2+310",
  "device_model": "iPhone 15 Pro"
}
```

Only `code` and `message` are required. Everything else is whatever the app
**managed** to collect at the moment of the failure — requiring any of them
would turn an incomplete collection into a lost report.

## The two rules that make this work

### A truncated report beats a lost report

A payload above the column limit is **cut**, never refused:

```python
from tempest_fastapi_sdk.app_errors import (
    APP_ERROR_TRUNCATION_SUFFIX,
    AppErrorService,
)


def example() -> str | None:
    """Show what a value over the limit becomes.

    Returns:
        str | None: The cut value, marked.
    """
    return AppErrorService.truncate("x" * 5000, 4000)
```

The value comes back ending in `…[truncado]`, so whoever reads the listing
knows content is missing.

!!! danger "Why not 422"
    The sender is an app that has **just crashed**. It has no path for
    handling a refusal: the 422 becomes an exception inside the exception
    handler, and the report simply evaporates. Refusing by size loses
    exactly the most interesting report — the one that came with a large
    stack trace.

### `user_id` comes from the token, never from the body

`AppErrorReportSchema` — what the client may send — **has no such field**.
The service fills it from whoever the request authenticated as:

```python
from uuid import UUID

from tempest_fastapi_sdk.app_errors import AppErrorReportSchema, AppErrorService


async def record(
    service: AppErrorService, data: AppErrorReportSchema, caller: UUID | None
) -> None:
    """Store a report attributed to the authenticated caller.

    Args:
        service (AppErrorService): The service.
        data (AppErrorReportSchema): The body the client sent.
        caller (UUID | None): Who the token says is calling.
    """
    await service.report_error(data, user_id=caller)
```

!!! warning "This is why there are two schemas"
    If `user_id` were a body field, any client could attribute its own error
    to somebody else's account. The split between `AppErrorReportSchema` and
    `AppErrorCreateSchema` is not tidiness: it is what makes that
    impossible, rather than merely discouraged.

## Investigating

The listing is **opt-in**. It exists only if you pass the admin dependency:

```python
from fastapi import APIRouter

from tempest_fastapi_sdk.app_errors import make_app_error_router

from src.api.dependencies.auth import require_admin
from src.api.factories import service_factory, session_factory

router: APIRouter = make_app_error_router(
    service_factory=service_factory,
    session_factory=session_factory,
    admin_dependency=require_admin,
)
```

Without `admin_dependency` the `GET` route is **not mounted** — deliberately:
the listing returns stack traces and device identifiers, which must not be
left open by forgetting to protect them.

!!! tip "Or skip the endpoint entirely"
    If your service already uses `AdminSite`, register the table there:
    listing, filtering and pagination come for free and you maintain no
    route at all. The router's `GET` is for services with their own panel.

The cut that resolves most investigations is `code` + `app_version` — it
isolates one defect in one build:

```text
GET /api/app-errors?code=PLAN_ACTIVATION_FAILED&app_version=1.4.2
```

!!! info "The date range is half-open on purpose"
    The filter builds `created_at >= start` and `created_at < end + 1 day`
    rather than `func.date(created_at)`. Applying a function to the column
    **discards the index** — and this is the table that grows fastest. From
    the outside, `start_date` and `end_date` stay inclusive on both ends.

!!! warning "`start_date` and `end_date` are **UTC** days"
    `created_at` is written by `utcnow`, and the filter compares the date
    you send against midnight — so the cut is a UTC boundary, not the one
    of the zone the process runs in.

    Measured: a report stored at `2026-03-10T02:30Z` — still
    `2026-03-09 23:30` on a São Paulo clock — shows up when filtering
    `2026-03-10` (`total=1`) and **not** when filtering `2026-03-09`
    (`total=0`). The answer is the same with the server in
    `America/Sao_Paulo` or in `Asia/Tokyo`, which is the point: the window
    does not shift with the machine.

    In practice: build the range from `datetime.now(UTC).date()`, not from
    `datetime.now().date()`. The two disagree for three hours a day in BRT,
    and those are the hours where "today" comes back empty.

## The request ceiling

The `POST` is public. It needs a ceiling, and the ceiling does **not** live
in this module:

```python
from fastapi import FastAPI

from tempest_fastapi_sdk.api.middlewares.rate_limit import (
    FailOpenRateLimitStore,
    MemoryRateLimitStore,
    RateLimitMiddleware,
)

app: FastAPI = FastAPI()
app.add_middleware(
    RateLimitMiddleware,
    store=FailOpenRateLimitStore(MemoryRateLimitStore()),
    max_requests=120,
    window_seconds=3600.0,
)
```

!!! danger "`FailOpenRateLimitStore` is not decoration"
    Measured: with a store whose `hit` raises, the exception **propagates**
    through `RateLimitMiddleware` and the caller gets an error. For most
    endpoints that is defensible — a limiter that cannot count cannot
    protect.

    Here it is backwards. The moment the counter store is unwell is exactly
    when errors spike, and rejecting the reports then destroys the evidence
    of the incident being reported. Losing the report is worse than serving
    above the ceiling.

    The wrapper makes that trade explicit, and keeps it visible: every
    failure is logged at `WARNING`, so "the ceiling is not being enforced"
    never happens silently.

## Recap

- `make_app_error_model` creates the table; nullable `user_id` and
  `SET NULL` preserve login-flow reports and the evidence of the bug.
- Only `code` and `message` are required; the rest is what the app managed
  to collect.
- An oversized value is truncated with a marker, never refused.
- `user_id` comes from the token — the client's schema has no such field.
- The listing exists only with `admin_dependency`, or through `AdminSite`.
- The `POST` ceiling is `RateLimitMiddleware`, and it has to fail open.
