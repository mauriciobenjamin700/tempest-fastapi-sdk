# Hosted generative AI (Anthropic, OpenAI, DeepSeek, Google)

The SDK's AI base runs local models — `TextGenerator` (transformers) or
`OllamaGenerator` (daemon). Sometimes the number that matters is not RAM but
**cost per token**, or you need a model that does not fit on your machine.
That is what the hosted backends are for:

| Provider | Backend | Generation + stream | Tool calling | Structured output | Embeddings |
| --- | --- | --- | --- | --- | --- |
| Anthropic | `AnthropicGenerator` | yes | yes | yes (`json_schema`) | — |
| OpenAI | `OpenAICompatGenerator` | yes | yes | yes (`json_schema`) | `OpenAICompatEmbedder` |
| DeepSeek | `OpenAICompatGenerator` | yes | yes | yes (`json_object`) | — |
| Google (Gemini) | `OpenAICompatGenerator` | yes, through the compatible endpoint (untested) | yes, likewise | yes (`json_schema`) | — |

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
OPENAI_STT_MODEL=<transcription model id>
DEEPSEEK_API_KEY=sk-...
GOOGLE_API_KEY=AIza...
GOOGLE_MODEL=<Gemini model id>
```

Each provider has `*_API_KEY`, `*_BASE_URL` and `*_MODEL`. The defaults:

| Variable | Default |
| --- | --- |
| `ANTHROPIC_BASE_URL` | `https://api.anthropic.com` |
| `ANTHROPIC_MODEL` | `claude-opus-5-5` |
| `OPENAI_BASE_URL` | `https://api.openai.com/v1` |
| `OPENAI_MODEL` | empty — required |
| `OPENAI_STT_MODEL` | empty — required by `openai_speech_kwargs()` |
| `DEEPSEEK_BASE_URL` | `https://api.deepseek.com` |
| `DEEPSEEK_MODEL` | `deepseek-flash` |
| `GOOGLE_BASE_URL` | `https://generativelanguage.googleapis.com/v1beta/openai` |
| `GOOGLE_MODEL` | empty — required by `google_kwargs()` |

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

## Full outcome, images and the provider from the model id

`chat_structured` answers one question — "give me the instance" — and raises
on any other outcome. Whoever pays for each call needs three more answers,
**even when there is no instance**: what the call cost, why the model stopped
and what it wrote. A refusal, a reply cut at `max_tokens` or JSON that fails
the schema were billed too.

`complete_structured` returns all of it in a `StructuredCompletion`, and
`hosted_generator` picks the provider from the model id:

```python
import asyncio
from pathlib import Path

from pydantic import BaseModel

from tempest_fastapi_sdk import BaseAppSettings, HostedGenAISettings
from tempest_fastapi_sdk.genai import (
    StructuredCompletion,
    StructuredCompletionBackend,
    hosted_generator,
    image_part,
)


class Settings(HostedGenAISettings, BaseAppSettings):
    """Service settings."""

    AI_MODEL: str = "claude-haiku-5-5"


class Transcript(BaseModel):
    """The text read from the photo."""

    readable: bool
    text: str


async def transcribe(
    backend: StructuredCompletionBackend,
    photo: bytes,
) -> StructuredCompletion[Transcript]:
    """Read the text of a notebook photo."""
    return await backend.complete_structured(
        [
            {"role": "system", "content": "Transcribe the text in the photo."},
            {
                "role": "user",
                "content": [
                    image_part(photo, "image/jpeg"),
                    {"type": "text", "text": "Transcribe it."},
                ],
            },
        ],
        Transcript,
        effort="low",
        cache_system=True,
        max_tokens=2048,
    )


async def main() -> None:
    """Transcribe with the configured model."""
    settings = Settings()
    backend = hosted_generator(**settings.hosted_kwargs(settings.AI_MODEL))
    result = await transcribe(backend, Path("notebook.jpg").read_bytes())
    if result.output is not None:
        print(result.output.text)
    else:
        print("no result:", result.stop_reason, result.error)
    print("tokens:", result.usage)


asyncio.run(main())
```

