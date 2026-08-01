# Modelops (export, bench, quantization)

Three jobs that always travel together: you **quantize** to make a model
cheaper, you **benchmark** to find out whether it actually got cheaper, and
you **export** to the format the target device runs.

`tempest_fastapi_sdk.modelops` covers all three, measuring CPU, RAM, GPU and
**energy** over the same window — "how fast" and "how much power" come out
of one measurement instead of two unrelated runs.

```bash
uv add "tempest-fastapi-sdk[modelops]"        # benchmarking only
uv add "tempest-fastapi-sdk[modelops-onnx]"   # + ONNX, .ort, quantization
```

!!! check "Nothing here caps your `transformers` version"
    Two extras, and neither pulls `optimum` — which today declares
    `transformers<4.58`. The HuggingFace path (optimizing and quantizing an
    export) runs on the `onnxruntime` from `[modelops-onnx]`, so your service
    stays free to use the 5.x series. The one step that does need `optimum`
    is producing the export, and that becomes a throwaway `uvx` command —
    see "HuggingFace: optimize and quantize an export".

!!! info "Submodule, not top-level"
    Like `genai`/`vision`, this is heavy tooling and lives in a submodule:
    `from tempest_fastapi_sdk.modelops import benchmark_onnx`. The module
    imports with **no extra installed** — every dependency is resolved
    inside the function that needs it, and its absence raises an
    `ImportError` naming the extra to install.

## scikit-learn to the edge

Classic scikit-learn models are the most common embedded case: small, fast,
and stuck to Python for as long as they live as a `.pkl`. Exporting to ONNX
takes Python, NumPy and scikit-learn itself off the device.

```bash
uv add "tempest-fastapi-sdk[modelops-onnx,modelops-sklearn]"
```

```python
from tempest_fastapi_sdk.modelops import edge_bundle

bundle = edge_bundle(
    model,                       # a fitted estimator or Pipeline
    X_train[:50],                # only to shape the graph
    "dist/",
    name="classifier",
    verify_samples=X_test,       # held-out data, not the export rows
)

print(bundle.deployable)
print(bundle.verification.passed, bundle.verification.label_agreement)
```

### Three decisions the SDK makes for you

| Decision | Why |
| --- | --- |
| **float32**, not float64 | scikit-learn works in double precision; edge runtimes want single. Half the memory, and the precision accelerators implement. It **changes the numbers** — hence the verification. |
| **ZipMap off** | By default `skl2onnx` wraps probabilities in a `ZipMap`: a **dictionary per row**. Convenient in Python, unusable on a minimal runtime that does not implement the operator. |
| **Always verify** | An export that silently disagrees with the model you trained is worse than one that fails, because you ship it. |

### What measuring showed

Run against real estimators, three results the docs would rather state than
let you discover:

!!! warning "int8 quantisation does not apply to most scikit-learn models"
    Trees, linear models and scalers convert to `ai.onnx.ml` operators whose
    parameters are node attributes, not weight tensors. There is no matrix to
    requantise, and the quantiser refuses with `Failed to find proper ai.onnx
    domain`. `edge_bundle` detects this and **skips with the reason** rather
    than failing opaquely.

!!! warning "Optimising and converting to `.ort` often makes the file bigger"
    These graphs are kilobytes; the metadata added outweighs what is saved.
    So `edge_bundle` ships the **smallest** artifact produced, not the last
    one — handing back a larger file and calling it optimised would be a lie
    the tool tells on its own.

!!! danger "Tree + binary classification converts incorrectly today"
    With `skl2onnx` 1.20 and scikit-learn 1.9, a binary
    `RandomForestClassifier` produces a graph whose probability output is a
    score in `[-1, 1]` rather than `[0, 1]`, and the predicted labels
    disagree with the estimator on a significant fraction of rows.
    Multi-class and linear models are correct.

    No converter option fixes it — `zipmap`, `raw_scores` and four target
    opsets were tried. `export.warnings` flags the combination, and
    verification catches it:

    ```python
    export = export_sklearn_to_onnx(model, X[:10], "m.onnx")
    if export.needs_verification:
        print(export.warnings[0])
    ```

    Alternatives: a multi-class formulation, a linear model, or pinning
    versions you have validated.

