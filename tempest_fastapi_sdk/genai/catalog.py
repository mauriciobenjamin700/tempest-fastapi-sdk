"""Named model ids for the tasks the SDK runs locally.

Every genai class takes a **string** model id, and a typo in one only
surfaces when the download 404s — after the process is up, sometimes
after minutes of unrelated work. These enums put the ids the SDK is
actually exercised against behind names, so the editor completes them and
a typo is a `NameError` at import.

They are a **starting point, not a whitelist**: the constructors keep
taking plain strings, so any Hub id still works.

    >>> from tempest_fastapi_sdk.genai import TextGenerator, TextModel
    >>> gen = TextGenerator(TextModel.QWEN2_5_7B_INSTRUCT)

Every id here resolved on huggingface.co when it was added (the Coqui TTS
names come from the Coqui model list, not the Hub). Sizes quoted in the
tables are the bf16 weights — int8 halves them, int4 quarters them, which
is what `recommend()` picks between.

See the docs page **Escolhendo o modelo / Choosing a model** for the
use-case table that goes with these values.
"""

from __future__ import annotations

from enum import StrEnum


class TextModel(StrEnum):
    """Chat and instruction models for `TextGenerator` and agents.

    | Member | Params | Use it when |
    | --- | --- | --- |
    | `QWEN2_5_0_5B_INSTRUCT` | 0.5B | CI, tests, laptop CPU |
    | `QWEN2_5_1_5B_INSTRUCT` | 1.5B | Smallest that tool-calls reliably |
    | `QWEN2_5_3B_INSTRUCT` | 3B | Small GPU (6 GB) or patient CPU |
    | `QWEN2_5_7B_INSTRUCT` | 7B | Default for real work, 8-16 GB VRAM |
    | `QWEN2_5_14B_INSTRUCT` | 14B | 24 GB VRAM, better multi-step goals |
    | `QWEN2_5_CODER_7B_INSTRUCT` | 7B | Code generation and review |
    | `PHI_3_5_MINI_INSTRUCT` | 3.8B | Best reasoning per gigabyte |
    | `MISTRAL_7B_INSTRUCT_V03` | 7B | European multilingual, Apache-2.0 |
    | `LLAMA_3_1_8B_INSTRUCT` | 8B | Gated: needs licence + Hub token |
    """

    QWEN2_5_0_5B_INSTRUCT = "Qwen/Qwen2.5-0.5B-Instruct"
    QWEN2_5_1_5B_INSTRUCT = "Qwen/Qwen2.5-1.5B-Instruct"
    QWEN2_5_3B_INSTRUCT = "Qwen/Qwen2.5-3B-Instruct"
    QWEN2_5_7B_INSTRUCT = "Qwen/Qwen2.5-7B-Instruct"
    QWEN2_5_14B_INSTRUCT = "Qwen/Qwen2.5-14B-Instruct"
    QWEN2_5_CODER_7B_INSTRUCT = "Qwen/Qwen2.5-Coder-7B-Instruct"
    PHI_3_5_MINI_INSTRUCT = "microsoft/Phi-3.5-mini-instruct"
    MISTRAL_7B_INSTRUCT_V03 = "mistralai/Mistral-7B-Instruct-v0.3"
    LLAMA_3_1_8B_INSTRUCT = "meta-llama/Llama-3.1-8B-Instruct"


class EmbeddingModel(StrEnum):
    """Sentence embedders for `Embedder`, RAG and semantic search.

    | Member | Dim | Use it when |
    | --- | --- | --- |
    | `ALL_MINILM_L6_V2` | 384 | English only, fastest, RAG default |
    | `PARAPHRASE_MULTILINGUAL_MINILM_L12_V2` | 384 | Cheap PT-BR |
    | `MULTILINGUAL_E5_LARGE` | 1024 | Best PT-BR retrieval |
    | `BGE_M3` | 1024 | Multilingual, 8k context, dense+sparse |

    `MULTILINGUAL_E5_LARGE` expects the ``query: `` / ``passage: ``
    prefixes on every text — without them retrieval quality drops to
    roughly the MiniLM level it was chosen to beat.
    """

    ALL_MINILM_L6_V2 = "sentence-transformers/all-MiniLM-L6-v2"
    PARAPHRASE_MULTILINGUAL_MINILM_L12_V2 = (
        "sentence-transformers/paraphrase-multilingual-MiniLM-L12-v2"
    )
    MULTILINGUAL_E5_LARGE = "intfloat/multilingual-e5-large"
    BGE_M3 = "BAAI/bge-m3"


