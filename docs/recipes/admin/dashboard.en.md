---
description: "Use to put business metrics on the admin dashboard: `dashboard_cards`, metric areas with thresholds and a geographic distribution map."
---

# Dashboard: cards, indicators and map

Use to put business metrics on the dashboard: cards, indicator areas with thresholds and the geographic distribution map.

Part of the [Admin site](../admin.md) recipe.

## Dashboard: business-metric cards (`dashboard_cards=`)

The dashboard already shows CPU/RAM/counters. For **your business**
metrics — orders today, revenue vs last week, users by plan — pass
`dashboard_cards`. Each card is a `MetricCard(label, compute)` where
`compute` is an async function that takes the session and returns one of
three types:

```python
from datetime import date, timedelta

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from tempest_fastapi_sdk import (
    AdminSite,
    MetricCard,
    MetricPartition,
    MetricTrend,
    MetricValue,
)

from src.db.models import OrderModel
from src.db.repositories import OrderRepository


async def orders_today(session: AsyncSession) -> MetricValue:
    total = await OrderRepository(session).count(filters={"start_in": date.today()})
    return MetricValue(total, unit="orders")


async def revenue_between(session: AsyncSession, start: date, end: date) -> float:
    """Sum the revenue of the orders created in [start, end)."""
    result = await session.execute(
        select(func.coalesce(func.sum(OrderModel.total), 0)).where(
            func.date(OrderModel.created_at) >= start,
            func.date(OrderModel.created_at) < end,
        )
    )
    return float(result.scalar_one())


async def revenue_trend(session: AsyncSession) -> MetricTrend:
    tomorrow = date.today() + timedelta(days=1)
    this_week = tomorrow - timedelta(days=7)
    last_week = this_week - timedelta(days=7)
    return MetricTrend(
        value=await revenue_between(session, this_week, tomorrow),
        previous=await revenue_between(session, last_week, this_week),
        unit="BRL",
    )


async def users_by_plan(session: AsyncSession) -> MetricPartition:
    return MetricPartition(segments=[("free", 120), ("pro", 30), ("enterprise", 4)])


site = AdminSite(
    title="Shop",
    dashboard_cards=[
        MetricCard("Orders today", orders_today, help_text="since midnight"),
        MetricCard("Revenue", revenue_trend),
        MetricCard("Users by plan", users_by_plan),
    ],
)
```

- **`MetricValue(value, unit=None)`** — a big number.
- **`MetricTrend(value, previous, unit=None)`** — a number + a ▲/▼ arrow
  and the percentage change vs the previous period (`delta` / `pct` /
  `direction` computed for you).
- **`MetricPartition(segments=[(label, value), ...])`** — a breakdown
  with proportional bars; `total` summed automatically.

