# AI agents (tools)

Almost every tool a service writes falls into one of three families:

| The tool… | Example | Where |
| --- | --- | --- |
| queries the application's database | "which services exist in Picos?" | [AI agents (database)](agents-db.md) |
| calls an outside API | "where is CEP 64600-000?" | [this page](#a-tool-that-calls-an-api) |
| turns free text into an object | "pull the contact out of this message" | [this page](#a-tool-that-structures-text) |

The first one has its own page. This one covers the other two, and the three
ways of building a tool they use: `@tool`, `typed_tool` and `text_tool`.

!!! tip "Before this page"
    [AI agents](agents.md) up to *Writing your own tool*. Every example here
    runs **offline**: the model is the `ScriptedBackend` from
    [AI agents (testing)](agents-testing.md) and the outside API is an
    `httpx.MockTransport`. The output shown is what each file prints.

## A tool that calls an API

A postal-code (CEP) lookup: the model passes the eight digits, the tool calls
the CEP service and returns the city and state.

```python title="cep_tool.py" hl_lines="61 63 64 92 99 101 107"
import asyncio

import httpx
from pydantic import Field

from tempest_fastapi_sdk.agents import (
    Agent,
    AgentBudget,
    AgentContext,
    AgentToolError,
    tool,
)
from tempest_fastapi_sdk.agents.testing import (
    ScriptedBackend,
    replies,
    replies_with_tool,
    tool_steps,
)
from tempest_fastapi_sdk.schemas import BaseSchema

CEP_TIMEOUT_SECONDS: float = 5.0
AGENT_BUDGET: AgentBudget = AgentBudget(max_steps=12, max_seconds=30.0)


def fake_cep_api(request: httpx.Request) -> httpx.Response:
    """Stand in for the real CEP service, so this file runs offline.

    Args:
        request (httpx.Request): The request the tool sent.

    Returns:
        httpx.Response: A canned answer chosen by the CEP in the path.

    Raises:
        httpx.ReadTimeout: For the CEP that simulates a slow upstream.
    """
    cep = request.url.path.rsplit("/", 1)[-1]
    if cep == "99999999":
        raise httpx.ReadTimeout("timed out", request=request)
    if cep == "50000000":
        return httpx.Response(503, text="upstream unavailable")
    if cep != "64600000":
        return httpx.Response(404, json={"erro": True})
    return httpx.Response(
        200,
        json={
            "cep": "64600-000",
            "logradouro": "",
            "bairro": "",
            "localidade": "Picos",
            "uf": "PI",
            "estado": "Piauí",
            "regiao": "Nordeste",
            "ibge": "2208007",
            "ddd": "89",
            "siafi": "1145",
        },
    )


http_client: httpx.AsyncClient = httpx.AsyncClient(
    base_url="https://cep.example.com",
    timeout=CEP_TIMEOUT_SECONDS,
    transport=httpx.MockTransport(fake_cep_api),
)


class CepArgs(BaseSchema):
    """Arguments for the postal-code lookup."""

    cep: str = Field(description="8-digit CEP, digits only.", pattern=r"^\d{8}$")


@tool("lookup_cep", "Look up the city and state of a Brazilian postal code (CEP).")
async def lookup_cep(args: CepArgs, context: AgentContext) -> str:
    """Call the CEP service and keep only what the model needs.

    Args:
        args (CepArgs): The validated CEP.
        context (AgentContext): The run context (unused here).

    Returns:
        str: One line with the city and the state.

    Raises:
        AgentToolError: For every failure the model can act on — an unknown
            CEP, a slow upstream or an upstream error — with a message
            written for it to read.
    """
    try:
        response = await http_client.get(f"/cep/{args.cep}")
    except httpx.TimeoutException as exc:
        raise AgentToolError(
            f"the CEP service did not answer within {CEP_TIMEOUT_SECONDS:.0f} s; "
            "try again later",
        ) from exc
    except httpx.TransportError as exc:
        raise AgentToolError("the CEP service is down") from exc
    if response.status_code == 404:
        raise AgentToolError(f"CEP {args.cep} does not exist; check the digits")
    if response.is_error:
        raise AgentToolError(
            f"the CEP service failed (HTTP {response.status_code}); "
            "try again later",
        )
    data = response.json()
    return f"CEP {data['cep']}: {data['localidade']}/{data['uf']}"


async def main() -> None:
    """Script five calls — one success, four failures — and print the trace."""
    backend = ScriptedBackend(
        [
            replies_with_tool("lookup_cep", {"cep": "646"}),
            replies_with_tool("lookup_cep", {"cep": "00000000"}),
            replies_with_tool("lookup_cep", {"cep": "99999999"}),
            replies_with_tool("lookup_cep", {"cep": "50000000"}),
            replies_with_tool("lookup_cep", {"cep": "64600000"}),
            replies("CEP 64600-000 is in Picos/PI."),
        ],
    )
    agent = Agent(backend, tools=[lookup_cep], budget=AGENT_BUDGET)
    run = await agent.run("Where is CEP 64600-000?")

    for step in tool_steps(run):
        print(step.arguments["cep"], "->", step.error or step.output)
    print(run.stop_reason, "|", run.output)
    await http_client.aclose()


if __name__ == "__main__":
    asyncio.run(main())
```

```bash
python cep_tool.py
```

```text
646 -> AgentToolError: invalid arguments for lookup_cep: cep: String should match pattern '^\d{8}$'
00000000 -> AgentToolError: CEP 00000000 does not exist; check the digits
99999999 -> AgentToolError: the CEP service did not answer within 5 s; try again later
50000000 -> AgentToolError: the CEP service failed (HTTP 503); try again later
64600000 -> CEP 64600-000: Picos/PI
completed | CEP 64600-000 is in Picos/PI.
```

The model asked five times; four went wrong, each in its own way, and the fifth
answered. Piece by piece:

**The client belongs to the process.** `http_client` is created once, at
import, and every call of the tool reuses the same connection pool. In a
service it lives next to the generator, in `runtime.py`, and is closed in the
lifespan; here the `await http_client.aclose()` at the end of `main` plays that
part. Creating an `AsyncClient` inside the tool opens a new connection on every
model call.

**`transport=` is the only offline line.** `httpx.MockTransport(fake_cep_api)`
swaps the network for a function. In production, drop `transport=` and point
`base_url` at the real service; the rest of the file does not change.

**The client's timeout stays below the budget.** `CEP_TIMEOUT_SECONDS` (5 s)
is lower than the `AGENT_BUDGET`'s `max_seconds` (30 s). The gap decides who
cuts a slow call — and [the next section](#the-clients-timeout-stays-below-the-budget)
shows what happens when it is the budget.

**Every failure becomes an `AgentToolError`, written for the model to read.**
The message says what happened **and** what to do: check the digits, try
later. That is what the model reads as the observation, and what the trace
records. CEP `646` never even made a request: the `CepArgs` `pattern` rejected
the argument before the handler ran.

**Return little.** The service answered ten fields; the tool returns one line
with two. Every bit of text the tool returns goes back to the model on every
later turn of the run
([the context grows every turn](agents-concepts.md#the-context-grows-every-turn-and-you-pay-for-it)).

### The client's timeout stays below the budget

Now the opposite: a service that takes 5 s to answer, a client with a 10 s
timeout and a run with `max_seconds=1.0`.

```python title="cep_budget.py" hl_lines="31 62"
import asyncio

import httpx

from tempest_fastapi_sdk.agents import Agent, AgentBudget, AgentContext, tool
from tempest_fastapi_sdk.agents.testing import (
    ScriptedBackend,
    replies,
    replies_with_tool,
    tool_steps,
)

from cep_tool import CepArgs


async def slow_cep_api(request: httpx.Request) -> httpx.Response:
    """Answer only after five seconds, like an upstream having a bad day.

    Args:
        request (httpx.Request): The request the tool sent.

    Returns:
        httpx.Response: An empty JSON answer, too late to matter.
    """
    await asyncio.sleep(5)
    return httpx.Response(200, json={})


slow_client: httpx.AsyncClient = httpx.AsyncClient(
    base_url="https://cep.example.com",
    timeout=10.0,
    transport=httpx.MockTransport(slow_cep_api),
)


@tool("lookup_cep", "Look up the city and state of a Brazilian postal code (CEP).")
async def lookup_cep_slow(args: CepArgs, context: AgentContext) -> str:
    """Call the slow upstream with a timeout longer than the run's budget.

    Args:
        args (CepArgs): The validated CEP.
        context (AgentContext): The run context (unused here).

    Returns:
        str: The response body.
    """
    response = await slow_client.get(f"/cep/{args.cep}")
    return response.text


async def main() -> None:
    """Let the budget, not the client, end the call."""
    backend = ScriptedBackend(
        [
            replies_with_tool("lookup_cep", {"cep": "64600000"}),
            replies("I could not look the CEP up right now."),
        ],
    )
    agent = Agent(
        backend,
        tools=[lookup_cep_slow],
        budget=AgentBudget(max_seconds=1.0),
    )
    run = await agent.run("Where is CEP 64600-000?")

    print(run.stop_reason, "|", tool_steps(run)[0].error)
    print("model calls:", backend.calls)
    await slow_client.aclose()


if __name__ == "__main__":
    asyncio.run(main())
```

```text
timeout | tool 'lookup_cep' interrupted: the time budget ran out
model calls: 1
```

The budget did the cutting: the run ended in `timeout` and the model was called
**once** — it never read an observation, never got the chance to say "the
service is slow". In `cep_tool.py`, with the client's timeout below the budget,
CEP `99999999` came back as an observation (`did not answer within 5 s`) and
the run went on to `completed`.

!!! info "What the `MockTransport` simulates there"
    `fake_cep_api` raises `httpx.ReadTimeout` for CEP `99999999`. That is the
    exception the real transport raises: measured against a local server that
    accepts the connection and never answers, an `AsyncClient(timeout=0.2)`
    raised `ReadTimeout`, a subclass of `httpx.TimeoutException` — the class
    the tool catches.

!!! tip "Retries spend the run's clock too"
    The SDK's [`HTTPClient`](http-client.md) retries with backoff and a
    circuit-breaker, and takes `transport=` like `httpx.AsyncClient`. Inside a
    tool, every attempt runs under the run's same budget: three 5 s attempts,
    plus the backoff, have to fit in `max_seconds`.

### Why translate the exception

The shortcut is to call `raise_for_status()` and let the `httpx` exception
escape:

```python title="cep_raw.py" hl_lines="34 35"
import asyncio

import httpx

from tempest_fastapi_sdk.agents import Agent, AgentContext, tool
from tempest_fastapi_sdk.agents.testing import (
    ScriptedBackend,
    replies,
    replies_with_tool,
    tool_steps,
)

from cep_tool import CepArgs

API_KEY: str = "s3cr3t"

raw_client: httpx.AsyncClient = httpx.AsyncClient(
    base_url="https://cep.example.com",
    transport=httpx.MockTransport(lambda request: httpx.Response(503)),
)


@tool("lookup_cep", "Look up the city and state of a Brazilian postal code (CEP).")
async def lookup_cep_raw(args: CepArgs, context: AgentContext) -> str:
    """Call the CEP service and let any failure escape untranslated.

    Args:
        args (CepArgs): The validated CEP.
        context (AgentContext): The run context (unused here).

    Returns:
        str: The raw response body.
    """
    response = await raw_client.get(f"/cep/{args.cep}", params={"apikey": API_KEY})
    response.raise_for_status()
    return response.text


async def main() -> None:
    """Show what the trace keeps from an untranslated exception."""
    backend = ScriptedBackend(
        [
            replies_with_tool("lookup_cep", {"cep": "64600000"}),
            replies("I could not look the CEP up right now."),
        ],
    )
    run = await Agent(backend, tools=[lookup_cep_raw]).run("Where is CEP 64600-000?")

    print(tool_steps(run)[0].error)
    await raw_client.aclose()


if __name__ == "__main__":
    asyncio.run(main())
```

```text
HTTPStatusError: the tool failed (details withheld)
```

The trace kept only the type: an exception that is not an `AgentToolError` has
its text withheld from the step, because the trace is what the HTTP router, the
SSE stream and the sinks expose. The **model**, though, reads the full text.
Measured with a backend that keeps the messages, the observation it received
was:

```text
HTTPStatusError: Server error '503 Service Unavailable' for url 'https://cep.example.com/cep/64600000?apikey=s3cr3t'
For more information check: https://developer.mozilla.org/en-US/docs/Web/HTTP/Status/503
```

The API key, inside the conversation the model reads — and it can repeat
what it reads. With
the `AgentToolError` from `cep_tool.py`, the model reads only what you wrote and
the trace records the same sentence. `Agent(..., expose_tool_errors=True)`
writes the full text to the trace as well; it is for development, not for an
open endpoint. The whole mechanism is in
[A failing tool does not end the run](agents.md#a-failing-tool-does-not-end-the-run).

### An injected client: `typed_tool` over a bound method

`@tool` decorates a module function, so the tool above reads the client from a
global. To test with another transport without touching a global, the client
becomes a class parameter and the tool is the **bound method** — which the
decorator cannot reach, but `typed_tool` can, taking the arguments model
explicitly:

```python title="cep_service.py"
import httpx

from tempest_fastapi_sdk.agents import AgentContext, AgentToolError

from cep_tool import CepArgs


class CepService:
    """The CEP lookup, around an HTTP client it receives instead of creating."""

    def __init__(self, client: httpx.AsyncClient) -> None:
        """Store the client.

        Args:
            client (httpx.AsyncClient): The process-wide client in production,
                one over ``httpx.MockTransport`` in a test.
        """
        self.client: httpx.AsyncClient = client

    async def lookup(self, args: CepArgs, context: AgentContext) -> str:
        """Call the CEP service and keep only what the model needs.

        Args:
            args (CepArgs): The validated CEP.
            context (AgentContext): The run context (unused here).

        Returns:
            str: One line with the city and the state.

        Raises:
            AgentToolError: For an unknown CEP, a slow upstream or an
                upstream error, with a message written for the model.
        """
        try:
            response = await self.client.get(f"/cep/{args.cep}")
        except httpx.TimeoutException as exc:
            raise AgentToolError(
                "the CEP service did not answer; try again later",
            ) from exc
        if response.status_code == 404:
            raise AgentToolError(f"CEP {args.cep} does not exist; check the digits")
        if response.is_error:
            raise AgentToolError(
                f"the CEP service failed (HTTP {response.status_code})",
            )
        data = response.json()
        return f"CEP {data['cep']}: {data['localidade']}/{data['uf']}"
```

```python title="test_cep_service.py" hl_lines="20 26 30"
import httpx
import pytest

from tempest_fastapi_sdk.agents import Agent, typed_tool
from tempest_fastapi_sdk.agents.testing import (
    ScriptedBackend,
    assert_completed,
    failed_steps,
    replies,
    replies_with_tool,
)

from cep_service import CepService
from cep_tool import CepArgs


@pytest.mark.asyncio
async def test_an_unknown_cep_reaches_the_model_as_a_readable_error() -> None:
    """A 404 upstream must become an error the model can act on."""
    transport = httpx.MockTransport(lambda request: httpx.Response(404))
    async with httpx.AsyncClient(
        base_url="https://cep.example.com",
        transport=transport,
    ) as client:
        service = CepService(client)
        lookup = typed_tool(
            "lookup_cep",
            "Look up the city and state of a Brazilian postal code (CEP).",
            CepArgs,
            service.lookup,
        )
        backend = ScriptedBackend(
            [
                replies_with_tool("lookup_cep", {"cep": "00000000"}),
                replies("That CEP does not exist."),
            ],
        )
        run = await Agent(backend, tools=[lookup]).run("Where is CEP 00000000?")

    assert_completed(run)
    assert failed_steps(run)[0].error == (
        "AgentToolError: CEP 00000000 does not exist; check the digits"
    )
```

```bash
pytest test_cep_service.py -q
```

```text
.                                                                        [100%]
1 passed in 0.51s
```

`typed_tool("lookup_cep", ..., CepArgs, service.lookup)` produces the same
schema and the same validation as `@tool`: the handler receives an already
validated `CepArgs`. In production the `CepService` gets the process's client
and the tool is assembled where the agent is composed (`src/ai/agents/`, in the
[architecture](agents-architecture.md#agents-composition-only)).

## A tool that structures text

The other common case: a free-text message goes in, an object comes out. "Hi,
this is Ana from Picos, I need an electrician" becomes a lead with a name, a
city and an interest. The trick is that **there is no text parsing**: the
tool's arguments *are* the structure, and the model fills them in the way it
fills any tool's arguments.

### The schema the model sees: `schema_of`

```python title="lead_schema.py"
import json

from pydantic import Field

from tempest_fastapi_sdk.agents import schema_of
from tempest_fastapi_sdk.schemas import BaseSchema


class AddressSchema(BaseSchema):
    """Where the lead is."""

    city: str = Field(description="City.")
    state: str = Field(description="Two-letter state code, upper case.", pattern=r"^[A-Z]{2}$")


class LeadSchema(BaseSchema):
    """A sales lead read out of a free-text message."""

    name: str = Field(description="The person's name.")
    email: str | None = Field(default=None, description="Email, only if it appears in the text.")
    address: AddressSchema
    interests: list[str] = Field(
        default_factory=list,
        description="Services the person mentioned.",
    )


if __name__ == "__main__":
    print(sorted(LeadSchema.model_json_schema()))
    print(json.dumps(schema_of(LeadSchema), ensure_ascii=False, indent=2))
```

```text
['$defs', 'description', 'properties', 'required', 'title', 'type']
{
  "description": "A sales lead read out of a free-text message.",
  "properties": {
    "name": {
      "description": "The person's name.",
      "title": "Name",
      "type": "string"
    },
    "email": {
      "anyOf": [
        {
          "type": "string"
        },
        {
          "type": "null"
        }
      ],
      "default": null,
      "description": "Email, only if it appears in the text.",
      "title": "Email"
    },
    "address": {
      "description": "Where the lead is.",
      "properties": {
        "city": {
          "description": "City.",
          "title": "City",
          "type": "string"
        },
        "state": {
          "description": "Two-letter state code, upper case.",
          "pattern": "^[A-Z]{2}$",
          "title": "State",
          "type": "string"
        }
      },
      "required": [
        "city",
        "state"
      ],
      "title": "AddressSchema",
      "type": "object"
    },
    "interests": {
      "description": "Services the person mentioned.",
      "items": {
        "type": "string"
      },
      "title": "Interests",
      "type": "array"
    }
  },
  "required": [
    "name",
    "address"
  ],
  "type": "object"
}
```

The first line is Pydantic's raw JSON-schema: the nested `AddressSchema` sits
in `$defs`, referenced by `$ref`. `schema_of` — what `@tool`, `typed_tool` and
`final_answer_tool` use underneath — **inlines** the definition in place of the
reference and drops the top-level `title`; the result is a flat object, with no
`$defs`. The `AddressSchema` docstring became the `address` field's
`description`, and the `state` `pattern` went along: that is what the model
fills in from.

### Ending the run with the object: `final_answer_tool`

```python title="lead_extract.py" hl_lines="24 39 48"
import asyncio

from tempest_fastapi_sdk.agents import Agent, AgentContext, final_answer_tool
from tempest_fastapi_sdk.agents.testing import (
    ScriptedBackend,
    replies_with_tool,
    tool_steps,
)

from lead_schema import LeadSchema

MESSAGE: str = (
    "Hi, this is Ana Souza from Picos (PI). I need an electrician and a "
    "plumber this month. My email is ana@example.com."
)


async def main() -> None:
    """Extract a lead: one invalid answer, corrected on the next turn."""
    backend = ScriptedBackend(
        [
            replies_with_tool(
                "final_answer",
                {"name": "Ana Souza", "address": {"city": "Picos", "state": "Piauí"}},
            ),
            replies_with_tool(
                "final_answer",
                {
                    "name": "Ana Souza",
                    "email": "ana@example.com",
                    "address": {"city": "Picos", "state": "PI"},
                    "interests": ["electrician", "plumber"],
                },
            ),
        ],
    )
    agent = Agent(
        backend,
        tools=[final_answer_tool(LeadSchema)],
        system_prompt="Extract the contact from the message by calling final_answer.",
    )
    context = AgentContext()
    run = await agent.run(MESSAGE, context=context)

    for step in tool_steps(run):
        print(step.error or "ok")
    print(run.stop_reason, "| model calls:", backend.calls)
    print(repr(context.answer))


if __name__ == "__main__":
    asyncio.run(main())
```

```text
AgentToolError: invalid answer for final_answer: address.state: String should match pattern '^[A-Z]{2}$'
ok
completed | model calls: 2
LeadSchema(name='Ana Souza', email='ana@example.com', address=AddressSchema(city='Picos', state='PI'), interests=['electrician', 'plumber'])
```

* **The first answer did not validate** (`state` as `Piauí`, against the
  two-letter pattern). It became an observation, and the model corrected it on
  the next turn.
* **The valid call ended the run.** Two model calls, not three:
  `final_answer_tool` returns a `ToolResult` with `final=True`, and the agent
  stops right there, `completed`, without asking the model for a prose answer.
* **The validated object is in `context.answer`**; `run.output` is the same
  object as JSON.

!!! tip "`run_structured` does this for you"
    `agent.run_structured(goal, LeadSchema)` adds the `final_answer` tool,
    appends the instruction to finish through it to the prompt, and tries to
    recover the answer when the model replies in prose
    ([Structured output](agents-advanced.md#structured-output-an-object-not-a-paragraph)).
    Use `final_answer_tool` directly when you want to control the whole prompt,
    or when only one agent in a multi-agent flow reports structurally.

### When the object goes somewhere: `ToolResult(final=True)`

If extracting is only the means — the lead has to be **saved** — the
destination tool itself can end the run:

```python title="lead_save.py" hl_lines="34 40"
import asyncio

from tempest_fastapi_sdk.agents import Agent, AgentContext, ToolResult, tool
from tempest_fastapi_sdk.agents.testing import (
    ScriptedBackend,
    replies_with_tools,
    tool_call,
)

from lead_schema import LeadSchema

SAVED: list[LeadSchema] = []

LEAD: dict[str, object] = {
    "name": "Ana Souza",
    "address": {"city": "Picos", "state": "PI"},
    "interests": ["electrician"],
}


@tool("save_lead", "Save the contact found in the message. Call it once, at the end.")
async def save_lead(args: LeadSchema, context: AgentContext) -> ToolResult:
    """Store the validated lead and end the run with a receipt.

    Args:
        args (LeadSchema): The lead, already validated.
        context (AgentContext): The run context (unused here).

    Returns:
        ToolResult: The receipt, with ``final=True`` so no further model
        turn is spent.
    """
    SAVED.append(args)
    return ToolResult(text=f"lead {len(SAVED)} saved: {args.name}", final=True)


async def main() -> None:
    """The model asks for two saves in one turn; only the first runs."""
    backend = ScriptedBackend(
        [replies_with_tools(tool_call("save_lead", LEAD), tool_call("save_lead", LEAD))],
    )
    run = await Agent(backend, tools=[save_lead]).run("Hi, this is Ana from Picos.")

    print(run.stop_reason, "|", run.output)
    print("saved:", len(SAVED), "| model calls:", backend.calls)


if __name__ == "__main__":
    asyncio.run(main())
```

```text
completed | lead 1 saved: Ana Souza
saved: 1 | model calls: 1
```

The model asked for `save_lead` **twice** in the same turn. The first returned
`final=True`, the run ended there with its text as the answer, and the second
call did not run: one lead saved, one model call.

## `text_tool`: one text argument

For a tool that takes just a text, `text_tool` spares you the arguments model
and writes the schema for you:

```python title="word_count.py"
import json
from typing import Any

from tempest_fastapi_sdk.agents import AgentContext, AgentTool, ToolReturn, text_tool


async def count_words(arguments: dict[str, Any], context: AgentContext) -> ToolReturn:
    """Count the words of the text the model passed.

    Args:
        arguments (dict[str, Any]): The raw arguments, with ``text``.
        context (AgentContext): The run context (unused here).

    Returns:
        ToolReturn: The count, as one line.
    """
    return f"{len(str(arguments['text']).split())} words"


word_count: AgentTool = text_tool(
    "count_words",
    "Count the words in a piece of text.",
    count_words,
)

if __name__ == "__main__":
    print(json.dumps(word_count.to_spec()["function"]["parameters"], indent=2))
```

```text
{
  "type": "object",
  "properties": {
    "text": {
      "type": "string",
      "description": "The input text."
    }
  },
  "required": [
    "text"
  ]
}
```

The handler receives the raw `dict` (`arguments["text"]`), without validation:
that is the `AgentTool` shape. `ToolReturn` is the type of what a handler
returns — `ToolResult | str`; a `str` becomes `ToolResult(text=...)` on its
own. When the tool grows a second argument, `parameters=` takes a hand-written
schema, but by then `@tool` with a Pydantic model is the path that keeps schema
and handler from drifting apart.

| Builder | Arguments | The handler receives | Use it when |
| --- | --- | --- | --- |
| `@tool(name, description)` | Pydantic model in the annotation | validated instance | the function lives at module level |
| `typed_tool(name, description, Model, handler)` | explicit Pydantic model | validated instance | the handler is a bound method, a lambda or comes from elsewhere |
| `text_tool(name, description, handler)` | just `text` | raw `dict` | one text in, one text out |
| `AgentTool(...)` | hand-written JSON-schema | raw `dict` | the schema does not fit a Pydantic model |

## Recap

- **The database has its own page**: [AI agents (database)](agents-db.md).
- **A tool that calls an API** uses the process's `httpx.AsyncClient`, with a
  timeout **below** the budget's `max_seconds` — otherwise the budget does the
  cutting and the model never reads the error.
- **4xx, 5xx and timeouts become `AgentToolError`** with a sentence that says
  what to do. A raw exception has its text withheld from the trace, but reaches
  the model in full — with the URL and whatever is in it.
- **Return little**: ten API fields become one line.
- **`typed_tool` over a bound method** keeps the client injectable, and the
  test uses `httpx.MockTransport`.
- **Structuring text is not parsing**: the tool's arguments are the object.
  `schema_of` flattens the nested schema; `final_answer_tool` ends the run with
  the validated object in `context.answer`.
- **`ToolResult(final=True)`** ends the run in a tool of your own, with no extra
  model turn.
- **`text_tool`** for a single text; `@tool` as soon as there are more
  arguments.

See also: [AI agents (prompts)](agents-prompts.md) for each tool's *when*, which
lives in the prompt; [AI agents (testing)](agents-testing.md) to assert on what
the tool did; [HTTP client (outbound)](http-client.md) for retries and the
circuit-breaker.
