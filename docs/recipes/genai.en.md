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

client = httpx.AsyncClient()
search = WebSearch(SearxngBackend("http://localhost:8080", http_client=client))

results = await search.search("what is PIX?", max_results=5)   # list[SearchResult]
context = build_context("what is PIX?", results, long_text=False, max_chars=2000)
# -> a string ready to inject into your TextGenerator prompt
```

The backend is a `Protocol` (`WebSearchBackend`) — swap SearXNG for another
provider without touching call sites. The `httpx.AsyncClient` is injected
(pool reuse; wire it in the FastAPI lifespan).

!!! tip "From question to context in one call"
    `WebSearch.retrieve` does search → (optional) parallel body extraction
    → `build_context`, all at once:

    ```python
    from tempest_fastapi_sdk.genai.rag import ContentExtractor

    extractor = ContentExtractor(http_client=client)
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

extractor = ContentExtractor(http_client=client)
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

## Recap

- **`can_run` / `recommend`** — answer whether the host runs the model
  and what to do if not, **before** the download.
- **RAG** — `WebSearch`/`SearxngBackend` (search), `ContentExtractor`
  (page bodies), `PdfReader` (PDF → text/chunks) and `build_context`
  (prompt block), all under the `[genai-rag]` extra.
- **`probe_hardware`** — a CPU/RAM/GPU/disk snapshot; degrades without the
  extras.
- **`estimate_model_bytes` / `bytes_per_param`** — the estimation math,
  testable and reusable.
- Everything imports without the `[genai]` extra; install it to actually
  run models (upcoming module slices).
