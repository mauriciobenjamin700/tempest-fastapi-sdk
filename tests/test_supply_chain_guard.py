"""The supply-chain gates of #436 stay in the workflows that ship the SDK.

Four gaps were measured in ``.github/`` before #436: no advisory gate at
all, ``uv sync --all-extras`` without ``--locked`` in all four workflows,
``pypa/gh-action-pypi-publish@release/v1`` -- a moving branch -- in the job
that holds the PyPI OIDC token, and ``requires = ["hatchling"]`` resolving
the newest backend on every build.

Each fix is one token in a YAML or TOML file, and dropping any of them
breaks nothing visible: CI stays green without ``--locked``, the publish
step works on a branch ref, the build works with any hatchling. That is the
"violable in silence" shape, so each one is read back here. Every check is
also fed the exact text that shipped before #436 and must flag it.

Blind spot: the guard reads the workflow files, not GitHub's run of them --
a step disabled by an ``if:`` that never holds would still pass.
"""

from __future__ import annotations

import pathlib
import re
import tomllib

ROOT: pathlib.Path = pathlib.Path(__file__).resolve().parent.parent
WORKFLOWS: pathlib.Path = ROOT / ".github" / "workflows"

PINNED_ACTIONS: tuple[str, ...] = (
    "pypa/gh-action-pypi-publish",
    "astral-sh/setup-uv",
)
"""Actions that must be referenced by commit SHA, never by tag or branch."""

_SETUP_UV_SHA: str = "astral-sh/setup-uv@caf0cab7a618c569241d31dcd442f54681755d39"
"""The ``setup-uv`` reference pinned in the workflows (tag ``v3.2.4``)."""

_UV_SYNC: re.Pattern[str] = re.compile(r"\buv sync\b[^\n]*")
_USES: re.Pattern[str] = re.compile(
    r"uses:\s*(?P<action>[\w.-]+/[\w.-]+)@(?P<ref>\S+)(?P<rest>[^\n]*)"
)
_SHA: re.Pattern[str] = re.compile(r"^[0-9a-f]{40}$")
_TAG_COMMENT: re.Pattern[str] = re.compile(r"^\s*#\s*v\d+(\.\d+)*\s*$")


def unlocked_syncs(text: str) -> list[str]:
    """List the ``uv sync`` invocations that do not pass ``--locked``.

    YAML comment lines are skipped: ``ci.yml`` explains in a comment that
    "`uv sync` honours" ``.python-version``, which is prose, not a command.

    Args:
        text (str): A workflow file's content.

    Returns:
        list[str]: Each offending command line, stripped.
    """
    return [
        m.group(0).strip()
        for line in text.splitlines()
        if not line.lstrip().startswith("#")
        for m in _UV_SYNC.finditer(line)
        if "--locked" not in m.group(0)
    ]


def unpinned_actions(text: str) -> list[str]:
    """List uses of :data:`PINNED_ACTIONS` not pinned to a SHA with a tag comment.

    Args:
        text (str): A workflow file's content.

    Returns:
        list[str]: Each offending ``action@ref`` reference.
    """
    offending: list[str] = []
    for match in _USES.finditer(text):
        if match.group("action") not in PINNED_ACTIONS:
            continue
        sha_ok: bool = bool(_SHA.match(match.group("ref")))
        tag_ok: bool = bool(_TAG_COMMENT.match(match.group("rest")))
        if not (sha_ok and tag_ok):
            offending.append(f"{match.group('action')}@{match.group('ref')}")
    return offending


def missing_release_gates(text: str) -> list[str]:
    """List the gates the release workflow must run before ``uv build``.

    Args:
        text (str): The release workflow's content.

    Returns:
        list[str]: Each gate that is absent or comes after the build.
    """
    build: int = text.find("run: uv build")
    missing: list[str] = []
    for gate in ("run: uv lock --check", "run: make audit"):
        position: int = text.find(gate)
        if position == -1 or build == -1 or position > build:
            missing.append(gate)
    return missing


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


def _workflow_texts() -> dict[str, str]:
    """Read every workflow file.

    Returns:
        dict[str, str]: File name to content.
    """
    return {
        path.name: path.read_text(encoding="utf-8")
        for path in sorted(WORKFLOWS.glob("*.yml"))
    }


