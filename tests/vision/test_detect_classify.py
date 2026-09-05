"""The fused detect-classify surface: lazy task, schema, mapper, drift.

The mapper duck-types its input, so the fakes below would keep passing if
``ort-vision-sdk`` renamed a field tomorrow. :class:`TestUpstreamShape` is
what stops that: it reads the attribute names off the **real** upstream
dataclasses, so a rename upstream fails here instead of in a service.
"""

from __future__ import annotations

import importlib.util

import pytest

from tempest_fastapi_sdk import vision
from tempest_fastapi_sdk.vision import (
    DetectClassifySchema,
    to_detect_classify_schemas,
)

_HAS_ORT: bool = importlib.util.find_spec("ort_vision_sdk") is not None


class _FakeBox:
    """Stands in for ``ort_vision_sdk.BoundingBox``."""

    def as_xyxy(self) -> tuple[float, float, float, float]:
        """Return the box corners.

        Returns:
            tuple[float, float, float, float]: The xyxy corners.
        """
        return (1.0, 2.0, 3.0, 4.0)


class _FakeProb:
    """Stands in for ``ort_vision_sdk.ClassProbability``."""

    def __init__(self, class_id: int, class_name: str, probability: float) -> None:
        """Store the ranked score.

        Args:
            class_id (int): Classifier class index.
            class_name (str): Classifier label.
            probability (float): Score in ``[0, 1]``.
        """
        self.class_id = class_id
        self.class_name = class_name
        self.probability = probability


class _FakeClassification:
    """Stands in for the **singular** ``ort_vision_sdk.ClassificationResult``."""

    def __init__(self) -> None:
        """Build a second-stage verdict with two ranked scores."""
        self.class_id = 3
        self.class_name = "famacha_3"
        self.confidence = 0.77
        self.probabilities = (
            _FakeProb(3, "famacha_3", 0.77),
            _FakeProb(2, "famacha_2", 0.21),
        )


class _FakeDetection:
    """Stands in for ``ort_vision_sdk.DetectionResult``."""

    def __init__(self, classification: _FakeClassification | None) -> None:
        """Build a detection carrying an optional second stage.

        Args:
            classification (_FakeClassification | None): The classifier's
                verdict on this object's crop.
        """
        self.class_id = 18
        self.class_name = "sheep"
        self.confidence = 0.94
        self.bbox = _FakeBox()
        self.classification = classification


class _FakeResults:
    """Stands in for ``ort_vision_sdk.DetectClassifyResults``."""

    def __init__(self, detections: list[_FakeDetection]) -> None:
        """Hold the per-image detections.

        Args:
            detections (list[_FakeDetection]): The surviving detections.
        """
        self.detections = detections


class TestDetectClassifyMapper:
    """The mapper nests the classifier verdict under the detection."""

    def test_maps_both_stages(self) -> None:
        """Detector and classifier labels both survive, and stay separate."""
        out = to_detect_classify_schemas(
            _FakeResults([_FakeDetection(_FakeClassification())])
        )
        assert len(out) == 1
        schema = out[0]
        assert isinstance(schema, DetectClassifySchema)
        assert schema.class_id == 18
        assert schema.class_name == "sheep"
        assert schema.classification is not None
        assert schema.classification.class_id == 3
        assert schema.classification.class_name == "famacha_3"

    def test_label_spaces_do_not_collide(self) -> None:
        """The two ``class_id`` values are unrelated and must not be merged."""
        out = to_detect_classify_schemas(
            _FakeResults([_FakeDetection(_FakeClassification())])
        )
        schema = out[0]
        assert schema.classification is not None
        assert schema.class_id != schema.classification.class_id

    def test_ranked_scores_are_carried(self) -> None:
        """The classifier's top-k comes through in order."""
        out = to_detect_classify_schemas(
            _FakeResults([_FakeDetection(_FakeClassification())])
        )
        assert out[0].classification is not None
        names = [p.class_name for p in out[0].classification.probabilities]
        assert names == ["famacha_3", "famacha_2"]

    def test_missing_second_stage_is_none(self) -> None:
        """A detection with no classification maps to ``None``, not an error."""
        out = to_detect_classify_schemas(_FakeResults([_FakeDetection(None)]))
        assert out[0].classification is None

    def test_no_detections_is_an_empty_list(self) -> None:
        """An empty collection is success, per the SDK's own convention."""
        assert to_detect_classify_schemas(_FakeResults([])) == []


@pytest.mark.skipif(not _HAS_ORT, reason="requires the [vision] extra")
class TestUpstreamShape:
    """The fakes above must keep matching the real upstream dataclasses."""

    def test_detection_result_has_the_fields_the_mapper_reads(self) -> None:
        """A rename in ``DetectionResult`` fails here, not in a service."""
        from ort_vision_sdk import DetectionResult

        fields = set(DetectionResult.__annotations__)
        assert {"class_id", "class_name", "confidence", "bbox", "classification"} <= (
            fields
        )

    def test_classification_result_is_the_singular_spelling(self) -> None:
        """The nested verdict uses ``class_id``/``confidence``, not ``cls``/``conf``.

        The plural ``ClassificationResults`` spells them ``cls`` / ``name`` /
        ``conf``, which is the trap this pins: reading the plural names off
        the singular object yields ``AttributeError`` at request time.
        """
        from ort_vision_sdk import ClassificationResult

        fields = set(ClassificationResult.__annotations__)
        assert {"class_id", "class_name", "confidence", "probabilities"} <= fields

    def test_detect_classify_results_carries_two_label_spaces(self) -> None:
        """The second label map is what makes this type distinct."""
        from ort_vision_sdk import DetectClassifyResults

        fields = set(DetectClassifyResults.__annotations__)
        assert {"detections", "names", "classifier_names"} <= fields


@pytest.mark.skipif(not _HAS_ORT, reason="requires the [vision] extra")
class TestLazyTaskExport:
    """``DetectClassify`` resolves through the package's lazy re-export."""

    def test_task_resolves(self) -> None:
        """The name comes from ``ort_vision_sdk``, not a local wrapper."""
        assert vision.DetectClassify.__module__.startswith("ort_vision_sdk")

    def test_results_type_resolves(self) -> None:
        """The result envelope is re-exported alongside the task."""
        assert vision.DetectClassifyResults.__module__.startswith("ort_vision_sdk")

    def test_unknown_attribute_still_raises(self) -> None:
        """The lazy hook must not swallow genuine typos."""
        with pytest.raises(AttributeError):
            _ = vision.NotAThing

    def test_the_task_needs_no_onnx(self) -> None:
        """Running a fused graph is ``[vision]``; building one is not.

        Measured against ``ort-vision-sdk`` 0.8.0 installed without its
        ``[compose]`` extra: ``DetectClassify`` imports, and
        ``ort_vision_sdk.compose`` raises ``ImportError: No module named
        'onnx'``. That split is why the two live behind different extras.
        """
        import ort_vision_sdk

        assert hasattr(ort_vision_sdk, "DetectClassify")
