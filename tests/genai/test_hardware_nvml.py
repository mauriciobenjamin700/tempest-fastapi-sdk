"""The hardware probe reads GPU memory through NVML, not a CUDA context.

``torch.cuda.mem_get_info`` initializes CUDA in the calling process —
measured at +209 MiB of VRAM per GPU — and ``probe_hardware`` is what the
``/models`` endpoint and the admin card call from the web process. Both
``torch`` and ``pynvml`` are replaced by stand-ins here, so the test runs
the same on a CI box without a GPU.
"""

from __future__ import annotations

import sys
import types
from typing import Any

import pytest

from tempest_fastapi_sdk.genai import probe_hardware


class FakeCuda:
    """``torch.cuda`` that fails the test if CUDA would be initialized."""

    def __init__(self, count: int) -> None:
        """Initialize the stand-in.

        Args:
            count (int): Devices ``torch`` reports.
        """
        self.count = count
        self.initialized = False

    def is_available(self) -> bool:
        """Report CUDA as present (no context is created by this call).

        Returns:
            bool: ``True``.
        """
        return True

    def device_count(self) -> int:
        """Return the device count.

        Returns:
            int: The configured count.
        """
        return self.count

    def mem_get_info(self, index: int) -> tuple[int, int]:
        """Record that CUDA was initialized.

        Args:
            index (int): Device index.

        Returns:
            tuple[int, int]: ``(free, total)``.
        """
        self.initialized = True
        return (1, 2)

    def get_device_name(self, index: int) -> str:
        """Record that CUDA was initialized.

        Args:
            index (int): Device index.

        Returns:
            str: A name.
        """
        self.initialized = True
        return "torch-name"


class FakeMemory:
    """An NVML memory struct."""

    total: int = 16 * 2**30
    free: int = 15 * 2**30


def _fake_torch(cuda: FakeCuda) -> types.ModuleType:
    """Build a ``torch`` module with ``cuda`` and no MPS.

    Args:
        cuda (FakeCuda): The CUDA stand-in.

    Returns:
        types.ModuleType: The module.
    """
    module = types.ModuleType("torch")
    module.cuda = cuda  # type: ignore[attr-defined]
    module.backends = types.SimpleNamespace(mps=None)  # type: ignore[attr-defined]
    return module


def _fake_pynvml(count: int, *, fail_init: bool = False) -> types.ModuleType:
    """Build a ``pynvml`` module reporting ``count`` devices.

    Args:
        count (int): Devices NVML reports.
        fail_init (bool): Make ``nvmlInit`` raise, as without a driver.

    Returns:
        types.ModuleType: The module.
    """
    module = types.ModuleType("pynvml")

    def _init() -> None:
        if fail_init:
            raise RuntimeError("NVML Shared Library Not Found")

    module.nvmlInit = _init  # type: ignore[attr-defined]
    module.nvmlShutdown = lambda: None  # type: ignore[attr-defined]
    module.nvmlDeviceGetCount = lambda: count  # type: ignore[attr-defined]
    module.nvmlDeviceGetHandleByIndex = lambda index: index  # type: ignore[attr-defined]
    module.nvmlDeviceGetName = lambda handle: b"NVML GPU"  # type: ignore[attr-defined]
    module.nvmlDeviceGetMemoryInfo = lambda handle: FakeMemory()  # type: ignore[attr-defined]
    return module


def _install(
    monkeypatch: pytest.MonkeyPatch,
    cuda: FakeCuda,
    nvml: Any,
) -> None:
    """Put the stand-ins in ``sys.modules`` for the lazy imports.

    Args:
        monkeypatch (pytest.MonkeyPatch): Patcher.
        cuda (FakeCuda): The CUDA stand-in.
        nvml (Any): The ``pynvml`` stand-in, or ``None`` for "not installed".
    """
    monkeypatch.setitem(sys.modules, "torch", _fake_torch(cuda))
    monkeypatch.setitem(sys.modules, "pynvml", nvml)
    monkeypatch.delenv("CUDA_VISIBLE_DEVICES", raising=False)


def test_nvml_answers_without_initializing_cuda(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """With NVML present the probe never calls into CUDA's runtime."""
    cuda = FakeCuda(count=1)
    _install(monkeypatch, cuda, _fake_pynvml(1))

    info = probe_hardware()

    assert info.has_cuda
    assert cuda.initialized is False
    assert info.gpus[0].name == "NVML GPU"
    assert info.gpus[0].vram_free_bytes == FakeMemory.free


def test_falls_back_to_torch_without_pynvml(monkeypatch: pytest.MonkeyPatch) -> None:
    cuda = FakeCuda(count=1)
    _install(monkeypatch, cuda, None)

    info = probe_hardware()

    assert cuda.initialized is True
    assert info.gpus[0].name == "torch-name"


def test_falls_back_when_nvml_cannot_start(monkeypatch: pytest.MonkeyPatch) -> None:
    cuda = FakeCuda(count=1)
    _install(monkeypatch, cuda, _fake_pynvml(1, fail_init=True))

    assert probe_hardware().gpus[0].name == "torch-name"


def test_falls_back_when_the_counts_disagree(monkeypatch: pytest.MonkeyPatch) -> None:
    """NVML indices are only trusted when they cannot differ from CUDA's."""
    cuda = FakeCuda(count=1)
    _install(monkeypatch, cuda, _fake_pynvml(2))

    assert probe_hardware().gpus[0].name == "torch-name"


def test_falls_back_under_cuda_visible_devices(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """``CUDA_VISIBLE_DEVICES`` can reorder devices NVML still lists by PCI."""
    cuda = FakeCuda(count=1)
    _install(monkeypatch, cuda, _fake_pynvml(1))
    monkeypatch.setenv("CUDA_VISIBLE_DEVICES", "1")

    assert probe_hardware().gpus[0].name == "torch-name"
