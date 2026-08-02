# Choosing a model

Every AI object in the SDK takes a **model id as a string**:

```python
from tempest_fastapi_sdk.genai import TextGenerator

gen = TextGenerator("Qwen/Qwen2.5-7B-Instruct")
```

That works, and it fails badly: a typo (`Qwen2.5-7b-Instruct`) only shows
up when the download 404s — sometimes minutes later, in the middle of
something else. Worse, the question that matters is not *how to spell the
id*, it is **which model to pick**.

This page answers both: one enum per task, and a use-case table.

## The enum

```python
from tempest_fastapi_sdk.genai import TextGenerator, TextModel

gen = TextGenerator(TextModel.QWEN2_5_7B_INSTRUCT)
```

`TextModel` is a `StrEnum`: each member **is** the id string, so it drops
straight into anything that takes a `str` — a constructor, settings,
JSON, a query parameter.

```python
from tempest_fastapi_sdk.genai import TextModel

assert TextModel.QWEN2_5_7B_INSTRUCT == "Qwen/Qwen2.5-7B-Instruct"
print(f"loading {TextModel.QWEN2_5_7B_INSTRUCT}")
```

!!! info "A starting point, not a whitelist"
    The constructors still take `str`. The enum covers the models the SDK
    exercises and documents; any Hub id remains valid.

One enum per task:

| Enum | Feeds |
| --- | --- |
| `TextModel` | `TextGenerator`, `Agent`, `AIChatPipeline` |
| `EmbeddingModel` | `Embedder`, `Retriever`, `HybridRetriever` |
| `RerankerModel` | `Reranker` |
| `VisionModel` | `VisionTextGenerator`, `describe_image_tool` |
| `ImageModel` | `ImageGenerator`, `generate_image_tool` |
| `SpeechToTextModel` | `SpeechToText`, `transcribe_audio_tool` |
| `TextToSpeechModel` | `TextToSpeech`, `speak_tool` |

## Text and agents — `TextModel`

| Member | Params | VRAM (bf16) | Use it when |
| --- | --- | --- | --- |
| `QWEN2_5_0_5B_INSTRUCT` | 0.5B | ~1 GB | CI, tests, laptop CPU. It answers; it does not reason. |
| `QWEN2_5_1_5B_INSTRUCT` | 1.5B | ~3 GB | Smallest size that follows a tool-calling prompt consistently. |
| `QWEN2_5_3B_INSTRUCT` | 3B | ~6 GB | A small GPU, or a CPU and some patience. |
| `QWEN2_5_7B_INSTRUCT` | 7B | ~15 GB | **The default for real work.** Fits 8 GB at int8. |
| `QWEN2_5_14B_INSTRUCT` | 14B | ~28 GB | 24 GB of VRAM; clearly better on multi-step goals. |
| `QWEN2_5_CODER_7B_INSTRUCT` | 7B | ~15 GB | Writing and reviewing code. |
| `PHI_3_5_MINI_INSTRUCT` | 3.8B | ~8 GB | Best reasoning per gigabyte; MIT licence. |
| `MISTRAL_7B_INSTRUCT_V03` | 7B | ~15 GB | European multilingual, Apache-2.0. |
| `LLAMA_3_1_8B_INSTRUCT` | 8B | ~16 GB | Gated: accept the licence and use a Hub token. |

!!! tip "Do not memorize VRAM — ask"
    `recommend()` measures the host and picks the precision that fits,
    from bf16 down to int4:

    ```python
    from tempest_fastapi_sdk.genai import TextModel, recommend

    report = recommend(model_id=TextModel.QWEN2_5_7B_INSTRUCT)
    print(report.dtype, report.fits, report.device)
    ```

!!! warning "An agent needs tool calling"
    An `Agent` with tools only works when the backend exposes
    `chat_with_tools`. Models below ~1.5B tend to ignore the schema and
    answer in prose — the agent degrades to a single-shot answerer.

## Embeddings and RAG — `EmbeddingModel`

| Member | Dim | Languages | Use it when |
| --- | --- | --- | --- |
| `ALL_MINILM_L6_V2` | 384 | English | English corpus; fastest, the RAG default. |
| `PARAPHRASE_MULTILINGUAL_MINILM_L12_V2` | 384 | 50+ | Cheap multilingual, runs on CPU. |
| `MULTILINGUAL_E5_LARGE` | 1024 | 100+ | Best non-English retrieval quality. |
| `BGE_M3` | 1024 | 100+ | Long documents (8k tokens), dense + sparse in one model. |

