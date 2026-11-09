"""Publish a tagged release to PyPI from the maintainer's machine.

This is what ``.github/workflows/release-pypi.yml`` did until 2026-11-09, run
locally and in the same order. The account was locked for billing that day,
the v0.346.0 tag reached the remote and nothing published, and the rule since
then is that nothing in this repository depends on GitHub Actions. The
publish is ``make publish VERSION=X.Y.Z``, which runs this script.

Every step in :data:`STEPS` runs in order and the first failure stops the
run. The checks come first, the build next, and the two irreversible steps --
the upload and the GitHub Release -- last, so a refusal never leaves half a
release behind. ``--dry-run`` runs everything except those two.

The upload authenticates with ``UV_PUBLISH_TOKEN``, read by ``uv publish``
from the environment. The script only checks that it is set: the value is
never passed on a command line, never printed, never written to disk.
"""

from __future__ import annotations

import argparse
import os
import pathlib
import re
import shlex
import shutil
import subprocess
import sys
import tempfile
import zipfile
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, field

PACKAGE: str = "tempest_fastapi_sdk"
"""Import name of the distribution being published."""

DIST_NAME: str = "tempest-fastapi-sdk"
"""Distribution name, as ``uv.lock`` and PyPI spell it."""

TOKEN_ENV: str = "UV_PUBLISH_TOKEN"
"""Environment variable ``uv publish`` reads the PyPI API token from."""

TWINE_VERSION: str = "7.0.0"
"""``twine`` pinned for ``twine check``, like ``pip-audit`` in ``make audit``."""

SMOKE_PYTHON: str = "3.12"
"""The supported floor: the smoke install runs on the oldest Python."""

PYPI_SIMPLE_URL: str = "https://pypi.org/simple/"
"""Index ``uv publish --check-url`` asks, so a re-run skips files already up."""

MIN_WHEEL_PY_FILES: int = 20
"""Fewer ``.py`` files than this means the wheel lost the package."""

SMOKE_CHECK: str = (
    f"import {PACKAGE} as m; "
    "assert m.__version__, 'no __version__'; "
    "assert m.BaseModel and m.BaseRepository and m.AsyncDatabaseManager, "
    "'core primitives missing'; "
    "assert m.AlembicHelper and m.NAMING_CONVENTION, 'alembic helpers missing'; "
    "assert m.PasswordUtils and m.JWTUtils and m.EmailUtils and m.UploadUtils, "
    "'utils missing'; "
    "assert m.is_valid_cpf and m.is_valid_cnpj and m.is_valid_phone_br, "
    "'BR regex helpers missing'; "
    "print('Smoke OK - version =', m.__version__)"
)
"""Base-install smoke: every extra is lazy-loaded, so the top level imports bare."""

_PYPROJECT_VERSION: re.Pattern[str] = re.compile(r'^version = "([^"]+)"', re.MULTILINE)
_DUNDER_VERSION: re.Pattern[str] = re.compile(
    r'^__version__: str = "([^"]+)"', re.MULTILINE
)
_LOCK_SELF_VERSION: re.Pattern[str] = re.compile(
    r'^name = "' + re.escape(DIST_NAME) + r'"\nversion = "([^"]+)"', re.MULTILINE
)
_VERSION: re.Pattern[str] = re.compile(r"^\d+\.\d+\.\d+$")


class PublishError(Exception):
    """A step refused to continue; the message says why and what to do."""


@dataclass
class Release:
    """What one run of the script publishes, and how.

    Attributes:
        root (pathlib.Path): Repository checkout the release is built from.
        version (str): The version being published, without the ``v``.
        dry_run (bool): Run every check and the build, skip upload and release.
        env (Mapping[str, str]): The environment the token is read from.
        notes (str): Release notes, filled by :func:`step_changelog_notes`.
    """

    root: pathlib.Path
    version: str
    dry_run: bool
    env: Mapping[str, str] = field(default_factory=lambda: dict(os.environ))
    notes: str = ""

    @property
    def tag(self) -> str:
        """Return the git tag of the release.

        Returns:
            str: ``v`` followed by the version.
        """
        return f"v{self.version}"

    @property
    def dist(self) -> pathlib.Path:
        """Return the directory the distributions are built into.

        Returns:
            pathlib.Path: ``<root>/dist``.
        """
        return self.root / "dist"

    def distributions(self) -> list[str]:
        """List the built sdist and wheel, without the ``.gitignore`` uv adds.

        Returns:
            list[str]: Paths of every ``.whl`` and ``.tar.gz`` in ``dist/``.
        """
        return sorted(
            str(path)
            for path in self.dist.iterdir()
            if path.name.endswith((".whl", ".tar.gz"))
        )


