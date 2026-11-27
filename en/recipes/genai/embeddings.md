# Embeddings and scale

Use to generate embeddings (torch or ONNX), batch concurrent inference and share and inspect the loaded models.

Part of the [Self-hosted generative AI](https://mauriciobenjamin700.github.io/tempest-fastapi-sdk/en/recipes/genai.md) recipe.

## Generate embeddings

`Embedder` turns text into vectors on your hardware (semantic search, RAG,
clustering). It loads the model once, batches, and (optionally) caches a
vector per text — a cache hit never touches the model.

```python
import asyncio

from tempest_fastapi_sdk.genai import Embedder, InMemoryEmbeddingCache

emb = Embedder(
    "sentence-transformers/all-MiniLM-L6-v2",
    cache=InMemoryEmbeddingCache(),     # or a Redis wrapper (get/set)
)


async def main() -> None:
    """Run this example."""
    vectors = await emb.embed(["what is pix?", "how to refund?"])   # list[list[float]]


asyncio.run(main())
```

`cache` is any object with `get(key)->list|None` and `set(key, val)` —
pass a wrapper over `AsyncRedisManager` to share across workers.
`InMemoryEmbeddingCache` is an LRU of 1024 vectors by default
(`max_entries=`; `None` removes the bound), so a long-lived worker does not
grow forever.
`device`/`dtype`/`unload`/`unload_if_idle` work as on `TextGenerator`.

For semantic search, use `normalize=True` (unit vectors) + the
`cosine_similarity` function:

```python
import asyncio

from tempest_fastapi_sdk.genai import Embedder, cosine_similarity


emb = Embedder("sentence-transformers/all-MiniLM-L6-v2", normalize=True)


async def main() -> None:
    """Run this example."""
    q, *docs = await emb.embed(["question", "doc a", "doc b"])
    ranked = sorted(docs, key=lambda d: cosine_similarity(q, d), reverse=True)


asyncio.run(main())
```

## ONNX embeddings (no torch)

When you don't want the heavy `torch`/`transformers` stack just to embed,
`OnnxEmbedder` runs an embedding model exported to ONNX via ONNX Runtime —
light dependencies (`onnxruntime` + `tokenizers`, the `[genai-onnx]` extra),
cheap on CPU. It satisfies the same `SupportsEmbed`, so it drops into a
`Retriever` / `make_genai_router` unchanged.

```python
import asyncio

from tempest_fastapi_sdk.genai import OnnxEmbedder

emb = OnnxEmbedder(
    "all-MiniLM-L6-v2.onnx",
    tokenizer="sentence-transformers/all-MiniLM-L6-v2",
    normalize=True,
)


async def main() -> None:
    """Run this example."""
    vectors = await emb.embed(["question", "doc a"])


asyncio.run(main())
```

The default pooling is the **attention-mask-weighted mean** of the token
embeddings (not a naive average over padding), so vectors match the torch
`Embedder` for the same model (cosine ≈ 1.0). BGE-family models are trained on
the `[CLS]` token — pass `pooling="cls"` for them, as the model card says. A
graph exported with its pooling head baked in (a 2-D `sentence_embedding`
output) is used as is, and padding uses the tokenizer's own `<pad>` (id 1 in
RoBERTa/XLM-R), not the id 0 `tokenizers` assumes. Export the model in a throwaway environment — `optimum` is **not** a
dependency of this package, because it pins `transformers<4.58` in the lock of
whoever installs it:

```bash
uvx --from "optimum[onnxruntime]" optimum-cli export onnx \
    --model sentence-transformers/all-MiniLM-L6-v2 \
    --task feature-extraction exports/minilm
```

Then point `model_path` at the generated `.onnx`.

## Batch concurrent inference

On a GPU, one item at a time wastes the device. `BatchScheduler` coalesces
concurrent calls into a single batch — each caller still `await`s its own
result:

```python
import asyncio

from tempest_fastapi_sdk.genai import BatchScheduler, Embedder, EmbeddingModelEnum

emb = Embedder(EmbeddingModelEnum.ALL_MINILM_L6_V2)


async def embed_batch(texts: list[str]) -> list[list[float]]:
    """Embed a whole batch in one forward pass."""
    return await emb.embed(texts)


sched = BatchScheduler(embed_batch, max_batch=32, max_wait_ms=10)


async def main() -> None:
    """Run this example."""
    # N concurrent requests become 1 forward pass:
    vector = await sched.submit("text")
    await sched.aclose()


asyncio.run(main())
```

It forms a batch once `max_batch` items are queued **or** `max_wait_ms`
has elapsed since the first — whichever comes first. A handler error
propagates to every caller in that batch.

Every path resolves every caller: a handler that returns something without
a length (`None`, a generator) or the wrong number of results fails the
batch with `RuntimeError` and the worker moves on; a `CancelledError` the
handler raised itself cancels only that batch; and cancelling the worker
cancels the batch in flight **and** everything still queued — no `submit`
waits forever.

## Share loaded models

`ModelRegistry` keeps loaded models by id (LRU) — two call sites asking
for the same model reuse the instance, and the least-recently-used one is
unloaded (`unload()`) once over `max_models`:

```python
from tempest_fastapi_sdk.genai import Embedder, ModelRegistry

registry = ModelRegistry(max_models=2)

def get_embedder(model_id: str) -> Embedder:
    return registry.get(model_id, lambda: Embedder(model_id))
```

