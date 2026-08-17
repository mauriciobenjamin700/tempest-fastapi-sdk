# AI agents (database)

The first useful tool anyone writes is almost never a weather lookup — it is a
query against **the application's own database**: "which services exist in
Picos", "how many orders does this customer have", "what is waiting for
approval".

And that raises the question this page answers: **where does the tool get its
session from?**

!!! tip "Before this page"
    Read [AI agents](agents.md) through *Typed tools with Pydantic* and
    [Database](database.md) through *Connecting to the database*. This is
    where the two meet.

## `Depends` cannot reach a tool

In FastAPI the session arrives through `Depends(db.session_dependency)`, and
the dependency is resolved by the framework when the request comes in. An
agent tool never goes down that path:

- the agent is built **once**, not per request;
- the run may not come from HTTP at all — a TaskIQ task, a FastStream consumer
  or a command-line script call the same `agent.run(...)`;
- the caller is the agent loop, which passes only the `arguments` and an
  `AgentContext`. There is no dependency graph there to inject anything into.

Forcing the request's session down to the tool is expensive on both ends: the
agent would have to be rebuilt per request (and `make_agent_router` stops being
usable), and the session would stay open for the **whole** run — which can take
minutes across several steps.

The answer is simpler than it looks: **the tool opens its own session**. That
is exactly what the SDK's own database objects do — `DbFactStore` and
`DbAgentRunSink` take the `AsyncDatabaseManager` and open a session when they
need one.

## The file that runs

```python title="catalog_setup.py" hl_lines="12 32 35 56 57"
import asyncio

from pydantic import Field
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import Mapped, mapped_column

from tempest_fastapi_sdk import AsyncDatabaseManager, BaseModel, BaseRepository
from tempest_fastapi_sdk.agents import Agent, AgentContext, tool
from tempest_fastapi_sdk.genai import TextGenerator, TextModel
from tempest_fastapi_sdk.schemas import BaseSchema

db = AsyncDatabaseManager("sqlite+aiosqlite:///catalog.db")


class ServiceModel(BaseModel):
    """A service published in the catalogue."""

    __tablename__ = "services"

    name: Mapped[str] = mapped_column()
    city: Mapped[str] = mapped_column()
    state: Mapped[str] = mapped_column()


class CatalogService:
    """Business logic over the service catalogue."""

    def __init__(self, repository: BaseRepository[ServiceModel]) -> None:
        """Store the repository the service delegates to."""
        self.repository = repository

    @classmethod
    def from_session(cls, session: AsyncSession) -> "CatalogService":
        """Assemble the service over a single database session."""
        return cls(BaseRepository(session, model=ServiceModel))

    async def search(self, city: str | None, limit: int) -> list[ServiceModel]:
        """Return the services published in a city."""
        filters = {"city": city} if city else {}
        page = await self.repository.paginate(filters=filters, page_size=limit)
        return list(page["items"])


class SearchArgs(BaseSchema):
    """Arguments the model may choose when searching the catalogue."""

    city: str | None = Field(
        default=None,
        description="City to search in, e.g. 'Picos'. Omit to skip the filter.",
    )


@tool("search_services", "Search published services by city.")
async def search_services(args: SearchArgs, _context: AgentContext) -> str:
    """Search the catalogue and describe the matches in one line each."""
    async with db.get_session_context() as session:
        catalog = CatalogService.from_session(session)
        found = await catalog.search(args.city, limit=5)

    if not found:
        return "No service found. Try another city."
    return "\n".join(f"- {item.name} ({item.city}/{item.state})" for item in found)


def build_agent() -> Agent:
    """Build the agent the rest of this page imports."""
    return Agent(
        TextGenerator(TextModel.QWEN2_5_0_5B_INSTRUCT),
        tools=[search_services],
        system_prompt=(
            "You help people find services. Always use the search_services "
            "tool and never invent a service it did not return."
        ),
    )


async def main() -> None:
    """Create the table, seed one row and ask the agent about it."""
    await db.create_tables()
    async with db.get_session_context() as session:
        session.add(ServiceModel(name="Electrical install", city="Picos", state="PI"))

    run = await build_agent().run("Find me services in the city of Picos.")

    print(run.output)
    print(run.tool_calls)


if __name__ == "__main__":
    asyncio.run(main())
```

