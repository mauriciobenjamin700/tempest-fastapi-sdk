"""``scripts/release_gate.py`` skips the suite only when CI already ran it.

The gate lets ``make release`` and the tag workflow publish in minutes by
reusing a green CI run instead of re-running the suite. Its failure mode is
silent and expensive: a gate that says "covered" too eagerly ships code no
suite ever ran. So the tests here pin the refusals -- a dependency bump in
``uv.lock``, a docs change, a pending or red check -- as hard as the
acceptance.
"""

from __future__ import annotations

import pathlib
import sys
from collections.abc import Callable

import pytest

REPO_ROOT: pathlib.Path = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT / "scripts"))

import release_gate  # noqa: E402

LOCK_OLD: str = (
    '[[package]]\nname = "anyio"\nversion = "4.9.0"\n\n'
    '[[package]]\nname = "tempest-fastapi-sdk"\nversion = "0.308.0"\n'
)
"""A two-package ``uv.lock`` excerpt at the old version."""


def _reader(files: dict[tuple[str, str], str]) -> Callable[[str, str], str]:
    """Build a ``(rev, path) -> content`` reader over a fixed table.

    Args:
        files (dict[tuple[str, str], str]): Content per ``(rev, path)``.

    Returns:
        Callable[[str, str], str]: The reader.
    """
    return lambda rev, path: files[(rev, path)]


def _green_run(name: str) -> dict[str, object]:
    """Build one successful check run.

    Args:
        name (str): The check name.

    Returns:
        dict[str, object]: A completed, successful run.
    """
    return {"name": name, "status": "completed", "conclusion": "success"}


class TestOnlyVersionChanged:
    """A release bump is recognised; anything else is not."""

    def test_bump_of_the_three_version_strings_is_equivalent(self) -> None:
        """pyproject, ``__version__`` and our own lock entry may move."""
        files: dict[tuple[str, str], str] = {
            ("a", "pyproject.toml"): 'name = "x"\nversion = "0.308.0"\n',
            ("b", "pyproject.toml"): 'name = "x"\nversion = "0.309.0"\n',
            ("a", "tempest_fastapi_sdk/__init__.py"): '__version__: str = "0.308.0"\n',
            ("b", "tempest_fastapi_sdk/__init__.py"): '__version__: str = "0.309.0"\n',
            ("a", "uv.lock"): LOCK_OLD,
            ("b", "uv.lock"): LOCK_OLD.replace("0.308.0", "0.309.0"),
        }
        assert release_gate.only_version_changed(
            "a",
            "b",
            read=_reader(files),
            changed=lambda a, b: sorted({p for _, p in files}),
        )

    def test_dependency_bump_in_the_lock_is_not_equivalent(self) -> None:
        """Another package's ``version =`` line is code that ships."""
        files: dict[tuple[str, str], str] = {
            ("a", "uv.lock"): LOCK_OLD,
            ("b", "uv.lock"): LOCK_OLD.replace("4.9.0", "4.10.0"),
        }
        assert not release_gate.only_version_changed(
            "a", "b", read=_reader(files), changed=lambda a, b: ["uv.lock"]
        )

    def test_other_line_in_a_version_file_is_not_equivalent(self) -> None:
        """A dependency added next to the bump disqualifies the shortcut."""
        files: dict[tuple[str, str], str] = {
            ("a", "pyproject.toml"): 'version = "0.308.0"\ndependencies = []\n',
            ("b", "pyproject.toml"): 'version = "0.309.0"\ndependencies = ["x"]\n',
        }
        assert not release_gate.only_version_changed(
            "a", "b", read=_reader(files), changed=lambda a, b: ["pyproject.toml"]
        )

    @pytest.mark.parametrize("path", ["CHANGELOG.md", "docs/index.md", "README.md"])
    def test_docs_are_not_equivalent(self, path: str) -> None:
        """The docs guards read prose, so prose can turn the suite red."""
        assert not release_gate.only_version_changed(
            "a", "b", read=_reader({}), changed=lambda a, b: [path]
        )

    def test_empty_diff_is_equivalent(self) -> None:
        """``HEAD`` itself, before the bump, is trivially covered."""
        assert release_gate.only_version_changed(
            "a", "a", read=_reader({}), changed=lambda a, b: []
        )


