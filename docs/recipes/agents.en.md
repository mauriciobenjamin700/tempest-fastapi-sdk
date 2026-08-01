# AI agents

An **agent** takes a goal, decides what to do, calls tools, and reports what
it did. That last part is what separates it from a chat: the run comes back
with a **step-by-step trace** — arguments, outputs, timings, failures — plus
whatever files it produced.

The ready-made tools wrap the models the SDK already runs locally: text,
image, audio and RAG. No paid API, nothing leaving the machine.

```bash
uv add "tempest-fastapi-sdk"        # the module itself needs no extra
```

!!! info "Submodule, no extra"
    `from tempest_fastapi_sdk.agents import Agent`. The module imports with
    no extra at all — the weight lives in the objects **you** inject, and
    each keeps its own lazy loading.

## Your first agent

```python
from typing import Any

from tempest_fastapi_sdk.agents import Agent, AgentContext, text_tool
from tempest_fastapi_sdk.genai import TextGenerator


async def get_weather(arguments: dict[str, Any], _context: AgentContext) -> str:
    """Return the weather for a city."""
    return f"{arguments['city']}: 22 degrees, clear sky"


tool = text_tool(
    "get_weather",
    "Get the current weather for a city.",
    get_weather,
    parameters={
        "type": "object",
        "properties": {"city": {"type": "string", "description": "City name."}},
        "required": ["city"],
    },
)

agent = Agent(TextGenerator("Qwen/Qwen2.5-0.5B-Instruct"), tools=[tool])
run = await agent.run("What is the weather in Recife? Use the tool.")

print(run.output)
print(run.tool_calls)
print([(step.kind, step.name) for step in run.steps])
```

```text
The weather in Recife is 22 degrees, clear sky.
['get_weather']
[('model', 'chat'), ('tool', 'get_weather'), ('model', 'chat')]
```

Three steps: the model asked for the tool, the tool ran, the model read the
result and answered. All of it on a 0.5B model running on CPU.

!!! tip "The tool description is what matters"
    The model picks by `description` — it is the only text it reads about
    your tool. Worth more care than the implementation.

## Always check `stop_reason`

```python
run = await agent.run("a long task")
if not run.succeeded:
    print("truncated:", run.stop_reason)
```

`succeeded` is `True` only when the **model** decided it was done. The other
reasons are the agent cutting the run short:

| `stop_reason` | What happened |
| --- | --- |
| `completed` | The model answered without asking for another tool. |
| `max_steps` | The step budget ran out first. |
| `timeout` | The wall-clock budget ran out first. |
| `max_tool_calls` | The tool-call budget ran out first. |
| `error` | The model backend failed. |
| `blocked` | Moderation rejected the goal or the answer. |

!!! warning "A truncated run still carries text"
    The `output` of a cut-short run is the last thing the model said —
    partial work, not a final answer. A caller that ignores `stop_reason`
    presents half-finished work as done.

## Budget

```python
from tempest_fastapi_sdk.agents import Agent, AgentBudget

agent = Agent(
    generator,
    tools=tools,
    budget=AgentBudget(max_steps=8, max_seconds=90, max_tool_calls=5),
)
```

Steps alone do **not** bound a run: one tool call can hang, and the agent
sits there without burning a single step. That is why wall-clock is checked
too, and why `max_seconds` has a default (120s) rather than being optional.

## Tools over your local models

This is where the module meets the rest of the SDK:

```python
from tempest_fastapi_sdk.agents import (
    Agent,
    describe_image_tool,
    generate_image_tool,
    retrieve_tool,
    speak_tool,
    transcribe_audio_tool,
    web_search_tool,
)

agent = Agent(
    generator,
    tools=[
        generate_image_tool(image_generator, default_steps=4),
        describe_image_tool(vision_generator),
        transcribe_audio_tool(speech_to_text),
        speak_tool(text_to_speech),
        retrieve_tool(retriever),
        web_search_tool(web_search),
    ],
)
```

| Tool | Model behind it | What it does |
| --- | --- | --- |
| `generate_image_tool` | `ImageGenerator` | Draws, stored as an artifact |
| `describe_image_tool` | `VisionTextGenerator` | Looks at an image and answers |
| `transcribe_audio_tool` | `SpeechToText` | Audio → text |
| `speak_tool` | `TextToSpeech` | Text → audio (WAV artifact) |
| `retrieve_tool` | `Retriever` | Searches the indexed corpus |
| `web_search_tool` | `WebSearch` | Searches the web via SearXNG |
| `save_artifact_tool` | — | Saves text as a deliverable file |