def run(
    release: Release, argv: Sequence[str], *, capture: bool = False
) -> subprocess.CompletedProcess[str]:
    """Run a command in the release checkout and stop on failure.

    Args:
        release (Release): The release being published.
        argv (Sequence[str]): The command. Never carries the token.
        capture (bool): Capture stdout instead of streaming it.

    Returns:
        subprocess.CompletedProcess[str]: The finished process.

    Raises:
        PublishError: If the command exits non-zero.
    """
    print(f"$ {shlex.join(argv)}", flush=True)
    completed: subprocess.CompletedProcess[str] = subprocess.run(
        list(argv),
        cwd=release.root,
        env=dict(release.env),
        capture_output=capture,
        text=True,
        check=False,
    )
    if completed.returncode != 0:
        detail: str = (completed.stderr or "").strip() if capture else ""
        raise PublishError(
            f"`{shlex.join(argv)}` exited {completed.returncode}. {detail}".strip()
        )
    return completed


def git(release: Release, *args: str) -> str:
    """Run a git command in the checkout and return its stripped stdout.

    Args:
        release (Release): The release being published.
        *args (str): Arguments after ``git``.

    Returns:
        str: The command's standard output, stripped.
    """
    return run(release, ["git", *args], capture=True).stdout.strip()


def missing_token(env: Mapping[str, str], *, dry_run: bool) -> str | None:
    """Explain why the upload cannot authenticate, or return None when it can.

    Args:
        env (Mapping[str, str]): The process environment.
        dry_run (bool): A dry run uploads nothing and needs no token.

    Returns:
        str | None: The refusal message, which never contains the token.
    """
    if dry_run or env.get(TOKEN_ENV, "").strip():
        return None
    return (
        f"{TOKEN_ENV} is not set. Export a PyPI API token scoped to the "
        f"{DIST_NAME} project (it starts with `pypi-`) before `make publish`, "
        "or pass DRY_RUN=1 to run every check without uploading."
    )


def read_versions(root: pathlib.Path) -> dict[str, str]:
    """Read the version recorded in each file that has to match the tag.

    Args:
        root (pathlib.Path): Repository checkout.

    Returns:
        dict[str, str]: ``{"pyproject.toml": ..., "__init__.py": ...,
        "uv.lock": ...}``; an empty string where the pattern did not match.
    """
    sources: dict[str, tuple[pathlib.Path, re.Pattern[str]]] = {
        "pyproject.toml": (root / "pyproject.toml", _PYPROJECT_VERSION),
        "__init__.py": (root / PACKAGE / "__init__.py", _DUNDER_VERSION),
        "uv.lock": (root / "uv.lock", _LOCK_SELF_VERSION),
    }
    versions: dict[str, str] = {}
    for name, (path, pattern) in sources.items():
        found: re.Match[str] | None = pattern.search(path.read_text(encoding="utf-8"))
        versions[name] = found.group(1) if found else ""
    return versions


def version_mismatches(version: str, versions: Mapping[str, str]) -> list[str]:
    """List every file whose recorded version is not the one being published.

    Args:
        version (str): The version from the tag, without the ``v``.
        versions (Mapping[str, str]): Output of :func:`read_versions`.

    Returns:
        list[str]: One message per disagreeing file, empty when all match.
    """
    return [
        f"{name} records {found!r}, the tag is {version!r}"
        for name, found in versions.items()
        if found != version
    ]


