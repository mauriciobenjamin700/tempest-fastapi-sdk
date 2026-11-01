"""Guards for what the ``[genai-audio]`` extra promises and what it says.

Two failures shipped together in v0.245.0 and are both covered here: the
extra installed ``coqui-tts`` without the runtime that ``import TTS.api``
requires, and the import guard replaced the upstream diagnosis with an
instruction to install the extra that was already installed (issue #191).

Since v0.342.0 the extra no longer caps ``transformers<5`` (#683), which is
only safe while ``coqui-tts`` stays at or above the release that stopped
importing the symbol transformers 5.1 removed; that pairing is guarded too.
"""

from __future__ import annotations

import builtins
import tomllib
from pathlib import Path
from typing import Any

import pytest
from packaging.requirements import Requirement
from packaging.version import Version

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
        ["torch", "torchaudio", "torchcodec", "coqui-tts", "faster-whisper", "six"],
    )
    def test_the_runtime_the_code_imports_is_declared(self, package: str) -> None:
        """coqui-tts hides its runtime behind its own extras.

        Measured with coqui-tts 0.27.5: installing it alone leaves
        ``import TTS.api`` dying on ``No module named 'torchaudio'``, so a
        consumer of ``[genai-audio]`` had to name torch, torchaudio and
        torchcodec by hand to get a working synthesis. ``six`` joined the
        list with coqui-tts 0.28.0, which imports it without declaring it:
        measured in a clean venv, ``import TTS.api`` dies on
        ``No module named 'six'``.
        """
        declared = {
            requirement.split(">")[0].split("<")[0].split("=")[0].strip()
            for requirement in _audio_extra()
        }
        assert package in declared

    def test_an_unbounded_transformers_needs_coqui_0_28(self) -> None:
        assert _transformers_pairing_problem(_audio_extra()) is None

    @pytest.mark.parametrize(
        ("requirements", "expected"),
        [
            (["coqui-tts>=0.28.0"], None),
            (["coqui-tts>=0.27.5", "transformers<5"], None),
            (["coqui-tts>=0.27.5"], "coqui-tts floor 0.27.5"),
            (["coqui-tts"], "coqui-tts floor none"),
        ],
    )
    def test_the_pairing_check_fires_on_the_unsafe_shape(
        self, requirements: list[str], expected: str | None
    ) -> None:
        """Dropping the cap with the old coqui floor is what must fail."""
        problem = _transformers_pairing_problem(requirements)
        if expected is None:
            assert problem is None
        else:
            assert problem is not None and expected in problem


COQUI_TRANSFORMERS_5_FLOOR: Version = Version("0.28.0")
"""First coqui-tts that imports cleanly on transformers 5.

coqui-tts 0.27.5 did ``from transformers.pytorch_utils import
isin_mps_friendly``, a symbol present in transformers 5.0.0 and gone from
5.1.0 on; 0.28.0 has zero references to it (searched every ``.py`` of its
wheel) and ``import TTS.api`` succeeds on transformers 5.22.1 (#683).
"""


def _transformers_pairing_problem(requirements: list[str]) -> str | None:
    """Explain why a ``[genai-audio]`` requirement list is unsafe, if it is.

    Args:
        requirements (list[str]): The extra's requirement strings.

    Returns:
        str | None: ``None`` when transformers is capped below 5, or when
        coqui-tts is floored at :data:`COQUI_TRANSFORMERS_5_FLOOR` or above;
        otherwise a one-line description naming the coqui-tts floor.
    """
    parsed = [Requirement(requirement) for requirement in requirements]
    capped = any(
        requirement.name == "transformers"
        and not requirement.specifier.contains("5.1.0")
        for requirement in parsed
    )
    if capped:
        return None
    floors = [
        Version(spec.version)
        for requirement in parsed
        if requirement.name == "coqui-tts"
        for spec in requirement.specifier
        if spec.operator in (">=", "==")
    ]
    floor = max(floors) if floors else None
    if floor is not None and floor >= COQUI_TRANSFORMERS_5_FLOOR:
        return None
    return (
        f"transformers is uncapped but coqui-tts floor {floor or 'none'} is below "
        f"{COQUI_TRANSFORMERS_5_FLOOR}"
    )


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
