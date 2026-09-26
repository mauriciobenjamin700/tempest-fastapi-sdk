"""Seed isolation of :class:`~tempest_fastapi_sdk.genai.TextGenerator`.

``model.generate`` samples with the process-wide RNG, and the generator
used to seed it with ``transformers.set_seed``: a concurrent sampling call
drew from the same stream, so two seeded calls running together gave
different text than either one alone (issue #321). These tests pin the fix
— a seeded call samples from a private ``torch.Generator`` — against a
randomly initialised Llama built from a config (no download, CPU, well
under a second), plus one ``@pytest.mark.model`` run on a real instruct
model.

Concurrency is forced, not hoped for: a user logits processor holds both
calls at a two-party barrier on every decoding step, so their draws
interleave step by step.
"""

from __future__ import annotations

import asyncio
import threading
from typing import Any

import pytest

from tempest_fastapi_sdk.genai import (
    GenerationConfig,
    TextGenerator,
    VisionTextGenerator,
)
from tempest_fastapi_sdk.genai.schemas import HardwareInfo
from tempest_fastapi_sdk.genai.text import _sampling_warpers, _SeededSampler

torch: Any = pytest.importorskip("torch")
transformers: Any = pytest.importorskip("transformers")

PROMPT: str = "w1 w2 w3"
VOCAB_SIZE: int = 65
NEW_TOKENS: int = 12


def _cpu() -> HardwareInfo:
    """Return a CPU-only snapshot so no hardware probe runs.

    Returns:
        HardwareInfo: The snapshot.
    """
    return HardwareInfo(
        cpu_cores=2,
        ram_total_bytes=10**9,
        ram_available_bytes=10**9,
    )


def _tiny_tokenizer() -> Any:
    """Build a whitespace word-level tokenizer over ``w0`` … ``w63``.

    Returns:
        Any: A ``PreTrainedTokenizerFast`` that needs no download.
    """
    from tokenizers import Tokenizer, models, pre_tokenizers

    vocab: dict[str, int] = {f"w{i}": i for i in range(VOCAB_SIZE - 1)}
    vocab["[UNK]"] = VOCAB_SIZE - 1
    backend = Tokenizer(models.WordLevel(vocab=vocab, unk_token="[UNK]"))
    backend.pre_tokenizer = pre_tokenizers.Whitespace()
    return transformers.PreTrainedTokenizerFast(
        tokenizer_object=backend,
        unk_token="[UNK]",
        pad_token="[UNK]",
        model_input_names=["input_ids", "attention_mask"],
    )


def _tiny_model() -> Any:
    """Build a one-layer random Llama with no EOS, so every call runs full length.

    Returns:
        Any: The model, in eval mode, on CPU.
    """
    torch.manual_seed(0)
    config = transformers.LlamaConfig(
        vocab_size=VOCAB_SIZE,
        hidden_size=16,
        intermediate_size=32,
        num_hidden_layers=1,
        num_attention_heads=2,
        num_key_value_heads=2,
        max_position_embeddings=64,
        pad_token_id=VOCAB_SIZE - 1,
    )
    model = transformers.LlamaForCausalLM(config).eval()
    model.generation_config.eos_token_id = None
    return model


@pytest.fixture(scope="module")
def tiny() -> TextGenerator:
    """Return a :class:`TextGenerator` wired to the in-memory tiny model.

    Returns:
        TextGenerator: A loaded generator; no weights are downloaded.
    """
    generator = TextGenerator("tiny-random-llama", device="cpu", hardware=_cpu())
    generator._model = _tiny_model()
    generator._tokenizer = _tiny_tokenizer()
    return generator


class TokenizerAsProcessor:
    """Minimal ``AutoProcessor`` stand-in over the tiny tokenizer.

    ``VisionTextGenerator`` calls ``processor(text=, images=, return_tensors=)``
    and ``processor.decode``; a text-only call needs nothing else.
    """

    def __init__(self, tokenizer: Any) -> None:
        """Wrap ``tokenizer``.

        Args:
            tokenizer (Any): The tiny tokenizer.
        """
        self.tokenizer: Any = tokenizer

    def __call__(self, text: str, images: Any, return_tensors: str) -> Any:
        """Tokenize ``text``; ``images`` must be ``None``.

        Args:
            text (str): The prompt.
            images (Any): Ignored (text-only calls).
            return_tensors (str): Tensor format.

        Returns:
            Any: The tokenizer's ``BatchEncoding``.
        """
        return self.tokenizer(text, return_tensors=return_tensors)

    def decode(self, ids: Any, skip_special_tokens: bool) -> str:
        """Decode ``ids`` with the tokenizer.

        Args:
            ids (Any): Token ids.
            skip_special_tokens (bool): Forwarded.

        Returns:
            str: The text.
        """
        return str(self.tokenizer.decode(ids, skip_special_tokens=skip_special_tokens))