def changelog_notes(changelog: str, version: str) -> str | None:
    """Extract the body of one version's section from ``CHANGELOG.md``.

    Args:
        changelog (str): The changelog's content.
        version (str): The version whose ``## [X.Y.Z]`` section to read.

    Returns:
        str | None: The section body, stripped, or None when there is none.
    """
    match: re.Match[str] | None = re.search(
        r"^## \[" + re.escape(version) + r"\][^\n]*\n(.*?)(?=^## \[|\Z)",
        changelog,
        re.DOTALL | re.MULTILINE,
    )
    if match is None or not match.group(1).strip():
        return None
    return match.group(1).strip()


def wheel_problems(members: Sequence[str]) -> list[str]:
    """List what is missing from a wheel's member list.

    Args:
        members (Sequence[str]): ``ZipFile.namelist()`` of the wheel.

    Returns:
        list[str]: One message per problem, empty for a complete wheel.
    """
    py_files: list[str] = [
        name
        for name in members
        if name.startswith(f"{PACKAGE}/") and name.endswith(".py")
    ]
    templates: list[str] = [
        name for name in members if name.endswith("env.py.template")
    ]
    problems: list[str] = []
    if len(py_files) < MIN_WHEEL_PY_FILES:
        problems.append(f"wheel suspiciously empty ({len(py_files)} .py files)")
    if len(templates) != 1:
        problems.append(f"expected one env.py.template, found {len(templates)}")
    return problems


def step_preflight(release: Release) -> None:
    """Refuse a bad version, a missing token or a logged-out ``gh`` up front.

    Checked before any work so a publish that cannot finish fails in seconds,
    not after the audit and the build.

    Args:
        release (Release): The release being published.

    Raises:
        PublishError: If the run cannot reach the upload.
    """
    if not _VERSION.match(release.version):
        raise PublishError(f"VERSION must look like 0.346.0, got {release.version!r}.")
    refusal: str | None = missing_token(release.env, dry_run=release.dry_run)
    if refusal is not None:
        raise PublishError(refusal)
    if not release.dry_run:
        if shutil.which("gh") is None:
            raise PublishError("`gh` is not installed; it creates the GitHub Release.")
        run(release, ["gh", "auth", "status"], capture=True)


def step_head_is_tag(release: Release) -> None:
    """Require a clean tree whose ``HEAD`` is the pushed tag, on ``main``.

    Args:
        release (Release): The release being published.

    Raises:
        PublishError: If the tree is dirty, ``HEAD`` is not the tag, the tag
            is not on the remote, or the commit is not on ``origin/main``.
    """
    if git(release, "status", "--porcelain"):
        raise PublishError(
            "Working tree dirty. Publish from a clean checkout of the tag."
        )
    head: str = git(release, "rev-parse", "HEAD")
    tagged: str = git(release, "rev-parse", f"{release.tag}^{{commit}}")
    if head != tagged:
        raise PublishError(
            f"HEAD is {head[:12]} but {release.tag} is {tagged[:12]}. "
            f"Run `git checkout {release.tag}` first."
        )
    remote: str = git(
        release,
        "ls-remote",
        "origin",
        f"refs/tags/{release.tag}",
        f"refs/tags/{release.tag}^{{}}",
    )
    remote_shas: set[str] = {line.split()[0] for line in remote.splitlines() if line}
    if head not in remote_shas:
        raise PublishError(
            f"origin has no {release.tag} pointing at {head[:12]}. "
            f"Push it first: git push origin {release.tag}"
        )
    git(release, "fetch", "--quiet", "origin", "main")
    ancestor: subprocess.CompletedProcess[str] = subprocess.run(
        ["git", "merge-base", "--is-ancestor", head, "FETCH_HEAD"],
        cwd=release.root,
        text=True,
        check=False,
    )
    if ancestor.returncode != 0:
        raise PublishError(
            f"{head[:12]} is not on origin/main. Push main first: git push origin main"
        )


