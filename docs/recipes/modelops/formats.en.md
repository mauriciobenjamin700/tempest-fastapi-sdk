---
description: "Use to fuse a detector and a classifier into one `.onnx`, convert to `.ort`, optimize the graph and inspect without running; covers the report via an API and the CLI."
---

# Fuse, `.ort`, inspect, API and CLI

Use to fuse a detector and a classifier, convert `.onnx` to `.ort`, inspect without running and expose the report through an API or the CLI.

Part of the [Modelops (export, bench, quantization)](../modelops.md) recipe.

## Fusing a detector and a classifier

The rest of this page is the life cycle of **one** model. Composing
**two** is a different job, and the one this step covers: a detector at
640 finding the objects, a classifier at 224 judging each crop, and a
single `.onnx` that runs both.

```python
from pathlib import Path

from tempest_fastapi_sdk.modelops import fuse_detect_classify

fused: Path = Path("dist/fused.onnx")
fuse_detect_classify(
    "detector.onnx",
    "classifier.onnx",
    fused,
    conf_threshold=0.25,
    max_detections=20,
)
```

Serving the resulting file is the
[Computer vision](../vision.md#detection-classification-in-one-graph)
recipe, with `DetectClassify`.

!!! info "Why `onnx.compose.merge_models` does not do this"
    Between the two stages sits a **dynamic** crop: how many boxes there
    are, and where, is known only once the detector has run. Two graphs do
    not concatenate across that. The bridge is `RoiAlign`, and assembling
    it is exactly what `fuse_detect_classify` does.

!!! note "Two extras, because they are two machines"
    Fusing needs `[modelops-compose]` (which brings `onnx`); serving the
    fused graph needs only `[vision]`. The production service never
    imports `onnx`.

    ```bash
    uv add "tempest-fastapi-sdk[modelops-compose]"   # on the build machine
    uv add "tempest-fastapi-sdk[vision]"             # in the service
    ```

!!! tip "It is a re-export, not a wrapper"
    `fuse_detect_classify` is `ort-vision-sdk`'s own symbol, re-exported
    lazily — touching the name imports the library and raises an
    `ImportError` naming the extra when it is missing. Its keyword
    arguments are **not a fixed set**: measured, 18 at `ort-vision-sdk`
    0.8.0 (the floor we declare) and 19 at 0.9.0, `normalization` having
    been added in between. A wrapper written against the floor would have
    silently dropped that argument for anyone on the newer release — so
    the signature you call is always theirs.

## Shipping to the edge: `.onnx` to `.ort`

`.ort` is ONNX Runtime's own serialized format. It matters on mobile and
embedded for two reasons: the graph optimizations are already applied, so
start-up does not pay for them, and the conversion emits a
`.required_operators.config` listing exactly which kernels the model uses —
feed that to a minimal ONNX Runtime build and the binary drops from tens of
megabytes to a few.

```python
from tempest_fastapi_sdk.modelops import export_onnx_to_ort

results = export_onnx_to_ort(
    "models/classify.int8.onnx",
    "dist/mobile",
    target_platform="arm",
    enable_type_reduction=True,
)
for result in results:
    print(result.output_path, result.output_size_mb)
    print(result.extra_files)
```

```bash
tempest model export-ort models/classify.int8.onnx -o dist/mobile -t arm
```

| Parameter | Effect |
| --- | --- |
| `optimization_style` | `FIXED` bakes the optimizations in (smallest, fastest to load — the mobile default); `RUNTIME` keeps the graph re-optimizable on the device. |
| `target_platform` | `"amd64"` or `"arm"` — restricts to optimizations valid there. Set it whenever the converting machine and the target differ, which for a mobile build is always. |
| `enable_type_reduction` | Also records **which data types** each operator needs, so a minimal build can drop unused implementations. |

Pass a directory instead of a file and the conversion is recursive, giving
you one `ExportResult` per `.ort` written.

### Coming from PyTorch

```python
import torch

from tempest_fastapi_sdk.modelops import export_torch_to_onnx

result = export_torch_to_onnx(
    torch.nn.Linear(128, 10),
    "models/linear.onnx",
    example_input=torch.randn(1, 128),
    opset=17,
    input_names=["features"],
    output_names=["logits"],
    dynamic_axes={"features": {0: "batch"}},
)
print(result.opset, result.output_size_mb)
```

!!! tip "Keep fixed whatever can stay fixed"
    A fixed dimension lets the runtime pick faster kernels. Only declare in
    `dynamic_axes` what genuinely has to vary.

!!! note "Opset is compatibility, not capability"
    A newer opset is more expressive; an older one is more portable. Mobile
    runtimes and third-party converters tend to lag, and `12` is still the
    safest floor for those.

### Optimizing the graph without leaving `.onnx`

When `.ort` is not an option but start-up hurts, the same fusions can be
persisted into an `.onnx`:

```python
from tempest_fastapi_sdk.modelops import optimize_onnx_graph

result = optimize_onnx_graph(
    "models/classify.onnx",
    "models/classify.opt.onnx",
)
print(result.size_ratio)
```

!!! warning "An optimized graph is provider-specific"
    A model fused for CUDA can be slower — or fail to load — on a CPU-only
    host. Optimize per target.

## Inspecting without running

`analyze_onnx` reads the artifact and nothing else: instant, and it gives
the same number on any machine — which is what makes it the right thing to
quote next to a latency figure, which is comparable across no machines at
all.

```python
from tempest_fastapi_sdk.modelops import analyze_onnx

metrics = analyze_onnx("models/classify.onnx")
print(metrics.n_parameters, metrics.disk_size_mb, metrics.opset)
for spec in metrics.inputs:
    print(spec.name, spec.shape, spec.dtype)
```

Parameters are summed from the initializer dimensions rather than from the
data — a multi-gigabyte model is inspected without loading a single weight.

`analyze_ort` does the same for `.ort`, with one honest limitation: the
serialized format does not expose the initializer table, so `n_parameters`
stays `0`. Analyze the source `.onnx` when the count matters.

## Exposing the report over an API

`BenchmarkReport` is a Pydantic schema — `None` instead of `NaN` precisely
so it can become JSON:

```python
# src/api/routers/models.py
from fastapi import APIRouter

from tempest_fastapi_sdk.modelops import BenchmarkReport, benchmark_models

router = APIRouter(prefix="/api/models", tags=["models"])


@router.post("/benchmark")
async def run_benchmark(paths: list[str]) -> BenchmarkReport:
    """Measure and rank the given models."""
    return benchmark_models(paths, n_warmup=5, n_repetitions=20)
```

!!! warning "Benchmarking is CPU-bound and slow"
    Do not leave an endpoint like this unauthenticated or unthrottled: it
    holds a worker for seconds and distorts everyone else's latency. In
    production, run it through [TaskIQ](../queue-tasks.md) and return the
    stored report.

## CLI

| Command | What it does |
| --- | --- |
| `tempest model analyze <model>` | Parameters, size, opset and shapes, without running it. |
| `tempest model bench <model>` | Latency, memory and energy over N repetitions. |
| `tempest model export-ort <model>` | Converts to `.ort` plus the operator config. |
| `tempest model optimize <in> <out>` | Persists ONNX Runtime's graph optimizations. |
| `tempest model quantize <in> <out>` | Dynamic quantization, int8 by default (`--weight-type` accepts `uint8`, `int16`, `uint16`, `int4`, `uint4`). |
| `tempest model hardware` | What this host runs, and what it can measure. |
| `tempest model pull <model_id>` | Downloads a HuggingFace model's weights ahead of the first request; `--pin` prints the commit behind the revision. |
| `tempest model cache-list` | Lists what the local weight cache holds, largest first. |
| `tempest model cache-rm <model_id>` | Deletes a model from the local cache; asks for confirmation without `--yes`, and `--dry-run` only measures. |

`--json` exists on `analyze`, `bench`, `hardware`, `pull` and `cache-list`;
`quantize`, `optimize`, `export-ort` and `cache-rm` do not take the flag.
With it, the command becomes a CI step:

```bash
tempest model bench models/classify.onnx --json > bench.json
```
