# Recommended architecture

The [Architecture](architecture.md) page says **which layers exist**. This
one says **how each file inside them is shaped**, in a way that works the
same for someone joining the project and for an AI writing code in it: a
file's name says what it holds, the dependency graph is predictable, and the
suite explains what each test proves.

Every rule here has **a code** that `tempest check` enforces. That is the
point: in a real service (`transport-backend`, recorded in
[#616](https://github.com/mauriciobenjamin700/tempest-fastapi-sdk/issues/616)),
the rules that lived only in `CLAUDE.md` were broken in hundreds of places
when the first guard was written. Prose is not a gate.

!!! tip "`tempest new` already ships this way"
    The project `tempest new` writes passes every rule on this page, errors
    and warnings. If you are starting out, this page explains what is
    already on your disk. If you have an older service, jump to
    [Migrating an existing service](#migrating-an-existing-service).

## How the gate enforces it

`tempest lint-layers` reads the source with `ast` and **never imports the
project**. Each violation is one `file:line: CODE message` line, and the
message says what to change. The rules on this page enter as **warnings**:
they print with `(warning)` and do not fail `tempest check` until you turn
on strict mode:

```toml
[tool.tempest]
layers_strict = true
```

To migrate one rule at a time, filter:

```bash
tempest lint-layers --rule IMP002
```

The full code table, with each one's real output, is in
[CLI › Layer rules](recipes/cli.md#layer-rules-tempest-lint-layers).

## 1. One class per file, in a context package

**Why:** the file name becomes an index. `services/billing/invoice.py` holds
`InvoiceService` and nothing else — whoever looks for the invoice service
finds it without opening anything, and a diff touching the file touches only
it. A `models/route.py` with five models forces you to open it to learn what
is inside.

The four layers holding classes — `controllers/`, `services/`,
`db/repositories/` and `db/models/` — keep each module in a **context
package** (a plural noun of the domain: `products`, `orders`, `billing`),
with one class per module.

❌ Before:

```python
# src/db/models/route.py
from sqlalchemy.orm import Mapped, mapped_column
from tempest_fastapi_sdk import BaseModel


class RouteModel(BaseModel):
    """A delivery route."""

    __tablename__ = "routes"

    name: Mapped[str] = mapped_column()


class StopModel(BaseModel):
    """A stop on a route."""

    __tablename__ = "stops"

    address: Mapped[str] = mapped_column()
```

✅ After — one file per model, and the package re-exporting:

```python
# src/db/models/routing/route.py
from sqlalchemy.orm import Mapped, mapped_column
from tempest_fastapi_sdk import BaseModel


class RouteModel(BaseModel):
    """A delivery route."""

    __tablename__ = "routes"

    name: Mapped[str] = mapped_column()
```

```python
# src/db/models/routing/__init__.py
from .route import RouteModel as RouteModel
from .stop import StopModel as StopModel

__all__: list[str] = ["RouteModel", "StopModel"]
```

| Code | Reports |
| --- | --- |
| `LAY001` | a second class or a module-level function in `controllers/`, `repositories/` or `models/` |
| `LAY002` | a module directly at the root of one of the four layers |
| `SVC002` (error) | the same in `services/` |

The context name is free: the list the project's `CLAUDE.md` suggests is a
guide, not a limit.

## 2. Import through the layer package; relative `__init__`

**Why:** a package's internal path is its own business. Code importing
`from src.services.billing.invoice import InvoiceService` breaks the day the
file moves; code importing `from src.services import InvoiceService` does
not. And an `__init__.py` importing its neighbours by absolute path repeats
the package's own name on every line.

✅ The package imports its neighbours relatively; the rest of the code
imports through the package:

```python
# src/services/__init__.py
from .billing import InvoiceService as InvoiceService
from .products import ProductService as ProductService

__all__: list[str] = ["InvoiceService", "ProductService"]
```

```python
# src/controllers/billing/invoice.py
from tempest_fastapi_sdk import BaseController

from src.schemas import InvoiceResponseSchema
from src.services import InvoiceService


class InvoiceController(BaseController[InvoiceService, InvoiceResponseSchema]):
    """Invoice coordination."""
```

| Code | Reports |
| --- | --- |
| `IMP001` | an `__init__.py` importing a module of its own package by absolute path |
| `IMP002` | importing a name from its submodule when a package above already re-exports it (followed transitively, through `import *` too) |

!!! warning "When the package would close a cycle"
    `IMP002` suggests the top-most package that re-exports the name
    **without containing** the importing module. Even so, importing through
    the package sometimes closes a cycle — in the transport service, tasks
    and scripts imported `api.dependencies.resources` directly, because
    importing the `api.dependencies` package would close a cycle with
    `src.tasks`. That case is a [declared exception](#declared-exception),
    not an import rewrite.

## 3. What services and controllers receive

**Why:** a service taking the `AsyncSession` builds its own repositories, so
nobody outside can swap them in a test. A service taking another service
hides an orchestration that is the controller's job. And a class with a
`from_session` knows how to build its own graph: the knowledge of "what needs
what" spreads over every class, and every call site repeats the assembly.

❌ Before:

```python
# src/services/wallet/settlement.py
from sqlalchemy.ext.asyncio import AsyncSession

from src.db.repositories import SettlementRepository


class SettlementService:
    """Settles the held balance."""

    def __init__(self, session: AsyncSession) -> None:
        """Keep the session."""
        self.repository = SettlementRepository(session)

    @classmethod
    def from_session(cls, session: AsyncSession) -> "SettlementService":
        """Build the service over a session."""
        return cls(session)
```

✅ After — the service receives the repository, and the graph is assembled
in one place, the **composition root**:

```python
# src/scripts/composition.py
from sqlalchemy.ext.asyncio import AsyncSession

from src.controllers import SettlementController
from src.db.repositories import SettlementRepository
from src.services import SettlementService


def build_settlement_controller(session: AsyncSession) -> SettlementController:
    """Assemble the settlement controller over one session.

    Args:
        session (AsyncSession): The session the repository reads through.

    Returns:
        SettlementController: The controller, fully wired.
    """
    return SettlementController(SettlementService(SettlementRepository(session)))
```

The request provider in `api/dependencies/controllers.py` calls
`build_settlement_controller(session)` with the session `Depends` resolved; a
task, a queue consumer, a seed and the suite call the same function. The
graph is written once.

| Code | Reports |
| --- | --- |
| `DI001` | an `AsyncSession` parameter in a service or controller `__init__` |
| `DI002` | a service taking a `*Service`, a controller taking a `*Controller` |
| `DI003` | a function or method named `from_session`, anywhere |

The real case where two services must go together exists — in the transport
service, the settlement and the wallet credit land in the same transaction.
It becomes a [declared exception](#declared-exception), aimed at that one
parameter. For the SDK's wallet, since v0.330.0 not even that: the balance
write is the
[`WalletLedger`](recipes/wallet.md#the-ledger-not-the-service-walletledger),
a data layer the service receives the way it receives a repository.

## 4. A repository does not import a repository

**Why:** a query over two tables is a `select()` with a `join`, in the
repository that owns the query. A repository importing another to "reuse" a
query creates a dependency between data classes no service sees, and
combining two repositories is exactly the service's job.

| Code | Reports |
| --- | --- |
| `REPO002` | importing a `*Repository` inside `repositories/` (a name used only as a class base — the project's base repository — passes) |

## 5. One router per file; `/api` once

**Why:** with `/api` repeated in every `include_router` (22 times, in the
transport service), changing the API prefix or mounting a domain outside it
turns into a prefix hunt. And two routers in one file hide one of them from
whoever looks by name.

✅ Each domain declares only its own prefix; `/api` appears in one place:

```python
# src/api/routers/api.py
from fastapi import APIRouter

from .orders import router as orders_router
from .service_info import router as service_info_router

API_PREFIX: str = "/api"
"""Prefix every business route lives under."""


def build_api_router() -> APIRouter:
    """Assemble the router ``create_app`` includes.

    Returns:
        APIRouter: ``/api`` with every domain router included.
    """
    api: APIRouter = APIRouter(prefix=API_PREFIX)
    api.include_router(service_info_router)
    api.include_router(orders_router)
    return api
```

| Code | Reports |
| --- | --- |
| `RTR003` | more than one `APIRouter(...)` in a `routers/` module |
| `RTR004` | a `/api` prefix (or below it) outside a `build_api_router` function |

## 6. No nested function, no private module helper

**Why:** a function defined inside another has no importable name, no test of
its own, and carries state through a closure, invisible in its signature. A
module-level `_x` function is a helper with no home: nobody reuses it, and it
drops out of the package's index.

❌ Before:

```python
# src/utils/notify.py
from collections.abc import Awaitable, Callable


def make_sender(channel: str) -> Callable[[str], Awaitable[None]]:
    """Build a sender for the channel."""

    async def send(message: str) -> None:
        """Send the message."""
        print(channel, message)

    return send
```

✅ After — state becomes a callable object; a fixed argument becomes
`functools.partial` over a module-level function:

```python
# src/utils/notify.py
from collections.abc import Coroutine
from functools import partial
from typing import Any


async def send(message: str, *, channel: str) -> None:
    """Send the message through the channel.

    Args:
        message (str): The text.
        channel (str): Where it goes.
    """
    print(channel, message)


class ChannelSender:
    """A sender bound to a channel, with visible state."""

    def __init__(self, channel: str) -> None:
        """Keep the channel.

        Args:
            channel (str): Where the messages go.
        """
        self.channel: str = channel

    async def __call__(self, message: str) -> None:
        """Send a message through the stored channel.

        Args:
            message (str): The text.
        """
        await send(message, channel=self.channel)


send_by_email: partial[Coroutine[Any, Any, None]] = partial(send, channel="email")
"""The same sender, with the channel already fixed."""
```

| Code | Reports |
| --- | --- |
| `STR001` | a function defined inside another (in the source **and** the suite) |
| `STR002` | a module-level function whose name starts with a single `_` (in the source **and** the suite) |

In the suite, the private helper becomes a fixture or a public helper module
— in the transport service there were 174 nested functions and 76 `_x`
helpers in the tests alone.

## 7. A task is registration only

**Why:** the API process should not import the tasks' code — and with it the
whole graph they use. A 400-line `tasks.py` mixes registration, rules and
scheduling.

The task layer `tempest new --extras tasks` writes splits it like this:

| File | Role |
| --- | --- |
| `tasks/names.py` | `*_TASK_NAME` and `*_SCHEDULE` (with `every_n_minutes` / `daily`); imports nothing from the project |
| `tasks/example.py` | registration only: `@tq.cron(EXAMPLE_SCHEDULE, name=EXAMPLE_TASK_NAME)`, calling the script |
| `scripts/example.py` | the work, callable with or without a worker |
| `tasks/dispatch.py` | `TaskDispatcher`: the API enqueues **by name**, without importing the task |
| `tasks/worker.py` | imports the task modules and exposes `broker` / `scheduler` |

Sending by name uses
[`TaskQueue.enqueue_by_name`](recipes/queue-tasks.md#enqueue-by-name-enqueue_by_name),
and the task's retry policy still applies — the worker restores the labels
it was registered with.

!!! danger "A task name is for life"
    A queued message keeps the name. Renaming `EXAMPLE_TASK_NAME` strands the
    old messages: the worker logs `task "<name>" is not found` and drops
    them. When reorganizing tasks, **move the code and keep the name**.

## 8. Documented schema

**Why:** the client reads the `openapi.json`. A field without `title`,
`description` and `examples` arrives there with a name and a type only — 370
fields, in the transport service.

A leaf field carries all three; a field holding another schema, only
`title` and `description`. This is the only rule that has to import the
project, so it runs in the suite (`assert_schema_metadata`) and in the
`tempest lint-schemas` command — details in
[CLI › Schema metadata](recipes/cli.md#schema-metadata-tempest-lint-schemas).

## 9. The suite explains what it proves

**Why:** a test is executable documentation. The docstring says the
situation it builds and what it proves; a test that checks nothing always
passes; `pytest.raises(Exception)` passes with any bug; and a drawn value in a
`parametrize` id makes each `pytest -n` worker collect different tests — the
transport suite did not run in parallel because of it (4 min serially, 47 s
in parallel after the fix).

✅ The shape:

```python
# tests/test_orders.py
from fastapi.testclient import TestClient

from src.api.app import create_app


def test_a_duplicate_order_is_refused() -> None:
    """Cenário: the same order sent twice.

    Esperado: the second answers ``409`` with ``ORDER_DUPLICATED``.
    """
    with TestClient(create_app()) as client:
        payload = {"code": "A-1"}
        assert client.post("/api/orders", json=payload).status_code == 201
        duplicate = client.post("/api/orders", json=payload)
    assert duplicate.status_code == 409
    assert duplicate.json()["code"] == "ORDER_DUPLICATED"
```

| Code | Reports |
| --- | --- |
| `TST001` | a test docstring without `Cenário:` and `Esperado:` |
| `TST002` | a test with no `assert`, no `pytest.raises`/`warns` and no `assert*` call |
| `TST003` | `pytest.raises(Exception)` or `BaseException` |
| `TST004` | a `parametrize` id holding a value drawn at import |

`TST004` follows the measurement, not the assumption: a **bare** `uuid4()` or
`datetime.now()` is ided by position and passes; it becomes a problem once it
turns into text (`str(uuid4())`, `uuid4().hex`) or sits in `ids=`.

## Declared exception

Every rule admits the case you keep on purpose — **with its reason
written**, in one place, reviewed in the diff like code:

```python
# tests/architecture_exceptions.py
ARCHITECTURE_EXCEPTIONS: dict[str, str] = {
    "DI002 src/services/wallet/settlement.py::SettlementService.wallet_service": (
        "the settlement and the credit land in the same transaction"
    ),
}
```

The key is `"<CODE> <path>"` (every finding of that code in the file) or
`"<CODE> <path>::<subject>"` (only the one about that name). An entry that no
longer matches anything prints as `EXC002`: an exception does not outlive the
code it excused.

## Migrating an existing service

Start by **measuring**. The count per code tells how much work there is and
where to start:

```bash
tempest lint-layers | awk '{print $2}' | grep -E '^[A-Z]+[0-9]+$' | sort | uniq -c
```

The 0.328.0 scaffold itself, with no extras, gave this count (measured):

```text
     11 IMP001
      8 IMP002
      1 LAY002
      1 RTR004
      3 TST001
```

Then take on **one family per PR**, each with `--rule` until it reaches zero:

1. **`IMP001`/`IMP002`** first: it is mechanical, changes no behaviour and
   makes the next changes cheaper — moving a file no longer breaks an
   import.
2. **`LAY001`/`LAY002`**: move each class into its context package,
   re-exporting relatively. With step 1 done, the call sites do not change.
3. **`DI001`–`DI003`**: create `scripts/composition.py` and make the
   providers call the `build_*` functions; delete the `from_session`s.
4. **`RTR003`/`RTR004`**: create `routers/api.py` and take `/api` out of the
   domains.
5. **`STR001`/`STR002`**: stateful callbacks become classes; a fixed
   argument becomes `partial`.
6. **`TST001`–`TST004`**: the suite — by far the biggest bill in the transport
   service (1380 of 1394 tests without the docstring shape).
7. **Tasks and schemas**, when the service has them: split registration from
   work, and document the fields.

Three traps the transport service hit on the way:

!!! warning "Import cycle through the resources"
    Importing `api.dependencies` (the package) from the tasks and scripts
    would close a cycle with `src.tasks`. There, tasks and scripts kept
    importing `api.dependencies.resources` directly — with an `IMP002`
    [declared exception](#declared-exception).

!!! warning "Task names preserved"
    When splitting the big `tasks.py` into domain modules, a task changes
    file but **not name** — or the messages already queued are stranded. Pin
    the old name in the `*_TASK_NAME` constant.

!!! warning "pytest-xdist ids"
    A `parametrize` with `uuid4()` turned into text in the id blocked
    `pytest -n auto`. Swap it for fixed literals before turning parallel on.

Once a family reaches zero, turn on `layers_strict = true` — from then on, a
warning that comes back fails `tempest check`.

## Recap

- **One class per file**, in `<layer>/<context>/`; the package re-exports
  relatively and the rest imports through the package.
- **A service receives repositories; a controller receives services.** The
  graph is assembled in `scripts/composition.py`, and nobody has a
  `from_session`.
- **A repository does not import a repository.**
- **One router per file**, and `/api` only in `build_api_router`.
- **No nested function and no module `_helper`**, in the source and the
  suite.
- **A task is registration only**, with a fixed name; the API enqueues by
  name.
- **Documented schema**, checked by `assert_schema_metadata`.
- **Tests with `Cenário:`/`Esperado:`** that check something, with a specific
  exception and a deterministic `parametrize`.
- All of it is a **warning** until you turn on `layers_strict`; what you keep
  on purpose goes in `tests/architecture_exceptions.py`, with its reason.