def step_versions_match(release: Release) -> None:
    """Require the tag, ``pyproject``, ``__version__`` and ``uv.lock`` to agree.

    ``uv.lock`` records this package's own version and drifted a version
    behind the pyproject in v0.236.0, v0.237.0, v0.238.0 and v0.247.0
    (``tests/test_lock_version_guard.py``).

    Args:
        release (Release): The release being published.

    Raises:
        PublishError: If any of the four differs.
    """
    versions: dict[str, str] = read_versions(release.root)
    for name, found in versions.items():
        print(f"  {name:<15} {found}")
    mismatches: list[str] = version_mismatches(release.version, versions)
    if mismatches:
        raise PublishError(
            "Version mismatch: "
            + "; ".join(mismatches)
            + ". Fix the files (and `uv lock`), commit, and move the tag."
        )


def step_changelog_notes(release: Release) -> None:
    """Read the release notes now, so a missing section stops before the upload.

    Args:
        release (Release): The release being published; ``notes`` is filled.

    Raises:
        PublishError: If ``CHANGELOG.md`` has no section for the version.
    """
    changelog: str = (release.root / "CHANGELOG.md").read_text(encoding="utf-8")
    notes: str | None = changelog_notes(changelog, release.version)
    if notes is None:
        raise PublishError(
            f"CHANGELOG.md has no non-empty '## [{release.version}]' section."
        )
    release.notes = notes


def step_lock_check(release: Release) -> None:
    """Require ``uv.lock`` to still satisfy ``pyproject.toml`` (#436).

    Args:
        release (Release): The release being published.
    """
    run(release, ["uv", "lock", "--check"])


def step_audit(release: Release) -> None:
    """Fail on any known advisory in the locked resolution (``make audit``).

    Args:
        release (Release): The release being published.
    """
    run(release, ["make", "--no-print-directory", "audit"])


def step_build(release: Release) -> None:
    """Build the sdist and the wheel into an emptied ``dist/``.

    Args:
        release (Release): The release being published.

    Raises:
        PublishError: If the build does not yield exactly one sdist and one
            wheel of this version.
    """
    shutil.rmtree(release.dist, ignore_errors=True)
    run(release, ["uv", "build", "--out-dir", str(release.dist)])
    stem: str = f"{PACKAGE}-{release.version}"
    built: list[str] = [pathlib.Path(path).name for path in release.distributions()]
    expected: list[str] = [f"{stem}-py3-none-any.whl", f"{stem}.tar.gz"]
    if built != expected:
        raise PublishError(f"dist/ holds {built}, expected {expected}.")


def step_twine_check(release: Release) -> None:
    """Validate the distributions' metadata with ``twine check --strict``.

    Args:
        release (Release): The release being published.
    """
    files: list[str] = release.distributions()
    run(release, ["uvx", f"twine@{TWINE_VERSION}", "check", "--strict", *files])


def step_wheel_contents(release: Release) -> None:
    """Require the wheel to carry the package source and the Alembic template.

    Args:
        release (Release): The release being published.

    Raises:
        PublishError: If :func:`wheel_problems` reports anything.
    """
    wheel: pathlib.Path = next(release.dist.glob("*.whl"))
    with zipfile.ZipFile(wheel) as archive:
        members: list[str] = archive.namelist()
    problems: list[str] = wheel_problems(members)
    if problems:
        raise PublishError(f"{wheel.name}: " + "; ".join(problems))
    print(f"  {wheel.name}: {len(members)} members, env.py.template present")


def step_smoke(release: Release) -> None:
    """Install the built wheel, no extras, in a clean venv and import the top level.

    Args:
        release (Release): The release being published.
    """
    wheel: pathlib.Path = next(release.dist.glob("*.whl"))
    with tempfile.TemporaryDirectory(prefix="tempest-publish-smoke-") as scratch:
        venv: pathlib.Path = pathlib.Path(scratch) / "venv"
        python: str = str(venv / "bin" / "python")
        run(release, ["uv", "venv", "--quiet", "--python", SMOKE_PYTHON, str(venv)])
        run(
            release, ["uv", "pip", "install", "--quiet", "--python", python, str(wheel)]
        )
        run(release, [python, "-c", SMOKE_CHECK])


