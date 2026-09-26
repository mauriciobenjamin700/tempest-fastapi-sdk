"""A failed synthesis must not leave its temporary ``.wav`` behind.

Without ``out_path`` the audio goes through ``tempfile.mkstemp``. The
shipped code unlinked it only after a successful ``tts_to_file`` — a bad
speaker, an unsupported language or an out-of-memory left one file in the
temp directory per failed request. Coqui is replaced through
``_require_tts``, so the test needs no weights.
"""

from __future__ import annotations

import tempfile
from pathlib import Path
from typing import Any

import pytest

from tempest_fastapi_sdk.genai.audio import TextToSpeech
from tempest_fastapi_sdk.genai.audio import tts as tts_module


class FailingVoice:
    """Writes part of a file, then raises, like a synthesis that breaks."""

    def to(self, device: str) -> FailingVoice:
        """Return itself.

        Args:
            device (str): Ignored.

        Returns:
            FailingVoice: This voice.
        """
        return self

    def tts_to_file(self, *, file_path: str, **kwargs: Any) -> None:
        """Start writing and fail.

        Args:
            file_path (str): Where to write.
            **kwargs (Any): Ignored.

        Raises:
            ValueError: Always.
        """
        Path(file_path).write_bytes(b"RIFF")
        raise ValueError("speaker 'nobody' not found")


def test_failed_synthesis_removes_its_temp_file(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    created: list[str] = []
    real_mkstemp = tempfile.mkstemp

    def _recording_mkstemp(*args: Any, **kwargs: Any) -> tuple[int, str]:
        handle, name = real_mkstemp(*args, **kwargs)
        created.append(name)
        return handle, name

    monkeypatch.setattr(tempfile, "mkstemp", _recording_mkstemp)
    monkeypatch.setattr(
        tts_module,
        "_require_tts",
        lambda: lambda model_name: FailingVoice(),
    )
    voice = TextToSpeech(device="cpu")

    with pytest.raises(ValueError, match="nobody"):
        voice._synthesize_sync("hi", None, "nobody", None, None)

    assert len(created) == 1
    assert not Path(created[0]).exists()
