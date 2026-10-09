"""Find the commit whose green CI already vouches for the tree being released.

A release used to run the full suite three times on the same code: on the
push to ``main``, again inside ``make release`` and a third time in the tag
workflow -- each one serial and under coverage, 25 to 43 minutes apiece. The
code being tagged is the code CI just tested; only the version strings moved.

This script answers one question: is there a first-parent ancestor of
``HEAD`` whose required CI checks all passed, and whose tree differs from
``HEAD`` **only** in this package's version strings? When there is, the suite
has already run on what ships, and the caller can skip it. When there is not
-- CI still running, failed, or any other byte changed -- the caller runs the
suite itself.

Docs are deliberately not "safe": the docs guards (``test_docs_*``) read
``docs/``, ``README.md`` and even ``CHANGELOG.md``, so a prose-only commit can
still turn the suite red.

Usage::

    python scripts/release_gate.py [--repo OWNER/NAME] [--max-depth N]

Exit status 0 prints the vouching SHA; exit status 1 means "run the suite".
"""

from __future__ import annotations

import argparse
import json
import os
import re
import subprocess
import sys
from collections.abc import Callable, Sequence

REQUIRED_CHECKS: tuple[str, ...] = ("Python 3.11", "Python 3.12", "Python 3.13")
"""Check-run names that must all conclude ``success`` (the CI matrix)."""

VERSION_PATTERNS: dict[str, re.Pattern[str]] = {
    "pyproject.toml": re.compile(r'(?m)^(version = )"[^"]+"$'),
    "tempest_fastapi_sdk/__init__.py": re.compile(
        r'(?m)^(__version__: str = )"[^"]+"$'
    ),
    "uv.lock": re.compile(r'(name = "tempest-fastapi-sdk"\nversion = )"[^"]+"'),
}
"""Per file, the one version string a release bump is allowed to change."""

DEFAULT_MAX_DEPTH: int = 10
"""How many first-parent ancestors to inspect before giving up."""


def _git(*args: str) -> str:
    """Run git and return its stdout.

    Args:
        *args (str): Arguments after ``git``.

    Returns:
        str: The command's standard output.

    Raises:
        subprocess.CalledProcessError: If git exits non-zero.
    """
    return subprocess.run(
        ["git", *args], check=True, capture_output=True, text=True
    ).stdout


def normalize_version(path: str, text: str) -> str:
    """Blank out the package's own version string in one file.

    Args:
        path (str): Repository-relative path, a key of :data:`VERSION_PATTERNS`.
        text (str): The file's content.

    Returns:
        str: The content with the version replaced by a fixed placeholder,
        only the first occurrence -- ``uv.lock`` lists hundreds of
        ``version =`` lines, and a dependency bump must not pass as ours.
    """
    return VERSION_PATTERNS[path].sub(r'\1"X"', text, count=1)


def only_version_changed(
    base: str,
    head: str = "HEAD",
    *,
    read: Callable[[str, str], str] | None = None,
    changed: Callable[[str, str], list[str]] | None = None,
) -> bool:
    """Tell whether two commits differ only in the package's version strings.

    Args:
        base (str): The older commit.
        head (str): The newer commit.
        read (Callable[[str, str], str] | None): ``(rev, path) -> content``;
            defaults to ``git show``. Injected by the tests.
        changed (Callable[[str, str], list[str]] | None): ``(base, head) ->
            paths``; defaults to ``git diff --name-only``. Injected by the tests.

    Returns:
        bool: ``True`` when every changed path is a version file and each one
        is identical once its version string is normalized. An empty diff is
        ``True``.
    """
    read_file: Callable[[str, str], str] = read or (
        lambda rev, path: _git("show", f"{rev}:{path}")
    )
    list_changed: Callable[[str, str], list[str]] = changed or (
        lambda a, b: [p for p in _git("diff", "--name-only", a, b).splitlines() if p]
    )
    paths: list[str] = list_changed(base, head)
    if any(path not in VERSION_PATTERNS for path in paths):
        return False
    return all(
        normalize_version(path, read_file(base, path))
        == normalize_version(path, read_file(head, path))
        for path in paths
    )


def checks_green(check_runs: Sequence[dict[str, object]]) -> bool:
    """Tell whether every required check has a successful run.

    Args:
        check_runs (Sequence[dict[str, object]]): The ``check_runs`` array of
            GitHub's ``GET /repos/{repo}/commits/{sha}/check-runs``.

    Returns:
        bool: ``True`` when each name in :data:`REQUIRED_CHECKS` has at least
        one completed run concluding ``success``. Pending, missing, failed or
        cancelled runs all read as "not green".
    """
    passed: set[str] = {
        str(run.get("name"))
        for run in check_runs
        if run.get("status") == "completed" and run.get("conclusion") == "success"
    }
    return all(name in passed for name in REQUIRED_CHECKS)