!!! note "`default_steps` is not a detail"
    A turbo model wants ~4 diffusion steps and a full one ~30. If the LLM
    picks blind, a render takes ten times longer than it needs to. Pin your
    checkpoint's value on the tool.

## Chaining multimodal: draw, then look

This is where **named artifacts** earn their keep:

```python
run = await agent.run(
    "Draw a red bicycle as bike.png, then tell me what appears in the "
    "image you created.",
)
for step in run.steps:
    print(step.kind, step.name, step.artifacts)
print(run.artifact("bike.png").media_type)
```

```text
model chat []
tool generate_image ['bike.png']
model chat []
tool describe_image []
model chat []
image/png
```

`generate_image` registers `bike.png` on the run; `describe_image` accepts
that same name and reads the bytes back from the context. **The image never
touches disk and the model never carries base64 in the prompt** — it just
passes a name along.

If the model invents a name that does not exist, the tool says which ones do:

```text
no artifact named 'chart.png'; available: bike.png
```

That is deliberate: a bare "not found" gives the model nothing to correct
with.

## A failing tool does not end the run

```python
from tempest_fastapi_sdk.agents import AgentToolError


async def save(arguments: dict[str, Any], _context: AgentContext) -> str:
    """Save something, or explain why it could not be saved."""
    raise AgentToolError("disk is full")
```

The step is marked with `error`, and the message goes back **to the model**
as an observation. It usually tries another route. Letting the exception
escape would throw away everything the run had done so far.

```python
failed = [step for step in run.steps if step.error]
print(failed[0].error)
```

```text
AgentToolError: disk is full
```

Any exception from the handler is treated the same way — using
`AgentToolError` just makes the intent explicit.

## Pydantic-typed tools

Writing JSON-schema by hand next to the handler means **two descriptions of
the same thing**, drifting apart from the first edit: the schema says `city`,
the handler reads `arguments["town"]`, and nothing catches it until a model
calls the tool. The `@tool` decorator removes the duplicate.

```python
from pydantic import Field

from tempest_fastapi_sdk.agents import AgentContext, tool
from tempest_fastapi_sdk.schemas import BaseSchema


class WeatherArgs(BaseSchema):
    """Arguments for the weather tool."""

    city: str = Field(description="City to look up.")
    days: int = Field(default=1, ge=1, le=7, description="Forecast horizon.")


@tool("get_weather", "Get the current weather for a city.")
async def get_weather(args: WeatherArgs, context: AgentContext) -> str:
    """Return the forecast for the requested city."""
    return f"{args.city}: 22 degrees, {args.days}d"
```

The schema the model sees is **generated** from the Pydantic model, and the
handler receives a **validated instance** — `args.city` is typed and `mypy`
checks it.

!!! check "A bad argument becomes an observation, not a `KeyError`"
    Validation happens **before** the handler runs. A model that invents
    `town=` gets back:

    ```text
    invalid arguments for get_weather: city: Field required
    ```

    Precise enough to correct from next turn. Before, that blew up in the
    middle of your code.

Constraints declared on the model are enforced too: `ge`, `le`,
`max_length`, enums. A model asking for `days=500` is corrected before you
see it.

Without the decorator (lambdas, bound methods, handlers from elsewhere):

```python
from tempest_fastapi_sdk.agents import typed_tool

built = typed_tool("get_weather", "Get the weather.", WeatherArgs, get_weather_impl)
```

## Structured output: an object, not a paragraph

An agent that ends in prose is fine for a chat and useless for a pipeline —
something downstream has to turn "the invoice totals R$ 1,240.50 and is due
on the 15th" back into fields, and that breaks the day the model phrases it
differently.

```python
from pydantic import Field

from tempest_fastapi_sdk.agents import Agent
from tempest_fastapi_sdk.schemas import BaseSchema


class WeatherReport(BaseSchema):
    """The structured answer."""

    city: str = Field(description="City reported on.")
    celsius: int = Field(description="Temperature in celsius.")
    sky: str = Field(description="Sky condition, one word.")


run = await agent.run_structured("Get the weather in Recife and report it.", WeatherReport)

if run.has_data:
    print(run.data.city, run.data.celsius)
else:
    print("no data:", run.parse_error)
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

## Writing your own tool

```python
from typing import Any

from tempest_fastapi_sdk.agents import (
    AgentArtifact,
    AgentContext,
    AgentTool,
    ToolResult,
)


