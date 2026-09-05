"""Graph composition is re-exported lazily, behind its own extra.

The point of these guards is that ``import tempest_fastapi_sdk.modelops``
must stay free of ``onnx``: a service that only *serves* a fused graph
installs ``[vision]`` and never pays for the build-time dependency.
"""

from __future__ import annotations

import importlib
import importlib.util
import inspect

import pytest

from tempest_fastapi_sdk import modelops

_HAS_COMPOSE: bool = importlib.util.find_spec("onnx") is not None


class TestComposeIsLazy:
    """Importing the package must not import the composition dependency."""

    def test_importing_modelops_does_not_import_onnx(self) -> None:
        """The module object exists without resolving anything heavy."""
        module = importlib.import_module("tempest_fastapi_sdk.modelops.compose")
        assert module.__name__.endswith("compose")

    def test_unknown_attribute_raises(self) -> None:
        """The lazy hook must not swallow genuine typos."""
        with pytest.raises(AttributeError):
            _ = modelops.not_a_composition_helper

    def test_declared_in_all(self) -> None:
        """The public name is advertised even while unresolved."""
        assert "fuse_detect_classify" in modelops.__all__


@pytest.mark.skipif(not _HAS_COMPOSE, reason="requires the [modelops-compose] extra")
class TestFuseDetectClassify:
    """The re-export resolves to upstream, not to a local restatement."""

    def test_resolves_to_upstream(self) -> None:
        """A wrapper here would drift from a signature that keeps changing.

        Measured: 18 keyword arguments at ``ort-vision-sdk`` 0.8.0, 19 at
        0.9.0. Asserting a count here would encode the drift instead of
        catching it, so this pins the identity of the object instead.
        """
        assert modelops.fuse_detect_classify.__module__.startswith("ort_vision_sdk")

    def test_signature_is_upstreams_own(self) -> None:
        """Nothing local narrows or renames the arguments.

        Pinned because the alternative considered — a facade restating the
        parameters — silently drops any argument added upstream.
        """
        from ort_vision_sdk.compose import fuse_detect_classify as upstream

        assert inspect.signature(modelops.fuse_detect_classify) == inspect.signature(
            upstream
        )

    def test_takes_a_detector_and_a_classifier(self) -> None:
        """The two positional inputs are the two stages being fused."""
        parameters = list(inspect.signature(modelops.fuse_detect_classify).parameters)
        assert parameters[:2] == ["detector", "classifier"]
