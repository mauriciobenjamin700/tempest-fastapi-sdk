# AI agents

An **agent** takes a goal, decides what to do, calls tools, and reports what
it did. That last part is what separates it from a chat: the run comes back
with a **step-by-step trace** — arguments, outputs, timings, failures — plus
whatever files it produced.

The ready-made tools wrap the models the SDK already runs locally: text,
image, audio and RAG. No paid API, nothing leaving the machine.

```bash
uv add "tempest-fastapi-sdk[genai]"   # agents needs no extra; the model does
```

!!! info "Submodule, no extra"
    `from tempest_fastapi_sdk.agents import Agent`. The module imports with
    no extra at all — the weight lives in the objects **you** inject, and
    each keeps its own lazy loading.

!!! warning "The model is what pulls the extra in"
    An agent with no model does nothing, and every example on this page
    injects a `TextGenerator`, which lives in `[genai]`. Without it the
    first instantiation raises `ImportError: Text generation requires the
    optional [genai] extra.` The same holds for `[genai-image]`,
    `[genai-audio]` and `[genai-rag]` in the sections below.

!!! abstract "Want the mechanism before the code?"
    [Agents: how they work inside](agents-concepts.md) shows the loop with the
    literal transcript the model receives on each turn, the vocabulary (step,
    observation, artifact, budget) and the criterion for choosing between a
    tool, a skill, delegation and a loop. This page assumes the mechanism;
    that one explains it.


    Read it in order: it builds an agent from nothing up to serving it over
    HTTP. When you are done, [AI agents (advanced)](agents-advanced.md)
    covers structured output, memory, skills, delegation between agents and
    autonomous loops.

## Your first agent

```python title="agent_setup.py" hl_lines="27 33 41"
import asyncio
from typing import Any

from tempest_fastapi_sdk.agents import Agent, AgentContext, text_tool
from tempest_fastapi_sdk.genai import TextGenerator, TextModel


async def get_weather(arguments: dict[str, Any], _context: AgentContext) -> str:
    """Return the weather for a city."""
    return f"{arguments['city']}: 22 degrees, clear sky"


weather_tool = text_tool(
    "get_weather",
    "Get the current weather for a city.",
    get_weather,
    parameters={
        "type": "object",
        "properties": {"city": {"type": "string", "description": "City name."}},
        "required": ["city"],
    },
)


def build_agent() -> Agent:
    """Build the agent the rest of this page imports."""
    return Agent(TextGenerator(TextModel.QWEN2_5_0_5B_INSTRUCT), tools=[weather_tool])


async def main() -> None:
    """Run the agent once and print the answer plus the step trace."""
    agent = build_agent()
    run = await agent.run("What is the weather in Recife? Use the tool.")

    print(run.output)
    print(run.tool_calls)
    print([(step.kind, step.name) for step in run.steps])


if __name__ == "__main__":
    asyncio.run(main())
```

```bash
python agent_setup.py
```

```text
The weather in Recife is 22 degrees, clear sky.
['get_weather']
[('model', 'chat'), ('tool', 'get_weather'), ('model', 'chat')]
```