def fetch_check_runs(repo: str, sha: str) -> list[dict[str, object]]:
    """Read a commit's check runs through the ``gh`` CLI.

    Args:
        repo (str): ``OWNER/NAME``.
        sha (str): The commit.

    Returns:
        list[dict[str, object]]: The check runs, or ``[]`` when ``gh`` fails
        (no auth, no network) -- which the caller reads as "not green".
    """
    result: subprocess.CompletedProcess[str] = subprocess.run(
        [
            "gh",
            "api",
            f"repos/{repo}/commits/{sha}/check-runs?per_page=100",
            "--jq",
            ".check_runs",
        ],
        capture_output=True,
        text=True,
    )
    if result.returncode != 0:
        return []
    runs: object = json.loads(result.stdout or "[]")
    return runs if isinstance(runs, list) else []


def find_vouching_commit(
    repo: str,
    *,
    max_depth: int = DEFAULT_MAX_DEPTH,
    ancestors: Callable[[int], list[str]] | None = None,
    same_tree: Callable[[str], bool] | None = None,
    green: Callable[[str], bool] | None = None,
) -> str | None:
    """Walk first-parent ancestors of ``HEAD`` for one green CI already covers.

    Args:
        repo (str): ``OWNER/NAME``.
        max_depth (int): How many commits to inspect, ``HEAD`` included.
        ancestors (Callable[[int], list[str]] | None): ``n -> shas``, newest
            first; defaults to ``git rev-list --first-parent``. Injected by
            the tests.
        same_tree (Callable[[str], bool] | None): ``sha -> bool``, whether
            ``sha`` and ``HEAD`` differ only in the version; defaults to
            :func:`only_version_changed`.
        green (Callable[[str], bool] | None): ``sha -> bool``; defaults to
            :func:`checks_green` over :func:`fetch_check_runs`.

    Returns:
        str | None: The nearest ancestor that is version-equivalent to
        ``HEAD`` and green, or ``None``. The walk stops at the first
        ancestor that is not version-equivalent: every older one carries
        that same difference.
    """
    list_ancestors: Callable[[int], list[str]] = ancestors or (
        lambda n: _git("rev-list", "--first-parent", f"--max-count={n}", "HEAD").split()
    )
    is_same: Callable[[str], bool] = same_tree or only_version_changed
    is_green: Callable[[str], bool] = green or (
        lambda sha: checks_green(fetch_check_runs(repo, sha))
    )
    for sha in list_ancestors(max_depth):
        if not is_same(sha):
            return None
        if is_green(sha):
            return sha
    return None


def _default_repo() -> str:
    """Resolve ``OWNER/NAME`` from ``GITHUB_REPOSITORY`` or ``gh``.

    Returns:
        str: The repository slug, or ``""`` when neither source answers.
    """
    env: str = os.environ.get("GITHUB_REPOSITORY", "")
    if env:
        return env
    result: subprocess.CompletedProcess[str] = subprocess.run(
        ["gh", "repo", "view", "--json", "nameWithOwner", "--jq", ".nameWithOwner"],
        capture_output=True,
        text=True,
    )
    return result.stdout.strip() if result.returncode == 0 else ""


def main(argv: Sequence[str] | None = None) -> int:
    """Print the vouching commit, or explain why the suite must run.

    Args:
        argv (Sequence[str] | None): Command-line arguments.

    Returns:
        int: ``0`` when a green, version-equivalent ancestor exists; ``1``
        otherwise.
    """
    parser: argparse.ArgumentParser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--repo", default="")
    parser.add_argument("--max-depth", type=int, default=DEFAULT_MAX_DEPTH)
    args: argparse.Namespace = parser.parse_args(argv)
    repo: str = args.repo or _default_repo()
    if not repo:
        print(
            "release-gate: cannot resolve the repository; run the suite.",
            file=sys.stderr,
        )
        return 1
    sha: str | None = find_vouching_commit(repo, max_depth=args.max_depth)
    if sha is None:
        print(
            "release-gate: no green CI run covers this tree "
            "(CI pending or red, or more than the version changed); run the suite.",
            file=sys.stderr,
        )
        return 1
    print(sha)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
