"""Tests for make_vision_router (fake task objects, no ort-vision-sdk)."""

from __future__ import annotations

import io
from typing import ClassVar

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from ort_vision_sdk.core.exceptions import ImageLoadError
from PIL import Image

from tempest_fastapi_sdk.vision import make_vision_router


class _Box:
    def as_xyxy(self) -> tuple[float, float, float, float]:
        return (1.0, 2.0, 3.0, 4.0)


class _Detection:
    class_id = 1
    class_name = "cat"
    confidence = 0.9
    bbox = _Box()


class _DetectionResults:
    detections: ClassVar = [_Detection()]


class _Prob:
    class_id = 1
    class_name = "cat"
    probability = 0.9


class _ClassificationResults:
    cls = 1
    name = "cat"
    conf = 0.9
    probabilities: ClassVar = [_Prob()]


class _FakeDetector:
    async def async_predict(self, data: bytes) -> list[_DetectionResults]:
        return [_DetectionResults()]


class _FakeClassifier:
    async def async_predict(self, data: bytes) -> list[_ClassificationResults]:
        return [_ClassificationResults()]


class _FakeSegmenter:
    async def async_predict(self, data: bytes) -> list[_DetectionResults]:
        return [_DetectionResults()]


def _png(width: int = 4, height: int = 4) -> bytes:
    buffer = io.BytesIO()
    Image.new("RGB", (width, height)).save(buffer, format="PNG")
    return buffer.getvalue()


def _flat_png(width: int, height: int) -> bytes:
    """Encode a blank 1-bit PNG: tiny on disk, a full canvas once decoded."""
    buffer = io.BytesIO()
    Image.new("1", (width, height)).save(buffer, format="PNG", optimize=True)
    return buffer.getvalue()


def _upload(data: bytes | None = None) -> dict[str, tuple[str, bytes, str]]:
    return {"file": ("x.png", data if data is not None else _png(), "image/png")}


class _RecordingDetector:
    def __init__(self) -> None:
        self.calls: int = 0

    async def async_predict(self, data: bytes) -> list[_DetectionResults]:
        self.calls += 1
        return [_DetectionResults()]


class _BrokenDecoder:
    async def async_predict(self, data: bytes) -> list[_DetectionResults]:
        raise ImageLoadError("Failed to decode image bytes")


def _client(**options: object) -> TestClient:
    app = FastAPI()
    app.include_router(make_vision_router(**options))  # type: ignore[arg-type]
    return TestClient(app)


class TestVisionLimits:
    def test_oversized_upload_is_refused_before_inference(self) -> None:
        detector = _RecordingDetector()
        client = _client(detector=detector, max_upload_bytes=64)
        response = client.post("/api/vision/detect", files=_upload(b"x" * 65))
        assert response.status_code == 422
        assert "larger than" in response.json()["detail"]
        assert detector.calls == 0

    def test_decompression_bomb_is_refused_before_decode(self) -> None:
        bomb = _flat_png(9400, 9400)
        assert len(bomb) < 20_000
        detector = _RecordingDetector()
        client = _client(detector=detector)
        response = client.post("/api/vision/detect", files=_upload(bomb))
        assert response.status_code == 422
        assert "pixels" in response.json()["detail"]
        assert detector.calls == 0

    def test_pixel_cap_is_configurable(self) -> None:
        detector = _RecordingDetector()
        client = _client(detector=detector, max_image_pixels=100)
        response = client.post("/api/vision/detect", files=_upload(_png(20, 20)))
        assert response.status_code == 422
        assert detector.calls == 0

    def test_non_image_is_refused_with_422(self) -> None:
        detector = _RecordingDetector()
        client = _client(detector=detector)
        response = client.post("/api/vision/detect", files=_upload(b"fakebytes"))
        assert response.status_code == 422
        assert detector.calls == 0

    def test_image_load_error_maps_to_422_not_500(self) -> None:
        client = _client(detector=_BrokenDecoder(), max_image_pixels=None)
        response = client.post("/api/vision/detect", files=_upload(b"fakebytes"))
        assert response.status_code == 422

    def test_a_real_image_within_limits_passes(self) -> None:
        detector = _RecordingDetector()
        client = _client(detector=detector)
        response = client.post("/api/vision/detect", files=_upload())
        assert response.status_code == 200
        assert detector.calls == 1


class TestMakeVisionRouter:
    def test_requires_a_task(self) -> None:
        with pytest.raises(ValueError, match="at least one"):
            make_vision_router()

    def test_detect_endpoint(self) -> None:
        app = FastAPI()
        app.include_router(make_vision_router(detector=_FakeDetector()))
        client = TestClient(app)
        resp = client.post("/api/vision/detect", files=_upload())
        assert resp.status_code == 200
        body = resp.json()
        assert body[0]["class_name"] == "cat"
        assert body[0]["box"]["x2"] == 3.0

    def test_classify_endpoint(self) -> None:
        app = FastAPI()
        app.include_router(make_vision_router(classifier=_FakeClassifier()))
        client = TestClient(app)
        resp = client.post("/api/vision/classify", files=_upload())
        assert resp.status_code == 200
        assert resp.json()["class_name"] == "cat"

    def test_segment_endpoint(self) -> None:
        app = FastAPI()
        app.include_router(make_vision_router(segmenter=_FakeSegmenter()))
        client = TestClient(app)
        resp = client.post("/api/vision/segment", files=_upload())
        assert resp.status_code == 200
        assert resp.json()[0]["class_name"] == "cat"

    def test_only_injected_endpoints_mounted(self) -> None:
        app = FastAPI()
        app.include_router(make_vision_router(detector=_FakeDetector()))
        client = TestClient(app)
        assert client.post("/api/vision/classify", files=_upload()).status_code == 404
        assert client.post("/api/vision/segment", files=_upload()).status_code == 404
