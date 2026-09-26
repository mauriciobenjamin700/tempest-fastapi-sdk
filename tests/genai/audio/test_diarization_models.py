"""Pinned digests and bounded downloads for the diarization models.

Regression suite: both shipped :class:`DiarizationModel` constants carried
``sha256=""``, so :func:`ensure_models` skipped verification while the
docstring promised a check on every fetch; and ``urlopen`` had no timeout, so
a stalled connection hung ``load()`` forever.
"""

from __future__ import annotations

import hashlib
import io
from pathlib import Path
from typing import Any

import pytest

from tempest_fastapi_sdk.genai.audio import diarization
from tempest_fastapi_sdk.genai.audio.diarization import (
    _DOWNLOAD_TIMEOUT_SECONDS,
    EMBEDDING_MODEL,
    SEGMENTATION_MODEL,
    DiarizationModel,
    ensure_models,
)


class TestPinnedDigests:
    def test_segmentation_digest_is_pinned(self) -> None:
        assert SEGMENTATION_MODEL.sha256 == (
            "220ad67ca923bef2fa91f2390c786097bf305bceb5e261d4af67b38e938e1079"
        )

    def test_embedding_digest_is_pinned(self) -> None:
        assert EMBEDDING_MODEL.sha256 == (
            "1a331345f04805badbb495c775a6ddffcdd1a732567d5ec8b3d5749e3c7a5e4b"
        )

    @pytest.mark.parametrize("model", [SEGMENTATION_MODEL, EMBEDDING_MODEL])
    def test_a_tampered_cached_file_is_refused(
        self, model: DiarizationModel, tmp_path: Path
    ) -> None:
        target = tmp_path / model.member
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(b"not the published model")
        with pytest.raises(OSError, match="expected sha256"):
            ensure_models(tmp_path, models=[model])

    def test_a_matching_cached_file_resolves(self, tmp_path: Path) -> None:
        payload = b"weights"
        model = DiarizationModel(
            name="custom",
            url="https://example.invalid/custom.onnx",
            member="custom.onnx",
            sha256=hashlib.sha256(payload).hexdigest(),
        )
        (tmp_path / "custom.onnx").write_bytes(payload)
        assert ensure_models(tmp_path, models=[model]) == {
            "custom": tmp_path / "custom.onnx",
        }


class TestBoundedDownload:
    def test_urlopen_gets_a_timeout(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
    ) -> None:
        seen: dict[str, Any] = {}

        def _fake_urlopen(url: str, *args: Any, **kwargs: Any) -> io.BytesIO:
            seen["url"] = url
            seen["args"] = args
            seen["kwargs"] = kwargs
            return io.BytesIO(b"payload")

        monkeypatch.setattr(diarization.urllib.request, "urlopen", _fake_urlopen)
        destination = tmp_path / "model.onnx"
        diarization._download("https://example.invalid/model.onnx", destination)
        assert destination.read_bytes() == b"payload"
        assert seen["kwargs"].get("timeout") == _DOWNLOAD_TIMEOUT_SECONDS
        assert _DOWNLOAD_TIMEOUT_SECONDS > 0
