# Image generation (local)

The SDK already generated text, understood images (VLM), embedded,
transcribed and synthesized speech. What it could not do was **draw**.
`ImageGenerator` closes that gap by running a HuggingFace diffusion model on
your own hardware — no paid API, nothing leaving the machine.

```bash
uv add "tempest-fastapi-sdk[genai,genai-image]"
```

!!! info "It mirrors `TextGenerator`"
    Same device/precision resolution, same lazy load, same
    `unload_if_idle`, same Hub pinning keywords
    (`revision=`/`local_files_only=`/`trust_remote_code=`). A service that
    already self-hosts an LLM gains images without learning a second set of
    conventions.

!!! warning "`[genai-image]` carries an upper bound"
    `diffusers` declares `httpx<1.0.0` and `huggingface-hub<2.0`. Neither
    bites today (httpx is still on the 0.28 line), and being an optional
    extra the bound only enters the resolution of whoever installs it. Still:
    if your service comes to depend on httpx 1.x, this extra is the first
    place to look.

## The first drawing

```python
from pathlib import Path

from tempest_fastapi_sdk.genai import ImageGenerator

generator = ImageGenerator("stabilityai/sdxl-turbo")
images = await generator.generate("a lighthouse at dawn")
Path("lighthouse.png").write_bytes(images[0].data)
print(images[0].seed, images[0].width, images[0].height)
```

```text
418223901 512 512
```

Notice what came back: **not loose bytes**, a list of `GeneratedImage` with
the **seed** attached. Diffusion is deterministic given a seed, so returning
it is the difference between "nice image, gone forever" and "nice image, and
here is how to get it back". When you pass no seed, only the generator knows
which one was drawn — which is why it reports it.

## Configuring: turbo and full models want opposite things

```python
from tempest_fastapi_sdk.genai import ImageGenerationConfig, ImageGenerator

turbo = ImageGenerator("stabilityai/sdxl-turbo")
images = await turbo.generate(
    "a lighthouse at dawn",
    config=ImageGenerationConfig(steps=4, guidance_scale=0.0, seed=7),
)
```

```python
from tempest_fastapi_sdk.genai import ImageGenerationConfig, ImageGenerator

full = ImageGenerator("stabilityai/stable-diffusion-xl-base-1.0")
images = await full.generate(
    "a lighthouse at dawn",
    config=ImageGenerationConfig(
        steps=30,
        guidance_scale=7.5,
        width=1024,
        height=1024,
        negative_prompt="blurry, watermark",
    ),
)
```

| Field | Turbo / distilled model | Full model |
| --- | --- | --- |
| `steps` | 1–8 | 20–50 |
| `guidance_scale` | `0.0` | 5–9 |
| `width`/`height` | the model's native size | 1024 on SDXL |

!!! tip "Only what you set is sent"
    Unset fields fall through to the model's own defaults. That matters more
    here than for text: passing `steps=30` to a turbo model wastes 26 of
    them, and passing `guidance_scale=7.5` to it degrades the image.

## Reproducing

```python
from tempest_fastapi_sdk.genai import ImageGenerationConfig, ImageGenerator

generator = ImageGenerator("stabilityai/sdxl-turbo")
first = await generator.generate(
    "a lighthouse at dawn",
    config=ImageGenerationConfig(seed=7, steps=4, guidance_scale=0.0),
)
again = await generator.generate(
    "a lighthouse at dawn",
    config=ImageGenerationConfig(seed=7, steps=4, guidance_scale=0.0),
)
print(first[0].data == again[0].data)
```

```text
True
```

Same seed, same prompt, same hardware → same image. Across different GPUs
the result can drift slightly (kernels and reduction order are not
identical), so treat the seed as reproducibility *on your* host, not as a
universal hash.

## Redrawing an existing image

```python
from pathlib import Path

from tempest_fastapi_sdk.genai import ImageGenerator

generator = ImageGenerator("stabilityai/sdxl-turbo")
edited = await generator.edit(
    "the same room, at night",
    "room.png",
    strength=0.6,
)
Path("room-night.png").write_bytes(edited[0].data)
```

`strength` says how far to move from the input: near `0.0` keeps the
composition almost intact, `1.0` all but ignores the original. The input
accepts a path, `bytes`, a `PIL.Image` or a NumPy array.

!!! check "The edit pipeline costs no extra VRAM"
    It is built with `AutoPipelineForImage2Image.from_pipe`, which **reuses**
    the already-loaded UNet, VAE and text encoders instead of reading a
    second copy off disk. An SDXL pipeline is ~7 GB; loading it twice on one
    card is how a service OOMs at the first edit request.