### With and without a GPU

**CPU (the common edge case):** the win is dropping Python and linking a
minimal ONNX Runtime build — not quantisation, which as above rarely applies
here.

**With a GPU:** keep the `.onnx` and pick the provider at load time:

```python
import onnxruntime

session = onnxruntime.InferenceSession(
    "dist/classifier.onnx",
    providers=["CUDAExecutionProvider", "CPUExecutionProvider"],
)
```

Measure before assuming it helped — these models are small, and the cost of
moving data to the GPU can exceed the gain of computing there:

```python
from tempest_fastapi_sdk.modelops import benchmark_onnx

profile = benchmark_onnx("dist/classifier.onnx", providers=["CUDAExecutionProvider"])
print(profile.runtime.latency_ms_median)
```


## Serving the model on the device

Exporting produces the file. This is everything between that file and an
answer — code every consumer rewrites identically and gets wrong the same
way.

```python
from tempest_fastapi_sdk.modelops import OnnxPredictor

predictor = OnnxPredictor("dist/classifier.onnx")
result = predictor.predict([[5.1, 3.5, 1.4, 0.2]])

print(result.labels, result.probabilities[0])
```

```text
[0] [0.98, 0.02, 0.0]
```

The predictor resolves what you would otherwise resolve by hand: **which
input is the input** (the name is not constant across exporters), **which
output is a label and which is a score** (indexing `[1]` works until you
serve a regressor), dtype coercion, and the warm-up — the first call pays
for allocation and kernel selection.

!!! danger "Threads: the rule is the shape of the workload, not the size of the device"
    The default here is `intra_op_threads=1`, and the reason is not the
    obvious one. It is not that threads "hurt on a small device" — it is
    that in a service with N concurrent requests, per-request threads
    oversubscribe the CPU and **every** request gets slower.

    Measured on a 12-core machine, a 300-tree forest over 20 features:

    | Threads | 1 row | 1000 rows |
    | --- | --- | --- |
    | 1 | 0.019 ms | 16.6 ms |
    | 2 | 0.013 ms | 8.2 ms |
    | 4 | 0.012 ms | 4.2 ms |
    | 8 | 0.010 ms | 2.3 ms |

    A batch scales nearly linearly. A small graph does not: the same
    measurement on a logistic regression gave 0.213 ms for 1000 rows at one
    thread and 0.214 ms at eight — there is no work to split.

    So: **a batch or a large ensemble wants threads**; one row at a time on
    a small graph does not care; a service serving many callers at once
    wants this default. Measure on the target device:

    ```python
    from tempest_fastapi_sdk.modelops import benchmark_onnx

    profile = benchmark_onnx("dist/classifier.onnx", n_repetitions=200)
    print(profile.runtime.latency_ms_median)
    ```

### With a GPU

```python
predictor = OnnxPredictor(
    "dist/classifier.onnx",
    providers=["CUDAExecutionProvider", "CPUExecutionProvider"],
)
print(predictor.info.providers)
```

!!! warning "Always include the CPU fallback"
    Without it, a driver problem becomes a failed load rather than a slower
    answer. And check `info.providers`: ONNX Runtime **falls back to CPU
    silently**, so the device you believe is on the GPU may not be.

### Serving over HTTP

```python
from fastapi import FastAPI

from tempest_fastapi_sdk.modelops import OnnxPredictor, make_prediction_router

app = FastAPI()
app.include_router(make_prediction_router(OnnxPredictor("dist/classifier.onnx")))
```

| Route | What it does |
| --- | --- |
| `POST /api/predict/` | Predicts for a batch of rows |
| `GET /api/predict/model` | What is loaded, providers **in use**, threads |
| `POST /api/predict/model/sync` | Reloads from the registry (only with a `source`) |

