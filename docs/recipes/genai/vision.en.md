# Multimodal vision (local VLM)

Use to ask a locally running vision-language model about an image.

Part of the [Self-hosted generative AI](../genai.md) recipe.

`VisionTextGenerator` is the multimodal sibling of `TextGenerator`: it loads an
`AutoModelForVision2Seq` + `AutoProcessor` and generates text conditioned on
images, on your hardware — parity with `OllamaGenerator`, which already accepts
`images`. Needs `[genai]` + `[genai-vlm]` (Pillow).

```python
import asyncio

from tempest_fastapi_sdk.genai import VisionTextGenerator

gen = VisionTextGenerator("llava-hf/llava-1.5-7b-hf")


async def main() -> None:
    """Run this example."""
    description = await gen.generate(
        "USER: <image>\nDescribe the image.\nASSISTANT:",
        images=["photo.jpg"],
    )


asyncio.run(main())
```

Images are accepted as a path, `bytes`, `PIL.Image` or a NumPy `ndarray` (same
leniency as `ort-vision-sdk`). `generate`/`chat` are image-optional — text-only
calls keep working (it is a `TextBackend`).

`GenerationConfig` applies here field by field as it does on
`TextGenerator`, and so do the per-call arguments — `stop_event` included:

- `seed` uses a generator private to the call for plain sampling;
- `stop` (in the config, or `stop=` per call, which wins over the config)
  ends decoding on the token that completes the stop string, matched against
  the processor's tokenizer. The stop string **stays** in the returned text,
  as on `TextGenerator`;
- the other fields (`max_new_tokens`, `temperature`, `top_p`, `top_k`,
  `repetition_penalty`, `do_sample`) go to `model.generate`.

```python
import asyncio
import threading

from tempest_fastapi_sdk.genai import GenerationConfig, VisionTextGenerator

gen = VisionTextGenerator("llava-hf/llava-1.5-7b-hf")
config = GenerationConfig(max_new_tokens=128, do_sample=False, stop=["\n\n"])


async def main() -> None:
    """Run this example."""
    stop = threading.Event()
    caption: str = await gen.generate(
        "USER: <image>\nWrite a short caption.\nASSISTANT:",
        images=["photo.jpg"],
        config=config,
        stop=["."],
        stop_event=stop,
    )
    print(caption)


asyncio.run(main())
```

Before, `stop` in the config was silently dropped (generation ran past the
stop string), and a per-call `stop=` or `stop_event=` made
`model.generate` raise `ValueError` listing the argument among the unused
`model_kwargs`. `tests/genai/test_vision_text_config.py` walks
`GenerationConfig.model_fields`: a new field the VLM neither applies nor
refuses with a clear error fails the test.

!!! warning "Processor conventions vary by family"
    This class targets the common `processor(text=..., images=...)` interface
    used by LLaVA and Qwen2-VL. Other families may need a thin adapter (image
    placeholder token, chat-template shape). Validate your target model before
    production.
