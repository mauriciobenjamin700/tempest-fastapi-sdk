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
sits there without burning a single step. That is why wall-clock is checked
too, and why `max_seconds` has a default (120s) rather than being optional.

The budget exists because the loop's natural stopping condition — "the model
decided it was done" — is exactly what a confused model does not meet.
Measured with a model that never stops asking for a tool and `max_steps=4`:
the run ends `max_steps`, `succeeded=False`, and `output` **empty**, because
it never got around to writing text. Details in
[why the budget exists](agents-concepts.md#why-the-budget-exists).

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

Any exception from the handler is treated the same way — using
`AgentToolError` just makes the intent explicit.

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

!!! tip "Already have `AIChatPipeline` tools?"
    `AgentTool.from_tool(tool)` adapts the chat pipeline's single-argument
    tools without touching them.

## Serving it over HTTP

```python title="app.py" hl_lines="11 15 19"
from fastapi import FastAPI

from agent_setup import weather_tool
from tempest_fastapi_sdk.agents import (
    Agent,
    InMemoryAgentRunSink,
    make_agent_router,
)
from tempest_fastapi_sdk.genai import TextGenerator, TextModel

store = InMemoryAgentRunSink(max_runs=50)
agent = Agent(
    TextGenerator(TextModel.QWEN2_5_0_5B_INSTRUCT),
    tools=[weather_tool],
    run_sink=store,
)

app = FastAPI()
app.include_router(make_agent_router(agent, run_store=store))
```

```bash
uvicorn app:app --reload
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
- **A tool error becomes an observation** for the model, not an exception.
- **`make_agent_router`** publishes `/run`, `/run/stream` and artifact
  download.

Next: [AI agents (advanced)](agents-advanced.md) — typed structured output,
the three memory layers, skills loaded on demand, delegation between agents,
and loops that keep going until a check passes.

See also: [Self-hosted generative AI](genai.md) for the models themselves,
[Image generation](image-generation.md) and
[Model weights](model-weights.md) to pin what the agent uses.
