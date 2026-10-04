# Self-hosted generative AI

Run HuggingFace models on **your own hardware** — no external API, no data
leaving your servers. This page covers the whole `tempest_fastapi_sdk.genai`
module, starting where every adoption starts: knowing, *before* you download
gigabytes of weights, whether the machine can handle the model.

!!! info "How the module was built"
    The module shipped in slices, and the order below is the recommended
    reading path -- each section assumes the ones before it. After v0.108 came
    structured output (v0.142), diarization (v0.223), STT correction (v0.235)
    and the Coqui runtime behind the `[genai-audio]` extra (v0.252), all
    documented in their own sections on this same page.
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
from tempest_fastapi_sdk.genai import can_run

report = can_run(model_id="Qwen/Qwen2.5-7B-Instruct")

if report.fits:
    print(f"OK on {report.device} — {report.headroom_pct:.0f}% headroom")
else:
    print(report.reason)
    print("Suggestion:", report.suggestion)   # e.g. "Quantize to int4 ..."
```

`CapacityReport` carries: `fits`, `device` (`cuda`/`mps`/`cpu`),
`estimated_bytes` vs `available_bytes`, `headroom_pct`, `reason`, a
concrete `suggestion` when it doesn't fit (quantize, offload to CPU, or
pick a smaller model), and `memory_measured`, which says whether the
device's free memory was actually read (see [Without `psutil`](#without-psutil)).

Without `dtype=`, `can_run` sizes the precision `TextGenerator(dtype="auto")`
loads on the chosen device: `bfloat16` on CUDA/MPS, `float32` on CPU. Pass
`dtype=` to plan for something else — an explicit `dtype=` on the
generator, or `ModelDtype.INT8`/`INT4` for `quantization=`.

!!! tip "Let the SDK pick the precision"
    `recommend(...)` tries the native precision of the best available
    device (`bfloat16` on a GPU, `float32` on CPU) → `int8` → `int4` and
    returns the **first** config that fits:

    ```python
    from tempest_fastapi_sdk.genai import recommend

    best = recommend(model_id="meta-llama/Llama-3.1-8B")
    print(best.device, best.dtype, best.fits)   # e.g. cuda int8 True
    ```

### On CPU

Up to 0.303.0 the planner sized every unquantized load at `bfloat16`, but
on CPU `TextGenerator` loads `float32` — twice the bytes. `recommend()`
said "fits" for a model that did not. CPU is now sized at `float32`, and
the rung below it is `int8` (never `bfloat16`, which is not a
quantization).

Measured with the GPU hidden (`CUDA_VISIBLE_DEVICES=`), i9-13900F, 62 GB
RAM, WSL2, torch 2.14, bitsandbytes 0.50.2, N=3 per row. "Measured" is the
process RSS growth from before to after `load()`, median; transformers
4.57.6 (the floor) / 5.18.0 (the newest on the day):

| Model | Precision | Estimate | Measured (4.57.6 / 5.18.0) |
| --- | --- | --- | --- |
| Qwen2.5-0.5B-Instruct | `float32` (was: `bfloat16`) | 2.47 GB (was: 1.24 GB) | 2.13 GB / 2.12 GB |
| Qwen2.5-0.5B-Instruct | `int8` | 0.62 GB | 0.89 GB / 1.29 GB |
| Qwen2.5-0.5B-Instruct | `int4` | 0.37 GB | 1.02 GB / 1.62 GB |
| Qwen2.5-3B-Instruct | `float32` (was: `bfloat16`) | 15.43 GB (was: 7.71 GB) | 12.51 GB / 12.48 GB |
| Qwen2.5-3B-Instruct | `int8` | 3.86 GB | 3.67 GB / 8.54 GB |
| Qwen2.5-3B-Instruct | `int4` | 2.31 GB | 3.25 GB / 8.16 GB |

What the table says:

- **`float32` is what CPU loads**, and the new estimate covers the RSS
  after the load. The peak **during** the load goes past it: 3.12 GB for
  the 0.5B and 14.71 GB / 18.66 GB for the 3B.
- **bitsandbytes int8/int4 load and generate on CPU** with bitsandbytes
  0.50.2 — no error in the table's 24 quantized runs.
- **The quantized estimate can be optimistic.** For the 0.5B the measured
  int4 RSS was 2.7× to 4.4× above it (embeddings are not quantized, and
  weigh more in a small model). For the 3B on transformers 4.57.6, int8
  came in below the estimate (3.67 against 3.86 GB) and int4 above it
  (3.25 against 2.31 GB). On transformers 5.18.0 the quantized 3B
  load leaves the process with 5.65 GB of **file-backed** RSS (`RssFile`,
  against 0.36 GB on 4.57.6); the whole process's anonymous memory was
  3.33 GB (int8) and 2.95 GB (int4).
- **bitsandbytes on CPU is slower than `float32`.** On the 3B, a
  `generate()` capped at 16 tokens took ~9 s at int8/int4 against ~3 s at
  `float32`. That is why a CPU quantization `suggestion` points at the CPU
  path the SDK already ships:

!!! tip "On CPU, GGUF through Ollama"
    llama.cpp (build 687e778, `-ngl 0`, context 4096) serving the Q4_K_M
    GGUF that Ollama 0.30.11 pulls for `qwen2.5:3b` sat at 3.60 GB RSS
    (1.61 GB anonymous + 1.99 GB file-backed) and generated 24 to 27
    tokens/s; `qwen2.5:0.5b` sat at 0.66 GB and 107 to 134 tokens/s (N=3
    each). Use [`OllamaGenerator`](#ollama-backend) — memory is in the same
    range as bitsandbytes int4; the measured advantage is speed. For an
    agent, what that buys per step (and where it does not: prefill) is in
    [Agents on CPU](agents.md#agents-on-cpu-backend-size-and-budget).

## Probing the hardware

```python
from tempest_fastapi_sdk.genai import probe_hardware

hw = probe_hardware()
print(hw.cpu_cores, hw.ram_available_bytes)
print(hw.has_cuda, [g.name for g in hw.gpus])   # per-GPU VRAM when CUDA is present
```

`HardwareInfo` reports CPU, total/available RAM, CUDA GPUs (name +
total/free VRAM), MPS (Apple), and free disk space. Without `torch`, no
GPU shows up (`has_cuda=False`, `gpus=[]`). RAM comes from `psutil` and,
on Linux, from `/proc/meminfo` when `psutil` is missing — see
[Without `psutil`](#without-psutil) —, bounded by the cgroup in a
container with `--memory` (see [In a container with a memory
limit](#in-a-container-with-a-memory-limit)). A reading that could not be taken is
marked **unmeasured**, not empty: the RAM fields are `0` with
`ram_measured=False`; when the disk does not answer, `disk_free_bytes=0`
with `disk_measured=False`. The fields stay `int`, so arithmetic on them
does not break — but check the flag before reading `0` as "nothing free".
A snapshot built by hand (`HardwareInfo(...)`) has both flags `True` by
default.

### Without `psutil`

`psutil` ships in the `[metrics]` extra, **not** in `[genai]`. What happens
without it depends on the platform:

- **Linux (WSL and containers included): RAM is measured anyway.**
  `probe_hardware()` reads `MemTotal` and `MemAvailable` from
  `/proc/meminfo` — the same file `psutil` reads on Linux. Measured in a
  clean venv with only the base package (WSL2, 62 GB,
  `CUDA_VISIBLE_DEVICES=`, no `torch`): a total of 67,430,916,096 bytes,
  equal to `free -b`; `MemAvailable` within 516 KB of `free -b` in the same
  window (the two readings are different instants). Reading the file and
  calling `psutil` 7.2.2 in the same process, both byte counts matched
  exactly. `recommend(num_params=500_000_000)` gives `fits=True` at
  `float32`.
- **A kernel without `MemAvailable`** (older than 3.14) or with
  `MemAvailable: 0`: RAM counts as **not measured**. `psutil` estimates in
  that case; summing `MemFree + Buffers + Cached` would overstate what a
  load can claim, and the planner's whole job is not to promise memory that
  is not there.
- **Windows and macOS: without `psutil` RAM is not measured**, and the
  report says so in plain words.

In 0.303.1, unmeasured RAM was read as zero free bytes: on the host above,
`recommend(num_params=500_000_000)` returned `fits=False` at `int4` with
*"Model is too large for this host even quantized; use a smaller model or
add memory."*. Now, with unknown memory (output below with `sys.platform`
switched to `darwin` after import, no `psutil`):

```python
from tempest_fastapi_sdk.genai import recommend

