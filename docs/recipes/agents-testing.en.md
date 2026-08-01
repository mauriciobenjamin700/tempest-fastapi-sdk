# Testing and validating an agent

An agent's behaviour depends on what the model decides, which makes it feel
untestable: you cannot assert anything about a 0.5B model's mood.

But almost every bug worth catching is in **your** code, not the model's — a
tool that mishandles an argument, a budget that never fires, a skill whose
tools never unlock, a delegation that loses its artifacts. All of that is
testable: you **write what the model would decide** and assert on what your
agent did with it.

These are the same helpers the SDK's own suite uses across 200+ agent tests.

```python
from tempest_fastapi_sdk.agents.testing import ScriptedBackend, replies
```

!!! info "No model, no network, no extra"
    `tempest_fastapi_sdk.agents.testing` imports with no optional
    dependency. A test that boots a real model is slow, flaky and tests the
    wrong thing.

## The minimal test

```python
import pytest

from tempest_fastapi_sdk.agents import Agent, AgentContext, tool
from tempest_fastapi_sdk.agents.testing import (
    ScriptedBackend,
    assert_completed,
    assert_used_tools,
    replies,
    replies_with_tool,
)
from tempest_fastapi_sdk.schemas import BaseSchema


class WeatherArgs(BaseSchema):
    """Arguments for the weather tool."""

    city: str


@tool("get_weather", "Get the current weather for a city.")
async def get_weather(args: WeatherArgs, context: AgentContext) -> str:
    """Return a canned forecast."""
    return f"{args.city}: 22 degrees"


@pytest.mark.asyncio
async def test_the_agent_uses_the_weather_tool() -> None:
    """The agent should call the tool and answer from its result."""
    backend = ScriptedBackend(
        [
            replies_with_tool("get_weather", {"city": "Recife"}),
            replies("It is 22 degrees in Recife."),
        ],
    )

    run = await Agent(backend, tools=[get_weather]).run("What is the weather in Recife?")

    assert_completed(run)
    assert_used_tools(run, "get_weather")
    assert run.output == "It is 22 degrees in Recife."
```

You stated the model's plan — "call `get_weather` with Recife, then answer" —
and asserted on what the agent did. No model loaded, and the test runs in
milliseconds.

## What to assert

| Helper | Answers |
| --- | --- |
| `assert_completed(run)` | The model finished on its own terms (not cut off by a budget). |
| `assert_used_tools(run, "a", "b")` | Exactly these tools, in this order. |
| `assert_artifact(run, "chart.png", media_type="image/png")` | The artifact exists and has the right type. |
| `tool_steps(run)` | Only the tool steps, to inspect arguments. |
| `failed_steps(run)` | The steps that errored — even in a successful run. |

!!! danger "The mistake almost every test makes"
    Asserting only on `run.output`. A budget-truncated run **also carries
    text** — it is the last thing the model said. A test that checks only the
    text passes on half-finished work.

    That is why `assert_completed` exists, and why it names the
    `stop_reason` when it fails:

    ```text
    AssertionError: run did not complete: stop_reason=max_steps, output='working on it'
    ```

## Testing error recovery

A tool that raises must **not** end the run — the error becomes an
observation and the model tries something else. Test it:

```python
import pytest

from tempest_fastapi_sdk.agents import Agent, AgentContext, AgentToolError, text_tool
from tempest_fastapi_sdk.agents.testing import (
    ScriptedBackend,
    failed_steps,
    replies,
    replies_with_tool,
)


@pytest.mark.asyncio
async def test_the_agent_recovers_from_a_failing_tool() -> None:
    """A raising tool becomes an observation, not a crashed run."""

    async def save(arguments: dict[str, str], context: AgentContext) -> str:
        """Always fail, to exercise the recovery path."""
        raise AgentToolError("disk is full")

    backend = ScriptedBackend(
        [
            replies_with_tool("save", {"text": "x"}),
            replies("I could not save it, but here is the content."),
        ],
    )

    run = await Agent(backend, tools=[text_tool("save", "Save it.", save)]).run("save")

    assert run.succeeded is True
    assert "disk is full" in failed_steps(run)[0].error
```

## Testing the budget

`repeat_last=True` makes the scripted model never stop asking for a tool —
exactly the scenario the ceiling exists for:

```python
import pytest

from tempest_fastapi_sdk.agents import Agent, AgentBudget, AgentContext, StopReason, text_tool
from tempest_fastapi_sdk.agents.testing import ScriptedBackend, replies_with_tool


@pytest.mark.asyncio
async def test_the_step_budget_stops_a_runaway_agent() -> None:
    """A model that never stops asking is what the ceiling is for."""

    async def noop(arguments: dict[str, str], context: AgentContext) -> str:
        """Do nothing, successfully."""
        return "ok"

    backend = ScriptedBackend(
        [replies_with_tool("t", {"text": "x"})],
        repeat_last=True,
    )

    run = await Agent(
        backend,
        tools=[text_tool("t", "T.", noop)],
        budget=AgentBudget(max_steps=4, max_seconds=None),
    ).run("do it forever")

    assert run.stop_reason == StopReason.MAX_STEPS
    assert run.succeeded is False
```

## Testing that a skill hid its tools

`backend.specs_seen` records the names offered on **each** turn, which is how
you prove on-demand loading:

```python
@pytest.mark.asyncio
async def test_skill_tools_are_hidden_until_loaded() -> None:
    """The skill's tools must not exist before load_skill runs."""
    backend = ScriptedBackend(
        [
            replies_with_tool("load_skill", {"name": "invoicing"}),
            replies_with_tool("parse_nfe", {"text": "123"}),
            replies("Done."),
        ],
    )

    run = await Agent(backend, skills=[invoicing]).run("read the invoice")

    assert "parse_nfe" not in backend.specs_seen[0]
    assert "parse_nfe" in backend.specs_seen[1]
```

## Testing that memory reached the model

`backend.system_prompts` records the system prompt of each turn:

```python
@pytest.mark.asyncio
async def test_facts_reach_the_model() -> None:
    """Stored facts must be injected, not merely available."""
    store = InMemoryFactStore()
    await store.put("timezone", "America/Recife", subject="u1")

    backend = ScriptedBackend([replies("ok")])
    agent = Agent(
        backend,
        system_prompt="Base." + await facts_prompt(store, subject="u1"),
    )
    await agent.run("what time is it?")

    assert "timezone: America/Recife" in backend.system_prompts[0]
```

## Testing a backend outage

```python
from tempest_fastapi_sdk.agents.testing import FailingBackend


@pytest.mark.asyncio
async def test_a_backend_outage_does_not_escape() -> None:
    """A dead model must become an ERROR stop, not an exception."""
    run = await Agent(FailingBackend("ollama is down")).run("hi")

    assert run.stop_reason == StopReason.ERROR
    assert "ollama is down" in run.output
```

This matters because an agent usually sits behind an endpoint: an escaping
exception is a 500, while a `StopReason.ERROR` is a response you control.

## Did the script run out?

```python
assert backend.exhausted is True
```

A test that scripts five turns and uses two is usually asserting less than
its author thinks — the agent stopped early and the rest never ran.

## What about a real model?

Scripting cannot answer one question: **does the model pick the right
tool?** Only a model can. Keep those tests separate and marked, out of the
fast suite:

```python
import pytest

from tempest_fastapi_sdk.agents import Agent, AgentBudget
from tempest_fastapi_sdk.genai import TextGenerator


@pytest.mark.model
@pytest.mark.asyncio
async def test_a_real_model_picks_the_weather_tool() -> None:
    """The model must reach for the tool when the goal calls for it."""
    generator = TextGenerator(
        "Qwen/Qwen2.5-0.5B-Instruct",
        device="cpu",
        local_files_only=True,
    )
    agent = Agent(
        generator,
        tools=[get_weather],
        budget=AgentBudget(max_steps=4, max_seconds=300),
    )

    run = await agent.run("What is the weather in Recife? Use the tool.")

    assert "get_weather" in run.tool_calls
```

Register the marker in `pyproject.toml` and exclude it by default:

```toml
[tool.pytest.ini_options]
markers = ["model: needs a real local model (slow)"]
addopts = ["-m", "not model"]
```

!!! tip "Run the model layer before shipping"
    Not on every commit, but before every release. Running against
    Qwen2.5-0.5B is how we found that small models solve the task and
    **answer in prose anyway** — which is what motivated the extraction pass
    in [`run_structured`](agents-advanced.md#structured-output-an-object-not-a-paragraph).
    No fake-based test would have found it: a fake `from_pretrained` accepts
    anything.

## Recap

- **Script the model's decisions** with `ScriptedBackend` — the rest of the
  agent is ordinary code and tests like ordinary code.
- **`assert_completed` before `run.output`**: a truncated run carries text
  too.
- **`specs_seen` / `system_prompts`** prove what reached the model each turn
  — that is how you test skills and memory.
- **`FailingBackend`** makes sure a dead model becomes a response, not a 500.
- **A separate `@model` layer** covers the one thing scripting cannot: does
  the model choose correctly.

See also: [AI agents](agents.md) for the basic track and
[AI agents (advanced)](agents-advanced.md) for memory, skills, delegation
and loops.
