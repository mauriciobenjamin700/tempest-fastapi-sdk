"""Energy and power measurement for model benchmarks.

Four samplers, one :class:`PowerSampler` protocol, so the benchmark loop
never branches on the host it runs on:

* :class:`NvmlPowerSampler` — NVIDIA GPU through ``pynvml``. Prefers the
  driver's monotonic total-energy counter and falls back to integrating
  power samples on older cards.
* :class:`NvidiaSmiPowerSampler` — same GPU numbers by polling the
  ``nvidia-smi`` binary, for hosts with the driver but without ``pynvml``.
* :class:`RaplEnergySampler` — CPU package energy from the Linux powercap
  interface. This is the only sampler here that measures the CPU.
* :class:`NullPowerSampler` — measures nothing and says so. Every sampler
  degrades into this behaviour rather than raising, so a benchmark written
  on a workstation still runs in CI.

**No sampler here reports wall-plug power.** A GPU reading excludes the CPU,
RAM, PSU losses and cooling; a RAPL reading covers the CPU package only.
Report the :class:`~tempest_fastapi_sdk.modelops.EnergySource` alongside any
number you publish.
"""

from __future__ import annotations

import contextlib
import logging
import shutil
import subprocess
import threading
import time
from itertools import pairwise
from pathlib import Path
from typing import Any, Protocol, runtime_checkable

from tempest_fastapi_sdk.modelops.schemas import EnergyReading, EnergySource

logger = logging.getLogger(__name__)

RAPL_ROOT: str = "/sys/class/powercap"
"""Linux powercap sysfs root holding the RAPL energy counters."""

DEFAULT_SAMPLE_INTERVAL_S: float = 0.02
"""Default polling interval: fast enough for ~10 ms kernels, cheap enough
that the sampler thread does not distort the measurement."""


@runtime_checkable
class PowerSampler(Protocol):
    """Something that can measure energy over a window.

    The benchmark calls :meth:`start` before the timed repetitions,
    :meth:`stop` after them, and :meth:`reading` once to collect the result.
    :meth:`latest_power_w` is polled per repetition to annotate raw samples.
    """

    @property
    def available(self) -> bool:
        """Whether this sampler can actually measure on this host."""
        ...

    def start(self) -> None:
        """Begin measuring. Must be safe to call when unavailable."""
        ...

    def stop(self) -> None:
        """Stop measuring. Must be safe to call without a prior start."""
        ...

    def reading(self) -> EnergyReading:
        """Return the energy measured between start and stop."""
        ...

    def latest_power_w(self) -> float | None:
        """Return the most recent power sample, or ``None``."""
        ...

    def peak_memory_mb(self) -> float | None:
        """Return the peak device memory seen, or ``None``."""
        ...


def _integrate(samples: list[tuple[float, float]]) -> float | None:
    """Integrate power over time with the trapezoidal rule.

    Args:
        samples (list[tuple[float, float]]): ``(timestamp_s, power_w)``
            pairs in chronological order.

    Returns:
        float | None: Energy in Joules, or ``None`` when fewer than two
        samples are available (a single instant has no width to integrate).
    """
    if len(samples) < 2:
        return None
    energy = 0.0
    for (t0, p0), (t1, p1) in pairwise(samples):
        energy += (p0 + p1) / 2.0 * (t1 - t0)
    return energy


class NullPowerSampler:
    """Sampler for hosts with nothing to measure.

    Every method is a no-op and :meth:`reading` reports
    :attr:`~tempest_fastapi_sdk.modelops.EnergySource.UNAVAILABLE` with all
    energy fields set to ``None``.
    """

    @property
    def available(self) -> bool:
        """Always ``False``.

        Returns:
            bool: ``False``.
        """
        return False

    def start(self) -> None:
        """Do nothing."""

    def stop(self) -> None:
        """Do nothing."""

    def reading(self) -> EnergyReading:
        """Return an empty reading.

        Returns:
            EnergyReading: Source ``UNAVAILABLE``, every field ``None``.
        """
        return EnergyReading(source=EnergySource.UNAVAILABLE)

    def latest_power_w(self) -> float | None:
        """Return ``None``.

        Returns:
            float | None: Always ``None``.
        """
        return None

    def peak_memory_mb(self) -> float | None:
        """Return ``None``.

        Returns:
            float | None: Always ``None``.
        """
        return None