class RerankerModel(StrEnum):
    """Cross-encoders for `Reranker`, the second stage of retrieval.

    | Member | Use it when |
    | --- | --- |
    | `MS_MARCO_MINILM_L6_V2` | English, small and fast — the usual pick. |
    | `BGE_RERANKER_V2_M3` | Multilingual, including PT-BR; heavier and better. |
    """

    MS_MARCO_MINILM_L6_V2 = "cross-encoder/ms-marco-MiniLM-L6-v2"
    BGE_RERANKER_V2_M3 = "BAAI/bge-reranker-v2-m3"


class VisionModel(StrEnum):
    """Vision-language models for `VisionTextGenerator`.

    | Member | Params | Use it when |
    | --- | --- | --- |
    | `QWEN2_VL_2B_INSTRUCT` | 2B | Captions, agent self-checks, CPU |
    | `QWEN2_VL_7B_INSTRUCT` | 7B | Real visual QA, 16 GB VRAM |
    | `LLAVA_1_5_7B` | 7B | The classic, widely documented baseline |
    """

    QWEN2_VL_2B_INSTRUCT = "Qwen/Qwen2-VL-2B-Instruct"
    QWEN2_VL_7B_INSTRUCT = "Qwen/Qwen2-VL-7B-Instruct"
    LLAVA_1_5_7B = "llava-hf/llava-1.5-7b-hf"


class ImageModel(StrEnum):
    """Diffusion checkpoints for `ImageGenerator`.

    Step counts differ by an order of magnitude between these — a turbo
    checkpoint wants ~4 steps and a full one ~30, so pass
    ``default_steps`` on the tool rather than letting a model guess.

    | Member | Steps | Use it when |
    | --- | --- | --- |
    | `SDXL_TURBO` | ~4 | Fast previews, agent loops, CPU-tolerable. |
    | `SDXL_BASE_1_0` | ~30 | Final quality at 1024², 10+ GB VRAM. |
    | `FLUX_1_SCHNELL` | ~4 | Best prompt adherence of the fast ones; 16+ GB VRAM. |
    """

    SDXL_TURBO = "stabilityai/sdxl-turbo"
    SDXL_BASE_1_0 = "stabilityai/stable-diffusion-xl-base-1.0"
    FLUX_1_SCHNELL = "black-forest-labs/FLUX.1-schnell"


class SpeechToTextModel(StrEnum):
    """Whisper sizes for `SpeechToText` (faster-whisper names, not Hub ids).

    | Member | Use it when |
    | --- | --- |
    | `TINY` | Smoke tests; misses accents and numbers. |
    | `BASE` | The default — usable PT-BR on CPU. |
    | `SMALL` | Noticeably better on noisy audio. |
    | `MEDIUM` | Interviews, calls, where the transcript is the product. |
    | `LARGE_V3` | Best accuracy; GPU or a lot of patience. |
    """

    TINY = "tiny"
    BASE = "base"
    SMALL = "small"
    MEDIUM = "medium"
    LARGE_V3 = "large-v3"


class TextToSpeechModel(StrEnum):
    """Coqui TTS voices for `TextToSpeech`.

    | Member | Use it when |
    | --- | --- |
    | `XTTS_V2` | Multilingual + voice cloning — the default, heaviest. |
    | `VITS_PT_BR` | PT-BR only, fast, no cloning. |
    | `VITS_EN` | English only, fast. |
    """

    XTTS_V2 = "tts_models/multilingual/multi-dataset/xtts_v2"
    VITS_PT_BR = "tts_models/pt/cv/vits"
    VITS_EN = "tts_models/en/ljspeech/vits"
