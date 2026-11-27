# Ollama backend

Use to run LLMs and embeddings through Ollama, without torch: generation, RAG, hybrid search, reranking and the same router swapping torch for Ollama.

Part of the [Self-hosted generative AI](../genai.md) recipe.

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

!!! info "Ollama also speaks and transcribes"
    Since v0.40.11 Ollama generates speech (`/v1/audio/speech`) and
    `lfm3-audio` transcribes; `OllamaSpeech` wraps both. What was measured,
    the limits and the sources are in
    [Voice through Ollama](audio.md#voice-through-ollama).

## Generate text via Ollama

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

!!! warning "A read timeout is not retried"
    The `HTTPClient` the generator builds retries a connection error and a
    retryable status (`429`, `5xx`), but **not** a `ReadTimeout`. When the
    client stops waiting, the daemon aborts the generation — in the Ollama
    0.30.11 log each attempt shows up as a `500` of exactly the timeout — so
    the next attempt starts from zero and times out again. Measured with
    `qwen2.5:3b` on CPU (`options={"num_gpu": 0}`) and `timeout=10`: up to
    v0.303.1 the failure arrived after 31.5 s, after three aborts; now it
    arrives after 10.0 s, with one (median of N=3 before and N=5 after). The
    same holds for `OllamaEmbedder` and `OpenAICompatGenerator`. To get the
    retry back, pass the policy explicitly — it is used as given:

    ```python
    from tempest_fastapi_sdk import RetryPolicy
    from tempest_fastapi_sdk.genai import OllamaGenerator

    gen: OllamaGenerator = OllamaGenerator(
        "llama3.2",
        retry_policy=RetryPolicy(retry_on_read_timeout=True),
    )
    ```

!!! info "`GenerationConfig` maps to Ollama options"
    The same typed `GenerationConfig` works here — its fields are translated
    to Ollama options: `max_new_tokens`→`num_predict`,
    `repetition_penalty`→`repeat_penalty`, and `temperature`/`top_p`/`top_k`/
    `seed`/`stop` pass through. `do_sample=False` becomes `temperature=0`
    (greedy generation).

!!! warning "A daemon error raises `OllamaError`"
    Ollama reports some failures (model not pulled, a runner that crashed
    mid-way) as an `{"error": "..."}` body — on a `200` or as one line of the
    NDJSON stream. `generate`, `chat`, `embed` and the structured variants
    raise `OllamaError` (with `.model` and `.detail`) instead of returning
    `""`, and `stream` raises on the error line instead of ending as if it
    had finished — pieces already yielded stay yielded.

`chat` follows `generate` on cache and metrics: with `generation_cache=`, a
repeated deterministic call skips the daemon, and with `metrics=` it is
recorded under the `chat` operation with Ollama's token counts. The `chat`
key is scoped apart from `generate`, so a prompt equal to the serialized
messages never collides.

### Context window: `num_ctx`

Ollama processes at most `num_ctx` prompt tokens, and the daemon **cuts the
rest without a word**: the reply comes back `200`, no error, generated over a
slice of what you sent (since 0.374.0 the SDK detects the cut and logs a
`WARNING` — see below). Measured on Ollama 0.30.11 (`lfm2.5-thinking:1.2b`,
on a 16 GB RTX 4070 Ti SUPER): a 35,018-token chat came back with
`prompt_eval_count` 2,051 under the default, and 35,018 with
`num_ctx=65536`. Pin the window on the generator, and it goes with every
call:

```python
import asyncio

from tempest_fastapi_sdk.genai import GenerationConfig, OllamaGenerator

gen = OllamaGenerator(
    "llama3.2",
    num_ctx=32768,
    options={"num_thread": 8},
    config=GenerationConfig(max_new_tokens=512),
)


async def main() -> None:
    """Run this example."""
    reply: str = await gen.chat([{"role": "user", "content": "Hi"}])
    print(reply)


asyncio.run(main())
```

- **`num_ctx`** becomes the `num_ctx` option of every request.
- **`options`** are raw Ollama options (`num_thread`, `num_gpu`, …), also
  on every request. `num_ctx` in both places raises `ValueError`.
- **`config`** is the default `GenerationConfig`. Each call's own wins field
  by field, and the call's `**kwargs` win over everything.

This matters most for a caller that passes **no** option per call — the
[agent loop](../agents.en.md#the-model-runs-on-the-generators-defaults) is one.
The window and the default config are part of the `generation_cache` key, so
two generators with different defaults never answer from each other's cache.

!!! warning "The cut now leaves a `WARNING` in the log"
    The daemon does not say it cut, but it leaves a trace: the truncated
    prompt comes back with `prompt_eval_count` at **half the window plus 2
    or 3 tokens**, whatever the size of what you sent. Measured on Ollama
    0.30.11 on CPU (`qwen2.5:0.5b`, `num_gpu=0`, prompts of 1,000 to
    30,000 words, all larger than the window): 1026 at `num_ctx` 2048, 2050
    at the default 4096, 4098 at 8192 and 8194 at 16384; the 2026-11-26/27 study saw 2050 (`qwen2.5:3b`,
    `qwen2.5-coder:14b`) and 2051 (`lfm2.5-thinking:1.2b`,
    `ministral-3:14b`) at the default. A prompt that fits comes back with its
    own size (1243 at `num_ctx` 2048).

    So `OllamaGenerator` logs a `WARNING` on the
    `tempest_fastapi_sdk.genai.ollama` logger when `prompt_eval_count` lands
    between half the window and half plus 16 (or reaches the whole window),
    with the model, the count and the window in the text and in `extra`:

    ```text
    WARNING tempest_fastapi_sdk.genai.ollama: Ollama model 'qwen2.5:0.5b'
    evaluated 2050 prompt tokens with a 4096-token context window: an
    overflowing prompt is cut to about half the window without an error, so
    this prompt was likely truncated. Raise num_ctx on the generator.
    ```

    - The window is the request's `num_ctx` when it has one (from the
      constructor or the call). Without it, it is the `context_length` the
      daemon's `/api/ps` reports for the loaded model — looked up once per
      generator, and only after a reply that carries `prompt_eval_count`. If
      the lookup fails the check is skipped; the call never fails over it.
    - It covers `generate`, `chat`, `chat_with_tools` (the `Agent` loop),
      `stream` and both structured calls.
    - The `num_ctx` default did **not** change: it is still `None`, inheriting
      the daemon's window (4096 without `OLLAMA_CONTEXT_LENGTH`). A bigger
      window costs VRAM, and that is the call of whoever runs the service;
      the warning is what makes the cut visible. A prompt that lands exactly
      in the band without being cut logs a false warning — rare, and the
      remedy is the same.

### Fail instead of cutting: `truncate`

The warning makes the cut visible, but the call still runs on the cut
prompt. In document extraction, in an agent with a long history or in RAG
with many passages, answering over half the context is worse than an error.
For those cases pass `truncate=False`: the daemon refuses a prompt that does
not fit, and `OllamaGenerator` raises `OllamaContextExceededError`.

```python
import asyncio

from tempest_fastapi_sdk.genai import OllamaContextExceededError, OllamaGenerator

gen = OllamaGenerator("qwen2.5:0.5b", num_ctx=2048, truncate=False)


async def main() -> None:
    """Run this example."""
    document: str = " ".join(f"word{i}" for i in range(4000))
    try:
        await gen.chat([{"role": "user", "content": document}])
    except OllamaContextExceededError as exc:
        print(exc.prompt_tokens, exc.context_window)


asyncio.run(main())
```

What the daemon answers was measured on Ollama 0.30.11 on CPU
(`qwen2.5:0.5b`, `num_gpu=0`, `num_ctx` 2048, a 4,000-word prompt), on
`/api/chat` and `/api/generate`, with and without `stream`:

| `truncate` in the body | status | result |
| --- | --- | --- |
| absent (the default) | `200` | prompt cut: `prompt_eval_count` 1026 |
| `true` | `200` | same as absent: `prompt_eval_count` 1026 |
| `false` | `400` | `exceed_context_size_error`, before any stream line |

The `400` body carries both sizes, and they reach the exception:

```text
OllamaContextExceededError: Ollama model 'qwen2.5:0.5b' refused a prompt
longer than its context window (the prompt has 18923 tokens and the context
window 2048) because truncate=False; raise num_ctx or shorten the prompt
```

- **`truncate=None`** (the default) sends no field: the daemon cuts, as
  before, and the `WARNING` above still applies.
- A call's `truncate` wins over the constructor's, and it works on the same
  six methods as `think` (`generate`, `chat`, `chat_with_tools`, `stream`,
  `generate_structured`, `chat_structured`). It is part of the
  `generation_cache` key: a reply cached from a cut prompt does not answer a
  `truncate=False` call.
- `prompt_tokens` and `context_window` are `None` when the error body does
  not carry the sizes; the exception is still `OllamaContextExceededError`.
- On `stream`, the exception comes out of the first iteration: the daemon
  refuses before sending any piece.
- On a prompt that fits, `truncate=False` changes nothing (measured: `200`,
  `prompt_eval_count` 32, on both endpoints).

### Reasoning: `think`

A reasoning model (`gpt-oss`, `ornith`) takes the `think` field at the
**top level** of the `/api/chat` and `/api/generate` body: `True`/`False`
switches reasoning on or off, and `"low"`/`"medium"`/`"high"` picks the
level on models that have levels. Inside `options` the daemon ignores the
field — and that is where a `think=` passed as a keyword landed before this change. It is
now a parameter of the generator (`ThinkLevel`) and of every call:

```python
import asyncio

from tempest_fastapi_sdk.genai import OllamaGenerator

gen = OllamaGenerator("gpt-oss:20b", think="low", num_ctx=16384)


async def main() -> None:
    """Run this example."""
    quick: str = await gen.chat([{"role": "user", "content": "Hi"}])
    careful: str = await gen.chat(
        [{"role": "user", "content": "What is 17 x 23?"}],
        think="high",
    )
    print(quick, careful)


asyncio.run(main())
```

- **`think=None`** (the default) sends no field: the model uses its own
  default, as before.
- A call's `think` wins over the constructor's, and is part of the
  `generation_cache` key.
- `generate`, `chat`, `chat_with_tools`, `stream`, `generate_structured`
  and `chat_structured` take the parameter; the `Agent` loop uses the
  constructor's.

Measured in the 2026-11-26/27 local-LLM study (Ollama 0.30.11, RTX 4070 Ti
SUPER, temperature 0):

| model / use | `think` | score | p50 |
| --- | --- | --- | --- |
| `gpt-oss:20b`, 34 tool tasks | `"low"` | 97.1% | **1.44 s** |
| `gpt-oss:20b`, 34 tool tasks | default (medium) | 94.1% | 1.68 s |
| `gpt-oss:20b`, 34 tool tasks | `"high"` | 97.1% | 3.59 s |
| `gpt-oss:20b`, RAG over 27 questions | `"low"` | 20/21 facts, 0/6 hallucinations | **0.99 s** |
| `ornith-1.5:9b`, 33 structured extractions | `False` | 93.3 per field | **1.34 s** |
| `ornith-1.5:9b`, 33 structured extractions | `True` | 95.0 per field | 5.60 s |

At N=34 and N=33 a point or so is one item: do not read a fine ranking
between `"low"` and `"high"`. What the table shows is time — `"low"` tied
with the other levels and, at the report's p50, took 25% to 40% of the
`"high"` time and 47% to 86% of the `"medium"` (default) time, depending on
the use.

!!! warning "A model without reasoning refuses `think`"
    A model without the `thinking` capability (in `/api/show`) answers `400`
    with `"<model>" does not support thinking` — reproduced on Ollama
    0.30.11 with `ministral-3:14b` and `think=True`, on `/api/chat` and
    `/api/generate`, streamed or not, without loading the model. The
    generator raises `OllamaThinkingUnsupportedError` (an `OllamaError`
    subclass, with `.model` and `.detail`) instead of the raw
    `httpx.HTTPStatusError`. For that model, leave `think=None`.

!!! danger "`think=False` makes some models ignore `format`"
    With `think=False`, `ornith:35b` answered a `chat_structured` call in
    markdown (`**Nome:** Ana Costa ...`), with `200`, in 3 of 3 calls; with
    `think` omitted or `True` it returned the JSON object 3 of 3.
    `ornith-1.5:9b`, `gpt-oss:20b` and `ministral-3:14b` honoured `format`
    with `think=False` (3 of 3 each). When the structured reply carries no
    JSON object at all, `generate_structured` and `chat_structured` raise
    `OllamaFormatIgnoredError` — a subclass of `OllamaError` **and** of
    `ValueError`, so an existing `except ValueError` still catches it — with
    `.content` (the start of the reply) and `.think`, and the message points
    at `think=False` when that was it. Before, the same case was a
    `ValueError("no JSON object ...")` with no hint at the cause.

## Embeddings via Ollama + RAG

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

### `nomic-embed-text` task prefixes

`nomic-embed-text` was trained with `search_query: ` in front of the question
and `search_document: ` in front of the passage. Declare both on
`OllamaEmbedder` — or splat the `NOMIC_EMBED_TEXT_PREFIXES` preset — and the
`Retriever` applies each one on the right side:

```python title="nomic_rag.py" hl_lines="11"
import asyncio

from tempest_fastapi_sdk.genai import NOMIC_EMBED_TEXT_PREFIXES, OllamaEmbedder
from tempest_fastapi_sdk.genai.rag import Chunk, InMemoryVectorStore, Retriever


async def main() -> None:
    """Index two passages and search them with nomic's task prefixes."""
    embedder = OllamaEmbedder(
        "nomic-embed-text",
        **NOMIC_EMBED_TEXT_PREFIXES,
    )
    rag = Retriever(embedder, InMemoryVectorStore())
    await rag.index(
        [
            Chunk(text="Refunds land within 7 business days.", source="faq", index=0),
            Chunk(text="Express delivery arrives tomorrow.", source="faq", index=1),
        ],
    )
    hits = await rag.search("How long does a refund take?", top_k=1)
    print(hits[0].text)
    await embedder.aclose()


if __name__ == "__main__":
    asyncio.run(main())
```

`Retriever.index` calls `embed_passages`, which sends
`search_document: <passage>` to the daemon; `Retriever.search` calls
`embed_query`, which sends `search_query: <question>`. Plain `embed()` stays
unprefixed, and both parameters default to `""` — if you declare nothing, you
keep the vectors you had.

You pick the preset; it is not guessed from the model name: the same family
ships under several tags (`nomic-embed-text`, `nomic-embed-text:v1.5`, a
local fine-tune), and a wrong guess would silently change every stored
vector. For the E5 family there is `MULTILINGUAL_E5_PREFIXES`
(`query: ` / `passage: `), which also works on the torch `Embedder`. For any
other model, pass `query_prefix=` / `passage_prefix=` with the strings from
its model card.

What it buys: in a measurement of 21 PT-BR questions over a fictional
handbook (`nomic-embed-text` via Ollama, one 2000-character chunk per
document), the right document was among the top 4 in **18/21** without the
prefix and **20/21** with it; k=1 and k=2 did not change (15/21 and 17/21).
With 300-character chunks the prefix changed nothing (18/21 both ways).
N=21 is small: it is a signal, not a rate.

!!! warning "Changing the prefix means reindexing"
    The prefix is part of the text that becomes a vector, so turning
    `query_prefix` / `passage_prefix` on, off or changing them changes
    **every** vector. An index written without a prefix and queried with one
    (or the other way around) compares vectors from different spaces and
    returns worse results with no error at all. When you change the
    prefixes, drop the index and run `Retriever.index` again over the whole
    corpus.

## Hybrid search (BM25 + dense)

Dense search captures meaning but misses exact terms — proper nouns, codes,
acronyms a query shares verbatim with a chunk. Sparse BM25 nails those and
ignores semantics. `HybridRetriever` runs both over the same indexed chunks and
fuses their rankings with **Reciprocal Rank Fusion** — so "what does BACEN do?"
finds the chunk that says "BACEN" even when the dense score is lukewarm. BM25
comes from `rank-bm25` (the `[genai-rag]` extra).

```python
import asyncio

from tempest_fastapi_sdk.genai import Embedder
from tempest_fastapi_sdk.genai.rag import (
    HybridRetriever,
    InMemoryVectorStore,
    chunk_text,
)

rag = HybridRetriever(
    Embedder("sentence-transformers/all-MiniLM-L6-v2", normalize=True),
    InMemoryVectorStore(),
)


async def main() -> None:
    """Run this example."""
    chunks = chunk_text(
        "The CNPJ identifies a legal entity at the Receita Federal.",
        source="faq.md",
    )
    await rag.index(chunks)                           # indexes dense + BM25
    hits = await rag.search("what is CNPJ?", top_k=5)  # fuses dense + sparse
    print(hits)


asyncio.run(main())
```

`search(query, top_k, candidates)` takes `candidates` from each side and fuses
to `top_k`. `reciprocal_rank_fusion(rankings, k=60)` is exposed standalone to
fuse arbitrary rankings. The BM25 index is in-memory (rebuilt on each `index`)
— good up to a few tens of thousands of chunks. It also has
`retrieve(query, top_k)` (hybrid search → context block), so `HybridRetriever`
satisfies `SupportsRetrieve` and drops into `make_genai_router(retriever=...)`
in place of a `Retriever`.

!!! info "Each `index` replaces the sources it names"
    A chunk is identified by `(source, index, text)` — not by
    `(source, index)` alone, which `chunk_text` repeats on every call (it
    restarts `index` at 0). And each `index(chunks)` **replaces** whatever
    was already indexed for the batch's `source`s: re-indexing an edited
    document leaves neither the old version nor a tail of it answering
    searches. So pass **every** chunk of a source in the same call.
    `InMemoryVectorStore`, `PgVectorStore` and `ChromaVectorStore` apply the
    same replacement on the dense side; a `VectorStore` of your own that
    only appends keeps returning the old rows, and `HybridRetriever` drops
    them before fusion (they only cost `candidates` slots).

    On the sparse side, only chunks sharing at least one term with the query
    enter the fusion. With no overlap BM25 scores every chunk 0, and a list
    of zeros "sorted by score" is just insertion order — added into RRF, it
    inverted the dense ranking.

## Reranking (cross-encoder)

Dense search (embed the query, embed the chunks, cosine) is fast but coarse:
it never sees query and chunk together. A **cross-encoder** scores each
`(query, chunk)` pair jointly — too slow for the whole corpus, ideal as a
second stage over the top-N candidates. Inject a `Reranker` into the
`Retriever`: search over-fetches candidates from the store and the
cross-encoder narrows them to `top_k`.

```python
import asyncio

from tempest_fastapi_sdk.genai import Embedder
from tempest_fastapi_sdk.genai.rag import InMemoryVectorStore, Reranker, Retriever

rag = Retriever(
    Embedder("sentence-transformers/all-MiniLM-L6-v2", normalize=True),
    InMemoryVectorStore(),
    reranker=Reranker("cross-encoder/ms-marco-MiniLM-L-6-v2"),
)


async def main() -> None:
    """Run this example."""
    # search fetches max(top_k, rerank_candidates) from the store, then reorders:
    chunks = await rag.search("how to refund?", top_k=5, rerank_candidates=20)


asyncio.run(main())
```

Without a `reranker` the `Retriever` stays dense-only. `Reranker` (the
`[genai]` extra) has lazy load + `unload`/`unload_if_idle` like
`TextGenerator`.

## Same router, torch OR Ollama

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