report = recommend(num_params=500_000_000)
print(report.memory_measured, report.fits, report.dtype)
print(report.suggestion)
```

```text
False False float32
Free RAM was not measured: on darwin measuring RAM requires psutil (pip install 'tempest-fastapi-sdk[metrics]'). Then check again.
```

On Linux that message only shows up when `/proc/meminfo` fails too, and it
then says that both `psutil` and `/proc/meminfo` failed. With unknown
memory:

- **`fits` is `False`** because nothing was verified — an `if report.fits:`
  that decides whether to load stays conservative. Code that needs to tell
  "does not fit" from "don't know" reads `memory_measured`.
- **`dtype` is the native precision** (`float32` on CPU, `bfloat16` on
  MPS), the one `TextGenerator(dtype="auto")` loads. `recommend()` does
  not step down to `int8`/`int4`: with no number to compare against,
  nothing justifies quantizing, and quantizing costs quality and, on CPU,
  speed (see [On CPU](#on-cpu)).
- **`available_bytes` and `headroom_pct` are `0`** and carry no
  information; `reason` says RAM was not measured.
- **On a GPU the verdict is still measured** — VRAM comes from
  `torch`/NVML. Only the CPU offload suggestion, which depends on RAM,
  becomes "RAM was not measured" instead of "the host is too small".

### In a container with a memory limit

A container's `/proc/meminfo` shows the **host's** memory, and
`psutil.virtual_memory()` reads the same file. Up to 0.303.2, in a
`docker run --memory=512m` on a 62 GB host, both reported the host's
67,430,916,096 bytes, and the planner answered `fits=True` for a 2.5 GB
load that does not fit in 512 MiB.

Now, on Linux, `probe_hardware()` also reads the memory limit of the
process's cgroup — `memory.max` on cgroup v2, `memory.limit_in_bytes` on
v1, found through `/proc/self/cgroup` and `/proc/self/mountinfo`, walking
up the parent cgroups. When that limit is below the host's RAM it becomes
`ram_total_bytes`, `ram_available_bytes` becomes the smaller of the host's
and what is left in the cgroup, and `ram_cgroup_limited=True`. It holds
with and without `psutil`:

```python
from tempest_fastapi_sdk.genai import can_run, probe_hardware, recommend

info = probe_hardware()
print(info.ram_total_bytes, info.ram_cgroup_limited)

native = can_run(num_params=500_000_000)
print(native.fits, native.dtype)
print(native.reason)

best = recommend(num_params=500_000_000)
print(best.fits, best.dtype)
```

Output in a `python:3.12-slim` with `--memory=512m` (WSL2, Docker 29.6.1,
cgroup v2), with this version's wheel and no `psutil`:

```text
536870912 True
False float32
~2.5 GB needed at float32 exceeds the ~0.4 GB free on cpu. RAM is limited by the container's cgroup to 512 MiB.
True int4
```

With `psutil` 7.2.2 installed in the same container the output is the
same. Without `--memory` (`memory.max` = `max`) nothing changes: the total
stays at the host's 67,430,916,096 bytes and `ram_cgroup_limited=False`.

**How much is left in the cgroup.** `memory.current`
(`memory.usage_in_bytes` on v1) counts page cache, which the kernel drops
before it calls the OOM killer. So the math is
`limit - (usage - (file - shmem))`: page cache comes back, but
`tmpfs`/shared memory (`shmem`) does not, without swap. Measured in the
same container with `--memory-swap=512m` (no swap), allocating 8 MiB at a
time until the OOM killer:

| Scenario | `limit - usage` | `limit - (usage - (file - shmem))` | Actually allocated |
| --- | --- | --- | --- |
| 300 MiB of page cache, read once | ~183 MiB | ~483 MiB | 504 MiB |
| the same 300 MiB read 3 times (`active_file`) | ~183 MiB | ~483 MiB | 504 MiB |
| 300 MiB in `/dev/shm` (`shmem`) | ~191 MiB | ~191 MiB | 200 MiB |

Subtracting only `inactive_file` (the `docker stats` math) would have
given ~203 MiB in the second scenario, and subtracting all of `file` would
have given ~491 MiB in the third. With the wheel, `probe_hardware()` itself
reported 417 MiB free and the process allocated 432 MiB (3 out of 3 runs);
with 300 MiB of active page cache, it reported 409 MiB and the process
allocated 416 or 432 MiB.

!!! note "Swap is left out"
    With `--memory=512m` and no `--memory-swap`, Docker lets the container
    use another 512 MiB of swap: on the same host the process allocated
    944 MiB before the OOM killer. The planner counts RAM only — a model
    paged out to swap is not a model that runs.

cgroup v1 is covered by tests over fake files (a limit, the "no limit"
sentinel, a `/docker/<id>` mount root), but it was not measured on a v1
host: Docker on WSL2 uses cgroup v2.

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
    **[Model weights »](model-weights.md#where-the-weights-live-and-why-the-second-run-is-instant)**.

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
    Pair it with the [capacity check](#can-the-machine-handle-it): run
    `can_run` / `recommend` to pick a `quantization`/`device` that **fits**
    before instantiating the `TextGenerator`.

!!! tip "Free VRAM between bursts"
    With `idle_unload_seconds` set, call `gen.unload_if_idle()` periodically
    (e.g. in a `@tq.interval(60)` [TaskQueue](queue-tasks.md) task) — it
    unloads only once past the idle threshold, no background-thread magic.
    `unload()` frees immediately.

    A call in progress does **not** count as idle: `seconds_idle` reads
    `0.0` while it runs and `unload_if_idle()` returns `False`. An
    `unload()` that arrives mid-call (including one from a `ModelRegistry`
    eviction) waits: whichever call finishes last drops the weights. And
    several simultaneous first calls load the model **once**. The same holds
    for every local loader (`Embedder`, `Reranker`, `ImageGenerator`,
    `SpeechToText`, `TextToSpeech`, `SpeakerDiarizer`, `VoiceEmbedder`,
    `OnnxEmbedder`...) and for the [face recognition](faces.md)
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

Not every service wants to hold weights: sometimes the budget that matters
is **cost per token**, not RAM. OpenAI's `/chat/completions` format became
the common denominator — DeepSeek, Groq, Together, OpenRouter, Mistral,
vLLM's server, TGI's OpenAI route and Azure all speak it — so one client
reaches all of them by swapping `base_url` and `model`.

```python
import asyncio

from tempest_fastapi_sdk.genai import OpenAICompatGenerator