```bash
python catalog_setup.py
```

```text
I found one service in Picos: Electrical install (Picos/PI).
['search_services']
```

!!! info "Every example on this page is a file that runs"
    Save the block above as `catalog_setup.py`. The next blocks are complete
    files sitting next to it that import what already exists
    (`from catalog_setup import db`) instead of repeating the setup.

## Block by block

**The manager lives at module level.** `db = AsyncDatabaseManager(...)` is
created once, at import time, and is the same object for every agent run. It
opens no connection at that point — the engine comes up on the first session
requested, or on `connect()` in the lifespan.

**`from_session` belongs to your application, not to the SDK.** The highlighted
classmethod is the only place that knows which repositories that service needs.
Here it is one; in a real service it is several — which is precisely why the SDK
cannot offer a generic `from_session`. Only you know the list.

It is worth adopting as a project convention: **every service gets a
`from_session`**, and becomes assemblable by any consumer that has no request —
agent, task, consumer, script, seed.

**The `async with` sits inside the tool.** That line is what defines the
session's lifetime, and the next section is entirely about it.

**What the tool returns is text for the model to read.** It is not your API
response. We come back to that at the end.

## One session per tool call

Notice where the `async with` is **not**: neither wrapped around
`agent.run(...)` nor stored on the agent. It opens when the tool is called and
closes when it returns.

| | Session per run | Session per tool call |
| --- | --- | --- |
| Where the `async with` goes | around `agent.run(...)` | inside the tool |
| Time the connection is held | the whole run | one query |
| A 40s run with 4 steps | 40s of connection held | ~4 × a few ms |
| Two tools in the same run | share the session | one session each |

The reason is the clock. An agent run is slow by nature: every step waits for
the model to generate tokens. Holding a pool connection through that wait
wastes it — worse in production, where several runs happen at once against a
pool of fixed size.

!!! check "Tool calls are sequential"
    The agent loop executes one call at a time, so a per-call session is never
    used by two concurrent tasks — which is what would break a shared
    `AsyncSession`.

## The commit belongs to the context manager

`get_session_context()` **commits on exit** and rolls back if you raise. That
differs from the request session:

| Form | Commits on success | What it is for |
| --- | --- | --- |
| `db.session_dependency` | no — the service layer commits | an HTTP request |
| `db.get_session_context()` | **yes**, on leaving the `async with` | agent tool, task, script |
| `db.get_session()` | no — you close it yourself | uncommon cases |

In a **read** tool this makes no difference: there is nothing to write.

!!! warning "A writing tool: one transaction per call"
    If the tool writes, each call becomes its own transaction that commits on
    its own. An agent calling `create_order` and then `debit_balance` has **no**
    atomicity across the two: the first has already committed when the second
    fails.

    When two writes must land together, they belong to the same tool — open one
    session and do both inside it, letting the service decide. The agent picks
    *what* to do; the transaction is still designed by you.

## One manager per process

`AsyncDatabaseManager` carries the engine and the pool. Two of them in the same
process are two pools against the same database, and neither knows about the
other:

```python title="wrong_second_manager.py"
from tempest_fastapi_sdk import AsyncDatabaseManager

db = AsyncDatabaseManager("postgresql+asyncpg://app:secret@localhost/app")
agent_db = AsyncDatabaseManager("postgresql+asyncpg://app:secret@localhost/app")
```

In practice this happens by accident: the app already has its manager in
`resources.py`, and the agent module creates another one "just for the tools".
Connection usage doubles, and the Postgres limit was never sized for that.
Import the one that already exists:

```python title="app_agent.py"
from fastapi import FastAPI

from catalog_setup import build_agent
from tempest_fastapi_sdk.agents import make_agent_router

app = FastAPI()
app.include_router(make_agent_router(build_agent()))
```

!!! danger "Dispose the engine on shutdown"
    `get_session_context()` connects on its own on the first call, so a tool
    works even without `connect()`. What it does not do is close: without
    `await db.disconnect()` in the lifespan, the process ends with the pool
    open. The full lifecycle is in
    [Database » Lifecycle in the lifespan](database.md#lifecycle-in-the-lifespan).

## The `context` parameter

Every tool receives two arguments: the already-validated `arguments` and an
`AgentContext`. In the search above it goes unused — and that is fine. The
signature is fixed because the loop calls every tool the same way.

!!! tip "Convention"
    Name it `_context` when you do not use it, as `catalog_setup.py` does. The
    reader immediately knows nothing is hidden in there.

It stops being decorative the minute the tool needs something the **model must
not choose**. That is the split: `arguments` is what the model decided;
`context` is what your application knows.

### Who is asking

The most important case of all. If `user_id` were a field on `SearchArgs`, a
user could write "show me that other person's orders" and the model would
comply. Identity is never a tool argument.

You seed the context in the endpoint, with the user authentication already
resolved:

```python title="app_ask.py" hl_lines="20"
from typing import Any
from uuid import UUID

from fastapi import Depends, FastAPI

from catalog_setup import build_agent
from tempest_fastapi_sdk.agents import AgentContext


async def get_current_user_id() -> UUID:
    """Stand-in for the project's real authentication dependency."""
    return UUID("11111111-1111-1111-1111-111111111111")


app = FastAPI()
agent = build_agent()


@app.post("/api/ask")
async def ask(question: str, user_id: UUID = Depends(get_current_user_id)) -> dict[str, Any]:
    """Answer a question with the caller's identity pinned to the run."""
    run = await agent.run(question, context=AgentContext(state={"user_id": user_id}))
    return {"output": run.output, "stop_reason": run.stop_reason}
```

And the tool reads from there, never from the arguments:

```python title="owner_tool.py" hl_lines="16"
from pydantic import Field

from catalog_setup import CatalogService, db
from tempest_fastapi_sdk.agents import AgentContext, AgentToolError, tool
from tempest_fastapi_sdk.schemas import BaseSchema


class MyServicesArgs(BaseSchema):
    """Arguments for listing the caller's own services."""

    city: str | None = Field(default=None, description="Optional city filter.")


@tool("get_my_services", "List the services owned by the current user.")
async def get_my_services(args: MyServicesArgs, context: AgentContext) -> str:
    """List the caller's own services, never anyone else's."""
    user_id = context.state.get("user_id")
    if user_id is None:
        raise AgentToolError("no authenticated user in this run")

    async with db.get_session_context() as session:
        catalog = CatalogService.from_session(session)
        found = await catalog.search(args.city, limit=5)

    return "\n".join(f"- {item.name} ({item.city})" for item in found) or "Nothing of yours here."
```

Reading top to bottom: the endpoint puts `user_id` on `state`; `state` travels
with the whole run; the tool takes it from there and hands it to the service.
The model never sees that value and has no way to swap it.

!!! warning "`make_agent_router` does not seed the context"
    The ready-made router calls `agent.run(goal)` with no context — it knows
    nothing about your authentication. When a tool needs to know who is asking,
    write the endpoint yourself, as above.

### Scratch space shared between tools

`state` is a free-form dict that lives for **one run** and disappears at the
end. It lets one tool leave something for the next without returning it to the
model — an id already resolved, a decision made, a count:

```python title="share_state.py"
from uuid import UUID

from tempest_fastapi_sdk.agents import AgentContext


def remember_category(context: AgentContext, category_id: UUID) -> None:
    """Leave the resolved category id for the next tool of this run."""
    context.state["resolved_category_id"] = category_id
```

The SDK itself uses that space: the notepad
([`scratchpad_tools`](agents-advanced.md#scratchpad-within-one-run)), loaded
skills and structured output all keep their state there. Prefix your keys with
something from your domain so they never collide.

### Files one tool hands to another

`context.artifacts` is what lets an agent generate an image and then look at
it, with no disk and no base64 in the prompt. One tool registers the
`AgentArtifact` on its `ToolResult` and the other picks it up by name:

```python title="read_artifact.py"
from tempest_fastapi_sdk.agents import AgentArtifact, AgentContext


def load_report(context: AgentContext) -> AgentArtifact:
    """Read back an artifact an earlier tool registered on this run."""
    return context.require_artifact("report.pdf")
```

`require_artifact` fails with a message the model can read and correct, instead
of a `KeyError`. The full chain is in
[AI agents » Chaining multimodal](agents.md#chaining-multimodal-draw-then-look).

### The run's clock

`context.deadline` is the instant (`time.monotonic()`) this run must stop at,
already accounting for this agent's budget **and** the budget of whoever
delegated to it. An expensive tool asks before starting:

```python
import time

from tempest_fastapi_sdk.agents import AgentContext, AgentToolError


def guard_deadline(context: AgentContext) -> None:
    """Refuse to start expensive work when the run is out of time."""
    if context.deadline is not None and time.monotonic() >= context.deadline:
        raise AgentToolError("no time left for this search")
```

Without it, a slow tool blows past the time a sub-agent inherited from its
parent — and the parent is the one holding a request open.

### Where this run sits in the tree

`depth` and `parent` only matter in multi-agent setups: `depth` is how many
delegations down you are (`0` is the top) and it is what stops A delegating to
B delegating back to A forever. Details in
[AI agents (advanced) » Delegating to another agent](agents-advanced.md#delegating-to-another-agent).

## What the tool returns to the model

The tool's return value is read by the model and, more often than not, repeated
to the user. It is not your API response — and treating the two as the same
thing costs you on the three points below.

**Return little.** Serializing a whole page spends the context window on UUIDs,
timestamps and nested relations the model never reasons about. One line per
record is enough, and it is what the example does.

**Pin what is your rule.** Filters that exist for security or domain reasons —
"active only", "owner only", "no contact details" — stay hardcoded in the tool,
outside the arguments schema. Whatever is in the schema is the model's choice,
and the model's choice is the choice of whoever wrote the prompt.

**Watch for private fields.** If your service response carries an address, a
phone number or an email, and the tool returns the whole object, the agent
publishes it in its answer. Build the string from the fields that may be read.

## Recap

- **`Depends` cannot reach a tool** — the agent does not live inside a request,
  so the tool opens its own session.
- **`async with db.get_session_context()` inside the tool**, one session per
  call: the connection is not held while waiting on the model.
- **`get_session_context` commits** on exit; in a writing tool that is one
  transaction per call, and writes that must land together belong to the same
  tool.
- **`from_session` is your application's convention** — only it knows how to
  assemble the service, and it serves every consumer without a request.
- **One `AsyncDatabaseManager` per process**, with `disconnect()` in the
  lifespan.
- **`context` carries what the model must not choose** — above all, who is
  asking. `make_agent_router` does not seed the context.
- **The return value is text for the model**: short, with security filters
  pinned in code and no private fields.

Next step: [AI agents (advanced)](agents-advanced.md) for durable memory,
skills and delegation; [AI agents (testing)](agents-testing.md) to test the
tool without loading a model at all.

See also: [AI agents (architecture)](agents-architecture.md) for where each piece
lives once the service outgrows a single tool; [Database](database.md) for
repositories, pagination and migrations; [Background tasks](queue-tasks.md) for
the other consumer that has no request.