class _ThreadedPowerSampler:
    """Shared machinery for samplers that poll power on a background thread.

    Subclasses implement :meth:`_poll` (one reading) and :attr:`available`.
    The thread appends ``(timestamp_s, power_w, memory_mb)`` tuples until
    :meth:`stop` sets the stop event.
    """

    source: EnergySource = EnergySource.UNAVAILABLE

    def __init__(
        self,
        *,
        device_index: int = 0,
        interval_s: float = DEFAULT_SAMPLE_INTERVAL_S,
    ) -> None:
        """Initialize the sampler.

        Args:
            device_index (int): Zero-based GPU index to poll.
            interval_s (float): Polling interval in seconds.
        """
        self.device_index: int = device_index
        self.interval_s: float = interval_s
        self._samples: list[tuple[float, float, float | None]] = []
        self._stop_event: threading.Event = threading.Event()
        self._thread: threading.Thread | None = None
        self._t_start: float = 0.0
        self._t_stop: float = 0.0

    @property
    def available(self) -> bool:
        """Whether the sampler can measure on this host.

        Returns:
            bool: ``False`` in the base class; subclasses override.
        """
        return False

    def start(self) -> None:
        """Reset the buffer and spawn the polling thread.

        No-op when the sampler is unavailable, so callers never branch.
        """
        if not self.available:
            return
        self._samples = []
        self._stop_event.clear()
        self._t_start = time.perf_counter()
        self._thread = threading.Thread(
            target=self._run,
            name=f"{type(self).__name__}-{self.device_index}",
            daemon=True,
        )
        self._thread.start()

    def stop(self) -> None:
        """Signal the polling thread and wait for it to exit."""
        if self._thread is None:
            return
        self._t_stop = time.perf_counter()
        self._stop_event.set()
        self._thread.join(timeout=2.0)
        self._thread = None

    def latest_power_w(self) -> float | None:
        """Return the most recent power sample.

        Returns:
            float | None: Watts, or ``None`` when nothing was sampled yet.
        """
        if not self._samples:
            return None
        return self._samples[-1][1]

    def peak_memory_mb(self) -> float | None:
        """Return the highest device-memory sample.

        Returns:
            float | None: MB, or ``None`` when memory was never reported.
        """
        values = [mem for _, _, mem in self._samples if mem is not None]
        if not values:
            return None
        return max(values)

    def reading(self) -> EnergyReading:
        """Integrate the collected samples into an energy reading.

        Returns:
            EnergyReading: Energy over the window, mean and peak power, and
            the sample count. Falls back to ``UNAVAILABLE`` when the thread
            never produced two samples to integrate between.
        """
        duration = max(self._t_stop - self._t_start, 0.0)
        power_series = [(ts, power) for ts, power, _ in self._samples]
        energy = _integrate(power_series)
        if energy is None:
            return EnergyReading(
                source=EnergySource.UNAVAILABLE,
                duration_s=duration,
                samples=len(self._samples),
                device_index=self.device_index,
            )
        powers = [power for _, power in power_series]
        return EnergyReading(
            source=self.source,
            duration_s=duration,
            energy_j=energy,
            mean_power_w=sum(powers) / len(powers),
            peak_power_w=max(powers),
            samples=len(powers),
            device_index=self.device_index,
        )

    def _run(self) -> None:
        """Poll until stopped, appending every successful reading."""
        while not self._stop_event.is_set():
            sample = self._poll()
            if sample is not None:
                power, memory = sample
                self._samples.append((time.perf_counter(), power, memory))
            self._stop_event.wait(self.interval_s)

    def _poll(self) -> tuple[float, float | None] | None:
        """Take one reading.

        Returns:
            tuple[float, float | None] | None: ``(power_w, memory_mb)``, or
            ``None`` when the reading failed and should be skipped.
        """
        raise NotImplementedError


