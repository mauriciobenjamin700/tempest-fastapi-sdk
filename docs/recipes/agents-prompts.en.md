# AI agents (prompts)

The system prompt is the only text the model reads on **every** turn of the
loop, and it is the one that shows up least in the code: a loose string in the
first example's `Agent(...)`, and then a `SERVICE_AGENT_PROMPT` imported from a
`prompts/` folder that [the architecture page](agents-architecture.md) names
without showing.

This page shows the folder, what goes in each file, how to compose a prompt
that depends on who is asking, and how to test that it reached the model.

!!! tip "Before this page"
    [AI agents](agents.md) up to *Pydantic-typed tools*, and
    [AI agents (architecture)](agents-architecture.md) for the `src/ai/` layout
    the `prompts/` folder lives in.

No example here loads a model: they all run on the `ScriptedBackend` from
[AI agents (testing)](agents-testing.md), which keeps what the agent sent on
each call.

## What the model receives

Before writing a prompt, look at what actually reaches the backend. There are
three things, and only one of them is your `system_prompt`:

```python title="what_the_model_sees.py" hl_lines="35 36 37 42 44 46"
import asyncio
import json

from pydantic import Field

from tempest_fastapi_sdk.agents import Agent, AgentContext, Skill, tool
from tempest_fastapi_sdk.agents.testing import ScriptedBackend, replies
from tempest_fastapi_sdk.schemas import BaseSchema


class SearchArgs(BaseSchema):
    """Arguments for the catalogue search."""

    name: str = Field(description="Partial match on the service title.")


@tool("search_services", "Search the public service catalogue by name.")
async def search_services(args: SearchArgs, context: AgentContext) -> str:
    """Return a canned catalogue line."""
    return f"1 service: {args.name} | Picos/PI"


invoicing = Skill(
    name="invoicing",
    description="Read and validate Brazilian invoices (NF-e).",
    instructions="The full NF-e guide.",
)


async def main() -> None:
    """Run once against a scripted backend and print what it received."""
    backend = ScriptedBackend([replies("ok")])
    agent = Agent(
        backend,
        tools=[search_services],
        skills=[invoicing],
        system_prompt="You are the service-catalogue assistant.",
    )
    await agent.run("Is there an electrician in Picos?")

    print("--- system")
    print(backend.system_prompts[0])
    print("--- user")
    print(backend.prompts[0])
    print("--- tools")
    print(backend.specs_seen[0])
    print(json.dumps(search_services.to_spec(), ensure_ascii=False, indent=2))


if __name__ == "__main__":
    asyncio.run(main())
```

```text
--- system
You are the service-catalogue assistant.

You have skills available. Each is a set of instructions and tools you can load with the 'load_skill' tool when the task needs it:
- invoicing: Read and validate Brazilian invoices (NF-e).
Load a skill before doing work that falls under it. Do not load skills you do not need.
--- user
Is there an electrician in Picos?
--- tools
['search_services', 'load_skill']
{
  "type": "function",
  "function": {
    "name": "search_services",
    "description": "Search the public service catalogue by name.",
    "parameters": {
      "description": "Arguments for the catalogue search.",
      "properties": {
        "name": {
          "description": "Partial match on the service title.",
          "title": "Name",
          "type": "string"
        }
      },
      "required": [
        "name"
      ],
      "type": "object"
    }
  }
}
```

Piece by piece:

* **`system`** is your `system_prompt`, plus the block the SDK **appends** when
  the agent has skills. You do not write that block, but it is there — which is
  why it pays to look at the real output instead of assuming.
* **`user`** is the goal passed to `run()`. It changes on every run; the prompt
  does not.
* **The tools** travel **outside** the prompt, as specs: the name, the
  `@tool` `description` and the arguments' JSON-schema. Notice that even the
  `SearchArgs` docstring reached the model, as the parameters' `description`.
  `load_skill` shows up because the agent has a skill.

