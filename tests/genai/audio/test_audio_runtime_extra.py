"""Guards for what the ``[genai-audio]`` extra promises and what it says.

Two failures shipped together in v0.245.0 and are both covered here: the
extra installed ``coqui-tts`` without the runtime that ``import TTS.api``
requires, and the import guard replaced the upstream diagnosis with an
instruction to install the extra that was already installed (issue #191).
"""

from __future__ import annotations

import builtins
import tomllib
from pathlib import Path
from typing import Any

import pytest

from tempest_fastapi_sdk.genai.audio.stt import _require_faster_whisper
from tempest_fastapi_sdk.genai.audio.tts import _require_tts

PYPROJECT = Path(__file__).resolve().parents[3] / "pyproject.toml"


def _audio_extra() -> list[str]:
    """Read the requirement strings declared by ``[genai-audio]``.

    Returns:
        list[str]: The requirements, verbatim.
    """
    data = tomllib.loads(PYPROJECT.read_text(encoding="utf-8"))
    extras: dict[str, list[str]] = data["project"]["optional-dependencies"]
    return extras["genai-audio"]


class TestAudioExtraDeclaresItsRuntime:
    @pytest.mark.parametrize(
        "package",
        ["torch", "torchaudio", "torchcodec", "coqui-tts", "faster-whisper"],
    )
    def test_the_runtime_the_code_imports_is_declared(self, package: str) -> None:
        """coqui-tts hides its runtime behind its own extras.

        Measured with coqui-tts 0.27.5: installing it alone leaves
        ``import TTS.api`` dying on ``No module named 'torchaudio'``, so a
        consumer of ``[genai-audio]`` had to name torch, torchaudio and
        torchcodec by hand to get a working synthesis.
        """
        declared = {
            requirement.split(">")[0].split("<")[0].split("=")[0].strip()
            for requirement in _audio_extra()
        }
        assert package in declared

    def test_transformers_keeps_its_upper_bound(self) -> None:
        """``TTS.api`` imports a symbol transformers 5.1.0 removed.

        Measured: ``transformers.pytorch_utils.isin_mps_friendly`` is present
        in 5.0.0 and gone from 5.1.0 on, and ``import TTS.api`` loads the
        Tortoise layer eagerly — so an unbounded resolve breaks XTTS too.
        """
        bounds = [req for req in _audio_extra() if req.startswith("transformers")]
        assert bounds == ["transformers<5"]


class TestImportGuardsKeepTheDiagnosis:
    def test_tts_guard_quotes_the_original_error(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """The message that names the fix has to survive the re-raise."""
        real_import = builtins.__import__

        def no_torchaudio(name: str, *args: Any, **kwargs: Any) -> Any:
            if name == "TTS.api" or name.startswith("TTS"):
                raise ImportError("No module named 'torchaudio'")
            return real_import(name, *args, **kwargs)

        monkeypatch.setattr(builtins, "__import__", no_torchaudio)
        with pytest.raises(ImportError) as caught:
            _require_tts()

        assert "torchaudio" in str(caught.value)

    def test_stt_guard_quotes_the_original_error(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Same shape, same failure, same fix on the faster-whisper side."""
        real_import = builtins.__import__

        def broken(name: str, *args: Any, **kwargs: Any) -> Any:
            if name == "faster_whisper":
                raise ImportError("libctranslate2.so: cannot open shared object file")
            return real_import(name, *args, **kwargs)

        monkeypatch.setattr(builtins, "__import__", broken)
        with pytest.raises(ImportError) as caught:
            _require_faster_whisper()

        assert "libctranslate2" in str(caught.value)
