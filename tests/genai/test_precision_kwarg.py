"""Tests for the ``from_pretrained`` precision keyword shim."""

from __future__ import annotations

import sys
import types
from collections.abc import Callable
from typing import Any

import pytest

from tempest_fastapi_sdk.genai.schemas import DTYPE_KWARG_RENAMED_IN, precision_kwarg


@pytest.fixture
def fake_transformers(monkeypatch: pytest.MonkeyPatch) -> Callable[[str], None]:
    """Install a stand-in ``transformers`` module with a chosen version.

    Returns:
        A callable taking the version string to report.
    """

    def _install(version: str) -> None:
        module = types.ModuleType("transformers")
        module.__version__ = version  # type: ignore[attr-defined]
        monkeypatch.setitem(sys.modules, "transformers", module)

    return _install


class TestPrecisionKwarg:
    @pytest.mark.parametrize("version", ["4.56.0", "4.57.2", "5.14.1", "5.0.0.dev0"])
    def test_modern_transformers_gets_dtype(
        self, fake_transformers: Any, version: str
    ) -> None:
        """From 4.56 on, ``torch_dtype`` is deprecated and logs on every load."""
        fake_transformers(version)
        assert precision_kwarg("bfloat16") == {"dtype": "bfloat16"}

    @pytest.mark.parametrize("version", ["4.44.0", "4.55.0", "4.55.4"])
    def test_older_transformers_gets_torch_dtype(
        self, fake_transformers: Any, version: str
    ) -> None:
        """Below 4.56 the new name is not a parameter — it would be swallowed."""
        fake_transformers(version)
        assert precision_kwarg("bfloat16") == {"torch_dtype": "bfloat16"}

    def test_the_boundary_matches_the_documented_release(self) -> None:
        """The constant is the release the rename actually shipped in."""
        assert DTYPE_KWARG_RENAMED_IN == (4, 56)

    def test_a_release_candidate_suffix_still_parses(
        self, fake_transformers: Any
    ) -> None:
        """``4.56.0.dev0`` and friends must not fall back to the old name."""
        fake_transformers("4.56.0.dev0")
        assert precision_kwarg("float32") == {"dtype": "float32"}
