"""``GenerationConfig`` parity between the VLM and the text generator.

:class:`~tempest_fastapi_sdk.genai.VisionTextGenerator` shares
:class:`~tempest_fastapi_sdk.genai.GenerationConfig` with
:class:`~tempest_fastapi_sdk.genai.TextGenerator`, and used to drop the
fields the text path handles outside ``to_generate_kwargs``: ``stop`` in the
config was discarded, and ``stop=`` per call reached ``model.generate``,
which refuses it with ``ValueError`` (issue #332; ``seed`` had the same
shape until #331).

The parity tests enumerate ``GenerationConfig.model_fields``, so a field
added later that the VLM neither applies nor refuses fails here instead of
evaporating. They run the real ``generate`` of a randomly initialised
one-layer Llama (no download, CPU) behind a processor stand-in.
"""

from __future__ import annotations

import inspect
import threading
from typing import Any

import pytest

from tempest_fastapi_sdk.genai import (
    GenerationConfig,
    GenerationStoppedError,
    TextGenerator,
    VisionTextGenerator,
)
from tests.genai.test_text_seed import TokenizerAsProcessor, _cpu

torch: Any = pytest.importorskip("torch")
transformers: Any = pytest.importorskip("transformers")

PROMPT: str = "w1 w2 w3"
VOCAB_SIZE: int = 65
NEW_TOKENS: int = 12

BASELINE: dict[str, Any] = {"max_new_tokens": 4, "do_sample": True}
"""Config every parity case starts from.

``do_sample=True`` so a ``seed`` has a draw to steer; ``_apply_seed`` leaves
greedy decoding alone, which would make the seed case vacuous.
"""

SAMPLE_VALUES: dict[str, Any] = {
    "max_new_tokens": 3,
    "temperature": 0.5,
    "top_p": 0.8,
    "top_k": 5,
    "repetition_penalty": 1.2,
    "do_sample": False,
    "seed": 7,
    "stop": ["w3"],
}
"""A valid value per ``GenerationConfig`` field, different from ``BASELINE``."""

REFUSED_BY_VLM: dict[str, str] = {}
"""Fields the VLM refuses on purpose, mapped to the error message it raises.

Empty today: every field is applied. A field that cannot be applied on the
vision path goes here, with a clear error in ``VisionTextGenerator``.
"""


def _tokenizer() -> Any:
    """Build a whitespace word-level tokenizer that ``StopStringCriteria`` accepts.

    ``StopStringCriteria`` cleans the vocabulary by encoding the literal
    ``"abcdef"`` and looking for it in every decoded token; a vocabulary
    that maps it to ``[UNK]`` makes that raise ``ValueError: substring not
    found``. The last ``w`` slot is therefore ``abcdef``.

    Returns:
        Any: A ``PreTrainedTokenizerFast`` that needs no download.
    """
    from tokenizers import Tokenizer, models, pre_tokenizers

    vocab: dict[str, int] = {f"w{i}": i for i in range(VOCAB_SIZE - 2)}
    vocab["abcdef"] = VOCAB_SIZE - 2
    vocab["[UNK]"] = VOCAB_SIZE - 1
    backend = Tokenizer(models.WordLevel(vocab=vocab, unk_token="[UNK]"))
    backend.pre_tokenizer = pre_tokenizers.Whitespace()
    return transformers.PreTrainedTokenizerFast(
        tokenizer_object=backend,
        unk_token="[UNK]",
        pad_token="[UNK]",
        model_input_names=["input_ids", "attention_mask"],
    )


def _model() -> Any:
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
def text_gen() -> TextGenerator:
    """Return a :class:`TextGenerator` over the in-memory tiny model.

    Returns:
        TextGenerator: A loaded generator; no weights are downloaded.
    """
    generator = TextGenerator("tiny-random-llama", device="cpu", hardware=_cpu())
    generator._model = _model()
    generator._tokenizer = _tokenizer()
    return generator


@pytest.fixture(scope="module")
def vlm(text_gen: TextGenerator) -> VisionTextGenerator:
    """Return a :class:`VisionTextGenerator` over the same model and tokenizer.

    Args:
        text_gen (TextGenerator): Supplies the model and tokenizer.

    Returns:
        VisionTextGenerator: A loaded generator; no weights are downloaded.
    """
    generator = VisionTextGenerator("tiny-random-llama", device="cpu", hardware=_cpu())
    generator._model = text_gen._model
    generator._processor = TokenizerAsProcessor(text_gen._tokenizer)
    return generator


def _greedy(**fields: Any) -> GenerationConfig:
    """Return a greedy config of ``NEW_TOKENS`` tokens plus ``fields``.

    Args:
        **fields (Any): Extra ``GenerationConfig`` fields.

    Returns:
        GenerationConfig: The config.
    """
    return GenerationConfig(max_new_tokens=NEW_TOKENS, do_sample=False, **fields)


async def _stop_word(text_gen: TextGenerator) -> str:
    """Return the third word of the unstopped greedy completion.

    Args:
        text_gen (TextGenerator): The generator whose output is read.

    Returns:
        str: A word the completion is guaranteed to reach.
    """
    full = await text_gen.generate(PROMPT, config=_greedy())
    return full.split()[2]


