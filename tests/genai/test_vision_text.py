"""Tests for VisionTextGenerator — image normalization + config (no torch)."""

from __future__ import annotations

import io

import pytest

from tempest_fastapi_sdk.genai import HardwareInfo, ModelDtype, VisionTextGenerator
from tempest_fastapi_sdk.genai.vision_text import _load_image


def _cpu_hw() -> HardwareInfo:
    return HardwareInfo(
        cpu_cores=4, ram_total_bytes=8 * 10**9, ram_available_bytes=6 * 10**9
    )


def _png_bytes() -> bytes:
    from PIL import Image

    buffer = io.BytesIO()
    Image.new("RGB", (4, 4), (255, 0, 0)).save(buffer, format="PNG")
    return buffer.getvalue()


class TestLoadImage:
    def test_passes_through_pil_image(self) -> None:
        from PIL import Image

        img = Image.new("RGB", (2, 2))
        assert _load_image(img) is img

    def test_loads_from_bytes(self) -> None:
        from PIL import Image

        img = _load_image(_png_bytes())
        assert isinstance(img, Image.Image)
        assert img.size == (4, 4)

    def test_loads_from_path(self, tmp_path: object) -> None:
        from PIL import Image

        path = tmp_path / "x.png"  # type: ignore[attr-defined]
        Image.new("RGB", (3, 3)).save(path)
        assert _load_image(str(path)).size == (3, 3)

    def test_loads_from_ndarray(self) -> None:
        np = pytest.importorskip("numpy")
        from PIL import Image

        array = np.zeros((5, 5, 3), dtype=np.uint8)
        img = _load_image(array)
        assert isinstance(img, Image.Image)
        assert img.size == (5, 5)

    def test_unsupported_type_raises(self) -> None:
        with pytest.raises(TypeError, match="unsupported image source"):
            _load_image(1234)


class TestInit:
    def test_auto_dtype_on_cpu(self) -> None:
        gen = VisionTextGenerator("m", hardware=_cpu_hw())
        assert gen.device == "cpu"
        assert gen.dtype == ModelDtype.FLOAT32
        assert gen.is_loaded is False

    def test_unload_when_not_loaded_is_noop(self) -> None:
        gen = VisionTextGenerator("m", hardware=_cpu_hw())
        gen.unload()
        assert gen.is_loaded is False

    def test_unload_if_idle_without_threshold(self) -> None:
        gen = VisionTextGenerator("m", hardware=_cpu_hw())
        assert gen.unload_if_idle() is False
