"""Tests for the GenAI hardware-capacity module (no torch needed)."""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

import tempest_fastapi_sdk.genai.hardware as hardware_module
from tempest_fastapi_sdk.genai import (
    GPUInfo,
    HardwareInfo,
    ModelDtype,
    auto_dtype_name,
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


QWEN_0_5B_PARAMS: int = 494_032_768
"""Parameter count of ``Qwen/Qwen2.5-0.5B-Instruct`` as loaded (tied embeddings)."""


def _cpu_with(ram_available_gb: float) -> HardwareInfo:
    """Build a CPU-only snapshot with ``ram_available_gb`` of free RAM.

    Args:
        ram_available_gb (float): Free RAM in GB (10**9 bytes).

    Returns:
        HardwareInfo: A host without CUDA or MPS.
    """
    return HardwareInfo(
        cpu_cores=4,
        ram_total_bytes=8 * 10**9,
        ram_available_bytes=int(ram_available_gb * 10**9),
    )


class TestCpuPlansTheLoadedPrecision:
    """On CPU the planner sizes the float32 weights ``TextGenerator`` loads.

    The shipped defect: ``can_run`` and ``recommend`` sized every unquantized
    load at ``bfloat16`` while ``TextGenerator(dtype="auto")`` loads
    ``float32`` on CPU. For Qwen2.5-0.5B the planner said ~1.2 GB fits in
    1.5 GB free; the measured resident growth of that load was ~2.1 GB.
    """

    def test_can_run_defaults_to_float32_on_cpu(self) -> None:
        report = can_run(num_params=QWEN_0_5B_PARAMS, hardware=_cpu_with(8.0))

        assert report.device == "cpu"
        assert report.dtype == ModelDtype.FLOAT32
        assert report.estimated_bytes == estimate_model_bytes(
            QWEN_0_5B_PARAMS, ModelDtype.FLOAT32
        )

    def test_can_run_defaults_to_bfloat16_on_cuda(self) -> None:
        report = can_run(num_params=QWEN_0_5B_PARAMS, hardware=_gpu(free_gb=24.0))

        assert report.device == "cuda"
        assert report.dtype == ModelDtype.BFLOAT16

    def test_explicit_dtype_is_still_honoured_on_cpu(self) -> None:
        report = can_run(
            num_params=QWEN_0_5B_PARAMS,
            dtype=ModelDtype.BFLOAT16,
            hardware=_cpu_with(8.0),
        )

        assert report.dtype == ModelDtype.BFLOAT16

    def test_recommend_does_not_promise_an_unquantized_cpu_load_that_cannot_fit(
        self,
    ) -> None:
        report = recommend(num_params=QWEN_0_5B_PARAMS, hardware=_cpu_with(1.5))

        assert report.fits is True
        assert report.dtype == ModelDtype.INT8

    def test_recommend_picks_float32_on_cpu_when_it_fits(self) -> None:
        report = recommend(num_params=QWEN_0_5B_PARAMS, hardware=_cpu_with(3.0))

        assert report.dtype == ModelDtype.FLOAT32
        assert report.fits is True

    def test_recommend_keeps_bfloat16_first_on_cuda(self) -> None:
        report = recommend(num_params=7_000_000_000, hardware=_gpu(free_gb=24.0))

        assert report.dtype == ModelDtype.BFLOAT16
        assert report.device == "cuda"

    def test_cpu_suggestion_never_proposes_bfloat16_as_a_quantization(self) -> None:
        report = can_run(num_params=QWEN_0_5B_PARAMS, hardware=_cpu_with(1.5))

        assert report.fits is False
        assert report.suggestion is not None
        assert "bfloat16" not in report.suggestion
        assert "int8" in report.suggestion
        assert "OllamaGenerator" in report.suggestion

    def test_cuda_quantization_suggestion_has_no_cpu_note(self) -> None:
        report = can_run(num_params=13_000_000_000, hardware=_gpu(free_gb=12.0))

        assert report.suggestion is not None
        assert report.suggestion.startswith("Quantize to int")
        assert "OllamaGenerator" not in report.suggestion

    def test_auto_dtype_name_agrees_with_the_planner(self) -> None:
        for hardware in (_cpu_with(8.0), _gpu(free_gb=24.0)):
            report = can_run(num_params=QWEN_0_5B_PARAMS, hardware=hardware)

            assert auto_dtype_name(report.device) == report.dtype


def _cpu_without_ram_reading() -> HardwareInfo:
    """Build the CPU-only snapshot ``probe_hardware`` returns without psutil.

    Returns:
        HardwareInfo: A host whose RAM was not measured.
    """
    return HardwareInfo(
        cpu_cores=12,
        ram_total_bytes=0,
        ram_available_bytes=0,
        ram_measured=False,
    )


def _gpu_without_ram_reading(free_gb: float) -> HardwareInfo:
    """Build a CUDA snapshot whose VRAM was read and whose RAM was not.

    Args:
        free_gb (float): Free VRAM in GB (10**9 bytes).

    Returns:
        HardwareInfo: A CUDA host without a RAM reading.
    """
    return _gpu(free_gb=free_gb).model_copy(
        update={
            "ram_total_bytes": 0,
            "ram_available_bytes": 0,
            "ram_measured": False,
        }
    )


@pytest.fixture(autouse=True)
def _no_cgroup_limit(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    """Hide the runner's own cgroup so a CI memory limit cannot clamp RAM.

    The cgroup clamp has its own suite in ``test_hardware_cgroup.py``.

    Args:
        monkeypatch (pytest.MonkeyPatch): The test's monkeypatch.
        tmp_path (Path): A directory whose ``no-cgroup`` child does not exist.
    """
    monkeypatch.setattr(
        hardware_module, "_PROC_SELF_CGROUP_PATH", str(tmp_path / "no-cgroup")
    )


def _hide_every_ram_source(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    """Make both RAM sources fail: no ``psutil`` and no ``/proc/meminfo``.

    Args:
        monkeypatch (pytest.MonkeyPatch): The test's monkeypatch.
        tmp_path (Path): A directory whose ``missing`` child does not exist.
    """
    monkeypatch.setitem(sys.modules, "psutil", None)
    monkeypatch.setattr(
        hardware_module, "_PROC_MEMINFO_PATH", str(tmp_path / "missing")
    )


class TestUnknownMemoryIsNotZero:
    """RAM that could not be read is "unknown", never "nothing free".

    The shipped defect (v0.303.1, clean venv with only the base package):
    ``probe_hardware()`` reported ``ram_available_bytes=0`` and
    ``recommend(num_params=0.5e9)`` answered ``fits=False`` at ``int4`` with
    "Model is too large for this host even quantized; use a smaller model or
    add memory." on a host with 62 GB of RAM.
    """

    def test_probe_marks_ram_unmeasured_without_any_source(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
    ) -> None:
        _hide_every_ram_source(monkeypatch, tmp_path)

        info = probe_hardware()

        assert info.ram_measured is False
        assert info.ram_total_bytes == 0
        assert info.ram_available_bytes == 0

    def test_probe_marks_ram_measured_with_psutil(self) -> None:
        pytest.importorskip("psutil")

        info = probe_hardware()

        assert info.ram_measured is True
        assert info.ram_total_bytes > 0

    def test_probe_marks_disk_unmeasured_when_the_query_fails(
        self, tmp_path: Path
    ) -> None:
        info = probe_hardware(cache_dir=str(tmp_path / "missing"))

        assert info.disk_measured is False
        assert info.disk_free_bytes == 0

    def test_probe_marks_disk_measured(self, tmp_path: Path) -> None:
        info = probe_hardware(cache_dir=str(tmp_path))

        assert info.disk_measured is True
        assert info.disk_free_bytes > 0

    def test_hand_built_snapshot_counts_as_measured(self) -> None:
        info = _cpu_only()

        assert info.ram_measured is True
        assert info.disk_measured is True

    def test_can_run_reports_unknown_instead_of_add_memory(self) -> None:
        report = can_run(num_params=500_000_000, hardware=_cpu_without_ram_reading())

        assert report.memory_measured is False
        assert report.fits is False
        assert report.dtype == ModelDtype.FLOAT32
        assert report.available_bytes == 0
        assert report.headroom_pct == 0.0
        assert "could not be measured" in report.reason
        assert report.suggestion is not None
        assert "psutil" in report.suggestion
        assert "[metrics]" in report.suggestion
        assert "add memory" not in report.suggestion
        assert "smaller model" not in report.suggestion

    def test_recommend_keeps_the_native_precision_when_ram_is_unknown(
        self,
    ) -> None:
        report = recommend(num_params=500_000_000, hardware=_cpu_without_ram_reading())

        assert report.memory_measured is False
        assert report.device == "cpu"
        assert report.dtype == ModelDtype.FLOAT32
        assert report.estimated_bytes == estimate_model_bytes(
            500_000_000, ModelDtype.FLOAT32
        )

    def test_mps_is_sized_from_ram_so_it_is_unknown_too(self) -> None:
        hardware = _cpu_without_ram_reading().model_copy(update={"has_mps": True})

        report = recommend(num_params=500_000_000, hardware=hardware)

        assert report.memory_measured is False
        assert report.device == "mps"
        assert report.dtype == ModelDtype.BFLOAT16

    def test_recommend_without_any_ram_source_end_to_end(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
    ) -> None:
        _hide_every_ram_source(monkeypatch, tmp_path)
        monkeypatch.setitem(sys.modules, "torch", None)

        report = recommend(num_params=500_000_000)

        assert report.memory_measured is False
        assert report.device == "cpu"
        assert report.dtype == ModelDtype.FLOAT32
        assert "add memory" not in (report.suggestion or "")

    def test_measured_report_keeps_memory_measured_true(self) -> None:
        report = can_run(num_params=500_000_000, hardware=_cpu_with(8.0))

        assert report.memory_measured is True
        assert report.fits is True

    def test_gpu_verdict_is_still_measured_when_only_ram_is_unknown(self) -> None:
        report = can_run(
            num_params=7_000_000_000, hardware=_gpu_without_ram_reading(24.0)
        )

        assert report.memory_measured is True
        assert report.device == "cuda"
        assert report.fits is True

    def test_gpu_overflow_does_not_call_the_host_too_small_when_ram_is_unknown(
        self,
    ) -> None:
        report = can_run(
            num_params=70_000_000_000,
            dtype=ModelDtype.INT4,
            hardware=_gpu_without_ram_reading(2.0),
        )

        assert report.memory_measured is True
        assert report.fits is False
        assert report.suggestion is not None
        assert "psutil" in report.suggestion
        assert "add memory" not in report.suggestion


MEMINFO_62GB: str = """MemTotal:       65850504 kB
MemFree:        40000000 kB
MemAvailable:   51899780 kB
Buffers:          300000 kB
Cached:          9000000 kB
"""
"""A ``/proc/meminfo`` excerpt shaped like the 62 GB WSL2 host's."""


def _meminfo(tmp_path: Path, content: str) -> str:
    """Write ``content`` as a fake ``/proc/meminfo`` and return its path.

    Args:
        tmp_path (Path): Where to write it.
        content (str): The file body.

    Returns:
        str: The file's path.
    """
    path = tmp_path / "meminfo"
    path.write_text(content, encoding="ascii")
    return str(path)


class TestProcMeminfoFallback:
    """Without psutil, Linux reads RAM from ``/proc/meminfo``.

    psutil's own Linux backend reads the same file, so a base install on
    Linux (WSL and containers included) needs no extra to size CPU loads.
    """

    def test_reads_total_and_available_in_bytes(self, tmp_path: Path) -> None:
        reading = hardware_module._read_proc_meminfo(_meminfo(tmp_path, MEMINFO_62GB))

        assert reading == (65850504 * 1024, 51899780 * 1024)

    def test_missing_file_is_not_measured(self, tmp_path: Path) -> None:
        assert hardware_module._read_proc_meminfo(str(tmp_path / "missing")) is None

    def test_old_kernel_without_memavailable_is_not_measured(
        self, tmp_path: Path
    ) -> None:
        content = "MemTotal: 8000000 kB\nMemFree: 1000000 kB\nCached: 5000000 kB\n"

        assert hardware_module._read_proc_meminfo(_meminfo(tmp_path, content)) is None

    def test_zero_memavailable_is_not_measured(self, tmp_path: Path) -> None:
        content = "MemTotal: 8000000 kB\nMemFree: 1000000 kB\nMemAvailable: 0 kB\n"

        assert hardware_module._read_proc_meminfo(_meminfo(tmp_path, content)) is None

    def test_available_above_total_falls_back_to_memfree(self, tmp_path: Path) -> None:
        content = "MemTotal: 1000 kB\nMemFree: 300 kB\nMemAvailable: 5000 kB\n"

        reading = hardware_module._read_proc_meminfo(_meminfo(tmp_path, content))

        assert reading == (1000 * 1024, 300 * 1024)

    def test_probe_uses_proc_on_linux_without_psutil(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
    ) -> None:
        monkeypatch.setitem(sys.modules, "psutil", None)
        monkeypatch.setattr(sys, "platform", "linux")
        monkeypatch.setattr(
            hardware_module, "_PROC_MEMINFO_PATH", _meminfo(tmp_path, MEMINFO_62GB)
        )

        info = probe_hardware()

        assert info.ram_measured is True
        assert info.ram_total_bytes == 65850504 * 1024
        assert info.ram_available_bytes == 51899780 * 1024

    def test_recommend_fits_at_float32_from_proc_alone(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
    ) -> None:
        monkeypatch.setitem(sys.modules, "psutil", None)
        monkeypatch.setitem(sys.modules, "torch", None)
        monkeypatch.setattr(sys, "platform", "linux")
        monkeypatch.setattr(
            hardware_module, "_PROC_MEMINFO_PATH", _meminfo(tmp_path, MEMINFO_62GB)
        )

        report = recommend(num_params=500_000_000)

        assert report.memory_measured is True
        assert report.fits is True
        assert report.device == "cpu"
        assert report.dtype == ModelDtype.FLOAT32

    @pytest.mark.parametrize("platform", ["darwin", "win32"])
    def test_other_platforms_need_psutil(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path, platform: str
    ) -> None:
        monkeypatch.setitem(sys.modules, "psutil", None)
        monkeypatch.setitem(sys.modules, "torch", None)
        monkeypatch.setattr(sys, "platform", platform)
        monkeypatch.setattr(
            hardware_module, "_PROC_MEMINFO_PATH", _meminfo(tmp_path, MEMINFO_62GB)
        )

        info = probe_hardware()
        report = recommend(num_params=500_000_000)

        assert info.ram_measured is False
        assert report.memory_measured is False
        assert report.suggestion is not None
        assert f"on {platform} measuring RAM requires psutil" in report.suggestion
        assert "[metrics]" in report.suggestion

    def test_linux_message_names_both_failed_sources(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
    ) -> None:
        _hide_every_ram_source(monkeypatch, tmp_path)
        monkeypatch.setattr(sys, "platform", "linux")

        report = can_run(num_params=500_000_000, hardware=_cpu_without_ram_reading())

        assert report.suggestion is not None
        assert "could not be read" in report.suggestion
        assert "[metrics]" in report.suggestion

    def test_agrees_with_psutil(self) -> None:
        psutil = pytest.importorskip("psutil")
        if not sys.platform.startswith("linux"):
            pytest.skip("/proc/meminfo is Linux-only")

        reading = hardware_module._read_proc_meminfo(hardware_module._PROC_MEMINFO_PATH)
        mem = psutil.virtual_memory()

        assert reading is not None
        total, available = reading
        assert total == mem.total
        assert abs(available - mem.available) <= 0.05 * mem.total