class GenerateSpy:
    """Wraps ``model.generate`` and records the keyword args of each call."""

    def __init__(self, generate: Any) -> None:
        """Wrap ``generate``.

        Args:
            generate (Any): The model's bound ``generate``.
        """
        self._generate: Any = generate
        self.calls: list[dict[str, Any]] = []

    def __call__(self, **kwargs: Any) -> Any:
        """Record ``kwargs`` minus the tokenized inputs, then generate.

        Args:
            **kwargs (Any): The ``model.generate`` keyword args.

        Returns:
            Any: The real ``generate`` output.
        """
        inputs: set[str] = {"input_ids", "attention_mask"}
        self.calls.append({k: v for k, v in kwargs.items() if k not in inputs})
        return self._generate(**kwargs)


@pytest.fixture
def spy(vlm: VisionTextGenerator, monkeypatch: pytest.MonkeyPatch) -> GenerateSpy:
    """Record what the VLM hands to ``model.generate``.

    Args:
        vlm (VisionTextGenerator): The generator under test.
        monkeypatch (pytest.MonkeyPatch): Restores ``generate`` afterwards.

    Returns:
        GenerateSpy: The installed spy.
    """
    recorder = GenerateSpy(vlm._model.generate)
    monkeypatch.setattr(vlm._model, "generate", recorder)
    return recorder


class TestVisionTextStop:
    async def test_config_stop_cuts_the_output(
        self, text_gen: TextGenerator, vlm: VisionTextGenerator
    ) -> None:
        word = await _stop_word(text_gen)
        full = await vlm.generate(PROMPT, config=_greedy())
        stopped = await vlm.generate(PROMPT, config=_greedy(stop=[word]))
        assert stopped.split() == full.split()[:3]

    async def test_per_call_stop_cuts_the_output(
        self, text_gen: TextGenerator, vlm: VisionTextGenerator
    ) -> None:
        word = await _stop_word(text_gen)
        full = await vlm.generate(PROMPT, config=_greedy())
        stopped = await vlm.generate(PROMPT, config=_greedy(), stop=[word])
        assert stopped.split() == full.split()[:3]

    async def test_matches_the_text_generator(
        self, text_gen: TextGenerator, vlm: VisionTextGenerator
    ) -> None:
        word = await _stop_word(text_gen)
        cfg = _greedy(stop=[word])
        assert await vlm.generate(PROMPT, config=cfg) == await text_gen.generate(
            PROMPT, config=cfg
        )

    async def test_per_call_stop_wins_over_config(
        self, text_gen: TextGenerator, vlm: VisionTextGenerator
    ) -> None:
        full = (await vlm.generate(PROMPT, config=_greedy())).split()
        stopped = await vlm.generate(
            PROMPT, config=_greedy(stop=[full[4]]), stop=[full[1]]
        )
        assert stopped.split() == full[:2]


class TestVisionTextStopEvent:
    async def test_set_event_raises_stopped(self, vlm: VisionTextGenerator) -> None:
        event = threading.Event()
        event.set()
        with pytest.raises(GenerationStoppedError, match="was stopped"):
            await vlm.generate(PROMPT, config=_greedy(), stop_event=event)

    async def test_unset_event_generates(self, vlm: VisionTextGenerator) -> None:
        text = await vlm.generate(
            PROMPT, config=_greedy(), stop_event=threading.Event()
        )
        assert len(text.split()) == NEW_TOKENS


class TestGenerationConfigParity:
    def test_every_field_has_a_sample_value(self) -> None:
        assert set(SAMPLE_VALUES) == set(GenerationConfig.model_fields), (
            "GenerationConfig gained or lost a field: add a sample value here "
            "and make VisionTextGenerator apply it (or refuse it in "
            "REFUSED_BY_VLM with a clear error)"
        )

    @pytest.mark.parametrize("field", sorted(GenerationConfig.model_fields))
    async def test_config_field_is_applied_or_refused(
        self, field: str, vlm: VisionTextGenerator, spy: GenerateSpy
    ) -> None:
        await vlm.generate(PROMPT, config=GenerationConfig(**BASELINE))
        config = GenerationConfig(**{**BASELINE, field: SAMPLE_VALUES[field]})
        if field in REFUSED_BY_VLM:
            with pytest.raises(ValueError, match=REFUSED_BY_VLM[field]):
                await vlm.generate(PROMPT, config=config)
            return
        await vlm.generate(PROMPT, config=config)
        baseline, applied = spy.calls
        assert applied != baseline, f"{field!r} in the config never reached generate"

    @pytest.mark.parametrize("field", sorted(GenerationConfig.model_fields))
    async def test_per_call_field_is_applied_or_refused(
        self, field: str, vlm: VisionTextGenerator, spy: GenerateSpy
    ) -> None:
        config = GenerationConfig(**BASELINE)
        await vlm.generate(PROMPT, config=config)
        override: dict[str, Any] = {field: SAMPLE_VALUES[field]}
        if field in REFUSED_BY_VLM:
            with pytest.raises(ValueError, match=REFUSED_BY_VLM[field]):
                await vlm.generate(PROMPT, config=config, **override)
            return
        await vlm.generate(PROMPT, config=config, **override)
        baseline, applied = spy.calls
        assert applied != baseline, f"{field!r} per call never reached generate"


class TestSignatureParity:
    @pytest.mark.parametrize("method", ["generate", "chat"])
    def test_vlm_accepts_every_text_generator_keyword(self, method: str) -> None:
        text_params = inspect.signature(getattr(TextGenerator, method)).parameters
        vision_params = inspect.signature(
            getattr(VisionTextGenerator, method)
        ).parameters
        keyword_only = {
            name
            for name, param in text_params.items()
            if param.kind is inspect.Parameter.KEYWORD_ONLY
        }
        missing = keyword_only - set(vision_params)
        assert not missing, f"VisionTextGenerator.{method} lacks {sorted(missing)}"