A row of the wrong width is a **422**, not a 500 — it is a client error.

### Swapping the model without a deploy

```python
from tempest_fastapi_sdk.modelops import RegistryModelSource

source = RegistryModelSource(registry, "fraud-classifier", cache_dir="models/")
app.include_router(make_prediction_router(predictor, source=source))
```

The device asks the `ArtifactRegistry` which version is current, downloads it
if it does not have it, and reloads. Call `source.sync(predictor)` from a
periodic task — it is a no-op when the right version is already loaded.

!!! check "A bad rollout degrades to the previous version, never to nothing"
    The new session is built **before** the old one is dropped. A corrupt
    file leaves the predictor serving the previous model rather than taking
    the device out of service. A fleet that can go silent from a deploy is
    worse than one that is occasionally out of date.

    One file per version in `cache_dir`, so a rollback is a reload rather
    than a re-download. Nothing is deleted automatically — on a small disk
    you want to decide when old versions go.


## The edge package: one directory, two runtimes

`edge_bundle` answers "what does each optimisation stage cost on my model".
The next question is: **what do I actually publish, and how does the thing
running it know what it got.**

```python
from tempest_fastapi_sdk.modelops import edge_pipeline

package = edge_pipeline(
    model,
    X_train,
    "dist/risk",
    name="risk",
    labels=y_train,
    feature_names=["age", "income", "tenure", "score", "visits"],
)
print(package.manifest.version, package.manifest.verified)
```

```text
cc17b06c76d4 True
```

Four files come out, and you publish the whole directory:

```text
dist/risk/
├── risk.onnx          the graph
├── risk.onnx.gz       the same, at 10-13% of the size
├── baseline.json      drift reference, taken from training
└── manifest.json      the contract
```

### Why a manifest

A published model is never one file. Whoever runs it needs the **column
order** that produced the training, the classes it can answer, the digest to
know the download arrived whole, and the version to know whether it already
has this one.

Without a manifest all of that lives in a wiki page that goes stale — and
the failure is silent: a model served with two columns swapped answers
confidently and wrongly.

```python
from tempest_fastapi_sdk.modelops import load_edge_package

loaded = load_edge_package("dist/risk")
result = loaded.predictor.predict(rows)
loaded.monitor.observe(rows, result)
```