The cards render at the top of the dashboard, computed on each load. A
card whose `compute` raises is **skipped** — a broken metric never
blanks the page — and the failure is logged with its traceback
(`logger.exception` on the `tempest_fastapi_sdk.admin.router` logger,
naming the card's `label`), so the card missing from the page shows up in
the log.

## Indicator areas: schema + service (`metrics_areas=`)

`MetricCard` receives the session and queries the database directly, card
by card. When your service **already knows** how to collect the
indicators, repeating the query in the admin is double work — and skips
the service layer. `MetricsArea` is the other shape: you hand over **a
schema with one field per indicator** and **a service whose method takes
no argument**, and the admin generates a page with one card per field,
listed under **Metrics** in the sidebar.

```python
from pydantic import BaseModel, Field
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from tempest_fastapi_sdk import AdminSite, BaseUserModel, MetricsArea, Threshold


class UserModel(BaseUserModel):
    __tablename__ = "users"


class RideMetricsSchema(BaseModel):
    messages_to_route: float = Field(
        title="Messages until the route",
        description="Average messages before the user reached the route they wanted.",
    )
    accesses: int = Field(title="Accesses")
    accounts_created: int = Field(title="Accounts created")
    rides_created: int = Field(title="Rides created")


class RideMetricsService:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    async def collect(self) -> RideMetricsSchema:
        accounts = await self.session.scalar(select(func.count()).select_from(UserModel))
        return RideMetricsSchema(
            messages_to_route=4.2,
            accesses=1200,
            accounts_created=accounts or 0,
            rides_created=87,
        )


site = AdminSite(
    title="Transport",
    metrics_areas=[
        MetricsArea(
            "Rides",
            RideMetricsSchema,
            provider=RideMetricsService,
            method="collect",
            thresholds={
                "messages_to_route": Threshold(green=3, yellow=6, lower_is_better=True),
                "accounts_created": Threshold(green=100, yellow=40),
                "rides_created": Threshold(green=100, yellow=40),
            },
            units={"rides_created": "rides"},
        )
    ],
)
```

The page lives at `/admin/metrics/rides` (the slug comes from the title).
Piece by piece:

- **`provider`** builds the service from the admin's session. The class
  itself works when its `__init__` takes the session; a composition
  function (`build_ride_metrics_service`) works too. Sync or `async`.
- **`method`** is called **with no argument** and returns an instance of
  `schema`. Anything else becomes an error on the page.
- **Each field becomes a card**: `title` is the label (the field name when
  missing), `description` the line under the value. A number becomes a
  value card; a field typed `MetricValue`, `MetricTrend` or
  `MetricPartition` renders as that card.
- **`units`** puts the unit after the number.

### Thresholds: green, yellow, red

`Threshold(green=, yellow=)` paints the card of a numeric field (or the
current value of a `MetricTrend`) with a border and a badge — **OK**,
**Attention** or **Critical**:

| `Threshold(green=100, yellow=40)` (higher is better) | Band |
| --- | --- |
| `>= 100` | green |
| `>= 40` and `< 100` | yellow |
| `< 40` | red |

With `lower_is_better=True` the comparison flips — `Threshold(green=3,
yellow=6, lower_is_better=True)` is green up to 3, yellow up to 6 and red
above. The exact limit always lands in the better band. The colour comes
from the theme tokens (`--tempest-success`, `--tempest-warning`,
`--tempest-danger`), so light and dark stay consistent.

!!! tip "Fail early, not an uncoloured card"
    `thresholds` or `units` naming a field the schema does not have raises
    `ValueError` when the `MetricsArea` is built, and limits in the wrong
    order for the direction (`green < yellow` with higher-is-better) raise
    when the `Threshold` is built. A typo never becomes a card that just
    does not paint.

### When the service fails

If the `provider` or the method raises, the page answers `200` with a
notice in place of the cards, and the traceback goes to the log
(`logger.exception` on `tempest_fastapi_sdk.admin.router`, naming the
area). A broken service never turns the panel into a `500`.

### Who sees it

The area is the `AdminSurfaceEnum.METRICS` surface: with a
`surface_policy`, whoever the policy denies gets `403` on the page and no
links in the sidebar.

## Geographic distribution map (`location_maps=`) *(v0.367.0+)*

With [`BRLocationMixin`](../geo.md#a-users-city-and-state-brlocationmixin-v03670)
on the model, the panel draws **where** the rows are: a map of Brazil with
each state shaded by its count and one bubble per city, the legend, the
state and city tables, and how many rows have no location yet. Register one
`BRLocationMap` per map in `AdminSite(location_maps=[...])`:

```python
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

import uvicorn
from fastapi import FastAPI

from tempest_fastapi_sdk import (
    AdminSite,
    AsyncDatabaseManager,
    BaseUserModel,
    UserModelAuthBackend,
    make_admin_router,
)
from tempest_fastapi_sdk.admin import AdminModel, BRLocationMap
from tempest_fastapi_sdk.geo import BRLocationMixin


class UserModel(BRLocationMixin, BaseUserModel):
    """Users with the city and state they live in."""

    __tablename__ = "users"


db = AsyncDatabaseManager("sqlite+aiosqlite:///./painel.db")
site = AdminSite(
    title="Painel",
    location_maps=[
        BRLocationMap("Usuários por região", UserModel, value_label="usuários"),
        BRLocationMap(
            "Usuários ativos",
            UserModel,
            where=lambda: UserModel.is_active.is_(True),
            value_label="usuários",
        ),
    ],
)
site.register(AdminModel(UserModel))


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    """Create the tables and a few users spread over three cities."""
    await db.connect()
    await db.create_tables()
    async with db.get_session_context() as session:
        admin = UserModel(email="admin@example.com", hashed_password="", is_admin=True)
        admin.set_password("troque-esta-senha")
        session.add(admin)
        places = [("São Paulo", "SP"), ("São Paulo", "SP"), ("Campinas", "SP")]
        places.append(("Recife", "PE"))
        for index, (city, state) in enumerate(places):
            user = UserModel(email=f"user{index}@example.com", hashed_password="")
            user.set_br_location(city, state)
            session.add(user)
        await session.commit()
    yield
    await db.disconnect()


app = FastAPI(lifespan=lifespan)
app.include_router(
    make_admin_router(
        site,
        db=db,
        auth_backend=UserModelAuthBackend(UserModel),
        secret_key="troque-por-um-segredo-de-32-bytes-ou-mais",
        cookie_secure=False,
    )
)

if __name__ == "__main__":
    uvicorn.run(app, host="127.0.0.1", port=8000)
```

!!! note "`create_tables()` here is a demo shortcut"
    The example fits in a single file, so it creates the tables straight
    in the lifespan. In a service, the lifespan calls `sync_schema_async()`
    over the Alembic migrations: `create_tables()` adds no column to a
    table that already exists. See
    [Migrations »](../migrations.md#3-sync-the-schema-in-the-lifespan).

Each map becomes an entry in the sidebar's **Maps** section, at
`/admin/maps/<slug>` (the slug comes from the title, accents dropped:
`usuarios-por-regiao`). Measured with the example, by requests to the panel:

- the sidebar lists `/admin/maps/usuarios-por-regiao` and
  `/admin/maps/usuarios-ativos`;
- the page answers `200` with Total `5`, With location `4` and No location
  `1` (the administrator has no city);
- the bubbles, largest first: `São Paulo (SP): 2 usuários`,
  `Campinas (SP): 1 usuários`, `Recife (PE): 1 usuários`;
- each state's tooltip (`SP: 3 usuários`, `PE: 1 usuários`,
  `BA: 0 usuários`) and the legend with each shade's range.

What each argument does:

- `where=` takes a function that builds the filter on every request
  (`lambda: UserModel.is_active.is_(True)`), applied to every count.
- `city_limit=` (default 50) caps the bubbles and the table at the largest
  cities.
- `value_label=` is the word after each number in the tooltips and tables.

The map is an SVG built on the server, with the state outlines shipped in
the package: the page makes **no external request** (no map script, no tile
server). Its colours come from the theme variables, so dark mode
(`AdminTheme(dark_mode=True)`) already applies. Validated in a real browser
at 1366 px and 390 px, in the light and dark themes: on a phone the map
takes the full width and the tables move below it.

!!! info "Who sees it"
    The page asks the `access_policy` for `VIEW` on the model, of the
    model's registered `AdminModel` (or of a throwaway `AdminModel(model)`
    when it is not registered). Denied, the page answers `403` and leaves
    the sidebar.

!!! note "When the count fails"
    If `where=` or the database raises, the page still renders, with the
    notice `This map could not be counted` in place of the map and the
    traceback in the log — like the indicator areas.
