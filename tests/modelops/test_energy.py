"""Tests for the power and energy samplers."""

from __future__ import annotations

import builtins
import time
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

from tempest_fastapi_sdk.modelops import (
    EnergySource,
    NullPowerSampler,
    NvidiaSmiPowerSampler,
    NvmlPowerSampler,
    PowerSampler,
    RaplEnergySampler,
    resolve_cpu_energy_sampler,
    resolve_power_sampler,
)
from tempest_fastapi_sdk.modelops.energy import _integrate, _ThreadedPowerSampler


def _fake_nvml(
    *,
    energy_mj: list[int] | None = None,
    power_mw: int = 42_000,
    memory_used: int = 512 * 1024**2,
    init_fails: bool = False,
) -> SimpleNamespace:
    """Build a fake ``pynvml`` module exposing only what the sampler calls.

    Args:
        energy_mj (list[int] | None): Successive total-energy readings. When
            ``None`` the counter is reported as unsupported.
        power_mw (int): Instantaneous power the fake reports, in milliwatts.
        memory_used (int): Bytes of VRAM the fake reports as used.
        init_fails (bool): Make ``nvmlInit`` raise, as on a host with the
            library but no driver.

    Returns:
        SimpleNamespace: The fake module.
    """
    readings = list(energy_mj or [])

    def total_energy(_handle: Any) -> int:
        if not readings:
            raise RuntimeError("not supported")
        return readings.pop(0) if len(readings) > 1 else readings[0]

    def init() -> None:
        if init_fails:
            raise RuntimeError("no driver")

    return SimpleNamespace(
        nvmlInit=init,
        nvmlShutdown=lambda: None,
        nvmlDeviceGetHandleByIndex=lambda index: object(),
        nvmlDeviceGetPowerUsage=lambda handle: power_mw,
        nvmlDeviceGetMemoryInfo=lambda handle: SimpleNamespace(used=memory_used),
        nvmlDeviceGetTotalEnergyConsumption=total_energy,
    )


def _patch_import(
    monkeypatch: pytest.MonkeyPatch, name: str, module: Any | None
) -> None:
    """Make ``import <name>`` return ``module``, or fail when it is ``None``.

    Args:
        monkeypatch (pytest.MonkeyPatch): Patcher.
        name (str): Module name to intercept.
        module (Any | None): Replacement, or ``None`` to raise ImportError.
    """
    real_import = builtins.__import__

    def fake_import(target: str, *args: Any, **kwargs: Any) -> Any:
        if target == name:
            if module is None:
                raise ImportError(f"no module named {name}")
            return module
        return real_import(target, *args, **kwargs)

    monkeypatch.setattr(builtins, "__import__", fake_import)


def _wait_for_samples(
    sampler: _ThreadedPowerSampler, count: int, timeout: float = 5.0
) -> None:
    """Block until the background thread has collected ``count`` samples.

    Polling on the actual condition rather than sleeping a fixed amount
    keeps the timing tests deterministic on a loaded CI runner.

    Args:
        sampler (_ThreadedPowerSampler): Sampler being driven.
        count (int): Samples to wait for.
        timeout (float): Seconds before giving up.
    """
    deadline = time.perf_counter() + timeout
    while len(sampler._samples) < count and time.perf_counter() < deadline:
        time.sleep(0.005)


class _CountingSampler(_ThreadedPowerSampler):
    """Threaded sampler whose poll returns a fixed power and memory."""

    source: EnergySource = EnergySource.NVML_SAMPLING

    @property
    def available(self) -> bool:
        """Always available, so the thread actually runs.

        Returns:
            bool: ``True``.
        """
        return True

    def _poll(self) -> tuple[float, float | None] | None:
        """Return a constant 10 W and 100 MB.

        Returns:
            tuple[float, float | None] | None: ``(10.0, 100.0)``.
        """
        return 10.0, 100.0


