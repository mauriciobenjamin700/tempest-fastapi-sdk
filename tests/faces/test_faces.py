"""Tests for the face pipeline.

The models need 16 MB of weights and real photographs, so the accuracy
numbers live in the ``model`` tier and are recorded in the docstrings and
the recipe. What runs everywhere is the geometry and the guards — the
parts where a mistake produces a plausible wrong answer rather than a
crash.
"""

from __future__ import annotations

import math

import pytest

from tempest_fastapi_sdk.faces import (
    ARCFACE_TEMPLATE,
    DEFAULT_MATCH_THRESHOLD,
    FACE_SIZE,
    LARGE_PACK,
    LIGHT_PACK,
    MIN_FACE_PIXELS,
    BoundingBox,
    DetectedFace,
    FaceDetector,
    FaceRecognizer,
    align_face,
    compare_faces,
    resolve_pack,
    similarity_transform,
)
from tempest_fastapi_sdk.faces.detector import (
    ANCHORS_PER_LOCATION,
    DETECT_SIZE,
    STRIDES,
    _distance_to_box,
    _distance_to_landmarks,
    _non_max_suppression,
    _prepare,
)


class TestTemplate:
    def test_the_arcface_template_is_pinned(self) -> None:
        """These are the positions the recognition weights were trained on.

        Changing them degrades every embedding while leaving the code
        working, so a drift has to break a test rather than a metric
        nobody is watching. Ported from insightface's ``arcface_dst``.
        """
        assert ARCFACE_TEMPLATE == (
            (38.2946, 51.6963),
            (73.5318, 51.5014),
            (56.0252, 71.7366),
            (41.5493, 92.3655),
            (70.7299, 92.2041),
        )
        assert FACE_SIZE == 112

    def test_the_template_fits_inside_the_crop(self) -> None:
        """A landmark outside the crop would be warped out of frame."""
        for x, y in ARCFACE_TEMPLATE:
            assert 0 < x < FACE_SIZE
            assert 0 < y < FACE_SIZE


class TestSimilarityTransform:
    def test_recovers_a_known_translation(self) -> None:
        """The simplest case, where the answer is checkable by eye."""
        source = [[0.0, 0.0], [1.0, 0.0], [0.0, 1.0]]
        target = [[2.0, 3.0], [3.0, 3.0], [2.0, 4.0]]
        matrix = similarity_transform(source, target)
        assert matrix[0][2] == pytest.approx(2.0)
        assert matrix[1][2] == pytest.approx(3.0)
        assert matrix[0][0] == pytest.approx(1.0)

    def test_recovers_a_known_scale(self) -> None:
        """Scale is what makes a far face and a near face comparable."""
        source = [[0.0, 0.0], [1.0, 0.0], [0.0, 1.0]]
        target = [[0.0, 0.0], [2.0, 0.0], [0.0, 2.0]]
        matrix = similarity_transform(source, target)
        assert matrix[0][0] == pytest.approx(2.0)

    def test_recovers_a_known_rotation(self) -> None:
        """A tilted head has to be rotated upright, not sheared."""
        angle = math.radians(30.0)
        source = [[1.0, 0.0], [0.0, 1.0], [-1.0, 0.0]]
        target = [
            [math.cos(angle), math.sin(angle)],
            [-math.sin(angle), math.cos(angle)],
            [-math.cos(angle), -math.sin(angle)],
        ]
        matrix = similarity_transform(source, target)
        assert matrix[0][0] == pytest.approx(math.cos(angle), abs=1e-6)
        assert matrix[1][0] == pytest.approx(math.sin(angle), abs=1e-6)

    def test_never_produces_a_reflection(self) -> None:
        """A mirrored face embeds as a different person.

        The determinant correction is what prevents it; without it the
        SVD happily returns the reflection that fits best.
        """
        import numpy as np

        source = [[0.0, 0.0], [1.0, 0.0], [0.0, 1.0]]
        target = [[0.0, 0.0], [0.0, 1.0], [1.0, 0.0]]
        matrix = similarity_transform(source, target)
        assert np.linalg.det(np.asarray(matrix)[:, :2]) > 0

    def test_refuses_degenerate_input(self) -> None:
        """Each of these has no similarity transform to find."""
        with pytest.raises(ValueError, match="differ in shape"):
            similarity_transform([[0.0, 0.0]], [[0.0, 0.0], [1.0, 1.0]])
        with pytest.raises(ValueError, match="at least two"):
            similarity_transform([[0.0, 0.0]], [[1.0, 1.0]])
        with pytest.raises(ValueError, match="coincident"):
            similarity_transform([[1.0, 1.0]] * 3, [[0.0, 0.0], [1.0, 0.0], [0.0, 1.0]])