class NvmlPowerSampler(_ThreadedPowerSampler):
    """GPU energy through NVML (``pynvml``).

    Two measurement paths, picked automatically:

    * **Total-energy counter** — ``nvmlDeviceGetTotalEnergyConsumption``
      returns millijoules accumulated since the driver loaded. Taking the
      delta across the window gives energy the driver itself integrated, so
      short kernels that a sampler would miss are still counted. Volta and
      newer.
    * **Sampled power** — on older cards the counter is unsupported, so the
      background thread integrates ``nvmlDeviceGetPowerUsage`` instead.

    The polling thread runs either way: it is what supplies per-repetition
    instantaneous power and GPU-memory peaks.

    Example:

        >>> sampler = NvmlPowerSampler(device_index=0)
        >>> sampler.start()
        >>> run_inference()
        >>> sampler.stop()
        >>> sampler.reading().energy_j
    """

    source: EnergySource = EnergySource.NVML_SAMPLING

    def __init__(
        self,
        *,
        device_index: int = 0,
        interval_s: float = DEFAULT_SAMPLE_INTERVAL_S,
    ) -> None:
        """Initialize the sampler and probe NVML.

        Probing happens once, here, so :attr:`available` is a cheap property
        the benchmark can consult before committing to a code path.

        Args:
            device_index (int): Zero-based GPU index to measure.
            interval_s (float): Polling interval in seconds.
        """
        super().__init__(device_index=device_index, interval_s=interval_s)
        self._pynvml: Any = None
        self._handle: Any = None
        self._energy_start_mj: int | None = None
        self._energy_end_mj: int | None = None
        self._probe()

    @property
    def available(self) -> bool:
        """Whether NVML resolved a handle for the requested device.

        Returns:
            bool: ``True`` when the device can be queried.
        """
        return self._handle is not None

    def start(self) -> None:
        """Snapshot the energy counter and start the polling thread."""
        self._energy_start_mj = self._read_energy_mj()
        self._energy_end_mj = None
        super().start()

    def stop(self) -> None:
        """Stop polling and snapshot the energy counter again."""
        super().stop()
        self._energy_end_mj = self._read_energy_mj()

    def reading(self) -> EnergyReading:
        """Prefer the driver's energy counter, fall back to integration.

        Returns:
            EnergyReading: Source ``NVML_COUNTER`` when the counter delta is
            usable, ``NVML_SAMPLING`` when the integrated samples are, and
            ``UNAVAILABLE`` when neither produced a figure.
        """
        sampled = super().reading()
        start_mj = self._energy_start_mj
        end_mj = self._energy_end_mj
        if start_mj is None or end_mj is None or end_mj < start_mj:
            return sampled
        duration = sampled.duration_s
        energy_j = (end_mj - start_mj) / 1000.0
        mean_power = energy_j / duration if duration > 0 else None
        return sampled.model_copy(
            update={
                "source": EnergySource.NVML_COUNTER,
                "energy_j": energy_j,
                "mean_power_w": mean_power or sampled.mean_power_w,
            }
        )

    def _probe(self) -> None:
        """Initialize NVML and resolve the device handle.

        Failure is expected and silent: no ``pynvml``, no driver, WSL
        without compute, or a device index that does not exist all leave
        :attr:`available` ``False``.
        """
        try:
            import pynvml
        except ImportError:
            return
        try:
            pynvml.nvmlInit()
            self._handle = pynvml.nvmlDeviceGetHandleByIndex(self.device_index)
            self._pynvml = pynvml
        except Exception as exc:
            logger.debug("NVML unavailable for power sampling: %s", exc)
            self._handle = None
            with contextlib.suppress(Exception):
                pynvml.nvmlShutdown()

    def _read_energy_mj(self) -> int | None:
        """Read the monotonic total-energy counter.

        Returns:
            int | None: Millijoules since driver load, or ``None`` when the
            device does not implement the counter.
        """
        if self._handle is None:
            return None
        try:
            return int(self._pynvml.nvmlDeviceGetTotalEnergyConsumption(self._handle))
        except Exception:
            return None

    def _poll(self) -> tuple[float, float | None] | None:
        """Read instantaneous power and memory usage.

        Returns:
            tuple[float, float | None] | None: ``(watts, memory_mb)``, or
            ``None`` when the query failed.
        """
        if self._handle is None:
            return None
        try:
            milliwatts = self._pynvml.nvmlDeviceGetPowerUsage(self._handle)
        except Exception:
            return None
        memory_mb: float | None
        try:
            mem = self._pynvml.nvmlDeviceGetMemoryInfo(self._handle)
            memory_mb = float(mem.used) / 1024.0**2
        except Exception:
            memory_mb = None
        return float(milliwatts) / 1000.0, memory_mb