class TestIntegrate:
    def test_needs_two_points(self) -> None:
        assert _integrate([]) is None
        assert _integrate([(0.0, 10.0)]) is None

    def test_trapezoid(self) -> None:
        assert _integrate([(0.0, 10.0), (2.0, 10.0)]) == pytest.approx(20.0)
        assert _integrate([(0.0, 0.0), (2.0, 10.0)]) == pytest.approx(10.0)


class TestNullSampler:
    def test_reports_nothing_and_never_raises(self) -> None:
        sampler = NullPowerSampler()
        sampler.start()
        sampler.stop()
        reading = sampler.reading()
        assert sampler.available is False
        assert reading.source == EnergySource.UNAVAILABLE
        assert reading.energy_j is None
        assert sampler.latest_power_w() is None
        assert sampler.peak_memory_mb() is None

    def test_satisfies_the_protocol(self) -> None:
        assert isinstance(NullPowerSampler(), PowerSampler)


class TestThreadedSampler:
    def test_integrates_sampled_power(self) -> None:
        sampler = _CountingSampler(interval_s=0.002)
        sampler.start()
        _wait_for_samples(sampler, 3)
        sampler.stop()
        reading = sampler.reading()
        assert reading.samples >= 2
        assert reading.source == EnergySource.NVML_SAMPLING
        assert reading.energy_j is not None
        assert reading.mean_power_w == pytest.approx(10.0)
        assert reading.peak_power_w == pytest.approx(10.0)
        assert sampler.peak_memory_mb() == pytest.approx(100.0)

    def test_stop_without_start_is_safe(self) -> None:
        sampler = _CountingSampler()
        sampler.stop()
        assert sampler.reading().source == EnergySource.UNAVAILABLE


