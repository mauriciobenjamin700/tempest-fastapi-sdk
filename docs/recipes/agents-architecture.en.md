# AI agents (architecture)

One tool and one agent fit comfortably in a single file. The **second** agent
does not: it wants the same tools behind a different prompt, and suddenly the
file that was clear is the place where everything lives. This page is the layout
that survives the third one — and the reason each folder exists.

!!! tip "Before this page"
    [AI agents](agents.md) for `@tool` and the loop, and
    [AI agents (database)](agents-db.md) for the session inside a tool. Here we
    build the whole service around them.

## The layout

```text
src/
├── ai/
│   ├── runtime.py        # the process's model + the budget every run is held to
│   ├── policy.py         # who is asking, and how a tool asks for it
│   ├── schemas/          # the arguments the model may choose
│   ├── views/            # the text the model reads back
│   ├── prompts/          # one system prompt per agent
│   ├── tools/            # per domain, mirroring services/
│   └── agents/           # composition: model + tools + prompt
├── controllers/          # orchestration; this is what calls agent.run
├── services/             # the business rules a tool consumes
└── db/
    └── manager.py        # the process's AsyncDatabaseManager
```

`ai/` sits **beside** `services` and `controllers`, not inside either. An agent
is not one domain's implementation detail: it cuts across several.

## The rule the rest rests on: import direction

| Layer | May import | Never imports |
| --- | --- | --- |
| `api/routers` | `controllers`, `schemas` | `ai`, `db` |
| `controllers` | `ai`, `services`, `schemas` | `api` |
| `ai` | `services`, `schemas`, `db/manager` | `api`, `controllers`, `db/repositories` |
| `services` | `db/repositories` | `ai` |

Two consequences worth saying out loud:

* **The router never talks to the agent.** A controller calls `agent.run` — it
  seeds the identity, decides what to do with `stop_reason` and translates the
  `AgentRun` into the HTTP response. The day the same question arrives from a
  task or a consumer, the entry point already exists.
* **A tool never talks to a repository.** It goes through a service like every
  other caller. What `ai` imports from `db` is the manager only —
  infrastructure, not the data layer.

## `runtime.py` — the model belongs to the process

```python title="src/ai/runtime.py"
from tempest_fastapi_sdk.agents import AgentBudget
from tempest_fastapi_sdk.genai import TextGenerator

from src.core.settings import settings

generator: TextGenerator = TextGenerator(
    settings.AI_MODEL_ID,
    local_files_only=settings.AI_LOCAL_FILES_ONLY,
    idle_unload_seconds=settings.AI_IDLE_UNLOAD_SECONDS,
)


def agent_budget() -> AgentBudget:
    """Return the ceilings every agent run is held to.

    Returns:
        AgentBudget: Steps, wall-clock seconds and tool calls, from settings.
    """
    return AgentBudget(
        max_steps=settings.AI_MAX_STEPS,
        max_seconds=settings.AI_MAX_SECONDS,
        max_tool_calls=settings.AI_MAX_TOOL_CALLS,
    )
```

A `TextGenerator` owns model weights. Building one inside `build_agent()` looks
harmless while there is one agent — the day the second arrives, that is two
copies of the model in memory. At 0.5B nobody notices; at a quantized 7B it is
gigabytes.

!!! tip "`idle_unload_seconds` gives memory back for free"
    In an API that serves far more plain HTTP than agent questions, the model
    unloads itself between conversations and reloads on the next one. It costs
    no extra code — it costs one settings field.

!!! warning "The budget is what protects the request"
    Keep `max_seconds` **below** the proxy timeout. Past it the client is gone
    and the GPU is still working for nobody.

## `tools/` apart from `agents/`

A tool is the **reusable** unit; an agent is a **composition**. A support agent
will want `search_services` without inheriting the catalogue agent's prompt —
and that is exactly what becomes impossible when both live in one file.

`tools/` mirrors `services/`: whoever knows `services/job.py` finds
`tools/service.py` without searching.

## `schemas/` — the model's contract is not the HTTP contract

Your endpoint's pagination filter makes a poor tool-arguments model. It carries
ids the model cannot guess, mutually exclusive flags whose wrong combination
raises, and length floors that turn a reasonable guess into a validation error.

```python title="src/ai/schemas/service.py"
from pydantic import Field
from tempest_fastapi_sdk import BaseSchema, CityNameField, UFField


class ServiceSearchArgsSchema(BaseSchema):
    """Arguments for searching the public service catalogue."""

    name: str | None = Field(
        default=None,
        description="Partial match on the service title. Omit to skip it.",
        max_length=255,
    )
    state: UFField | None = Field(
        default=None,
        description="Two-letter state code, e.g. 'PI'.",
    )
    city: CityNameField | None = Field(
        default=None,
        description="City the service is offered in, e.g. 'Picos'.",
        max_length=255,
    )
    page: int = Field(
        default=1,
        ge=1,
        description="1-indexed page. Use it only to see more of the same search.",
    )
```