def step_upload(release: Release) -> None:
    """Upload both distributions to PyPI with the token from the environment.

    ``--check-url`` makes a re-run after a partial upload skip the files PyPI
    already has instead of failing on them.

    Args:
        release (Release): The release being published.
    """
    if release.dry_run:
        print("  DRY_RUN: skipping `uv publish`")
        return
    files: list[str] = release.distributions()
    run(
        release,
        [
            "uv",
            "publish",
            "--trusted-publishing",
            "never",
            "--check-url",
            PYPI_SIMPLE_URL,
            *files,
        ],
    )


def step_github_release(release: Release) -> None:
    """Create (or refresh) the GitHub Release from the CHANGELOG section.

    ``gh release`` is the REST API, not GitHub Actions.

    Args:
        release (Release): The release being published.
    """
    if release.dry_run:
        print(f"  DRY_RUN: skipping `gh release create {release.tag}`")
        print(f"  notes: {len(release.notes)} characters from CHANGELOG.md")
        return
    with tempfile.NamedTemporaryFile(
        "w", suffix=".md", prefix="release-notes-", delete=False, encoding="utf-8"
    ) as handle:
        handle.write(release.notes + "\n")
        notes_file: str = handle.name
    try:
        exists: bool = (
            subprocess.run(
                ["gh", "release", "view", release.tag],
                cwd=release.root,
                capture_output=True,
                check=False,
            ).returncode
            == 0
        )
        if exists:
            run(
                release,
                [
                    "gh",
                    "release",
                    "edit",
                    release.tag,
                    "--notes-file",
                    notes_file,
                    "--latest",
                ],
            )
        else:
            run(
                release,
                [
                    "gh",
                    "release",
                    "create",
                    release.tag,
                    "--title",
                    release.tag,
                    "--notes-file",
                    notes_file,
                    "--verify-tag",
                    "--latest",
                ],
            )
    finally:
        pathlib.Path(notes_file).unlink(missing_ok=True)


STEPS: tuple[tuple[str, Callable[[Release], None]], ...] = (
    ("preflight", step_preflight),
    ("head-is-tag", step_head_is_tag),
    ("versions-match", step_versions_match),
    ("changelog-notes", step_changelog_notes),
    ("lock-check", step_lock_check),
    ("audit", step_audit),
    ("build", step_build),
    ("twine-check", step_twine_check),
    ("wheel-contents", step_wheel_contents),
    ("smoke", step_smoke),
    ("upload", step_upload),
    ("github-release", step_github_release),
)
"""Every step, in the order it runs. ``tests/test_release_flow_guard.py`` pins it."""


def parse_args(argv: Sequence[str]) -> argparse.Namespace:
    """Parse the command line.

    Args:
        argv (Sequence[str]): Arguments after the program name.

    Returns:
        argparse.Namespace: ``version``, ``root`` and ``dry_run``.
    """
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument(
        "--version", required=True, help="Version to publish, e.g. 0.346.0"
    )
    parser.add_argument(
        "--root",
        type=pathlib.Path,
        default=pathlib.Path.cwd(),
        help="Checkout to publish from (default: the current directory).",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Run every check and the build; skip the upload and the GitHub Release.",
    )
    return parser.parse_args(list(argv))


def main(argv: Sequence[str] | None = None) -> int:
    """Run every step of :data:`STEPS` in order.

    Args:
        argv (Sequence[str] | None): Arguments; ``sys.argv[1:]`` when None.

    Returns:
        int: ``0`` on success, ``1`` when a step refused.
    """
    args: argparse.Namespace = parse_args(sys.argv[1:] if argv is None else argv)
    release = Release(
        root=args.root.resolve(),
        version=args.version.removeprefix("v"),
        dry_run=args.dry_run,
    )
    mode: str = "DRY RUN" if release.dry_run else "PUBLISH"
    print(f"== {mode} {DIST_NAME} {release.tag} from {release.root}")
    for number, (name, step) in enumerate(STEPS, start=1):
        print(f"\n[{number}/{len(STEPS)}] {name}", flush=True)
        try:
            step(release)
        except PublishError as error:
            print(f"\nREFUSED at {name}: {error}", file=sys.stderr)
            return 1
    done: str = (
        "every check passed, nothing uploaded" if release.dry_run else "published"
    )
    print(f"\n== {release.tag}: {done}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