@pytest.fixture(scope="module")
def tiny_vlm(tiny: TextGenerator) -> VisionTextGenerator:
    """Return a :class:`VisionTextGenerator` over the same tiny model.

    Args:
        tiny (TextGenerator): Supplies the model and tokenizer.

    Returns:
        VisionTextGenerator: A loaded generator; no weights are downloaded.
    """
    generator = VisionTextGenerator("tiny-random-llama", device="cpu", hardware=_cpu())
    generator._model = tiny._model
    generator._processor = TokenizerAsProcessor(tiny._tokenizer)
    return generator


class GlobalRngNoise:
    """Logits processor that draws from the global torch RNG every step.

    Stands in for any other sampling generation running in the process.
    """

    def __call__(self, input_ids: Any, scores: Any) -> Any:
        """Consume one global draw and pass the scores through.

        Args:
            input_ids (Any): The token ids so far.
            scores (Any): The next-token logits.

        Returns:
            Any: ``scores`` unchanged.
        """
        torch.rand(1)
        return scores


class LockstepBarrier:
    """Logits processor that holds two calls together on every step."""

    def __init__(self) -> None:
        """Create the two-party barrier."""
        self.barrier: threading.Barrier = threading.Barrier(2, timeout=30)

    def __call__(self, input_ids: Any, scores: Any) -> Any:
        """Wait for the other call, then pass the scores through.

        Args:
            input_ids (Any): The token ids so far.
            scores (Any): The next-token logits.

        Returns:
            Any: ``scores`` unchanged.
        """
        self.barrier.wait()
        return scores


def _seeded(seed: int = 7) -> GenerationConfig:
    """Return a sampling config with ``seed`` and a fixed length.

    Args:
        seed (int): The seed.

    Returns:
        GenerationConfig: The config.
    """
    return GenerationConfig(
        seed=seed,
        max_new_tokens=NEW_TOKENS,
        do_sample=True,
        temperature=0.9,
        top_p=0.95,
    )


class TestSeedIsolation:
    async def test_concurrent_same_seed_calls_match_the_serial_run(
        self, tiny: TextGenerator
    ) -> None:
        serial = await tiny.generate(PROMPT, config=_seeded())
        lockstep = transformers.LogitsProcessorList([LockstepBarrier()])
        first, second = await asyncio.gather(
            tiny.generate(PROMPT, config=_seeded(), logits_processor=lockstep),
            tiny.generate(PROMPT, config=_seeded(), logits_processor=lockstep),
        )
        assert first == second == serial

    async def test_global_rng_draws_between_steps_do_not_change_the_output(
        self, tiny: TextGenerator
    ) -> None:
        clean = await tiny.generate(PROMPT, config=_seeded())
        noisy = await tiny.generate(
            PROMPT,
            config=_seeded(),
            logits_processor=transformers.LogitsProcessorList([GlobalRngNoise()]),
        )
        assert noisy == clean

    async def test_seeded_call_leaves_the_global_seed_alone(
        self, tiny: TextGenerator
    ) -> None:
        torch.manual_seed(999)
        await tiny.generate(PROMPT, config=_seeded(seed=7))
        assert torch.initial_seed() == 999

    async def test_different_seeds_differ(self, tiny: TextGenerator) -> None:
        a = await tiny.generate(PROMPT, config=_seeded(seed=1))
        b = await tiny.generate(PROMPT, config=_seeded(seed=2))
        assert a != b

    async def test_stream_matches_generate_under_noise(
        self, tiny: TextGenerator
    ) -> None:
        expected = await tiny.generate(PROMPT, config=_seeded())
        pieces: list[str] = [
            piece
            async for piece in tiny.stream(
                PROMPT,
                config=_seeded(),
                logits_processor=transformers.LogitsProcessorList(
                    [GlobalRngNoise()],
                ),
            )
        ]
        assert "".join(pieces).strip() == expected.strip()

    async def test_user_processors_are_kept_and_run(self, tiny: TextGenerator) -> None:
        calls: list[int] = []

        def count(input_ids: Any, scores: Any) -> Any:
            """Record one decoding step.

            Args:
                input_ids (Any): The token ids so far.
                scores (Any): The next-token logits.

            Returns:
                Any: ``scores`` unchanged.
            """
            calls.append(1)
            return scores

        await tiny.generate(
            PROMPT,
            config=_seeded(),
            logits_processor=transformers.LogitsProcessorList([count]),
        )
        assert len(calls) == NEW_TOKENS


