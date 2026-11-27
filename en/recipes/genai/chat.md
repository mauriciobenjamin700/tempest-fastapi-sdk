# AI chat pipeline

Use to assemble the full chat (history, memory, tools) with `AIChatPipeline`, the ready `make_ai_chat_router` endpoint and streaming.

Part of the [Self-hosted generative AI](https://mauriciobenjamin700.github.io/tempest-fastapi-sdk/en/recipes/genai.md) recipe.

Here the earlier slices click together. Building a "real" chatbot — memory,
web RAG, tool-calling, optional TTS — usually means writing (and
maintaining) an entire inference microservice. `AIChatPipeline` does it
**inside your process**: you inject the pieces you've already seen
(`OllamaGenerator`, `ChatMemory`, `WebSearch`, `Tool`s) and call `respond`.

!!! tip "Picking the chat's voice model"
    TTS weight licences vary more than they look (F5-TTS and AudioLDM 2 are
    non-commercial). Measured table and sources in
    [Picking a voice or audio model](https://mauriciobenjamin700.github.io/tempest-fastapi-sdk/en/recipes/genai/audio.md#picking-a-voice-or-audio-model-licence-and-where-it-runs).

```python
import asyncio

from tempest_fastapi_sdk import HTTPClient
from tempest_fastapi_sdk.genai import (
    AIChatPipeline,
    OllamaEmbedder,
    OllamaGenerator,
    Tool,
)
from tempest_fastapi_sdk.genai.rag import ChatMemory, SearxngBackend, WebSearch


async def get_weather(args: dict) -> str:
    """Tool handler: takes validated args, returns text for the model."""
    return f"It's 24°C in {args['city']}."


weather_tool = Tool(
    name="get_weather",
    description="Look up the weather for a city.",
    parameters={
        "type": "object",
        "properties": {"city": {"type": "string"}},
        "required": ["city"],
    },
    handler=get_weather,
)

pipeline = AIChatPipeline(
    OllamaGenerator("llama3.2"),
    memory=ChatMemory(OllamaEmbedder("nomic-embed-text")),
    web_search=WebSearch(SearxngBackend("http://localhost:8080", http_client=HTTPClient())),
    tools=[weather_tool],
    base_system_prompt="You are a concise assistant. Answer in English.",
)


async def main() -> None:
    result = await pipeline.respond(
        user_id="u1",
        chat_id="c1",
        content="What's the weather in Recife?",
        use_web_search=False,      # True augments the prompt with web search
        speak=False,               # True generates audio (needs tts=)
    )
    print(result.reply)
    print("tools called:", result.tool_calls_made)
    print("sources:", result.sources)
    print("memories used:", len(result.memory_hits))


asyncio.run(main())
```

`respond` runs the whole cycle: recall memory → (optional) augment with web
search → build the messages (system + memory + context + history + user
turn; `images` ride on the user turn) → generate (with a bounded
tool-calling loop when `tools` + a capable backend are set —
`OllamaGenerator` **or** the local `TextGenerator` (transformers);
otherwise plain `chat`) → (optional) TTS → best-effort index of both
turns.

!!! tip "Moderation + context window in the pipeline"
    Optional constructor args: `moderator=` (a `ModerationBackend` —
    `RuleModerator`/`ClassifierModerator`) screens the input **and every
    `history` turn** before generating and the reply after — in `respond`
    and in `stream`; a flagged turn answers `blocked_message` (a flagged
    input never calls the model). `tokenizer=` + `max_context_tokens=` trim the
    oldest turns (via `truncate_messages`) to fit the window before generating.
    Both opt-in.

`AIChatResult` carries `reply`, `sources`, `memory_hits`,
`tool_calls_made` and `audio_base64`.

!!! tip "Tools on the local backend (transformers)"
    `TextGenerator.chat_with_tools` renders the chat template with
    `tools=` (transformers >= 4.44) and parses the tool calls the model
    emits (`<tool_call>{...}</tool_call>` for Qwen/Hermes, or Llama JSON),
    returning the same shape as `OllamaGenerator` — so the same
    `AIChatPipeline` runs on local weights, no daemon required. Use a
    tool-capable instruct model (e.g. `Qwen/Qwen2.5-7B-Instruct`).

## Ready endpoint: `make_ai_chat_router`

One router, a whole chat backend in-process:

```python
from fastapi import FastAPI

from tempest_fastapi_sdk.genai import (
    AIChatPipeline,
    TextGenerator,
    TextModelEnum,
    make_ai_chat_router,
)

pipeline = AIChatPipeline(generator=TextGenerator(TextModelEnum.QWEN2_5_7B_INSTRUCT))


app = FastAPI()
app.include_router(make_ai_chat_router(pipeline))   # prefix /api/ai-chat
```

It mounts `POST /api/ai-chat/chat` (returns `AIChatResult`) and
`POST /api/ai-chat/chat/stream` (tokens over SSE). The body is
`{"chat_id": ..., "content": ..., "history": [...]}` — no `user_id`.

!!! note "The router is stateless"
    History lives in the request body, not on the server — each call sends
    `history`. That keeps the backend sessionless (horizontal scale for
    free) and long-term memory handles the "remembering" via `ChatMemory`.

### With memory: the conversation owner comes from the session

With `memory=` on the pipeline, every turn recalls and indexes the
**user's** memories. Who the user is comes from your auth dependency, never
from the body:

```python
# src/api/app.py

from fastapi import Depends, FastAPI

from tempest_fastapi_sdk.genai import (
    AIChatPipeline,
    OllamaEmbedder,
    OllamaGenerator,
    make_ai_chat_router,
)
from tempest_fastapi_sdk.genai.rag import ChatMemory

from src.api.dependencies.auth import current_user_id

pipeline = AIChatPipeline(
    OllamaGenerator("llama3.2"),
    memory=ChatMemory(OllamaEmbedder("nomic-embed-text")),
)


def create_app() -> FastAPI:
    """Mount the AI chat behind the service's own auth."""
    app = FastAPI()
    app.include_router(
        make_ai_chat_router(
            pipeline,
            current_user_id=current_user_id,
            dependencies=[Depends(current_user_id)],
        ),
    )
    return app
```

!!! warning "`current_user_id` is required alongside `memory`"
    Recalling memory under a `user_id` taken from the **body** would hand
    anyone's past conversations to whoever knows their id — and
    `AIChatResult` returns the `memory_hits`. So the router refuses that
    combination at mount time (`ValueError`). Without memory,
    `current_user_id` is optional and the turn runs with no owner.

!!! info "The body does not pick roles"
    `history[].role` accepts only `"user"` and `"assistant"`. A
    client-sent `"system"` turn would sit in the prompt with the authority
    of your `base_system_prompt` — the body gets a `422`. And with
    `moderator=`, every `history` turn's content goes through the same
    filter as the new message.

??? note "Migrating from a version that read `user_id` from the body"
    Up to 0.299.0, `AIChatRequestSchema` had `user_id` and the router
    passed it to `memory.search`. Now:

    - the field is gone from the schema; an older client still sending
      `user_id` does not break — the value is ignored;
    - a pipeline **with** `memory=` needs `current_user_id=` on
      `make_ai_chat_router`, or mounting raises `ValueError`;
    - `history` with a `role` other than `user`/`assistant` now answers
      `422`;
    - calling the pipeline directly, `user_id=None` skips memory for the
      turn (no recall, no indexing).

## Streaming

`stream` yields tokens as they come (prompt mode; it resolves any
tool-calls **before** it starts emitting):

```python
import asyncio

from tempest_fastapi_sdk.genai import AIChatPipeline, TextGenerator, TextModelEnum

pipeline = AIChatPipeline(generator=TextGenerator(TextModelEnum.QWEN2_5_7B_INSTRUCT))


async def stream_demo() -> None:
    async for token in pipeline.stream(
        user_id="u1", chat_id="c1", content="Explain RAG in one sentence.",
    ):
        print(token, end="", flush=True)


asyncio.run(stream_demo())
```

!!! warning "Moderation while streaming: what was sent stays sent"
    With `moderator=`, `stream` screens the reply too, and
    `stream_moderation=` on the constructor picks how:

    - `"incremental"` (default) — before each piece is sent, the text
      generated so far (that piece included) goes through the moderator.
      On the first flag the stream stops and the last piece is
      `blocked_message`. Earlier pieces **were already delivered** and
      cannot be recalled; the guarantee is that the piece which makes the
      text flaggable is never sent. With `RuleModerator` and the block list
      `["secret"]`, the reply `"here is the SECRET plan"`, emitted one
      character per piece, comes out as
      `"here is the SECRE"` followed by `blocked_message` — the term is
      never emitted whole, a prefix of it can be. It costs one moderator
      call per piece.
    - `"buffered"` — generates the whole reply, moderates once, and only
      then sends: either the whole reply as a single piece, or
      `blocked_message`. Nothing leaks, but the client waits for the full
      generation. It is the choice for a `ClassifierModerator`, which would
      cost one inference per piece in incremental mode.

    A blocked reply is not indexed into memory, same as `respond`.

!!! tip "The inference microservice becomes a choice, not a requirement"
    With the pipeline in-process, running a separate LLM-only service turns
    into an organizational decision (isolate the GPU, scale it apart) — not
    an architectural obligation. The same `TextBackend` lets you swap Ollama
    for vLLM/TGI later without touching the call site.
