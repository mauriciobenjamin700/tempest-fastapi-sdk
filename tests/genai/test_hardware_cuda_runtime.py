"""``probe_hardware`` warns about a CUDA 13 ``torch`` next to ``ctranslate2``.

faster-whisper's CTranslate2 wheels link against CUDA 12 and look up
``libcublas.so.12``; the PyPI ``torch`` is a CUDA 13 build since 2.14, so the
pair fails on the first GPU transcription (#723). ``torch`` and the
``ctranslate2`` lookup are replaced by stand-ins, so the test runs the same
on a box without a GPU and without either package.
"""

from __future__ import annotations

import importlib.machinery
import importlib.util
import sys
import types
from collections.abc import Callable

import pytest

from tempest_fastapi_sdk.genai import probe_hardware


class FakeCuda:
    """``torch.cuda`` reporting one device, with or without CUDA."""

    def __init__(self, *, available: bool) -> None:
        """Initialize the stand-in.

        Args:
            available (bool): What ``is_available()`` answers.
        """
        self.available = available

    def is_available(self) -> bool:
        """Report whether CUDA is usable.

        Returns:
            bool: The configured answer.
        """
        return self.available

    def device_count(self) -> int:
        """Return the device count.

        Returns:
            int: One device.
        """
        return 1

    def mem_get_info(self, index: int) -> tuple[int, int]:
        """Return free and total VRAM.

        Args:
            index (int): Device index.

        Returns:
            tuple[int, int]: ``(free, total)``.
        """
        return (1, 2)

    def get_device_name(self, index: int) -> str:
        """Return the device name.

        Args:
            index (int): Device index.

        Returns:
            str: A name.
        """
        return "Test GPU"


def _fake_torch(
    cuda_version: str | None, *, available: bool = True
) -> types.ModuleType:
    """Build a ``torch`` module whose ``version.cuda`` is ``cuda_version``.

    Args:
        cuda_version (str | None): ``torch.version.cuda``; ``None`` is a CPU
            build.
        available (bool): Whether CUDA is available.

    Returns:
        types.ModuleType: The module.
    """
    module = types.ModuleType("torch")
    module.cuda = FakeCuda(available=available)  # type: ignore[attr-defined]
    module.backends = types.SimpleNamespace(mps=None)  # type: ignore[attr-defined]
    module.version = types.SimpleNamespace(cuda=cuda_version)  # type: ignore[attr-defined]
    return module


def _install(
    monkeypatch: pytest.MonkeyPatch,
    torch_module: types.ModuleType,
    *,
    ctranslate2_installed: bool,
) -> list[str]:
    """Install the ``torch`` stand-in and fake the ``ctranslate2`` lookup.

    Args:
        monkeypatch (pytest.MonkeyPatch): Patcher.
        torch_module (types.ModuleType): The ``torch`` stand-in.
        ctranslate2_installed (bool): Whether ``find_spec("ctranslate2")``
            finds it.

    Returns:
        list[str]: Every name passed to ``importlib.util.find_spec``, so a
        test can assert that ``ctranslate2`` was looked up, not imported.
    """
    lookups: list[str] = []
    real_find_spec: Callable[..., importlib.machinery.ModuleSpec | None] = (
        importlib.util.find_spec
    )

    def _find_spec(
        name: str, package: str | None = None
    ) -> importlib.machinery.ModuleSpec | None:
        """Record the lookup and answer for ``ctranslate2`` from the fixture.

        Args:
            name (str): Module name.
            package (str | None): Anchor package for a relative name.

        Returns:
            importlib.machinery.ModuleSpec | None: A spec without a loader
            for an installed ``ctranslate2``; the real lookup otherwise.
        """
        lookups.append(name)
        if name == "ctranslate2":
            if not ctranslate2_installed:
                return None
            return importlib.machinery.ModuleSpec("ctranslate2", None)
        return real_find_spec(name, package)

    monkeypatch.setitem(sys.modules, "torch", torch_module)
    monkeypatch.setitem(sys.modules, "pynvml", None)
    monkeypatch.delitem(sys.modules, "ctranslate2", raising=False)
    monkeypatch.setattr(importlib.util, "find_spec", _find_spec)
    return lookups


def test_cuda_13_torch_with_ctranslate2_warns(monkeypatch: pytest.MonkeyPatch) -> None:
    lookups = _install(monkeypatch, _fake_torch("13.2"), ctranslate2_installed=True)

    info = probe_hardware()

    assert len(info.warnings) == 1
    warning = info.warnings[0]
    assert "CUDA 13.2 build" in warning
    assert "libcublas.so.12" in warning
    assert "https://download.pytorch.org/whl/cu126" in warning
    assert "ctranslate2" in lookups
    assert "ctranslate2" not in sys.modules


def test_cuda_12_torch_does_not_warn(monkeypatch: pytest.MonkeyPatch) -> None:
    _install(monkeypatch, _fake_torch("12.6"), ctranslate2_installed=True)

    assert probe_hardware().warnings == []


def test_without_ctranslate2_does_not_warn(monkeypatch: pytest.MonkeyPatch) -> None:
    _install(monkeypatch, _fake_torch("13.0"), ctranslate2_installed=False)

    assert probe_hardware().warnings == []


def test_cpu_torch_build_does_not_warn(monkeypatch: pytest.MonkeyPatch) -> None:
    _install(monkeypatch, _fake_torch(None), ctranslate2_installed=True)

    assert probe_hardware().warnings == []


def test_without_cuda_available_does_not_warn(monkeypatch: pytest.MonkeyPatch) -> None:
    """A CUDA 13 build on a host with no GPU transcribes on the CPU, which works."""
    _install(
        monkeypatch,
        _fake_torch("13.2", available=False),
        ctranslate2_installed=True,
    )

    assert probe_hardware().warnings == []


def test_without_torch_does_not_warn(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setitem(sys.modules, "torch", None)

    assert probe_hardware().warnings == []
