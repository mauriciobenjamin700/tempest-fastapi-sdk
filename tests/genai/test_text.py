"""Tests for TextGenerator — pure logic + state (no torch in CI)."""

from __future__ import annotations

import importlib.util

import pytest

from tempest_fastapi_sdk.genai import (
    HardwareInfo,
    ModelDtype,
    TextGenerator,
    auto_dtype_name,
    resolve_device,
)
from tempest_fastapi_sdk.genai.schemas import GPUInfo


def _gpu_hw() -> HardwareInfo:
    return HardwareInfo(
        cpu_cores=8,
        ram_total_bytes=32 * 10**9,
        ram_available_bytes=16 * 10**9,
        has_cuda=True,
        gpus=[
            GPUInfo(
                index=0,
                name="T",
                vram_total_bytes=24 * 10**9,
                vram_free_bytes=24 * 10**9,
            )
        ],
    )


def _cpu_hw() -> HardwareInfo:
    return HardwareInfo(
        cpu_cores=4, ram_total_bytes=8 * 10**9, ram_available_bytes=6 * 10**9
    )


class TestResolvers:
    def test_resolve_fixed_device_passthrough(self) -> None:
        assert resolve_device("cpu", _gpu_hw()) == "cpu"

    def test_resolve_auto_prefers_cuda(self) -> None:
        assert resolve_device("auto", _gpu_hw()) == "cuda"

    def test_resolve_auto_falls_back_to_cpu(self) -> None:
        assert resolve_device("auto", _cpu_hw()) == "cpu"

    def test_auto_dtype(self) -> None:
        assert auto_dtype_name("cuda") == "bfloat16"
        assert auto_dtype_name("cpu") == "float32"


class TestInit:
    def test_auto_dtype_on_gpu(self) -> None:
        gen = TextGenerator("m", hardware=_gpu_hw())
        assert gen.device == "cuda"
        assert gen.dtype == ModelDtype.BFLOAT16

    def test_auto_dtype_on_cpu(self) -> None:
        gen = TextGenerator("m", hardware=_cpu_hw())
        assert gen.device == "cpu"
        assert gen.dtype == ModelDtype.FLOAT32

    def test_quantization_accepted(self) -> None:
        gen = TextGenerator("m", quantization="int4", hardware=_gpu_hw())
        assert gen.quantization == ModelDtype.INT4

    def test_bad_quantization_rejected(self) -> None:
        with pytest.raises(ValueError, match="quantization"):
            TextGenerator("m", quantization="float16", hardware=_cpu_hw())

    def test_not_loaded_initially(self) -> None:
        assert TextGenerator("m", hardware=_cpu_hw()).is_loaded is False


class TestState:
    def test_unload_when_not_loaded_is_noop(self) -> None:
        gen = TextGenerator("m", hardware=_cpu_hw())
        gen.unload()  # no raise
        assert gen.is_loaded is False

    def test_unload_if_idle_without_threshold(self) -> None:
        gen = TextGenerator("m", hardware=_cpu_hw())
        assert gen.unload_if_idle() is False

    def test_unload_if_idle_not_loaded(self) -> None:
        gen = TextGenerator("m", idle_unload_seconds=0.0, hardware=_cpu_hw())
        # not loaded -> nothing to unload
        assert gen.unload_if_idle() is False

    def test_seconds_idle_advances(self) -> None:
        import time

        gen = TextGenerator("m", hardware=_cpu_hw())
        gen._last_used = time.monotonic() - 5
        assert gen.seconds_idle >= 5


class TestWithoutExtra:
    @pytest.mark.skipif(
        importlib.util.find_spec("transformers") is not None,
        reason="transformers installed; the missing-extra path can't be exercised",
    )
    async def test_generate_without_transformers_raises(self) -> None:
        gen = TextGenerator("m", hardware=_cpu_hw())
        # transformers not installed -> helpful ImportError
        with pytest.raises(ImportError, match=r"\[genai\]"):
            await gen.generate("hi")
