"""System metrics helpers — CPU, memory, disk and GPU usage."""

from __future__ import annotations

import asyncio
import contextlib
import logging
from dataclasses import asdict, dataclass, field
from typing import Any

try:
    import psutil
except ImportError as exc:  # pragma: no cover - guarded by extras
    raise ImportError(
        "MetricsUtils requires the [metrics] extra. "
        "Install with `pip install tempest-fastapi-sdk[metrics]`."
    ) from exc

logger = logging.getLogger(__name__)


@dataclass(slots=True)
class CPUMetrics:
    """CPU usage snapshot.

    Attributes:
        percent (float): Aggregate CPU utilization (0-100).
        cores_logical (int): Logical core count (including SMT).
        cores_physical (int): Physical core count.
        load_average (tuple[float, float, float] | None): 1/5/15-minute
            load averages on POSIX; ``None`` on Windows.
    """

    percent: float
    cores_logical: int
    cores_physical: int
    load_average: tuple[float, float, float] | None = None

    def to_dict(self) -> dict[str, Any]:
        """Return the metrics as a plain dict.

        Returns:
            dict[str, Any]: The dataclass fields.
        """
        return asdict(self)


@dataclass(slots=True)
class MemoryMetrics:
    """RAM usage snapshot.

    Attributes:
        total_bytes (int): Total physical memory.
        used_bytes (int): Memory actively in use.
        available_bytes (int): Memory available for new allocations.
        percent (float): Used percentage (0-100).
    """

    total_bytes: int
    used_bytes: int
    available_bytes: int
    percent: float

    def to_dict(self) -> dict[str, Any]:
        """Return the metrics as a plain dict.

        Returns:
            dict[str, Any]: The dataclass fields.
        """
        return asdict(self)


@dataclass(slots=True)
class DiskMetrics:
    """Disk usage snapshot for a single mount point.

    Attributes:
        path (str): The mount point inspected.
        total_bytes (int): Filesystem total capacity.
        used_bytes (int): Used bytes.
        free_bytes (int): Free bytes.
        percent (float): Used percentage (0-100).
    """

    path: str
    total_bytes: int
    used_bytes: int
    free_bytes: int
    percent: float

    def to_dict(self) -> dict[str, Any]:
        """Return the metrics as a plain dict.

        Returns:
            dict[str, Any]: The dataclass fields.
        """
        return asdict(self)


@dataclass(slots=True)
class GPUMetrics:
    """Single-GPU usage snapshot.

    Populated by :func:`pynvml` (NVIDIA only). Non-NVIDIA hosts get an
    empty list from :meth:`MetricsUtils.gpus`.

    Attributes:
        index (int): Device index (0-based).
        name (str): Device model name.
        memory_total_bytes (int): VRAM capacity.
        memory_used_bytes (int): VRAM in use.
        memory_free_bytes (int): VRAM free.
        utilization_percent (float): GPU utilization (0-100).
        temperature_celsius (float | None): Core temperature, when
            reported by the driver.
    """

    index: int
    name: str
    memory_total_bytes: int
    memory_used_bytes: int
    memory_free_bytes: int
    utilization_percent: float
    temperature_celsius: float | None = None

    def to_dict(self) -> dict[str, Any]:
        """Return the metrics as a plain dict.

        Returns:
            dict[str, Any]: The dataclass fields.
        """
        return asdict(self)


@dataclass(slots=True)
class SystemMetrics:
    """Full machine snapshot returned by :meth:`MetricsUtils.snapshot`.

    Attributes:
        cpu (CPUMetrics): CPU usage block.
        memory (MemoryMetrics): RAM usage block.
        disks (list[DiskMetrics]): One entry per inspected path.
        gpus (list[GPUMetrics]): One entry per detected NVIDIA GPU.
    """

    cpu: CPUMetrics
    memory: MemoryMetrics
    disks: list[DiskMetrics] = field(default_factory=list)
    gpus: list[GPUMetrics] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        """Return the snapshot as a nested dict ready for JSON.

        Returns:
            dict[str, Any]: ``cpu``/``memory``/``disks``/``gpus`` keys.
        """
        return {
            "cpu": self.cpu.to_dict(),
            "memory": self.memory.to_dict(),
            "disks": [d.to_dict() for d in self.disks],
            "gpus": [g.to_dict() for g in self.gpus],
        }


