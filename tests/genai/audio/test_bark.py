"""Tests for Bark text-to-speech.

``transformers`` is never loaded: a fake processor and model are injected
over :func:`tempest_fastapi_sdk.genai.audio.bark._require_transformers`. The
per-call ceiling the splitter works around (13.95 s; 180 Portuguese
characters fit, 240 were cut) was measured on ``suno/bark-small`` on
2026-10-31.
"""

from __future__ import annotations

import contextlib
import io
import random
import wave
from collections.abc import Iterator
from types import SimpleNamespace
from typing import Any

import numpy as np
import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from tempest_fastapi_sdk import register_exception_handlers
from tempest_fastapi_sdk.exceptions import ValidationException
from tempest_fastapi_sdk.genai import make_genai_router
from tempest_fastapi_sdk.genai.audio import (
    BarkSpeech,
    Language,
    SpeechSynthesizer,
    split_for_bark,
)
from tempest_fastapi_sdk.genai.audio import bark as bark_module
from tempest_fastapi_sdk.genai.audio.bark import bark_language_code

SAMPLE_RATE: int = 24000
PRESETS: dict[str, Any] = {
    "repo_or_path": "suno/bark-small",
    "v2/pt_speaker_0": {},
    "v2/pt_speaker_1": {},
    "v2/en_speaker_6": {},
}


class FakeArray:
    """Stands in for the tensor ``generate`` returns."""

    def __init__(self, array: np.ndarray) -> None:
        self.array = array

    def float(self) -> FakeArray:
        return self

    def cpu(self) -> FakeArray:
        return self

    def numpy(self) -> np.ndarray:
        return self.array


class FakeInputs(dict[str, Any]):
    def to(self, device: str) -> FakeInputs:
        return self


class FakeProcessor:
    """Records each piece and the preset it was spoken with."""

    def __init__(self) -> None:
        self.speaker_embeddings = PRESETS
        self.calls: list[tuple[str, str | None]] = []

    def __call__(self, text: str, voice_preset: str | None = None) -> FakeInputs:
        self.calls.append((text, voice_preset))
        return FakeInputs(text=text)


class FakeModel:
    """Speaks 0.1 s of audio per character, like a very fast reader."""

    def __init__(self) -> None:
        self.generation_config = SimpleNamespace(sample_rate=SAMPLE_RATE)

    def to(self, device: str) -> FakeModel:
        return self

    def generate(self, **kwargs: Any) -> FakeArray:
        samples = int(len(kwargs["text"]) * 0.1 * SAMPLE_RATE)
        return FakeArray(np.full((1, samples), 0.2, dtype=np.float32))


class FakeTorch:
    float32 = "float32"
    float16 = "float16"
    bfloat16 = "bfloat16"

    def __init__(self) -> None:
        self.seeds: list[int] = []

    def manual_seed(self, seed: int) -> None:
        self.seeds.append(seed)

    @staticmethod
    @contextlib.contextmanager
    def no_grad() -> Iterator[None]:
        yield


def _install(monkeypatch: pytest.MonkeyPatch) -> SimpleNamespace:
    """Inject fake torch + transformers into the module."""
    model = FakeModel()
    processor = FakeProcessor()
    torch = FakeTorch()
    transformers = SimpleNamespace(
        AutoProcessor=SimpleNamespace(from_pretrained=lambda *a, **k: processor),
        BarkModel=SimpleNamespace(from_pretrained=lambda *a, **k: model),
    )
    monkeypatch.setattr(
        bark_module, "_require_transformers", lambda: (torch, transformers)
    )
    return SimpleNamespace(model=model, processor=processor, torch=torch)


def _seconds(data: bytes) -> float:
    """Read a WAV's duration.

    Args:
        data (bytes): The WAV bytes.

    Returns:
        float: Duration in seconds.
    """
    with wave.open(io.BytesIO(data)) as reader:
        return float(reader.getnframes() / reader.getframerate())