class TestModesOutsidePlainSampling:
    async def test_greedy_ignores_the_seed_and_the_global_rng(
        self, tiny: TextGenerator
    ) -> None:
        torch.manual_seed(999)
        cfg = GenerationConfig(seed=7, max_new_tokens=NEW_TOKENS, do_sample=False)
        a = await tiny.generate(PROMPT, config=cfg)
        b = await tiny.generate(PROMPT, config=cfg.model_copy(update={"seed": 8}))
        assert a == b
        assert torch.initial_seed() == 999

    async def test_beam_sampling_falls_back_to_the_global_seed(
        self, tiny: TextGenerator
    ) -> None:
        torch.manual_seed(999)
        first = await tiny.generate(PROMPT, config=_seeded(seed=7), num_beams=2)
        assert torch.initial_seed() == 7
        second = await tiny.generate(PROMPT, config=_seeded(seed=7), num_beams=2)
        assert first == second


class TestVisionTextSeed:
    async def test_config_seed_is_honoured(
        self, tiny: TextGenerator, tiny_vlm: VisionTextGenerator
    ) -> None:
        text = await tiny.generate(PROMPT, config=_seeded(), top_k=0)
        vision = await tiny_vlm.generate(PROMPT, config=_seeded(), top_k=0)
        assert vision == text

    async def test_per_call_seed_is_honoured(
        self, tiny_vlm: VisionTextGenerator
    ) -> None:
        cfg = _seeded().model_copy(update={"seed": None})
        a = await tiny_vlm.generate(PROMPT, config=cfg, seed=11)
        b = await tiny_vlm.generate(
            PROMPT,
            config=cfg,
            seed=11,
            logits_processor=transformers.LogitsProcessorList([GlobalRngNoise()]),
        )
        assert a == b


class TestSeededSampler:
    def test_returns_one_hot_scores_at_the_drawn_token(self) -> None:
        scores = torch.randn(3, 11)
        sampler = _SeededSampler(torch, 5, [])
        out = sampler(torch.zeros(3, 1, dtype=torch.long), scores)
        finite = torch.isfinite(out)
        assert finite.sum(dim=-1).tolist() == [1, 1, 1]
        assert out[finite].tolist() == [0.0, 0.0, 0.0]

    def test_same_seed_same_draws(self) -> None:
        scores = torch.randn(1, 50)
        ids = torch.zeros(1, 1, dtype=torch.long)
        a = _SeededSampler(torch, 3, [])
        b = _SeededSampler(torch, 3, [])
        draws_a = [int(a(ids, scores).argmax()) for _ in range(10)]
        draws_b = [int(b(ids, scores).argmax()) for _ in range(10)]
        assert draws_a == draws_b
        assert len(set(draws_a)) > 1


def _describe(processor: Any) -> tuple[str, dict[str, Any]]:
    """Reduce a warper to its class name and plain-value attributes.

    Args:
        processor (Any): A transformers logits warper.

    Returns:
        tuple[str, dict[str, Any]]: ``(class name, attributes)`` with
        tensors converted to lists, so two instances compare by value.
    """
    attrs: dict[str, Any] = {
        key: value.tolist() if isinstance(value, torch.Tensor) else value
        for key, value in vars(processor).items()
    }
    return type(processor).__name__, attrs