class MetricsUtils:
    """Aggregated CPU/RAM/disk/GPU readings for the current host.

    Built on top of :mod:`psutil` (always required by the ``[metrics]``
    extra) and ``pynvml`` (optional — NVIDIA GPU support degrades to
    an empty list when the library is missing or no NVIDIA device is
    present).

    Every method has a synchronous and an asynchronous variant. Sync
    methods call :mod:`psutil` directly (most calls are non-blocking
    or block briefly for sampling); async variants run the same code
    via :func:`asyncio.to_thread` so they never stall the event loop
    when a longer sampling ``interval`` is requested.

    Stateless — instantiation is unnecessary; every method is a
    classmethod.
    """

    @classmethod
    def cpu(cls, *, interval: float = 0.1) -> CPUMetrics:
        """Sample CPU usage.

        Args:
            interval (float): Sampling window in seconds. ``0`` returns
                the cumulative measure since the previous call (which
                is meaningless on first invocation). Defaults to a
                short blocking sample.

        Returns:
            CPUMetrics: The sampled metrics.
        """
        percent = float(psutil.cpu_percent(interval=interval))
        logical = psutil.cpu_count(logical=True) or 0
        physical = psutil.cpu_count(logical=False) or 0
        load: tuple[float, float, float] | None
        try:
            la = psutil.getloadavg()
            load = (float(la[0]), float(la[1]), float(la[2]))
        except (AttributeError, OSError):
            load = None
        return CPUMetrics(
            percent=percent,
            cores_logical=int(logical),
            cores_physical=int(physical),
            load_average=load,
        )

    @classmethod
    async def cpu_async(cls, *, interval: float = 0.1) -> CPUMetrics:
        """Asyncio-friendly wrapper around :meth:`cpu`.

        Args:
            interval (float): Sampling window in seconds.

        Returns:
            CPUMetrics: The sampled metrics.
        """
        return await asyncio.to_thread(cls.cpu, interval=interval)

    @classmethod
    def memory(cls) -> MemoryMetrics:
        """Sample RAM usage.

        Returns:
            MemoryMetrics: The current memory snapshot.
        """
        vm = psutil.virtual_memory()
        return MemoryMetrics(
            total_bytes=int(vm.total),
            used_bytes=int(vm.used),
            available_bytes=int(vm.available),
            percent=float(vm.percent),
        )

    @classmethod
    async def memory_async(cls) -> MemoryMetrics:
        """Asyncio-friendly wrapper around :meth:`memory`.

        Returns:
            MemoryMetrics: The current memory snapshot.
        """
        return await asyncio.to_thread(cls.memory)

    @classmethod
    def disk(cls, path: str = "/") -> DiskMetrics:
        """Sample disk usage for ``path``.

        Args:
            path (str): The filesystem path to inspect. Defaults to
                the root partition.

        Returns:
            DiskMetrics: The usage snapshot.

        Raises:
            FileNotFoundError: When ``path`` does not exist.
        """
        usage = psutil.disk_usage(path)
        return DiskMetrics(
            path=path,
            total_bytes=int(usage.total),
            used_bytes=int(usage.used),
            free_bytes=int(usage.free),
            percent=float(usage.percent),
        )

    @classmethod
    def disks(cls, paths: list[str] | None = None) -> list[DiskMetrics]:
        """Sample usage for multiple disks.

        Args:
            paths (list[str] | None): Paths to inspect. ``None``
                defaults to ``["/"]``.

        Returns:
            list[DiskMetrics]: One entry per resolvable path; paths
            that raise are logged and skipped.
        """
        targets = paths if paths is not None else ["/"]
        results: list[DiskMetrics] = []
        for path in targets:
            try:
                results.append(cls.disk(path))
            except (FileNotFoundError, PermissionError, OSError) as exc:
                logger.warning("Disk metrics for %r failed: %s", path, exc)
        return results

    @classmethod
    async def disks_async(
        cls,
        paths: list[str] | None = None,
    ) -> list[DiskMetrics]:
        """Asyncio-friendly wrapper around :meth:`disks`.

        Args:
            paths (list[str] | None): Paths to inspect.

        Returns:
            list[DiskMetrics]: The collected snapshots.
        """
        return await asyncio.to_thread(cls.disks, paths)

    @classmethod
    def gpus(cls) -> list[GPUMetrics]:
        """Sample NVIDIA GPU usage via ``pynvml`` when available.

        Returns an empty list (without raising) when:

        * ``pynvml`` is not installed,
        * the NVML library cannot be loaded (no NVIDIA driver, WSL
          without compute, etc.), or
        * no NVIDIA devices are present.

        Returns:
            list[GPUMetrics]: One entry per detected GPU.
        """
        try:
            import pynvml
        except ImportError:
            return []

        try:
            pynvml.nvmlInit()
        except Exception as exc:
            logger.debug("NVML init failed: %s", exc)
            return []

        gpus: list[GPUMetrics] = []
        try:
            count = pynvml.nvmlDeviceGetCount()
            for index in range(count):
                handle = pynvml.nvmlDeviceGetHandleByIndex(index)
                name_raw = pynvml.nvmlDeviceGetName(handle)
                name = (
                    name_raw.decode("utf-8")
                    if isinstance(name_raw, bytes)
                    else str(name_raw)
                )
                mem = pynvml.nvmlDeviceGetMemoryInfo(handle)
                util = pynvml.nvmlDeviceGetUtilizationRates(handle)
                temperature: float | None
                try:
                    temperature = float(
                        pynvml.nvmlDeviceGetTemperature(
                            handle, pynvml.NVML_TEMPERATURE_GPU
                        )
                    )
                except Exception:
                    temperature = None
                gpus.append(
                    GPUMetrics(
                        index=index,
                        name=name,
                        memory_total_bytes=int(mem.total),
                        memory_used_bytes=int(mem.used),
                        memory_free_bytes=int(mem.free),
                        utilization_percent=float(util.gpu),
                        temperature_celsius=temperature,
                    )
                )
        except Exception as exc:
            logger.warning("NVML enumeration failed: %s", exc)
        finally:
            with contextlib.suppress(Exception):
                pynvml.nvmlShutdown()
        return gpus

    @classmethod
    async def gpus_async(cls) -> list[GPUMetrics]:
        """Asyncio-friendly wrapper around :meth:`gpus`.

        Returns:
            list[GPUMetrics]: One entry per detected GPU.
        """
        return await asyncio.to_thread(cls.gpus)

    @classmethod
    def snapshot(
        cls,
        *,
        disk_paths: list[str] | None = None,
        cpu_interval: float = 0.1,
    ) -> SystemMetrics:
        """Build a full :class:`SystemMetrics` snapshot.

        Args:
            disk_paths (list[str] | None): Disks to inspect.
            cpu_interval (float): CPU sampling window.

        Returns:
            SystemMetrics: The combined snapshot.
        """
        return SystemMetrics(
            cpu=cls.cpu(interval=cpu_interval),
            memory=cls.memory(),
            disks=cls.disks(disk_paths),
            gpus=cls.gpus(),
        )

    @classmethod
    async def snapshot_async(
        cls,
        *,
        disk_paths: list[str] | None = None,
        cpu_interval: float = 0.1,
    ) -> SystemMetrics:
        """Asyncio-friendly wrapper around :meth:`snapshot`.

        Runs every sub-collector concurrently via :func:`asyncio.gather`
        so the wall-clock cost is bounded by the slowest sample
        (typically CPU).

        Args:
            disk_paths (list[str] | None): Disks to inspect.
            cpu_interval (float): CPU sampling window.

        Returns:
            SystemMetrics: The combined snapshot.
        """
        cpu, memory, disks, gpus = await asyncio.gather(
            cls.cpu_async(interval=cpu_interval),
            cls.memory_async(),
            cls.disks_async(disk_paths),
            cls.gpus_async(),
        )
        return SystemMetrics(cpu=cpu, memory=memory, disks=disks, gpus=gpus)


__all__: list[str] = [
    "CPUMetrics",
    "DiskMetrics",
    "GPUMetrics",
    "MemoryMetrics",
    "MetricsUtils",
    "SystemMetrics",
]
