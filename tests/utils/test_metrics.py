"""Tests for tempest_fastapi_sdk.utils.MetricsUtils."""

from __future__ import annotations

from types import SimpleNamespace
from typing import Any

import pytest

from tempest_fastapi_sdk import (
    CPUMetrics,
    DiskMetrics,
    GPUMetrics,
    MemoryMetrics,
    MetricsUtils,
    SystemMetrics,
)
from tempest_fastapi_sdk.utils import metrics as metrics_module


class TestCPU:
    def test_cpu_sample_returns_metrics(self) -> None:
        result = MetricsUtils.cpu(interval=0)
        assert isinstance(result, CPUMetrics)
        assert 0.0 <= result.percent <= 100.0
        assert result.cores_logical >= 1
        assert result.cores_physical >= 1

    async def test_cpu_async_matches_sync(self) -> None:
        result = await MetricsUtils.cpu_async(interval=0)
        assert isinstance(result, CPUMetrics)


class TestMemory:
    def test_memory_sample_populates_fields(self) -> None:
        result = MetricsUtils.memory()
        assert isinstance(result, MemoryMetrics)
        assert result.total_bytes > 0
        assert 0.0 <= result.percent <= 100.0
        assert result.available_bytes <= result.total_bytes

    async def test_memory_async(self) -> None:
        result = await MetricsUtils.memory_async()
        assert isinstance(result, MemoryMetrics)


class TestDisk:
    def test_disk_root_returns_metrics(self) -> None:
        result = MetricsUtils.disk("/")
        assert isinstance(result, DiskMetrics)
        assert result.path == "/"
        assert result.total_bytes > 0

    def test_disks_handles_invalid_path(self) -> None:
        results = MetricsUtils.disks(["/", "/nonexistent-xyz-path-zzz"])
        assert any(d.path == "/" for d in results)
        assert all(d.path != "/nonexistent-xyz-path-zzz" for d in results)

    async def test_disks_async(self) -> None:
        results = await MetricsUtils.disks_async()
        assert isinstance(results, list)

    async def test_disk_async_returns_metrics_for_one_path(self) -> None:
        result = await MetricsUtils.disk_async("/")
        assert isinstance(result, DiskMetrics)
        assert result.path == "/"
        assert result.total_bytes > 0

    async def test_disk_async_propagates_a_bad_path(self) -> None:
        """The whole reason it exists: `disks_async` swallows this.

        A metrics endpoint reading one disk had only `disks_async([path])`,
        which logs and skips — so a mount that vanished answers `200` with
        the disk block missing, indistinguishable from a disk nobody asked
        for.
        """
        with pytest.raises((FileNotFoundError, OSError)):
            await MetricsUtils.disk_async("/nonexistent-xyz-path-zzz")

    def test_disks_strict_raises_instead_of_shortening_the_list(self) -> None:
        with pytest.raises((FileNotFoundError, OSError)):
            MetricsUtils.disks(["/", "/nonexistent-xyz-path-zzz"], strict=True)

    async def test_disks_async_strict_raises(self) -> None:
        with pytest.raises((FileNotFoundError, OSError)):
            await MetricsUtils.disks_async(
                ["/nonexistent-xyz-path-zzz"],
                strict=True,
            )


class TestGPUFallbacks:
    def test_gpus_returns_empty_without_pynvml(
        self,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        import builtins

        real_import = builtins.__import__

        def fake_import(name: str, *args: Any, **kwargs: Any) -> Any:
            if name == "pynvml":
                raise ImportError("nope")
            return real_import(name, *args, **kwargs)

        monkeypatch.setattr(builtins, "__import__", fake_import)
        assert MetricsUtils.gpus() == []

    def test_gpus_returns_empty_when_init_fails(
        self,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        fake_pynvml = SimpleNamespace(
            nvmlInit=lambda: (_ for _ in ()).throw(RuntimeError("no driver")),
            nvmlShutdown=lambda: None,
        )
        import builtins

        real_import = builtins.__import__

        def fake_import(name: str, *args: Any, **kwargs: Any) -> Any:
            if name == "pynvml":
                return fake_pynvml
            return real_import(name, *args, **kwargs)

        monkeypatch.setattr(builtins, "__import__", fake_import)
        assert MetricsUtils.gpus() == []

    def test_gpus_enumerates_devices(
        self,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        device_handle = object()

        fake_pynvml = SimpleNamespace(
            NVML_TEMPERATURE_GPU=0,
            nvmlInit=lambda: None,
            nvmlShutdown=lambda: None,
            nvmlDeviceGetCount=lambda: 1,
            nvmlDeviceGetHandleByIndex=lambda i: device_handle,
            nvmlDeviceGetName=lambda h: b"FakeGPU 9000",
            nvmlDeviceGetMemoryInfo=lambda h: SimpleNamespace(
                total=16 * 1024**3,
                used=4 * 1024**3,
                free=12 * 1024**3,
            ),
            nvmlDeviceGetUtilizationRates=lambda h: SimpleNamespace(gpu=42),
            nvmlDeviceGetTemperature=lambda h, kind: 65,
        )

        import builtins

        real_import = builtins.__import__

        def fake_import(name: str, *args: Any, **kwargs: Any) -> Any:
            if name == "pynvml":
                return fake_pynvml
            return real_import(name, *args, **kwargs)

        monkeypatch.setattr(builtins, "__import__", fake_import)
        gpus = MetricsUtils.gpus()
        assert len(gpus) == 1
        gpu = gpus[0]
        assert isinstance(gpu, GPUMetrics)
        assert gpu.name == "FakeGPU 9000"
        assert gpu.memory_total_bytes == 16 * 1024**3
        assert gpu.utilization_percent == 42.0
        assert gpu.temperature_celsius == 65.0


class TestSnapshot:
    def test_snapshot_assembles_everything(self) -> None:
        snap = MetricsUtils.snapshot(cpu_interval=0)
        assert isinstance(snap, SystemMetrics)
        assert isinstance(snap.cpu, CPUMetrics)
        assert isinstance(snap.memory, MemoryMetrics)
        assert isinstance(snap.disks, list)
        assert isinstance(snap.gpus, list)

    async def test_snapshot_async(self) -> None:
        snap = await MetricsUtils.snapshot_async(cpu_interval=0)
        assert isinstance(snap, SystemMetrics)

    def test_to_dict_round_trip(self) -> None:
        snap = MetricsUtils.snapshot(cpu_interval=0)
        payload = snap.to_dict()
        assert "cpu" in payload
        assert "memory" in payload
        assert "disks" in payload
        assert "gpus" in payload
        assert isinstance(payload["cpu"]["percent"], float)


def test_module_imports_psutil() -> None:
    assert metrics_module._psutil is not None
