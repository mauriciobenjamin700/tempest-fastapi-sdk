# Hosted generative AI (Anthropic, OpenAI, DeepSeek)

The SDK's AI base runs local models — `TextGenerator` (transformers) or
`OllamaGenerator` (daemon). Sometimes the number that matters is not RAM but
**cost per token**, or you need a model that does not fit on your machine.
That is what the hosted backends are for:

| Provider | Backend | Generation + stream | Tool calling | Structured output | Embeddings |
| --- | --- | --- | --- | --- | --- |
| Anthropic | `AnthropicGenerator` | yes | yes | yes (`json_schema`) | — |
| OpenAI | `OpenAICompatGenerator` | yes | yes | yes (`json_schema`) | `OpenAICompatEmbedder` |
| DeepSeek | `OpenAICompatGenerator` | yes | yes | yes (`json_object`) | — |

The idea is a single one: they implement **the same protocols** as Ollama —
`TextBackend` (`generate` / `chat` / `stream`), `StructuredTextBackend`
(`chat_structured`), the agent's `ToolCallingBackend` (`chat_with_tools`) and
RAG's `SupportsEmbed`. You change the line that builds the backend and the
rest of the service stays as it is.

!!! info "Installation"

    ```bash
    uv add "tempest-fastapi-sdk[genai-hosted]"
    ```

    The extra only pulls `httpx`, which is already a base dependency — it
    exists so the service declares the capability it uses. No vendor SDK
    (`anthropic`, `openai`) is installed.

## First step: the keys in settings

Compose the `HostedGenAISettings` mixin into the service settings:

```python
from tempest_fastapi_sdk import BaseAppSettings, HostedGenAISettings


class Settings(HostedGenAISettings, BaseAppSettings):
    """Service settings."""


settings: Settings = Settings()
```

```bash
# .env
ANTHROPIC_API_KEY=sk-ant-...
OPENAI_API_KEY=sk-...
OPENAI_MODEL=<OpenAI model id>
DEEPSEEK_API_KEY=sk-...
```

Each provider has `*_API_KEY`, `*_BASE_URL` and `*_MODEL`. The defaults:

| Variable | Default |
| --- | --- |
| `ANTHROPIC_BASE_URL` | `https://api.anthropic.com` |
| `ANTHROPIC_MODEL` | `claude-opus-5-5` |
| `OPENAI_BASE_URL` | `https://api.openai.com/v1` |
| `OPENAI_MODEL` | empty — required |
| `DEEPSEEK_BASE_URL` | `https://api.deepseek.com` |
| `DEEPSEEK_MODEL` | `deepseek-flash` |

