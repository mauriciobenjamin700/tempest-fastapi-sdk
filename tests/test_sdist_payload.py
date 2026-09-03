"""Guard the sdist against tooling artifacts riding along.

``tests/test_wheel_payload.py`` pins the wheel's non-Python payload, and
the wheel is easy to reason about: ``packages = ["tempest_fastapi_sdk"]``
means nothing outside the package can get in. The **sdist** is the
opposite — it takes the whole repository minus an explicit ``exclude``,
so every directory a tool drops at the root ships to PyPI until someone
notices.

Nobody noticed twice:

* ``.claude/`` added 33.7 kB of skill, agent and settings files to the
  tarball; the fix was an ``exclude`` entry, recorded in ``pyproject.toml``.
* ``.playwright-mcp/`` added 60 kB of console logs and page snapshots from
  a single browser-validation session in 2026-08-01, and shipped inside
  every sdist from then through 0.284.0.

Both are the same defect, one release apart, and the first fix did not
prevent the second because it named a directory instead of stating the
rule. This states the rule: a dotted entry at the sdist root is allowed
only if it is listed here, with the reason it belongs to a source
distribution.

The check builds an sdist in a temporary directory, so it measures the
artifact rather than the repository — they differ exactly when the build
configuration is wrong, which is what is under guard.
"""

from __future__ import annotations

import pathlib
import shutil
import subprocess
import tarfile
import tempfile
from typing import Final

import pytest

ROOT: Final[pathlib.Path] = pathlib.Path(__file__).resolve().parents[1]

ALLOWED_DOT_ENTRIES: Final[dict[str, str]] = {
    ".github": "CI workflows, useful to a downstream packager rebuilding",
    ".gitignore": "one file, and it documents what the tree deliberately omits",
    ".python-version": "one file, and it pins the interpreter the lock targets",
}
"""Dotted entries a source distribution may carry, each with its reason.

An entry here is a decision. A dotted directory absent from this mapping
is the defect the guard exists for — see the module docstring for the two
that shipped.
"""


@pytest.fixture(scope="module")
def sdist_names() -> list[str]:
    """Build an sdist and return the member paths inside it.

    Returns:
        list[str]: Every archive member, with the top-level
        ``<name>-<version>/`` prefix stripped.
    """
    if shutil.which("uv") is None:
        pytest.skip("uv is not on PATH")
    with tempfile.TemporaryDirectory() as out:
        subprocess.run(
            ["uv", "build", "--sdist", "--out-dir", out],
            cwd=ROOT,
            check=True,
            capture_output=True,
        )
        archives = sorted(pathlib.Path(out).glob("*.tar.gz"))
        assert archives, "uv build produced no sdist"
        with tarfile.open(archives[0]) as tar:
            members = tar.getnames()
    return [name.partition("/")[2] for name in members if "/" in name]


def top_level_dot_entries(names: list[str]) -> set[str]:
    """Return the dotted entries at the archive root.

    Args:
        names (list[str]): Member paths, prefix already stripped.

    Returns:
        set[str]: The first path segment of every member that starts with
        a dot.
    """
    return {name.split("/", 1)[0] for name in names if name.startswith(".")}


class TestNoToolingArtifactShips:
    def test_every_dotted_entry_is_accounted_for(
        self,
        sdist_names: list[str],
    ) -> None:
        unexpected = sorted(
            top_level_dot_entries(sdist_names) - set(ALLOWED_DOT_ENTRIES),
        )

        assert not unexpected, (
            f"{len(unexpected)} tooling artifact(s) in the sdist: "
            + ", ".join(unexpected)
            + ". Add a .gitignore entry (and `git rm -r --cached`), or list "
            "it in ALLOWED_DOT_ENTRIES with the reason it belongs."
        )

    def test_the_package_itself_is_in_there(
        self,
        sdist_names: list[str],
    ) -> None:
        """The inverse failure: an exclude that drops too much."""
        assert "tempest_fastapi_sdk/__init__.py" in sdist_names

    def test_every_allowance_carries_a_reason(self) -> None:
        assert all(reason.strip() for reason in ALLOWED_DOT_ENTRIES.values())


class TestTheGuardFires:
    """Fed the artifact that shipped, the check has to refuse."""

    def test_the_playwright_directory_would_be_reported(self) -> None:
        shipped = [
            "tempest_fastapi_sdk/__init__.py",
            ".playwright-mcp/console-2026-08-01T19-55-53-764Z.log",
            ".playwright-mcp/page-2026-08-01T19-55-53-849Z.yml",
            ".github/workflows/ci.yml",
        ]

        unexpected = top_level_dot_entries(shipped) - set(ALLOWED_DOT_ENTRIES)

        assert unexpected == {".playwright-mcp"}

    def test_the_claude_directory_would_be_reported_too(self) -> None:
        """The earlier one, which an ``exclude`` fixed without a rule."""
        shipped = ["tempest_fastapi_sdk/__init__.py", ".claude/settings.json"]

        unexpected = top_level_dot_entries(shipped) - set(ALLOWED_DOT_ENTRIES)

        assert unexpected == {".claude"}