class TestSplitForBark:
    def test_short_text_is_one_piece(self) -> None:
        assert split_for_bark("Bom dia. Tudo bem?") == ["Bom dia. Tudo bem?"]

    def test_sentences_are_grouped_up_to_the_limit(self) -> None:
        text = "Primeira frase aqui. Segunda frase aqui. Terceira frase aqui."
        assert split_for_bark(text, max_chars=45) == [
            "Primeira frase aqui. Segunda frase aqui.",
            "Terceira frase aqui.",
        ]

    def test_long_sentence_is_split_at_commas_then_spaces(self) -> None:
        sentence = "um, " + "palavra " * 30 + "fim."
        pieces = split_for_bark(sentence, max_chars=50)
        assert all(len(piece) <= 50 for piece in pieces)
        assert " ".join(pieces).split() == sentence.split()

    def test_no_word_is_lost_or_reordered(self) -> None:
        text = "Ônibus atrasado! Rota alternativa: pela avenida; depois, o centro. Fim?"
        pieces = split_for_bark(text, max_chars=20)
        assert " ".join(pieces).split() == text.split()

    def test_abbreviations_stay_with_the_next_word(self) -> None:
        text = "O Dr. Silva pagou R$ 3,50. Depois a Sra. Lima saiu."
        assert split_for_bark(text, max_chars=40) == [
            "O Dr. Silva pagou R$ 3,50.",
            "Depois a Sra. Lima saiu.",
        ]

    def test_numbers_are_never_split(self) -> None:
        assert split_for_bark("Custa 1.000,00 reais.", max_chars=8) == [
            "Custa",
            "1.000,00",
            "reais.",
        ]

    def test_a_word_longer_than_the_limit_stays_whole(self) -> None:
        assert split_for_bark("a supercalifragilistico b", max_chars=5) == [
            "a",
            "supercalifragilistico",
            "b",
        ]

    def test_punctuation_only_pieces_are_dropped(self) -> None:
        assert split_for_bark("... ?! ;") == []
        assert split_for_bark("Oi. ... Tchau.", max_chars=4) == ["Oi.", "Tchau."]

    @pytest.mark.parametrize("seed", range(20))
    def test_random_text_keeps_every_word_in_order(self, seed: int) -> None:
        rng = random.Random(seed)
        vocabulary = [
            "ônibus",
            "Dr.",
            "R$",
            "3,50",
            "rota,",
            "fim.",
            "sim!",
            "não?",
            "a;",
            "b:",
        ]
        words = [rng.choice(vocabulary) for _ in range(rng.randint(1, 120))]
        text = " ".join(words)
        limit = rng.randint(5, 80)
        pieces = split_for_bark(text, max_chars=limit)
        assert " ".join(pieces).split() == words
        assert all(len(p) <= limit or " " not in p for p in pieces)

    def test_whitespace_is_normalized_and_blank_is_empty(self) -> None:
        assert split_for_bark("  a.\n\n  b.  ") == ["a. b."]
        assert split_for_bark("   ") == []

    def test_rejects_non_positive_limit(self) -> None:
        with pytest.raises(ValueError, match="max_chars"):
            split_for_bark("a", max_chars=0)


class TestLanguageCode:
    @pytest.mark.parametrize(
        ("language", "code"),
        [(Language.PT_BR, "pt"), ("pt-BR", "pt"), ("en_US", "en"), (" PT ", "pt")],
    )
    def test_maps_to_the_preset_prefix(
        self, language: Language | str, code: str
    ) -> None:
        assert bark_language_code(language) == code


