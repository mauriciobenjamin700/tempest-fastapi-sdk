---
description: "Use to serve the model at the edge on GPU or over HTTP and swap it without a redeploy; covers the edge bundle with its manifest and what is worth optimizing, measured."
---

# Serve at the edge and publish the bundle

Use to serve the model (with a GPU or over HTTP), swap it without a redeploy, publish the edge bundle and know what is worth optimizing.

Part of the [Modelops (export, bench, quantization)](../modelops.md) recipe.

## Serving the model on the device

Exporting produces the file. This is everything between that file and an
answer — code every consumer rewrites identically and gets wrong the same
way.

```python
from sklearn.datasets import load_iris
from sklearn.ensemble import RandomForestClassifier
from sklearn.model_selection import train_test_split

from tempest_fastapi_sdk.modelops import OnnxPredictor, export_sklearn_to_onnx

X_train, X_test, y_train, y_test = train_test_split(
    *load_iris(return_X_y=True), random_state=0
)
model = RandomForestClassifier(n_estimators=20, random_state=0).fit(
    X_train, y_train
)
export_sklearn_to_onnx(model, X_train[:10], "dist/classifier.onnx")


predictor = OnnxPredictor("dist/classifier.onnx")
result = predictor.predict([[5.1, 3.5, 1.4, 0.2]])

print(result.labels, result.probabilities[0])
```

```text
[0] [1.0000001192092896, 0.0, 0.0]
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
from tempest_fastapi_sdk.modelops import OnnxPredictor


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

A row of the wrong width is a **422**, not a 500 — it is a client error. So
is a batch larger than `max_rows` (`DEFAULT_MAX_PREDICT_ROWS`, 10,000 rows;
pass `max_rows=None` to remove the limit): without a ceiling, one request
decides how much memory and how many seconds of inference the device spends
on it.

Inference and reload run in a worker thread (`asyncio.to_thread`), not on the
event loop. Measured with a predictor that holds its thread for 1 s: on
0.299.0 the loop stood still for 0.992 s — no other request was answered in
that time —; now the scheduling delay stays under 1 ms.

!!! warning "The router ships no authentication"
    `GET /model`, `POST /model/sync` and `GET /monitor` are operational
    routes: the second makes the device download and reload a model. Guard
    them with `admin_dependencies` — `POST /` stays as open as the rest of
    the service — or every route with `dependencies`:

    ```python
    import hmac

    from fastapi import Depends, FastAPI, Header, HTTPException

    from tempest_fastapi_sdk.modelops import OnnxPredictor, make_prediction_router

    OPERATOR_TOKEN: str = "change-me"


    def require_operator(x_token: str = Header(default="")) -> None:
        if not hmac.compare_digest(x_token, OPERATOR_TOKEN):
            raise HTTPException(status_code=401, detail="operator token required")


    app = FastAPI()
    app.include_router(
        make_prediction_router(
            OnnxPredictor("dist/classifier.onnx"),
            admin_dependencies=[Depends(require_operator)],
        ),
    )
    ```

    `GET /model` returns only the file **name** in `path` (`classifier.onnx`),
    not the absolute path, which describes the device's disk and is of no use
    to a client. `expose_model_path=True` brings the full path back.

### Swapping the model without a deploy

```python
from pathlib import Path

from fastapi import FastAPI

from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine
from tempest_fastapi_sdk import ArtifactRegistry
from tempest_fastapi_sdk import BaseRepository
from tempest_fastapi_sdk.artifacts import ArtifactRegistry
from tempest_fastapi_sdk.modelops import (
    OnnxPredictor,
    RegistryModelSource,
    make_prediction_router,
)

from src.db.models import ModelVersion

# In a service the session comes from `db.get_session_context()`; here, SQLite.
session = AsyncSession(create_async_engine("sqlite+aiosqlite:///:memory:"))

predictor = OnnxPredictor("model.onnx")
registry = ArtifactRegistry(BaseRepository(session, model=ModelVersion))
app = FastAPI()


source = RegistryModelSource(registry, "fraud-classifier", cache_dir="models/")
app.include_router(make_prediction_router(predictor, source=source))
```

The device asks the `ArtifactRegistry` which version is current, downloads it
if it does not have it, and reloads. Call `source.sync(predictor)` from a
periodic task — it is a no-op when the right version is already loaded.

!!! check "A bad rollout degrades to the previous version, never to nothing"
    The new session is built **and warmed** (one throwaway inference)
    **before** the old one is dropped. A corrupt file — or one that loads but
    cannot answer the input its own graph declares — makes `reload` raise
    and leaves the predictor serving the previous model rather than taking
    the device out of service. A fleet that can go silent from a deploy is
    worse than one that is occasionally out of date.

    One file per version in `cache_dir`, so a rollback is a reload rather
    than a re-download. Nothing is deleted automatically — on a small disk
    you want to decide when old versions go.

The download lands in a `.part` file and is renamed to its final name only
once it completes, so a connection dropped mid-file is downloaded again on
the next sync instead of becoming a "cached version". `sync` holds a lock
from fetch to reload: the periodic task and a `POST /model/sync` at the same
time download and reload the version **once**.

!!! tip "Verifying what arrived: a `sha256` column"
    `ArtifactVersionMixin` declares no digest. Declare a `sha256` column on
    your version model, filled in by whoever publishes the file, and every
    download is checked before it is cached or loaded — a file that does not
    match is deleted and the sync fails with `503`, the previous model still
    serving. A row without the attribute (or with it empty) downloads
    unverified; another column name goes in
    `RegistryModelSource(..., checksum_field="digest")`, and
    `checksum_field=None` turns the check off.


## The edge package: one directory, two runtimes

`edge_bundle` answers "what does each optimisation stage cost on my model".
The next question is: **what do I actually publish, and how does the thing
running it know what it got.**

```python
from sklearn.datasets import load_iris
from sklearn.ensemble import RandomForestClassifier
from sklearn.model_selection import train_test_split

from tempest_fastapi_sdk.modelops import edge_pipeline

X_train, X_test, y_train, y_test = train_test_split(
    *load_iris(return_X_y=True), random_state=0
)
model = RandomForestClassifier(n_estimators=20, random_state=0).fit(
    X_train, y_train
)


package = edge_pipeline(
    model,
    X_train,
    "dist/risk",
    name="risk",
    labels=y_train,
    feature_names=["sepal_length", "sepal_width", "petal_length", "petal_width"],
)
print(package.manifest.version, package.manifest.verified)
```

```text
55a78cb2a1d4 True
```

`feature_names` needs one name per graph column. Passing five names for
iris's four raises before the manifest is written:
`ValueError: 5 feature names for a model of 4 features: the manifest would
record a column order no reader can apply.`

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
from sklearn.datasets import load_iris
from sklearn.model_selection import train_test_split

from tempest_fastapi_sdk.modelops import load_edge_package

X_train, X_test, y_train, y_test = train_test_split(
    *load_iris(return_X_y=True), random_state=0
)

rows = X_test.tolist()


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
    through quietly: a **binary** tree classifier used to answer wrongly —
    the probability came back as a score in `[-1, 1]` — while the graph ran
    smoothly regardless. Measured with `skl2onnx` 1.20.0, `sklearn` 1.9.0
    and `onnx` 1.22.0 held fixed and only the runtime moving, the culprit
    was clear: **`onnxruntime`**, not the converter. Error of 1.0 against
    `predict_proba` on 1.27.0, 9.5e-08 on 1.28.0. The SDK's floor is
    `onnxruntime>=1.29.0` — above the 1.28.0 that fixed the defect — and
    the export still warns if it finds an older runtime force-installed.

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