What the model may **not** choose simply is not here — it is pinned in the
translation to the domain filter:

```python title="src/ai/tools/_filters.py"
from src.schemas import ServicePaginationFilterSchema

PAGE_SIZE: int = 5


def service_filters(
    *,
    name: str | None = None,
    state: str | None = None,
    city: str | None = None,
    page: int = 1,
) -> ServicePaginationFilterSchema:
    """Translate what the model chose into the domain filter.

    Returns:
        ServicePaginationFilterSchema: The filter, with the fields the model may
            not choose pinned here — active rows only, no embedded candidates,
            and a page small enough not to flood the context.
    """
    return ServicePaginationFilterSchema(
        name=name,
        state=state,
        city=city,
        page=page,
        page_size=PAGE_SIZE,
        is_active=True,
        include_candidates=False,
    )
```

The rule is short: **a field in the schema is the model's choice.** Security,
privacy and size limits live in the translation, where no well-crafted sentence
reaches them.

## `views/` — what the model reads back

```python title="src/ai/views/service.py"
from src.schemas import ServicePaginationSchema

EMPTY_SEARCH: str = (
    "No service matched those filters. "
    "Relax one at a time: the city first, then the state."
)


def render_services(page: ServicePaginationSchema, *, empty: str) -> str:
    """Flatten a page of services into the few lines a model needs.

    Args:
        page (ServicePaginationSchema): The page the service layer returned.
        empty (str): What to say when nothing matched — it doubles as the
            model's instruction on what to try next.

    Returns:
        str: The totals, one line per service, and an explicit next-page hint.
    """
    if not page.items:
        return empty

    lines: list[str] = [f"{page.total} service(s) — page {page.page} of {page.pages}."]
    lines.extend(f"- {item.name} | {item.city}/{item.state}" for item in page.items)
    if page.page < page.pages:
        lines.append(f"More results: call again with page={page.page + 1}.")
    return "\n".join(lines)
```

Three things you gain by isolating this in a pure function:

* **one place decides what leaves.** Address, phone, email — the decision not to
  leak lives in one file instead of scattered across N tools;
* **it tests with no agent, no model and no database.** Schema in, string out;
* **adjusting is cheap.** "The model keeps confusing city with state" becomes a
  formatting edit, not a change to the tool.

!!! warning "The tool's return value is read by the user"
    The model repeats what the tool returned. Returning your whole response
    schema publishes every field it holds into the chat.

## `policy.py` — identity is never an argument

```python title="src/ai/policy.py"
from uuid import UUID

from tempest_fastapi_sdk.agents import AgentContext, AgentToolError

USER_ID_KEY: str = "user_id"


def context_for(user_id: UUID) -> AgentContext:
    """Build the context a run started by an authenticated user carries.

    Args:
        user_id (UUID): The caller the authentication dependency resolved.

    Returns:
        AgentContext: A context whose state pins the caller for every tool.
    """
    return AgentContext(state={USER_ID_KEY: user_id})


def require_user_id(context: AgentContext) -> UUID:
    """Return the caller pinned to this run, or fail with a readable message.

    Args:
        context (AgentContext): The run context handed to the tool.

    Returns:
        UUID: The authenticated caller.

    Raises:
        AgentToolError: When the run carries no identity — raised rather than
            returned so the loop records a failed step instead of the tool
            quietly answering about nobody.
    """
    user_id = context.state.get(USER_ID_KEY)
    if not isinstance(user_id, UUID):
        raise AgentToolError("this tool needs an authenticated user")
    return user_id
```

That splits tools into two families, and the difference shows on each one's
first line:

| Family | Identity | Example |
| --- | --- | --- |
| Catalogue | none | `search_services` |
| Owner | `require_user_id(context)` | `get_my_services`, `cancel_my_application` |

If every tool reimplemented the lookup, one of them would get the key wrong —
and a typo there **fails open**: it returns somebody else's data instead of
raising.

## `agents/` — composition only

```python title="src/ai/agents/service.py"
from tempest_fastapi_sdk.agents import Agent, AgentRunSink

from src.ai.prompts import SERVICE_AGENT_PROMPT
from src.ai.runtime import agent_budget, generator
from src.ai.tools import get_my_services, search_services


def build_service_agent(*, run_sink: AgentRunSink | None = None) -> Agent:
    """Build the agent that answers about services.

    Args:
        run_sink (AgentRunSink | None): Where finished runs are recorded.
            Injected rather than built here, so this module never reaches the
            database layer: the API wires the persistent sink and a test passes
            an in-memory one, or none.

    Returns:
        Agent: The configured agent, sharing the process-wide generator.
    """
    return Agent(
        generator,
        tools=[search_services, get_my_services],
        system_prompt=SERVICE_AGENT_PROMPT,
        budget=agent_budget(),
        run_sink=run_sink,
        name="service-agent",
    )
```

Everything this module uses lives somewhere else. What is left is the **choice**
of which pieces go together — and that is what makes the second agent a second
small file instead of a copy of this one.