!!! note "A kept handle still counts toward `max_models`"
    Keeping the object `get()` returned (`embedder = registry.get(...)` at
    startup, used for every request) is safe with the SDK loaders. After an
    eviction, the next call on that handle does **not** reload behind the
    registry: it registers the model again under the same key (replacing
    any object a later `get()` built there), evicts the least-recently-used
    entry, and only then loads the weights. With `max_models=1`, keeping
    `A`, asking for `B` and using `A` again means three loads and two
    unloads, never both models resident at once.

    When the evicted model still has calls running, whatever takes its
    place waits for them to finish before loading — both the readmitted
    handle and the new model from a `get()`. The exception is a call made
    **from inside** another model's call: it does not wait (the outer call
    may be exactly what the evicted model is waiting on), so there the
    ceiling is exceeded until the outer call ends. A third-party object
    that only implements `unload()` has no such hook — for those, call
    `get()` per request.

## What is loaded right now

A self-hosted service can hold several models at once, each holding
gigabytes of VRAM for as long as it stays loaded. `runtime_report` answers
the operational question — *what is resident right now?*:

```python
from tempest_fastapi_sdk.genai import Embedder, TextGenerator, runtime_report

report = runtime_report(
    {
        "chat": TextGenerator("Qwen/Qwen2.5-0.5B-Instruct"),
        "embed": Embedder("sentence-transformers/all-MiniLM-L6-v2"),
    },
)
for model in report.models:
    print(model.key, model.kind, model.loaded, model.seconds_idle)
print(report.loaded_count, "of", report.total_count)
```

```text
chat TextGenerator True 612.4
embed Embedder False None
1 of 2
```

The order is not accidental: **loaded first, and among those the
longest-idle first** — the order someone reads when the card is full and
they have to decide what to free. `model.idle_past_threshold` says which
ones are already past their own threshold.

For a single handle, `describe_model`:

```python
from tempest_fastapi_sdk.genai import TextGenerator, describe_model

info = describe_model(TextGenerator("Qwen/Qwen2.5-0.5B-Instruct"), key="chat")
print(info.kind, info.model_id, info.device, info.loaded)
```

!!! check "Reading never loads"
    `describe_model` reads attributes only — calling it on a generator that
    has never loaded returns `loaded=False` and leaves it unloaded. That is
    what makes it safe to call on a live service, health check included.

!!! note "A missing field becomes `None`, never a guess"
    Each loader exposes a slightly different surface, and third-party
    objects expose less still. A handle implementing only `is_loaded` still
    shows up in the report with the rest `None` — better than vanishing
    from a memory audit.

The registry can describe itself, and can free what has gone stale:

```python
from tempest_fastapi_sdk.genai import ModelRegistry

registry = ModelRegistry(max_models=3)
report = registry.inventory(probe=False)
freed = registry.unload_idle()
print(freed)
```

`unload_idle()` calls each handle's `unload_if_idle()` and returns the keys
it freed. **The entries stay registered** — a `TextGenerator` that dropped
its weights is still the right object to hand out, and it reloads on next
use. To forget the entry entirely, use `evict()` / `evict_all()`.

And over HTTP:

```python
from fastapi import FastAPI

from tempest_fastapi_sdk.genai import ModelRegistry, make_genai_router

registry = ModelRegistry(max_models=3)
app = FastAPI()
app.include_router(make_genai_router(models=registry))
```

```bash
curl "http://127.0.0.1:8000/api/genai/models?probe=false"
```

`probe=false` skips the host probe — the only part of the endpoint that costs
anything. With `pynvml` installed (the `[metrics]` extra), the probe reads each
GPU's name and memory through NVML, without creating a CUDA context in the web
process; without it, it falls back to `torch.cuda.mem_get_info`, which does —
measured at +209 MiB of VRAM on an RTX 4070 Ti SUPER. With the default, the report comes alongside the host's memory
picture, so one call answers both "what is loaded" and "how much room is
left".

## In Prometheus and in the admin

The same inventory feeds the two surfaces someone is already watching.

```python
from tempest_fastapi_sdk.genai import GenAIMetrics, ModelRegistry

registry = ModelRegistry(max_models=3)
metrics = GenAIMetrics()

def publish() -> None:
    metrics.observe_inventory(registry.inventory(probe=False))
```

Call `publish()` from a periodic task and `/metrics` starts exposing
`genai_models_loaded{kind,device}`, `genai_models_known` and — when the
report carried hardware — `genai_gpu_vram_free_bytes{index}`.

!!! note "A gauge is a snapshot, which is why it is cleared first"
    The counters answer "how much work went through"; these answer "what is
    resident **now**", the question that explains an OOM. The labelled series
    are cleared on every call: a model that unloaded between two snapshots
    must **stop** being reported, or the stale value reads as residency.

In the admin, the same numbers become cards:

```python
from tempest_fastapi_sdk.admin import AdminSite
from tempest_fastapi_sdk.genai import ModelRegistry, make_model_cards

registry = ModelRegistry(max_models=3)
site = AdminSite(dashboard_cards=make_model_cards(registry))
```

Three of them: **Models resident** (`2 of 5`), **Resident by device**
(`cuda: 2, cpu: 1`) and **VRAM free**. Only the last one probes the host, so
pass `include_vram=False` on a GPU-less box or when the dashboard must not
read NVML.

!!! tip "The handles are read at render time"
    A registry that is empty when you build the site still reports correctly
    once models load — the cards query the inventory on every render, not at
    construction.
