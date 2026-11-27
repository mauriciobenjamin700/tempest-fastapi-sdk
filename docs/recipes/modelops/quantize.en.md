---
description: "Use to quantize an ONNX model dynamically or statically with calibration; covers optimizing and quantizing a HuggingFace export without depending on `optimum`."
---

# Quantize (dynamic, static and HuggingFace)

Use to quantize dynamically or statically and to optimize and quantize a HuggingFace export without depending on `optimum`.

Part of the [Modelops (export, bench, quantization)](../modelops.md) recipe.

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
    you accuracy, try `CalibrationMethodEnum.ENTROPY` or `PERCENTILE`: a single
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
from tempest_fastapi_sdk.modelops import (
    HFQuantizationTargetEnum,
    optimize_hf_onnx,
    quantize_hf_onnx,
)

optimized = optimize_hf_onnx("exports/distilbert", "exports/distilbert-o2")
quantized = quantize_hf_onnx(
    "exports/distilbert-o2",
    "exports/distilbert-int8",
    target=HFQuantizationTargetEnum.AVX512_VNNI,
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
from tempest_fastapi_sdk.modelops import optimize_hf_onnx


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
[`TextGenerator`](../genai.md) — can load back:

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