class TestChecksGreen:
    """Every matrix job must have concluded ``success``."""

    def test_all_required_successful(self) -> None:
        """The three Python jobs passing is green."""
        runs: list[dict[str, object]] = [
            _green_run(name) for name in release_gate.REQUIRED_CHECKS
        ]
        assert release_gate.checks_green(runs)

    def test_pending_job_is_not_green(self) -> None:
        """A run still in progress vouches for nothing yet."""
        runs: list[dict[str, object]] = [
            _green_run("Python 3.11"),
            _green_run("Python 3.12"),
            {"name": "Python 3.13", "status": "in_progress", "conclusion": None},
        ]
        assert not release_gate.checks_green(runs)

    def test_failed_job_is_not_green(self) -> None:
        """One red job is red."""
        runs: list[dict[str, object]] = [
            _green_run("Python 3.11"),
            _green_run("Python 3.12"),
            {"name": "Python 3.13", "status": "completed", "conclusion": "failure"},
        ]
        assert not release_gate.checks_green(runs)

    def test_missing_job_is_not_green(self) -> None:
        """A commit CI never ran on has no runs at all."""
        assert not release_gate.checks_green([])


class TestFindVouchingCommit:
    """The walk returns the nearest covered ancestor, or stops."""

    def test_returns_the_green_parent_of_a_bump(self) -> None:
        """The bump commit itself is pending; its parent is green."""
        sha: str | None = release_gate.find_vouching_commit(
            "o/r",
            ancestors=lambda n: ["bump", "parent", "older"],
            same_tree=lambda s: True,
            green=lambda s: s == "parent",
        )
        assert sha == "parent"

    def test_stops_at_the_first_non_equivalent_ancestor(self) -> None:
        """An older green commit is not reused across a code change."""
        sha: str | None = release_gate.find_vouching_commit(
            "o/r",
            ancestors=lambda n: ["bump", "docs-change", "green-but-older"],
            same_tree=lambda s: s == "bump",
            green=lambda s: s == "green-but-older",
        )
        assert sha is None

    def test_none_when_nothing_is_green(self) -> None:
        """CI still running everywhere means the caller runs the suite."""
        sha: str | None = release_gate.find_vouching_commit(
            "o/r",
            ancestors=lambda n: ["bump", "parent"],
            same_tree=lambda s: True,
            green=lambda s: False,
        )
        assert sha is None


class TestReleaseWorkflowKeepsTheSuite:
    """The tag workflow may skip the suite only through the gate."""

    def test_suite_step_exists_and_is_gated(self) -> None:
        """Removing the suite, or skipping it unconditionally, fails here."""
        workflows: pathlib.Path = REPO_ROOT / ".github" / "workflows"
        text: str = (workflows / "release-pypi.yml").read_text(encoding="utf-8")
        gate: int = text.find("python scripts/release_gate.py")
        suite: int = text.find("run: uv run pytest")
        build: int = text.find("run: uv build")
        assert gate != -1 and suite != -1 and build != -1
        assert gate < suite < build
        assert "if: steps.gate.outputs.skip_suite != 'true'" in text

    def test_make_release_falls_back_to_the_suite(self) -> None:
        """``make release`` runs ``make test`` whenever the gate says no."""
        makefile: str = (REPO_ROOT / "Makefile").read_text(encoding="utf-8")
        release: str = makefile.split("\nrelease:", 1)[1].split("\n\n", 1)[0]
        assert "scripts/release_gate.py" in release
        assert "$(MAKE) test" in release


class TestAgainstThisRepository:
    """The real git plumbing agrees with the injected-function tests."""

    def test_head_is_version_equivalent_to_itself(self) -> None:
        """An empty diff through the real ``git diff`` is equivalent."""
        assert release_gate.only_version_changed("HEAD", "HEAD")

    def test_real_release_commit_is_a_pure_bump(self) -> None:
        """The v0.309.0 bump commit differs from its parent only in versions.

        ``f2d7c39`` is the commit ``make release`` produced; its parent
        ``c88ec48`` carried the CHANGELOG. Skipped when the clone is too
        shallow to have them.
        """
        try:
            release_gate._git("cat-file", "-e", "f2d7c39^{commit}")
            release_gate._git("cat-file", "-e", "c88ec48^{commit}")
        except Exception:
            pytest.skip("release commits not in this clone")
        assert release_gate.only_version_changed("c88ec48", "f2d7c39")
        assert not release_gate.only_version_changed("c88ec48~1", "f2d7c39")
