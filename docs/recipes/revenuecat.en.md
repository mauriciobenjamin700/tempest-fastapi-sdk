# RevenueCat (in-app subscriptions)

A Flutter (or native) app that sells subscriptions through the App Store and
Google Play almost always goes through RevenueCat. The app buys; RevenueCat
tells your backend through a **webhook** on every change (purchase, renewal,
cancellation, expiration...). The backend needs three things every service
ended up writing by hand:

1. **Authenticate** the webhook — and refuse everything when no secret is
   configured.
2. **Record the event exactly once**, answer `200` fast and process later —
   RevenueCat retries, and sometimes sends the same event twice.
3. **Give up on a poisoned event** after N attempts, instead of retrying it on
   every sweep, forever.

The SDK ships all three: the adapter in
`tempest_fastapi_sdk.integrations.subscriptions.revenuecat` and the generic
`WebhookInbox` in `tempest_fastapi_sdk.db`, which works for any provider.

!!! info "Installation"
    No extra. The client uses the SDK's `HTTPClient`, and `httpx` already
    ships with the base package.

!!! note "Where every claim about RevenueCat comes from"
    Event format, headers, event types and retry policy were read from the
    official documentation, not measured against a live account:
    [Webhooks](https://www.revenuecat.com/docs/integrations/webhooks),
    [Event Types and Fields](https://www.revenuecat.com/docs/integrations/webhooks/event-types-and-fields),
    [Sample Events](https://www.revenuecat.com/docs/integrations/webhooks/sample-events)
    and the [REST API v1](https://www.revenuecat.com/docs/api-v1). The three
    webhook pages are vendored in `vendor/` with their sha256 pinned by a
    test, and the SDK's list of event types is compared against them.

## The minimal path

One file with the inbox model, your product's subscription table, the handler
that applies an event, and the route:

```python
# src/billing.py
from datetime import datetime

from fastapi import FastAPI
from sqlalchemy import String, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import Mapped, mapped_column

from tempest_fastapi_sdk import register_exception_handlers
from tempest_fastapi_sdk.db import (
    AsyncDatabaseManager,
    BaseModel,
    BaseWebhookEventModel,
    UtcDateTime,
    WebhookInbox,
)
from tempest_fastapi_sdk.integrations.subscriptions.revenuecat import (
    RevenueCatWebhook,
    make_revenuecat_webhook_router,
    normalize,
)
from tempest_fastapi_sdk.settings import DatabaseSettings, RevenueCatSettings


class Settings(DatabaseSettings, RevenueCatSettings):
    """Database + RevenueCat credentials, read from the environment."""


class WebhookEventModel(BaseWebhookEventModel):
    """Webhook inbox: one row per distinct event."""

    __tablename__ = "webhook_events"


class SubscriptionModel(BaseModel):
    """Current subscription state of each app user."""

    __tablename__ = "subscriptions"

    app_user_id: Mapped[str] = mapped_column(String(255), unique=True)
    status: Mapped[str] = mapped_column(String(32))
    product_id: Mapped[str | None] = mapped_column(String(255), nullable=True)
    period_end: Mapped[datetime | None] = mapped_column(UtcDateTime, nullable=True)


settings = Settings()
db = AsyncDatabaseManager(**settings.database_kwargs())
inbox: WebhookInbox[WebhookEventModel] = WebhookInbox(db, model=WebhookEventModel)


async def apply_event(event: WebhookEventModel, session: AsyncSession) -> None:
    """Store the state the event left, in the inbox's own transaction.

    Args:
        event (WebhookEventModel): The claimed inbox row.
        session (AsyncSession): The session that also marks the event
            processed.
    """
    snapshot = normalize(RevenueCatWebhook.model_validate(event.payload).event)
    if snapshot.app_user_id is None:
        return
    current = (
        await session.execute(
            select(SubscriptionModel).where(
                SubscriptionModel.app_user_id == snapshot.app_user_id
            )
        )
    ).scalar_one_or_none()
    if current is None:
        current = SubscriptionModel(app_user_id=snapshot.app_user_id)
        session.add(current)
    current.status = snapshot.status.value
    current.product_id = snapshot.product_id
    current.period_end = snapshot.period_end


app = FastAPI()
register_exception_handlers(app)
app.include_router(
    make_revenuecat_webhook_router(
        inbox=inbox,
        secret=settings.REVENUECAT_WEBHOOK_SECRET,
    )
)
```

And the sweep that processes what arrived, every 5 minutes:

```python
# src/tasks/billing.py
from tempest_fastapi_sdk.tasks import CronEnum, TaskQueue

from src.billing import apply_event, db, inbox

tq = TaskQueue.rabbitmq("amqp://guest:guest@localhost:5672/", resources=[db])


@tq.cron(CronEnum.EVERY_5_MINUTES)
async def sweep_webhook_inbox() -> None:
    """Process every inbox event that is due."""
    await inbox.drain_once(apply_event)
```

In the RevenueCat dashboard, under **Integrations → Webhooks**, point the URL
at `https://<your-host>/webhooks/revenuecat` and fill the *authorization
header* field with the same value as `REVENUECAT_WEBHOOK_SECRET`.

What happens to a delivery, run with the code above (SQLite, three POSTs of a
trial `INITIAL_PURCHASE` carrying an `$email` in `subscriber_attributes` — no
header, with the header, with the header again —, one `drain_once`, then the
`subscriptions` row and the inbox row printed: status, and whether the stored
payload has `subscriber_attributes`):

```text
401 {'detail': 'Invalid RevenueCat webhook credentials', 'code': 'UNAUTHORIZED', 'details': {}}
200 {'recorded': True, 'event_id': 'evt-1'}
200 {'recorded': False, 'event_id': 'evt-1'}
processed 1 retrying 0
user-1 trial monthly 2030-03-17 17:46:40+00:00
processed False
```

The repeat got `200` with `recorded: False`: RevenueCat only needs to stop
retrying, and nothing was stored twice.

## Piece by piece

### 1. The route holds no business logic

`make_revenuecat_webhook_router` authenticates, records the event in the inbox
with `provider="revenuecat"` and `external_id=event.id`, and answers `200`.
That is all.

Why: according to the [documentation](https://www.revenuecat.com/docs/integrations/webhooks),
any status other than `200` is a failure, RevenueCat retries "up to 5 times"
(after 5, 10, 20, 40 and 80 minutes) and disconnects after 60 seconds. The
same page recommends deferring processing until after the response.

!!! tip "Process right away, without waiting for the sweep"
    Pass `on_recorded`: it receives the inbox row id of every **new** event
    (not of a repeat). Enqueue the task there. If enqueueing fails, the route
    still answers `200` — the event is already recorded and the sweep picks
    it up.

    ```python
    from uuid import UUID

    from tempest_fastapi_sdk.integrations.subscriptions.revenuecat import (
        make_revenuecat_webhook_router,
    )
    from tempest_fastapi_sdk.tasks import TaskQueue

    from src.billing import apply_event, inbox

    tq = TaskQueue.rabbitmq("amqp://guest:guest@localhost:5672/")


    @tq.task
    async def process_webhook_event(event_id: str) -> None:
        """Process one inbox event.

        Args:
            event_id (str): The inbox row id.
        """
        await inbox.process(UUID(event_id), apply_event)


    async def enqueue(event_id: UUID) -> None:
        """Send the newly recorded event to the worker.

        Args:
            event_id (UUID): The inbox row id.
        """
        await process_webhook_event.enqueue(str(event_id))


    router = make_revenuecat_webhook_router(
        inbox=inbox, secret="dashboard-value", on_recorded=enqueue
    )
    ```

### 2. Authentication: the header and, optionally, HMAC

RevenueCat offers two ways, both documented on the webhooks page:

| Way | Header | What it proves |
| --- | --- | --- |
| *Authorization header* (the value you type in the dashboard) | `Authorization` | that the sender knows the secret |
| *HMAC webhook signing* (opt-in, per integration) | `X-RevenueCat-Webhook-Signature: t=<ts>,v1=<hex>` | that **and** that the body is intact |

```python
from tempest_fastapi_sdk.integrations.subscriptions.revenuecat import (
    make_revenuecat_webhook_router,
)
from tempest_fastapi_sdk.settings import RevenueCatSettings

from src.billing import inbox

settings = RevenueCatSettings()
router = make_revenuecat_webhook_router(
    inbox=inbox,
    secret=settings.REVENUECAT_WEBHOOK_SECRET,
    signing_secret=settings.REVENUECAT_WEBHOOK_SIGNING_SECRET,
)
```

A secret is configured when it holds a value. Every filled secret is
checked, and with both filled a delivery must pass both; an empty one only
turns its own check off. That is why the same code serves "header only",
"HMAC only" and "both", changing nothing but the environment variables.
Rules the SDK applies and the tests pin:

- constant-time comparison (`hmac.compare_digest`);
- the `Authorization` header is accepted with or without a `Bearer ` prefix;
- **no filled secret refuses everything** (`401`) — a forgotten environment
  variable does not turn into an open endpoint;
- `secret=None` together with `signing_secret=None` raises `ValueError` when
  the router is built: there is no unauthenticated route;
- an HMAC signature outside the 5-minute window is refused. According to the
  documentation, `t` is recomputed on every attempt, retries included, so the
  window only covers clock skew and latency.

!!! info "Per-request secret"
    `secret` and `signing_secret` also accept a `Callable[[Request], str]` —
    useful for rotation or for a service with several RevenueCat projects.
    Emptiness is read per request: a function returning `""` turns that
    check off for that delivery only.

### 3. The inbox: once, with an attempt budget

`WebhookInbox` is generic — it knows nothing about RevenueCat. It works the
same for Stripe, OpenPix, Mercado Pago or zap-api: record with the event id
the provider promises is stable across retries.

| Step | What it does | Why this way |
| --- | --- | --- |
| `record(provider, external_id, event_type, payload)` | `INSERT ... ON CONFLICT DO NOTHING` (PostgreSQL and SQLite) | a repeat returns `None` instead of raising |
| `claim(id)` | `UPDATE ... WHERE status = 'received'` | of two workers, only one wins |
| `process(id, handler)` | runs the handler **in the transaction** that marks it `processed` | the database effect and the mark commit together, or neither does |
| `fail(id, error)` | back to `received` with exponential backoff | a dependency that is down gets time to come back |
| `max_attempts` | once reached, the row is `failed` and leaves the sweep | a poisoned event does not run forever |
| `reclaim_stale()` | a row stuck in `processing` comes back (or fails, with no budget left) | a worker that died mid-way does not strand the event |

```python
from tempest_fastapi_sdk.db import WebhookInbox

from src.billing import WebhookEventModel, db

inbox: WebhookInbox[WebhookEventModel] = WebhookInbox(
    db,
    model=WebhookEventModel,
    max_attempts=5,
    backoff_base_seconds=60.0,
    stale_after=600.0,
)
```

!!! warning "Effects outside the database are not in the transaction"
    E-mail, push, a call to another API: if the handler does that and then
    fails, the next attempt does it again. Make that effect idempotent on
    `event.external_id`.

An event that reached `failed` stays in the table for a person to look at
(`last_error` says why). To reprocess it after fixing the cause, set `status`
back to `received` and reset `attempts`.

### 4. From event to state: `normalize`

The documentation lists 21 event types. `normalize(event)` returns a
`SubscriptionSnapshot` with a `SubscriptionStatusEnum` and the fields that
matter (`app_user_id`, `product_id`, `entitlement_ids`, `period_end`,
`grace_period_end`, `reason`, `event_at`...):

| Event | Status |
| --- | --- |
| `INITIAL_PURCHASE`, `RENEWAL`, `UNCANCELLATION`, `NON_RENEWING_PURCHASE`, `PRODUCT_CHANGE`, `SUBSCRIPTION_EXTENDED`, `REFUND_REVERSED` | `TRIAL` when `period_type` is `TRIAL`, `ACTIVE` otherwise |
| `CANCELLATION` with `CUSTOMER_SUPPORT` | `REFUNDED` |
| `CANCELLATION` with `BILLING_ERROR`, `BILLING_ISSUE` | `BILLING_ISSUE` |
| `CANCELLATION` (any other reason) | `CANCELLED` — the period runs to its end |
| `SUBSCRIPTION_PAUSED` | `PAUSE_SCHEDULED` — the documentation says **not** to revoke access here |
| `EXPIRATION` with `SUBSCRIPTION_PAUSED` | `PAUSED` |
| `EXPIRATION` | `EXPIRED` |
| `TEMPORARY_ENTITLEMENT_GRANT` | `TEMPORARY_GRANT` (up to 24 h, during a store outage) |
| `TEST`, `TRANSFER`, `EXPERIMENT_ENROLLMENT`, `VIRTUAL_CURRENCY_TRANSACTION`, `SUBSCRIBER_ALIAS`, `PURCHASE_REDEEMED`, `INVOICE_ISSUANCE`, `PRICE_INCREASE_CONSENT_*` | `NO_CHANGE` |
| a type this release does not know | `UNKNOWN` — never raises |

What each status **means** for your product (which plan unlocks what, how long
the grace period lasts, what a refund takes away) stays in your service. The
SDK delivers the state, not the entitlement.

!!! tip "Out-of-order delivery"
    `snapshot.event_at` is when RevenueCat generated the event. Keep the last
    one applied per user and ignore an older event that arrives late.

### 5. Minimal collection

`subscriber_attributes` is where the app puts `$email`, a phone number and
whatever else it attached to the customer — the documentation's
`INITIAL_PURCHASE` sample carries an `$email`. It is still readable as
`event.subscriber_attributes`, but it stays **out of `model_dump()`**, and
`model_dump()` is what the route stores in the inbox. In the run above, the
`processed False` on the last line is exactly that: the field never reached
the database.

### 6. Reconciliation through the API

The [webhooks page](https://www.revenuecat.com/docs/integrations/webhooks)
recommends re-reading the customer through `GET /subscribers` after any
webhook. `RevenueCatClient.get_subscriber` makes that call with a **secret**
key:

```python
import asyncio

from tempest_fastapi_sdk import HTTPClient
from tempest_fastapi_sdk.integrations.subscriptions.revenuecat import (
    RevenueCatClient,
)
from tempest_fastapi_sdk.settings import RevenueCatSettings


async def main() -> None:
    """Read a user's current state straight from RevenueCat."""
    settings = RevenueCatSettings()
    async with HTTPClient(**settings.revenuecat_kwargs()) as http:
        info = await RevenueCatClient(http).get_subscriber("user-1456")
    for name, entitlement in info.subscriber.entitlements.items():
        print(name, entitlement.expires_date)


asyncio.run(main())
```

!!! danger "The endpoint is *Get or Create*"
    The reference calls the route "Get or Create Customer": an `app_user_id`
    RevenueCat has never seen is **created**. Reconcile only ids that came
    from a webhook or from your own users table. That is also why
    `tempest integrations verify revenuecat` exits with code 2 without calling
    anything: verifying the key through that route would create a customer.

The `app_user_id` reaches the path encoded as a single segment, and `""`,
`"."` and `".."` raise `ValueError` before any request — the dot is not
escaped, and up to v0.334.1 `".."` climbed one level up the path with the
secret key. `FakeRevenueCatClient` refuses the same three.

For tests, `FakeRevenueCatClient` answers what you seed and fails when told
to:

```python
import asyncio

import httpx

from tempest_fastapi_sdk.testing.fakes import FakeRevenueCatClient


async def main() -> None:
    """Seed a customer, read it, and force a network failure."""
    fake = FakeRevenueCatClient()
    fake.set_subscriber("user-1", {"entitlements": {"pro": {}}})
    info = await fake.get_subscriber("user-1")
    print(list(info.subscriber.entitlements))

    fake.fail_next(httpx.ConnectError("down"))
    try:
        await fake.get_subscriber("user-1")
    except httpx.ConnectError as exc:
        print("failed:", exc)


asyncio.run(main())
```

```text
['pro']
failed: down
```

An `app_user_id` nobody seeded raises `httpx.HTTPStatusError` (404), the same
type the real client raises for a non-2xx answer — instead of creating the
customer as the real endpoint would.

## Configuration

| Variable | What for | Empty means |
| --- | --- | --- |
| `REVENUECAT_WEBHOOK_SECRET` | the dashboard's *authorization header* value | the `Authorization` header is not checked; with `REVENUECAT_WEBHOOK_SIGNING_SECRET` empty too, every delivery is refused (`401`) |
| `REVENUECAT_WEBHOOK_SIGNING_SECRET` | the HMAC secret, when enabled | the signature is not checked; with `REVENUECAT_WEBHOOK_SECRET` empty too, every delivery is refused (`401`) |
| `REVENUECAT_API_KEY` | secret key (`sk_...`) for `GET /v1/subscribers` | the call goes out with an empty `Bearer `; the reference documents `401` for an invalid key |

Composed with `EnvironmentSettings`, `RevenueCatSettings` fails the boot under
`ENV=production` when **neither** webhook secret is set — in production that
would mean refusing every purchase and renewal:

```text
ValidationError 1 validation error for Settings
  Value error, ENV=production refuses development values:
- REVENUECAT_WEBHOOK_SECRET: empty, so every RevenueCat webhook is refused
```

## RevenueCat switched off (`REVENUECAT_ENABLED=false`)

`REVENUECAT_ENABLED=false` makes the production guard skip the
both-secrets-empty rule and puts `"revenuecat"` in
`settings.disabled_integrations()`. That is all: the webhook dependency
reads the secrets, not the flag, so with both empty it keeps refusing
every delivery with `401` — nothing is accepted silently. See
[Switching integrations on and off](system-checks.md#switching-integrations-on-and-off-phased-deploys).

## Recap

- `make_revenuecat_webhook_router` authenticates, records in the inbox and
  answers `200`; every filled secret is checked, and with none filled every
  delivery is refused.
- `WebhookInbox` records each event once, processes it under a claim, commits
  the effect with the mark, and stops at `max_attempts` — and works for any
  provider.
- `normalize` turns the 21 event types into one state; a new type becomes
  `UNKNOWN`.
- `subscriber_attributes` is not stored.
- `RevenueCatClient.get_subscriber` reconciles — remembering that the endpoint
  creates the customer when it does not exist.