class TestWarperDrift:
    """``_sampling_warpers`` is a port; this pins it to the installed upstream."""

    @pytest.mark.parametrize(
        "knobs",
        [
            {},
            {"temperature": 0.7, "top_k": 20, "top_p": 0.8},
            {
                "temperature": 1.3,
                "top_k": 5,
                "top_p": 0.9,
                "min_p": 0.05,
                "typical_p": 0.9,
                "epsilon_cutoff": 0.01,
                "eta_cutoff": 0.01,
            },
            {"temperature": 1.0, "top_k": 0, "top_p": 1.0},
            {"temperature": 0.8, "top_h": 0.4},
        ],
    )
    def test_matches_what_generate_builds(
        self, tiny: TextGenerator, knobs: dict[str, Any]
    ) -> None:
        if "top_h" in knobs and not hasattr(transformers, "TopHLogitsWarper"):
            pytest.skip("TopHLogitsWarper exists from transformers 5.x on")
        model: Any = tiny._model

        def upstream(do_sample: bool) -> list[Any]:
            """Return the processors ``generate`` builds for ``knobs``.

            Args:
                do_sample (bool): Whether sampling warpers are added.

            Returns:
                list[Any]: The processor list.
            """
            gc = transformers.GenerationConfig(do_sample=do_sample, **knobs)
            return list(
                model._get_logits_processor(
                    generation_config=gc,
                    input_ids_seq_length=3,
                    encoder_input_ids=torch.zeros(1, 3, dtype=torch.long),
                    logits_processor=transformers.LogitsProcessorList(),
                    device="cpu",
                ),
            )

        base = len(upstream(do_sample=False))
        expected = [_describe(p) for p in upstream(do_sample=True)[base:]]
        effective = transformers.GenerationConfig(do_sample=True, **knobs)
        ported = [
            _describe(p) for p in _sampling_warpers(transformers, effective, "cpu")
        ]
        assert ported == expected

    @pytest.mark.parametrize(
        ("model_defaults", "call_knobs"),
        [
            ({}, {}),
            ({"top_k": None, "top_p": None, "temperature": None}, {}),
            ({"top_k": 20, "top_p": 0.8}, {"min_p": 0.05}),
            ({}, {"typical_p": 0.9, "eta_cutoff": 0.01}),
        ],
    )
    async def test_sampler_warpers_equal_the_ones_generate_applies(
        self,
        tiny: TextGenerator,
        monkeypatch: pytest.MonkeyPatch,
        model_defaults: dict[str, Any],
        call_knobs: dict[str, Any],
    ) -> None:
        """End to end: the config resolution *and* the port.

        Records the processor list ``generate`` really builds during a
        seeded call; everything after the sampler is upstream's warper
        chain, which must equal the chain the sampler applied itself.
        Unset model defaults are where 4.57 and 5.x resolve differently
        (5.x fills them from global defaults).
        """
        model: Any = tiny._model
        for key, value in model_defaults.items():
            monkeypatch.setattr(model.generation_config, key, value)
        built: list[Any] = []
        original = model._get_logits_processor

        def record(*args: Any, **kwargs: Any) -> Any:
            """Call the real builder and keep what it returned.

            Args:
                *args (Any): Forwarded.
                **kwargs (Any): Forwarded.

            Returns:
                Any: The real processor list.
            """
            processors = original(*args, **kwargs)
            built.extend(processors)
            return processors

        monkeypatch.setattr(model, "_get_logits_processor", record)
        cfg = GenerationConfig(seed=3, max_new_tokens=2, do_sample=True)
        await tiny.generate(PROMPT, config=cfg, **call_knobs)
        samplers = [p for p in built if isinstance(p, _SeededSampler)]
        assert len(samplers) == 1
        tail = built[built.index(samplers[0]) + 1 :]
        assert [_describe(p) for p in samplers[0]._warpers] == [
            _describe(p) for p in tail
        ]


@pytest.mark.model
class TestSeedIsolationWithModel:
    async def test_concurrent_same_seed_calls_match_the_serial_run(
        self, tiny_instruct_lm: TextGenerator
    ) -> None:
        cfg = GenerationConfig(seed=1234, max_new_tokens=24, temperature=1.0)
        prompt = "Write a short poem about the sea."
        serial = await tiny_instruct_lm.generate(
            prompt,
            config=cfg,
            min_new_tokens=24,
        )
        lockstep = transformers.LogitsProcessorList([LockstepBarrier()])
        first, second = await asyncio.gather(
            tiny_instruct_lm.generate(
                prompt,
                config=cfg,
                logits_processor=lockstep,
                min_new_tokens=24,
            ),
            tiny_instruct_lm.generate(
                prompt,
                config=cfg,
                logits_processor=lockstep,
                min_new_tokens=24,
            ),
        )
        assert first == second == serial