!!! note "Where the defaults come from"

    `claude-opus-5-5` is the model Anthropic's models table recommends as the
    starting point (<https://platform.claude.com/docs/en/models/overview>).
    `deepseek-flash` is one of the two values DeepSeek's reference accepts
    in the `model` field
    (<https://api-docs.deepseek.com/api/create-chat-completion>). For OpenAI
    the SDK pins no id: `openai_kwargs()` raises `ValueError` while
    `OPENAI_MODEL` is empty.

## Generating text

The `*_kwargs()` methods return exactly what the constructor takes, so
building the backend is a splat:

```python
import asyncio

from tempest_fastapi_sdk import BaseAppSettings, HostedGenAISettings
from tempest_fastapi_sdk.genai import AnthropicGenerator, OpenAICompatGenerator


class Settings(HostedGenAISettings, BaseAppSettings):
    """Service settings."""


settings: Settings = Settings(ANTHROPIC_API_KEY="sk-ant-...", DEEPSEEK_API_KEY="sk-...")

claude: AnthropicGenerator = AnthropicGenerator(**settings.anthropic_kwargs())
deepseek: OpenAICompatGenerator = OpenAICompatGenerator(**settings.deepseek_kwargs())


async def main() -> None:
    """Ask both providers the same thing."""
    print(await claude.generate("Explain PIX in one sentence."))
    print(await deepseek.generate("Explain PIX in one sentence."))
    async for piece in claude.stream("Count to five."):
        print(piece, end="", flush=True)


asyncio.run(main())
```

Piece by piece:

- `AnthropicGenerator` speaks the Messages API: `POST {base_url}/v1/messages`
  with the `x-api-key` and `anthropic-version` headers
  (<https://platform.claude.com/docs/en/api/overview>). `system` turns become
  the top-level `system` field, which is where that API reads them.
- `OpenAICompatGenerator` speaks `/chat/completions`. `deepseek_kwargs()`
  points it at `https://api.deepseek.com`; the format is the same, according
  to <https://api-docs.deepseek.com/>.
- `stream()` reads each provider's SSE events and yields only the text.

An empty key raises `ValueError` **at construction**, not a 401 from inside
the first background job.

!!! warning "`max_tokens` on Anthropic"

    The Messages API requires `max_tokens` on every call
    (<https://platform.claude.com/docs/en/api/messages>).
    `AnthropicGenerator` sends `16000` when the call sets no
    `max_new_tokens`; change it with `max_tokens=` on the constructor or with
    `GenerationConfig(max_new_tokens=...)`.

## The same code, any backend

This is where the swap becomes free. Write the consumer against the
protocol, not against the provider:

```python
import asyncio

from tempest_fastapi_sdk.genai import (
    AnthropicGenerator,
    OllamaGenerator,
    OpenAICompatGenerator,
    TextBackend,
)


async def summarize(backend: TextBackend, text: str) -> str:
    """Summarize a text with any backend."""
    return await backend.chat(
        [
            {"role": "system", "content": "Summarize in one sentence."},
            {"role": "user", "content": text},
        ],
    )


async def main() -> None:
    """Run the same summary on three backends."""
    backends: list[TextBackend] = [
        OllamaGenerator("llama3.2"),
        AnthropicGenerator(api_key="sk-ant-..."),
        OpenAICompatGenerator(
            "deepseek-flash",
            api_key="sk-...",
            base_url="https://api.deepseek.com",
        ),
    ]
    for backend in backends:
        print(await summarize(backend, "PIX is Brazil's instant payment system."))


asyncio.run(main())
```

`make_genai_router`, `AIChatPipeline` and `Agent` take the backend the same
way — pass the hosted one wherever you would pass `OllamaGenerator`.

## Agent with tools

`Agent` calls `chat_with_tools(messages, tools)` and reads `tool_calls` in the
OpenAI/Ollama shape. `AnthropicGenerator` translates both ways: tools go out
as `{name, description, input_schema}`, `tool_use` blocks come back as
`tool_calls` (keeping the `id`), and `role: tool` results go back to the API
as `tool_result` blocks in a single `user` turn, as the tool-use guide asks
(<https://platform.claude.com/docs/en/agents-and-tools/tool-use/overview>).

```python
import asyncio
from typing import Any

from tempest_fastapi_sdk.agents import Agent, AgentContext, AgentTool
from tempest_fastapi_sdk.genai import AnthropicGenerator


async def add(arguments: dict[str, Any], context: AgentContext) -> str:
    """Add two integers."""
    return str(int(arguments["a"]) + int(arguments["b"]))


add_tool: AgentTool = AgentTool(
    name="add",
    description="Add two integers.",
    parameters={
        "type": "object",
        "properties": {"a": {"type": "integer"}, "b": {"type": "integer"}},
        "required": ["a", "b"],
    },
    handler=add,
)


async def main() -> None:
    """Run an agent on Claude."""
    agent: Agent = Agent(AnthropicGenerator(api_key="sk-ant-..."), tools=[add_tool])
    run = await agent.run("What is 2 + 3?")
    print(run.stop_reason, run.output)


asyncio.run(main())
```

Swapping `AnthropicGenerator(...)` for `OpenAICompatGenerator(...)` or
`OllamaGenerator(...)` changes nothing else in the example.

## Structured output

`chat_structured(messages, schema)` returns a validated instance of your
Pydantic model:

```python
import asyncio

from pydantic import BaseModel

from tempest_fastapi_sdk.genai import AnthropicGenerator, StructuredTextBackend


class Invoice(BaseModel):
    """Fields to extract."""

    number: str
    total_cents: int


async def read(backend: StructuredTextBackend, document: str) -> Invoice:
    """Extract the invoice from a document."""
    return await backend.chat_structured(
        [
            {"role": "system", "content": "Extract the invoice fields."},
            {"role": "user", "content": document},
        ],
        Invoice,
    )


async def main() -> None:
    """Extract with Claude."""
    print(await read(AnthropicGenerator(api_key="sk-ant-..."), "Invoice A-1, $5.00"))


asyncio.run(main())
```

Each provider asks for JSON differently, and the backend handles it:

- **Anthropic** — `output_config.format` with `type: json_schema`. The API
  requires `additionalProperties: false` on every object and rejects numeric
  and string-length constraints
  (<https://platform.claude.com/docs/en/build-with-claude/structured-outputs>);
  the schema sent is adapted, and the reply is validated against **your**
  model, original constraints included.
- **OpenAI** — `response_format` with `type: json_schema`.
- **DeepSeek** — `response_format` with `type: json_object`, the only JSON
  mode documented (<https://api-docs.deepseek.com/guides/json_mode>); the
  schema goes in a `system` turn at the end of the conversation, which also
  meets the requirement of the word "json" in the prompt.

## Embeddings (OpenAI)

`OpenAICompatEmbedder` implements `SupportsEmbed`, so it goes into a
`Retriever` in place of `OllamaEmbedder`:

```python
import asyncio

from tempest_fastapi_sdk.genai import OpenAICompatEmbedder


async def main() -> None:
    """Produce two vectors."""
    embedder: OpenAICompatEmbedder = OpenAICompatEmbedder(
        "text-embedding-3-small",
        api_key="sk-...",
    )
    vectors: list[list[float]] = await embedder.embed(["hello", "world"])
    print(len(vectors))


asyncio.run(main())
```

Vectors come back in input order, sorted by the response's `index`.

!!! note "Anthropic and DeepSeek have no embeddings"

    Anthropic offers no embedding endpoint, and the navigation of DeepSeek's
    reference lists Chat Completions, Responses, FIM, models, balance and
    files — no embeddings. For RAG with those providers, produce the vectors
    with `OpenAICompatEmbedder`, `OllamaEmbedder` or `Embedder`.

## When the provider fails

Both backends raise the same exception family:

| Exception | Status | What to do |
| --- | --- | --- |
| `HostedAuthenticationError` | 401, 403 | Fix the key — retrying will not help |
| `HostedRateLimitError` | 429 | Wait `retry_after` seconds (when the header is sent) |
| `HostedServerError` | 5xx, including Anthropic's 529 | Try later, or fall back to another backend |
| `HostedAPIError` | anything else (400, 402, 422...) | Fix the request |

All inherit from `HostedAPIError`, which inherits from
`httpx.HTTPStatusError` — code that already caught that exception from
`OpenAICompatGenerator` keeps catching it.

```python
import asyncio

from tempest_fastapi_sdk.genai import (
    AnthropicGenerator,
    HostedRateLimitError,
    HostedServerError,
    OllamaGenerator,
)


async def answer(question: str) -> str:
    """Use Claude and fall back to local Ollama when it is down."""
    claude: AnthropicGenerator = AnthropicGenerator(api_key="sk-ant-...")
    try:
        return await claude.generate(question)
    except HostedRateLimitError as error:
        print(f"rate limited; retry in {error.retry_after}s")
        raise
    except HostedServerError:
        return await OllamaGenerator("llama3.2").generate(question)


print(asyncio.run(answer("Explain PIX.")))
```

Each exception carries `provider`, `status_code`, `error_type` and `detail`
(from the provider's error body) and `request_id` (Anthropic's `request-id`
header). On Anthropic, an error can also arrive **after** the `200`, in the
middle of the stream (`event: error`,
<https://platform.claude.com/docs/en/build-with-claude/streaming>):
`stream()` raises the same typed exception — an `overloaded_error` becomes a
`HostedServerError` with `status_code` 529 — and the text already yielded
stays yielded.

!!! tip "Retry is built in"

    The SDK's HTTP client retries `429`, `500`, `502`, `503` and `504` with exponential backoff —
    on `AnthropicGenerator`, `529` too. What it does **not** retry is a read
    timeout: the provider aborts the generation when the client gives up, and
    the retry would start it again from zero.

## Why not the official SDKs

`anthropic` and `openai` are HTTP wrappers. At their current versions
(`anthropic` 1.15.0, `openai` 3.31.0), both require `httpx2` — a second HTTP
stack that the SDK's `HTTPClient` and the tests' `httpx.MockTransport` do not
reach — plus `jiter`, `anyio` and `sniffio` with upper bounds. What the SDK
uses from them is one POST and an SSE reader. Building on the `httpx` that is
already a dependency keeps retry, circuit-breaker and `X-Request-ID` the same
as Ollama's, and puts no upper bound in any consumer's lock.

## Recap

- `AnthropicGenerator` and `OpenAICompatGenerator` implement the same
  protocols as `OllamaGenerator`: change the construction, keep the rest.
- `HostedGenAISettings` + `anthropic_kwargs()` / `openai_kwargs()` /
  `deepseek_kwargs()` build the backend by splat.
- Tool calling, structured output and streaming work on all three;
  embeddings only on OpenAI (`OpenAICompatEmbedder`).
- Failures become `HostedAuthenticationError`, `HostedRateLimitError` or
  `HostedServerError` — mid-stream included.