class NvidiaSmiPowerSampler(_ThreadedPowerSampler):
    """GPU energy by polling the ``nvidia-smi`` binary.

    The fallback for hosts that have the NVIDIA driver but not ``pynvml``.
    Each poll spawns a process, so the interval defaults to a slower 50 ms —
    below that the sampler starts competing with the model for CPU.
    """

    source: EnergySource = EnergySource.NVIDIA_SMI

    def __init__(
        self,
        *,
        device_index: int = 0,
        interval_s: float = 0.05,
    ) -> None:
        """Initialize the sampler and look for ``nvidia-smi`` on PATH.

        Args:
            device_index (int): Zero-based GPU index to query.
            interval_s (float): Polling interval in seconds.
        """
        super().__init__(device_index=device_index, interval_s=interval_s)
        self._binary: str | None = shutil.which("nvidia-smi")

    @property
    def available(self) -> bool:
        """Whether ``nvidia-smi`` is on PATH.

        Returns:
            bool: ``True`` when the binary was found.
        """
        return self._binary is not None

    def _poll(self) -> tuple[float, float | None] | None:
        """Query power and memory for the configured device.

        Returns:
            tuple[float, float | None] | None: ``(watts, memory_mb)``, or
            ``None`` when the call failed or the driver reported ``N/A``.
        """
        if self._binary is None:
            return None
        try:
            raw = subprocess.check_output(
                [
                    self._binary,
                    f"--id={self.device_index}",
                    "--query-gpu=power.draw,memory.used",
                    "--format=csv,noheader,nounits",
                ],
                stderr=subprocess.DEVNULL,
                timeout=2.0,
            )
        except (OSError, subprocess.SubprocessError):
            return None
        line = raw.decode(errors="replace").strip().splitlines()
        if not line:
            return None
        parts = [part.strip() for part in line[0].split(",")]
        if len(parts) < 2:
            return None
        try:
            power = float(parts[0])
        except ValueError:
            return None
        memory_mb: float | None
        try:
            memory_mb = float(parts[1])
        except ValueError:
            memory_mb = None
        return power, memory_mb


