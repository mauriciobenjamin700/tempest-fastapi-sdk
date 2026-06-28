"""Tests for the vision integration (schemas + mappers + lazy export)."""

from __future__ import annotations

import importlib.util

import pytest

from tempest_fastapi_sdk import vision
from tempest_fastapi_sdk.vision import (
    ClassificationSchema,
    DetectionSchema,
    SegmentationSchema,
    to_classification_schema,
    to_detection_schemas,
    to_segmentation_schemas,
)


class _FakeBox:
    def __init__(self, xyxy: tuple[float, float, float, float]) -> None:
        self._xyxy = xyxy

    def as_xyxy(self) -> tuple[float, float, float, float]:
        return self._xyxy


class _FakeDetection:
    def __init__(self, name: str, conf: float) -> None:
        self.class_id = 1
        self.class_name = name
        self.confidence = conf
        self.bbox = _FakeBox((10.0, 20.0, 30.0, 40.0))


class _FakeDetResults:
    def __init__(self, dets: list[_FakeDetection]) -> None:
        self.detections = dets


class _FakeProb:
    def __init__(self, name: str, p: float) -> None:
        self.class_id = 2
        self.class_name = name
        self.probability = p


class _FakeClsResults:
    cls = 2
    name = "cat"
    conf = 0.91
    probabilities = (_FakeProb("cat", 0.91), _FakeProb("dog", 0.06))


class TestMappers:
    def test_detection_mapping(self) -> None:
        out = to_detection_schemas(_FakeDetResults([_FakeDetection("car", 0.8)]))
        assert len(out) == 1
        assert isinstance(out[0], DetectionSchema)
        assert out[0].class_name == "car"
        assert out[0].box.model_dump() == {
            "x1": 10.0,
            "y1": 20.0,
            "x2": 30.0,
            "y2": 40.0,
        }

    def test_detection_empty(self) -> None:
        assert to_detection_schemas(_FakeDetResults([])) == []

    def test_classification_mapping(self) -> None:
        out = to_classification_schema(_FakeClsResults())
        assert isinstance(out, ClassificationSchema)
        assert (out.class_name, out.confidence) == ("cat", 0.91)
        assert [p.class_name for p in out.probabilities] == ["cat", "dog"]

    def test_segmentation_mapping(self) -> None:
        out = to_segmentation_schemas(_FakeDetResults([_FakeDetection("person", 0.7)]))
        assert len(out) == 1
        assert isinstance(out[0], SegmentationSchema)
        assert out[0].class_name == "person"


class TestLazyExport:
    @pytest.mark.skipif(
        importlib.util.find_spec("ort_vision_sdk") is not None,
        reason="ort-vision-sdk installed; the missing-extra path can't be exercised",
    )
    def test_task_class_raises_without_extra(self) -> None:
        with pytest.raises(ImportError, match=r"\[vision\] extra"):
            _ = vision.Detector

    def test_unknown_attribute_raises_attribute_error(self) -> None:
        with pytest.raises(AttributeError):
            _ = vision.DoesNotExist