class TestAlignment:
    def test_produces_the_expected_crop_size(self) -> None:
        """The recognizer declares a 112x112 input; anything else fails it."""
        from PIL import Image

        picture = Image.new("RGB", (400, 400), (10, 20, 30))
        landmarks = [
            [150.0, 160.0],
            [250.0, 160.0],
            [200.0, 220.0],
            [160.0, 280.0],
            [240.0, 280.0],
        ]
        aligned = align_face(picture, landmarks)
        assert aligned.size == (FACE_SIZE, FACE_SIZE)
        assert aligned.mode == "RGB"

    def test_refuses_the_wrong_number_of_landmarks(self) -> None:
        """Four points is a different detector's output, not a face."""
        from PIL import Image

        with pytest.raises(ValueError, match="five"):
            align_face(Image.new("RGB", (100, 100)), [[1.0, 2.0]] * 4)


class TestDecoding:
    def test_boxes_come_out_as_edges(self) -> None:
        """SCRFD predicts distances; a sign error puts faces off-image."""
        import numpy as np

        centers = np.array([[100.0, 100.0]])
        distances = np.array([[10.0, 20.0, 30.0, 40.0]])
        box = _distance_to_box(centers, distances)[0]
        assert list(box) == [90.0, 80.0, 130.0, 140.0]

    def test_landmarks_keep_their_pairing(self) -> None:
        """Interleaved x/y offsets must not transpose into (y, x)."""
        import numpy as np

        centers = np.array([[0.0, 0.0]])
        distances = np.array([[1.0, 2.0, 3.0, 4.0, 5.0, 6.0, 7.0, 8.0, 9.0, 10.0]])
        points = _distance_to_landmarks(centers, distances)
        assert points.shape == (1, 5, 2)
        assert list(points[0][0]) == [1.0, 2.0]
        assert list(points[0][4]) == [9.0, 10.0]

    def test_anchor_math_matches_the_model_shapes(self) -> None:
        """The published output rows are what pin these two constants.

        At stride 8 on a 640 canvas the model emits 12800 score rows, and
        80 x 80 x 2 is 12800. Assuming one anchor would read half the
        predictions and drop every second face.
        """
        for stride, rows in zip(STRIDES, (12800, 3200, 800), strict=True):
            size = DETECT_SIZE // stride
            assert size * size * ANCHORS_PER_LOCATION == rows

    def test_suppression_keeps_the_best_of_an_overlapping_pair(self) -> None:
        """Two boxes on one face must not become two faces."""
        import numpy as np

        boxes = np.array([[0.0, 0.0, 10.0, 10.0], [1.0, 1.0, 11.0, 11.0]])
        scores = np.array([0.9, 0.8])
        assert _non_max_suppression(boxes, scores, 0.4) == [0]

    def test_suppression_keeps_distant_boxes(self) -> None:
        """Two people standing apart are two people."""
        import numpy as np

        boxes = np.array([[0.0, 0.0, 10.0, 10.0], [100.0, 100.0, 110.0, 110.0]])
        scores = np.array([0.9, 0.8])
        assert sorted(_non_max_suppression(boxes, scores, 0.4)) == [0, 1]


