---
description: "Use to export scikit-learn, including from a `.pkl`, to ONNX or the runtime-free compact format; covers GPU, pinning the release hash and what the format refuses."
---

# Export scikit-learn and `.pkl`

Use to export a scikit-learn model (including one from a `.pkl`) to ONNX or to the runtime-free compact format.

Part of the [Modelops (export, bench, quantization)](../modelops.md) recipe.

## scikit-learn to the edge

Classic scikit-learn models are the most common embedded case: small, fast,
and stuck to Python for as long as they live as a `.pkl`. Exporting to ONNX
takes Python, NumPy and scikit-learn itself off the device.

```bash
uv add "tempest-fastapi-sdk[modelops-onnx,modelops-sklearn]"
```

```python
from sklearn.datasets import load_iris
from sklearn.ensemble import RandomForestClassifier
from sklearn.model_selection import train_test_split

from tempest_fastapi_sdk.modelops import edge_bundle

X_train, X_test, y_train, y_test = train_test_split(
    *load_iris(return_X_y=True), random_state=0
)
model = RandomForestClassifier(n_estimators=20, random_state=0).fit(
    X_train, y_train
)


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

!!! note "Tree + binary classification: an old runtime defect, still watched"
    An exported **binary** `RandomForestClassifier` used to answer wrong:
    the probability came back as a score in `[-1, 1]` instead of `[0, 1]`,
    and the labels disagreed with the estimator. The cause was
    `onnxruntime` < 1.28, not the converter (the measurement is in
    [Why a manifest](edge.md#why-a-manifest)). The SDK's floor is
    `onnxruntime>=1.29.0`, so a normal install no longer reproduces it:
    with `onnxruntime` 1.29.0, `skl2onnx` 1.20.0 and scikit-learn 1.9.0, a
    50-tree binary forest on `load_breast_cancer` exports with no warning
    and verifies with `label_agreement == 1.0` and a maximum probability
    difference of `1.8e-07`.

    The warning stays in the code for an environment forced below the
    floor: `export.warnings` only carries the message when the final
    estimator is a binary tree ensemble **and** the installed
    `onnxruntime` predates `BINARY_TREE_FIXED_IN_ONNXRUNTIME` (currently
    `(1, 28)`):

    ```python
    from sklearn.datasets import load_breast_cancer
    from sklearn.ensemble import RandomForestClassifier

    from tempest_fastapi_sdk.modelops import export_sklearn_to_onnx

    X, y = load_breast_cancer(return_X_y=True)
    model: RandomForestClassifier = RandomForestClassifier(
        n_estimators=50, random_state=0
    ).fit(X, y)
    export = export_sklearn_to_onnx(model, X[:10], "m.onnx")
    if export.needs_verification:
        print(export.warnings[0])
    ```

    With the floor installed, this prints nothing.

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


## It arrived as a `.pkl`. Now what?

The training team hands over `joblib.dump(...)`, because that is what every
notebook writes. The browser cannot use it: **a pickle is a Python
program**, not data. Running it would need a whole Python runtime in the
browser — which kills offline, size, and the reason the module exists.

The bridge belongs to the **build**, not to the request path:

```python
from sklearn.datasets import load_iris
from sklearn.model_selection import train_test_split

from tempest_fastapi_sdk.modelops import edge_pipeline_from_pickle

X_train, X_test, y_train, y_test = train_test_split(
    *load_iris(return_X_y=True), random_state=0
)


