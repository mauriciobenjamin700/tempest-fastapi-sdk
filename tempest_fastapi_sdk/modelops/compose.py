"""Fuse a detector and a classifier into one ``.onnx`` graph.

The rest of :mod:`modelops` is the life cycle of **one** model — export it,
quantize it, benchmark it, convert it for the edge. Composing **two** is a
different job, and the one this module covers: a detector at 640 finding
the objects, a classifier at 224 judging each crop, and a single graph
that runs both.

    from tempest_fastapi_sdk.modelops import fuse_detect_classify
    from tempest_fastapi_sdk.vision import DetectClassify

    fuse_detect_classify("detector.onnx", "classifier.onnx", "fused.onnx")
    pipeline = DetectClassify("fused.onnx")
    for detection in pipeline.predict("flock.jpg")[0]:
        print(detection.name, detection.classification.name)

Fusing and running are **two extras**, because they are two machines. The
build step needs ``[modelops-compose]``; the service that serves the fused
graph needs only ``[vision]``, and never imports ``onnx`` at all.

**``onnx.compose.merge_models`` does not do this.** Between the stages sits
a *dynamic* crop — how many boxes there are, and where, is known only once
the detector has run — so the two graphs cannot simply be concatenated.
The bridge that closes it is ``RoiAlign``, which is what
``fuse_detect_classify`` assembles.

The implementation is `ort-vision-sdk
<https://pypi.org/project/ort-vision-sdk/>`_, re-exported **lazily**:
touching the name imports it and raises an ``ImportError`` naming the
extra when it is missing. It is re-exported rather than wrapped on
purpose — the function takes eighteen keyword arguments, and a wrapper
that restated them would drift from upstream the first time one is added.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from ort_vision_sdk.compose import (
        fuse_detect_classify as fuse_detect_classify,
    )

_LAZY_EXPORTS: frozenset[str] = frozenset({"fuse_detect_classify"})


def __getattr__(name: str) -> Any:
    """Lazily resolve the ``ort-vision-sdk`` graph-composition helpers.

    Args:
        name (str): The attribute requested.

    Returns:
        Any: The ``ort_vision_sdk.compose`` symbol when ``name`` is one of
        :data:`_LAZY_EXPORTS`.

    Raises:
        ImportError: When the ``[modelops-compose]`` extra is not installed.
        AttributeError: For any other attribute name.
    """
    if name in _LAZY_EXPORTS:
        try:
            from ort_vision_sdk import compose
        except ImportError as exc:  # pragma: no cover - guarded by extra
            raise ImportError(
                "Graph composition requires the optional [modelops-compose] "
                "extra (ort-vision-sdk[compose], which adds onnx). Install "
                "with: pip install tempest-fastapi-sdk[modelops-compose]",
            ) from exc
        return getattr(compose, name)
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")


__all__: list[str] = ["fuse_detect_classify"]
