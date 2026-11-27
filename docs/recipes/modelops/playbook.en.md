# Playbook: where time and bytes go

Look up the measured decisions, in order of attack: estimator, transport, monitoring cost and cold start.

Part of the [Modelops (export, bench, quantization)](../modelops.md) recipe.

This section is not theory — these are measurements taken **with this SDK's
own instruments** (`benchmark_models`, `analyze_onnx`, `rank`,
`PredictionMonitor`) on a 12-core machine, across 7 candidates trained on the
same dataset (20 features, 3 classes, 6000 samples).

## 1. Choose the estimator, not the post-processing

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

## 2. Latency is not the axis — transport is

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

## 3. Monitoring has a cost, and it was measured

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

## 4. Cold start: what booting costs

| Step | Cost |
| --- | --- |
| `read_manifest` | 0.15 ms |
| `load_edge_package` (266 KB, with SHA-256) | 2.16 ms |
| the same, `verify_digest=False` | 2.00 ms |

Checking the digest costs **0.16 ms** — leave it on. And `read_manifest`
being 14x cheaper than loading is what makes it viable to ask "is there a new
version?" on a schedule without touching the graph.

## 5. Order of attack

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