class TestSynthesize:
    async def test_long_text_is_spoken_in_pieces_with_pauses(
        self,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        fakes = _install(monkeypatch)
        speech = BarkSpeech(device="cpu", max_chunk_chars=30, pause_seconds=0.5)
        text = "Primeira frase longa aqui. Segunda frase longa aqui."
        data = await speech.synthesize(text, language="pt")
        spoken = [piece for piece, _ in fakes.processor.calls]
        assert spoken == ["Primeira frase longa aqui.", "Segunda frase longa aqui."]
        expected = sum(len(piece) * 0.1 for piece in spoken) + 0.5
        assert _seconds(data) == pytest.approx(expected, abs=1e-3)

    async def test_language_picks_speaker_zero(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        fakes = _install(monkeypatch)
        await BarkSpeech(device="cpu").synthesize("oi", language=Language.PT_BR)
        assert fakes.processor.calls[0][1] == "v2/pt_speaker_0"

    async def test_explicit_speaker_wins(self, monkeypatch: pytest.MonkeyPatch) -> None:
        fakes = _install(monkeypatch)
        await BarkSpeech(device="cpu").synthesize("oi", speaker="v2/pt_speaker_1")
        assert fakes.processor.calls[0][1] == "v2/pt_speaker_1"

    async def test_no_voice_is_bark_unconditioned(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        fakes = _install(monkeypatch)
        await BarkSpeech(device="cpu").synthesize("oi")
        assert fakes.processor.calls[0][1] is None

    async def test_seed_is_applied_once(self, monkeypatch: pytest.MonkeyPatch) -> None:
        fakes = _install(monkeypatch)
        await BarkSpeech(device="cpu", max_chunk_chars=5).synthesize("a. b. c.", seed=9)
        assert fakes.torch.seeds == [9]

    @pytest.mark.parametrize(
        ("kwargs", "field"),
        [
            ({"speaker": "v2/xx_speaker_9"}, "speaker"),
            ({"language": "xx"}, "language"),
            ({"language": "en", "speaker": "v2/pt_speaker_1"}, "speaker"),
            ({"language": "pt", "speaker": "v2/en_speaker_6_pt_speaker_1"}, "speaker"),
            ({"seed": -1}, "seed"),
            ({"seed": 2**64}, "seed"),
        ],
    )
    async def test_invalid_voice_is_a_422(
        self,
        monkeypatch: pytest.MonkeyPatch,
        kwargs: dict[str, Any],
        field: str,
    ) -> None:
        _install(monkeypatch)
        with pytest.raises(ValidationException) as caught:
            await BarkSpeech(device="cpu").synthesize("oi", **kwargs)
        assert caught.value.details == {"field": field}

    @pytest.mark.parametrize(
        "kwargs",
        [
            {"language": "en", "speaker": "v2/pt_speaker_1"},
            {"seed": 2**64},
            {"language": "pt", "speaker": "announcer"},
        ],
    )
    async def test_pure_checks_run_before_loading(
        self,
        monkeypatch: pytest.MonkeyPatch,
        kwargs: dict[str, Any],
    ) -> None:
        _install(monkeypatch)
        speech = BarkSpeech(device="cpu")
        with pytest.raises(ValidationException):
            await speech.synthesize("oi", **kwargs)
        assert speech.is_loaded is False

    async def test_legacy_speaker_matches_its_language(
        self,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        fakes = _install(monkeypatch)
        fakes.processor.speaker_embeddings = {**PRESETS, "pt_speaker_1": {}}
        await BarkSpeech(device="cpu").synthesize(
            "oi", language="pt-BR", speaker="pt_speaker_1"
        )
        assert fakes.processor.calls[0][1] == "pt_speaker_1"

    async def test_generate_failure_drops_the_device_tensors(
        self,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        fakes = _install(monkeypatch)

        def out_of_memory(**kwargs: Any) -> FakeArray:
            raise RuntimeError("CUDA out of memory")

        fakes.model.generate = out_of_memory
        with pytest.raises(RuntimeError) as caught:
            await BarkSpeech(device="cpu").synthesize("oi")
        frames = []
        traceback = caught.value.__traceback__
        while traceback is not None:
            frames.append(traceback.tb_frame)
            traceback = traceback.tb_next
        speak = [f for f in frames if f.f_code.co_name == "_speak"]
        assert speak
        assert speak[0].f_locals["inputs"] is None

    async def test_blank_text_is_a_422_without_loading(
        self,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        _install(monkeypatch)
        speech = BarkSpeech(device="cpu")
        with pytest.raises(ValidationException) as caught:
            await speech.synthesize("  \n ")
        assert caught.value.details == {"field": "text"}
        assert speech.is_loaded is False

    async def test_out_path_gets_the_same_bytes(
        self,
        monkeypatch: pytest.MonkeyPatch,
        tmp_path: Any,
    ) -> None:
        _install(monkeypatch)
        target = tmp_path / "voz.wav"
        data = await BarkSpeech(device="cpu").synthesize("oi", out_path=target)
        assert target.read_bytes() == data


class TestLifecycle:
    def test_voices_lists_presets_without_the_repo_key(
        self,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        _install(monkeypatch)
        assert BarkSpeech(device="cpu").voices() == [
            "v2/en_speaker_6",
            "v2/pt_speaker_0",
            "v2/pt_speaker_1",
        ]

    def test_failure_moving_to_the_device_releases(
        self,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        fakes = _install(monkeypatch)

        def out_of_memory(device: str) -> FakeModel:
            raise RuntimeError("CUDA out of memory")

        fakes.model.to = out_of_memory
        speech = BarkSpeech(device="cpu")
        released: list[bool] = []
        monkeypatch.setattr(speech, "_release", lambda: released.append(True))
        with pytest.raises(RuntimeError, match="out of memory"):
            speech.load()
        assert released == [True]
        assert speech.is_loaded is False

    @pytest.mark.parametrize(
        "kwargs",
        [{"max_concurrent": 0}, {"max_chunk_chars": 0}, {"pause_seconds": -1.0}],
    )
    def test_rejects_bad_settings(self, kwargs: dict[str, Any]) -> None:
        with pytest.raises(ValueError):
            BarkSpeech(device="cpu", **kwargs)

    def test_satisfies_the_tts_protocol(self) -> None:
        speech: SpeechSynthesizer = BarkSpeech(device="cpu")
        assert isinstance(speech, SpeechSynthesizer)


class TestRouter:
    def _client(self, monkeypatch: pytest.MonkeyPatch) -> TestClient:
        _install(monkeypatch)
        app = FastAPI()
        register_exception_handlers(app)
        app.include_router(make_genai_router(text_to_speech=BarkSpeech(device="cpu")))
        return TestClient(app)

    def test_tts_with_language(self, monkeypatch: pytest.MonkeyPatch) -> None:
        response = self._client(monkeypatch).post(
            "/api/genai/tts",
            json={"text": "Bom dia.", "language": "pt-BR"},
        )
        assert response.status_code == 200
        assert response.headers["content-type"] == "audio/wav"

    def test_unknown_speaker_is_a_422(self, monkeypatch: pytest.MonkeyPatch) -> None:
        response = self._client(monkeypatch).post(
            "/api/genai/tts",
            json={"text": "oi", "speaker": "nope"},
        )
        assert response.status_code == 422
        assert response.json()["details"] == {"field": "speaker"}


class TestMissingExtra:
    def test_import_error_names_the_extra(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        import builtins

        real_import = builtins.__import__

        def fail(name: str, *args: Any, **kwargs: Any) -> Any:
            if name == "transformers":
                raise ImportError("no module")
            return real_import(name, *args, **kwargs)

        monkeypatch.setattr(builtins, "__import__", fail)
        with pytest.raises(ImportError, match=r"\[genai\]"):
            bark_module._require_transformers()
