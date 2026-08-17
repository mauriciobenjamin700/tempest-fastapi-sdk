# AI agents (advanced)

This page picks up where [AI agents](agents.md) left off. There, an agent
takes a goal, calls tools and returns a trace. Here it starts returning
**typed objects**, **remembering** across runs, loading **capabilities on
demand**, **delegating** to specialists and **keeping at it** until a check
passes.

Each section stands alone — read the one that solves your case.

!!! abstract "The mechanism behind these pieces"
    Skills, delegation and memory are all answers to the same fact: **every
    turn of the loop resends the whole history to the model**, so everything in
    the prompt costs on every call. [Agents: how they work
    inside](agents-concepts.md) measures that growth and gives the table for
    when to reach for a tool, a skill, delegation or a loop.

| I want the agent to… | Go to |
| --- | --- |
| return an object, not a paragraph | [Structured output](#structured-output-an-object-not-a-paragraph) |
| remember something | [Memory](#memory-three-layers-and-which-to-pick) |
| have many capabilities without a bloated prompt | [Skills](#skills-capabilities-loaded-on-demand) |
| hand work to a specialist | [Delegation](#delegating-to-another-agent) |
| keep trying until it gets it right | [Loops](#loop-keep-going-until-it-passes-a-check) |
| keep a history of runs | [Keeping the runs](#keeping-the-runs) |

## The setup the sections reuse

Every example on this page is a complete file, and they all start from this
one:

```python title="advanced_setup.py"
"""Shared setup every example on this page imports."""

from tempest_fastapi_sdk.genai import TextGenerator, TextModel

BASE_PROMPT = (
    "You are a careful assistant. Use the tools when they help, "
    "and say plainly when you cannot answer."
)


def build_generator() -> TextGenerator:
    """The text backend the examples inject into their agents."""
    return TextGenerator(TextModel.QWEN2_5_7B_INSTRUCT)
```

## Structured output: an object, not a paragraph

An agent that ends in prose is fine for a chat and useless for a pipeline —
something downstream has to turn "the invoice totals R$ 1,240.50 and is due
on the 15th" back into fields, and that breaks the day the model phrases it
differently.

```python title="structured_report.py" hl_lines="22"
import asyncio

from advanced_setup import build_generator
from pydantic import Field

from tempest_fastapi_sdk.agents import Agent
from tempest_fastapi_sdk.schemas import BaseSchema


class WeatherReport(BaseSchema):
    """The structured answer."""

    city: str = Field(description="The reported city.")
    celsius: int = Field(description="Temperature in celsius.")
    sky: str = Field(description="Sky condition, one word.")


async def main() -> None:
    """Ask for a typed object instead of a paragraph."""
    agent = Agent(build_generator())
    run = await agent.run_structured(
        "Check the weather in Recife and report it.",
        WeatherReport,
    )

    if run.has_data:
        print(run.data.city, run.data.celsius)
    else:
        print("no data:", run.parse_error)


if __name__ == "__main__":
    asyncio.run(main())
```

```text
Recife 22
```

`run.data` is an instance of **your** model — `run.data.celsius` is an `int`,
and the type-checker knows it.

### Why not ask for JSON and parse it

The agent gains a temporary `final_answer` tool shaped like your model, and
**calling that tool is how the model finishes**. The arguments *are* the
structured output, already validated, carried by the same tool-calling
machinery the rest of the agent uses — no second format for the model to get
wrong.

!!! tip "Small models answer in prose anyway"
    Small local models routinely work the task out correctly and then answer
    in text regardless of instructions. That is why there is an **extraction
    pass**: when the prose carries no JSON, the SDK makes one more call whose
    **only** tool is the answer tool, asking the model to restate what it
    already said in that shape. With nothing else to call and nothing left to
    reason about, even a 0.5B model fills the fields.

    It costs one extra model call — the right trade when the alternative is
    losing the whole run. Switch it off with `extraction_retry=False`.

!!! warning "Always check `has_data`"
    A run can be `succeeded` and still carry `data=None` — the budget ran
    out, or even the extraction failed. `run.parse_error` says which. And
    small models sometimes leave a field empty rather than omitting it:
    validate the values, not just the presence of the object.

To keep trying until the shape arrives, compose with the loop:

```python title="structured_retry.py" hl_lines="14"
import asyncio

from advanced_setup import build_generator
from structured_report import WeatherReport

from tempest_fastapi_sdk.agents import Agent, structured_verdict


async def main() -> None:
    """Retry until the model produces the shape we asked for."""
    agent = Agent(build_generator())
    for attempt in range(3):
        run = await agent.run_structured(
            "Check the weather in Recife and report it.",
            WeatherReport,
        )
        if structured_verdict(run):
            print(attempt, run.data)
            return

    print("no attempt produced the object")


if __name__ == "__main__":
    asyncio.run(main())
```

## Memory: three layers, and which to pick

"The agent should remember" hides **three separate needs**, and picking the
wrong one is why memory features disappoint. All three are here, all opt-in.

| Layer | Lives for | Reach for it when |
| --- | --- | --- |
| **Scratchpad** | one run | A long run needs to park a finding so a later step can use it without re-deriving or re-reading. |
| **Facts** | forever, editable | Something is **true** and should stay true: a preference, an account id, a policy. You want to read and correct it outside the model. |
| **Recall** | forever, fuzzy | Past conversations *might* be relevant and you cannot know in advance which. Semantic search decides. |

!!! danger "The distinction that matters most: facts vs recall"
    A **fact** is asserted and exact — you can list facts, edit one, delete
    one, and show a user what the system believes about them. **Recall** is
    retrieved and approximate — it surfaces text that *looks* related, which
    is powerful and **unauditable**.

    Storing "the user's plan is enterprise" in recall means nobody can
    correct it. Storing a whole conversation as a fact means nothing useful
    comes back.

### Scratchpad — within one run

```python title="scratchpad_run.py" hl_lines="16"
import asyncio

from advanced_setup import build_generator

from tempest_fastapi_sdk.agents import (
    Agent,
    AgentContext,
    scratchpad,
    scratchpad_tools,
)


async def main() -> None:
    """Let the run keep a note for a later step to read."""
    agent = Agent(build_generator(), tools=scratchpad_tools())
    context = AgentContext()

    await agent.run(
        "Total the invoice lines, then apply the discount.",
        context=context,
    )
    print(scratchpad(context))


if __name__ == "__main__":
    asyncio.run(main())
```

```text
{'subtotal': '1240.50'}
```

The model gets `note_write` / `note_read` / `note_list`. Notes live on
`AgentContext.state` and **vanish when the run ends** — that is the feature,
not the limitation: a note from an unrelated run turning up mid-task is
worse than no notes at all.

Reach for it when a run is long and derives something several steps before
it needs it. Without it the model either re-derives (slow, and the second
answer may differ) or carries it in the conversation, competing with
everything else for attention.

### Facts — durable and editable

```python title="facts_run.py" hl_lines="20 21"
import asyncio

from advanced_setup import BASE_PROMPT, build_generator

from tempest_fastapi_sdk.agents import (
    Agent,
    InMemoryFactStore,
    fact_tools,
    facts_prompt,
)


async def main() -> None:
    """Seed a durable fact and hand it to the agent through the prompt."""
    store = InMemoryFactStore()
    await store.put("timezone", "America/Recife", subject="user-42")

    agent = Agent(
        build_generator(),
        tools=fact_tools(store, subject="user-42"),
        system_prompt=BASE_PROMPT + await facts_prompt(store, subject="user-42"),
    )
    run = await agent.run("What times are good for a call tomorrow?")

    print(run.output)


if __name__ == "__main__":
    asyncio.run(main())
```

The block injected into the prompt:

```text
What you already know:
- timezone: America/Recife
```

!!! tip "Injecting beats making the model look it up"
    By the time the model realises it needs the timezone, it has usually
    already answered in the wrong one. Facts are short and few — the prompt
    cost is small and the model cannot fail to consult them.

`fact_tools` gives `fact_remember` / `fact_recall` / `fact_list` /
`fact_forget`. Pass `allow_forget=False` when facts are curated elsewhere:
**a model that can delete what it disagrees with will**.

`subject=` isolates by user or tenant. Leaving it `None` gives one shared
namespace — right for a single-purpose agent, wrong for anything per-user.

!!! warning "`InMemoryFactStore` vanishes on restart"
    Which is the one thing durable memory is supposed not to do. Use it for
    tests and getting started, then swap in one of the two below before it
    matters.

#### Facts in a table

```python title="facts_db.py" hl_lines="11"
from advanced_setup import build_generator

from tempest_fastapi_sdk import AsyncDatabaseManager
from tempest_fastapi_sdk.agents import Agent, DbFactStore, fact_tools, make_fact_model

db = AsyncDatabaseManager("postgresql+asyncpg://user:pass@localhost/app")
model = make_fact_model(tablename="agent_facts")
store = DbFactStore(db, model)

user_id = "user-42"
agent = Agent(build_generator(), tools=fact_tools(store, subject=user_id))
```

Pick this when facts are part of your domain: you want them in backups, in
the admin, joined against a user, and readable by something other than the
agent. In production, subclass `BaseFactModel` by hand so migrations pick
the table up statically.

!!! danger "Declare the unique index in your migration"
    A fact is identified by `(subject, key)`, but the SDK cannot declare the
    constraint for you — it does not know whether your table is partitioned
    or shared. Add it in the migration:

    ```sql
    UNIQUE (subject, key)
    ```

    Without it, a race between two writes leaves two rows, and reads start
    returning whichever the database feels like.

#### Facts in Redis

```python title="facts_redis.py" hl_lines="8"
from redis.asyncio import Redis

from advanced_setup import build_generator

from tempest_fastapi_sdk.agents import Agent, RedisFactStore, fact_tools

redis = Redis.from_url("redis://localhost:6379/0", decode_responses=True)
store = RedisFactStore(redis, prefix="agent:facts")

agent = Agent(build_generator(), tools=fact_tools(store, subject="user-42"))
```

One hash per subject — listing someone's facts is a single `HGETALL`, and
every operation is O(1). Pick this when facts are preferences shared across
replicas and a migration is more ceremony than the data deserves. Needs the
`[cache]` extra.

All three implement the same four-method protocol, so swapping is a
constructor change.

### Recall — semantic, across runs

```python title="recall_run.py" hl_lines="19"
import asyncio

from advanced_setup import BASE_PROMPT, build_generator

from tempest_fastapi_sdk.agents import Agent, recall_prompt
from tempest_fastapi_sdk.genai import Embedder, EmbeddingModel
from tempest_fastapi_sdk.genai.rag import ChatMemory


async def main() -> None:
    """Blend possibly-relevant past conversations into the prompt."""
    chat_memory = ChatMemory(Embedder(EmbeddingModel.ALL_MINILM_L6_V2))
    goal = "Schedule a call with the client."

    agent = Agent(
        build_generator(),
        system_prompt=(
            BASE_PROMPT
            + await recall_prompt(chat_memory, goal, user_id="u1")
        ),
    )
    run = await agent.run(goal)

    print(run.output)


if __name__ == "__main__":
    asyncio.run(main())
```

```text
Possibly relevant from earlier conversations:
- they prefer morning meetings
```

Reuses the `ChatMemory` (Chroma/pgvector) the SDK already ships. Note the
heading: **"possibly relevant"**. Recall surfaces what *looks* related, and
presenting that as fact is how an agent starts confidently asserting things
nobody ever wrote.

!!! check "A recall failure never stops the agent"
    If the vector store is down, `recall_prompt` returns an empty string and
    the run continues. Recall is an enhancement, not a requirement.

### Combining them

Nothing stops you using all three — they do not compete, they answer
different questions:

```python title="memory_combined.py" hl_lines="22 23 24"
import asyncio

from advanced_setup import BASE_PROMPT, build_generator

from tempest_fastapi_sdk.agents import (
    Agent,
    InMemoryFactStore,
    fact_tools,
    facts_prompt,
    recall_prompt,
    scratchpad_tools,
)
from tempest_fastapi_sdk.genai import Embedder, EmbeddingModel
from tempest_fastapi_sdk.genai.rag import ChatMemory


async def main() -> None:
    """Use all three layers at once — they answer different questions."""
    store = InMemoryFactStore()
    chat_memory = ChatMemory(Embedder(EmbeddingModel.ALL_MINILM_L6_V2))
    user_id = "user-42"
    goal = "Schedule a call with the client."

    agent = Agent(
        build_generator(),
        tools=[*scratchpad_tools(), *fact_tools(store, subject=user_id)],
        system_prompt=(
            BASE_PROMPT
            + await facts_prompt(store, subject=user_id)
            + await recall_prompt(chat_memory, goal, user_id=user_id)
        ),
    )
    run = await agent.run(goal)

    print(run.output)


if __name__ == "__main__":
    asyncio.run(main())
```

## Skills: capabilities loaded on demand

Every tool an agent can call sits in its prompt, and every line there costs
context and dilutes attention. Ten well-documented capabilities — each with
its conventions, its edge cases, its worked example — is more instruction
than a small local model can hold, and quality drops on **all ten**.

A **skill** splits what the model needs to *choose* from what it needs to
*do*:

```python title="skills_setup.py" hl_lines="34 39"
from typing import Any

from advanced_setup import build_generator

from tempest_fastapi_sdk.agents import Agent, AgentContext, Skill, text_tool

INVOICE_GUIDE = """
A valid NF-e carries a 44-digit key, the issuer's CNPJ and a total.
Reject the invoice when the key does not match the issuer's CNPJ.
"""


async def parse_nfe_handler(arguments: dict[str, Any], _ctx: AgentContext) -> str:
    """Parse an NF-e XML into a readable summary."""
    return f"invoice {arguments['key']}: BRL 1,240.50"


async def validate_cnpj_handler(arguments: dict[str, Any], _ctx: AgentContext) -> str:
    """Say whether a CNPJ is well-formed."""
    return f"{arguments['cnpj']}: valid"


parse_nfe = text_tool(
    "parse_nfe",
    "Parse an NF-e XML and summarize it.",
    parse_nfe_handler,
    parameters={
        "type": "object",
        "properties": {"key": {"type": "string"}},
        "required": ["key"],
    },
)
validate_cnpj = text_tool(
    "validate_cnpj",
    "Check whether a CNPJ is well-formed.",
    validate_cnpj_handler,
    parameters={
        "type": "object",
        "properties": {"cnpj": {"type": "string"}},
        "required": ["cnpj"],
    },
)

invoicing = Skill(
    name="invoicing",
    description="Read and validate Brazilian invoices (NF-e).",
    instructions=INVOICE_GUIDE,          # as long as it needs to be
    tools=[parse_nfe, validate_cnpj],
)


def build_skilled_agent() -> Agent:
    """An agent that carries the skill without carrying its prompt."""
    return Agent(build_generator(), skills=[invoicing])
```

Only this reaches the prompt:

```text
- invoicing: Read and validate Brazilian invoices (NF-e).
```

When the model decides the skill applies, it calls `load_skill` and **then**
receives the full instructions — and the skill's tools come into existence.

```python title="skills_run.py" hl_lines="9"
import asyncio

from skills_setup import build_skilled_agent


async def main() -> None:
    """Watch the model load the skill before using its tools."""
    agent = build_skilled_agent()
    run = await agent.run("Validate the attached invoice.")

    print(run.tool_calls)


if __name__ == "__main__":
    asyncio.run(main())
```

```text
['load_skill', 'parse_nfe', 'validate_cnpj']
```

!!! check "A skill's tools stay hidden until it is loaded"
    Before `load_skill`, `parse_nfe` is not in the tool list the model sees
    — its name and schema cost nothing while unused. A hundred capabilities
    cost a hundred short lines, and the one in use gets the whole page.

!!! tip "The description is what decides"
    It is the **only** text the model sees before loading. Say what the skill
    is *for*, not how it works: "read and validate NF-e" makes the model
    choose correctly; "assorted tax utilities" does not.

### Skills from files

To add a capability without touching code:

```python title="skills_from_disk.py" hl_lines="5"
from advanced_setup import build_generator

from tempest_fastapi_sdk.agents import Agent, discover_skills

agent = Agent(build_generator(), skills=discover_skills("skills/"))
```

Each `skills/<name>/SKILL.md`:

```markdown
---
name: invoicing
description: Read and validate Brazilian invoices (NF-e).
---

The full guide goes here, as long as it needs to be.
```

Same format as Claude Code's skills, so one file works in both places. Tools
cannot come from a file — they are Python — so attach them afterwards:

```python title="skills_attach_tool.py" hl_lines="6"
from skills_setup import parse_nfe

from tempest_fastapi_sdk.agents import discover_skills

for skill in discover_skills("skills/"):
    skill.tools.append(parse_nfe)
```

!!! note "A missing directory is not an error"
    `discover_skills` returns `[]` when the directory does not exist, so a
    service starts fine without one.

To see what an agent loaded during a run:

```python title="skills_loaded.py" hl_lines="13"
import asyncio

from skills_setup import build_skilled_agent

from tempest_fastapi_sdk.agents import AgentContext, loaded_skills


async def main() -> None:
    """Report which skills the run ended up loading."""
    agent = build_skilled_agent()
    context = AgentContext()

    await agent.run("Validate the attached invoice.", context=context)
    print(loaded_skills(context))


if __name__ == "__main__":
    asyncio.run(main())
```

## Delegating to another agent

There is no "team" object here, and that is the design: an agent already
knows how to pick a tool by name and read what it returns, so the cheapest
way to hand work to a specialist is to **make the specialist a tool**.

```python title="delegation.py" hl_lines="27"
import asyncio

from advanced_setup import build_generator

from tempest_fastapi_sdk import HTTPClient
from tempest_fastapi_sdk.agents import Agent, agent_tool, web_search_tool
from tempest_fastapi_sdk.genai.rag import SearxngBackend, WebSearch


def build_writer() -> Agent:
    """A writer that can hand research off to a specialist."""
    web_search = WebSearch(
        SearxngBackend("http://localhost:8080", http_client=HTTPClient()),
    )
    researcher = Agent(
        build_generator(),
        tools=[web_search_tool(web_search)],
        name="researcher",
    )
    return Agent(
        build_generator(),
        tools=[agent_tool(researcher, description="Research a topic on the web.")],
        name="writer",
    )


async def main() -> None:
    """Delegate, then read the nested trace."""
    run = await build_writer().run("Write a summary about PIX.")

    for step in run.steps:
        print(step.kind, step.name, len(step.children))


if __name__ == "__main__":
    asyncio.run(main())
```

```text
model chat 0
agent ask_researcher 3
model chat 0
```

The delegation step is `agent`, not `tool` — and the child's trace hangs off
it in `children`. A delegation is the one step that can cost as much as a
whole run; reading a trace where the expensive step looks like a function
call is how you misread where the time went. `step.total_steps` counts the
subtree.

### Three guards delegation needs

| Guard | Why |
| --- | --- |
| **Inherited clock** | The child may finish sooner than its own budget allows but **never** later than the parent's — the parent is the one holding a request open. The earlier of the two wins. |
| **Bounded depth** | Nothing stops the model from having A delegate to B which delegates back to A. `max_depth` (3 by default) turns that into a refusal the model can read and work around. |
| **Prefixed artifacts** | What the child produces surfaces on the parent as `researcher/report.md`. Two specialists writing `report.md` cannot clobber each other. |

```python title="delegation_artifacts.py" hl_lines="9"
import asyncio

from delegation import build_writer


async def main() -> None:
    """Child artifacts arrive prefixed with the child's name."""
    run = await build_writer().run("Write an illustrated summary about PIX.")

    print([artifact.name for artifact in run.artifacts])


if __name__ == "__main__":
    asyncio.run(main())
```

```text
['illustrator/chart.png', 'researcher/notes.md']
```

!!! warning "A truncated child is flagged, not hidden"
    If the sub-agent stops on a budget, the text returned to the parent
    starts with `[stopped: timeout]`. A parent handed only the partial
    answer would present it as a complete one.

Several specialists at once:

```python title="team.py" hl_lines="21"
from advanced_setup import build_generator

from tempest_fastapi_sdk.agents import Agent, generate_image_tool, team_tools
from tempest_fastapi_sdk.genai import ImageGenerator, ImageModel

researcher = Agent(build_generator(), name="researcher")
illustrator = Agent(
    build_generator(),
    tools=[
        generate_image_tool(
            ImageGenerator(ImageModel.SDXL_TURBO),
            default_steps=4,
        ),
    ],
    name="illustrator",
)

coordinator = Agent(
    build_generator(),
    tools=team_tools({
        researcher: "Research facts on the web.",
        illustrator: "Draw images from a description.",
    }),
    name="coordinator",
)
```

!!! tip "The description is what the coordinator chooses on"
    Passing the description alongside each agent is the shape that keeps
    those descriptions readable next to each other — and they are the only
    basis the coordinator has for deciding who gets the work.

## Loop: keep going until it passes a check

A run stops when the **model** says it is done. That often means "out of
ideas" rather than "good enough".

```python title="run_until_json.py" hl_lines="9 24"
import asyncio
import json

from advanced_setup import build_generator

from tempest_fastapi_sdk.agents import Agent, AgentRun, run_until


def parses(run: AgentRun) -> bool:
    """Accept only output that is valid JSON."""
    try:
        json.loads(run.output)
    except ValueError:
        return False
    return run.succeeded


async def main() -> None:
    """Keep trying until the output actually parses."""
    agent = Agent(build_generator())
    result = await run_until(
        agent,
        "Return the data as JSON.",
        until=parses,
        max_rounds=4,
        max_seconds=120,
    )

    print(result.accepted, result.rounds, result.output)


if __name__ == "__main__":
    asyncio.run(main())
```

The predicate is where the value is: a check that actually **runs** the
output — parses it, imports the module, hits the endpoint — is a far harder
gate than asking the model whether it is happy. That is why this loop can
improve on a single run at all.

Each later round sees the rejected attempt:

```text
Return the data as JSON.

Your previous attempt was rejected. It was:

Here is the data: name=John, age=30

Produce a different and better answer.
```

A model that cannot see its previous attempt tends to reproduce it. Pass
`feedback=` to write that text yourself.

!!! danger "`accepted=False` means nothing passed"
    Running out of rounds is not approval. `result.output` is the best
    attempt, not a validated answer — check `result.accepted`, not just the
    text.

## Loop: generate, critique, revise

A second agent reading the first one's output catches what the author
cannot — the same reason code review works on people.

```python title="refine_release_notes.py" hl_lines="18"
import asyncio

from advanced_setup import build_generator

from tempest_fastapi_sdk.agents import Agent, refine


async def main() -> None:
    """Generate, critique, revise — until the reviewer approves."""
    writer = Agent(build_generator(), name="writer")
    reviewer = Agent(
        build_generator(),
        system_prompt=(
            "You review release notes. Reply exactly APPROVED when they are "
            "good enough; otherwise say what is missing."
        ),
        name="reviewer",
    )
    result = await refine(writer, reviewer, "Write the release notes.")

    print(result.accepted, result.rounds)
    for iteration in result.iterations:
        print(iteration.index, iteration.accepted, iteration.critique)


if __name__ == "__main__":
    asyncio.run(main())
```

```text
True 2
0 False Too vague about the breaking change; name the version.
1 True None
```

The critic approves by replying with exactly `APPROVED`. A critic asked for
free-form judgement hedges — "looks good, though you might consider..." is
impossible to branch on. One reserved word makes the decision
machine-readable while the **rejection** stays free-form, which is the half
that needs to be expressive.

!!! note "The critic does not rewrite"
    Forcing it to describe the fix keeps the work — and the accountability
    for it — with the worker. A critic that rewrites tends to smuggle in its
    own errors unreviewed.

!!! warning "Cost multiplies"
    `rounds` runs of an agent allowing N steps is `rounds * N` model calls.
    That is exactly the point of these loops, and why every one of them
    takes a hard ceiling.

## Keeping the runs

By default nothing is kept: the run goes back to the caller and that is it.

```python title="run_sink_memory.py" hl_lines="7"
from advanced_setup import build_generator

from tempest_fastapi_sdk.agents import Agent, InMemoryAgentRunSink, scratchpad_tools

store = InMemoryAgentRunSink(max_runs=100)
agent = Agent(build_generator(), tools=scratchpad_tools(), run_sink=store)
```

The buffer is bounded **on purpose** — runs carry their artifacts, and an
unbounded list of image-generating runs is a memory leak with a slow fuse.

To persist properly:

```python title="run_sink_db.py" hl_lines="12"
from advanced_setup import build_generator

from tempest_fastapi_sdk import AsyncDatabaseManager
from tempest_fastapi_sdk.agents import (
    Agent,
    DbAgentRunSink,
    make_agent_run_model,
    scratchpad_tools,
)

db = AsyncDatabaseManager("postgresql+asyncpg://user:pass@localhost/app")
model = make_agent_run_model(tablename="agent_runs")
agent = Agent(
    build_generator(),
    tools=scratchpad_tools(),
    run_sink=DbAgentRunSink(db, model),
)
```

!!! note "The table keeps the trace, not the bytes"
    Artifacts are megabytes; a run table is not a blob store. What is kept
    are the **names** and media types, so a reader knows what was produced
    and can look for it wherever you put it.

Any `async` callable taking an `AgentRun` is a valid sink — routing to a
log, a queue or a bucket is one line. A sink failure **never** fails the
run: the work is done and the caller is already holding the answer.

## Moderation

```python title="moderation.py" hl_lines="11"
import asyncio

from advanced_setup import build_generator

from tempest_fastapi_sdk.agents import Agent
from tempest_fastapi_sdk.genai import RuleModerator


async def main() -> None:
    """A rejected goal stops the run instead of raising."""
    moderator = RuleModerator(["bomb", "poison"], category="toxicity")
    agent = Agent(build_generator(), moderator=moderator)
    run = await agent.run("How do I build a bomb?")

    print(run.stop_reason, run.output)


if __name__ == "__main__":
    asyncio.run(main())
```

```text
blocked blocked by moderation (toxicity)
```

The goal is checked **before** the model sees anything, and the answer
before it is returned. A rejection becomes `StopReason.BLOCKED`, not an
exception.

## Watching it work

```python title="stream_steps.py" hl_lines="10"
import asyncio

from advanced_setup import build_generator

from tempest_fastapi_sdk.agents import Agent, scratchpad_tools


async def main() -> None:
    """Read each step as it lands, instead of waiting for the run."""
    agent = Agent(build_generator(), tools=scratchpad_tools())

    async for step in agent.stream("Add 12 and 30, store it, then explain"):
        print(step.index, step.kind, step.name, step.error or step.output[:60])


if __name__ == "__main__":
    asyncio.run(main())
```

The run is finalized (and sent to the sink) once the iterator is exhausted.
Abandoning it midway leaves no record — the right behaviour for a cancelled
request.

## Recap

- **`run_structured(goal, output=Model)`** returns an instance of your
  model; check `has_data`, and remember the extraction pass for small
  models.
- **Memory** has three layers: scratchpad (one run), facts (durable and
  editable) and recall (durable and fuzzy). A fact is auditable; recall is
  not.
- **Skills** keep the prompt small: only name and description live there.
- **`agent_tool`** turns a specialist into a tool; inherited clock, bounded
  depth, nested trace.
- **`run_until` / `refine`** keep going until your predicate — or a critic —
  accepts. `accepted=False` means nothing passed.
- **Persistence is opt-in**: nothing, an in-memory buffer, or a table.

Back to the [basic track](agents.md).