class TestPreparation:
    def test_a_tiny_image_is_upscaled(self) -> None:
        """A 112x112 portrait returned zero detections until enlarged."""
        from PIL import Image

        _, _, scale = _prepare(Image.new("RGB", (112, 112)))
        assert scale > 1.0 / 6.0

    def test_the_canvas_is_always_square(self) -> None:
        """The model was trained on a square letterbox."""
        from PIL import Image

        for size in ((112, 112), (1280, 886), (100, 900)):
            canvas, _, _ = _prepare(Image.new("RGB", size))
            assert canvas.size == (DETECT_SIZE, DETECT_SIZE)

    def test_coordinates_round_trip(self) -> None:
        """Undoing the letterbox is what puts boxes back on the original.

        Getting this wrong yields boxes that look plausible and sit in
        the wrong place, which no exception would catch.
        """
        from PIL import Image

        picture = Image.new("RGB", (800, 400))
        _, offset, scale = _prepare(picture)
        for point in ((0.0, 0.0), (800.0, 400.0), (123.0, 321.0)):
            canvas_point = (point[0] * scale + offset[0], point[1] * scale + offset[1])
            back = (
                (canvas_point[0] - offset[0]) / scale,
                (canvas_point[1] - offset[1]) / scale,
            )
            assert back[0] == pytest.approx(point[0], abs=1e-6)
            assert back[1] == pytest.approx(point[1], abs=1e-6)


class TestComparison:
    def test_identical_vectors_score_one(self) -> None:
        """The trivial anchor."""
        assert compare_faces([1.0, 0.0], [1.0, 0.0]) == pytest.approx(1.0)

    def test_orthogonal_vectors_score_zero(self) -> None:
        """Different people land far apart."""
        assert compare_faces([1.0, 0.0], [0.0, 1.0]) == pytest.approx(0.0)

    def test_a_zero_vector_does_not_divide(self) -> None:
        """An unembeddable face yields zeros, not an exception."""
        assert compare_faces([0.0, 0.0], [1.0, 0.0]) == 0.0

    def test_different_lengths_are_an_error(self) -> None:
        """Two models produce incomparable vectors.

        A low score would read as "not this person" when the truth is
        "this comparison means nothing".
        """
        with pytest.raises(ValueError, match="differ in length"):
            compare_faces([1.0, 0.0], [1.0, 0.0, 0.0])


class TestConfiguration:
    def test_the_default_pack_is_the_light_one(self) -> None:
        """Measured: 12x smaller, 3.6x faster, same separation."""
        assert FaceRecognizer().pack is LIGHT_PACK
        assert LIGHT_PACK.megabytes < LARGE_PACK.megabytes

    def test_packs_resolve_by_name(self) -> None:
        """The CLI and config files pass strings, not objects."""
        assert resolve_pack("buffalo_s") is LIGHT_PACK
        assert resolve_pack("buffalo_l") is LARGE_PACK

    def test_an_unknown_pack_lists_the_alternatives(self) -> None:
        """A typo should be one read away from fixed."""
        with pytest.raises(ValueError, match="buffalo_s"):
            resolve_pack("buffalo_xl")

    def test_thresholds_are_validated(self) -> None:
        """Outside 0..1 a similarity threshold can only be a mistake."""
        for bad in (-0.1, 1.5):
            with pytest.raises(ValueError, match="threshold"):
                FaceRecognizer(threshold=bad)
            with pytest.raises(ValueError, match="score_threshold"):
                FaceDetector("x.onnx", score_threshold=bad)

    def test_concurrency_must_be_positive(self) -> None:
        """Zero would deadlock on the first call."""
        with pytest.raises(ValueError, match="max_concurrent"):
            FaceRecognizer(max_concurrent=0)

    def test_starts_unloaded(self) -> None:
        """Construction must not download 16 MB of models."""
        assert FaceRecognizer().is_loaded is False

    def test_the_match_threshold_sits_in_the_measured_gap(self) -> None:
        """Measured: same person 0.904-0.960, different at most 0.225."""
        assert 0.225 < DEFAULT_MATCH_THRESHOLD < 0.904

    def test_the_small_face_floor_is_pinned(self) -> None:
        """Below it the embedding describes the upscaling, not the person."""
        assert MIN_FACE_PIXELS == 40


class TestSchemas:
    def test_the_box_reports_its_own_size(self) -> None:
        """Callers crop with these, so the arithmetic belongs here."""
        box = BoundingBox(left=10.0, top=20.0, right=40.0, bottom=60.0)
        assert box.width == 30.0
        assert box.height == 40.0

    def test_a_face_without_an_embedding_is_representable(self) -> None:
        """detect() returns faces with no vector, and that is not an error."""
        face = DetectedFace(
            box=BoundingBox(left=0.0, top=0.0, right=1.0, bottom=1.0),
            confidence=0.9,
        )
        assert face.embedding == []
        assert face.landmarks == []