class TestWorkflows:
    """The committed workflows carry every gate."""

    def test_every_uv_sync_is_locked(self) -> None:
        """Every workflow installs the committed resolution."""
        offending: dict[str, list[str]] = {
            name: found
            for name, text in _workflow_texts().items()
            if (found := unlocked_syncs(text))
        }
        assert offending == {}

    def test_sensitive_actions_are_pinned_by_sha(self) -> None:
        """The publish and setup-uv actions are immutable references."""
        offending: dict[str, list[str]] = {
            name: found
            for name, text in _workflow_texts().items()
            if (found := unpinned_actions(text))
        }
        assert offending == {}

    def test_publish_action_is_present_and_pinned(self) -> None:
        """The pin check is not vacuous: the publish action is still used."""
        text: str = (WORKFLOWS / "release-pypi.yml").read_text(encoding="utf-8")
        assert "pypa/gh-action-pypi-publish@" in text

    def test_release_checks_lock_and_audits_before_building(self) -> None:
        """The release fails on a stale lock or a known advisory before building."""
        text: str = (WORKFLOWS / "release-pypi.yml").read_text(encoding="utf-8")
        assert missing_release_gates(text) == []

    def test_audit_workflow_runs_on_a_schedule(self) -> None:
        """A new advisory is noticed without anyone pushing."""
        text: str = (WORKFLOWS / "audit.yml").read_text(encoding="utf-8")
        assert "schedule:" in text
        assert "run: make audit" in text

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
    """Each check flags the exact text that shipped before #436."""

    def test_flags_unlocked_sync(self) -> None:
        """``ci.yml:55`` before #436."""
        assert unlocked_syncs("        run: uv sync --all-extras\n") == [
            "uv sync --all-extras"
        ]
        assert unlocked_syncs("        run: uv sync --all-extras --group docs\n") != []

    def test_skips_comment_lines(self) -> None:
        """A comment that names the command is not an invocation."""
        assert unlocked_syncs("      # `uv sync` honours it -- so every\n") == []

    def test_accepts_locked_sync(self) -> None:
        """The fixed form passes."""
        assert unlocked_syncs("        run: uv sync --locked --all-extras\n") == []

    def test_flags_branch_ref_on_publish(self) -> None:
        """``release-pypi.yml:122`` before #436."""
        text: str = "        uses: pypa/gh-action-pypi-publish@release/v1\n"
        assert unpinned_actions(text) == ["pypa/gh-action-pypi-publish@release/v1"]

    def test_flags_tag_ref_on_setup_uv(self) -> None:
        """``astral-sh/setup-uv@v3`` before #436."""
        assert unpinned_actions("        uses: astral-sh/setup-uv@v3\n") == [
            "astral-sh/setup-uv@v3"
        ]

    def test_flags_sha_without_tag_comment(self) -> None:
        """A bare SHA loses which release it is, so readers cannot tell."""
        text: str = f"        uses: {_SETUP_UV_SHA}\n"
        assert unpinned_actions(text) != []

    def test_accepts_sha_with_tag_comment(self) -> None:
        """The fixed form passes."""
        text: str = f"        uses: {_SETUP_UV_SHA} # v3.2.4\n"
        assert unpinned_actions(text) == []

    def test_flags_release_without_gates(self) -> None:
        """The release job before #436 built with neither gate."""
        text: str = (
            "      - run: uv sync --all-extras\n"
            "      - name: Build\n"
            "        run: uv build\n"
        )
        assert missing_release_gates(text) == [
            "run: uv lock --check",
            "run: make audit",
        ]

    def test_flags_gate_after_build(self) -> None:
        """A gate that runs after the build gates nothing."""
        text: str = (
            "        run: uv build\n"
            "        run: uv lock --check\n"
            "        run: make audit\n"
        )
        assert missing_release_gates(text) == [
            "run: uv lock --check",
            "run: make audit",
        ]

    def test_flags_unbounded_hatchling(self) -> None:
        """``pyproject.toml:337`` before #436."""
        assert not hatchling_is_capped(["hatchling"])
        assert not hatchling_is_capped(["hatchling>=1.32.4"])
        assert not hatchling_is_capped(["hatchling<=1.32.4"])

    def test_accepts_bounded_hatchling(self) -> None:
        """The fixed form passes."""
        assert hatchling_is_capped(["hatchling>=1.32.4,<2"])