package = edge_pipeline_from_pickle(
    "artifacts/risk.pkl",
    X_train,
    "dist/risk",
    labels=y_train,
)
print(package.manifest.source.file, package.manifest.verified)
```

```text
risk.pkl True
```

The `.pkl` stays in the pipeline. What goes to devices and browsers is the
ONNX package — which `edge_pipeline` already verifies against the loaded
object's own predictions.

!!! danger "Loading a pickle executes arbitrary code"
    `joblib.load` and `pickle.load` are not parsers: they **run
    instructions from the file**. A pickle from an untrusted source is
    remote code execution, not a risk to weigh.

    That is why `load_sklearn_artifact` takes a **local path** and refuses a
    URL with an explicit error — not as protection (anyone can download
    first), but so the shape "load the model from this URL" never exists in
    the code, since that is what turns a registry into an RCE surface.

    Rule of thumb: `.pkl` only from artifacts **your** pipeline produced, in
    your build environment. Never from an upload, never downloaded by a
    device. It is exactly the asymmetry conversion resolves — **ONNX is
    data, a pickle is a program**.

!!! warning "A pickle carries no version contract you can rely on"
    Measured on scikit-learn 1.9: a model pickled by one version and loaded
    by another emits **no warning at all** and stores no version field — the
    mismatch, when it matters, is silent.

    Reading it once at build time and publishing ONNX trades that class of
    problem for a conversion that either verifies or refuses. The manifest
    records the scikit-learn version **that performed the conversion**,
    which is the only version fact the chain has to offer.

### What the bridge does beyond `joblib.load`

**It recovers the column order.** A model fitted on a DataFrame carries
`feature_names_in_`; the bridge reads it into the manifest. That is the
field that catches the error no runtime check can — the right features in
the wrong order.

**It finds the model inside the dict.** Pipelines almost always dump
`{"model": est, "auc": 0.91, ...}`. With exactly one estimator inside it
resolves by itself; with two it **refuses and lists what it found** rather
than guessing:

```python
from sklearn.datasets import load_iris
from sklearn.model_selection import train_test_split

from tempest_fastapi_sdk.modelops import edge_pipeline_from_pickle

X_train, X_test, y_train, y_test = train_test_split(
    *load_iris(return_X_y=True), random_state=0
)

X = X_test


package = edge_pipeline_from_pickle("bundle.pkl", X, "dist/", key="challenger")
```

**It records provenance.** The `.pkl`'s name, SHA-256 and size go into
`manifest.source`, so a model running on a device six months from now can
still answer "which file produced me".

**It refuses what cannot predict** with a direct message, instead of letting
the failure surface inside the converter.

### Pinning the release digest

Recording the SHA-256 in the manifest answers "which file produced me"
**after** the fact. To refuse a swapped `.pkl` **before** it runs, pass the
digest your release recorded as `expected_sha256`:

```python
from sklearn.datasets import load_iris

from tempest_fastapi_sdk.modelops import (
    ArtifactDigestMismatchError,
    EdgePackage,
    LoadedArtifact,
    edge_pipeline_from_pickle,
    load_sklearn_artifact,
)

RELEASED_SHA256: str = (
    "sha256:e7b7c9febd99888341148a95224c21d846e040c50a83d3252d5ca390447e988b"
)

X_train, y_train = load_iris(return_X_y=True)

artifact: LoadedArtifact = load_sklearn_artifact(
    "artifacts/risk.pkl",
    expected_sha256=RELEASED_SHA256,
)
print(artifact.estimator_type)

try:
    package: EdgePackage = edge_pipeline_from_pickle(
        "artifacts/risk.pkl",
        X_train,
        "dist/risk",
        labels=y_train,
        expected_sha256=RELEASED_SHA256,
    )
except ArtifactDigestMismatchError as exc:
    print(exc.expected, exc.actual)
    raise
```

The comparison runs **before** `joblib.load`. The file is opened once: the
digest is computed through that handle and unpickling reads from the same
handle, so the bytes that were checked are the bytes that run. When the file
differs, the call raises `ArtifactDigestMismatchError` (a `ValueError`
subclass, like `load_edge_package`'s digest check) and nothing is
deserialized:

```text
refusing to load artifacts/risk.pkl: its SHA-256 is e7b7c9febd99888341148a95224c21d846e040c50a83d3252d5ca390447e988b, expected 0000000000000000000000000000000000000000000000000000000000000000. The file was replaced, truncated or is not the released artifact; it was not unpickled.
```

`edge_pipeline_from_pickle` only forwards the parameter to
`load_sklearn_artifact`, so on a mismatch no package directory is created.

**Accepted format:** 64 hexadecimal digits, upper or lower case, with an
optional `sha256:` prefix (in any case) and surrounding whitespace ignored.
`E7B7...`, `sha256:e7b7...` and `SHA256:E7B7...` are equivalent. Anything
else is refused before the file is opened:

```text
expected_sha256 must be a 64-character hex digest, optionally prefixed with 'sha256:'; got 'abc'
```

!!! tip "Where the digest comes from"
    Compute it in the same pipeline step that ran `joblib.dump`
    (`sha256sum artifacts/risk.pkl`) and publish it with the model release —
    in the service's configuration, the registry, the release notes. The
    digest only protects you if it comes from somewhere other than the file
    it checks.

## No runtime at all: the compact format

ONNX in the browser costs **25.6 MB of WebAssembly** (6.0 MB gzipped) before
the first prediction. Against that, the model is noise: a 12-tree forest is
20 KB.

For an app whose only model is tabular, the runtime **is** the download. So
there is a way out that drops the runtime instead of the model:

```python
from sklearn.datasets import load_iris
from sklearn.ensemble import RandomForestClassifier
from sklearn.model_selection import train_test_split

