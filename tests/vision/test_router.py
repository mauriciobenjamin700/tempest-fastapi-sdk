"""Tests for make_vision_router (fake task objects, no ort-vision-sdk)."""

from __future__ import annotations

from typing import ClassVar

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

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


def _upload() -> dict[str, tuple[str, bytes, str]]:
    return {"file": ("x.png", b"fakebytes", "image/png")}


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
