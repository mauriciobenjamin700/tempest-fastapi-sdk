"""The supply-chain gates of #436 stay where the SDK is shipped from.

Four gaps were measured in ``.github/`` before #436: no advisory gate at
all, ``uv sync --all-extras`` without ``--locked`` in all four workflows,
``pypa/gh-action-pypi-publish@release/v1`` -- a moving branch -- in the job
that holds the PyPI OIDC token, and ``requires = ["hatchling"]`` resolving
the newest backend on every build.

The workflows are gone since 2026-11-09 (``tests/test_release_flow_guard.py``)
and the publish runs locally through ``scripts/publish.py``, so the two
workflow-only gaps (unlocked ``uv sync``, unpinned actions) have nothing left
to guard. What remains: the publish checks the lock and audits **before** it
builds, ``make audit`` reads the committed resolution, and the build backend
is bounded. Each fix is one token, dropping any of them breaks nothing
visible, and each check is also fed the shape that shipped before #436.

Blind spot: the guard reads the step list and the Makefile, not a run of
them. And with the daily ``audit.yml`` gone, a new advisory is noticed at the
next ``make release`` / ``make publish`` (or a manual ``make audit``), not the
next morning.
"""

from __future__ import annotations

import importlib.util
import pathlib
import re
import sys
import tomllib
from types import ModuleType

ROOT: pathlib.Path = pathlib.Path(__file__).resolve().parent.parent

RELEASE_GATES: tuple[str, ...] = ("lock-check", "audit")
"""Publish steps that must run before ``build``: a stale lock or a known
advisory fails the release before anything is built."""


def load_publish() -> ModuleType:
    """Import ``scripts/publish.py``, which is not a package.

    Returns:
        ModuleType: The loaded module, registered in ``sys.modules``.
    """
    name: str = "tempest_publish_script"
    if name in sys.modules:
        return sys.modules[name]
    spec = importlib.util.spec_from_file_location(name, ROOT / "scripts" / "publish.py")
    assert spec is not None and spec.loader is not None
    module: ModuleType = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


def missing_release_gates(steps: list[str]) -> list[str]:
    """List the gates the publish must run before ``build``.

    Args:
        steps (list[str]): The publish step names, in run order.

    Returns:
        list[str]: Each gate that is absent or comes after the build.
    """
    build: int = steps.index("build") if "build" in steps else -1
    return [
        gate
        for gate in RELEASE_GATES
        if gate not in steps or build == -1 or steps.index(gate) > build
    ]


def hatchling_is_capped(requires: list[str]) -> bool:
    """Tell whether the build backend requirement has both a floor and a cap.

    Args:
        requires (list[str]): ``[build-system].requires`` from ``pyproject.toml``.

    Returns:
        bool: ``True`` when the hatchling entry declares ``>=`` and ``<``.
    """
    for entry in requires:
        if entry.replace(" ", "").startswith("hatchling"):
            return ">=" in entry and re.search(r"<(?!=)", entry) is not None
    return False


class TestShippingGates:
    """The committed publish and Makefile carry every gate."""

    def test_publish_checks_lock_and_audits_before_building(self) -> None:
        """The release fails on a stale lock or a known advisory before building."""
        steps: list[str] = [name for name, _ in load_publish().STEPS]
        assert missing_release_gates(steps) == []

    def test_lock_check_is_uv_lock_check(self) -> None:
        """The ``lock-check`` step is ``uv lock --check``, not a re-lock."""
        source: str = (ROOT / "scripts" / "publish.py").read_text(encoding="utf-8")
        assert '["uv", "lock", "--check"]' in source

    def test_makefile_audit_reads_the_locked_resolution(self) -> None:
        """The audit exports from the committed lock, every extra included."""
        makefile: str = (ROOT / "Makefile").read_text(encoding="utf-8")
        assert re.search(r"uv export --locked\b[^\n]*--all-extras", makefile)
        assert "pip-audit@" in makefile

    def test_build_backend_has_a_range(self) -> None:
        """``hatchling`` is bounded on both sides."""
        pyproject: dict[str, object] = tomllib.loads(
            (ROOT / "pyproject.toml").read_text(encoding="utf-8")
        )
        build_system: object = pyproject["build-system"]
        assert isinstance(build_system, dict)
        assert hatchling_is_capped(list(build_system["requires"]))


class TestGuardFires:
    """Each check flags the exact shape that shipped before #436."""

    def test_flags_release_without_gates(self) -> None:
        """The release job before #436 built with neither gate."""
        steps: list[str] = ["head-is-tag", "versions-match", "build", "upload"]
        assert missing_release_gates(steps) == ["lock-check", "audit"]

    def test_flags_gate_after_build(self) -> None:
        """A gate that runs after the build gates nothing."""
        steps: list[str] = ["build", "lock-check", "audit", "upload"]
        assert missing_release_gates(steps) == ["lock-check", "audit"]

    def test_flags_unbounded_hatchling(self) -> None:
        """``pyproject.toml:337`` before #436."""
        assert not hatchling_is_capped(["hatchling"])
        assert not hatchling_is_capped(["hatchling>=1.32.4"])
        assert not hatchling_is_capped(["hatchling<=1.32.4"])

    def test_accepts_bounded_hatchling(self) -> None:
        """The fixed form passes."""
        assert hatchling_is_capped(["hatchling>=1.32.4,<2"])