!!! info "Weights download once — then it is a disk cache"
    The first call writes the gigabytes to `$HF_HOME/hub` (or
    `~/.cache/huggingface/hub`); later runs read them from there, no network.
    In a **container with no volume** that is lost on every restart. Pointing
    the cache somewhere durable, pinning the revision, pre-downloading at
    deploy time and running offline are all in
    **[Model weights »](model-weights.md#where-the-weights-live-and-why-the-second-run-is-instant)**.

Three steps: the model asked for the tool, the tool ran, the model read the
result and answered. All of it on a 0.5B model running on CPU.

What happened underneath: the agent sent the model the `system_prompt` plus
your goal, alongside the list of tools. The model **executed nothing** — it
returned a request (`get_weather`, `{"city": "Recife"}`). The agent ran the
handler, appended the output to the conversation as a `tool` message and asked
again; that time the model answered without asking for anything else, and the
loop closed as `completed`. The literal transcript of those two calls is in
[how they work inside](agents-concepts.md#what-the-model-sees-on-each-turn).

!!! warning "`agent.run` is a coroutine — it needs an async context"
    `await` outside an `async` function is a `SyntaxError`. That is why the
    call lives in `async def main()` and the file ends with
    `asyncio.run(main())`. Inside a FastAPI endpoint (`async def`) you are
    already in an async context: call `await agent.run(...)` directly, no
    `asyncio.run`.

!!! info "Every example on this page is a file you can run"
    Save the block above as `agent_setup.py`. The examples that follow are
    complete files sitting next to it, importing what was already built
    (`from agent_setup import build_agent`) instead of repeating thirty
    lines of setup — no snippet leaning on a name that exists nowhere.

!!! tip "The tool description is what matters"
    The model picks by `description` — it is the only text it reads about
    your tool. Worth more care than the implementation.

## Always check `stop_reason`

```python title="stop_reason.py" hl_lines="11 12"
import asyncio

from agent_setup import build_agent


async def main() -> None:
    """Print the answer only when the model decided it was finished."""
    agent = build_agent()
    run = await agent.run("Compare the weather in Recife, Olinda and Jaboatão.")

    if not run.succeeded:
        print("truncated:", run.stop_reason, f"({run.seconds:.1f}s)")
        return
    print(run.output)


if __name__ == "__main__":
    asyncio.run(main())
```

`succeeded` is `True` only when the **model** decided it was done. The other
reasons are the agent cutting the run short:

| `stop_reason` | What happened |
| --- | --- |
| `completed` | The model answered with text, without asking for another tool. |
| `max_steps` | The step budget ran out first. |
| `timeout` | The wall-clock budget ran out first. |
| `max_tool_calls` | The tool-call budget ran out first. |
| `error` | The model backend failed. |
| `blocked` | Moderation rejected the goal or the answer. |
| `empty_response` | The model ended its turn with no text and no tool call. |

!!! warning "A truncated run still carries text"
    The `output` of a cut-short run is the last thing the model said —
    partial work, not a final answer. A caller that ignores `stop_reason`
    presents half-finished work as done.

!!! info "An empty reply is not `completed`"
    A model message with no text (or whitespace only) and no `tool_calls`
    ends in `empty_response`, with `succeeded=False`. Up to 0.303.1 it
    became `completed` with `output == ""`: a success with no answer.
    `qwen2.5:0.5b` on Ollama 0.30.11, in CPU, with this page's goal, ended
    58 of 200 runs that way. The agent does not re-ask on its own — that
    would spend a step of the budget and hide how often the model fails.
    To retry, use
    [`run_until(agent, goal, until=succeeded)`](agents-advanced.md#loop-keep-going-until-it-passes-a-check): on the
    same model, 58 of 60 loops with `max_rounds=3` ended accepted.

## Budget

```python title="budget.py" hl_lines="13"
import asyncio

from agent_setup import weather_tool
from tempest_fastapi_sdk.agents import Agent, AgentBudget
from tempest_fastapi_sdk.genai import TextGenerator, TextModel


async def main() -> None:
    """Run the same agent under an explicit ceiling."""
    agent = Agent(
        TextGenerator(TextModel.QWEN2_5_0_5B_INSTRUCT),
        tools=[weather_tool],
        budget=AgentBudget(max_steps=8, max_seconds=90, max_tool_calls=5),
    )
    run = await agent.run("What is the weather in Recife?")

    print(run.stop_reason, f"{run.seconds:.1f}s", len(run.steps), "steps")


if __name__ == "__main__":
    asyncio.run(main())
```

Steps alone do **not** bound a run: one tool call can hang, and the agent
sits there without burning a single step. That is why every model call and
every tool call runs under the time **left** on the clock and is cancelled
when it runs out, and why `max_seconds` has a default (120s) rather than
being optional. Measured with a tool that sleeps 3 s and `max_seconds=0.5`:
the run ends after 0.75 s with `stop_reason=timeout` — the 0.5 s budget plus
the 0.25 s grace a top-level tool gets so a sub-agent can stop on its own and
hand back its trace.

The step and tool-call ceilings also hold **inside** one turn: a model that
asks for 50 tools at once does not get 50. Measured with `max_steps=5,
max_tool_calls=2` and one turn asking for 50 calls: 2 ran, and the run
stopped at `max_tool_calls`.

The budget exists because the loop's natural stopping condition — "the model
decided it was done" — is exactly what a confused model does not meet.
Measured with a model that never stops asking for a tool and `max_steps=4`:
the run ends `max_steps`, `succeeded=False`, and `output` **empty**, because
it never got around to writing text. Details in
[why the budget exists](agents-concepts.md#why-the-budget-exists).

## The model runs on the generator's defaults

The agent loop calls the model with the messages and the tools, and nothing
else: no `config`, no `max_new_tokens`, no context size. So what applies is
whatever **the generator** ships with — and the generator is where you tune
it:

```python title="defaults.py" hl_lines="12-13"
import asyncio

from agent_setup import weather_tool
from tempest_fastapi_sdk.agents import Agent
from tempest_fastapi_sdk.genai import GenerationConfig, OllamaGenerator


async def main() -> None:
    """Run an agent whose model has a bounded window and greedy decoding."""
    model = OllamaGenerator(
        "ministral-3:14b",
        num_ctx=32768,
        config=GenerationConfig(max_new_tokens=512, do_sample=False),
    )
    agent = Agent(model, tools=[weather_tool])
    run = await agent.run("What's the weather in Recife?")

    print(run.stop_reason, run.output)


if __name__ == "__main__":
    asyncio.run(main())
```

`config=` applies to every call the generator makes, and a call that brings
its own `config` wins field by field. `TextGenerator` takes the same
`config=`.

!!! warning "On Ollama, `num_ctx` is not optional for an agent"
    An agent's prompt is the system turn, every tool spec and **every** tool
    result so far — it grows with each step. Once it passes the context
    window, Ollama cuts the prompt **with no error**. Measured on Ollama
    0.30.11 with `ministral-3:14b`, with a tool returning ~11.5k tokens:
    without `num_ctx` the daemon processed 2,051 tokens, the model lost the
    question and the run ended `completed` with the wrong answer (3 of 3
    runs); with `num_ctx=32768` it processed all 11,551 and answered
    correctly (3 of 3).

## Pydantic-typed tools

Writing JSON-schema by hand next to the handler means **two descriptions of
the same thing**, drifting apart from the first edit: the schema says `city`,
the handler reads `arguments["town"]`, and nothing catches it until a model
calls the tool. The `@tool` decorator removes the duplicate.

```python title="typed_tool_agent.py" hl_lines="17 18"
import asyncio

from pydantic import Field

from tempest_fastapi_sdk.agents import Agent, AgentContext, tool
from tempest_fastapi_sdk.genai import TextGenerator, TextModel
from tempest_fastapi_sdk.schemas import BaseSchema


class WeatherArgs(BaseSchema):
    """Arguments for the weather tool."""

    city: str = Field(description="City to look up.")
    days: int = Field(default=1, ge=1, le=7, description="Forecast horizon in days.")


@tool("get_weather", "Get the current weather for a city.")
async def get_weather(args: WeatherArgs, context: AgentContext) -> str:
    """Return the forecast for the requested city."""
    return f"{args.city}: 22 degrees, {args.days}d"


async def main() -> None:
    """Hand the decorated tool to an agent and run it."""
    agent = Agent(
        TextGenerator(TextModel.QWEN2_5_0_5B_INSTRUCT),
        tools=[get_weather],
    )
    run = await agent.run("What is the 3-day forecast for Olinda?")

    print(run.output)


if __name__ == "__main__":
    asyncio.run(main())
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

```python title="typed_tool_manual.py" hl_lines="19"
from pydantic import Field

from tempest_fastapi_sdk.agents import AgentContext, AgentTool, typed_tool
from tempest_fastapi_sdk.schemas import BaseSchema


class WeatherArgs(BaseSchema):
    """Arguments for the weather tool."""

    city: str = Field(description="City to look up.")
    days: int = Field(default=1, ge=1, le=7, description="Forecast horizon in days.")


async def get_weather_impl(args: WeatherArgs, context: AgentContext) -> str:
    """Return the forecast — a plain function, no decorator involved."""
    return f"{args.city}: 22 degrees, {args.days}d"


built: AgentTool = typed_tool(
    "get_weather",
    "Get the weather.",
    WeatherArgs,
    get_weather_impl,
)
```

## Tools over your local models

This is where the module meets the rest of the SDK:

```python title="multimodal_setup.py" hl_lines="40"
from tempest_fastapi_sdk import HTTPClient
from tempest_fastapi_sdk.agents import (
    Agent,
    describe_image_tool,
    generate_image_tool,
    retrieve_tool,
    speak_tool,
    transcribe_audio_tool,
    web_search_tool,
)
from tempest_fastapi_sdk.genai import (
    Embedder,
    EmbeddingModel,
    ImageGenerator,
    ImageModel,
    TextGenerator,
    TextModel,
    VisionModel,
    VisionTextGenerator,
)
from tempest_fastapi_sdk.genai.audio import SpeechToText, TextToSpeech
from tempest_fastapi_sdk.genai.rag import (
    InMemoryVectorStore,
    Retriever,
    SearxngBackend,
    WebSearch,
)


def build_multimodal_agent() -> Agent:
    """Wire one agent over every local model the SDK can run."""
    retriever = Retriever(
        Embedder(EmbeddingModel.ALL_MINILM_L6_V2),
        InMemoryVectorStore(),
    )
    web_search = WebSearch(
        SearxngBackend("http://localhost:8080", http_client=HTTPClient()),
    )
    return Agent(
        TextGenerator(TextModel.QWEN2_5_7B_INSTRUCT),
        tools=[
            generate_image_tool(ImageGenerator(ImageModel.SDXL_TURBO), default_steps=4),
            describe_image_tool(VisionTextGenerator(VisionModel.QWEN2_VL_2B_INSTRUCT)),
            transcribe_audio_tool(SpeechToText("base")),
            speak_tool(TextToSpeech()),
            retrieve_tool(retriever),
            web_search_tool(web_search),
        ],
    )
```

!!! warning "Each tool pulls its own extra"
    `[genai]` (text), `[genai-image]` (images), `[genai-vlm]` (vision),
    `[genai-audio]` (STT/TTS) and `[genai-rag]` (retriever + web search).
    Install only what you use — weights download on each model's first
    call, not at the instantiation above.

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

```python title="draw_then_look.py" hl_lines="9 17"
import asyncio

from multimodal_setup import build_multimodal_agent


async def main() -> None:
    """Draw an image, then ask the vision model what it drew."""
    agent = build_multimodal_agent()
    run = await agent.run(
        "Draw a red bicycle as bike.png, then tell me what "
        "shows up in the image you created.",
    )

    for step in run.steps:
        print(step.kind, step.name, step.artifacts)

    bike = run.artifact("bike.png")
    if bike is not None:
        print(bike.media_type)


if __name__ == "__main__":
    asyncio.run(main())
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

```python title="failing_tool.py" hl_lines="10 30"
import asyncio
from typing import Any

from tempest_fastapi_sdk.agents import Agent, AgentContext, AgentToolError, text_tool
from tempest_fastapi_sdk.genai import TextGenerator, TextModel


async def save(arguments: dict[str, Any], _context: AgentContext) -> str:
    """Save something, or explain why it could not be saved."""
    raise AgentToolError("disk full")


save_tool = text_tool(
    "save_note",
    "Save a note to disk.",
    save,
    parameters={
        "type": "object",
        "properties": {"text": {"type": "string"}},
        "required": ["text"],
    },
)


async def main() -> None:
    """Show that a raising tool becomes an observation, not a crash."""
    agent = Agent(TextGenerator(TextModel.QWEN2_5_0_5B_INSTRUCT), tools=[save_tool])
    run = await agent.run("Save the note 'buy bread'.")

    failed = [step for step in run.steps if step.error]
    print(failed[0].error)
    print(run.stop_reason, run.output)


if __name__ == "__main__":
    asyncio.run(main())
```

The step is marked with `error`, and the message goes back **to the model**
as an observation. It usually tries another route. Letting the exception
escape would throw away everything the run had done so far.

```text
AgentToolError: disk full
completed I could not save the note: the disk is full.
```

Any exception from the handler also becomes an observation, with one
difference that matters: an `AgentToolError` message is treated as
**written to be shown** and goes onto the trace in full, while for any other
exception the trace keeps only its type. The trace is what the HTTP router,
the SSE stream and the sinks expose, and an arbitrary exception carries a
DSN, a token or a file path. Measured with a handler raising
`RuntimeError("could not connect to postgresql://admin:hunter2@db:5432/app")`:
the model reads the whole text, and the step records
`RuntimeError: the tool failed (details withheld)` — the password is not in
`run.model_dump_json()`. The full exception goes to the log
(`tempest_fastapi_sdk.agents.agent`). In development,
`Agent(..., expose_tool_errors=True)` records the whole text on the trace.

!!! tip "Arguments as a JSON string"
    OpenAI-format servers (vLLM, TGI, hosted APIs) send a call's
    `arguments` as a JSON **string**; the agent parses it. Invalid JSON, or
    anything that is not an object, becomes a tool error the model reads —
    not a call with `{}`. When the call carries an `id`, the `tool` message
    sent back includes `tool_call_id` and `name`.

## Writing your own tool

```python title="report_tool.py" hl_lines="15 33"
import asyncio
from pathlib import Path
from typing import Any

from tempest_fastapi_sdk.agents import (
    Agent,
    AgentArtifact,
    AgentContext,
    AgentTool,
    ToolResult,
)
from tempest_fastapi_sdk.genai import TextGenerator, TextModel


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


report_tool = AgentTool(
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


async def main() -> None:
    """Run the agent and write the artifact it produced to disk."""
    agent = Agent(TextGenerator(TextModel.QWEN2_5_0_5B_INSTRUCT), tools=[report_tool])
    run = await agent.run("Generate a 'Sales' report summarizing the quarter.")

    report = run.artifact("report.md")
    if report is not None:
        Path("report.md").write_bytes(report.data)
        print("written:", report.media_type, len(report.data), "bytes")


if __name__ == "__main__":
    asyncio.run(main())
```

The handler takes **two** arguments: `arguments` (what the model passed) and
`context` (the run's artifacts, plus whatever your application seeds into it).
Returning a plain `str` works too when there is nothing binary — it is wrapped
into a `ToolResult` for you.

!!! tip "A tool that queries the database?"
    That is what almost everyone writes first, and the session does **not**
    arrive through `Depends` — the agent does not live inside a request.
    [AI agents (database) »](agents-db.md) shows the whole pattern, and it is
    where the `context` parameter is unpacked.

!!! tip "A tool that calls an API, or that structures text?"
    The process's HTTP client, a timeout below the budget, 4xx/5xx becoming
    `AgentToolError`, and pulling an object out of free text with `schema_of`
    and `final_answer_tool`: [AI agents (tools) »](agents-tools.md).

!!! tip "Already have `AIChatPipeline` tools?"
    `AgentTool.from_tool(tool)` adapts the chat pipeline's single-argument
    tools without touching them.

## Serving it over HTTP

```python title="app.py" hl_lines="11 14 23 30"
from fastapi import FastAPI, Header

from agent_setup import weather_tool
from tempest_fastapi_sdk.agents import (
    Agent,
    InMemoryAgentRunSink,
    make_agent_router,
)
from tempest_fastapi_sdk.genai import TextGenerator, TextModel

store = InMemoryAgentRunSink(max_runs=50)


def current_user(x_user_id: str = Header()) -> str:
    """Return who is calling.

    A stand-in for your real authentication dependency: trusting a header
    is only acceptable behind a gateway that sets it.
    """
    return x_user_id


agent = Agent(
    TextGenerator(TextModel.QWEN2_5_0_5B_INSTRUCT),
    tools=[weather_tool],
    run_sink=store,
)

app = FastAPI()
app.include_router(make_agent_router(agent, run_store=store, owner=current_user))
```

```bash
uvicorn app:app --reload
```

| Route | What it does |
| --- | --- |
| `POST /api/agent/run` | Runs to completion, returns the record with its `run_id` |
| `POST /api/agent/run/stream` | Each step as an SSE event, then a `done` carrying `{"run_id": ...}` |
| `GET /api/agent/runs` | Recent runs of **the caller** (only with a `run_store`) |
| `GET /api/agent/runs/{run_id}/artifacts/{name}` | Downloads an artifact (only with `run_store`); `name` may contain `/` (`illustrator/bike.png`) |

A kept run is addressed by its `run_id`, a stable id — never by its position
in the history, which shifts with every new run and would make a link from
one second ago serve someone else's run.

`owner=` is the FastAPI dependency that returns **who is calling**. With it,
every run is tagged with that id (`AgentRun.owner`, and `AgentContext.owner`
for tools to read), `GET /runs` lists only the caller's runs, and another
caller's artifact answers `404`, exactly like a run that does not exist.
Without `owner=`, everyone who reaches the router sees every kept run — only
acceptable when a single principal can reach it.

The JSON carries artifacts as **metadata** (name, type, size), never bytes:
a generated image is megabytes, and base64 in the body inflates that by a
third. The bytes come from a second request with the right media type —
which also means an `<img src>` works directly.

!!! tip "Several runs at once on a CPU model"
    Every run that reaches the router calls the same `TextGenerator`, and on
    CPU one decode already takes every core: with no limit, four concurrent
    runs split the machine and all of them finish late. Build the generator
    with `max_concurrent=1` —
    `TextGenerator(TextModel.QWEN2_5_0_5B_INSTRUCT, max_concurrent=1)` — and
    the extra requests wait in line. Measured with that model, four
    concurrent calls: median latency from 22.73 s to 11.91 s, throughput from
    22.4 to 26.9 tokens/s. The agent's time budget counts the time spent in
    line, so size `max_seconds` with that in mind. The numbers, the hardware
    and why it is a thread pool and not a semaphore are in
    [Self-hosted generative AI](genai.md#several-requests-at-once-on-cpu-max_concurrent).

## Agents on CPU: backend, size and budget

With no GPU, every turn of the loop is a full generation on the CPU, and
three choices move a run's time by a factor of five or more: which backend
runs the model, how big the model is, and how much text the tools return.
The budget and the timeout have to fit those numbers, not the other way
around.

```python title="cpu_agent.py" hl_lines="11-16 20"
import asyncio

from agent_setup import weather_tool
from tempest_fastapi_sdk.agents import Agent, AgentBudget
from tempest_fastapi_sdk.genai import GenerationConfig, OllamaGenerator


def build_cpu_agent() -> Agent:
    """Build an agent sized for a CPU-only host."""
    model = OllamaGenerator(
        "qwen2.5:3b",
        num_ctx=8192,
        options={"num_gpu": 0},
        timeout=120.0,
        config=GenerationConfig(max_new_tokens=256),
    )
    return Agent(
        model,
        tools=[weather_tool],
        budget=AgentBudget(max_steps=12, max_seconds=120),
    )


async def main() -> None:
    """Run once and print how long each step took."""
    agent = build_cpu_agent()
    run = await agent.run("Qual o tempo no Recife? Use a ferramenta.")

    print(run.stop_reason, f"{run.seconds:.1f}s")
    for step in run.steps:
        print(step.kind, step.name, f"{step.seconds:.2f}s")


if __name__ == "__main__":
    asyncio.run(main())
```

```text
completed 1.9s
model chat 0.86s
tool get_weather 0.00s
model chat 1.05s
```

(One run with the model already loaded in the daemon; the numbers behind it
are just below.)

Piece by piece:

- **`OllamaGenerator("qwen2.5:3b")`** — a 4-bit GGUF served by Ollama, not
  the `TextGenerator` in `float32`. Why is in the numbers below; memory
  sizing (and why bitsandbytes on CPU is not the way out) is in
  [Self-hosted generative AI › On CPU](genai.md#on-cpu).
- **`num_ctx=8192`** — the agent's prompt grows every step, and Ollama cuts
  whatever passes the window with no error. See
  [The model runs on the generator's defaults](#the-model-runs-on-the-generators-defaults).
- **`options={"num_gpu": 0}`** — forces the CPU on a host that has a GPU
  (that is how the measurements below were taken). On a CPU-only host, drop
  it.
- **`config=GenerationConfig(max_new_tokens=256)`** — a token ceiling per
  turn. On CPU generation is the expensive part, and the loop passes no
  ceiling of its own.
- **`timeout=120.0` and `max_seconds=120`** — the defaults, written out
  because they move together; [The Ollama timeout](#the-ollama-timeout-and-the-budget)
  explains why.

### What each step costs

Measured on an i9-13900F under WSL2 (12 visible logical CPUs = 6 cores × 2,
62 GB of RAM, GPU hidden), torch 2.14 + transformers 4.57.6 for the
`TextGenerator` in `float32`, Ollama 0.30.11 with `num_gpu: 0` for the
Q4_K_M GGUFs (`size_vram` = 0 in `/api/ps`). The agent is the one in this
section, with each backend's default generation settings, and the goal
takes **one** tool call plus the answer (three steps). "Cold" is the first
run of a fresh process, with the weight load inside the first step
(weights already in the OS disk cache); N=3. "Warm" is the next five runs
of each process; N=15. Medians; part of the rounds had another process
busy on one logical CPU.

With the tool returning one line (a ~230-token prompt):

| Backend and model | Cold run | Warm run | Warm model step | Generation |
| --- | --- | --- | --- | --- |
| `TextGenerator` Qwen2.5-0.5B `float32` | 4.39 s | 2.12 s | 1.02 s | 25.6 tokens/s |
| `TextGenerator` Qwen2.5-3B `float32` | 13.87 s | 11.59 s | 5.50 s | 5.2 tokens/s |
| `OllamaGenerator` `qwen2.5:0.5b` | 1.52 s | 0.64 s | 0.29 s | 130 tokens/s |
| `OllamaGenerator` `qwen2.5:3b` | 4.83 s | 1.94 s | 0.84 s | 30.6 tokens/s |

With the tool returning ~4,100 tokens that differ on every run (last step's
prompt: ~4,400 tokens). The `TextGenerator` rereads the whole prompt on
every step; on Ollama the observation changes from run to run so the
prefix cache does not hide the cost:

| Backend and model | Warm run | Last step | Last step's prefill |
| --- | --- | --- | --- |
| `TextGenerator` Qwen2.5-0.5B `float32` | 7.60 s | 6.56 s | 819 tokens/s |
| `TextGenerator` Qwen2.5-3B `float32` | 43.08 s | 37.51 s | 134 tokens/s |
| `OllamaGenerator` `qwen2.5:0.5b` | 9.65 s | 9.34 s | 509 tokens/s |
| `OllamaGenerator` `qwen2.5:3b` | 35.91 s | 35.06 s | 132 tokens/s |

All 180 runs finished inside the default `AgentBudget` (`max_steps=12`,
`max_seconds=120`): the slowest took 51.3 s, and the slowest step 43.0 s
(3B `float32`, cold, long observation).

What the numbers say:

- **GGUF wins at generating, not at reading the prompt.** On the 3B,
  Ollama generated 30.6 tokens/s against 5.2 for `float32` — 6× — and the
  short run dropped from 11.59 s to 1.94 s. But prefill came out the same
  (132 against 134 tokens/s): with a 4k-token observation the step is
  almost all prefill, and the gap shrinks to 35.91 s against 43.08 s.
- **Ollama reuses the prefix; the `TextGenerator` rereads everything.**
  With the same long observation repeated verbatim from one run to the
  next, the last step's prefill dropped to 0.05 s on `qwen2.5:3b` and
  stayed at 32.66 s on the 3B `float32`. The system prompt and tool list
  are the same prefix in every run, which is why the first warm step's
  prefill costs 0.04 s on `qwen2.5:3b` and 1.65 s on the 3B `float32`.
- **Observations are what costs.** On a 3B on this CPU, every thousand
  tokens a tool returns cost ~7.6 s of prefill (1,000 / 132) on either
  backend. A tool that returns the summary rather than the dump is a time
  lever, not only a context one.
- **0.5B on Ollama returned empty replies.** In 12 of 54 runs
  `qwen2.5:0.5b` did not call the tool and Ollama returned the message with
  no text and no `tool_calls`. Up to 0.303.1 that finished `completed` with
  an empty `output` — a success with no answer; it now ends
  [`empty_response`](#always-check-stop_reason), with `succeeded=False`. The same
  model through the `TextGenerator` called the tool in all 36 runs, and
  `qwen2.5:3b` in all 54. On CPU, the 3B through Ollama was the smallest
  that completed every run.
- **The first step pays for the load.** Cold against warm, the first step
  cost +2.2 s on the 0.5B and +2.6 s on the 3B in `float32`, and Ollama
  reported 1.4 s to 2.2 s of `load_duration` on the 3B — with the weights
  already in the disk cache. Call the `TextGenerator`'s `load()` at service
  startup (through `asyncio.to_thread`) so the first user does not pay for
  it.

### Sizing the budget

A run's time is the sum, step by step, of **new prompt tokens ÷ prefill
rate** plus **generated tokens ÷ generation rate**. With the rates measured
above, the arithmetic matches the measurement: on the 3B `float32` with the
long observation, 229/140 + 21/5.4 + 4,381/134 + 23/4.6 ≈ 43.2 s, against
43.08 s measured.

Use that sum, with **your** machine's rates, to pick the ceiling:

- **`max_new_tokens` weighs more than it looks.** The `TextGenerator`
  default is 256 tokens per turn; at 5.2 tokens/s, a turn that uses the
  whole ceiling is ~49 s of generation on the 3B `float32`, and two of them
  already pass 120 s. At 30.6 tokens/s, on the GGUF, the same turn is ~8 s.
- **`max_seconds` is an HTTP request's ceiling**, so it comes from what
  your client will wait for; what you tune to fit inside it is the model,
  the observation size and `max_new_tokens`. At 132 tokens/s, the default
  120 s holds ~15k tokens of observation summed over the whole run, before
  counting generation.
- **The queue counts on the clock.** Concurrent runs on a `TextGenerator`
  with `max_concurrent=1` wait for one another, and the wait comes out of
  the same `max_seconds` — see
  [Several runs at once on a CPU model](#serving-it-over-http). Concurrency
  inside Ollama was not measured here.

### The Ollama timeout and the budget

The `OllamaGenerator`'s `timeout=` (default 120 s) is **per request**, and
the generator's HTTP client does **not** retry a request that times out:
when the client gives up, the daemon aborts the generation, so a new
attempt would start from zero and time out again. The `ReadTimeout` arrives
after one timeout. Measured with `qwen2.5:3b` on CPU and `timeout=10.0`:
the call raised `ReadTimeout` after 10.01 s (median, N=5), with one 10.0 s
`POST /api/chat` in the Ollama log. A connection error and a `429`/`5xx`
are still retried.

??? note "Up to v0.303.1, the timeout was retried three times"
    The client made three attempts, waiting 0.5 s and 1 s between them. On
    the same scenario, with a ~4,400-token prompt (~33 s of prefill), the
    call raised `ReadTimeout` after 31.53 s (a single run), the Ollama log showed three
    10.0 s `POST /api/chat`, and inside an agent the run ended in `error`
    after 33.73 s. On those versions, pass
    `retry_policy=RetryPolicy(max_attempts=1)` (with
    `from tempest_fastapi_sdk import RetryPolicy`): with it, the agent run
    ended in `error` after 10.84 s — one attempt, which is the default
    behaviour from v0.303.2 on.

Two setups that behave:

- **`timeout` greater than or equal to `max_seconds`** (the defaults): the
  budget cuts before the timeout. With `max_seconds=20`, the run ended in
  `timeout` after 20.02 s.
- **A timeout shorter than `max_seconds`**: the turn that outlives the
  timeout fails after one timeout, and the run ends in `error`. For the
  agent to treat it as the end of the budget, prefer the previous setup.

!!! tip "A starting point on CPU"
    `OllamaGenerator` with a 3B GGUF model, an explicit `num_ctx`, an
    explicit `max_new_tokens`, tools that return little text, and the
    `AgentBudget` defaults. Then measure one `run.steps` on your machine and
    redo the sum: the rates on this page come from an i9-13900F, and your
    CPU changes every one of them.

## Recap

- **`Agent.run(goal)`** returns an `AgentRun`: answer, trace, artifacts and
  **why it stopped**.
- **`AgentBudget`** bounds steps, time and tool calls; time is what actually
  protects a request.
- **`@tool`** derives the schema from a Pydantic model — one description,
  and a bad argument becomes a correctable observation.
- **Ready-made tools** cover image, vision, audio, RAG and web over the
  models you already host.
- **Named artifacts** chain multimodal work without disk or base64.
- **A tool error becomes an observation** for the model, not an exception —
  and only `AgentToolError` text reaches the trace in full.
- **`make_agent_router`** publishes `/run`, `/run/stream` and artifact
  download by `run_id`; `owner=` keeps each caller's runs apart.
- **On CPU**, the GGUF through `OllamaGenerator` generates 6× faster than
  `float32` on the 3B, but prefill is the same: observation size and
  `max_new_tokens` are what make a run fit in `max_seconds`.

Next: [AI agents (advanced)](agents-advanced.md) — typed structured output,
the three memory layers, skills loaded on demand, delegation between agents,
and loops that keep going until a check passes.

See also: [AI agents (architecture)](agents-architecture.md) for where each piece
lives in a real service, [Self-hosted generative AI](genai.md) for the models
themselves, [Image generation](image-generation.md) and
[Model weights](model-weights.md) to pin what the agent uses.