!!! info "Resources come in from the edges"
    The `run_sink` arrives as a parameter because persistence is the
    infrastructure layer's business. Built here, `ai` would start importing
    `db/models` and the arrow in the table above would be reversed.

The agent is built **once** and shared. That is safe because it keeps nothing
per request: identity travels on the run context and each tool opens its own
session.

## The endpoint: why not `make_agent_router`

```python title="src/controllers/ai.py"
from tempest_fastapi_sdk.agents import Agent

from src.ai import context_for
from src.db.models import UserModel
from src.schemas import AgentAnswerResponseSchema, AgentAskRequestSchema


class AIController:
    """Controller for the agent-backed endpoints."""

    def __init__(self, agent: Agent) -> None:
        """Store the shared agent.

        Args:
            agent (Agent): The process-wide agent; it holds no request state.
        """
        self.agent: Agent = agent

    async def ask(
        self,
        user: UserModel,
        data: AgentAskRequestSchema,
    ) -> AgentAnswerResponseSchema:
        """Answer a question with the caller pinned to the run.

        Args:
            user (UserModel): The authenticated caller.
            data (AgentAskRequestSchema): The question.

        Returns:
            AgentAnswerResponseSchema: The answer plus what the run did.
                ``succeeded`` is False when a budget truncated it, and the
                answer is partial work rather than a conclusion.
        """
        run = await self.agent.run(data.question, context=context_for(user.id))
        return AgentAnswerResponseSchema(
            output=run.output,
            succeeded=run.succeeded,
            stop_reason=str(run.stop_reason),
            tool_calls=run.tool_calls,
            seconds=run.seconds,
        )
```

The SDK's ready-made router calls `agent.run(goal)` **with no context** — it
knows nothing about your authentication. A service with owner tools needs its
own endpoint; `make_agent_router` stays great for an agent with no identity (a
documentation assistant, an internal support agent).

!!! check "Always translate `stop_reason`"
    A budget-truncated run still returns text — the last thing the model said.
    Publishing that as the answer without saying it was cut short is like
    returning half a query with no warning.

## Auditing: one row per run

```python title="src/api/dependencies/resources.py"
from tempest_fastapi_sdk.agents import Agent, DbAgentRunSink

from src.ai import build_service_agent
from src.db.manager import db
from src.db.models import AgentRunModel

_service_agent = build_service_agent(run_sink=DbAgentRunSink(db, AgentRunModel))


def get_service_agent() -> Agent:
    """Return the process-wide service agent.

    Returns:
        Agent: The shared agent, recording each run to the database.
    """
    return _service_agent
```

In a product where the agent talks to end users, this is what answers "why did
it say that?" three days later. Adding it early costs one migration; adding it
late costs the migration **plus** the data from the period nobody was recording.

!!! tip "Read-only in the admin"
    Register the table with `can_create=False`, `can_edit=False` and every field
    in `readonly_fields`. It is a log: reading is the use case, writing never is.

## Latency: where this breaks first

A local generator is **serialized** — one generation at a time, per GPU. A
three-step agent is three generations. Ten people asking at once form a queue,
and the tenth waits thirty generations.

Cheapest to most expensive:

1. **`AgentBudget` tied to the timeout** — already in `runtime.py`.
2. **A semaphore in front of the agent**, answering "busy" instead of queueing
   without bound.
3. **The agent becomes a task** and the answer returns over SSE. If the service
   already has a queue and SSE, that is recombination, not new infrastructure.

## When it grows: skills before multi-agent

1. **One agent, N tools** — while the descriptions fit in the prompt without the
   model losing track. On a small model that ceiling arrives early: 5 to 8.
2. **[Skills](agents-advanced.md#skills-capabilities-loaded-on-demand)** —
   capabilities load on demand and only their names sit in the prompt. That is
   the right step when the ceiling above arrives.
3. **[Delegation](agents-advanced.md#delegating-to-another-agent)** — only when
   there are domains whose prompts contradict each other. It costs depth, an
   inherited budget and a nested trace.

Jumping straight to 3 is the common mistake. Most services live at 1 for a long
time.

## Recap

- **`ai/` sits beside `services` and `controllers`**, and import direction is
  what keeps the layer from turning into a tangle.
- **`runtime.py` holds the process's model** — one `TextGenerator` per service,
  not per agent.
- **`tools/` apart from `agents/`**: a tool is a reusable unit, an agent is a
  composition.
- **`schemas/` is the model's contract**, and what it may not choose lives in the
  translation to the domain filter.
- **`views/` decides what the model reads** — and, by extension, what the end
  user reads with it.
- **`policy.py` holds identity**, which is never an argument.
- **The controller calls `agent.run`**, seeds the context and translates
  `stop_reason`.
- **The `run_sink` comes in from the edges** and answers "why did it say that?".

See also: [AI agents (database)](agents-db.md) for the session inside a tool,
[AI agents (testing)](agents-testing.md) to exercise all of this without loading
a model, and [Database](database.md) for the shared manager.