class RaplEnergySampler:
    """CPU package energy from the Linux powercap (RAPL) counters.

    Reads ``energy_uj`` for every ``intel-rapl:<n>`` package domain (the AMD
    driver exposes the same interface), sums the deltas across the window and
    handles counter wraparound with ``max_energy_range_uj``.

    Two things commonly make this unavailable, both silently:

    * **Permissions.** Since CVE-2020-8694 most distributions ship
      ``energy_uj`` as ``0400 root``, because a high-resolution energy trace
      leaks information about what the CPU is doing. Reading it as a normal
      user then fails.
    * **Virtualization.** WSL2, most containers and most cloud VMs do not
      expose powercap at all.

    Example:

        >>> sampler = RaplEnergySampler()
        >>> sampler.start()
        >>> run_inference()
        >>> sampler.stop()
        >>> sampler.reading().source
    """

    def __init__(self, *, root: str = RAPL_ROOT) -> None:
        """Discover readable package domains under ``root``.

        Args:
            root (str): Powercap sysfs root. Overridable for testing.
        """
        self.root: str = root
        self._domains: list[Path] = self._discover()
        self._start_uj: dict[str, int] = {}
        self._total_uj: float = 0.0
        self._t_start: float = 0.0
        self._t_stop: float = 0.0

    @property
    def available(self) -> bool:
        """Whether at least one readable package domain was found.

        Returns:
            bool: ``True`` when energy can be read.
        """
        return bool(self._domains)

    def start(self) -> None:
        """Snapshot every domain counter."""
        if not self.available:
            return
        self._t_start = time.perf_counter()
        self._total_uj = 0.0
        self._start_uj = {}
        for domain in self._domains:
            value = self._read_uj(domain)
            if value is not None:
                self._start_uj[str(domain)] = value

    def stop(self) -> None:
        """Read every counter again and accumulate the deltas."""
        if not self.available or not self._start_uj:
            return
        self._t_stop = time.perf_counter()
        total = 0.0
        for domain in self._domains:
            before = self._start_uj.get(str(domain))
            after = self._read_uj(domain)
            if before is None or after is None:
                continue
            delta = after - before
            if delta < 0:
                delta += self._max_range_uj(domain)
            total += float(delta)
        self._total_uj = total

    def reading(self) -> EnergyReading:
        """Return the accumulated CPU package energy.

        Returns:
            EnergyReading: Source ``RAPL`` with energy and mean power, or
            ``UNAVAILABLE`` when no domain could be read.
        """
        duration = max(self._t_stop - self._t_start, 0.0)
        if not self.available or not self._start_uj:
            return EnergyReading(
                source=EnergySource.UNAVAILABLE,
                duration_s=duration,
            )
        energy_j = self._total_uj / 1_000_000.0
        mean_power = energy_j / duration if duration > 0 else None
        return EnergyReading(
            source=EnergySource.RAPL,
            duration_s=duration,
            energy_j=energy_j,
            mean_power_w=mean_power,
            samples=len(self._start_uj),
        )

    def latest_power_w(self) -> float | None:
        """Return ``None``: RAPL exposes energy, not instantaneous power.

        Returns:
            float | None: Always ``None``.
        """
        return None

    def peak_memory_mb(self) -> float | None:
        """Return ``None``: RAPL says nothing about memory.

        Returns:
            float | None: Always ``None``.
        """
        return None

    def _discover(self) -> list[Path]:
        """Find readable ``intel-rapl:<n>`` package domains.

        Sub-domains (``intel-rapl:0:0`` for cores, ``:1`` for the iGPU) are
        skipped on purpose: they are already included in their parent
        package, so summing both would double-count.

        Returns:
            list[Path]: Domain directories whose ``energy_uj`` is readable.
        """
        root = Path(self.root)
        if not root.is_dir():
            return []
        domains: list[Path] = []
        for entry in sorted(root.iterdir()):
            name = entry.name
            if not name.startswith("intel-rapl:"):
                continue
            if name.count(":") != 1:
                continue
            if self._read_uj(entry) is None:
                continue
            domains.append(entry)
        return domains

    def _read_uj(self, domain: Path) -> int | None:
        """Read ``energy_uj`` from one domain.

        Args:
            domain (Path): Domain directory.

        Returns:
            int | None: Microjoules, or ``None`` when unreadable.
        """
        try:
            return int((domain / "energy_uj").read_text().strip())
        except (OSError, ValueError):
            return None

    def _max_range_uj(self, domain: Path) -> int:
        """Read the counter wrap point for one domain.

        Args:
            domain (Path): Domain directory.

        Returns:
            int: ``max_energy_range_uj``, or ``0`` when unreadable — which
            makes a wrapped delta contribute nothing instead of a negative.
        """
        try:
            return int((domain / "max_energy_range_uj").read_text().strip())
        except (OSError, ValueError):
            return 0


def resolve_power_sampler(
    *,
    device_index: int = 0,
    interval_s: float = DEFAULT_SAMPLE_INTERVAL_S,
) -> PowerSampler:
    """Return the best available GPU power sampler for this host.

    Order of preference: NVML (accurate, cheap), ``nvidia-smi`` (works
    without ``pynvml``), then :class:`NullPowerSampler`.

    Args:
        device_index (int): Zero-based GPU index to measure.
        interval_s (float): Polling interval in seconds.

    Returns:
        PowerSampler: A sampler that is either available or a no-op.

    Example:

        >>> sampler = resolve_power_sampler()
        >>> sampler.available
        False
    """
    nvml = NvmlPowerSampler(device_index=device_index, interval_s=interval_s)
    if nvml.available:
        return nvml
    smi = NvidiaSmiPowerSampler(device_index=device_index)
    if smi.available:
        return smi
    return NullPowerSampler()


def resolve_cpu_energy_sampler() -> PowerSampler:
    """Return a CPU energy sampler, or a no-op when RAPL is unreachable.

    Returns:
        PowerSampler: :class:`RaplEnergySampler` when readable, otherwise
        :class:`NullPowerSampler`.

    Example:

        >>> resolve_cpu_energy_sampler().available
        False
    """
    rapl = RaplEnergySampler()
    if rapl.available:
        return rapl
    return NullPowerSampler()


__all__: list[str] = [
    "DEFAULT_SAMPLE_INTERVAL_S",
    "RAPL_ROOT",
    "NullPowerSampler",
    "NvidiaSmiPowerSampler",
    "NvmlPowerSampler",
    "PowerSampler",
    "RaplEnergySampler",
    "resolve_cpu_energy_sampler",
    "resolve_power_sampler",
]
