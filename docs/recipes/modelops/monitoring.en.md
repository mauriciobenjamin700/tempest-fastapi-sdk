---
description: "Use to detect model drift in production without labels; covers the three signals, a training baseline, PSI, constant memory and exposure over HTTP and Prometheus."
---

# Know whether the model still works

Use to monitor drift in production: three signals, a training baseline, PSI, constant memory, HTTP and Prometheus.

Part of the [Modelops (export, bench, quantization)](../modelops.md) recipe.

The device answers in 3 ms. That says nothing about whether the answers are
right.

In production there are no labels — nobody tells the device it just
misclassified something — so **accuracy is not measurable there**. What *is*
measurable is whether the world still looks like the one the model was
trained on, and whether the model's own output has shifted. Both are
proxies, and the implementation says so rather than pretending otherwise.

```python
from sklearn.datasets import load_iris
from sklearn.ensemble import RandomForestClassifier
from sklearn.model_selection import train_test_split

from tempest_fastapi_sdk.modelops import (
    OnnxPredictor,
    PredictionMonitor,
    baseline_from_samples,
    export_sklearn_to_onnx,
)

X_train, X_test, y_train, y_test = train_test_split(
    *load_iris(return_X_y=True), random_state=0
)
model = RandomForestClassifier(n_estimators=20, random_state=0).fit(
    X_train, y_train
)
export_sklearn_to_onnx(model, X_train[:10], "model.onnx")
predictor = OnnxPredictor("model.onnx")


baseline = baseline_from_samples(X_train, labels=y_train)

held_out = X_test.tolist()
monitor = PredictionMonitor(baseline=baseline)
monitor.observe(held_out, predictor.predict(held_out))
report = monitor.report()
print(report.drift.n_rows, report.drift.verdict, round(report.drift.worst_psi, 2))

shifted = [[value * 1.3 for value in row] for row in X_train.tolist()]
monitor = PredictionMonitor(baseline=baseline)
monitor.observe(shifted, predictor.predict(shifted))
report = monitor.report()
print(report.drift.n_rows, report.drift.verdict, round(report.drift.worst_psi, 2))
```

```text
38 insufficient_data 0.44
112 significant 5.09
```

The 38 test rows come from the **same** distribution as training and still
give a PSI of 0.44, above the `significant` threshold (0.25): with that few
rows an empty bin is sampling noise. That is why the verdict stays
`insufficient_data` below `MIN_ROWS_FOR_DRIFT` (100 rows). The 112 rows
scaled by 1.3 simulate a sensor that changed scale, and those do come out
`significant`.

## Three signals, because they separate different failures

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

## The baseline comes from training, not from production

`baseline_from_samples` keeps **bin edges and proportions**, never the rows.
That is a few kilobytes and no records — small enough to version alongside
the model.

```python
from pathlib import Path

from sklearn.datasets import load_iris
from sklearn.model_selection import train_test_split

from tempest_fastapi_sdk.modelops import baseline_from_samples

X_train, X_test, y_train, y_test = train_test_split(
    *load_iris(return_X_y=True), random_state=0
)

baseline = baseline_from_samples(X_train, labels=y_train)


Path("dist/baseline.json").write_text(baseline.model_dump_json())
```

!!! danger "Building the baseline from production traffic defeats the measurement"
    It would describe the **already drifted** population as normal. The
    baseline comes from the training set, at training time.

## PSI, and what it is not

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

Labels are compared **by value**, not by their text: `0`, `0.0`,
`numpy.int64(0)` and `numpy.float32(0.0)` all land on the key `"0"`, on both
sides. Before, a baseline built from float targets (`"0.0"`) against a
classifier answering integers (`"0"`) scored PSI 26.24 — `significant` — on
identical distributions; it now scores 0.0. Baselines already saved with
`"0.0"` keys are read the same way, with no need to rebuild them.

## Constant memory

Rows are counted into bins and discarded. The cost is
`n_features x n_bins` counters regardless of traffic — nothing accumulates a
copy of the requests, which also means no feature value stays in memory to
leak into a log or a crash dump.

Predicted labels are capped too: at most `MAX_TRACKED_LABELS` (64) keys of
their own, plus the baseline's classes, which never lose theirs; the rest
count into `OTHER_LABEL` (`"__other__"`). That is what holds a regressor,
whose output is a new value per row: measured with 50,000 distinct values,
the report had 50,000 keys and 889,270 bytes of JSON; with the cap, 65 keys
and 1,350 bytes. A regressor's signal is in `mean`, `minimum` and `maximum`,
which stay exact. `PredictionMonitor(max_labels=...)` changes the cap.

Drift is measured **per window** (`DEFAULT_WINDOW_ROWS`, 1000 rows). When
one closes it becomes the last complete measurement and the counters reset,
so the numbers describe recent traffic rather than everything since boot —
which would take days to react to a real shift.

## Over HTTP and in Prometheus

```python
from fastapi import FastAPI
from sklearn.datasets import load_iris
from sklearn.model_selection import train_test_split

from tempest_fastapi_sdk.modelops import (
    PredictionMonitor,
    OnnxPredictor,
    PredictionMetrics,
    baseline_from_samples,
    make_prediction_router,
)

X_train, X_test, y_train, y_test = train_test_split(
    *load_iris(return_X_y=True), random_state=0
)
monitor = PredictionMonitor(baseline=baseline_from_samples(X_train, labels=y_train))
predictor = OnnxPredictor("model.onnx")
app = FastAPI()


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
