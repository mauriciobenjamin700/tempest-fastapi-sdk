# AI agents (advanced)

This page picks up where [AI agents](agents.md) left off. There, an agent
takes a goal, calls tools and returns a trace. Here it starts returning
**typed objects**, **remembering** across runs, loading **capabilities on
demand**, **delegating** to specialists and **keeping at it** until a check
passes.

Each section stands alone — read the one that solves your case.

| I want the agent to… | Go to |
| --- | --- |
| return an object, not a paragraph | [Structured output](#structured-output-an-object-not-a-paragraph) |
| remember something | [Memory](#memory-three-layers-and-which-to-pick) |
| have many capabilities without a bloated prompt | [Skills](#skills-capabilities-loaded-on-demand) |
| hand work to a specialist | [Delegation](#delegating-to-another-agent) |
| keep trying until it gets it right | [Loops](#loop-keep-going-until-it-passes-a-check) |
| keep a history of runs | [Keeping the runs](#keeping-the-runs) |

## Structured output: an object, not a paragraph

An agent that ends in prose is fine for a chat and useless for a pipeline —
something downstream has to turn "the invoice totals R$ 1,240.50 and is due
on the 15th" back into fields, and that breaks the day the model phrases it
differently.

```python
import asyncio

from pydantic import Field

from tempest_fastapi_sdk.agents import Agent
from tempest_fastapi_sdk.schemas import BaseSchema


class WeatherReport(BaseSchema):
    """The structured answer."""

    city: str = Field(description="City reported on.")
    celsius: int = Field(description="Temperature in celsius.")
    sky: str = Field(description="Sky condition, one word.")


async def main() -> None:
    """Run this example."""
    run = await agent.run_structured("Get the weather in Recife and report it.", WeatherReport)

    if run.has_data:
        print(run.data.city, run.data.celsius)
    else:
        print("no data:", run.parse_error)


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

```python
from tempest_fastapi_sdk.agents import run_until, structured_verdict
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

```python
import asyncio

from tempest_fastapi_sdk.agents import Agent, AgentContext, scratchpad, scratchpad_tools

agent = Agent(generator, tools=scratchpad_tools())

context = AgentContext()


async def main() -> None:
    """Run this example."""
    run = await agent.run("Total the invoice lines, then apply the discount.", context=context)
    print(scratchpad(context))


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

```python
import asyncio

from tempest_fastapi_sdk.agents import (
    Agent,
    InMemoryFactStore,
    fact_tools,
    facts_prompt,
)

store = InMemoryFactStore()


async def main() -> None:
    """Run this example."""
    await store.put("timezone", "America/Recife", subject="user-42")

    agent = Agent(
        generator,
        tools=fact_tools(store, subject="user-42"),
        system_prompt=BASE_PROMPT + await facts_prompt(store, subject="user-42"),
    )


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

```python
from tempest_fastapi_sdk.agents import Agent, DbFactStore, fact_tools, make_fact_model

model = make_fact_model(tablename="agent_facts")
store = DbFactStore(db, model)

agent = Agent(generator, tools=fact_tools(store, subject=user_id))
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

```python
from tempest_fastapi_sdk.agents import RedisFactStore

store = RedisFactStore(redis, prefix="agent:facts")
```

One hash per subject — listing someone's facts is a single `HGETALL`, and
every operation is O(1). Pick this when facts are preferences shared across
replicas and a migration is more ceremony than the data deserves. Needs the
`[cache]` extra.

All three implement the same four-method protocol, so swapping is a
constructor change.

### Recall — semantic, across runs

```python
import asyncio

from tempest_fastapi_sdk.agents import Agent, recall_prompt

goal = "Schedule a call with the client."


async def main() -> None:
    """Run this example."""
    agent = Agent(
        generator,
        system_prompt=BASE_PROMPT + await recall_prompt(chat_memory, goal, user_id="u1"),
    )


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

```python
import asyncio


async def main() -> None:
    """Run this example."""
    agent = Agent(
        generator,
        tools=[*scratchpad_tools(), *fact_tools(store, subject=user_id)],
        system_prompt=(
            BASE_PROMPT
            + await facts_prompt(store, subject=user_id)
            + await recall_prompt(chat_memory, goal, user_id=user_id)
        ),
    )


asyncio.run(main())
```

## Skills: capabilities loaded on demand

Every tool an agent can call sits in its prompt, and every line there costs
context and dilutes attention. Ten well-documented capabilities — each with
its conventions, its edge cases, its worked example — is more instruction
than a small local model can hold, and quality drops on **all ten**.

A **skill** splits what the model needs to *choose* from what it needs to
*do*:

```python
from tempest_fastapi_sdk.agents import Agent, Skill

invoicing = Skill(
    name="invoicing",
    description="Read and validate Brazilian invoices (NF-e).",
    instructions=INVOICE_GUIDE,          # as long as it needs to be
    tools=[parse_nfe, validate_cnpj],
)

agent = Agent(generator, skills=[invoicing])
```

Only this reaches the prompt:

```text
- invoicing: Read and validate Brazilian invoices (NF-e).
```

When the model decides the skill applies, it calls `load_skill` and **then**
receives the full instructions — and the skill's tools come into existence.

```python
import asyncio


async def main() -> None:
    """Run this example."""
    run = await agent.run("Validate the attached invoice.")
    print(run.tool_calls)


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

```python
from tempest_fastapi_sdk.agents import Agent, discover_skills

agent = Agent(generator, skills=discover_skills("skills/"))
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

```python
skill.tools.append(parse_nfe)
```

!!! note "A missing directory is not an error"
    `discover_skills` returns `[]` when the directory does not exist, so a
    service starts fine without one.

To see what an agent loaded during a run:

```python
import asyncio

from tempest_fastapi_sdk.agents import AgentContext, loaded_skills

context = AgentContext()


async def main() -> None:
    """Run this example."""
    run = await agent.run("...", context=context)
    print(loaded_skills(context))


asyncio.run(main())
```

## Delegating to another agent

There is no "team" object here, and that is the design: an agent already
knows how to pick a tool by name and read what it returns, so the cheapest
way to hand work to a specialist is to **make the specialist a tool**.

```python
import asyncio

from tempest_fastapi_sdk.agents import Agent, agent_tool, web_search_tool

researcher = Agent(
    generator,
    tools=[web_search_tool(web_search)],
    name="researcher",
)
writer = Agent(
    generator,
    tools=[agent_tool(researcher, description="Research a topic on the web.")],
    name="writer",
)


async def main() -> None:
    """Run this example."""
    run = await writer.run("Write a short brief about PIX.")
    for step in run.steps:
        print(step.kind, step.name, len(step.children))


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

```python
import asyncio


async def main() -> None:
    """Run this example."""
    run = await writer.run("...")
    print([a.name for a in run.artifacts])


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

```python
from tempest_fastapi_sdk.agents import Agent, team_tools

coordinator = Agent(
    generator,
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

```python
import asyncio

from tempest_fastapi_sdk.agents import AgentRun, run_until

def parses(run: AgentRun) -> bool:
    """Accept only output that is valid JSON."""
    import json
    try:
        json.loads(run.output)
    except ValueError:
        return False
    return run.succeeded


async def main() -> None:
    """Run this example."""
    result = await run_until(
        agent,
        "Return the data as JSON.",
        until=parses,
        max_rounds=4,
        max_seconds=120,
    )
    print(result.accepted, result.rounds, result.output)


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

```python
import asyncio

from tempest_fastapi_sdk.agents import refine


async def main() -> None:
    """Run this example."""
    result = await refine(writer, reviewer, "Write the release notes.")

    print(result.accepted, result.rounds)
    for iteration in result.iterations:
        print(iteration.index, iteration.accepted, iteration.critique)


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

```python
from tempest_fastapi_sdk.agents import InMemoryAgentRunSink

store = InMemoryAgentRunSink(max_runs=100)
agent = Agent(generator, tools=tools, run_sink=store)
```

The buffer is bounded **on purpose** — runs carry their artifacts, and an
unbounded list of image-generating runs is a memory leak with a slow fuse.

To persist properly:

```python
from tempest_fastapi_sdk.agents import DbAgentRunSink, make_agent_run_model

model = make_agent_run_model(tablename="agent_runs")
agent = Agent(generator, tools=tools, run_sink=DbAgentRunSink(db, model))
```

!!! note "The table keeps the trace, not the bytes"
    Artifacts are megabytes; a run table is not a blob store. What is kept
    are the **names** and media types, so a reader knows what was produced
    and can look for it wherever you put it.

Any `async` callable taking an `AgentRun` is a valid sink — routing to a
log, a queue or a bucket is one line. A sink failure **never** fails the
run: the work is done and the caller is already holding the answer.

## Moderation

```python
import asyncio

agent = Agent(generator, tools=tools, moderator=moderator)


async def main() -> None:
    """Run this example."""
    run = await agent.run("something disallowed")
    print(run.stop_reason, run.output)


asyncio.run(main())
```

```text
blocked blocked by moderation (toxicity)
```

The goal is checked **before** the model sees anything, and the answer
before it is returned. A rejection becomes `StopReason.BLOCKED`, not an
exception.

## Watching it work

```python
import asyncio


async def main() -> None:
    """Run this example."""
    async for step in agent.stream("Draw a cat and describe it"):
        print(step.index, step.kind, step.name, step.error or step.output[:60])


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