One line gives you a predictor plus a monitor already wired to the package's
baseline, with the version stamped onto every report. `manifest.json` is
plain JSON and carries a `schema_version`: the same directory is served as
static assets to
[`tempest-react-sdk/tabular`](https://mauriciobenjamin700.github.io/tempest-react-sdk/en/tabular/),
which reads the same file in the browser.

!!! check "A truncated download fails as a digest mismatch, not as a parse error"
    `load_edge_package` checks the SHA-256 before loading. Half a model
    becomes a message that says so — instead of a protobuf error, or worse,
    nothing.

!!! danger "An export that does not reproduce the estimator does not pass"
    The pipeline verifies against the estimator's own predictions and
    **raises** when they disagree. It is the one outcome it refuses to let
    through quietly: there are known converter defects (binary tree
    ensembles on skl2onnx 1.20) that produce a graph which runs smoothly and
    answers wrongly.

## What is actually worth optimising (measured)

I ran the stages on real forests of 10 to 300 trees exported from
scikit-learn. Three of the four do not pay:

| Stage | 10 trees | 50 trees | 300 trees |
| --- | --- | --- | --- |
| Exported `.onnx` | 381 KB | 1,955 KB | 12,061 KB |
| Graph optimisation | 381 KB | 1,955 KB | 12,061 KB |
| `.ort` conversion | 878 KB | 4,497 KB | 26,970 KB |
| **gzip** | **51 KB** | **226 KB** | **1,266 KB** |

- **Graph optimisation changes nothing** (0.1 KB): `ai.onnx.ml` operators
  are single nodes, there is nothing to fuse.
- **`.ort` more than doubles it**, at every scale. It is a loading format,
  not a compression one.
- **int8 quantisation does not apply**: tree and linear parameters are node
  attributes, not tensors.
- **gzip takes it to 10-13%** and costs one `Content-Encoding` header.

That is why `edge_pipeline` runs export → verify → baseline → manifest +
gzip, and nothing else. Use `edge_bundle` when you want those stages
measured on **your** model rather than trusting the table.

### Size is decided before you export

The estimator is the lever, not the post-processing. A 50-tree forest over
20 features, 3 classes, accuracy on a held-out test set:

| `max_depth` | Size | Accuracy | 1 row |
| --- | --- | --- | --- |
| 3 | 36 KB | 0.797 | 0.0073 ms |
| 6 | 257 KB | 0.881 | 0.0075 ms |
| 12 | 1,275 KB | 0.918 | 0.0078 ms |
| unlimited | 1,444 KB | 0.922 | 0.0079 ms |

`max_depth=6` fits in 1/5.6 of the space for 4 points of accuracy. And look
at the last column: **latency is not what you are trading** — it barely
moves. On the edge the cost is bytes, not milliseconds.

Same lesson in the tree count: 10 → 300 trees multiplies the file by 32
(381 KB → 12 MB) and the latency by 2.7 (0.0073 → 0.0199 ms/row).

!!! tip "Input: pass an array, not a list of lists"
    Measured over 1000 rows: `float32` 2.65 ms, `float64` 2.66 ms (the
    conversion costs nothing measurable), Python list-of-lists 2.99 ms. Only
    the last one shows up, and by ~12%.

## Knowing whether the model still works

The device answers in 3 ms. That says nothing about whether the answers are
right.

In production there are no labels — nobody tells the device it just
misclassified something — so **accuracy is not measurable there**. What *is*
measurable is whether the world still looks like the one the model was
trained on, and whether the model's own output has shifted. Both are
proxies, and the implementation says so rather than pretending otherwise.

```python
from tempest_fastapi_sdk.modelops import PredictionMonitor, baseline_from_samples

baseline = baseline_from_samples(X_train, labels=y_train)
monitor = PredictionMonitor(baseline=baseline)

result = predictor.predict(rows)
monitor.observe(rows, result)

report = monitor.report()
print(report.drift.verdict, report.drift.worst_psi)
```

```text
significant 3.95
```

### Three signals, because they separate different failures

| Signal | Catches |
| --- | --- |
| Latency and volume | A thermally throttled device, a provider that fell back to CPU |
| Input drift | A sensor that changed units, a form that changed a default, a season the training data never saw |
| Prediction distribution | Inputs within their usual ranges, combined in a way that pushes every row to one class |

Reading the last two together is what gives a diagnosis:

!!! tip "How to read the combination"
    - **Input moved, output stable** → usually a harmless covariate shift.
    - **Output moved, input stable** → the model is extrapolating.
    - **Both moved** → retrain, do not tune a threshold.

### The baseline comes from training, not from production

`baseline_from_samples` keeps **bin edges and proportions**, never the rows.
That is a few kilobytes and no records — small enough to version alongside
the model.

```python
from pathlib import Path

Path("dist/baseline.json").write_text(baseline.model_dump_json())
```

!!! danger "Building the baseline from production traffic defeats the measurement"
    It would describe the **already drifted** population as normal. The
    baseline comes from the training set, at training time.

### PSI, and what it is not

The metric is the **Population Stability Index**, the credit-scoring
standard for decades: `< 0.1` stable, `0.1-0.25` moved, `> 0.25` moved
enough to distrust the calibration.

!!! warning "A convention, not a statistical test"
    PSI has no p-value and no null distribution. It does not tell you the
    shift is significant — only that it is large by a rule of thumb the
    industry agreed on. Crossing a threshold is a reason to **look**, not a
    reason to act automatically.

Below `MIN_ROWS_FOR_DRIFT` (100 rows) the verdict is `insufficient_data`,
not `stable`: with 30 rows across 10 bins, an empty bin is the expected
outcome of sampling. "We do not have traffic yet" and "there is no drift"
are different answers, and the second one would lie on the dashboard.

### Constant memory

Rows are counted into bins and discarded. The cost is
`n_features x n_bins` counters regardless of traffic — nothing accumulates a
copy of the requests, which also means no feature value stays in memory to
leak into a log or a crash dump.

Drift is measured **per window** (`DEFAULT_WINDOW_ROWS`, 1000 rows). When
one closes it becomes the last complete measurement and the counters reset,
so the numbers describe recent traffic rather than everything since boot —
which would take days to react to a real shift.

### Over HTTP and in Prometheus

```python
from tempest_fastapi_sdk.modelops import PredictionMetrics

app.include_router(
    make_prediction_router(
        predictor,
        monitor=monitor,
        metrics=PredictionMetrics(),
    ),
)
```

`GET /api/predict/monitor` returns the whole report; the metrics go to the
same registry the SDK's `/metrics` endpoint serves
(`edge_model_predictions_total`, `edge_model_prediction_seconds`,
`edge_model_feature_drift_psi{feature}`,
`edge_model_prediction_share{label}`).

!!! check "Swapping the model resets the monitor"
    `POST /model/sync` calls `monitor.reset()` when the version actually
    changes. Mixing two versions into one percentile would hide exactly the
    regression a fleet update needs to catch.

    Without a baseline the monitor still records latency and the output
    distribution — a device with no baseline should not be left with no
    monitoring at all.

## Playbook: where the time and the bytes actually go

This section is not theory — these are measurements taken **with this SDK's
own instruments** (`benchmark_models`, `analyze_onnx`, `rank`,
`PredictionMonitor`) on a 12-core machine, across 7 candidates trained on the
same dataset (20 features, 3 classes, 6000 samples).

### 1. Choose the estimator, not the post-processing

```python
from tempest_fastapi_sdk.modelops import benchmark_models

report = benchmark_models(
    ["dist/logreg/logreg.onnx", "dist/mlp/mlp.onnx", "dist/forest/forest.onnx"],
    quality={"logreg": 0.787, "mlp": 0.966, "forest": 0.931},
    n_repetitions=200,
)
for profile in report.profiles:
    print(profile.name, profile.composite_score, profile.is_pareto)
```

What came out:

| Model | Accuracy | `.onnx` | gzip | p50 |
| --- | --- | --- | --- | --- |
| logreg | 0.787 | 1.0 KB | 0.9 KB (84%) | 0.0039 ms |
| tree d8 | 0.833 | 16.6 KB | 3.8 KB (23%) | 0.0032 ms |
| forest 50 d6 | 0.887 | 265.8 KB | 40.5 KB (15%) | 0.0046 ms |
| forest 300 | 0.931 | 13,464 KB | — | 0.0082 ms |
| hist gb | 0.951 | 669.1 KB | — | 0.0064 ms |
| **MLP (64,32)** | **0.966** | **15.3 KB** | — | 0.0062 ms |

!!! tip "The 'tabular means forest' reflex costs 880x the size for worse accuracy"
    The 300-tree forest delivers 0.931 in 13.4 MB. The small MLP delivers
    **0.966 in 15.3 KB**, in the same latency band. On a fleet that downloads
    models over the air, that is the difference between an update and an
    incident.

    It is not a law — it is the result on this dataset. The point is that
    only measurement tells you, and `benchmark_models` is three lines.

!!! warning "gzip does not pay the same on every model"
    Measured: forest 15%, tree 23%, **logistic regression 84%**. A small
    dense model has no redundancy to compress. The "10-13%" rule holds for
    tree ensembles, which is where size hurts.

### 2. Latency is not the axis — transport is

Measured on `forest_50_d6` with an in-process `TestClient` (no network, so
this is the **floor**):

| Batch | Inference | Monitor | HTTP total | µs per row (HTTP) |
| --- | --- | --- | --- | --- |
| 1 | 0.0075 ms | 0.0061 ms | 1.22 ms | 1,223 |
| 8 | 0.0147 ms | 0.0112 ms | 1.37 ms | 171 |
| 64 | 0.0988 ms | 0.0493 ms | 2.16 ms | 34 |
| 512 | 0.7708 ms | 0.3626 ms | 8.42 ms | 16 |

!!! danger "One request per row spends 99.4% of its time outside the model"
    For a single row, HTTP costs **1.2 ms against 0.0075 ms** of inference —
    160x. Changing model here changes nothing you can feel; **batching
    does**: from batch 1 to 512, the per-row cost falls from 1,223 µs to
    16 µs, 74x.

    The recommended flow: accumulate on the client and send batches. If
    per-item response latency is a requirement, the right place for the model
    is **next to the caller** — on the device (`load_edge_package`) or in the
    browser
    ([`tempest-react-sdk/tabular`](https://mauriciobenjamin700.github.io/tempest-react-sdk/en/tabular/)),
    where the round trip simply does not exist.

Per-row inference plateaus at about 1.5 µs from batch 8 — above that you are
paying for serialisation, not for the model.

### 3. Monitoring has a cost, and it was measured

The first version of `PredictionMonitor` binned drift with a loop per
feature and per bin. Measured: **67 µs per single-row call against 7.5 µs of
inference** — monitoring cost 9x predicting.

v0.192.0 vectorised it: one comparison against the padded edge matrix
resolves every feature of every row, and one `bincount` folds the batch in.

| | Before | After |
| --- | --- | --- |
| `observe` 1 row | 67.1 µs | **6.0 µs** |
| `observe` 64 rows | ~104 µs | **49.7 µs** |

Without a baseline (latency and output distribution only) it costs 1.4 µs —
so a device with no baseline should still turn the monitor on.

!!! tip "Measure yours; do not trust this table"
    ```python
    import statistics, time

    def median_us(fn, reps=1000):
        for _ in range(100):
            fn()
        samples = []
        for _ in range(reps):
            started = time.perf_counter()
            fn()
            samples.append((time.perf_counter() - started) * 1e6)
        return statistics.median(samples)

    print(median_us(lambda: predictor.predict(rows)))
    print(median_us(lambda: monitor.observe(rows, prediction)))
    ```

### 4. Cold start: what booting costs

| Step | Cost |
| --- | --- |
| `read_manifest` | 0.15 ms |
| `load_edge_package` (266 KB, with SHA-256) | 2.16 ms |
| the same, `verify_digest=False` | 2.00 ms |

Checking the digest costs **0.16 ms** — leave it on. And `read_manifest`
being 14x cheaper than loading is what makes it viable to ask "is there a new
version?" on a schedule without touching the graph.

### 5. Order of attack

1. **Batch the requests.** 74x per row, without touching the model.
2. **Measure candidates with `benchmark_models` + `quality=`.** The Pareto
   frontier shows what is defensible; `composite_score` (cost, **lower
   wins**) orders within it.
3. **Cut size in the estimator** — depth and tree count, or change family.
   See the depth table above.
4. **Serve gzip.** One header, 85% less network on an ensemble.
5. **Only then touch threads**, and by the shape of the workload (previous
   section).
6. **If per-item latency matters, take HTTP out of the path** — model on the
   device or in the browser.

!!! info "Energy was not measured here, and the SDK says so"
    This environment (WSL2) does not expose `powercap`, so
    `resolve_cpu_energy_sampler()` returns a `NullPowerSampler` and the
    reports carry `energy_source: "unavailable"` — instead of an invented
    number. On a host with RAPL or an NVIDIA GPU, the same calls start
    filling `energy_per_inference_j`.

## Measure before you optimize

Measuring comes first. Without `tempest model bench` you have no baseline
to tell whether quantization helped.

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
    next to the number — `EnergySource.NVML_COUNTER` and `EnergySource.RAPL`
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
from tempest_fastapi_sdk.modelops import rank

report = rank(
    profiles,
    weights={"latency_ms_p99": 0.7, "rss_peak_mb": 0.3},
    quality={"n": 0.802, "s": 0.841},
)
```

The **Pareto frontier** takes no opinion. A model is on it when nothing
else is at least as cheap on every axis *and* at least as good. What
survives is the set of defensible choices:

```python
from tempest_fastapi_sdk.modelops import pareto_points

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

## Quantizing

### Dynamic: no calibration data

Weights quantized ahead of time, activation ranges computed on the fly. It
is the zero-friction option and usually the right first attempt for
transformers and dense models, where the win is in the weights:

```python
from tempest_fastapi_sdk.modelops import quantize_onnx_dynamic

result = quantize_onnx_dynamic(
    "models/classify.onnx",
    "models/classify.int8.onnx",
)
print(result.compression_ratio)
print(result.backend)
```

```bash
tempest model quantize models/classify.onnx models/classify.int8.onnx
```

### Static: with representative samples

A calibration pass runs the model over real inputs to learn the range each
activation actually occupies. Weights **and** activations become integer,
which unlocks the fused int8 kernels — a bigger speedup, and a bigger
accuracy risk:

```python
import numpy as np

from tempest_fastapi_sdk.modelops import quantize_onnx_static

batches = [
    {"images": np.load(f"calib/{index:03d}.npy")} for index in range(128)
]
result = quantize_onnx_static(
    "models/classify.onnx",
    "models/classify.qdq.onnx",
    calibration_inputs=batches,
    per_channel=True,
)
print(result.notes)
```

!!! tip "A few hundred real samples beat tens of thousands of synthetic ones"
    A range learned from noise will clip real activations. If `MINMAX` costs
    you accuracy, try `CalibrationMethod.ENTROPY` or `PERCENTILE`: a single
    outlier stretches a min/max range until everything else quantizes into
    a handful of levels.

!!! danger "Quantization is lossy — re-measure accuracy"
    How much int8 costs is a property of your model, and nothing in this
    module can predict it. Run your evaluation set on the quantized
    artifact before shipping. When one specific layer collapses, use
    `nodes_to_exclude=` to leave just that one in float.

## HuggingFace: optimize and quantize an export

```bash
uv add "tempest-fastapi-sdk[modelops-onnx]"
```

### Step 0: producing the export is out of scope, on purpose

Turning an arbitrary architecture into ONNX needs a per-architecture graph
description, and the only maintained registry of those lives in HuggingFace
`optimum` — which declares `transformers<4.58`. A cap like that travels to
**everyone** who installs the SDK, so it does not go in here. The export
becomes a build step you run in a throwaway environment:

```bash
uvx --from "optimum[onnxruntime]" optimum-cli export onnx \
    --model distilbert-base-uncased --task text-classification \
    exports/distilbert
```

!!! tip "Why `uvx` instead of an extra"
    `uvx` resolves `optimum` in a temporary environment and throws it away
    afterwards. The `transformers` cap stays in there and never touches your
    project — you keep running `transformers` 5.x at runtime. Same
    capability, without tying the package down.

### Steps 1 and 2: fuse and quantize

The directory that command wrote is the input to both functions below.
Neither touches `optimum`: they run on the `onnxruntime` that
`[modelops-onnx]` already brings.

```python
from tempest_fastapi_sdk.modelops import optimize_hf_onnx, quantize_hf_onnx

optimized = optimize_hf_onnx("exports/distilbert", "exports/distilbert-o2")
quantized = quantize_hf_onnx(
    "exports/distilbert-o2",
    "exports/distilbert-int8",
    target="avx512_vnni",
)
print(optimized.size_ratio, quantized.compression_ratio)
```

`optimize_hf_onnx` is **lossless in precision** at `O1`/`O2`: it fuses
attention, layer norm and friends into single kernels without changing what
the graph computes. `O3` swaps in an approximate GELU and `O4` converts to
float16 — those two do move the numbers, and `O4` is GPU-only.

The fusion type comes from the export's `config.json`. An architecture
outside the mapping is **reported, never guessed** — fusing a graph as the
wrong shape yields a model that loads and returns wrong numbers. When that
happens, choose it yourself:

```python
optimized = optimize_hf_onnx(
    "exports/my-architecture",
    "exports/my-architecture-o2",
    model_type="bert",
)
```

`model_type=` also lets you optimize a bare graph with no `config.json`
beside it.

!!! note "Exports with several graphs"
    Encoder-decoder models export several `.onnx` files into one directory
    (`encoder_model.onnx`, `decoder_model.onnx`…). Pass `file_name=` to pick
    which one to process — each goes separately. Without it the functions
    raise `ValueError` listing what they found, rather than picking one at
    random.

`target` picks the instruction set: `arm64` (phones, Raspberry Pi, Apple
silicon, Graviton), `avx2`, `avx512` or `avx512_vnni` (the fastest int8 path
on x86). Picking the wrong one still produces a valid model, just a slow one.

!!! info "`reduce_range` only exists where it means something"
    AVX2 and AVX512 without VNNI can saturate accumulating int8, and dropping
    to 7 bits avoids it. ARM64 and VNNI do not have the problem — there
    `reduce_range=True` would be pure accuracy loss, so it is refused with a
    `ValueError` instead of accepted and ignored.

There is no `tensorrt` target: that profile is **static** quantization, and
`quantize_hf_onnx` is the dynamic path. For a TensorRT artifact use
`quantize_onnx_static` (the "Static: with representative samples" section
above) with your own calibration data.

Both steps copy the export's non-graph files (`config.json`, tokenizer,
preprocessor) into the output directory, so the result stays loadable by
`AutoTokenizer`.

For generative models that stay in PyTorch there is the bitsandbytes path,
which saves int4/int8 weights that `AutoModelForCausalLM` — and therefore
[`TextGenerator`](genai.md) — can load back:

```python
from tempest_fastapi_sdk.modelops import quantize_hf_bnb

result = quantize_hf_bnb(
    "Qwen/Qwen2.5-0.5B-Instruct",
    "models/qwen-int4",
    bits=4,
    quant_type="nf4",
)
print(result.notes)
```

Needs `[genai]` + `[genai-quant]` and a CUDA GPU: bitsandbytes has no CPU
kernel for the conversion.

!!! danger "`trust_remote_code=True` executes remote Python"
    `quantize_hf_bnb` accepts the flag because some Hub architectures require
    it. It runs arbitrary code from the remote repository on your machine —
    only enable it for a repository you audited.

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
    production, run it through [TaskIQ](queue-tasks.md) and return the
    stored report.

## CLI

| Command | What it does |
| --- | --- |
| `tempest model analyze <model>` | Parameters, size, opset and shapes, without running it. |
| `tempest model bench <model>` | Latency, memory and energy over N repetitions. |
| `tempest model quantize <in> <out>` | Dynamic int8 quantization. |
| `tempest model optimize <in> <out>` | Persists ONNX Runtime's graph optimizations. |
| `tempest model export-ort <model>` | Converts to `.ort` plus the operator config. |
| `tempest model hardware` | What this host runs, and what it can measure. |

They all accept `--json` (except `export-ort` and `optimize`, which already
print the written paths), which makes them usable as a CI step:

```bash
tempest model bench models/classify.onnx --json > bench.json
```

## Recap

- Measure **before** optimizing, with warm-up and repetitions — `tempest
  model bench` or `benchmark_onnx`.
- Report **median + IQR**, the hardware and the `energy_source`. No reading
  here is wall-plug.
- Compare with a **composite score** (weights published) **and** the Pareto
  frontier; `quality` is yours, the SDK does not invent it.
- Quantize dynamic first, static when you have calibration data — and
  **re-measure accuracy** either way.
- For HuggingFace: export with `optimum-cli` through `uvx` (outside the
  project, so its `transformers` ceiling never enters), then
  `optimize_hf_onnx` → `quantize_hf_onnx` on the `onnxruntime` you already
  have.
- For the edge: `.onnx` → `.ort` with `target_platform` and the minimal
  build's `.required_operators.config`.