The practical consequence of the third item: **the prompt does not need to
repeat the schema**. The model already gets the name, type and description of
every argument. What the prompt adds is what the schema does not say — *when*
to use each tool.

!!! info "The system message is the same on every turn"
    The `system` message is built once per run and resent unchanged on every
    model call. Measured on a three-turn run: the three
    `backend.system_prompts` were identical. That is why data that changes
    mid-run does not belong in the prompt (more on that
    [below](#what-not-to-put-in-the-prompt)).

## The `prompts/` folder

One prompt per agent, and one file with the rules they all share:

```text
src/ai/prompts/
├── __init__.py   # re-exports the constants
├── base.py       # rules common to every agent
├── service.py    # SERVICE_AGENT_PROMPT
└── support.py    # SUPPORT_AGENT_PROMPT
```

```python title="src/ai/prompts/base.py"
"""Text blocks every agent in this service shares."""

TOOL_FAILURE_RULE: str = (
    "When a tool fails, read the error and change the call; "
    "do not repeat the same call."
)
NO_GUESSING_RULE: str = (
    "Answer only from what the tools returned. "
    "If they did not bring the answer, say you could not find it."
)
ANSWER_FORMAT_RULE: str = "Answer in English, in at most three sentences, no markdown."

BASE_RULES: str = "\n".join(
    [TOOL_FAILURE_RULE, NO_GUESSING_RULE, ANSWER_FORMAT_RULE],
)
```

```python title="src/ai/prompts/service.py"
"""System prompt of the service-catalogue agent."""

from src.ai.prompts.base import BASE_RULES

SERVICE_AGENT_PROMPT: str = f"""\
You are the platform's service-catalogue assistant.

Scope: the public service catalogue and the services the user published.
For anything else, say you cannot help with that.

Tools:
- search_services: questions about the public catalogue ("is there an electrician in Picos?").
- get_my_services: questions about what the user published.

{BASE_RULES}"""
```

```python title="src/ai/prompts/support.py"
"""System prompt of the support agent."""

from src.ai.prompts.base import BASE_RULES

SUPPORT_AGENT_PROMPT: str = f"""\
You are the platform's support desk.

Scope: account, payment and how-to questions about the site.
For questions about the service catalogue, say the catalogue assistant answers those.

Tools:
- search_help: search the FAQ before answering any how-to question.
- open_ticket: only when the FAQ did not solve it and the user asks for a human.

{BASE_RULES}"""
```

```python title="src/ai/prompts/__init__.py"
from src.ai.prompts.base import (
    ANSWER_FORMAT_RULE,
    BASE_RULES,
    NO_GUESSING_RULE,
    TOOL_FAILURE_RULE,
)
from src.ai.prompts.service import SERVICE_AGENT_PROMPT
from src.ai.prompts.support import SUPPORT_AGENT_PROMPT

__all__: list[str] = [
    "ANSWER_FORMAT_RULE",
    "BASE_RULES",
    "NO_GUESSING_RULE",
    "SERVICE_AGENT_PROMPT",
    "SUPPORT_AGENT_PROMPT",
    "TOOL_FAILURE_RULE",
]
```

What each choice buys:

* **A module constant, `UPPER_SNAKE_CASE`.** The prompt is configuration, not
  logic: importing `SERVICE_AGENT_PROMPT` is what `src/ai/agents/service.py`
  does, and a test imports the same constant to assert on it.
* **A separate `base.py`.** The tool-failure rule written once applies to both
  agents; fixed once, it is fixed for both. The [test](#testing-the-prompt)
  makes sure no prompt forgot to include it.
* **`__init__.py` re-exports.** The rest of the service imports
  `from src.ai.prompts import SERVICE_AGENT_PROMPT`, never from the submodule —
  the same import rule as every package in the service.
* **Prompt text in the file, not in the database.** Changing a prompt changes
  behaviour; in a file, the change goes through review and tests like any
  other.

To see the assembled result, run this next to `src/`:

```python title="show_prompt.py"
import asyncio

from tempest_fastapi_sdk.agents import Agent
from tempest_fastapi_sdk.agents.testing import ScriptedBackend, replies

from src.ai.prompts import SERVICE_AGENT_PROMPT


async def main() -> None:
    """Print the system message exactly as the backend receives it."""
    backend = ScriptedBackend([replies("ok")])
    await Agent(backend, system_prompt=SERVICE_AGENT_PROMPT).run("hi")

    print(backend.system_prompts[0])


if __name__ == "__main__":
    asyncio.run(main())
```

```text
You are the platform's service-catalogue assistant.

Scope: the public service catalogue and the services the user published.
For anything else, say you cannot help with that.

Tools:
- search_services: questions about the public catalogue ("is there an electrician in Picos?").
- get_my_services: questions about what the user published.

When a tool fails, read the error and change the call; do not repeat the same call.
Answer only from what the tools returned. If they did not bring the answer, say you could not find it.
Answer in English, in at most three sentences, no markdown.
```

## Anatomy of a prompt

The `SERVICE_AGENT_PROMPT` above has five parts. The order and the wording are
a **writing judgement**, not a measured result: this page did not compare one
prompt's hit rate against another's with a real model, and you should distrust
anyone who claims that without naming the model and the N.

| Part | Answers | In the example |
| --- | --- | --- |
| Role | who the model is in this conversation | "service-catalogue assistant" |
| Scope | what is in, and what to do with the rest | "For anything else, say you cannot help" |
| When to use each tool | the decision the schema does not carry | `search_services` for the public catalogue, `get_my_services` for the user's own |
| Tool failure | what to do when the observation is an error | `TOOL_FAILURE_RULE` |
| Format | what the final answer should look like | `ANSWER_FORMAT_RULE` |

Three things here are **not** judgement, they are SDK mechanics:

* **A tool name in the prompt must be the real name.** The model calls by name;
  a prompt that says `search_catalog` when the tool is `search_services` teaches
  the model to ask for a tool that does not exist, and the observation comes
  back as `unknown tool 'search_catalog'; available: ...`.
* **The error the model reads is the one the tool wrote.** The failure rule
  only works if the `AgentToolError` message says what to change —
  [A failing tool does not end the run](agents.md#a-failing-tool-does-not-end-the-run)
  shows how that message reaches the model.
* **Tool descriptions live on the `@tool`, not here.** The prompt says *when*;
  the `description` and the schema say *what*. Writing the *what* twice is two
  descriptions that drift apart on the first edit.

## What the SDK already puts in the prompt

### `DEFAULT_SYSTEM_PROMPT`

`Agent(...)` without `system_prompt=` uses the `DEFAULT_SYSTEM_PROMPT`
constant:

```text
You are a capable assistant working towards the user's goal. Use the available tools when they help and answer directly when they do not. When a tool fails, read the error and try a different approach rather than repeating the same call. When you have the answer, reply with it and stop calling tools.
```

Passing `system_prompt=` **replaces** that text — it does not append to it.
Measured: with `system_prompt="X"`, the backend receives exactly `"X"`. If you
want to keep the default's rules and add your own, compose:

```python title="extend_default.py"
from tempest_fastapi_sdk.agents import DEFAULT_SYSTEM_PROMPT, Agent
from tempest_fastapi_sdk.agents.testing import ScriptedBackend

SHOP_PROMPT: str = DEFAULT_SYSTEM_PROMPT + "\n\nAlways answer in English."


def build_agent() -> Agent:
    """Build an agent that keeps the default rules and adds one of its own.

    Returns:
        Agent: The agent, on a scripted backend for the example.
    """
    return Agent(ScriptedBackend([]), system_prompt=SHOP_PROMPT)
```

The `BASE_RULES` in the `prompts/` folder is the same idea with your service's
rules in place of the SDK's.

### `skills_prompt`

With `skills=`, the agent appends the `skills_prompt(skills)` block to your
prompt — what you saw in this page's first output, starting at
`You have skills available.` The block comes **after** your text, and each
skill takes one line: `- name: description`. The skill's full instructions only
arrive when the model calls `load_skill`
([Skills](agents-advanced.md#skills-capabilities-loaded-on-demand)).

Two effects on your prompt:

* **Do not list the skills again.** The block already lists them; repeating is
  the same information twice in the context, on every turn.
* **A sentence of yours on *when* to load each skill still pays off**, for the
  same reason as "when to use each tool" above.

### `DEFAULT_CRITIC_PROMPT`

`refine(worker, critic, goal)` uses `DEFAULT_CRITIC_PROMPT` (or whatever you
pass as `critic_prompt=`). It does **not** go into the critic's system message:
it opens the `user` message, followed by `GOAL:` and `WORK TO REVIEW:`.
Measured with a critic built with `system_prompt="C"`: the system received was
`C`, and the `user` message started with the `DEFAULT_CRITIC_PROMPT` text. A
critic prompt of your own must keep asking for the exact word `APPROVED` — that
is what `refine` decides on
([Loop: generate, critique, revise](agents-advanced.md#loop-generate-critique-revise)).

## A prompt that depends on the request

`facts_prompt` and `recall_prompt` return text blocks to append to the
prompt — the user's facts, what earlier conversations might contribute. Both
depend on **who** is asking, and `Agent` takes the prompt in its constructor:
`run()` does not accept another one. So a per-request prompt is a
**per-request agent**, built by a function:

```python title="prompt_per_request.py" hl_lines="25 38 49 50"
import asyncio

from tempest_fastapi_sdk.agents import (
    Agent,
    AgentBackend,
    FactStore,
    InMemoryFactStore,
    facts_prompt,
)
from tempest_fastapi_sdk.agents.testing import ScriptedBackend, replies

from src.ai.prompts import SERVICE_AGENT_PROMPT


def build_agent(generator: AgentBackend, prompt: str) -> Agent:
    """Build the agent of one request around the process-wide generator.

    Args:
        generator (AgentBackend): The shared backend; it is reused, never copied.
        prompt (str): The system prompt composed for this request.

    Returns:
        Agent: An agent whose prompt belongs to this request only.
    """
    return Agent(generator, system_prompt=prompt, name="service-agent")


async def prompt_for(user_id: str, facts: FactStore) -> str:
    """Compose the fixed prompt with what is known about this user.

    Args:
        user_id (str): The authenticated caller.
        facts (FactStore): Where durable facts live.

    Returns:
        str: The agent prompt plus the user's facts block.
    """
    return SERVICE_AGENT_PROMPT + await facts_prompt(facts, subject=user_id)


async def main() -> None:
    """Serve two users with one generator and show each prompt's tail."""
    facts = InMemoryFactStore()
    await facts.put("city", "Picos/PI", subject="ana")
    await facts.put("city", "Teresina/PI", subject="bruno")
    generator = ScriptedBackend([replies("ok"), replies("ok")])

    for user_id in ("ana", "bruno"):
        agent = build_agent(generator, await prompt_for(user_id, facts))
        await agent.run("Is there an electrician near me?")

    for prompt in generator.system_prompts:
        print(prompt.splitlines()[-2:])


if __name__ == "__main__":
    asyncio.run(main())
```

```text
['What you already know:', '- city: Picos/PI']
['What you already know:', '- city: Teresina/PI']
```

One generator, two agents, two prompts. The expensive part — the model weights —
stays in `runtime.py`, one per process; what is built per request is just the
`Agent` object, which holds references to the generator and to the tools that
already existed.

### What building the agent costs

Measured on the same i9-13900F under WSL2 as
[What each step costs](agents.md#what-each-step-costs), Python 3.11.12, median
of 5 rounds:

```text
8 tools + 3 skills: 3.28 µs (N=100000 x 5)
8 tools, no skills: 1.37 µs (N=100000 x 5)
facts_prompt + build: 12.18 µs (N=20000 x 5)
```

The cheapest model step measured on that page was 0.29 s (Ollama,
`qwen2.5:0.5b`, warm). Building the agent with eight typed tools and three
skills costs ~3.3 µs — five orders of magnitude below a single model turn. The
skills double the cost because the constructor builds the `load_skill` tool and
concatenates the `skills_prompt` block; the tools are not rebuilt, only the list
that references them.

??? info "The measurement script"

    ```python title="measure_build.py"
    import asyncio
    import statistics
    import timeit
    from collections.abc import Callable

    from pydantic import Field

    from tempest_fastapi_sdk.agents import (
        Agent,
        AgentBudget,
        AgentContext,
        AgentTool,
        InMemoryFactStore,
        Skill,
        facts_prompt,
        tool,
    )
    from tempest_fastapi_sdk.agents.testing import ScriptedBackend
    from tempest_fastapi_sdk.schemas import BaseSchema

    ROUNDS: int = 5


    class SearchArgs(BaseSchema):
        """Arguments shared by the measured tools."""

        query: str = Field(description="What to search for.", max_length=255)
        city: str | None = Field(default=None, description="City name.")
        page: int = Field(default=1, ge=1)


    def make_tool(index: int) -> AgentTool:
        """Build one typed tool, the way a service builds them at import.

        Args:
            index (int): Suffix that keeps the tool names unique.

        Returns:
            AgentTool: The tool.
        """

        @tool(f"tool_{index}", f"Tool {index}: search one domain.")
        async def handler(args: SearchArgs, context: AgentContext) -> str:
            """Echo the query."""
            return args.query

        return handler


    TOOLS: list[AgentTool] = [make_tool(index) for index in range(8)]
    SKILLS: list[Skill] = [
        Skill(
            name=f"skill_{index}",
            description=f"Domain {index}.",
            instructions="x" * 2000,
            tools=[make_tool(100 + index)],
        )
        for index in range(3)
    ]
    GENERATOR: ScriptedBackend = ScriptedBackend([])
    PROMPT: str = "You are the service assistant. " * 20


    def build_agent(prompt: str, *, skills: list[Skill]) -> Agent:
        """Build the per-request agent around the shared generator.

        Args:
            prompt (str): The request's system prompt.
            skills (list[Skill]): The skills to attach.

        Returns:
            Agent: A fresh agent; the generator is shared, not copied.
        """
        return Agent(
            GENERATOR,
            tools=TOOLS,
            skills=skills,
            system_prompt=prompt,
            budget=AgentBudget(max_steps=8, max_seconds=30.0),
        )


    def median_us(call: Callable[[], object], number: int) -> float:
        """Return the median cost of ``call`` in microseconds over ROUNDS rounds.

        Args:
            call (Callable[[], object]): What to time.
            number (int): Calls per round.

        Returns:
            float: Median microseconds per call.
        """
        rounds = [timeit.timeit(call, number=number) / number * 1e6 for _ in range(ROUNDS)]
        return statistics.median(rounds)


    def main() -> None:
        """Time the construction with and without skills, and with facts."""
        loop = asyncio.new_event_loop()
        facts = InMemoryFactStore()
        loop.run_until_complete(facts.put("city", "Picos/PI", subject="ana"))

        async def with_facts() -> Agent:
            """Compose the prompt from the store, then build."""
            block = await facts_prompt(facts, subject="ana")
            return build_agent(PROMPT + block, skills=SKILLS)

        with_skills = median_us(lambda: build_agent(PROMPT, skills=SKILLS), 100_000)
        without = median_us(lambda: build_agent(PROMPT, skills=[]), 100_000)
        composed = median_us(lambda: loop.run_until_complete(with_facts()), 20_000)
        print(f"8 tools + 3 skills: {with_skills:.2f} µs (N=100000 x {ROUNDS})")
        print(f"8 tools, no skills: {without:.2f} µs (N=100000 x {ROUNDS})")
        print(f"facts_prompt + build: {composed:.2f} µs (N=20000 x {ROUNDS})")
        loop.close()


    if __name__ == "__main__":
        main()
    ```

    Two earlier runs of the same script (Portuguese labels) gave 3.30 / 1.39 /
    12.11 µs and 3.37 / 1.40 / 12.17 µs.

### In the service

In the [architecture](agents-architecture.md) layout the factory lives in
`src/ai/agents/` and the controller calls it on every request. The `run_sink`
and the `FactStore` come in as parameters, as they do there:

```python title="src/ai/agents/service.py"
from tempest_fastapi_sdk.agents import Agent, AgentRunSink, FactStore, facts_prompt

from src.ai.prompts import SERVICE_AGENT_PROMPT
from src.ai.runtime import agent_budget, generator
from src.ai.tools import get_my_services, search_services


async def build_service_agent(
    user_id: str,
    *,
    facts: FactStore,
    run_sink: AgentRunSink | None = None,
) -> Agent:
    """Build the service agent for one caller.

    Args:
        user_id (str): The authenticated caller; their facts join the prompt.
        facts (FactStore): Where durable facts live.
        run_sink (AgentRunSink | None): Where finished runs are recorded.

    Returns:
        Agent: A fresh agent around the process-wide generator and tools.
    """
    prompt = SERVICE_AGENT_PROMPT + await facts_prompt(facts, subject=user_id)
    return Agent(
        generator,
        tools=[search_services, get_my_services],
        system_prompt=prompt,
        budget=agent_budget(),
        run_sink=run_sink,
        name="service-agent",
    )
```

```python title="src/controllers/ai.py"
from tempest_fastapi_sdk.agents import AgentRunSink, FactStore

from src.ai import build_service_agent, context_for
from src.db.models import UserModel
from src.schemas import AgentAnswerResponseSchema, AgentAskRequestSchema


class AIController:
    """Controller for the agent-backed endpoints."""

    def __init__(self, facts: FactStore, run_sink: AgentRunSink | None) -> None:
        """Store the process-wide collaborators.

        Args:
            facts (FactStore): Where durable facts live.
            run_sink (AgentRunSink | None): Where finished runs are recorded.
        """
        self.facts: FactStore = facts
        self.run_sink: AgentRunSink | None = run_sink

    async def ask(
        self,
        user: UserModel,
        data: AgentAskRequestSchema,
    ) -> AgentAnswerResponseSchema:
        """Answer a question with a prompt built for this caller.

        Args:
            user (UserModel): The authenticated caller.
            data (AgentAskRequestSchema): The question.

        Returns:
            AgentAnswerResponseSchema: The answer plus what the run did.
        """
        agent = await build_service_agent(
            str(user.id),
            facts=self.facts,
            run_sink=self.run_sink,
        )
        run = await agent.run(data.question, context=context_for(user.id))
        return AgentAnswerResponseSchema(
            output=run.output,
            succeeded=run.succeeded,
            stop_reason=str(run.stop_reason),
            tool_calls=run.tool_calls,
            seconds=run.seconds,
        )
```

!!! note "`make_agent_router` takes a ready-made agent"
    `make_agent_router(agent, ...)` always serves the same `Agent`, hence the
    same prompt. A per-request prompt needs an endpoint of your own, like the
    controller above — which is what the
    [architecture](agents-architecture.md#the-endpoint-why-not-make_agent_router)
    already recommends when the tool needs more than an id.

## What not to put in the prompt

**Identity and permission.** "The user is 42, only show their services" is a
sentence, and the model can ignore a sentence. Who is asking travels on the
`AgentContext` and the tool reads it with `require_user_id`
([`policy.py`](agents-architecture.md#policypy-identity-is-never-an-argument)).
A fact in the prompt (`city: Picos/PI`) helps the model answer; it does not
**restrict** what the tool returns.

**Data that changes during the run.** The system message is the same on every
turn of a run ([measured above](#what-the-model-receives)). Stock, balance, an
order's status: whatever can change while the agent works is a tool result,
read at the moment the model needs it.

**The tools' schema.** It already goes in the specs, as this page's first
output showed.

**Secrets.** The prompt is text the model reads, and nothing in the SDK stops
the model from repeating it in the answer. API keys and credentials stay in the
tool's code.

## Testing the prompt

The prompt is a constant, so its test is fast — and the `ScriptedBackend` lets
you assert on what reached the model:

```python title="test_prompts.py" hl_lines="12 13 26 27"
import pytest

from tempest_fastapi_sdk.agents import InMemoryFactStore
from tempest_fastapi_sdk.agents.testing import ScriptedBackend, replies

from prompt_per_request import build_agent, prompt_for
from src.ai.prompts import BASE_RULES, SERVICE_AGENT_PROMPT, SUPPORT_AGENT_PROMPT


def test_every_agent_prompt_carries_the_base_rules() -> None:
    """A prompt that forgot the shared rules is a drift, caught here."""
    assert BASE_RULES in SERVICE_AGENT_PROMPT
    assert BASE_RULES in SUPPORT_AGENT_PROMPT


@pytest.mark.asyncio
async def test_each_request_sees_only_its_own_facts() -> None:
    """Ana's facts must not reach the run started for Bruno."""
    facts = InMemoryFactStore()
    await facts.put("city", "Picos/PI", subject="ana")
    generator = ScriptedBackend([replies("ok"), replies("ok")])

    await build_agent(generator, await prompt_for("ana", facts)).run("hi")
    await build_agent(generator, await prompt_for("bruno", facts)).run("hi")

    assert "Picos/PI" in generator.system_prompts[0]
    assert generator.system_prompts[1] == SERVICE_AGENT_PROMPT
```

```bash
pytest test_prompts.py -q
```

```text
..                                                                       [100%]
2 passed in 0.52s
```

The second test asserts something only the real output shows: with no fact at
all, `facts_prompt` returns an empty string and Bruno's prompt is **exactly**
`SERVICE_AGENT_PROMPT`, with no orphan heading.

!!! tip "What this test does not answer"
    Whether the model **follows** the prompt — picks the tool the prompt says,
    respects the format — is a question for the real model, in a separate,
    marked suite: [What about a real model?](agents-testing.md#what-about-a-real-model)

## Recap

- **The model receives three things**: the system message (your prompt plus
  what the SDK appends), the goal and the tool specs. `ScriptedBackend` shows
  all three without a model.
- **`src/ai/prompts/`** has one file per agent and a `base.py` with the shared
  rules, as `UPPER_SNAKE_CASE` constants re-exported from `__init__.py`.
- **The prompt says *when*; the `@tool` says *what*.** A tool name quoted in
  the prompt must be the real name.
- **`system_prompt=` replaces `DEFAULT_SYSTEM_PROMPT`**; compose with it if you
  want to keep the SDK's rules. Skills append their own block after your text.
- **A per-request prompt is a per-request agent**: a function builds the
  `Agent` around the process's generator. Measured, it costs ~3.3 µs with eight
  tools and three skills.
- **Identity, permission, volatile data and secrets stay out of the prompt.**
- **Test the constant and what reached the model**; whether the model obeys is
  another suite.

See also:
[AI agents (advanced)](agents-advanced.md#memory-three-layers-and-which-to-pick)
for the `facts_prompt` and `recall_prompt` blocks.