## Serving it over HTTP

```python
from fastapi import FastAPI

from tempest_fastapi_sdk.genai import ImageGenerator, make_genai_router

app = FastAPI()
app.include_router(
    make_genai_router(image_generator=ImageGenerator("stabilityai/sdxl-turbo")),
)
```

```bash
curl -X POST http://127.0.0.1:8000/api/genai/image \
  -H "Content-Type: application/json" \
  -d '{"prompt": "a lighthouse at dawn", "config": {"steps": 4, "guidance_scale": 0.0}}' \
  --output lighthouse.png --dump-header -
```

```text
HTTP/1.1 200 OK
content-type: image/png
x-image-seed: 418223901
```

The response body **is** the image, so the route returns only the first one;
the seed travels in the `X-Image-Seed` header. Want a batch? Use the class
directly — the route exists for the common case of one image per request.

## Not holding the GPU hostage

```python
from tempest_fastapi_sdk.genai import ImageGenerator

generator = ImageGenerator(
    "stabilityai/sdxl-turbo",
    idle_unload_seconds=300.0,
)
```

A periodic task calls `generator.unload_if_idle()` and the VRAM comes back
after five idle minutes. The next request reloads.

!!! note "Concurrency defaults to 1, on purpose"
    Unlike an LLM, one diffusion call already saturates the GPU. Running two
    concurrently makes both slower and **doubles peak VRAM**. Raise
    `max_concurrent` only once you have measured spare card.

## Pinning the model, like everything else

```python
from tempest_fastapi_sdk.genai import ImageGenerator

generator = ImageGenerator(
    "stabilityai/sdxl-turbo",
    revision="f4b0486b498f84668e828044de1d0c8ba486e05b",
    cache_dir="/var/lib/models",
    local_files_only=True,
)
```

The same three keywords as every other loader. Download ahead of serving
with `tempest model pull` and see
[Model weights (Hub lifecycle)](model-weights.md) — a diffusion pipeline is
several gigabytes, and paying for it inside the first request hurts more
here than anywhere else.

## Load-time decisions: `pipeline_kwargs`

Some choices happen **while loading**, not while drawing — and there the
escape hatch is no help, because the cost is already paid. Pass those through
`pipeline_kwargs`:

```python
from tempest_fastapi_sdk.genai import ImageGenerator

generator = ImageGenerator(
    "stabilityai/stable-diffusion-2-1",
    pipeline_kwargs={
        "safety_checker": None,
        "variant": "fp16",
        "use_safetensors": True,
    },
)
```

| Key | Why it matters |
| --- | --- |
| `safety_checker: None` | Stable Diffusion 1.x/2.x repositories bundle an extra CLIP purely to filter. It costs memory and sometimes returns a blank image. |
| `variant: "fp16"` | Fetches the half-precision weights — usually halves the download. |
| `use_safetensors: True` | Refuses a pickle checkpoint. |

!!! warning "Turning the filter off is your call, and it has a licence"
    The Stable Diffusion licence asks that unfiltered results not be exposed
    publicly. Disable it knowing your use case — `diffusers` itself warns at
    runtime when you do.

Keys in `pipeline_kwargs` are applied **last**, so they win over whatever the
SDK computed — that is also how you override `torch_dtype`.

## Swapping the scheduler (escape hatch)

```python
from tempest_fastapi_sdk.genai import ImageGenerator

generator = ImageGenerator("stabilityai/stable-diffusion-xl-base-1.0")
pipeline = generator.pipeline
print(type(pipeline).__name__)
```

`.pipeline` returns the `diffusers` object (loading it on first access). Use
it to swap the scheduler, attach a LoRA or enable a memory optimization the
SDK does not wrap — the SDK covers the common path and gets out of the way
for the rest.

## Recap

- **`generate(prompt, config=...)`** draws; it returns `GeneratedImage`
  carrying the **seed** that reproduces the result.
- **`ImageGenerationConfig`** types `steps`/`guidance_scale`/`width`/
  `height`/`seed`/`num_images`; turbo and full models want opposite values.
- **`edit(prompt, image, strength=...)`** redraws, reusing the already-loaded
  components — no extra VRAM.
- **`make_genai_router(image_generator=...)`** publishes `POST /image`, with
  the seed in the header.
- **`idle_unload_seconds`** hands the VRAM back when nobody is drawing.
- **`revision=`/`local_files_only=`** pin the model, exactly like the rest of
  the SDK.

Where to go next: [Self-hosted generative AI](genai.md) for text, embeddings
and RAG, and [Model weights](model-weights.md) for the download lifecycle.
