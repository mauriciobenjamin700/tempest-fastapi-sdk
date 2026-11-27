# Measure: time, CPU, GPU, RAM, energy and comparison

Use to measure before optimizing, time any callable, sample resources and energy and rank models by score and Pareto.

Part of the [Modelops (export, bench, quantization)](../modelops.md) recipe.

## Measure before you optimize

Measuring comes first. Without `tempest model bench` you have no baseline
to tell whether quantization helped.

!!! info "The `tempest` command needs the `[cli]` extra (or `[cli-ops]` on a server)"
    Without either, `tempest` exits with code 2 and prints how to install it.
    In development: `uv add --dev "tempest-fastapi-sdk[cli]"`. Details in
    [Installation → CLI](../../installation.md#cli).

```bash
tempest model bench models/classify.onnx --repetitions 50 --warmup 10
```

```text
classify  [cpu / CPUExecutionProvider]
  latency ms : median 12.412  iqr 0.804  p95 14.108  p99 15.902
  throughput : 79.4/s  (50 reps, 10 warm-up, batch 1)
  memory     : rss peak 412.50 MB  gpu peak -
  energy     : -  (unavailable)
  static     : 3,180,000 params  6.20 MB
```

The same thing in Python:

```python
from tempest_fastapi_sdk.modelops import benchmark_onnx

profile = benchmark_onnx(
    "models/classify.onnx",
    n_warmup=10,
    n_repetitions=50,
)
print(profile.runtime.latency_ms_median)
print(profile.runtime.throughput_per_s)
print(profile.static.n_parameters if profile.static else 0)
```

Three things the loop does that a `time.perf_counter()` around the call
does not:

| What | Why |
| --- | --- |
| **Warm-up** | The first calls pay for kernel selection, allocator growth and cuDNN autotuning. They are run and discarded. |
| **Median + IQR** | Latency is heavy-tailed. A mean alone hides exactly the tail your p99 cares about. |
| **Energy alongside** | A GPU and a CPU sampler run for the duration of the timed window. |

!!! warning "Synthetic input measures shape cost only"
    Without `feeds`, inputs are synthesized from the declared shapes. That
    is exact for an image classifier, whose cost depends only on the shape
    — and **misleading** for a detector or an autoregressive decoder, where
    the work depends on the content. Pass real inputs there.

### Symbolic dimensions

A graph declaring `["batch", 3, "height", "width"]` cannot run until you
say what `height` and `width` are. The SDK does **not** guess — feeding a
1x1 image to a CNN produces a confidently wrong number:

```bash
tempest model bench models/detect.onnx --dim height=640 --dim width=640
```

```python
from tempest_fastapi_sdk.modelops import benchmark_onnx

profile = benchmark_onnx(
    "models/detect.onnx",
    dynamic_dims={"height": 640, "width": 640},
    batch_size=1,
)
```

An unnamed leading dimension falls back to `batch_size`; anything else left
unresolved raises `ValueError` naming the missing dimension.

### Real inputs

```python
import numpy as np

from tempest_fastapi_sdk.modelops import benchmark_onnx

batch = {"images": np.load("samples/real_batch.npy")}
profile = benchmark_onnx("models/detect.onnx", feeds=batch, n_repetitions=100)
```

## Benchmark anything

`benchmark` times a zero-argument callable. Everything else in the module
is built on top of it, which is why an ONNX session, a torch module and a
hand-written closure all produce the same `BenchmarkProfile`:

```python
from tempest_fastapi_sdk.modelops import benchmark


def encode() -> int:
    """One unit of work — the thing you want to measure."""
    return sum(index * index for index in range(50_000))


profile = benchmark(encode, name="encode", n_warmup=5, n_repetitions=30)
print(profile.runtime.latency_ms_p99)
```

!!! tip "Build the inputs outside the callable"
    Everything inside it is measured as part of the model. Load the image
    in there and you are timing the disk too.

For PyTorch there is a typed shortcut that switches the module to `eval()`,
runs under `torch.no_grad()` and — on CUDA — brackets every timer with
`torch.cuda.synchronize()`:

```python
import torch

from tempest_fastapi_sdk.modelops import benchmark_torch

profile = benchmark_torch(
    torch.nn.Linear(512, 10),
    torch.randn(1, 512),
    n_warmup=10,
    n_repetitions=50,
)
```

!!! danger "Without synchronizing, a CUDA benchmark measures nothing"
    Kernel launches are asynchronous. Timing without
    `torch.cuda.synchronize()` measures the time to *enqueue* the work —
    close to zero, and entirely wrong. `benchmark_torch` handles it; if you
    call `benchmark` directly against an async backend, pass `sync=`.

## CPU, GPU, RAM and energy

Four samplers behind one `PowerSampler` protocol, so the benchmark loop
never has to know which machine it is on:

| Sampler | Measures | When it works |
| --- | --- | --- |
| `NvmlPowerSampler` | NVIDIA GPU, via `pynvml` | NVIDIA driver present. Prefers the driver's total-energy counter (Volta+), falls back to integrating power on older cards. |
| `NvidiaSmiPowerSampler` | NVIDIA GPU, via the binary | Driver present but no `pynvml`. |
| `RaplEnergySampler` | CPU package energy | Linux bare metal with a readable `/sys/class/powercap`. |
| `NullPowerSampler` | Nothing, and says so | Always. It is every other sampler's fallback. |

```python
from tempest_fastapi_sdk.modelops import (
    resolve_cpu_energy_sampler,
    resolve_power_sampler,
)

gpu = resolve_power_sampler()
cpu = resolve_cpu_energy_sampler()
print(type(gpu).__name__, gpu.available)
print(type(cpu).__name__, cpu.available)
```

The quick way to find out what this host can measure:

```bash
tempest model hardware
```

```text
hardware
  cpu cores  : 12
  ram total  : 67.4 GB
  cuda       : False
energy measurement
  gpu        : NvmlPowerSampler (available)
  cpu        : NullPowerSampler (unavailable)
```

!!! danger "None of these readings is wall-plug"
    A GPU reading excludes the CPU, RAM, PSU losses and cooling; a RAPL
    reading covers the CPU package only. Always publish the `energy_source`
    next to the number — `EnergySourceEnum.NVML_COUNTER` and `EnergySourceEnum.RAPL`
    are not the same quantity. For real at-the-socket consumption, use an
    external power meter.

??? note "Why RAPL is usually unavailable"
    Since CVE-2020-8694 most distributions ship `energy_uj` as `0400 root`,
    because a high-resolution energy trace leaks information about what the
    CPU is doing. On top of that WSL2, containers and most cloud VMs do not
    expose `powercap` at all. In both cases the sampler degrades silently to
    `UNAVAILABLE` — it never raises in the middle of your benchmark.

A CPU run does **not** resolve a GPU sampler by default: attributing a
shared card's idle draw and other processes' VRAM to a model running on the
CPU would be worse than reporting nothing. Pass `power_sampler=`
explicitly to measure the GPU anyway.

## Comparing models: composite score and Pareto

Measuring one model is easy; choosing between five is the real problem.
`benchmark_models` measures them all under the same conditions and ranks
them:

```python
from tempest_fastapi_sdk.modelops import benchmark_models

report = benchmark_models(
    ["models/n.onnx", "models/s.onnx", "models/m.onnx"],
    quality={"n": 0.802, "s": 0.841, "m": 0.856},
    n_warmup=10,
    n_repetitions=50,
)
for profile in report.profiles:
    print(profile.name, profile.composite_score, profile.is_pareto)
print(report.weights)
```

Two readings, deliberately kept side by side.

The **composite score** collapses several cost axes into one number. That
is convenient and it is also an opinion: the weights encode a deployment
scenario. The default is tuned for edge/mobile:

```python
from tempest_fastapi_sdk.modelops import DEFAULT_COST_WEIGHTS

print(DEFAULT_COST_WEIGHTS)
```

```text
{'latency_ms_median': 0.4, 'energy_per_inference_j': 0.25,
 'rss_peak_mb': 0.2, 'disk_size_mb': 0.15}
```

A server with a throughput SLO should re-weight — that is exactly what the
parameter is for:

```python
from sklearn.datasets import load_iris
from sklearn.ensemble import RandomForestClassifier

from tempest_fastapi_sdk.modelops import (
    BenchmarkProfile,
    benchmark_onnx,
    export_sklearn_to_onnx,
    rank,
)

X, y = load_iris(return_X_y=True)
profiles: list[BenchmarkProfile] = []
for name, trees in [("n", 5), ("s", 50)]:
    model = RandomForestClassifier(n_estimators=trees, random_state=0).fit(X, y)
    export_sklearn_to_onnx(model, X[:10], f"models/{name}.onnx")
    profiles.append(benchmark_onnx(f"models/{name}.onnx", n_repetitions=50))


report = rank(
    profiles,
    weights={"latency_ms_p99": 0.7, "rss_peak_mb": 0.3},
    quality={"n": 0.802, "s": 0.841},
)
for profile in report.profiles:
    print(profile.name, profile.composite_score, profile.is_pareto)
```

The **Pareto frontier** takes no opinion. A model is on it when nothing
else is at least as cheap on every axis *and* at least as good. What
survives is the set of defensible choices:

```python
from sklearn.datasets import load_iris
from sklearn.ensemble import RandomForestClassifier

from tempest_fastapi_sdk.modelops import (
    BenchmarkProfile,
    benchmark_onnx,
    export_sklearn_to_onnx,
    pareto_points,
)

X, y = load_iris(return_X_y=True)
profiles: list[BenchmarkProfile] = []
for name, trees in [("n", 5), ("s", 50)]:
    model = RandomForestClassifier(n_estimators=trees, random_state=0).fit(X, y)
    export_sklearn_to_onnx(model, X[:10], f"models/{name}.onnx")
    profile = benchmark_onnx(f"models/{name}.onnx", n_repetitions=50)
    quality = {"n": 0.802, "s": 0.841}[name]
    profiles.append(profile.model_copy(update={"quality": quality}))


for point in pareto_points(profiles):
    if point.is_pareto:
        print(point.name, point.latency_ms, point.quality)
```

!!! tip "Publish the weights, and show the frontier next to the score"
    A scalar score summarizes; Pareto preserves the trade-off. A paper or
    an ADR that shows only the score is hiding the weighting that decided
    the result.

!!! note "Missing measurements do not distort the ranking"
    A dimension **no** profile measured is dropped and the remaining
    weights are renormalized to sum to 1 — benchmarking on a laptop with no
    energy counter compares latency, memory and size on their own terms
    instead of handing everyone the same free 25%. A dimension **some**
    profile is missing is skipped for that profile only.

`quality` is never measured by the SDK: it has no way to know what "good"
means for your task. Without it the frontier degrades to a cost-only one —
useful for saying which models are never worth running, unable to say which
one is best.
