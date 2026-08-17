"""Bump the SDK version in the two files that must never disagree.

``pyproject.toml`` and ``tempest_fastapi_sdk/__init__.py`` both carry the
version string, and a release where they diverge publishes a wheel whose
``__version__`` lies. This script rewrites both, refuses to run when the
current values already disagree, and reports whether ``CHANGELOG.md`` has an
entry for the new version so the caller can stop before tagging.

Usage:
    python bump_version.py 0.235.0 [--root /path/to/repo] [--dry-run]

Exit codes:
    0  both files rewritten (or, with --dry-run, would be)
    1  refused: bad argument, file missing, or the two files disagree
    2  files rewritten but CHANGELOG.md has no entry for the new version
"""

from __future__ import annotations

import argparse
import pathlib
import re
import sys

_PYPROJECT_RE = re.compile(r'^version = "(?P<version>[^"]+)"$', re.MULTILINE)
_INIT_RE = re.compile(r'^__version__: str = "(?P<version>[^"]+)"$', re.MULTILINE)
_SEMVER_RE = re.compile(r"^\d+\.\d+\.\d+$")


def _read_version(path: pathlib.Path, pattern: re.Pattern[str]) -> str:
    """Return the single version string ``pattern`` matches inside ``path``.

    Args:
        path (pathlib.Path): File to read.
        pattern (re.Pattern[str]): Pattern with a ``version`` named group.

    Returns:
        The captured version string.

    Raises:
        SystemExit: If the file is missing, or the pattern matches zero or
            more than one line — both mean the caller's assumption about the
            file shape is wrong and a blind rewrite would corrupt it.
    """
    if not path.exists():
        raise SystemExit(f"refused: {path} does not exist")
    matches = pattern.findall(path.read_text(encoding="utf-8"))
    if len(matches) != 1:
        raise SystemExit(
            f"refused: expected exactly 1 version line in {path}, found {len(matches)}"
        )
    return matches[0]


def _write_version(
    path: pathlib.Path, pattern: re.Pattern[str], new_version: str
) -> None:
    """Rewrite the single version line ``pattern`` matches inside ``path``.

    Args:
        path (pathlib.Path): File to rewrite in place.
        pattern (re.Pattern[str]): Pattern with a ``version`` named group.
        new_version (str): Version to write.
    """
    text = path.read_text(encoding="utf-8")

    def _replace(match: re.Match[str]) -> str:
        """Swap the captured version for ``new_version``, keeping the line."""
        return match.group(0).replace(match.group("version"), new_version)

    path.write_text(pattern.sub(_replace, text, count=1), encoding="utf-8")


def main(argv: list[str] | None = None) -> int:
    """Rewrite both version files and check the changelog.

    Args:
        argv (list[str] | None): Argument vector; ``None`` uses ``sys.argv``.

    Returns:
        The process exit code documented in the module docstring.
    """
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("version", help="new version, e.g. 0.235.0")
    parser.add_argument("--root", default=".", help="repository root")
    parser.add_argument(
        "--dry-run", action="store_true", help="report without writing files"
    )
    args = parser.parse_args(argv)

    if not _SEMVER_RE.match(args.version):
        print(f"refused: {args.version!r} is not X.Y.Z", file=sys.stderr)
        return 1

    root = pathlib.Path(args.root).resolve()
    pyproject = root / "pyproject.toml"
    init = root / "tempest_fastapi_sdk" / "__init__.py"
    changelog = root / "CHANGELOG.md"

    current_pyproject = _read_version(pyproject, _PYPROJECT_RE)
    current_init = _read_version(init, _INIT_RE)
    if current_pyproject != current_init:
        print(
            f"refused: pyproject says {current_pyproject}, __init__ says "
            f"{current_init} — fix the disagreement first",
            file=sys.stderr,
        )
        return 1
    if current_pyproject == args.version:
        print(f"refused: already at {args.version}", file=sys.stderr)
        return 1

    verb = "would bump" if args.dry_run else "bumped"
    if not args.dry_run:
        _write_version(pyproject, _PYPROJECT_RE, args.version)
        _write_version(init, _INIT_RE, args.version)
    print(f"{verb} {current_pyproject} -> {args.version} (pyproject.toml, __init__.py)")

    marker = f"## [{args.version}]"
    if not changelog.exists() or marker not in changelog.read_text(encoding="utf-8"):
        print(
            f"CHANGELOG.md has no '{marker}' entry — write it before tagging",
            file=sys.stderr,
        )
        return 2
    print(f"CHANGELOG.md has the {marker} entry")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