class TestNvmlSampler:
    def test_unavailable_without_pynvml(self, monkeypatch: pytest.MonkeyPatch) -> None:
        _patch_import(monkeypatch, "pynvml", None)
        sampler = NvmlPowerSampler()
        assert sampler.available is False
        sampler.start()
        sampler.stop()
        assert sampler.reading().source == EnergySource.UNAVAILABLE

    def test_unavailable_when_the_driver_is_missing(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        _patch_import(monkeypatch, "pynvml", _fake_nvml(init_fails=True))
        assert NvmlPowerSampler().available is False

    def test_prefers_the_energy_counter(self, monkeypatch: pytest.MonkeyPatch) -> None:
        _patch_import(monkeypatch, "pynvml", _fake_nvml(energy_mj=[1_000, 3_500]))
        sampler = NvmlPowerSampler(interval_s=0.005)
        assert sampler.available is True
        sampler.start()
        sampler.stop()
        reading = sampler.reading()
        assert reading.source == EnergySource.NVML_COUNTER
        assert reading.energy_j == pytest.approx(2.5)

    def test_falls_back_to_sampling_without_the_counter(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        _patch_import(monkeypatch, "pynvml", _fake_nvml(energy_mj=None))
        sampler = NvmlPowerSampler(interval_s=0.002)
        sampler.start()
        _wait_for_samples(sampler, 3)
        sampler.stop()
        reading = sampler.reading()
        assert reading.source == EnergySource.NVML_SAMPLING
        assert reading.mean_power_w == pytest.approx(42.0)


class TestNvidiaSmiSampler:
    def test_unavailable_without_the_binary(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setattr(
            "tempest_fastapi_sdk.modelops.energy.shutil.which", lambda _name: None
        )
        sampler = NvidiaSmiPowerSampler()
        assert sampler.available is False
        sampler.start()
        sampler.stop()
        assert sampler.reading().source == EnergySource.UNAVAILABLE

    def test_parses_the_csv_line(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setattr(
            "tempest_fastapi_sdk.modelops.energy.shutil.which",
            lambda _name: "/usr/bin/nvidia-smi",
        )
        monkeypatch.setattr(
            "tempest_fastapi_sdk.modelops.energy.subprocess.check_output",
            lambda *args, **kwargs: b"47.35, 1024\n",
        )
        sampler = NvidiaSmiPowerSampler()
        assert sampler._poll() == (47.35, 1024.0)

    def test_skips_unsupported_readings(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setattr(
            "tempest_fastapi_sdk.modelops.energy.shutil.which",
            lambda _name: "/usr/bin/nvidia-smi",
        )
        monkeypatch.setattr(
            "tempest_fastapi_sdk.modelops.energy.subprocess.check_output",
            lambda *args, **kwargs: b"[Not Supported], 1024\n",
        )
        assert NvidiaSmiPowerSampler()._poll() is None


def _write_rapl_domain(
    root: Path, name: str, *, energy_uj: int, max_range: int = 1_000_000
) -> Path:
    """Create one fake powercap domain directory.

    Args:
        root (Path): Fake ``/sys/class/powercap`` root.
        name (str): Domain directory name.
        energy_uj (int): Initial counter value.
        max_range (int): Wrap point written to ``max_energy_range_uj``.

    Returns:
        Path: The created domain directory.
    """
    domain = root / name
    domain.mkdir(parents=True, exist_ok=True)
    (domain / "energy_uj").write_text(str(energy_uj))
    (domain / "max_energy_range_uj").write_text(str(max_range))
    return domain


class TestRaplSampler:
    def test_unavailable_when_powercap_is_absent(self, tmp_path: Path) -> None:
        sampler = RaplEnergySampler(root=str(tmp_path / "nope"))
        assert sampler.available is False
        sampler.start()
        sampler.stop()
        assert sampler.reading().source == EnergySource.UNAVAILABLE

    def test_sums_package_domains_only(self, tmp_path: Path) -> None:
        package = _write_rapl_domain(tmp_path, "intel-rapl:0", energy_uj=1_000)
        _write_rapl_domain(tmp_path, "intel-rapl:0:0", energy_uj=999_999)
        sampler = RaplEnergySampler(root=str(tmp_path))
        assert sampler.available is True
        sampler.start()
        (package / "energy_uj").write_text("2_500_001".replace("_", ""))
        sampler.stop()
        reading = sampler.reading()
        assert reading.source == EnergySource.RAPL
        assert reading.energy_j == pytest.approx(2.499001)
        assert reading.samples == 1

    def test_handles_counter_wraparound(self, tmp_path: Path) -> None:
        package = _write_rapl_domain(
            tmp_path, "intel-rapl:0", energy_uj=900_000, max_range=1_000_000
        )
        sampler = RaplEnergySampler(root=str(tmp_path))
        sampler.start()
        (package / "energy_uj").write_text("100000")
        sampler.stop()
        assert sampler.reading().energy_j == pytest.approx(0.2)

    def test_exposes_no_instantaneous_power(self, tmp_path: Path) -> None:
        _write_rapl_domain(tmp_path, "intel-rapl:0", energy_uj=1)
        sampler = RaplEnergySampler(root=str(tmp_path))
        assert sampler.latest_power_w() is None
        assert sampler.peak_memory_mb() is None


class TestResolvers:
    def test_gpu_resolver_degrades_to_null(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        _patch_import(monkeypatch, "pynvml", None)
        monkeypatch.setattr(
            "tempest_fastapi_sdk.modelops.energy.shutil.which", lambda _name: None
        )
        assert isinstance(resolve_power_sampler(), NullPowerSampler)

    def test_gpu_resolver_prefers_nvml(self, monkeypatch: pytest.MonkeyPatch) -> None:
        _patch_import(monkeypatch, "pynvml", _fake_nvml(energy_mj=[1]))
        assert isinstance(resolve_power_sampler(), NvmlPowerSampler)

    def test_gpu_resolver_falls_back_to_smi(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        _patch_import(monkeypatch, "pynvml", None)
        monkeypatch.setattr(
            "tempest_fastapi_sdk.modelops.energy.shutil.which",
            lambda _name: "/usr/bin/nvidia-smi",
        )
        assert isinstance(resolve_power_sampler(), NvidiaSmiPowerSampler)

    def test_cpu_resolver_returns_a_usable_sampler(self) -> None:
        sampler = resolve_cpu_energy_sampler()
        assert isinstance(sampler, RaplEnergySampler | NullPowerSampler)
