"""The version in ``uv.lock`` has to be the version being released.

``uv.lock`` records this package's own version, and ``make release`` used to
bump ``pyproject.toml`` and ``__init__.py`` without staging the refreshed
lock. Three tags shipped a lock one version behind their pyproject — v0.236.0,
v0.237.0, v0.238.0 — and v0.247.0 did it again after the staging fix, because
the drift can also arrive through an ordinary commit that touches the version
and leaves the lock alone.

Reading the lock **from disk** cannot catch any of that. Measured on
2026-08-23, in this repository:

```bash
sed -i '0,/^version = "0.250.0"$/s//version = "0.1.0"/' uv.lock
grep -A2 '^name = "tempest-fastapi-sdk"' uv.lock | grep version   # 0.1.0
uv run python -c "pass"
grep -A2 '^name = "tempest-fastapi-sdk"' uv.lock | grep version   # 0.250.0
```

``uv run`` re-locks before it runs anything, so by the time a test under
``uv run pytest`` opens the file, the drift has already been repaired. The
Makefile documented this as the reason no guard was possible.

It is possible — by asking **git** instead of the filesystem. What a tag ships
is the committed content, which is exactly what ``git show HEAD:<path>``
returns and exactly what ``uv run`` cannot rewrite.
"""

from __future__ import annotations

import pathlib
import re
import shutil
import subprocess

import pytest

ROOT: pathlib.Path = pathlib.Path(__file__).resolve().parent.parent

_PYPROJECT_VERSION: re.Pattern[str] = re.compile(r'^version = "([^"]+)"', re.MULTILINE)
"""First ``version = "..."`` of ``pyproject.toml`` — the project's own."""

_DUNDER_VERSION: re.Pattern[str] = re.compile(
    r'^__version__: str = "([^"]+)"', re.MULTILINE
)
"""The ``__version__`` the package exports."""

_LOCK_SELF_VERSION: re.Pattern[str] = re.compile(
    r'^name = "tempest-fastapi-sdk"\nversion = "([^"]+)"', re.MULTILINE
)
"""The lock's entry for this package, whose version is the one that drifts."""


def _committed(path: str) -> str | None:
    """Read a path as it stands in the current commit.

    Args:
        path (str): Repository-relative path.

    Returns:
        str | None: The committed content, or None when git cannot answer —
        no ``git`` binary, or not a work tree, which is the case for an
        unpacked sdist.
    """
    if shutil.which("git") is None:
        return None
    completed = subprocess.run(
        ["git", "show", f"HEAD:{path}"],
        cwd=ROOT,
        capture_output=True,
        text=True,
        check=False,
    )
    return completed.stdout if completed.returncode == 0 else None


def _first_group(pattern: re.Pattern[str], text: str) -> str:
    """Return a pattern's first capture, or ``""`` when it does not match.

    Args:
        pattern (re.Pattern[str]): The pattern to search with.
        text (str): The text to search.

    Returns:
        str: The captured group, or an empty string.
    """
    found = pattern.search(text)
    return found.group(1) if found else ""


def committed_versions() -> dict[str, str] | None:
    """Collect the three committed versions that have to agree.

    Returns:
        dict[str, str] | None: ``{"pyproject": ..., "__init__": ...,
        "uv.lock": ...}``, or None when the commit cannot be read.
    """
    pyproject = _committed("pyproject.toml")
    dunder = _committed("tempest_fastapi_sdk/__init__.py")
    lock = _committed("uv.lock")
    if pyproject is None or dunder is None or lock is None:
        return None
    return {
        "pyproject": _first_group(_PYPROJECT_VERSION, pyproject),
        "__init__": _first_group(_DUNDER_VERSION, dunder),
        "uv.lock": _first_group(_LOCK_SELF_VERSION, lock),
    }


def disagreements(versions: dict[str, str]) -> list[str]:
    """Report the files whose version differs from ``pyproject.toml``.

    Args:
        versions (dict[str, str]): Output of :func:`committed_versions`.

    Returns:
        list[str]: One message per disagreeing file, empty when they match.
    """
    expected = versions["pyproject"]
    return [
        f"{name} says {found!r}, pyproject.toml says {expected!r}"
        for name, found in versions.items()
        if name != "pyproject" and found != expected
    ]


def test_the_committed_versions_agree() -> None:
    """The release triple matches in the commit, not just on disk."""
    versions = committed_versions()
    if versions is None:
        pytest.skip("not a git work tree")
    assert versions["pyproject"], "could not read the project version"
    assert disagreements(versions) == [], (
        "run `uv lock` and stage uv.lock — the release workflow compares the "
        "tag against all three"
    )


def test_the_guard_fires_on_the_shape_that_shipped() -> None:
    """v0.247.0's exact drift, fed to the comparison.

    A guard that cannot fail is one nobody should trust, and this one is
    especially easy to write so that it never fails: read the wrong file, or
    read it at the wrong time, and everything looks fine forever.
    """
    shipped = {
        "pyproject": "0.247.0",
        "__init__": "0.247.0",
        "uv.lock": "0.246.0",
    }
    assert disagreements(shipped) == [
        "uv.lock says '0.246.0', pyproject.toml says '0.247.0'"
    ]
