# Self-hosted generative AI

Run HuggingFace models on **your own hardware** — no external API, no data
leaving your servers. The `tempest_fastapi_sdk.genai` module ships in
slices; this page covers the **first**: knowing, *before* you download
gigabytes of weights, whether the machine can handle the model.

!!! info "Module roadmap"
    - **v0.96:** `genai.hardware` — probing + `can_run` / `recommend`.
    - **v0.97:** `genai.rag` — RAG context (SearXNG web search + PDF
      reading) to inject into LLMs (this page, [RAG context](#rag-context)).
    - **v0.98:** `TextGenerator` — local LLM + int4/int8 quantization
      (section [Generate text](#generate-text-with-a-local-llm)).
    - **v0.99:** `Embedder`, `BatchScheduler`, `ModelRegistry` — embeddings
      + scale (section [Embeddings and scale](#embeddings-and-scale)).
    - **v0.102:** `SpeechToText` / `TextToSpeech` — audio (section
      [Audio (voice)](#audio-voice)).
    - **v0.107:** Ollama backend — `OllamaGenerator` / `OllamaEmbedder`,
      a local LLM without torch (section [Ollama backend](#ollama-backend)).
    - **v0.108:** long-term memory, AI chat pipeline and vision/tools —
      `ChatMemory` / `AIChatPipeline` (sections
      [Long-term memory](#long-term-memory) and
      [AI chat pipeline](#ai-chat-pipeline)).

The `[genai]` extra (transformers + torch + accelerate) is only needed to
**run** models. The capacity functions **import without the extra** —
`torch` is only used (when present) to read real GPU VRAM.

## "Can the machine handle it?"

Loading a too-large model ends in an OOM minutes into the download.
`can_run` answers first:

```python
from tempest_fastapi_sdk.genai import can_run, ModelDtype

report = can_run(model_id="Qwen/Qwen2.5-7B-Instruct", dtype=ModelDtype.BFLOAT16)

if report.fits:
    print(f"OK on {report.device} — {report.headroom_pct:.0f}% headroom")
else:
    print(report.reason)
    print("Suggestion:", report.suggestion)   # e.g. "Quantize to int4 ..."
```

`CapacityReport` carries: `fits`, `device` (`cuda`/`mps`/`cpu`),
`estimated_bytes` vs `available_bytes`, `headroom_pct`, `reason`, and a
concrete `suggestion` when it doesn't fit (quantize, offload to CPU, or
pick a smaller model).

!!! tip "Let the SDK pick the precision"
    `recommend(...)` tries `bfloat16` → `int8` → `int4` on the best
    available device and returns the **first** config that fits:

    ```python
    from tempest_fastapi_sdk.genai import recommend

    best = recommend(model_id="meta-llama/Llama-3.1-8B")
    print(best.device, best.dtype, best.fits)   # e.g. cuda int8 True
    ```

## Probing the hardware

```python
from tempest_fastapi_sdk.genai import probe_hardware

hw = probe_hardware()
print(hw.cpu_cores, hw.ram_available_bytes)
print(hw.has_cuda, [g.name for g in hw.gpus])   # per-GPU VRAM when CUDA is present
```

`HardwareInfo` reports CPU, total/available RAM, CUDA GPUs (name +
total/free VRAM), MPS (Apple), and free disk space. Without `psutil` or
`torch` installed, the matching fields fall back to safe defaults (`0` /
`False` / empty list) — nothing breaks.

## Estimate without downloading weights

The math is `params × bytes-per-param × overhead`. Bytes per param come
from the precision (`float32`=4, `float16`/`bfloat16`=2, `int8`=1,
`int4`≈0.6); the overhead (×1.25) covers activations, KV cache and
runtime context.

```python
from tempest_fastapi_sdk.genai import estimate_model_bytes, ModelDtype

gb = estimate_model_bytes(7_000_000_000, ModelDtype.INT4) / 1e9
print(f"~{gb:.1f} GB")   # 7B in int4
```

The parameter count can be passed explicitly (`num_params=`) or read from
the Hub by `model_id` (via `huggingface_hub`, without downloading the
weights — safetensors metadata).

## Generate text with a local LLM

`TextGenerator` loads a HuggingFace causal LM **once** and generates on
your hardware. It resolves device and precision itself, supports int4/int8
quantization, loads the weights lazily (on first call), and frees VRAM
when idle. Needs `[genai]` (and `[genai-quant]` to quantize).

```python
from tempest_fastapi_sdk.genai import TextGenerator

gen = TextGenerator(
    "Qwen/Qwen2.5-7B-Instruct",
    quantization="int4",            # fits a modest GPU; None = full precision
    idle_unload_seconds=300,        # free VRAM after 5 min idle
)

text = await gen.generate("Explain PIX in one sentence.", max_new_tokens=128)

# chat with a role template:
reply = await gen.chat([
    {"role": "system", "content": "You answer in English."},
    {"role": "user", "content": "What is PIX?"},
])

# token-by-token streaming:
async for piece in gen.stream("Write a haiku about rain."):
    print(piece, end="", flush=True)

gen.unload()                        # free the memory now
```

Blocking generation runs in `asyncio.to_thread` — it never blocks the
event loop. `device="auto"` picks CUDA → MPS → CPU; `dtype="auto"` uses
bf16 on GPU and fp32 on CPU.

!!! tip "Check before loading"
    Pair it with the [capacity check](#can-the-machine-handle-it): run
    `can_run` / `recommend` to pick a `quantization`/`device` that **fits**
    before instantiating the `TextGenerator`.

!!! tip "Free VRAM between bursts"
    With `idle_unload_seconds` set, call `gen.unload_if_idle()` periodically
    (e.g. in a `@tq.interval(60)` [TaskQueue](queue-tasks.md) task) — it
    unloads only once past the idle threshold, no background-thread magic.
    `unload()` frees immediately.

## Ollama backend

`TextGenerator` loads HuggingFace weights with `torch` on your hardware —
great when you have GPU/torch, but it means downloading gigabytes of weights
and managing VRAM. If you already run a local **Ollama daemon**,
`OllamaGenerator` uses the **same `genai` surface** (router, `Retriever`,
`GenerationConfig`) talking HTTP to Ollama: no torch, no local weights, no
`load()`. Ollama handles the download and VRAM for you.

Needs the `[genai-ollama]` extra (just `httpx`) and the daemon running with
the model already pulled:

```bash
uv add "tempest-fastapi-sdk[genai-ollama]"
ollama pull llama3.2
ollama pull nomic-embed-text
```

### Generate text via Ollama

`OllamaGenerator` mirrors `TextGenerator` — `generate`, `chat` and
`stream`, same signature:

```python
import asyncio

from tempest_fastapi_sdk.genai import OllamaGenerator

gen = OllamaGenerator("llama3.2")   # default base_url = http://127.0.0.1:11434


async def main() -> None:
    # simple generation:
    text = await gen.generate("Explain PIX in one sentence.")
    print(text)

    # chat with a role template:
    reply = await gen.chat([
        {"role": "system", "content": "You answer in English."},
        {"role": "user", "content": "What is PIX?"},
    ])
    print(reply)

    # token-by-token streaming:
    async for piece in gen.stream("Write a haiku about rain."):
        print(piece, end="", flush=True)


asyncio.run(main())
```

No `load()` or `unload()`: the model lives in the Ollama daemon, which
pulls it on the first call and frees VRAM on its own. `base_url` points at
another host when Ollama isn't local (the default is `DEFAULT_OLLAMA_URL`);
`keep_alive`, `timeout` and your own `http_client` (to reuse the pool) are
optional.

!!! info "`GenerationConfig` maps to Ollama options"
    The same typed `GenerationConfig` works here — its fields are translated
    to Ollama options: `max_new_tokens`→`num_predict`,
    `repetition_penalty`→`repeat_penalty`, and `temperature`/`top_p`/`top_k`/
    `seed`/`stop` pass through. `do_sample=False` becomes `temperature=0`
    (greedy generation).

### Embeddings via Ollama + RAG

`OllamaEmbedder` satisfies the same `SupportsEmbed` protocol as `Embedder`,
so it drops into `Retriever` and the `/embed` endpoint with nothing else to
change — the embeddings come from Ollama instead of torch:

```python
import asyncio

from tempest_fastapi_sdk.genai import OllamaEmbedder, OllamaGenerator
from tempest_fastapi_sdk.genai.rag import InMemoryVectorStore, PdfReader, Retriever

gen = OllamaGenerator("llama3.2")
rag = Retriever(OllamaEmbedder("nomic-embed-text"), InMemoryVectorStore())


async def main() -> None:
    await rag.index(PdfReader().chunks("/kb/manual.pdf"))     # once
    context = await rag.retrieve("how to refund?", top_k=5)   # cheap, afterwards
    print(await gen.generate(context))


asyncio.run(main())
```

`embed(texts, *, batch_size=32)` returns `list[list[float]]`, just like
`Embedder`.

### Same router, torch OR Ollama

`make_genai_router` type-hints `TextBackend` / `SupportsEmbed`, so the
Ollama objects slot in where the torch ones went without touching anything
else:

```python
from fastapi import FastAPI

from tempest_fastapi_sdk.genai import (
    OllamaEmbedder,
    OllamaGenerator,
    make_genai_router,
)

app = FastAPI()
app.include_router(
    make_genai_router(
        text_generator=OllamaGenerator("llama3.2"),
        embedder=OllamaEmbedder("nomic-embed-text"),
    )
)
```

Swapping `TextGenerator` / `Embedder` (torch) for `OllamaGenerator` /
`OllamaEmbedder` is the only change — the `/generate`, `/generate/stream`,
`/chat` and `/embed` endpoints are identical.

!!! tip "`TextBackend` is the seam for any engine"
    `TextBackend` is a `runtime_checkable` `Protocol` (`generate` / `chat`
    / `stream`). Ollama is just one implementation; to plug in vLLM, TGI or
    a hosted API, implement the same protocol and inject it into the router
    / `Retriever` — the call site doesn't change.

## Long-term memory

A chat that forgets everything between sessions isn't an assistant — it's a
form. `ChatMemory` gives the conversation **long-term memory**: every turn
becomes an indexed embedding, and before answering you recall the most
relevant snippets from **that same user** — even from old chats. Recall is
recency-aware: what is semantically close *and* recent floats to the top.

Needs the `[genai-chroma]` extra (ChromaDB). The embedder is any
`SupportsEmbed` — here an `OllamaEmbedder`, no torch:

```python
import asyncio
from datetime import datetime, timezone

from tempest_fastapi_sdk.genai import OllamaEmbedder
from tempest_fastapi_sdk.genai.rag import ChatMemory

memory = ChatMemory(
    OllamaEmbedder("nomic-embed-text"),
    persist_directory="./chat_memory",   # None = in-memory only
    top_k=5,
    min_similarity=0.55,
)


async def main() -> None:
    now = datetime.now(timezone.utc)

    # index two turns of an old conversation:
    await memory.index(
        user_id="u1", chat_id="c1", message_id="m1",
        role="user", content="I prefer short, direct answers.",
        created_at=now,
    )
    await memory.index(
        user_id="u1", chat_id="c1", message_id="m2",
        role="user", content="I work with FastAPI and Postgres.",
        created_at=now,
    )

    # in a NEW chat, recall what matters for that user:
    hits = await memory.search(
        user_id="u1",
        query="what stack does he use?",
        exclude_chat_id="c2",     # ignore the current chat
    )
    for hit in hits:
        print(f"{hit.score:.2f}  {hit.content}")


asyncio.run(main())
```

`search` filters by `user_id`, applies the similarity floor
(`min_similarity`), then blends in the recency decay — each `MemoryHit`
carries `content`, `role`, `chat_id`, `created_at`, `similarity` (raw
cosine) and `score` (the final value, recency included). `delete_for_chat`
wipes everything for a chat when it's removed.

!!! info "The `[genai-chroma]` extra and the recency decay"
    Install with `uv add "tempest-fastapi-sdk[genai-chroma]"`. The final
    `score` combines similarity and recency via
    `0.5 ** (age_in_days / recency_halflife_days)` — with the 14-day
    default, a 14-day-old snippet weighs half of a freshly written one.
    Tune the blend with `recency_weight` (0 = similarity only).

!!! tip "Generic RAG with `ChromaVectorStore`"
    Just need a persistent vector store (without the per-user memory
    logic)? `ChromaVectorStore` is a `VectorStore` like the others —
    `add(chunks, vectors)` / `search(vector, top_k=)` — backed by ChromaDB.
    Drop it into `Retriever` in place of `InMemoryVectorStore` /
    `PgVectorStore` to get a disk-persisted corpus:

    ```python
    from tempest_fastapi_sdk.genai import OllamaEmbedder
    from tempest_fastapi_sdk.genai.rag import ChromaVectorStore, Retriever

    rag = Retriever(
        OllamaEmbedder("nomic-embed-text"),
        ChromaVectorStore(collection_name="kb", persist_directory="./kb"),
    )
    ```

## AI chat pipeline

Here the earlier slices click together. Building a "real" chatbot — memory,
web RAG, tool-calling, optional TTS — usually means writing (and
maintaining) an entire inference microservice. `AIChatPipeline` does it
**inside your process**: you inject the pieces you've already seen
(`OllamaGenerator`, `ChatMemory`, `WebSearch`, `Tool`s) and call `respond`.

```python
import asyncio

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
    web_search=WebSearch(SearxngBackend("http://localhost:8080")),
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
turns. `AIChatResult` carries `reply`, `sources`, `memory_hits`,
`tool_calls_made` and `audio_base64`.

!!! tip "Tools on the local backend (transformers)"
    `TextGenerator.chat_with_tools` renders the chat template with
    `tools=` (transformers >= 4.44) and parses the tool calls the model
    emits (`<tool_call>{...}</tool_call>` for Qwen/Hermes, or Llama JSON),
    returning the same shape as `OllamaGenerator` — so the same
    `AIChatPipeline` runs on local weights, no daemon required. Use a
    tool-capable instruct model (e.g. `Qwen/Qwen2.5-7B-Instruct`).

### Ready endpoint: `make_ai_chat_router`

One router, a whole chat backend in-process:

```python
from fastapi import FastAPI

from tempest_fastapi_sdk.genai import make_ai_chat_router

app = FastAPI()
app.include_router(make_ai_chat_router(pipeline))   # prefix /api/ai-chat
```

It mounts `POST /api/ai-chat/chat` (returns `AIChatResult`) and
`POST /api/ai-chat/chat/stream` (tokens over SSE).

!!! note "The router is stateless"
    History lives in the request body, not on the server — each call sends
    `history`. That keeps the backend sessionless (horizontal scale for
    free) and long-term memory handles the "remembering" via `ChatMemory`.

### Streaming

`stream` yields tokens as they come (prompt mode; it resolves any
tool-calls **before** it starts emitting):

```python
import asyncio


async def stream_demo() -> None:
    async for token in pipeline.stream(
        user_id="u1", chat_id="c1", content="Explain RAG in one sentence.",
    ):
        print(token, end="", flush=True)


asyncio.run(stream_demo())
```

!!! tip "The inference microservice becomes a choice, not a requirement"
    With the pipeline in-process, running a separate LLM-only service turns
    into an organizational decision (isolate the GPU, scale it apart) — not
    an architectural obligation. The same `TextBackend` lets you swap Ollama
    for vLLM/TGI later without touching the call site.

## Embeddings and scale

### Generate embeddings

`Embedder` turns text into vectors on your hardware (semantic search, RAG,
clustering). It loads the model once, batches, and (optionally) caches a
vector per text — a cache hit never touches the model.

```python
from tempest_fastapi_sdk.genai import Embedder, InMemoryEmbeddingCache

emb = Embedder(
    "sentence-transformers/all-MiniLM-L6-v2",
    cache=InMemoryEmbeddingCache(),     # or a Redis wrapper (get/set)
)

vectors = await emb.embed(["what is pix?", "how to refund?"])   # list[list[float]]
```

`cache` is any object with `get(key)->list|None` and `set(key, val)` —
pass a wrapper over `AsyncRedisManager` to share across workers.
`device`/`dtype`/`unload`/`unload_if_idle` work as on `TextGenerator`.

For semantic search, use `normalize=True` (unit vectors) + the
`cosine_similarity` function:

```python
from tempest_fastapi_sdk.genai import cosine_similarity

emb = Embedder("sentence-transformers/all-MiniLM-L6-v2", normalize=True)
q, *docs = await emb.embed(["question", "doc a", "doc b"])
ranked = sorted(docs, key=lambda d: cosine_similarity(q, d), reverse=True)
```

### Batch concurrent inference

On a GPU, one item at a time wastes the device. `BatchScheduler` coalesces
concurrent calls into a single batch — each caller still `await`s its own
result:

```python
from tempest_fastapi_sdk.genai import BatchScheduler

sched = BatchScheduler(emb._embed_many, max_batch=32, max_wait_ms=10)

# N concurrent requests become 1 forward pass:
vector = await sched.submit("text")
await sched.aclose()
```

It forms a batch once `max_batch` items are queued **or** `max_wait_ms`
has elapsed since the first — whichever comes first. A handler error
propagates to every caller in that batch.

### Share loaded models

`ModelRegistry` keeps loaded models by id (LRU) — two call sites asking
for the same model reuse the instance, and the least-recently-used one is
unloaded (`unload()`) once over `max_models`:

```python
from tempest_fastapi_sdk.genai import Embedder, ModelRegistry

registry = ModelRegistry(max_models=2)

def get_embedder(model_id: str) -> Embedder:
    return registry.get(model_id, lambda: Embedder(model_id))
```

## Audio (voice)

Interpret and generate voice on your hardware — no external API. Needs the
`[genai-audio]` extra (faster-whisper + Coqui TTS); the engines import
lazily.

### Interpret audio (STT)

`SpeechToText` transcribes with **faster-whisper** (Whisper via
CTranslate2, fast on CPU/GPU). Loads once, runs in a worker thread,
serializes calls through a semaphore.

```python
from tempest_fastapi_sdk.genai.audio import SpeechToText

stt = SpeechToText("base", device="auto")     # tiny…large-v3
result = await stt.transcribe("meeting.wav")
print(result.text, result.language, result.duration)
for seg in result.segments:                    # per-span timestamps
    print(seg.start, seg.end, seg.text)
```

Accepts a path or `bytes`. `device`/`compute_type` resolve automatically
(`float16` on GPU, `int8` on CPU).

### Generate voice (TTS)

`TextToSpeech` synthesizes with **Coqui TTS** (WAV). Same discipline
(lazy + thread + semaphore).

```python
from tempest_fastapi_sdk.genai.audio import TextToSpeech

tts = TextToSpeech("tts_models/multilingual/multi-dataset/xtts_v2")
wav = await tts.synthesize("Hello, world.", language="en")   # -> WAV bytes
# voice cloning (XTTS): pass a reference clip
wav = await tts.synthesize("Hi!", language="en", speaker_wav="ref.wav")
```

`synthesize` returns the WAV `bytes`; pass `out_path=` to also write it to
disk.

### Language (PT-BR / EN-US)

No need to know the Whisper code or pick a TTS model: use the `Language`
enum. It resolves the code (`pt`/`en`) for STT and a good TTS model per
language:

```python
from tempest_fastapi_sdk.genai.audio import Language, SpeechToText, TextToSpeech

# STT: force the language without memorizing the code
await SpeechToText("base").transcribe("audio.wav", language=Language.PT_BR)

# TTS: picks the language's default model automatically
tts = TextToSpeech.for_language(Language.EN_US)     # en-US model
wav = await tts.synthesize("Hello, world.")
```

`preset_for(Language.PT_BR)` exposes the preset (`whisper_language`,
`tts_model`, `tts_language`) to inspect/override. `language=` on
`transcribe`/`synthesize` also accepts a raw code (``"pt"``) or ``None``
(auto-detect for STT).

!!! tip "Full voice loop"
    Chain with the LLM: **STT** transcribes speech → `TextGenerator`/RAG
    answers → **TTS** speaks the reply. All local, nothing leaves the box.

## RAG context

A local LLM only knows what it trained on. For current, grounded answers,
inject context: `tempest_fastapi_sdk.genai.rag` searches the web
(self-hosted SearXNG), extracts page bodies, reads PDFs, and assembles a
prompt-ready block — without sending data outside. Needs the `[genai-rag]`
extra (httpx + trafilatura + pymupdf); the pieces import lazily.

### Web search (SearXNG)

```python
import httpx
from tempest_fastapi_sdk.genai.rag import SearxngBackend, WebSearch, build_context
from tempest_fastapi_sdk.utils.http_client import HTTPClient

client = HTTPClient()
search = WebSearch(SearxngBackend("http://localhost:8080", http_client=client))

results = await search.search("what is PIX?", max_results=5)   # list[SearchResult]
context = build_context("what is PIX?", results, long_text=False, max_chars=2000)
# -> a string ready to inject into your TextGenerator prompt
```

The backend is a `Protocol` (`WebSearchBackend`) — swap SearXNG for another
provider without touching call sites. The `HTTPClient` is injected (pool
reuse, plus retry/backoff + a circuit-breaker for free; wire it in the
FastAPI lifespan).

!!! tip "From question to context in one call"
    `WebSearch.retrieve` does search → (optional) parallel body extraction
    → `build_context`, all at once:

    ```python
    from tempest_fastapi_sdk.genai.rag import ContentExtractor

    extractor = ContentExtractor(http_client=httpx.AsyncClient())
    context = await search.retrieve("what is PIX?", extractor=extractor, max_results=5)
    answer = await gen.generate(context)
    ```

    Without `extractor` it uses snippets only. `ContentExtractor.extract_many`
    fetches N pages concurrently (bounded by `concurrency`).

### Extract page bodies

Search snippets are thin. To give the LLM ground truth, fetch each page
and extract the clean text (via `trafilatura`):

```python
from tempest_fastapi_sdk.genai.rag import ContentExtractor

extractor = ContentExtractor(http_client=httpx.AsyncClient())
for result in results:
    outcome = await extractor.extract(result.url)
    result.content = outcome.text          # "" on failure; outcome.failed marks it
context = build_context("what is PIX?", results)   # now with full bodies
```

Failures (timeout, 4xx/5xx, empty page) **never** raise — they come back
as `ExtractionResult(text="", failed=True)`, so no source is silently
dropped.

### Read PDFs (knowledge base)

`PdfReader` (PyMuPDF — detailed, reading-order extraction) turns PDF paths
into text and prompt/embedding-ready chunks:

```python
from tempest_fastapi_sdk.genai.rag import PdfReader, build_context

reader = PdfReader()
doc = reader.read("/kb/manual.pdf")             # Document: text + pages + metadata
chunks = reader.chunks("/kb/manual.pdf", max_chars=2000, overlap=200)

context = build_context("how to refund?", chunks)   # cites "file (page N)"
```

`chunks(..., overlap=200)` shares characters between neighbors so a fact on
a boundary isn't cut in half; `per_page=True` (default) keeps each chunk on
a single page and records its page number.

!!! tip "Mix web + PDF in one context"
    `build_context` accepts `SearchResult` and `Chunk` in the same list —
    it delimits each source with `---` and labels the origin (URL or `file
    (page N)`) so the LLM can cite. Pass `long_text=False` to truncate each
    source to `max_chars`.

### RAG over your own corpus (vector store)

Web search is one source; the other is **your own knowledge** (PDFs, docs).
Instead of re-embedding everything each request, index once into a **vector
store** and retrieve by similarity. `Retriever` ties `Embedder` → store →
`build_context`:

```python
from tempest_fastapi_sdk.genai import Embedder
from tempest_fastapi_sdk.genai.rag import InMemoryVectorStore, PdfReader, Retriever

rag = Retriever(Embedder("sentence-transformers/all-MiniLM-L6-v2", normalize=True),
                InMemoryVectorStore())

await rag.index(PdfReader().chunks("/kb/manual.pdf"))     # once
context = await rag.retrieve("how to refund?", top_k=5)   # cheap, afterwards
answer = await gen.generate(context)
```

- **`VectorStore`** is a `Protocol` — `InMemoryVectorStore` (dev/tests,
  cosine scan) or `PgVectorStore` (production).
- **`PgVectorStore`** uses **pgvector** in the Postgres the service already
  has (no new infra): creates the table on demand, searches with the cosine
  distance operator `<=>`. Needs `[genai-rag]` + `CREATE EXTENSION vector`.

```python
from tempest_fastapi_sdk.genai.rag import PgVectorStore

store = PgVectorStore(db, dim=384)          # db = AsyncDatabaseManager
rag = Retriever(embedder, store)
```

`rag.search(query, top_k=)` returns the `Chunk`s with a `score` (similarity);
`rag.retrieve(...)` builds the context for you. Need Qdrant/Weaviate later?
Implement `VectorStore` (2 methods) and inject it — `Retriever` doesn't
change.

## Ergonomics: typed config, router and Redis cache

### Typed `GenerationConfig`

Instead of scattering `**kwargs` (`max_new_tokens=...`, `temperature=...`)
across every call, build a validated, reusable `GenerationConfig` and
pass it via `config=`:

```python
from tempest_fastapi_sdk.genai import GenerationConfig, TextGenerator

gen = TextGenerator("Qwen/Qwen2.5-7B-Instruct", quantization="int4")
config = GenerationConfig(max_new_tokens=512, temperature=0.2, top_p=0.9)

await gen.generate("Explain PIX in one sentence.", config=config)
await gen.chat([{"role": "user", "content": "Hi"}], config=config)
```

Only the set fields layer over the defaults; explicit `**kwargs` still
win over the config (`gen.generate(prompt, config=config,
temperature=0.9)` uses `0.9`).

!!! tip "`seed` and `stop` apply on the local path too"
    `seed` and `stop` are honored by both `OllamaGenerator` and
    `TextGenerator` (transformers): `seed` is reapplied via
    `transformers.set_seed` before generating (same seed + `do_sample=True`
    reproduces the output) and `stop` becomes `model.generate`'s
    `stop_strings` argument (requires transformers >= 4.44). Either may come
    from the `GenerationConfig` or per call — the per-call override wins.

### Structured output (validated JSON)

Force the model to return a Pydantic schema and get the validated instance
back — instead of hoping the output happens to be parseable JSON:

```python
from pydantic import BaseModel
from tempest_fastapi_sdk.genai import OllamaGenerator


class Person(BaseModel):
    name: str
    age: int


gen = OllamaGenerator("llama3.2")
person: Person = await gen.generate_structured("Any person.", Person)
# -> Person(name="...", age=...)
```

`OllamaGenerator` sends the schema in the daemon's `format` field (Ollama
enforces JSON-schema-valid output natively) and parses the reply — **this is
the recommended structured route, no extra library**.

!!! info "On the local backend (transformers)"
    `TextGenerator.generate_structured(prompt, schema, constrained=True)`
    constrains decoding with `lm-format-enforcer` (the `[genai-structured]`
    extra) so the model can only emit tokens that keep the JSON valid. If the
    installed `lm-format-enforcer` doesn't match the installed `transformers`,
    `constrained=True` raises a clear error — use `constrained=False`
    (best-effort: generate then parse) or the Ollama backend instead.

!!! tip "Just the parse"
    `parse_structured(text, schema)` pulls the JSON out of a raw completion
    (tolerating Markdown fences and surrounding prose) and validates it against
    the schema — reusable on any model output.

### Vision (local multimodal VLM)

`VisionTextGenerator` is the multimodal sibling of `TextGenerator`: it loads an
`AutoModelForVision2Seq` + `AutoProcessor` and generates text conditioned on
images, on your hardware — parity with `OllamaGenerator`, which already accepts
`images`. Needs `[genai]` + `[genai-vlm]` (Pillow).

```python
from tempest_fastapi_sdk.genai import VisionTextGenerator

gen = VisionTextGenerator("llava-hf/llava-1.5-7b-hf")
description = await gen.generate(
    "USER: <image>\nDescribe the image.\nASSISTANT:",
    images=["photo.jpg"],
)
```

Images are accepted as a path, `bytes`, `PIL.Image` or a NumPy `ndarray` (same
leniency as `ort-vision-sdk`). `generate`/`chat` are image-optional — text-only
calls keep working (it is a `TextBackend`).

!!! warning "Processor conventions vary by family"
    This class targets the common `processor(text=..., images=...)` interface
    used by LLaVA and Qwen2-VL. Other families may need a thin adapter (image
    placeholder token, chat-template shape). Validate your target model before
    production.

### `make_genai_router` — ready endpoints

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
| `speech_to_text` | `POST /transcribe` (audio upload) |
| `text_to_speech` | `POST /tts` (returns `audio/wav`) |

!!! tip "Streaming"
    `/generate/stream` returns `text/event-stream`: each token becomes an
    SSE event, ending with a `done` event. It reuses the SDK's
    `sse_response` — a client with `EventSource` receives tokens live.

### `RedisEmbeddingCache` — cache shared across workers

`Embedder` accepts a synchronous cache (`InMemoryEmbeddingCache`) **or**
an async one. Swap in `RedisEmbeddingCache` to share vectors across
processes with no call-site change:

```python
from tempest_fastapi_sdk.cache import AsyncRedisManager
from tempest_fastapi_sdk.genai import Embedder, RedisEmbeddingCache

from src.core.settings import settings

redis = AsyncRedisManager(**settings.redis_kwargs())
# in the lifespan: await redis.connect()  (before accessing .client)

cache = RedisEmbeddingCache(redis.client, ttl_seconds=86400)
embedder = Embedder("sentence-transformers/all-MiniLM-L6-v2", cache=cache)

await embedder.embed(["text"])  # first call computes; other workers reuse it
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

## Recap

- **`GenerationConfig`** — typed, reusable generation parameters instead
  of `**kwargs`.
- **`make_genai_router`** — mounts only the endpoints of the injected
  objects; `/generate/stream` streams tokens over SSE.
- **`RedisEmbeddingCache`** — a shared vector cache; `Embedder` accepts a
  sync or async cache at the same call site.
- **Ollama backend** — `OllamaGenerator` / `OllamaEmbedder` use the same
  surface (router, `Retriever`, `GenerationConfig`) via a local Ollama
  daemon: no torch, no weights, no `load()`; `TextBackend` is the seam for
  other engines.
- **`ChatMemory` / `ChromaVectorStore`** — per-user long-term memory with
  similarity + recency recall (`[genai-chroma]`); `ChromaVectorStore` is a
  persistent `VectorStore` for generic RAG.
- **`AIChatPipeline` / `make_ai_chat_router`** — a full chatbot in-process
  (memory + web RAG + tool-calling + optional TTS); one stateless router
  (`/chat` + `/chat/stream` SSE) kills the inference microservice.
- **`can_run` / `recommend`** — answer whether the host runs the model
  and what to do if not, **before** the download.
- **RAG over a corpus** — `Retriever` + `VectorStore` (`InMemoryVectorStore`
  / `PgVectorStore` pgvector): index chunks once, retrieve top-k by
  similarity.
- **Audio** — `SpeechToText` (faster-whisper) transcribes; `TextToSpeech`
  (Coqui TTS) synthesizes — local voice, end to end.
- **RAG** — `WebSearch`/`SearxngBackend` (search), `ContentExtractor`
  (page bodies), `PdfReader` (PDF → text/chunks) and `build_context`
  (prompt block), all under the `[genai-rag]` extra.
- **`probe_hardware`** — a CPU/RAM/GPU/disk snapshot; degrades without the
  extras.
- **`estimate_model_bytes` / `bytes_per_param`** — the estimation math,
  testable and reusable.
- Everything imports without the `[genai]` extra; install it to actually
  run models (upcoming module slices).
