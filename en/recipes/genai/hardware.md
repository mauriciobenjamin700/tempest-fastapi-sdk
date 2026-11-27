# Can the machine handle it? Hardware and estimates

Use before downloading weights: probe CPU, RAM and GPU (containers included) and estimate whether the model fits.

Part of the [Self-hosted generative AI](https://mauriciobenjamin700.github.io/tempest-fastapi-sdk/en/recipes/genai.md) recipe.

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
generator, or `ModelDtypeEnum.INT8`/`INT4` for `quantization=`.

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
    each). Use [`OllamaGenerator`](https://mauriciobenjamin700.github.io/tempest-fastapi-sdk/en/recipes/genai/ollama.md) — memory is in the same
    range as bitsandbytes int4; the measured advantage is speed. For an
    agent, what that buys per step (and where it does not: prefill) is in
    [Agents on CPU](https://mauriciobenjamin700.github.io/tempest-fastapi-sdk/en/recipes/agents.md#agents-on-cpu-backend-size-and-budget).

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

### Environment warnings

`HardwareInfo.warnings` lists the environment problem the probe can see
**before** a load fails — each item is a sentence with the cause and the fix.
An empty list means nothing was detected, not that every load will succeed.
Today there is one warning: a CUDA 13 `torch` build (`torch.version.cuda`
starting with `13.`) with CUDA available and faster-whisper's `ctranslate2`
installed. The `ctranslate2` wheels look up `libcublas.so.12`, and the first
transcription with `device="cuda"` would fail
([GPU with `[genai-audio]`](https://mauriciobenjamin700.github.io/tempest-fastapi-sdk/en/installation.md#genai-audio-gpu)). `ctranslate2`
is looked up with `importlib.util.find_spec`, not imported, so the probe loads
no CUDA library for it.

```python
from tempest_fastapi_sdk.genai import probe_hardware

for warning in probe_hardware().warnings:
    print(warning)
```

In a `python:3.12-slim` container with an RTX 4070 Ti SUPER, PyPI's `torch`
2.15.0+cu132 and `ctranslate2` 4.8.5:

```text
torch is a CUDA 13.2 build, but ctranslate2 (faster-whisper) links against CUDA 12 and looks for libcublas.so.12, which this torch does not install: transcribing on device='cuda' raises 'Library libcublas.so.12 is not found or cannot be loaded' unless the system provides CUDA 12 libraries. Install torch from the CUDA 12 index (https://download.pytorch.org/whl/cu126) instead; see 'GPU with [genai-audio]' in the installation docs.
```

With `torch` 2.14.1+cu126 from the cu126 index, on the same machine, the list
came back empty. The warning cannot see a system-wide CUDA 12 install — which
makes the pair work —, which is why the text says "unless the system provides
CUDA 12 libraries".

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
from tempest_fastapi_sdk.genai import estimate_model_bytes, ModelDtypeEnum

gb = estimate_model_bytes(7_000_000_000, ModelDtypeEnum.INT4) / 1e9
print(f"~{gb:.1f} GB")   # 7B in int4
```

The parameter count can be passed explicitly (`num_params=`) or read from
the Hub by `model_id` (via `huggingface_hub`, without downloading the
weights — safetensors metadata).