Piece by piece:

- **`hosted_generator(model, api_key=...)`** reads the provider off the id
  prefix (`claude-` → Anthropic, `gpt-`/`o1`/`o3`/`o4`/`chatgpt-` → OpenAI,
  `gemini-` → Google, `deepseek-` → DeepSeek; the table is `MODEL_PREFIXES`)
  and builds the backend with what each provider documents: the field the
  output budget goes to (`max_completion_tokens` on OpenAI), the JSON mode
  (`json_object` on DeepSeek) and the effort values. An id outside the table
  (a fine-tune, your own server) raises `ValueError`: pass `provider=` and,
  if needed, `base_url=`.
- **`settings.hosted_kwargs(model)`** returns exactly the arguments of
  `hosted_generator` — the model, and the key and URL **of the resolved
  provider**. Changing provider becomes changing `AI_MODEL` in `.env` (and
  having that provider's key there).
- **`image_part(data, media_type)`** builds the image part in the OpenAI
  shape, which is the SDK's message shape. `AnthropicGenerator` translates it
  to a Messages API `image` block; `OpenAICompatGenerator` forwards it as is.
- **`effort`** goes out as `output_config.effort` on Anthropic and as
  `reasoning_effort` on the OpenAI format. Gemini documents `minimal`, `low`,
  `medium` and `high` (<https://ai.google.dev/gemini-api/docs/openai>), so
  `xhigh` and `max` become `high` (`GOOGLE_EFFORT_VALUES`). DeepSeek does not
  document the field, and neither `hosted_generator` nor `deepseek_kwargs()`
  sends it (`effort_field=None`).
- **`cache_system=True`** marks the `system` turn with
  `cache_control: {"type": "ephemeral"}` on Anthropic. The OpenAI format has
  no per-message marker, and the argument is accepted and ignored.

The `StructuredCompletion` you get back:

| Field | What it is |
| --- | --- |
| `output` | the validated instance, or `None` |
| `text` | the reply as received |
| `usage` | the provider's `TokenUsage`, **whatever the outcome**; `None` when the response carries no `usage` |
| `stop_reason` | `end`, `max_tokens`, `refusal`, `tool_use` or `other` |
| `provider_stop_reason` | the original value (`end_turn`, `length`...) |
| `error` | why `output` is `None` after a natural stop |

!!! tip "Only a natural stop is parsed"

    A reply cut at `max_tokens` can validate by accident when the schema's
    last fields have defaults. So parsing only happens with
    `stop_reason == "end"`; otherwise `output` is `None` and `error` stays
    empty — `stop_reason` already says why.

!!! note "Cache written and cache read"

    `TokenUsage.cache_write_tokens` is the slice of the input written to the
    cache (Anthropic's `cache_creation_input_tokens`), which Anthropic bills
    above the base rate; `cache_hit_tokens` is the slice read. Both are
    already inside `input_tokens`. The OpenAI format reports no write, and
    the field stays `0`.

The tests for this surface run over `httpx.MockTransport`: they pin the
request each backend builds and the reading of the documented response, not
the behavior of a live provider.

## One provider chosen in `.env`

Reading the provider off the model id prefix is convenient, but it hides a
business decision inside a model name, and each provider wants its own key
variable. When the service sends **every** model to **one** provider, say so
in `.env`, with the provider, the key and (optionally) the URL:

```bash
# .env
GENAI_PROVIDER=openai
GENAI_API_KEY=sk-...
# GENAI_BASE_URL=https://internal-gateway/v1   # empty uses the provider URL
AI_MODEL_FREE=gpt-5-mini
AI_MODEL_FAMILY=gpt-5
```

```python
from tempest_fastapi_sdk import BaseAppSettings, HostedGenAISettings
from tempest_fastapi_sdk.genai import TextBackend, hosted_generator


class Settings(HostedGenAISettings, BaseAppSettings):
    """Service settings: both models come from the same provider."""

    AI_MODEL_FREE: str = "gpt-5-mini"
    AI_MODEL_FAMILY: str = "gpt-5"


def plan_backend(settings: Settings, family: bool) -> TextBackend:
    """Build the plan's generator with the provider from `.env`."""
    model = settings.AI_MODEL_FAMILY if family else settings.AI_MODEL_FREE
    return hosted_generator(**settings.hosted_kwargs(model))


settings = Settings(GENAI_PROVIDER="openai", GENAI_API_KEY="sk-...")
print(settings.hosted_kwargs(settings.AI_MODEL_FREE)["provider"])  # openai
```

With `GENAI_PROVIDER` set, `hosted_kwargs` changes rule:

- **The provider is the one in `.env`**, for every model. An id with another
  provider's prefix (`claude-...` with `GENAI_PROVIDER=openai`) raises
  `ValueError` instead of switching provider silently; an id with no known
  prefix (a fine-tune, a model behind an OpenAI-compatible server) goes to
  the chosen provider.
- **The key is `GENAI_API_KEY`**; empty, it falls back to the provider's own
  key (`OPENAI_API_KEY`...). The URL is `GENAI_BASE_URL` or, when empty, the
  provider's.
- **A `provider=`** that disagrees with `.env` raises `ValueError`: one
  service talks to one provider.
- **Without `GENAI_PROVIDER`** (absent or empty), nothing changes: the
  provider comes from the prefix, as in the section above.

!!! check "The production boot demands the key"

    With `EnvironmentSettings` composed and `ENV=production`, a
    `GENAI_PROVIDER` with no effective key (neither `GENAI_API_KEY` nor the
    provider's own) refuses the boot, listing `GENAI_API_KEY`. A service that
    never sets `GENAI_PROVIDER` is not affected: the per-provider keys stay
    optional.

## PDFs: `document_part` and `supports_documents`

You already send images with `image_part`. PDFs have the same problem, plus
one: each provider spells the document part its own way, and not every
provider takes one. Writing the block by hand (Anthropic's `document`, for
instance) ties the service to one provider — changing `GENAI_PROVIDER` then
breaks only the PDF flow, and only in production.

`document_part` builds the part once, and each generator translates it or
refuses it:

```python
import asyncio
from pathlib import Path

from pydantic import BaseModel

from tempest_fastapi_sdk import BaseAppSettings, HostedGenAISettings
from tempest_fastapi_sdk.genai import (
    StructuredCompletion,
    StructuredCompletionBackend,
    document_part,
    hosted_generator,
    supports_documents,
)


class Settings(HostedGenAISettings, BaseAppSettings):
    """The service settings."""

    AI_MODEL: str = "claude-haiku-5-5"


class Agenda(BaseModel):
    """The events read from the notice."""

    events: list[str]


async def read_agenda(
    backend: StructuredCompletionBackend,
    pdf: bytes,
) -> StructuredCompletion[Agenda]:
    """Extract the events from a PDF notice."""
    return await backend.complete_structured(
        [
            {
                "role": "user",
                "content": [
                    document_part(pdf, filename="notice.pdf"),
                    {"type": "text", "text": "List the events."},
                ],
            },
        ],
        Agenda,
        max_tokens=2048,
    )


async def main() -> None:
    """Check the provider at boot and read the PDF."""
    settings = Settings()
    kwargs = settings.hosted_kwargs(settings.AI_MODEL)
    if not supports_documents(kwargs["provider"]):
        raise SystemExit(f"{kwargs['provider']} documents no PDF input")
    backend = hosted_generator(**kwargs)
    result = await read_agenda(backend, Path("notice.pdf").read_bytes())
    print(result.output)


asyncio.run(main())
```

Piece by piece:

- **`document_part(data, media_type="application/pdf", *, filename=None)`**
  builds OpenAI's Chat Completions `file` part: `file_data` is a base64 data
  URL and `filename` goes with it (`document.pdf` when you pass none, because
  every base64 example in OpenAI's guide sends a name). Only
  `application/pdf` is accepted; another type, or empty bytes, raises
  `ValueError` right away. Plain text goes in a `text` part.
- **Anthropic:** `AnthropicGenerator` translates the part into a `document`
  block with a base64 `source`, and `filename` becomes the block's `title`.
- **OpenAI:** `OpenAICompatGenerator` forwards the part as is.
- **Google and DeepSeek:** the generator `hosted_generator` builds for them
  (and the one `google_kwargs()` / `deepseek_kwargs()` build) raises
  `ValueError` **before** opening the connection when a message carries the
  part, with a message naming the provider. Text and images still go out
  normally.
- **`supports_documents(provider)`** answers the same question without
  calling anything: `True` for `anthropic` and `openai`, `False` for
  `google`, `deepseek`, `None` and an unknown name. It is what the service
  checks at boot, or before offering the PDF route. The list is
  `DOCUMENT_PROVIDERS`.

Where each row came from, read on 2026-11-04:

| Provider | What the documentation says | SDK result |
| --- | --- | --- |
| Anthropic | `document` block with a base64 `application/pdf` `source`, according to Anthropic's documentation (<https://platform.claude.com/docs/en/build-with-claude/pdf-support>) | translates |
| OpenAI | "Chat Completions accepts only PDF files as `file` content parts", with `file_data` as a data URL, according to OpenAI's documentation (<https://developers.openai.com/api/docs/guides/pdf-files>) | forwards |
| Google | the compatible-endpoint page (<https://ai.google.dev/gemini-api/docs/openai>) documents image and audio, and no `file` part | `ValueError` |
| DeepSeek | the chat reference (<https://api-docs.deepseek.com/api/create-chat-completion>) lists a `file` part, but describes `file_data` as a data URL "of the image" and names no PDF | `ValueError` |

!!! warning "The request was tested, not the answer"

    The tests for this part run on `httpx.MockTransport`: they pin the body
    each generator builds and the `ValueError` with no request sent. No
    provider was actually called — that Anthropic and OpenAI read the PDF is
    what their documentation says, not something the SDK measured. If Google
    or DeepSeek start documenting PDFs, the change is one line in
    `DOCUMENT_PROVIDERS`.

!!! tip "Generic compatible server"

    An `OpenAICompatGenerator` built by hand (vLLM, your own gateway)
    forwards the part by default (`accepts_documents=True`): the SDK does not
    know what that server accepts, and the server answers for it. Pass
    `accepts_documents=False` to refuse before the call.

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

## Remote transcription (speech-to-text)

`SpeechToText` runs Whisper **in your process**: it downloads weights, holds
RAM (or GPU) and competes for CPU with the rest of the service. When a server
that transcribes already exists — `tempest-ai`, OpenAI itself, a `vLLM`
serving Whisper — `OpenAICompatSpeech` sends the audio to it over the OpenAI
endpoint `POST {base_url}/audio/transcriptions` and returns the same
`Transcription`.

First, the settings. The key and the root are the ones `openai_kwargs()`
uses (`OPENAI_API_KEY`, `OPENAI_BASE_URL`); only the model changes, in
`OPENAI_STT_MODEL`:

```bash
# .env
OPENAI_API_KEY=<tempest-ai API key>
OPENAI_BASE_URL=https://ai.buscar.app.br/api/openai/v1
OPENAI_STT_MODEL=<id that GET /models lists>
```

Then, a service that receives the upload and transcribes it:

```python
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import FastAPI, UploadFile

from tempest_fastapi_sdk import (
    BaseAppSettings,
    HostedGenAISettings,
    register_exception_handlers,
)
from tempest_fastapi_sdk.genai import OpenAICompatSpeech
from tempest_fastapi_sdk.genai.audio import Transcription


class Settings(HostedGenAISettings, BaseAppSettings):
    """Service settings."""


settings: Settings = Settings()
speech: OpenAICompatSpeech = OpenAICompatSpeech(**settings.openai_speech_kwargs())


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    """Close the backend's HTTP client on shutdown."""
    yield
    await speech.aclose()


app: FastAPI = FastAPI(lifespan=lifespan)
register_exception_handlers(app)


@app.post("/transcribe")
async def transcribe(file: UploadFile) -> Transcription:
    """Transcribe the uploaded Portuguese audio."""
    audio: bytes = await file.read()
    return await speech.transcribe(audio, language="pt", with_segments=False)
```

Piece by piece:

- `openai_speech_kwargs()` returns `model`, `api_key`, `base_url` and
  `provider="openai"` — the splat builds the backend. With `OPENAI_STT_MODEL`
  empty it raises `ValueError` at boot, as `openai_kwargs()` does for
  `OPENAI_MODEL`.
- `base_url` **includes** the version (`.../v1`), as with
  `OpenAICompatGenerator`; the backend appends `/audio/transcriptions`. For
  `tempest-ai`, pass `https://ai.buscar.app.br/api/openai/v1`.
- The request is multipart with `file`, `model`, `language` (only when you
  pass one) and `response_format=verbose_json`, the format that carries
  `duration`, `language` and `segments`. The key goes in
  `Authorization: Bearer`.
- `language` takes the ISO-639-1 code (`"pt"`) or a `LanguageEnum`
  (`LanguageEnum.PT_BR` becomes `"pt"`); `None` lets the server detect it.
- `with_segments=False` returns `segments=[]`; `duration` and `language`
  are still filled.
- `audio` takes `bytes` or a local path (`str` or `Path`). A string is always
  a path, never a URL: the backend fetches nothing off the machine.

!!! tip "Drop-in for `SpeechToText`"

    `OpenAICompatSpeech.transcribe` takes the same `language` and
    `with_segments` as `SpeechToText` and satisfies the `SpeechTranscriber`
    protocol. Code that does
    `await transcriber.transcribe(audio, language="pt", with_segments=False)`
    does not change; and `make_genai_router(speech_to_text=speech)` serves
    `/transcribe` with it.

Server errors become exceptions that `register_exception_handlers` already
answers:

| Server status | Exception | What your client gets |
| --- | --- | --- |
| 401, 403 | `HostedAuthenticationError` | 500 — a wrong key is a configuration defect |
| 413 | `FileTooLargeException` (server's message) | 413 |
| 400, 422 | `ValidationException` (server's message) | 422 |
| 429 | `HostedRateLimitError` | 500 |
| 5xx | `HostedServerError` | 500 |

On `413`/`400`/`422`, `details` carries `backend`, `model`, `status` and the
`server_code` the server sent (`AUDIO_TOO_LARGE`, `INVALID_AUDIO`).

!!! warning "Speech synthesis is left out"

    `/audio/speech` uses another model (the TTS one), and
    `OpenAICompatSpeech` holds a single model. For speech, use
    `TextToSpeech` or `OllamaSpeech`.

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
  `deepseek_kwargs()` / `google_kwargs()` build the backend by splat.
- `GENAI_PROVIDER` + `GENAI_API_KEY` (+ `GENAI_BASE_URL`) pin one provider
  for every model from `.env`; another provider's model raises.
- `hosted_generator(**settings.hosted_kwargs(model))` picks the provider
  from the model id; `complete_structured` returns output, usage and stop
  reason whatever the outcome (usage, when the provider reports it), with
  images through `image_part`.
- PDFs go through `document_part`: a `document` block on Anthropic, a `file`
  part on OpenAI, `ValueError` before the call on Google and DeepSeek;
  `supports_documents(provider)` answers at boot.
- Tool calling, structured output and streaming work on all four (on Gemini,
  tool calling and streaming go through the compatible endpoint without a
  test of their own); embeddings only on OpenAI (`OpenAICompatEmbedder`).
- `OpenAICompatSpeech(**settings.openai_speech_kwargs())` transcribes on a
  remote server (`/audio/transcriptions`) and replaces `SpeechToText` without
  changing the call; `413` becomes `FileTooLargeException` and `400`/`422`
  become `ValidationException`.
- Failures become `HostedAuthenticationError`, `HostedRateLimitError` or
  `HostedServerError` — mid-stream included.