from tempest_fastapi_sdk.modelops import export_sklearn_to_compact

X_train, X_test, y_train, y_test = train_test_split(
    *load_iris(return_X_y=True), random_state=0
)
model = RandomForestClassifier(n_estimators=20, random_state=0).fit(
    X_train, y_train
)


export = export_sklearn_to_compact(model, X_test, "dist/risk.tmc")
print(export.kind, export.size_bytes, export.verified)
```

```text
tree_ensemble 7476 True
```

!!! note "The size depends on the converter version"
    Measured with `scikit-learn` 1.9.0 and `skl2onnx` 1.20.0. The number moves
    when either does — the previous version of this page said `13124`, 43%
    higher. Read it as an order of magnitude, not a constant.

A linear model is a dot product. A tree is a chain of comparisons. Both fit
in **1.49 KB** of JavaScript — the reader lives in
[`tempest-react-sdk/tabular`](https://mauriciobenjamin700.github.io/tempest-react-sdk/en/tabular/),
and this exporter writes what it reads.

In an edge package it travels alongside the graph:

```python
from pathlib import Path

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


package = edge_pipeline(model, X_train, "dist/risk", labels=y_train, compact=True)
print([(r.kind, r.bytes) for r in package.manifest.runtimes])
```

```text
[('onnx', 15324), ('compact', 7476)]
```

The browser picks the route from the manifest.

### What it covers, and what it refuses

| Covers | Does not cover |
| --- | --- |
| Logistic, linear, ridge, SGD, linear SVC | Gradient boosting (sums contributions through a link) |
| Tree, forest, extra-trees | MLP |
| Their regressors | Any transform that is not `(x - offset) / scale` |
| `StandardScaler` / `MinMaxScaler` in a Pipeline | Imputer, encoder, PCA |

!!! danger "Refusing is the feature"
    A format that silently ignored a Pipeline step would produce a model
    that runs and answers wrongly — worse than an error. An estimator or
    transform outside the coverage raises `UnsupportedEstimatorError`
    **naming `export_sklearn_to_onnx`**, which covers everything this does
    not.

!!! check "Verified against scikit-learn, and nothing else counts"
    Reimplementing another library's arithmetic is only defensible with the
    comparison: the exporter runs the written file through the reference
    decoder and compares against the estimator's own `predict` /
    `predict_proba`. If they disagree it **raises** with the measured
    difference; the file stays on disk for inspection only, and the message
    says it must not be shipped.

    Split thresholds are narrowed to float32 rounding **down**, not to
    nearest. scikit-learn stores a threshold as a float64 midpoint between
    two float32 values; when the two are neighbours (`1.6999999` and
    `1.7000000` in iris), ordinary rounding lands on the larger one, and the
    training row holding exactly that value switched sides. The forest
    above, with no `max_depth`, verified on its own training data, was
    refused for that reason up to 0.302.0.

    On the browser side, tests run against fixtures generated here alongside
    scikit-learn's outputs: 7 families, identical labels, probabilities
    matching to 5 decimals.

### The file is data, never code

The classic alternative is emitting JavaScript with the thresholds baked
into `if` statements. That produces something the page has to evaluate — a
strict CSP forbids it, and no reviewer reads it. Here the reader is fixed
and audited; the model is arrays.

Layout `TMC1`: the magic, a `uint32` header length, a JSON header, then the
sections as typed arrays. The header is padded to a multiple of 8 bytes on
purpose — a JavaScript `Float32Array` cannot view an unaligned offset, and
without the padding the browser would have to copy every section instead of
mapping it.
