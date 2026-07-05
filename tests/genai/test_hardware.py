"""Tests for the GenAI hardware-capacity module (no torch needed)."""

from __future__ import annotations

import pytest

from tempest_fastapi_sdk.genai import (
    GPUInfo,
    HardwareInfo,
    ModelDtype,
    bytes_per_param,
    can_run,
    estimate_model_bytes,
    probe_hardware,
    recommend,
)


class TestEstimates:
    def test_bytes_per_param(self) -> None:
        assert bytes_per_param(ModelDtype.FLOAT32) == 4.0
        assert bytes_per_param(ModelDtype.BFLOAT16) == 2.0
        assert bytes_per_param(ModelDtype.INT8) == 1.0

    def test_estimate_scales_with_overhead(self) -> None:
        # 1B params at fp16 = 2 GB * 1.25 overhead = 2.5 GB
        assert estimate_model_bytes(1_000_000_000, ModelDtype.FLOAT16) == int(
            1_000_000_000 * 2.0 * 1.25
        )

    def test_int4_smaller_than_bf16(self) -> None:
        n = 7_000_000_000
        assert estimate_model_bytes(n, ModelDtype.INT4) < estimate_model_bytes(
            n, ModelDtype.BFLOAT16
        )

    def test_zero_params_raises(self) -> None:
        with pytest.raises(ValueError):
            estimate_model_bytes(0)


def _gpu(free_gb: float, total_gb: float = 24.0) -> HardwareInfo:
    return HardwareInfo(
        cpu_cores=8,
        ram_total_bytes=32 * 10**9,
        ram_available_bytes=16 * 10**9,
        has_cuda=True,
        gpus=[
            GPUInfo(
                index=0,
                name="Test GPU",
                vram_total_bytes=int(total_gb * 10**9),
                vram_free_bytes=int(free_gb * 10**9),
            )
        ],
    )


def _cpu_only() -> HardwareInfo:
    return HardwareInfo(
        cpu_cores=4,
        ram_total_bytes=8 * 10**9,
        ram_available_bytes=6 * 10**9,
    )


class TestCanRun:
    def test_fits_on_gpu(self) -> None:
        report = can_run(
            num_params=7_000_000_000,
            dtype=ModelDtype.BFLOAT16,
            hardware=_gpu(free_gb=24.0),
        )
        assert report.fits is True
        assert report.device == "cuda"
        assert report.suggestion is None
        assert report.headroom_pct > 0

    def test_does_not_fit_suggests_quantization(self) -> None:
        # 13B bf16 ~32.5GB won't fit 12GB free; int8/int4 might.
        report = can_run(
            num_params=13_000_000_000,
            dtype=ModelDtype.BFLOAT16,
            hardware=_gpu(free_gb=12.0),
        )
        assert report.fits is False
        assert report.suggestion is not None
        assert "int" in report.suggestion.lower()

    def test_auto_device_falls_back_to_cpu(self) -> None:
        report = can_run(
            num_params=1_000_000_000,
            dtype=ModelDtype.INT4,
            hardware=_cpu_only(),
        )
        assert report.device == "cpu"

    def test_requires_size(self) -> None:
        with pytest.raises(ValueError):
            can_run(hardware=_cpu_only())


class TestRecommend:
    def test_picks_precision_that_fits(self) -> None:
        # 7B: bf16 ~17.5GB won't fit 10GB; int8 ~8.75GB fits.
        report = recommend(num_params=7_000_000_000, hardware=_gpu(free_gb=10.0))
        assert report.fits is True
        assert report.dtype in (ModelDtype.INT8, ModelDtype.INT4)


class TestProbe:
    def test_probe_returns_info_without_torch(self) -> None:
        info = probe_hardware()
        assert isinstance(info, HardwareInfo)
        assert info.cpu_cores >= 1
        # torch not installed in the test env -> no CUDA reported
        assert isinstance(info.has_cuda, bool)
