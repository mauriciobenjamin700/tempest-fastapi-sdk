# Moderation, usage, metrics and cache

Use in production: moderate content, account usage per user, expose metrics and tracing, count tokens and cache generations.

Part of the [Self-hosted generative AI](https://mauriciobenjamin700.github.io/tempest-fastapi-sdk/en/recipes/genai.md) recipe.

## Content moderation

Screen user prompts and model outputs with a pluggable moderation layer. Two
backends satisfy `ModerationBackend` and return a `ModerationResult`
(`flagged`, `categories`, `score`):

```python
import asyncio

from tempest_fastapi_sdk.genai import RuleModerator

user_input = "Explique PIX em uma frase."


mod = RuleModerator(["slur", "banned-term"], category="abuse")


async def main() -> None:
    """Run this example."""
    verdict = await mod.check(user_input)
    if verdict.flagged:
        ...   # block or annotate, per your policy


asyncio.run(main())
```

`RuleModerator` is dependency-free and predictable (whole-word,
case-insensitive block-list) — the deterministic floor. Before matching,
both term and text go through NFKC, lose their format characters
(zero-width space, bidi marks) and are casefolded — so `"se"` + U+200B +
`"cret"` and the fullwidth spelling still hit `"secret"`. "Whole word"
means "no word character glued on either side", which is what makes terms
with punctuation at the edge (`"$hit"`, `"c++"`) work. Homoglyphs from
another script (Cyrillic `е` for `e`) are **not** folded: list those
spellings in the block list. `ClassifierModerator`
runs a local classifier (e.g. `unitary/toxic-bert`) over transformers
(`[genai]`), lazy, with `flagged_labels` / `threshold`. PT-BR toxicity-model
quality varies — treat the classifier as best-effort and keep `RuleModerator`
as the baseline.

### The classifier: independent labels, the whole text

`unitary/toxic-bert` is **multi-label**: `toxic`, `obscene` and `insult` are
separate yes/no questions, and one text can be all three. `ClassifierModerator`
reads that from the model config (`problem_type =
"multi_label_classification"`) and scores each label with a **sigmoid**. A
model without that declaration keeps the softmax; `activation="sigmoid"` or
`"softmax"` forces either one.

It also never truncates. The text is cut into overlapping windows of 64
tokens (`window_tokens=64`, `window_overlap=16`), each window is classified,
and every label keeps its highest score across windows:

```python
import asyncio

from tempest_fastapi_sdk.genai import ClassifierModerator, ModerationResult

mod = ClassifierModerator(
    "unitary/toxic-bert",
    flagged_labels=["toxic", "insult"],
    threshold=0.5,
)

prefix = "The weather report says it will be sunny and mild. " * 60
text = prefix + "You are a stupid idiot and I hate you."


async def main() -> None:
    """Screen a long text whose insult sits after 660 harmless tokens."""
    verdict: ModerationResult = await mod.check(text)
    print(verdict.flagged, verdict.categories)


asyncio.run(main())
```

Output: `True ['toxic', 'insult']` — the same verdict the sentence gets on its
own, 660 tokens later.

!!! info "Why sigmoid: what the softmax did"
    Measured with `unitary/toxic-bert` (revision `4d6c22e`), all six labels
    in `flagged_labels`, `threshold=0.5`, over a fixed set of 10 insulting
    English sentences and 5 clean ones:

    | | Sigmoid (now) | Softmax (before) |
    | --- | --- | --- |
    | insulting sentences flagged | 10 of 10 | 10 of 10 |
    | labels reported across them | 31 (`toxic` + `obscene` + `insult` on each, `threat` on one) | 10 (`toxic` only, every time) |
    | clean sentences flagged | 0 of 5 | 1 of 5 |

    With the softmax the six scores add up to 1, so labels compete for the
    same mass: `insult` never crossed 0.5 next to `toxic`, and a policy
    keyed on `insult` saw nothing. It also fails the other way — every
    score of *"Could you send me the invoice for last month?"* is tiny, the
    softmax makes them add up to 1 anyway, and `toxic` came out at 0.556.

!!! info "Why 64-token windows: the model dilutes"
    Before, `truncation=True` classified only the first 512 tokens. With
    about 650 harmless tokens in front, the same insult scored `toxic` at
    0.001 — prefixing filler was enough to get through.

    Windows as wide as the model's context (512) do not fully close that
    hole, because the classifier itself dilutes one sentence inside a lot of
    benign text: in a single window, the insult behind 130 tokens of
    filler was still caught in 9 of 10 sentences, behind 260 tokens in 3 of
    10, behind 490 tokens in none. Measured over 40 excerpts of this
    site's English prose (1801 to 3030 tokens each), with one of the 10
    sentences inserted at a random word (seed 316), `window_overlap` at a
    quarter of the window:

    | `window_tokens` | insult caught | clean excerpt flagged | ms per check |
    | --- | --- | --- | --- |
    | 512 | 1 of 40 | 0 of 40 | 41 |
    | 256 | 6 of 40 | 0 of 40 | 36 |
    | 128 | 23 of 40 | 0 of 40 | 41 |
    | 96 | 28 of 40 | 0 of 40 | 66 |
    | **64** (default) | **36 of 40** | 0 of 40 | 62 |
    | 48 | 39 of 40 | 0 of 40 | 80 |
    | 32 | 40 of 40 | 0 of 40 | 139 |

    The latency column is from an RTX 4070 Ti SUPER on a host under heavy
    load from other jobs: read the ratio between rows, not the absolute
    value. A text that fits in one window (up to 62 tokens here) costs the
    same single pass as before.

!!! tip "When to change the window"
    A shorter window sees less of the sentence around each word. The table
    above does not measure what that costs for a policy that depends on
    context (irony, quoting, negation) — if yours does, validate a larger
    `window_tokens` on your own data. `window_tokens=None` uses the model's
    whole context, and any value is capped there (512 for BERT).

## Per-user usage accounting (a table)

`GenAIMetrics` above answers "how is the fleet doing right now". It does
**not** answer "which account burned the budget last month": the series is
per-process, resets on deploy, and carries no user dimension — adding one
would make the cardinality unusable.

That question wants the other shape: **one row per paid call**, in a table
you query with ordinary SQL.

```python
# src/services/ai_usage.py
from uuid import UUID

from tempest_fastapi_sdk.db import AsyncDatabaseManager
from tempest_fastapi_sdk.genai import AIUsageStore, BaseAIUsageModel, TokenUsage


class AIUsageModel(BaseAIUsageModel):
    """One billed AI call in this application."""

    __tablename__ = "ai_usage"


db = AsyncDatabaseManager("sqlite+aiosqlite:///./app.db")
store: AIUsageStore[AIUsageModel] = AIUsageStore(
    db,
    model=AIUsageModel,
    price_input_per_1k=0.00014,
    price_output_per_1k=0.00028,
)


async def record(user_id: UUID, usage: TokenUsage | None) -> None:
    """Store what one call consumed.

    Args:
        user_id (UUID): Who pays for it.
        usage (TokenUsage | None): What the provider reported.
    """
    await store.record(subject_id=user_id, service="summary", usage=usage)
```

And the aggregations an admin screen draws:

```python
# src/services/ai_usage.py
from datetime import timedelta

from tempest_fastapi_sdk.db import AsyncDatabaseManager
from tempest_fastapi_sdk.genai import (
    AIUsageStore,
    BaseAIUsageModel,
    ServiceUsage,
    SubjectUsage,
    UsageTotals,
)


class AIUsageModel(BaseAIUsageModel):
    """One billed AI call in this application."""

    __tablename__ = "ai_usage"


db = AsyncDatabaseManager("sqlite+aiosqlite:///./app.db")
store: AIUsageStore[AIUsageModel] = AIUsageStore(db, model=AIUsageModel)


async def dashboard() -> tuple[UsageTotals, list[ServiceUsage], list[SubjectUsage]]:
    """Read what the admin screen shows.

    Returns:
        tuple[UsageTotals, list[ServiceUsage], list[SubjectUsage]]: Totals,
        the per-service split, and who spent the most.
    """
    window = timedelta(days=14)
    return (
        await store.totals(window),
        await store.by_service(window),
        await store.top_subjects(window, limit=20),
    )
```

!!! danger "A call the provider did not price writes no row"
    `record(usage=None)` stores **nothing**. "The provider did not say" is
    not "the call was free": a zeroed row would count toward the call count
    and toward "active users" while contributing no tokens, which is worse
    than not counting it. `TokenUsage(0, 0, 0)` writes nothing either — it
    is what a short-circuit that never reached the model produces.

!!! info "The price is never stored"
    Cost is computed from the tokens at read time, so correcting a price
    fixes the whole history — no reprocessing, and no rows disagreeing
    about what a token was worth.

!!! warning "Cost comes back unrounded"
    Any fixed precision is wrong at some scale: token prices live around
    `0.0001` per 1000, so rounding to cents reports zero for nearly every
    single call, while a monthly total wants cents. Formatting stays at the
    boundary, which knows which of the two it is showing. `cost is None`
    means "show no cost", never zero.

!!! tip "A cached prefix has its own price"
    `price_cache_hit_per_1k` is the reduced rate for tokens the provider
    served from a cached prefix, and `estimate_cost` bills the
    `cache_hit_tokens` slice at it instead of the input rate.

    The default is `None`, not `0.0`, and the difference matters: `0.0`
    **is** a price, so an unconfigured store would start giving the prefix
    away for free and understate every repeated call. `None` means "there
    is no separate rate" and leaves the slice at the normal input price —
    an absent setting should never move a number.

    The `cache_hit_tokens` column is new on `BaseAIUsageModel`: generate
    the migration (`tempest db revision`) before deploying. It is nullable
    on purpose — rows written before it existed do not know their own
    split, and claiming zero there would bill a discounted call at full
    rate.

!!! tip "Local inference is recorded by duration"
    `record_duration(subject_id=..., seconds=...)` is for a model running
    on your own hardware: there is no token bill, what it consumes is
    wall-clock. Those rows carry `service=NULL` and are excluded from token
    sums, so they never become a 0% slice on every chart.

## Inference metrics (Prometheus)

`GenAIMetrics` bundles the counters + histogram every inference service ends up
reimplementing — requests, latency and tokens in/out, labelled by model and
operation, with requests + latency also labelled by outcome (`status="ok"` or
`"error"`). It reuses `prometheus-client` (the `[prometheus]` extra) and takes an
explicit `registry` (composes with the SDK's `PrometheusMiddleware` /
`/metrics`). It is **opt-in**:

```python
import asyncio

from tempest_fastapi_sdk.genai import GenAIMetrics, OllamaGenerator

metrics = GenAIMetrics()
gen = OllamaGenerator("llama3.2", metrics=metrics)


async def main() -> None:
    """Run this example."""
    await gen.generate("Explain PIX.")   # records request + latency + tokens


asyncio.run(main())
```

!!! warning "The `status` label is new"
    Before, a call that raised counted as an ordinary request — a failing
    backend read as healthy traffic. `genai_requests_total` and
    `genai_request_seconds` now carry `status`; a `{model, op}` selector
    still matches, but a query comparing the whole series (or
    `get_sample_value` with the exact label set) must sum by `status` or
    filter `status="ok"`.

`OllamaGenerator`, `TextGenerator` and `Embedder` accept `metrics=` and record
request + latency (Ollama also reads `prompt_eval_count` / `eval_count` from the
response into the token counters). For any other call, wrap it with the context
manager and set the tokens you know:

```python
import asyncio

from tempest_fastapi_sdk.genai import GenAIMetrics

metrics = GenAIMetrics()


async def main() -> None:
    """Run this example."""
    async with metrics.track("my-model", "generate") as span:
        span.tokens_out = 128
        ...  # run the model


asyncio.run(main())
```

## Distributed tracing (OpenTelemetry)

Beyond metrics, genai calls emit **OpenTelemetry spans** — with no per-generator
setup. Call `setup_tracing` once at startup (`[otel]` extra) and the spans start
flowing next to the FastAPI / SQLAlchemy / httpx ones:

```python
from fastapi import FastAPI

from tempest_fastapi_sdk.api.tracing import setup_tracing

app: FastAPI = FastAPI()
setup_tracing(app, service_name="my-service", otlp_endpoint="localhost:4317")
```

From then on, `TextGenerator` / `OllamaGenerator` (`generate` / `chat`),
`Embedder.embed`, and the RAG `Retriever.search` / `.retrieve` open a span
automatically. Spans follow the OpenTelemetry **GenAI semantic conventions**
(`gen_ai.system`, `gen_ai.operation.name`, `gen_ai.request.model`, plus
`gen_ai.usage.input_tokens` / `gen_ai.usage.output_tokens` on the Ollama path);
an exception marks the span `ERROR` and records it.

!!! info "Zero-config, zero-cost by default"
    It is **ambient**: it reuses the global `TracerProvider` from
    `setup_tracing`. Without the `[otel]` extra the helper is a no-op; with the
    extra but no provider configured, the global tracer produces non-recording
    spans. Nothing is injected into the generators.

To instrument your own call, use the same context manager:

```python
import asyncio

from tempest_fastapi_sdk.genai import genai_span


async def main() -> None:
    """Run this example."""
    async with genai_span("generate", "my-model") as span:
        span.tokens_out = 128
        ...  # run the model


asyncio.run(main())
```

## Token counting and context window

To fit a chat into the model's window, count tokens with the **model's own
tokenizer** (never a heuristic — BPE and SentencePiece disagree) and drop the
oldest turns when it overflows:

```python
from transformers import AutoTokenizer

from tempest_fastapi_sdk.genai import (
    TextModelEnum,
    count_tokens,
    truncate_messages,
)

messages = [{"role": "user", "content": "oi"}]
tokenizer = AutoTokenizer.from_pretrained(TextModelEnum.QWEN2_5_7B_INSTRUCT)


n = count_tokens("Explain PIX.", tokenizer)   # the model's own tokenizer

fit = truncate_messages(
    messages, max_tokens=3000, tokenizer=tokenizer,
)   # keeps system + last turn, drops the oldest
```

`count_message_tokens(messages, tokenizer, per_message_overhead=4)` sums the
chat cost; `truncate_messages` keeps `system` messages (moved to the front) and
the most recent turn, dropping older ones until it fits. An `assistant` turn
with `tool_calls` and the `tool` turns answering it are dropped **together** —
the history never starts with a tool result whose call was cut. Both work over
any tokenizer with `encode(text) -> sequence` (`AutoTokenizer` qualifies).
## Generation cache (prompt → completion)

**Deterministic** generations (greedy, or `temperature=0`) always produce the
same text for the same prompt + params — so they can be cached and skip the
model on a repeat. Pass a cache to the generator; only deterministic calls are
cached (sampling never is, to avoid returning a stale sample):

```python
import asyncio

from tempest_fastapi_sdk.genai import (
    GenerationConfig,
    InMemoryGenerationCache,
    OllamaGenerator,
)

gen = OllamaGenerator("llama3.2", generation_cache=InMemoryGenerationCache())
cfg = GenerationConfig(temperature=0)   # deterministic → cacheable


async def main() -> None:
    """Run this example."""
    await gen.generate("Explain PIX.", config=cfg)   # runs the model
    await gen.generate("Explain PIX.", config=cfg)   # served from cache


asyncio.run(main())
```

`InMemoryGenerationCache` is process-local and an LRU of 1024 completions by
default (`max_entries=`; `None` removes the bound); `RedisGenerationCache` (the
`[cache]` extra) shares across workers — the generator awaits the sync-or-async
cache at one call site. Same on `TextGenerator` (`generation_cache=...`) and on
both backends' `chat`. Invalidate by dropping the key (or via a Redis TTL).

The key separates the call shape (`chat` never answers a `generate` whose
prompt is the JSON of the same messages) and, on `TextGenerator`, the weight
identity: `revision` and `quantization` are part of the key, so two instances
of one `model_id` loaded differently never read each other's cache. A
`generate` with neither `revision` nor `quantization` keeps its previous key,
so an already-warm Redis stays warm.
