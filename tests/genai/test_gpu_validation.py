"""Camada-3 behavioral validation on real models (GPU, opt-in).

Marked ``@pytest.mark.gpu`` — deselected by default, run with ``make test-gpu``
(or ``uv run --all-extras pytest -m gpu``) on a machine with CUDA + weights.
These assert real model *behavior* (not just wiring), backing the manual
checklist in ``planning/genai/manual-validation.md``.
"""

from __future__ import annotations

import io

import pytest
from pydantic import BaseModel

from tempest_fastapi_sdk.genai import (
    ClassifierModerator,
    Embedder,
    GenerationConfig,
    OnnxEmbedder,
    TextGenerator,
    VisionTextGenerator,
    cosine_similarity,
)
from tempest_fastapi_sdk.genai.rag import Chunk, Reranker

_MINILM = "sentence-transformers/all-MiniLM-L6-v2"


def _chunk(text: str, index: int) -> Chunk:
    return Chunk(text=text, source="s", index=index)


@pytest.mark.gpu
class TestModerationPtBr:
    async def test_flags_toxic_ptbr_passes_clean(self) -> None:
        mod = ClassifierModerator(
            "textdetox/xlmr-large-toxicity-classifier",
            flagged_labels=["toxic", "LABEL_1"],
            device="cuda",
        )
        toxic = await mod.check("Você é um idiota inútil e burro.")
        clean = await mod.check("Bom dia, tudo bem com você?")
        assert toxic.flagged is True
        assert clean.flagged is False


@pytest.mark.gpu
class TestVlm:
    async def test_describes_image(self) -> None:
        from PIL import Image

        buffer = io.BytesIO()
        Image.new("RGB", (64, 64), (220, 20, 20)).save(buffer, format="PNG")
        gen = VisionTextGenerator("Qwen/Qwen2-VL-2B-Instruct", device="cuda")
        try:
            gen.load()
        except ImportError as exc:
            pytest.skip(f"VLM processor unavailable in this env: {exc}")
        messages = [
            {
                "role": "user",
                "content": [
                    {"type": "image"},
                    {"type": "text", "text": "What color is this image?"},
                ],
            }
        ]
        reply = await gen.chat(
            messages,
            images=[buffer.getvalue()],
            config=GenerationConfig(max_new_tokens=32),
        )
        assert isinstance(reply, str) and reply.strip()
        assert "red" in reply.lower()


@pytest.mark.gpu
class TestRerankerQuality:
    async def test_ranks_relevant_first(self) -> None:
        reranker = Reranker("cross-encoder/ms-marco-MiniLM-L-6-v2", device="cuda")
        chunks = [
            _chunk("The Eiffel Tower is in Paris.", 0),
            _chunk("PIX is the Brazilian instant payment system.", 1),
            _chunk("Bananas are yellow.", 2),
        ]
        ranked = await reranker.rerank("How does PIX work?", chunks, top_k=3)
        assert ranked[0].text.startswith("PIX")


@pytest.mark.gpu
class TestToolCalling7b:
    async def test_emits_tool_call(self) -> None:
        gen = TextGenerator("Qwen/Qwen2.5-3B-Instruct", device="cuda")
        tools = [
            {
                "type": "function",
                "function": {
                    "name": "get_weather",
                    "description": "Get the current weather for a city.",
                    "parameters": {
                        "type": "object",
                        "properties": {"city": {"type": "string"}},
                        "required": ["city"],
                    },
                },
            }
        ]
        result = await gen.chat_with_tools(
            [{"role": "user", "content": "What's the weather in Recife right now?"}],
            tools,
            config=GenerationConfig(max_new_tokens=128, temperature=0, do_sample=False),
        )
        assert result["tool_calls"], "model should emit a tool call"
        assert result["tool_calls"][0]["function"]["name"] == "get_weather"


class Person(BaseModel):
    """Structured-output target."""

    name: str
    age: int


@pytest.mark.gpu
class TestStructuredBestEffort:
    async def test_best_effort_json(self) -> None:
        gen = TextGenerator("Qwen/Qwen2.5-3B-Instruct", device="cuda")
        person = await gen.generate_structured(
            "Return ONLY JSON for a person named Alice aged 30, "
            'schema {"name": str, "age": int}.',
            Person,
            constrained=False,
            config=GenerationConfig(max_new_tokens=64, temperature=0, do_sample=False),
        )
        assert isinstance(person, Person)
        assert isinstance(person.age, int)


@pytest.mark.gpu
class TestOnnxTorchParity:
    async def test_onnx_matches_torch(self) -> None:
        from huggingface_hub import hf_hub_download
        from huggingface_hub.utils import EntryNotFoundError

        try:
            onnx_path = hf_hub_download(_MINILM, "onnx/model.onnx")
        except EntryNotFoundError:
            pytest.skip("no pre-exported onnx/model.onnx in the model repo")

        onnx = OnnxEmbedder(onnx_path, tokenizer=_MINILM, normalize=True)
        torch_emb = Embedder(_MINILM, normalize=True, device="cpu")
        sentences = [
            "PIX is Brazil's instant payment system.",
            "The cat sat on the mat.",
            "Refunds are processed within five business days.",
            "Machine learning models run on GPUs.",
        ]
        onnx_vecs = await onnx.embed(sentences)
        torch_vecs = await torch_emb.embed(sentences)
        sims = [
            cosine_similarity(a, b) for a, b in zip(onnx_vecs, torch_vecs, strict=True)
        ]
        assert min(sims) >= 0.999, f"min cosine {min(sims)}"