!!! danger "`multilingual-e5` requires prefixes"
    E5 was trained with `query: ` on the question and `passage: ` on the
    chunk. Without them quality drops to MiniLM levels — you pay for 1024
    dimensions and get 384.

Swapping the embedder **invalidates the index**: the stored vectors came
from the old model. Reindex the whole corpus when this line changes.

## Rerank — `RerankerModel`

| Member | Use it when |
| --- | --- |
| `MS_MARCO_MINILM_L6_V2` | English; small and fast, the usual pick. |
| `BGE_RERANKER_V2_M3` | Multilingual; heavier and clearly better. |

The reranker is a **second stage**: the store returns N cheap candidates
and the cross-encoder reorders them down to `top_k`. It reads query and
chunk together, so it is expensive per pair — not a replacement for the
embedder, the fine filter after it.

## Vision — `VisionModel`

| Member | Params | Use it when |
| --- | --- | --- |
| `QWEN2_VL_2B_INSTRUCT` | 2B | Captions, screen reads, an agent checking what it drew. |
| `QWEN2_VL_7B_INSTRUCT` | 7B | Real visual question answering; 16 GB. |
| `LLAVA_1_5_7B` | 7B | The classic baseline, with plenty of material published. |

## Images — `ImageModel`

| Member | Steps | Use it when |
| --- | --- | --- |
| `SDXL_TURBO` | ~4 | Fast previews, agent loops, tolerable on CPU. |
| `SDXL_BASE_1_0` | ~30 | Final quality at 1024², 10+ GB of VRAM. |
| `FLUX_1_SCHNELL` | ~4 | Best prompt adherence among the fast ones; 16+ GB. |

!!! warning "The wrong step count costs 10x"
    A turbo checkpoint wants ~4 diffusion steps and a full one ~30. Let
    the LLM guess and a render takes ten times longer than it needs to —
    pin it on the tool's `default_steps`:

    ```python
    from tempest_fastapi_sdk.agents import generate_image_tool
    from tempest_fastapi_sdk.genai import ImageGenerator, ImageModel

    tool = generate_image_tool(
        ImageGenerator(ImageModel.SDXL_TURBO),
        default_steps=4,
    )
    ```

## Audio — `SpeechToTextModel` and `TextToSpeechModel`

| Member (STT) | Use it when |
| --- | --- |
| `TINY` | Smoke tests; it misses accents and numbers. |
| `BASE` | The default — usable on CPU. |
| `SMALL` | Noisy audio. |
| `MEDIUM` | Interviews, calls — when the transcript is the product. |
| `LARGE_V3` | Best accuracy; a GPU, or a lot of patience. |

| Member (TTS) | Use it when |
| --- | --- |
| `XTTS_V2` | Multilingual + voice cloning. The default, and the heaviest. |
| `VITS_PT_BR` | Brazilian Portuguese only, fast, no cloning. |
| `VITS_EN` | English only, fast. |

## One example tying it together

```python
import asyncio

from tempest_fastapi_sdk.agents import Agent, describe_image_tool, generate_image_tool
from tempest_fastapi_sdk.genai import (
    ImageGenerator,
    ImageModel,
    TextGenerator,
    TextModel,
    VisionModel,
    VisionTextGenerator,
)

agent = Agent(
    TextGenerator(TextModel.QWEN2_5_7B_INSTRUCT),
    tools=[
        generate_image_tool(
            ImageGenerator(ImageModel.SDXL_TURBO),
            default_steps=4,
        ),
        describe_image_tool(VisionTextGenerator(VisionModel.QWEN2_VL_2B_INSTRUCT)),
    ],
)


async def main() -> None:
    """Draw something, then ask the vision model what came out."""
    run = await agent.run(
        "Draw a red bicycle as bike.png, then describe the image.",
    )
    print(run.output)


asyncio.run(main())
```

## Recap

* The id is still a `str` — the enum only names what the SDK exercises.
* `TextModel.QWEN2_5_7B_INSTRUCT` is the starting point for real work;
  `QWEN2_5_0_5B_INSTRUCT` is for tests, not for quality.
* Changing `EmbeddingModel` forces a full reindex.
* A turbo image checkpoint wants ~4 steps; a full one ~30.
* Before downloading 15 GB, ask `recommend()` whether it fits.