gen = OpenAICompatGenerator(
    "deepseek-chat",
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
    "deepseek-chat",
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
    "deepseek-chat",
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
    "deepseek-chat",
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

#### Context window: `num_ctx`

Ollama processes at most `num_ctx` prompt tokens, and it **cuts the rest
without a word**: the reply comes back `200`, no error, generated over a
slice of what you sent. Measured on Ollama 0.30.11 (`lfm2.5-thinking:1.2b`,
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
[agent loop](agents.en.md#the-model-runs-on-the-generators-defaults) is one.
The window and the default config are part of the `generation_cache` key, so
two generators with different defaults never answer from each other's cache.

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

### Hybrid search (BM25 + dense)

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

### Reranking (cross-encoder)

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

Chroma is asked for the `top_k * candidate_multiplier` nearest neighbours
(default `4`), and the recency re-rank picks `top_k` of them — so a recent
message ranked just below the raw top-K can still rise. With
`candidate_multiplier=1` only the raw top-K is reordered. The per-user
quota (`max_entries_per_user`) evicts the oldest **UTC instant** first:
`created_at` is stored converted to UTC, alongside a `created_at_ts` in
epoch seconds, and older rows without that field are ordered by parsing
the ISO string. Sorting the ISO strings put `10:00+05:00` after
`08:00+00:00`, although it is three hours earlier.

!!! info "The `[genai-chroma]` extra and the recency decay"
    Install with `uv add "tempest-fastapi-sdk[genai-chroma]"`. The final
    `score` combines similarity and recency via
    `(1 - recency_weight) * sim + recency_weight * sim * decay`, with
    `decay = 0.5 ** (age_in_days / recency_halflife_days)`. With the
    defaults (14 days, `recency_weight=0.5`), a 14-day-old snippet has
    `decay = 0.5` and scores 0.75 of its similarity, against 1.0 for a
    freshly written one of equal similarity. `recency_weight=0` = similarity
    only.

!!! tip "Generic RAG with `ChromaVectorStore`"
    Just need a persistent vector store (without the per-user memory
    logic)? `ChromaVectorStore` is a `VectorStore` like the others —
    `add(chunks, vectors)` / `search(vector, top_k=)` — backed by ChromaDB,
    with the same re-index rule as the other stores (`add` replaces the
    batch's `source`s). Drop it into `Retriever` in place of
    `InMemoryVectorStore` / `PgVectorStore` to get a disk-persisted corpus:

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

### Ready endpoint: `make_ai_chat_router`

One router, a whole chat backend in-process:

```python
from fastapi import FastAPI

from tempest_fastapi_sdk.genai import (
    AIChatPipeline,
    TextGenerator,
    TextModel,
    make_ai_chat_router,
)

pipeline = AIChatPipeline(generator=TextGenerator(TextModel.QWEN2_5_7B_INSTRUCT))


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

#### With memory: the conversation owner comes from the session

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

### Streaming

`stream` yields tokens as they come (prompt mode; it resolves any
tool-calls **before** it starts emitting):

```python
import asyncio

from tempest_fastapi_sdk.genai import AIChatPipeline, TextGenerator, TextModel

pipeline = AIChatPipeline(generator=TextGenerator(TextModel.QWEN2_5_7B_INSTRUCT))


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

## Embeddings and scale

### Generate embeddings

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

### ONNX embeddings (no torch)

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

### Batch concurrent inference

On a GPU, one item at a time wastes the device. `BatchScheduler` coalesces
concurrent calls into a single batch — each caller still `await`s its own
result:

```python
import asyncio

from tempest_fastapi_sdk.genai import BatchScheduler, Embedder, EmbeddingModel

emb = Embedder(EmbeddingModel.ALL_MINILM_L6_V2)


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

### What is loaded right now

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

### In Prometheus and in the admin

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

## Audio (voice)

Interpret and generate voice on your hardware — no external API. Needs the
`[genai-audio]` extra (faster-whisper + Coqui TTS); the engines import
lazily.

!!! warning "In v0.252.0 the extra started shipping the Coqui runtime"
    `coqui-tts` keeps torch, torchaudio and torchcodec behind its own extras
    (`[cpu]`, `[cuda]`, `[codec]`), so up to v0.251.0 installing
    `[genai-audio]` gave you a TTS that **would not import**. Measured with
    coqui-tts 0.27.5 and torch 2.13.0:

    ```text
    ModuleNotFoundError: No module named 'torchaudio'
    ```

    And with torchaudio in place, a fresh resolve brought transformers 5.x,
    which removed the symbol `TTS.api` imports eagerly:

    ```text
    ImportError: cannot import name 'isin_mps_friendly' from 'transformers.pytorch_utils'
    ```

    Measured: `isin_mps_friendly` exists in transformers 5.0.0 and is gone
    from 5.1.0 on, and the measurement was repeated on 2026-09-02 against
    the newest coqui-tts (0.27.5) with transformers 5.16.1: the same
    `ImportError`, so the ceiling is still load-bearing.
    `[genai-audio]` declares `torch>=2.14`,
    `torchaudio>=2.11`, `torchcodec>=0.16` and `transformers<5` — the upper
    bound stays confined to this extra. If you pinned those four by hand, you
    can drop them.

!!! danger "XTTS v2 asks you to accept a licence, and a server has nobody to answer"
    `TextToSpeech`'s default model is licence-gated. Read in coqui-tts 0.27.5,
    `ModelManager.tos_agreed` accepts only a `tos_agreed.txt` beside the
    weights **or** `COQUI_TOS_AGREED` equal to the **string** `"1"`; without
    it, `ask_tos` calls `input()`. Synthesis runs in `asyncio.to_thread`,
    where there is no tty to answer — so export it before the first
    `synthesize`:

    ```bash
    export COQUI_TOS_AGREED=1
    ```

    `COQUI_TOS_AGREED=true` does not count: the comparison is against `"1"`.

### Interpret audio (STT)

`SpeechToText` transcribes with **faster-whisper** (Whisper via
CTranslate2, fast on CPU/GPU). Loads once, runs in a worker thread,
serializes calls through a semaphore.

```python
import asyncio

from tempest_fastapi_sdk.genai.audio import SpeechToText

stt = SpeechToText("base", device="auto")     # tiny…large-v3


async def main() -> None:
    """Run this example."""
    result = await stt.transcribe("meeting.wav")
    print(result.text, result.language, result.duration)
    for seg in result.segments:                    # per-span timestamps
        print(seg.start, seg.end, seg.text)


asyncio.run(main())
```

Accepts a path or `bytes`. `device`/`compute_type` resolve automatically
(`float16` on GPU, `int8` on CPU).

#### Transcribing faster on CPU

The four knobs below change **how** the decode is scheduled, not which
weights run — same model, same precision:

```python
from tempest_fastapi_sdk.genai.audio import SpeechToText

stt = SpeechToText(
    "large-v3-turbo",
    device="cpu",
    compute_type="int8",
    batch_size=8,                      # decode 8 speech spans in parallel
    cpu_threads=0,                     # 0 = CTranslate2 decides (intra_threads)
    num_workers=1,                     # parallel translations in one model
    condition_on_previous_text=False,  # turn off alongside batch_size
)
```

- **`batch_size`** swaps sequential `WhisperModel.transcribe()` for
  faster-whisper's `BatchedInferencePipeline`. It requires
  `vad_filter=True` — the VAD is what cuts the audio into the spans a
  batch is made of — and passing one without the other raises
  `ValueError` at construction, not on the first transcription. The cost
  is peak memory proportional to the value.
- **`cpu_threads`** / **`num_workers`** are CTranslate2's `intra_threads`
  / `inter_threads`. `num_workers` only matters when several concurrent
  calls share the instance.
- **`condition_on_previous_text`** hands each window the previous
  window's text as context. The default is `True`, which is
  faster-whisper's own — kept so an SDK upgrade does not silently change
  anybody's transcripts. Turn it off when batching: it serializes spans
  that would otherwise decode in parallel, and it is the path by which
  one bad span contaminates the ones after it (the repetition loop).

!!! tip "A long file should not look like a hang"
    faster-whisper hands back a generator, so an hour-long meeting spends
    minutes with no signal at all. `on_progress` receives
    `(seconds_done, total_seconds)` as the decode advances:

    ```python
    import asyncio
    import logging

    from tempest_fastapi_sdk.genai.audio import SpeechToText

    logger = logging.getLogger(__name__)
    stt = SpeechToText("base", device="cpu")


    async def main() -> None:
        """Run this example."""

        def progress(done: float, total: float) -> None:
            """Log how far the decode got."""
            logger.info("transcribed %.1fs of %.1fs", done, total)

        await stt.transcribe("meeting.wav", on_progress=progress)


    asyncio.run(main())
    ```

    The callback runs **on the worker thread**, not the event loop: do not
    touch a coroutine in there without `loop.call_soon_threadsafe`.

!!! info "One shared instance loads the model exactly once"
    `load()` is guarded by a `threading.Lock`, not by the concurrency
    semaphore — the semaphore admits `max_concurrent` callers by
    definition, so two of them arriving on a cold instance used to build
    two copies of the model (fixed in v0.235.0). Keep sharing one
    instance across requests: that is what saves the load.

### Generate voice (TTS)

`TextToSpeech` synthesizes with **Coqui TTS** (WAV). Same discipline
(lazy + thread + semaphore).

```python
import asyncio

from tempest_fastapi_sdk.genai.audio import TextToSpeech

tts = TextToSpeech("tts_models/multilingual/multi-dataset/xtts_v2")


async def main() -> None:
    """Run this example."""
    wav = await tts.synthesize("Hello, world.", language="en")   # -> WAV bytes
    # voice cloning (XTTS): pass a reference clip
    wav = await tts.synthesize("Hi!", language="en", speaker_wav="ref.wav")


asyncio.run(main())
```

`synthesize` returns the WAV `bytes`; pass `out_path=` to also write it to
disk.

### Language (PT-BR / EN-US)

No need to know the Whisper code or pick a TTS model: use the `Language`
enum. It resolves the code (`pt`/`en`) for STT and a good TTS model per
language:

```python
import asyncio

from tempest_fastapi_sdk.genai.audio import Language, SpeechToText, TextToSpeech


async def main() -> None:
    """Run this example."""
    # STT: force the language without memorizing the code
    await SpeechToText("base").transcribe("audio.wav", language=Language.PT_BR)

    # TTS: picks the language's default model automatically
    tts = TextToSpeech.for_language(Language.EN_US)     # en-US model
    wav = await tts.synthesize("Hello, world.")


asyncio.run(main())
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
import asyncio

import httpx
from tempest_fastapi_sdk.genai.rag import SearxngBackend, WebSearch, build_context
from tempest_fastapi_sdk.utils.http_client import HTTPClient

client = HTTPClient()
search = WebSearch(SearxngBackend("http://localhost:8080", http_client=client))


async def main() -> None:
    """Run this example."""
    results = await search.search("what is PIX?", max_results=5)   # list[SearchResult]
    context = build_context("what is PIX?", results, long_text=False, max_chars=2000)
    # -> a string ready to inject into your TextGenerator prompt


asyncio.run(main())
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
import asyncio

import httpx

from tempest_fastapi_sdk.genai.rag import ContentExtractor, build_context

results = []  # hits from a previous retriever.search(...)


extractor = ContentExtractor(http_client=httpx.AsyncClient())


async def main() -> None:
    """Run this example."""
    for result in results:
        outcome = await extractor.extract(result.url)
        result.content = outcome.text          # "" on failure; outcome.failed marks it
    context = build_context("what is PIX?", results)   # now with full bodies


asyncio.run(main())
```

Failures (timeout, 4xx/5xx, empty page) **never** raise — they come back
as `ExtractionResult(text="", failed=True)`, so no source is silently
dropped.

!!! info "The URL is treated as untrusted input"
    The URL is picked by the search engine or by the user, so `extract`
    keeps your server from becoming a proxy into the internal network
    (SSRF):

    - only `http` and `https` — `file://`, `ftp://` and friends come back
      `failed=True` without a request;
    - the host (an IP literal, or **every** address the hostname resolves
      to) must be public: loopback, private ranges (10/8, 172.16/12,
      192.168/16, `fd00::/8`), link-local — including the cloud metadata
      `169.254.169.254` —, CGNAT, multicast and reserved are refused, for
      IPv4 and IPv6;
    - redirects are followed by hand, with the same check on **every** hop,
      up to `max_redirects=` (default 5);
    - the body is streamed and the fetch gives up past
      `max_response_bytes=` (default 5 MiB);
    - `trafilatura` runs in a thread (`asyncio.to_thread`), so a large page
      does not stall the event loop.

    To read intranet pages on purpose, `allow_private_networks=True` turns
    off only the address check (scheme, redirect bound and body cap still
    apply). Turn it on only when the URLs do not come from an attacker.

    The connection goes to the address that passed the check, not to a
    second lookup of the name: each hop's URL carries the validated IP,
    while the `Host` header and the TLS name (SNI, through httpx's
    `sni_hostname` extension) keep the hostname. A DNS answer that changes
    between the check and the connection (DNS rebinding) never reaches the
    connection, and HTTPS still validates the certificate against the
    hostname — a certificate issued for another name is refused.

    The httpx pool keys connections by IP, so two hostnames on the same IP
    (common behind a CDN) could share a TLS session negotiated for one of
    them. When an HTTPS response arrives on a connection opened for another
    hostname, the body is not read, the connection is closed and the hop is
    retried on a fresh connection, up to 3 times. The request line and
    headers had already gone out on that connection; what is discarded is
    the response.

    Behind an HTTP proxy the tunnel is opened to the IP and httpcore 1.0.9
    does not send the SNI, so HTTPS fetches come back `failed=True`
    (`certificate verify failed: IP address mismatch`). In that setup the
    proxy resolves DNS, and the egress rule belongs there:
    `allow_private_networks=True` turns pinning off along with the check.

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
import asyncio

from tempest_fastapi_sdk.genai import Embedder, TextGenerator, TextModel
from tempest_fastapi_sdk.genai.rag import InMemoryVectorStore, PdfReader, Retriever

gen = TextGenerator(TextModel.QWEN2_5_7B_INSTRUCT)


rag = Retriever(Embedder("sentence-transformers/all-MiniLM-L6-v2", normalize=True),
                InMemoryVectorStore())


async def main() -> None:
    """Run this example."""
    await rag.index(PdfReader().chunks("/kb/manual.pdf"))     # once
    context = await rag.retrieve("how to refund?", top_k=5)   # cheap, afterwards
    answer = await gen.generate(context)


asyncio.run(main())
```

- **`VectorStore`** is a `Protocol` — `InMemoryVectorStore` (dev/tests,
  cosine scan) or `PgVectorStore` (production).
- **`PgVectorStore`** uses **pgvector** in the Postgres the service already
  has (no new infra): creates the table and a B-tree index on `source` on
  demand, searches with the cosine distance operator `<=>`. Needs
  `[genai-rag]` + `CREATE EXTENSION vector`.
- **Every SDK store replaces per source**: `add` deletes what each batch
  chunk's `source` already held and writes the batch (in `PgVectorStore`,
  in one transaction: one `DELETE` + one batched `INSERT`). Re-indexing an
  edited document leaves no old tail; pass every chunk of a source in the
  same call.

```python
from tempest_fastapi_sdk.genai import Embedder, EmbeddingModel
from tempest_fastapi_sdk.genai.rag import PgVectorStore, Retriever

from src.api.dependencies.resources import db

embedder = Embedder(EmbeddingModel.ALL_MINILM_L6_V2)


store = PgVectorStore(db, dim=384)          # db = AsyncDatabaseManager
rag = Retriever(embedder, store)
```

The table name is interpolated into the SQL (an identifier cannot be a bound
parameter), so the constructor refuses with `ValueError` anything outside an
unquoted `name` or `schema.name` (`[A-Za-z_][A-Za-z0-9_]*`, up to 63
characters each), and a `dim` that is not a positive integer.

#### Approximate index: HNSW or IVFFlat

Out of the box, search is **exact**: Postgres runs a sequential scan and
computes `<=>` against every row. With 100,000 chunks of 384 dimensions that
already costs ~50–60 ms per search (measurement below), and the cost follows
the row count.

An approximate index trades some **recall** (the fraction of the exact
neighbors that comes back) for latency. Ask for one in `ensure_schema`:

```python
import asyncio

from tempest_fastapi_sdk import AsyncDatabaseManager
from tempest_fastapi_sdk.genai.rag import Chunk, PgVectorStore

db = AsyncDatabaseManager("postgresql+asyncpg://app:app@127.0.0.1:5432/app")
store = PgVectorStore(db, dim=384)


async def main() -> None:
    """Build the HNSW index once, then search with a wider candidate list."""
    await store.ensure_schema(ann_index="hnsw", m=16, ef_construction=64)
    query: list[float] = [0.1] * 384
    hits: list[Chunk] = await store.search(query, top_k=10, ef_search=100)
    print([hit.text for hit in hits])


asyncio.run(main())
```

- **`ann_index="hnsw"`** builds a navigable graph. Build parameters: `m`
  (connections per node) and `ef_construction` (candidates while building).
  Needs pgvector **>= 0.5.0**; below that `ensure_schema` raises
  `RuntimeError` naming the version it found.
- **`ann_index="ivfflat"`** splits the vectors into `lists` groups and, at
  search time, scans only the nearest ones. Much faster to build, but the
  centroids come from the rows present **when it is built** — call it after
  loading the corpus, not before the first `add`.
- The index is named `<table>_embedding_idx` (`store.ann_index_name`) and
  uses `vector_cosine_ops`, the operator class of the `<=>` that `search`
  orders by.
- A parameter left as `None` stays out of the `WITH` clause, so pgvector's
  default applies. A parameter of the other method (`lists` with HNSW), or
  one given without `ann_index`, raises `ValueError` before any SQL runs.
- Calling it again with the same parameters does nothing. With **another**
  method or other parameters it raises `ValueError` asking for `DROP INDEX`:
  rebuilding a large index never happens hidden inside an `ensure_schema`.

At search time, `ef_search` (HNSW) and `probes` (IVFFlat) tune the trade-off
per call. `search` applies both with `set_config(..., true)`, the function
form of `SET LOCAL`: it lasts for the search's transaction only, and the next
session on the same pool reads the server value again (`40` and `1`,
checked in the docker test).

!!! warning "`ef_search` also caps how many results come back"
    An HNSW scan returns at most `ef_search` rows: measured on pgvector
    0.8.6, `search(top_k=20, ef_search=5)` returns **5** chunks. Keep
    `ef_search >= top_k`.

##### What it buys

Measured through `PgVectorStore.search` end to end (Python + asyncpg +
Postgres on `localhost`), `top_k=10`, against `pgvector/pgvector:pg16`
(pgvector 0.8.6, the container's default configuration, `--shm-size=1g`) on
a 12-core machine under WSL2. **N = 100,000 vectors of 384 dimensions.**
Recall@10 is the fraction of the exact scan's 10 neighbors that the index
returns; each row combines two runs with different seeds (200 searches per
seed on the Gaussian data, 500 on the clustered data), and the range shows
both.

Two synthetic corpora that bracket the real case from above and below:

- **Gaussian**: every component is `N(0, 1)`, no structure at all — the
  nearest neighbor is barely nearer than the rest;
- **clustered**: 1,000 Gaussian centers, each vector is a center plus
  `N(0, 0.5)` noise; queries are generated the same way.

| Search | p50 | recall@10 Gaussian | recall@10 clustered |
| --- | --- | --- | --- |
| exact (sequential scan) | 50–60 ms | 1.00 | 1.00 |
| HNSW, `ef_search=40` (default) | 4–8 ms | 0.05–0.06 | 0.986–0.992 |
| HNSW, `ef_search=100` | 4–8 ms | 0.12–0.13 | 1.00 |
| HNSW, `ef_search=400` | 7–20 ms | 0.36–0.37 | 1.00 |
| IVFFlat `lists=100`, `probes=1` (default) | 3.4–4.4 ms | 0.03–0.04 | 0.992–0.996 |
| IVFFlat `lists=100`, `probes=10` | 6–7.5 ms | 0.23 | 0.9996–0.9998 |
| IVFFlat `lists=100`, `probes=40` | 17–20 ms | 0.62 | 1.00 |

Index build over the 100,000 rows: HNSW with the defaults (`m=16`,
`ef_construction=64`) took 72–81 s on the Gaussian data and 26 s on the
clustered data; IVFFlat with `lists=100`, 0.8–1.5 s.

What to take from it:

- **Latency**: at this N the index takes a search from ~50 ms to ~5 ms. With
  5,000 Gaussian rows (50 searches, one seed) the exact scan measured ~20 ms
  at p50 and HNSW ~4 ms: the gain is there, but it is milliseconds.
- **Recall depends on the corpus, not only on the index.** The same
  parameters return nearly everything on the clustered data and nearly
  nothing on the Gaussian data. Text embeddings have structure (documents on
  the same subject sit close together), but how much only measuring **your**
  corpus tells: compare `search` with the index against the exact result
  (taken before creating the index) on a handful of real queries, and raise
  `ef_search`/`probes` until the recall is good enough.
- **A few thousand chunks?** Stay on exact search: the cost is small and the
  recall is 1.

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
import asyncio

from tempest_fastapi_sdk.genai import GenerationConfig, TextGenerator

gen = TextGenerator("Qwen/Qwen2.5-7B-Instruct", quantization="int4")
config = GenerationConfig(max_new_tokens=512, temperature=0.2, top_p=0.9)


async def main() -> None:
    """Run this example."""
    await gen.generate("Explain PIX in one sentence.", config=config)
    await gen.chat([{"role": "user", "content": "Hi"}], config=config)


asyncio.run(main())
```

Only the set fields layer over the defaults; explicit `**kwargs` still
win over the config (`gen.generate(prompt, config=config,
temperature=0.9)` uses `0.9`).

!!! tip "A default on the generator: `config=`"
    `TextGenerator(..., config=GenerationConfig(...))` (and the same on
    `OllamaGenerator`) applies to every call. The order, weakest to
    strongest: the built-in defaults, the generator's `config`, the call's
    `config` (field by field) and the call's `**kwargs`. It is the way in
    for a caller that passes no options per call, such as the agent loop.

!!! tip "`seed` and `stop` apply on the local path too"
    `seed` and `stop` are honored by both `OllamaGenerator` and
    `TextGenerator` (transformers): the same seed + `do_sample=True`
    reproduces the output, and `stop` becomes `model.generate`'s
    `stop_strings` argument (requires transformers >= 4.44). Either may come
    from the `GenerationConfig` or per call — the per-call override wins.

!!! info "The local `seed` is per call, and holds under concurrency"
    `model.generate` takes no per-call `torch.Generator` —
    `generate(..., generator=g)` raises `ValueError` (unused `model_kwargs`)
    on transformers 4.57.6 and on 5.17.0 — and samples with the process-wide
    RNG. So `TextGenerator` no longer uses `transformers.set_seed` for plain
    sampling (`do_sample=True`, `num_beams=1`, no assistant model): a logits
    processor of its own draws the token with a `torch.Generator` private
    to that call. Two concurrent generations with the same seed give the
    same text as one running alone, and unseeded calls do not become
    deterministic as a side effect.

    Measured with `Qwen/Qwen2.5-0.5B-Instruct`, 5 pairs of concurrent calls
    (`asyncio.gather`) with the same seed: before, all 5 pairs diverged from
    the serial run; now, none does — on CPU (transformers 4.57.6) and on an
    RTX 4070 Ti SUPER (4.57.6 and 5.17.0). Without concurrency, the same seed gives
    the same text as before the change, on both devices.

    The cost is paid by seeded calls only. The token-selection step went
    from 1.07 to 3.08 ms on the GPU and from 7.3 to 13.3 ms on the CPU
    (151 936-token vocabulary, top-k 20 + top-p 0.9, best of 5 rounds of 500
    steps). End to end, on a machine loaded by other processes, seeded
    generation came out 4 to 6% slower than the old path on the GPU (3 runs
    of 10 x 64 tokens) and 12% on the CPU (1 run of 5 x 32 tokens).

    Beam sampling (`num_beams > 1`), assisted/prompt-lookup decoding and
    DoLa sample in code a logits processor cannot steer; in those modes the
    seed still goes through `transformers.set_seed`, process-wide.

### Structured output (validated JSON)

Force the model to return a Pydantic schema and get the validated instance
back — instead of hoping the output happens to be parseable JSON:

```python
import asyncio

from pydantic import BaseModel
from tempest_fastapi_sdk.genai import OllamaGenerator


class Person(BaseModel):
    name: str
    age: int


gen = OllamaGenerator("llama3.2")


async def main() -> None:
    """Run this example."""
    person: Person = await gen.generate_structured("Any person.", Person)
    # -> Person(name="...", age=...)


asyncio.run(main())
```

`OllamaGenerator` sends the schema in the daemon's `format` field (Ollama
enforces JSON-schema-valid output natively) and parses the reply — **this is
the recommended structured route, no extra library**.

!!! warning "A long document needs `num_ctx` before anything else"
    Ollama's context window defaults to **4096 tokens**, and whatever does
    not fit is truncated silently — the extraction comes back with a few
    items, or none, and no error at all. Measured against `gpt-oss:20b`
    (Ollama 0.30.11) over a 24,086-character tender with 20 planted items,
    three runs per configuration:

    | `num_ctx` | instruction in `system` | instruction concatenated |
    | --- | --- | --- |
    | 4096 (default) | 0, 6, 5 items | error, 2, 1 items |
    | 16384 | **20, 20, 20** | **20, 20, 20** |

    With the window sized for the document, both shapes get everything.
    With the default window both degrade — the instruction in a `system`
    turn degrades less, because it is not competing for room with the
    document being truncated, and that is why the separation is still
    worth it:

    ```python
    await generator.generate_structured(
        document, Schema, system="...", num_ctx=16384
    )
    ```

    Size `num_ctx` by the document (a rough rule: one token per ~3
    characters, plus room for the schema and the answer), and keep the
    instruction in `system=`.

??? info "Why the call goes to `/api/chat` and not `/api/generate`"
    On a reasoning (harmony) model such as `gpt-oss`, `/api/generate`
    **with** `format` answers `200 OK` with a non-zero `eval_count` and an
    **empty** `response` — the reply lands in a reasoning channel that
    endpoint does not surface. Without `format` it works; with `format` it
    does not. `/api/chat` returns the JSON in `message.content`, and
    non-reasoning models behave identically on either. Since v0.229.0 the
    call uses `/api/chat`, and empty content raises `ValueError` instead of
    returning nothing.

!!! danger "A field with a `default` is a field the model may skip"
    Pydantic leaves a defaulted field out of `required` in the JSON schema,
    and the daemon's constrained decoder may then omit it — and it omits
    exactly the defaulted ones. In an extraction schema, **no field has a
    default**; absence is expressed in the data (`""`), never in the schema.

!!! tip "Instruction separated from the document — `chat_structured`"
    Extracting fields from a long document, the instruction belongs in a
    `system` turn and the content in a `user` one. Concatenating both
    into a single prompt degrades schema adherence **measurably**: the
    model starts answering *with* passages of the document.

    ```python
    import asyncio

    from pydantic import BaseModel
    from tempest_fastapi_sdk.genai import OllamaGenerator


    class Invoice(BaseModel):
        number: str
        total_cents: int


    gen = OllamaGenerator("gpt-oss:20b")


    async def main() -> None:
        """Run this example."""
        invoice: Invoice = await gen.chat_structured(
            [
                {"role": "system", "content": "Extract the invoice fields."},
                {"role": "user", "content": "NF-1 — total R$ 49.90"},
            ],
            Invoice,
        )


    asyncio.run(main())
    ```

    `format` goes at the **top level** of the body, which is where the
    daemon reads it. Passing the schema as a keyword to `chat()` lands it
    in `options`, and Ollama **ignores it silently**: `200 OK` with free
    text, and the failure only shows up at the `ValidationError` — or at
    a parse that happens to succeed. That is why `chat_structured`
    rejects an explicit `format=`.

!!! info "On the local backend (transformers)"
    `TextGenerator.generate_structured(prompt, schema, constrained=True)`
    constrains decoding with `lm-format-enforcer` (the `[genai-structured]`
    extra) so the model can only emit tokens that keep the JSON valid — the
    adapter is built from the library's stable core, so it works on
    transformers 4.x **and 5.x** (validated on Qwen2.5-3B). `constrained=False`
    stays available for best-effort parsing without the extra.

    Since v0.242.0 it also exposes `chat_structured(messages, schema)`, the
    **same** call the daemon answers: a service that reads documents types
    against `StructuredTextBackend` and runs on either without a line
    changing at the call site. The instruction/document split matters here
    too — the tokenizer's chat template is applied before generating.

    ```python
    import asyncio
    import threading

    from pydantic import BaseModel
    from tempest_fastapi_sdk.genai import StructuredTextBackend, TextGenerator


    class Invoice(BaseModel):
        number: str
        total_cents: int


    async def read(backend: StructuredTextBackend) -> Invoice:
        """Read the invoice with any structured backend.

        Args:
            backend (StructuredTextBackend): Ollama daemon or local model.

        Returns:
            Invoice: The extracted fields.
        """
        return await backend.chat_structured(
            [
                {"role": "system", "content": "Extract the invoice fields."},
                {"role": "user", "content": "INV-1 — total $49.90"},
            ],
            Invoice,
        )


    async def main() -> None:
        """Run this example."""
        stop = threading.Event()
        local = TextGenerator("Qwen/Qwen2.5-3B-Instruct")
        invoice: Invoice = await local.chat_structured(
            [{"role": "user", "content": "INV-1 — total $49.90"}],
            Invoice,
            stop_event=stop,
        )
        del invoice


    asyncio.run(main())
    ```

!!! info "Cancelling the call stops the local model"
    Local generation runs in a thread, and a thread cannot be cancelled
    from outside. So `TextGenerator` (and `VisionTextGenerator`) runs every
    call under an event that the transformers stopping criteria check after
    every token, and sets it when the call is cancelled: an
    `asyncio.timeout`, an agent budget or
    [`run_cancellable`](jobs.en.md#8-progress-the-bar-that-does-not-lie)
    stop decoding within one token. Measured with
    `Qwen/Qwen2.5-0.5B-Instruct` on CPU, 300 forced tokens, cancelled after
    1 s: the thread used to keep decoding 9.3 to 10.8 s after the cancellation
    (across separate measurements); it
    now ends in 0.01 to 0.02 s. `stop_event` still serves to stop **without**
    cancelling (from another thread, say), and it is set on cancellation
    too. On the daemon none of this is needed: aborting the HTTP request
    stops the generation.

!!! tip "Just the parse"
    `parse_structured(text, schema)` pulls the JSON out of a raw completion
    (tolerating Markdown fences and surrounding prose) and validates it against
    the schema — reusable on any model output.

#### When the answer is a **list**

A prompt asking "extract the tasks from this text" comes back as an array,
not an object. `parse_structured` looks for `{`…`}` and will not do; use
the list pair:

```python
from pydantic import BaseModel
from tempest_fastapi_sdk.genai import extract_json_list, parse_structured_list


class Task(BaseModel):
    title: str
    due: str | None = None


raw = 'Sure! Here they are: [{"title": "review the contract"}]'

tasks: list[Task] = parse_structured_list(raw, Task)
# -> [Task(title="review the contract", due=None)]

items: list | None = extract_json_list(raw)
# -> [{"title": "review the contract"}]
```

A Markdown fence around the array (the ```` ```json ```` a model adds even
when the prompt asks it not to) is tolerated just the same, whether it
wraps the whole completion or sits buried between two sentences.

- **`parse_structured_list(text, schema)`** validates every item and
  raises like `parse_structured` does. `skip_invalid=True` drops the bad
  item and returns the rest — what you want in a list of suggestions,
  where losing the nine good ones over one malformed item is the worst
  outcome.
- **`extract_json_list(text)`** returns the raw list, or `None` when there
  is no decodable array. **`None` is the "generate it again" signal**, and
  it differs from `[]`, which means "the model answered, and the answer is
  no items". Conflating the two either retries a call that already
  succeeded or gives up on a recoverable formatting slip.
- **`extract_json_object(text)`** is the twin for when the answer is
  **one** record rather than a list: an intent classifier, a routing
  decision, an extraction with fixed fields. Same tolerance for fences and
  prose as `parse_structured`, and the same `None`-means-retry in place of
  an exception — a retry loop written around `except ValueError` is the
  shape that swallows real errors along with format slips.

#### Retrying when the model gets the format wrong

`generate_structured_list` joins the two halves: generate, extract, and if
no array came back, **generate again at a higher temperature**.

```python
import asyncio

from pydantic import BaseModel
from tempest_fastapi_sdk.genai import (
    OpenAICompatGenerator,
    StructuredFormatError,
    generate_structured_list,
)


class Task(BaseModel):
    title: str


gen = OpenAICompatGenerator(
    "deepseek-chat",
    api_key="sk-...",
    base_url="https://api.deepseek.com",
)


async def main() -> None:
    """Run this example."""
    try:
        tasks: list[Task] = await generate_structured_list(
            gen,
            "Extract the tasks. Answer with a JSON array only.",
            Task,
            max_attempts=3,
            temperature_step=0.2,
        )
    except StructuredFormatError as exc:
        print(f"gave up after {exc.attempts} attempts: {exc.last_output}")
    else:
        print(len(tasks))


asyncio.run(main())
```

!!! tip "Why the temperature climbs"
    Repeating the call at the same temperature is close to pointless:
    greedy decoding is deterministic, so attempt two reproduces attempt one
    token for token, and the retry spends a call to get the same unusable
    output. The first attempt stays greedy (the most reliable single shot)
    and each retry adds `temperature_step`, giving sampling a real chance
    to leave the bad state.

!!! warning "A bad item does not cost an attempt"
    Only a **structural** failure — no array in the output at all —
    consumes an attempt. An array that parses but holds one malformed item
    is handled by `skip_invalid=True`, which drops the item and returns the
    rest; losing nine good suggestions over one bad one would be the worst
    outcome.

    For the same reason, **`[]` is a success**: the model answered, and the
    answer is no items. Retrying there re-asks a question already answered.

Takes any backend with `generate(prompt, config=...)` — the local
`TextGenerator`, `OllamaGenerator`, or `OpenAICompatGenerator`.

!!! info "A stray bracket no longer breaks the parse"
    Extraction counts depth instead of slicing to the last `]`/`}`. Before
    v0.235.0 one extra `]` at the end turned a perfect answer into a
    `ValueError`, and the retries
    reproduced the same cut, because the defect was in the slice and not
    in the generation. A non-greedy regex would not fix it: it stops at
    the first `]` and truncates any item with a nested list inside.

### Content moderation

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

#### The classifier: independent labels, the whole text

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

### Per-user usage accounting (a table)

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

### Inference metrics (Prometheus)

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

### Distributed tracing (OpenTelemetry)

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

### Token counting and context window

To fit a chat into the model's window, count tokens with the **model's own
tokenizer** (never a heuristic — BPE and SentencePiece disagree) and drop the
oldest turns when it overflows:

```python
from transformers import AutoTokenizer

from tempest_fastapi_sdk.genai import (
    TextModel,
    count_tokens,
    truncate_messages,
)

messages = [{"role": "user", "content": "oi"}]
tokenizer = AutoTokenizer.from_pretrained(TextModel.QWEN2_5_7B_INSTRUCT)


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
### Generation cache (prompt → completion)

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
### Vision (local multimodal VLM)

`VisionTextGenerator` is the multimodal sibling of `TextGenerator`: it loads an
`AutoModelForVision2Seq` + `AutoProcessor` and generates text conditioned on
images, on your hardware — parity with `OllamaGenerator`, which already accepts
`images`. Needs `[genai]` + `[genai-vlm]` (Pillow).

```python
import asyncio

from tempest_fastapi_sdk.genai import VisionTextGenerator

gen = VisionTextGenerator("llava-hf/llava-1.5-7b-hf")


async def main() -> None:
    """Run this example."""
    description = await gen.generate(
        "USER: <image>\nDescribe the image.\nASSISTANT:",
        images=["photo.jpg"],
    )


asyncio.run(main())
```

Images are accepted as a path, `bytes`, `PIL.Image` or a NumPy `ndarray` (same
leniency as `ort-vision-sdk`). `generate`/`chat` are image-optional — text-only
calls keep working (it is a `TextBackend`).

`GenerationConfig` applies here field by field as it does on
`TextGenerator`, and so do the per-call arguments — `stop_event` included:

- `seed` uses a generator private to the call for plain sampling;
- `stop` (in the config, or `stop=` per call, which wins over the config)
  ends decoding on the token that completes the stop string, matched against
  the processor's tokenizer. The stop string **stays** in the returned text,
  as on `TextGenerator`;
- the other fields (`max_new_tokens`, `temperature`, `top_p`, `top_k`,
  `repetition_penalty`, `do_sample`) go to `model.generate`.

```python
import asyncio
import threading

from tempest_fastapi_sdk.genai import GenerationConfig, VisionTextGenerator

gen = VisionTextGenerator("llava-hf/llava-1.5-7b-hf")
config = GenerationConfig(max_new_tokens=128, do_sample=False, stop=["\n\n"])


async def main() -> None:
    """Run this example."""
    stop = threading.Event()
    caption: str = await gen.generate(
        "USER: <image>\nWrite a short caption.\nASSISTANT:",
        images=["photo.jpg"],
        config=config,
        stop=["."],
        stop_event=stop,
    )
    print(caption)


asyncio.run(main())
```

Before, `stop` in the config was silently dropped (generation ran past the
stop string), and a per-call `stop=` or `stop_event=` made
`model.generate` raise `ValueError` listing the argument among the unused
`model_kwargs`. `tests/genai/test_vision_text_config.py` walks
`GenerationConfig.model_fields`: a new field the VLM neither applies nor
refuses with a clear error fails the test.

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

#### Per-request limits: `GenAIRequestLimits`

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
| `max_upload_bytes` | 25 MiB | the `/transcribe` upload, read in chunks |

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

### `RedisEmbeddingCache` — cache shared across workers

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

## Who said what: diarization

Transcription answers *what was said*. Diarization answers *who said it* —
it cuts the recording into turns and groups the voices. Together they give
you the minutes of a meeting or the record of a call:

```python
# scripts/minutes.py

import asyncio

from tempest_fastapi_sdk.genai.audio import (
    ConversationTranscriber,
    SpeakerDiarizer,
    SpeechToText,
)


async def main() -> None:
    """Transcribe a two-party call, attributing each line."""
    transcriber = ConversationTranscriber(
        stt=SpeechToText(model_size="small"),
        diarizer=SpeakerDiarizer(num_speakers=2),
    )
    conversation = await transcriber.transcribe("call.wav", language="pt")
    print(conversation.transcript())
    print(conversation.by_speaker())


if __name__ == "__main__":
    asyncio.run(main())
```

```text
Falante 0: Bom dia, em que posso ajudar?
Falante 1: Queria saber sobre a segunda via do boleto.
```

!!! info "Required extras"
    ```bash
    uv add "tempest-fastapi-sdk[genai-audio,genai-diarization]"
    ```
    `[genai-audio]` brings Whisper (transcription), `[genai-diarization]`
    brings `sherpa-onnx` (who spoke). Neither uses PyTorch.

### Why sherpa-onnx and not pyannote

Measured, not assumed. `pyannote.audio` 4.0.7 declares **21 runtime
dependencies** — `torch>=2.8`, `lightning`, `matplotlib`, three
OpenTelemetry packages and a client for the vendor's paid API — and its
pretrained pipeline is **gated** on HuggingFace: the container build needs
a token and a manually accepted licence.

`sherpa-onnx` declares **one** dependency, runs on ONNX Runtime with no
PyTorch, and its models are openly downloadable. On a 57-second
four-speaker recording it separated all four at **RTF 0.125** on CPU —
eight times faster than real time.

### The models are not in the wheel

They are 46 MB. Fetch them once, preferably at build time:

```python
from tempest_fastapi_sdk.genai.audio import ensure_models

ensure_models()  # honors TEMPEST_VOICE_MODEL_DIR
```

Leaving it to the first request makes one user pay the download inside
their timeout.

Both models have their SHA-256 pinned in the SDK (`SEGMENTATION_MODEL.sha256`,
`EMBEDDING_MODEL.sha256`), and `ensure_models` checks the file every time it
resolves it — fresh download or cache hit —, raising `OSError` on a mismatch.
A model swapped in the upstream release, or a corrupted cache, fails loudly
instead of changing the service's behavior with nothing in the diff. The
download uses a 60 s socket timeout: a connection that goes silent that long
raises instead of hanging the first `load()` forever (a slow but moving
download still completes).

### How many speakers? It works it out

Diarization has to answer two questions and only one is easy. *Where each turn
starts and ends* comes from the segmentation model. *How many distinct voices
there are* comes from no model — it has to be inferred from how the turns
group, and it is the part that gets a transcript wrong in ways nobody notices:
eight participants in a two-party call, or four people collapsed into one.

The default is `num_speakers="auto"`. Measured on a twelve-recording benchmark
whose speaker count is correct **by construction** — turns cut from distinct
recordings, so distinct people, rather than from the diarizer's own output:

| method | exact | mean error |
| --- | --- | --- |
| threshold 0.5 | 4/10 | 1.90 |
| threshold 0.7 | 8/10 | 0.40 |
| threshold 0.9 | 8/10 | 0.20 |
| **automatic** | **12/12** | **0.00** |

The automatic mode wins for a structural reason rather than a lucky constant: a
threshold asks *how close is close enough*, an answer that moves with the
microphone, the language and the room, while the spectral method asks *where
does this affinity matrix naturally split*, which is a property of the
recording itself.

It costs a second pass — one embedding per turn plus an eigendecomposition —
negligible next to the segmentation that produced the turns.

!!! tip "Know the count? Say so."
    ```python
    from tempest_fastapi_sdk.genai.audio import SpeakerDiarizer

    diarizer = SpeakerDiarizer(num_speakers=2)
    ```
    On a two-party call you do. It is exact and skips the second pass.
    `num_speakers=None` returns to threshold-only clustering, the weakest
    option, kept for callers who want the previous behaviour.

    When the count varies per request, pass it **per call** instead of
    setting it on the object:

    ```python
    turns = await diarizer.diarize(audio, num_speakers=2)
    ```

    A diarizer is built once and shared by every request, so writing to it
    affects the requests already in flight: two concurrent calls asking for 2
    and 5 speakers both saw whichever was written last, and the one that asked
    for 2 got 5 with no error anywhere. The per-call argument does not have
    that problem, and
    `ConversationTranscriber.transcribe(num_speakers=...)` forwards through
    it.

!!! warning "A monologue used to come back as a conversation"
    The spectral gap search **always** finds a split, including where there is
    none: a real six-turn dictation returned two speakers.

    A single voice is *uniformly* similar to itself — even its most distant
    pair of turns is close — while two voices produce pairs that are genuinely
    far apart. Measured across the twelve recordings, the 10th percentile of
    pairwise similarity was 0.490-0.667 for one speaker and -0.080-0.166 for
    more than one; the veto sits in the middle of that gap.

    That number is a property of the **bundled model's similarity scale**, not
    a universal constant: swapping the model means re-measuring it.

### How attribution works, and where it fails

The recording is transcribed **once**, not once per turn: handing Whisper
two-second clips throws away the context it uses for punctuation and
spelling, and costs one inference per turn. Each transcribed span then
goes to the turn it overlaps most in time.

The price of that choice: a Whisper span can **straddle** a speaker
change, and the whole span lands on whoever holds more of it. Speech the
diarizer dropped as too short still transcribes — that text comes back
with `speaker = -1` and is never lost. Dropping words silently is worse
than admitting the speaker is unknown.

### Recognising who it is: voice profiles

Diarization separates speaker 0 from speaker 1. Identification says speaker 0
**is Ana**, by matching the voice against an enrolled profile.

```python
from sqlalchemy.ext.asyncio import AsyncSession

from tempest_fastapi_sdk import make_voice_profile_model
from tempest_fastapi_sdk.genai.audio import VoiceProfileService

from src.db.models import UserModel

VoiceProfileModel = make_voice_profile_model(user_table="users")
profiles = VoiceProfileService(profile_model=VoiceProfileModel)
```

Enrolment requires consent — not optional, not configurable:

```python
from sqlalchemy.ext.asyncio import AsyncSession

from tempest_fastapi_sdk import make_voice_profile_model
from tempest_fastapi_sdk.genai.audio import (
    ConversationTranscriber,
    SpeechToText,
    VoiceProfileService,
)

from src.db.models import UserModel

profiles = VoiceProfileService(
    profile_model=make_voice_profile_model(user_table="users"),
)
transcriber = ConversationTranscriber(stt=SpeechToText())


async def enrol_voice(session: AsyncSession, user: UserModel) -> None:
    """Enrol a user's voice after they consented to it."""
    await profiles.enroll(
        session,
        user_id=user.id,
        audio="enrolment.wav",
        consent_reference="biometrics-policy-v3",
        label="onboarding",
    )
```

After that the transcript comes out named:

```python
from sqlalchemy.ext.asyncio import AsyncSession

from tempest_fastapi_sdk import make_voice_profile_model
from tempest_fastapi_sdk.genai.audio import (
    ConversationTranscriber,
    SpeechToText,
    VoiceProfileService,
)

from src.db.models import UserModel

profiles = VoiceProfileService(
    profile_model=make_voice_profile_model(user_table="users"),
)
transcriber = ConversationTranscriber(stt=SpeechToText())


async def minutes(session: AsyncSession, participants: list[UserModel]) -> str:
    """Transcribe a meeting with each line attributed to a person."""
    conversation = await transcriber.transcribe(
        "meeting.wav",
        identify_with=profiles,
        session=session,
        user_ids=[p.id for p in participants],
    )
    return conversation.transcript()
```

!!! danger "This is biometric data"
    A voiceprint identifies a person the way a fingerprint template does.
    Under Brazil's LGPD it is **sensitive personal data** (Art. 5, II), and
    processing it needs consent that is **specific and highlighted** for that
    purpose (Art. 11, I) — general terms of service do not cover it.

    So `consent_reference` is required and a blank one raises
    `ConsentRequired`. The SDK stores the vector and the consent on the same
    row, and **never writes the audio**: the vector cannot be played back,
    which makes a leak of this table cost far less than a leak of the
    recordings.

    `forget_user()` is a method rather than an example in the docs, because
    "delete my biometric data" is an unconditional right (Art. 18, VI) and must
    not depend on each project getting the `WHERE` clause right.

!!! tip "Restrict to the participants"
    `user_ids=[...]` turns "who in the whole database is this voice" into
    "which of these five people is it". Faster, and far less likely to put a
    stranger's name on a line.

Measured with real voices: enrolling from one turn and identifying a
**different** turn by the same person scored 0.687 and 0.734; an unenrolled
speaker came back `None`. The default threshold is 0.5 — raise it for anything
that grants access, where the expensive error stops being "did not recognise"
and becomes "recognised the wrong person".

Swapping the embedding model invalidates every enrolled profile: the vectors
stop being comparable and people silently stop being recognised.
`stale_profiles()` finds who needs re-enrolling.

### Serving it over HTTP and from the shell

```python
# src/api/app.py

from fastapi import Depends, FastAPI

from tempest_fastapi_sdk.genai.audio import make_voice_router

from src.api.dependencies.auth import current_user_id
from src.core.resources import db, profiles, transcriber


def create_app() -> FastAPI:
    """Mount the voice routes behind the service's own auth."""
    app = FastAPI()
    app.include_router(
        make_voice_router(
            session_factory=db.session_dependency,
            transcriber=transcriber,
            profiles=profiles,
            current_user_id=current_user_id,
            dependencies=[Depends(current_user_id)],
        ),
    )
    return app
```

Four routes: `POST /voice/transcribe`, plus `POST` / `GET` / `DELETE`
`/voice/profiles`.

!!! warning "`current_user_id` is required alongside `profiles`"
    Enrolling or erasing against a user id taken from the request **body**
    would let anyone write biometric data into somebody else's account. The
    router refuses that combination at wiring time — failing in production
    would be too late.

The listing and deletion routes are not a courtesy: they are the person's right
over their own data. And the listing **does not return the embedding** — they
need to know the profile exists, not to receive a copy of their own biometric
template over HTTP.

From the shell:

```bash
tempest voice models                        # fetch the models (do this at build)
tempest voice diarize meeting.wav -n 2      # who spoke when
tempest voice transcribe meeting.wav -n 2   # who said what
```

`diarize` never loads Whisper — it is the quick way to check the speaker count
and threshold before paying for transcription.

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
