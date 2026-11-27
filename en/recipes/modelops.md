# Modelops (export, bench, quantization)

Three jobs that always travel together: you **quantize** to make a model
cheaper, you **benchmark** to find out whether it actually got cheaper, and
you **export** to the format the target device runs.

`tempest_fastapi_sdk.modelops` covers all three, measuring CPU, RAM, GPU and
**energy** over the same window — "how fast" and "how much power" come out
of one measurement instead of two unrelated runs.

```bash
uv add "tempest-fastapi-sdk[modelops]"        # benchmarking, monitoring, compact reader
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

## Where each task lives

Use this table to open only the subpage for the task at hand; they are listed in reading order.

| Subpage | What it solves |
| --- | --- |
| [Start here](https://mauriciobenjamin700.github.io/tempest-fastapi-sdk/en/recipes/modelops/start.md) | Use the first time: an example that runs as is, which case is yours and the module's five words. |
| [Export scikit-learn and `.pkl`](https://mauriciobenjamin700.github.io/tempest-fastapi-sdk/en/recipes/modelops/sklearn.md) | Use to export a scikit-learn model (including one from a `.pkl`) to ONNX or to the runtime-free compact format. |
| [Serve at the edge and publish the bundle](https://mauriciobenjamin700.github.io/tempest-fastapi-sdk/en/recipes/modelops/edge.md) | Use to serve the model (with a GPU or over HTTP), swap it without a redeploy, publish the edge bundle and know what is worth optimizing. |
| [Know whether the model still works](https://mauriciobenjamin700.github.io/tempest-fastapi-sdk/en/recipes/modelops/monitoring.md) | Use to monitor drift in production: three signals, a training baseline, PSI, constant memory, HTTP and Prometheus. |
| [Playbook: where time and bytes go](https://mauriciobenjamin700.github.io/tempest-fastapi-sdk/en/recipes/modelops/playbook.md) | Look up the measured decisions, in order of attack: estimator, transport, monitoring cost and cold start. |
| [Measure: time, CPU, GPU, RAM, energy and comparison](https://mauriciobenjamin700.github.io/tempest-fastapi-sdk/en/recipes/modelops/measure.md) | Use to measure before optimizing, time any callable, sample resources and energy and rank models by score and Pareto. |
| [Quantize (dynamic, static and HuggingFace)](https://mauriciobenjamin700.github.io/tempest-fastapi-sdk/en/recipes/modelops/quantize.md) | Use to quantize dynamically or statically and to optimize and quantize a HuggingFace export without depending on `optimum`. |
| [Fuse, `.ort`, inspect, API and CLI](https://mauriciobenjamin700.github.io/tempest-fastapi-sdk/en/recipes/modelops/formats.md) | Use to fuse a detector and a classifier, convert `.onnx` to `.ort`, inspect without running and expose the report through an API or the CLI. |

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
