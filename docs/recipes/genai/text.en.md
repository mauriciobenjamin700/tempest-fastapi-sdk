# Generate text (local and hosted)

Use to generate text with a local LLM (`TextGenerator`) or an OpenAI-compatible backend (DeepSeek, Groq, OpenRouter, vLLM), with per-call cost and `extra_body`.

Part of the [Self-hosted generative AI](../genai.md) recipe.

## Generate text with a local LLM

`TextGenerator` loads a HuggingFace causal LM **once** and generates on
your hardware. It resolves device and precision itself, supports int4/int8
quantization, loads the weights lazily (on first call), and frees VRAM
when idle. Needs `[genai]` (and `[genai-quant]` to quantize).

```python
import asyncio

from tempest_fastapi_sdk.genai import TextGenerator

gen = TextGenerator(
    "Qwen/Qwen2.5-7B-Instruct",
    quantization="int4",            # fits a modest GPU; None = full precision
    idle_unload_seconds=300,        # free VRAM after 5 min idle
)


async def main() -> None:
    """Run this example."""
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


asyncio.run(main())
```

!!! info "Weights download once — then it is a disk cache"
    The first call writes the gigabytes to `$HF_HOME/hub` (or
    `~/.cache/huggingface/hub`); later runs read them from there, no network.
    In a **container with no volume** that is lost on every restart. Pointing
    the cache somewhere durable, pinning the revision, pre-downloading at
    deploy time and running offline are all in
    **[Model weights »](../model-weights.md#where-the-weights-live-and-why-the-second-run-is-instant)**.

Blocking generation runs in `asyncio.to_thread` — it never blocks the
event loop. That holds for `stream()` too: the first call's load and the wait
between tokens happen on a worker thread, and closing the iterator early (a
`break`, a client that disconnected) stops generation at the next token
instead of leaving the GPU generating up to `max_new_tokens` for nobody.
Measured with `Qwen/Qwen2.5-0.5B-Instruct` on an RTX 4070 Ti SUPER: the first
stream used to stall the loop for 4190 ms and now stalls it for 164 to
193 ms (three runs); closing after 5 of 200 tokens used to block for 3532 ms
and now returns in ~0.01 ms, with the worker thread done 28 ms later.

`device="auto"` picks CUDA → MPS → CPU; `dtype="auto"` uses
bf16 on GPU and fp32 on CPU.

!!! tip "Check before loading"
    Pair it with the [capacity check](hardware.md#can-the-machine-handle-it): run
    `can_run` / `recommend` to pick a `quantization`/`device` that **fits**
    before instantiating the `TextGenerator`.

!!! tip "Free VRAM between bursts"
    With `idle_unload_seconds` set, call `gen.unload_if_idle()` periodically
    (e.g. in a `@tq.interval(60)` [TaskQueue](../queue-tasks.md) task) — it
    unloads only once past the idle threshold, no background-thread magic.
    `unload()` frees immediately.

    A call in progress does **not** count as idle: `seconds_idle` reads
    `0.0` while it runs and `unload_if_idle()` returns `False`. An
    `unload()` that arrives mid-call (including one from a `ModelRegistry`
    eviction) waits: whichever call finishes last drops the weights. And
    several simultaneous first calls load the model **once**. The same holds
    for every local loader (`Embedder`, `Reranker`, `ImageGenerator`,
    `SpeechToText`, `TextToSpeech`, `SpeakerDiarizer`, `VoiceEmbedder`,
    `OnnxEmbedder`...) and for the [face recognition](../faces.md)
    `FaceRecognizer`.

### Several requests at once on CPU: `max_concurrent`

Every call runs on a worker thread, and on CPU the `model.generate` of
**one** thread already uses torch's whole intra-op pool. With no limit, four
agents answering at once become four threads fighting over the same cores:
nobody finishes early, and everybody finishes late. `max_concurrent` queues
the extra requests instead:

```python
import asyncio

from tempest_fastapi_sdk.genai import TextGenerator

gen = TextGenerator(
    "Qwen/Qwen2.5-0.5B-Instruct",
    device="cpu",
    max_concurrent=1,               # one decode at a time; the rest wait in line
)


async def main() -> None:
    """Run this example."""
    replies = await asyncio.gather(
        *(gen.generate(f"Give a Python tip, number {n}.") for n in range(4))
    )
    for reply in replies:
        print(reply)


asyncio.run(main())
```

Measured with `Qwen/Qwen2.5-0.5B-Instruct` on CPU (WSL2 on an i9-13900F, 12
visible logical CPUs = 6 cores × 2, torch 2.14 with 6 intra-op threads), 128
forced tokens (`min_new_tokens` = `max_new_tokens`), `do_sample=False`, each
configuration in its own process, median of 5 rounds:

| Concurrent calls | `max_concurrent` | Throughput | Median latency | Max latency |
| --- | --- | --- | --- | --- |
| 1 | `None` | 28.3 tokens/s | 4.53 s | 4.53 s |
| 2 | `None` | 24.6 tokens/s | 10.26 s | 10.42 s |
| 2 | `1` | 26.7 tokens/s | 7.27 s | 9.60 s |
| 2 | `2` | 27.7 tokens/s | 9.13 s | 9.24 s |
| 4 | `None` | 22.4 tokens/s | 22.73 s | 22.85 s |
| 4 | `1` | 26.9 tokens/s | 11.91 s | 19.03 s |
| 4 | `2` | 25.6 tokens/s | 14.80 s | 19.97 s |

With four requests and `max_concurrent=1`, throughput is back to 95% of a
lone call's, the **median latency halves**, and the last one still
finishes earlier than it would with no limit. A lone call does not change:
over two re-runs of 9 rounds, 4.56 s against 4.57 s and 4.66 s against
4.65 s.

!!! info "Why a queue of dedicated threads, and not a semaphore"
    Torch's OpenMP runtime keeps **one worker team per thread that calls**
    the model, and asyncio's default executor hands each call to whichever
    of its threads is free. Measured on the same hardware, decoding strictly
    one at a time: 4.5 s per call while everything ran on the same thread,
    6.4 s per call after alternating across four live threads — each new
    thread added 6 threads to the process, and the slowdown stayed even
    after going back to the first one. A semaphore in front of the default
    executor serialized calls **and** made each one slower: four concurrent
    calls finished at 18.5 tokens/s, below the 22.8 with no limit at all
    measured in the same process.
    That is why `max_concurrent=N` builds a pool of **N threads owned by the
    generator**, and decoding never leaves them. (`OMP_WAIT_POLICY=PASSIVE`
    does not fix it: it put the lone call at 6.7 s.)

The default stays `None` (no limit, every call starts right away), so an SDK
upgrade does not change anyone's concurrency — the trade-off was not measured
on GPU. On CPU, start with `max_concurrent=1`. A request cancelled while
waiting in line leaves it without running; one cancelled while decoding
stops at the next token and only frees its place once the thread has really
finished. It covers `generate`, `chat`, `chat_with_tools`,
`generate_structured`, `chat_structured` and `stream` (which holds its place
until the last token, or until one token after you close the iterator).
`VisionTextGenerator` takes the same `max_concurrent=`.

!!! note "What about the thread count?"
    There is no thread knob on `TextGenerator`, on purpose:
    `torch.set_num_threads` is **process-wide**, not per generator, and
    torch's default here was already the number of visible physical cores
    (6). With `max_concurrent=1` the throughput under load stayed within 5%
    of a lone call's, so there was little left to gain. If you need to
    change it, call `torch.set_num_threads(n)` once at startup, knowing it
    applies to every model in the process.

## Hosted backend (DeepSeek, Groq, OpenRouter, vLLM...)

!!! tip "Anthropic, OpenAI and DeepSeek"

    This section covers `OpenAICompatGenerator` itself. For Claude (`AnthropicGenerator`), tool calling, structured output, embeddings, settings (`HostedGenAISettings`) and typed errors, see [Hosted generative AI](../genai-hosted.md).

Not every service wants to hold weights: sometimes the budget that matters
is **cost per token**, not RAM. OpenAI's `/chat/completions` format became
the common denominator — DeepSeek, Groq, Together, OpenRouter, Mistral,
vLLM's server, TGI's OpenAI route and Azure all speak it — so one client
reaches all of them by swapping `base_url` and `model`.

```python
import asyncio

from tempest_fastapi_sdk.genai import OpenAICompatGenerator

gen = OpenAICompatGenerator(
    "deepseek-flash",
    api_key="sk-...",                        # never hardcoded: read it from settings
    base_url="https://api.deepseek.com",
)


async def main() -> None:
    """Run this example."""
    text: str = await gen.generate("Explain PIX in one sentence.")
    print(text)


asyncio.run(main())
```

It satisfies `TextBackend`, so it drops into `make_genai_router` and
`AIChatPipeline` in place of `TextGenerator` or `OllamaGenerator` with no
other change. An empty key raises `ValueError` **at construction** — the
mistake is a configuration one, and it surfaces where the configuration is
read rather than as a 401 inside the first background job.

### What the call cost

`generate_with_usage` returns the text **and** the usage the provider
itself reported. That is the number being billed, so it is the one worth
persisting when you need per-user accounting — not a local re-count with a
different tokenizer:

```python
import asyncio

from tempest_fastapi_sdk.genai import OpenAICompatGenerator, TokenUsage

gen = OpenAICompatGenerator(
    "deepseek-flash",
    api_key="sk-...",
    base_url="https://api.deepseek.com",
)


async def main() -> None:
    """Run this example."""
    text: str
    usage: TokenUsage | None = None
    text, usage = await gen.generate_with_usage("Summarize this.", system="Be brief.")
    if usage is not None:
        print(usage.input_tokens, usage.output_tokens, usage.total_tokens)


asyncio.run(main())
```

`TokenUsage` adds with `+`, for a job made of several calls (a map-reduce
summary is N chunk calls plus one reduce call, and what you want to record
is the job, not each leg).

!!! warning "`None` is not zero"
    `usage is None` means "the provider did not say", which differs from a
    zeroed usage claiming the call was free. Code that persists usage
    should write no row in that case.

!!! info "`total` comes from the provider, not from the sum"
    No provider is obliged to bill `input + output`: cached-prefix
    discounts show up exactly that way. The reported total is the
    authority; the sum is only a fallback when the field is absent.

#### The prefix that came from cache

When the start of a prompt matches a recent call, the provider serves that
part from cache and bills it far cheaper — DeepSeek's table puts the hit
token two orders of magnitude below the miss token. `cache_hit_tokens` is
that slice:

```python
import asyncio

from tempest_fastapi_sdk.genai import OpenAICompatGenerator, TokenUsage

gen = OpenAICompatGenerator(
    "deepseek-flash",
    api_key="sk-...",
    base_url="https://api.deepseek.com",
)


async def main() -> None:
    """Run this example."""
    usage: TokenUsage | None
    _text, usage = await gen.generate_with_usage("Summarize this.")
    if usage is not None:
        print(usage.input_tokens, "in, of which", usage.cache_hit_tokens, "cached")


asyncio.run(main())
```

!!! danger "It is a slice of `input_tokens`, not an extra charge"
    `cache_hit_tokens` counts tokens **already inside** `input_tokens`.
    Adding the two bills the prefix twice. Pricing subtracts instead:
    `(input − cached) × normal_rate + cached × cached_rate`, which is what
    `AIUsageStore.estimate_cost` does.

!!! info "Two spellings, both read"
    The same OpenAI-compatible family writes this two ways: DeepSeek sends
    `prompt_cache_hit_tokens` at the root of `usage`, OpenAI sends
    `prompt_tokens_details.cached_tokens`. `from_payload` reads both —
    handling only one works against the provider you tested and quietly
    overcharges on the other.

    A provider with no prompt cache sends no field at all, and the slice is
    `0`. Zero is honest here (nothing came from cache), unlike a missing
    `usage` as a whole, which is `None`.

### Provider-specific fields: `extra_body`

The format is shared; the extensions are not. `extra_body` is merged into
every request body, **under** the computed fields — so it cannot
accidentally redirect the call to another model.

```python
from tempest_fastapi_sdk.genai import OpenAICompatGenerator

gen = OpenAICompatGenerator(
    "deepseek-flash",
    api_key="sk-...",
    base_url="https://api.deepseek.com",
    extra_body={"thinking": {"type": "disabled"}},
)
```

!!! danger "A hybrid reasoning model returns empty content"
    That `thinking` field is not decoration. A hybrid model with reasoning
    **on by default** spends `max_tokens` on the hidden chain before the
    real content — billed as normal output — so a budget sized for the
    answer is exhausted there and `content` comes back empty. Reported by a
    service that hit it against DeepSeek; this repo does not reproduce it
    against a live provider. Turning it
    off is cheaper and safer, not a quality loss, when you only want the
    result.

!!! info "HuggingFace-only fields stay off the wire"
    `do_sample`, `top_k` and `repetition_penalty` exist on `GenerationConfig`
    but not in the OpenAI format, and a strict provider refuses the body (an
    audit reported `400` from OpenAI; the tests here use a mock transport).
    So they are **dropped**; `do_sample=False` without an explicit
    `temperature` becomes `temperature=0`. A provider that accepts those
    names (vLLM's server documents `top_k` and `repetition_penalty`) is
    opted in with `forward_params=["top_k", "repetition_penalty"]`. And
    `model`, `messages` and `stream` cannot arrive as a generation keyword —
    `generate("hi", model="other")` raises `TypeError` instead of
    redirecting the call.
