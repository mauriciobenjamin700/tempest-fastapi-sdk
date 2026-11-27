# Ready endpoints and Redis cache

Use to expose the module over HTTP with `make_genai_router` and share the embedding cache across workers with `RedisEmbeddingCache`.

Part of the [Self-hosted generative AI](https://mauriciobenjamin700.github.io/tempest-fastapi-sdk/en/recipes/genai.md) recipe.

## `make_genai_router` — ready endpoints

Inject the objects you have loaded and the router mounts **only** the
matching endpoints:

```python
from fastapi import FastAPI
from tempest_fastapi_sdk.genai import Embedder, TextGenerator, make_genai_router

app = FastAPI()
app.include_router(
    make_genai_router(
        text_generator=TextGenerator("Qwen/Qwen2.5-7B-Instruct"),
        embedder=Embedder("sentence-transformers/all-MiniLM-L6-v2"),
    )
)
```

| Object | Endpoints |
| --- | --- |
| `text_generator` | `POST /generate`, `POST /generate/stream` (token-by-token SSE), `POST /chat` |
| `embedder` | `POST /embed` |
| `retriever` | `POST /rag` (query → context block) |
| `speech_to_text` | `POST /transcribe` (audio upload + optional `prompt` form field, capped by `max_prompt_chars`; `SpeechToText`, `OllamaSpeech`, `OpenAICompatSpeech` or any `SpeechTranscriber`) |
| `text_to_speech` | `POST /tts` (returns `audio/wav`; `TextToSpeech`, `OllamaSpeech` or any `SpeechSynthesizer`) |

!!! tip "Streaming"
    `/generate/stream` returns `text/event-stream`: each token becomes an
    SSE event, ending with a `done` event. It reuses the SDK's
    `sse_response` — a client with `EventSource` receives tokens live.

### Per-request limits: `GenAIRequestLimits`

Every AI endpoint turns request size into GPU time or memory. So the router
checks the request against a `GenAIRequestLimits` **before** it calls the
model, and answers `422` when a ceiling is crossed:

| Field | Default | Where it applies |
| --- | --- | --- |
| `max_prompt_chars` | `32_000` | the `/generate` prompt, the sum of the `/chat` messages, the `/rag` query, each `/embed` text, the `/image` prompt and negative prompt |
| `max_chat_messages` | `100` | messages per `/chat` |
| `max_new_tokens` | `4096` | the `config.max_new_tokens` the client sends |
| `max_embed_texts` | `256` | texts per `/embed` |
| `max_top_k` | `50` | the `/rag` `top_k` (which the schema also requires to be `>= 1`) |
| `max_tts_chars` | `5_000` | the `/tts` text |
| `max_image_side` | `2048` | the `/image` `config.width` / `config.height` |
| `max_image_steps` | `100` | the `/image` `config.steps` |
| `max_upload_bytes` | 25 MiB | the `/transcribe` upload, read in chunks; this one answers `413 UPLOAD_TOO_LARGE`, not `422` |

The defaults suit a small self-hosted deployment. To change them, pass your
own instance:

```python
from fastapi import FastAPI

from tempest_fastapi_sdk import register_exception_handlers
from tempest_fastapi_sdk.genai import GenAIRequestLimits, TextGenerator, make_genai_router

app: FastAPI = FastAPI()
register_exception_handlers(app)
app.include_router(
    make_genai_router(
        text_generator=TextGenerator("Qwen/Qwen2.5-7B-Instruct"),
        limits=GenAIRequestLimits(max_prompt_chars=8_000, max_new_tokens=1024),
    ),
)
```

With `register_exception_handlers` installed, the `422` body says which
field crossed which ceiling:

```text
POST /api/genai/generate {"prompt": "hi", "config": {"max_new_tokens": 1000000000}}
422 {"detail": "max_new_tokens exceeds the limit of 4096", "code": "VALIDATION_ERROR",
     "details": {"field": "max_new_tokens", "limit": 4096}}
```

!!! note "An absent `max_new_tokens` passes"
    The ceiling applies to the value the **client** sends. A request without
    `max_new_tokens` falls through to the generator's default, which you
    chose.

!!! warning "`/image` returns one image, so it asks for one"
    The response body is the image, so `config.num_images` above `1` gets
    `422` — the route used to render the whole batch on the GPU and return
    only the first. For a batch, use `ImageGenerator` directly.

### Audio: the bound is decoded duration, not just bytes

`max_upload_bytes` bounds what the client sends, not what the upload turns
into once decoded: the file header declares its own sample rate, and a codec
compresses silence to almost nothing (ten minutes of silent 16 kHz mono FLAC fit in
~114 KB with PyAV's FLAC encoder). That is why `SpeechToText`, `SpeakerDiarizer` and `VoiceEmbedder`
decode audio through the same bounded decoder, before the model sees
anything:

- a declared rate outside `MIN_SOURCE_RATE`–`MAX_SOURCE_RATE`
  (8,000–192,000 Hz) is refused before resampling;
- decoded audio past `max_audio_seconds` is refused as soon as the sample
  count passes the bound — the default is `DEFAULT_MAX_AUDIO_SECONDS`, one
  hour.

Both refusals are a `ValidationException`, so with
`register_exception_handlers` the `/transcribe` route (of this router and of
`make_voice_router`) answers `422`, with `details` carrying `min_rate` and
`max_rate`, or `max_seconds`. The Whisper model receives the decoded
samples, not the file.

```python
from tempest_fastapi_sdk.genai.audio import SpeakerDiarizer, SpeechToText

stt: SpeechToText = SpeechToText("base", max_audio_seconds=900.0)
diarizer: SpeakerDiarizer = SpeakerDiarizer(max_audio_seconds=900.0)
```

`max_audio_seconds=None` lifts the duration bound (the rate range still
applies) — only for trusted files the operator supplies.

## `RedisEmbeddingCache` — cache shared across workers

`Embedder` accepts a synchronous cache (`InMemoryEmbeddingCache`) **or**
an async one. Swap in `RedisEmbeddingCache` to share vectors across
processes with no call-site change:

```python
import asyncio

from tempest_fastapi_sdk.cache import AsyncRedisManager
from tempest_fastapi_sdk.genai import Embedder, RedisEmbeddingCache

from src.core.settings import settings

redis = AsyncRedisManager(**settings.redis_kwargs())
# in the lifespan: await redis.connect()  (before accessing .client)

cache = RedisEmbeddingCache(redis.client, ttl_seconds=86400)
embedder = Embedder("sentence-transformers/all-MiniLM-L6-v2", cache=cache)


async def main() -> None:
    """Run this example."""
    await embedder.embed(["text"])  # first call computes; other workers reuse it


asyncio.run(main())
```

`Embedder` awaits `get`/`set` when the cache is async and calls them
directly when it is sync — the same code serves both.

!!! note "Redis client: `AsyncRedisManager`"
    `RedisEmbeddingCache` takes a raw `redis.asyncio.Redis`. Since the embedder
    runs in an async context (a service/RAG flow, not middleware), use
    `AsyncRedisManager` (the SDK's managed client) and pass `.client` **after**
    `await redis.connect()` in the lifespan — before that, `.client` raises
    `RuntimeError`. Needs the `[cache]` extra (the `redis` package) alongside
    `[genai]`.