async def render_report(
    arguments: dict[str, Any],
    context: AgentContext,
) -> ToolResult:
    """Render a report and return it as a downloadable artifact."""
    body = f"# {arguments['title']}\n\n{arguments['body']}"
    return ToolResult(
        text=f"Report '{arguments['title']}' generated.",
        artifacts=[
            AgentArtifact(
                name="report.md",
                media_type="text/markdown",
                data=body.encode("utf-8"),
            ),
        ],
    )


tool = AgentTool(
    name="render_report",
    description="Render a titled report the user can download.",
    parameters={
        "type": "object",
        "properties": {
            "title": {"type": "string"},
            "body": {"type": "string"},
        },
        "required": ["title", "body"],
    },
    handler=render_report,
)
```

The handler takes **two** arguments: `arguments` (what the model passed) and
`context` (the run's artifacts). Returning a plain `str` works too when
there is nothing binary — it is wrapped into a `ToolResult` for you.

!!! tip "Already have `AIChatPipeline` tools?"
    `AgentTool.from_tool(tool)` adapts the chat pipeline's single-argument
    tools without touching them.

## Serving it over HTTP

```python
from fastapi import FastAPI

from tempest_fastapi_sdk.agents import (
    Agent,
    InMemoryAgentRunSink,
    make_agent_router,
)

store = InMemoryAgentRunSink(max_runs=50)
agent = Agent(generator, tools=tools, run_sink=store)

app = FastAPI()
app.include_router(make_agent_router(agent, run_store=store))
```

| Route | What it does |
| --- | --- |
| `POST /api/agent/run` | Runs to completion, returns the record |
| `POST /api/agent/run/stream` | Each step as an SSE event, then `done` |
| `GET /api/agent/runs` | Recent runs (only with a `run_store`) |
| `GET /api/agent/runs/{i}/artifacts/{name}` | Downloads an artifact |

The JSON carries artifacts as **metadata** (name, type, size), never bytes:
a generated image is megabytes, and base64 in the body inflates that by a
third. The bytes come from a second request with the right media type —
which also means an `<img src>` works directly.

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
agent = Agent(generator, tools=tools, moderator=moderator)
run = await agent.run("something disallowed")
print(run.stop_reason, run.output)
```

```text
blocked blocked by moderation (toxicity)
```

The goal is checked **before** the model sees anything, and the answer
before it is returned. A rejection becomes `StopReason.BLOCKED`, not an
exception.

## Watching it work

```python
async for step in agent.stream("Draw a cat and describe it"):
    print(step.index, step.kind, step.name, step.error or step.output[:60])
```

The run is finalized (and sent to the sink) once the iterator is exhausted.
Abandoning it midway leaves no record — the right behaviour for a cancelled
request.

## Delegating to another agent

There is no "team" object here, and that is the design: an agent already
knows how to pick a tool by name and read what it returns, so the cheapest
way to hand work to a specialist is to **make the specialist a tool**.

```python
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

run = await writer.run("Write a short brief about PIX.")
for step in run.steps:
    print(step.kind, step.name, len(step.children))
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
run = await writer.run("...")
print([a.name for a in run.artifacts])
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
from tempest_fastapi_sdk.agents import AgentRun, run_until

def parses(run: AgentRun) -> bool:
    """Accept only output that is valid JSON."""
    import json
    try:
        json.loads(run.output)
    except ValueError:
        return False
    return run.succeeded

result = await run_until(
    agent,
    "Return the data as JSON.",
    until=parses,
    max_rounds=4,
    max_seconds=120,
)
print(result.accepted, result.rounds, result.output)
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
from tempest_fastapi_sdk.agents import refine

result = await refine(writer, reviewer, "Write the release notes.")

print(result.accepted, result.rounds)
for iteration in result.iterations:
    print(iteration.index, iteration.accepted, iteration.critique)
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

## Recap

- **`Agent.run(goal)`** returns an `AgentRun`: answer, trace, artifacts and
  **why it stopped**.
- **`AgentBudget`** bounds steps, time and tool calls; time is what actually
  protects a request.
- **Ready-made tools** cover image, vision, audio, RAG and web over the
  models you already host.
- **Named artifacts** chain multimodal work without disk or base64.
- **A tool error becomes an observation** for the model, not an exception.
- **Persistence is opt-in** — memory by default, ORM when you want it.
- **`agent_tool`** turns one agent into another's tool; the clock is
  inherited, depth is bounded and the child's trace nests.
- **`run_until`** keeps going until your predicate accepts; **`refine`** uses
  a critic. In both, `accepted=False` means nothing passed.

Where to go next: [Self-hosted generative AI](genai.md) for the models
themselves, [Image generation](image-generation.md) and
[Model weights](model-weights.md) to pin what the agent uses.
