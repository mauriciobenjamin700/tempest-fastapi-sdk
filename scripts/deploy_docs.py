"""Build the bilingual docs site once and push that build to ``gh-pages``.

This replaces ``.github/workflows/docs.yml`` (removed on 2026-11-09, when the
account was locked for billing and the repository stopped depending on
GitHub Actions). ``make docs-deploy`` runs it from a clean checkout of
``origin/main``.

``mkdocs gh-deploy`` would build and push in one call, but then the tree that
reaches the branch is a second build nobody inspected. Here the site is built
once, with the same ``mkdocs build --strict`` as ``make docs-build``, both
locales are checked in that directory, and ``ghp-import`` (the library
``mkdocs gh-deploy`` uses underneath, already in the ``docs`` group) commits
exactly that directory to ``gh-pages``. ``--dry-run`` stops after the checks.
"""

from __future__ import annotations

import argparse
import pathlib
import shlex
import subprocess
import sys
import tempfile
from collections.abc import Sequence

PAGES_BRANCH: str = "gh-pages"
"""Branch GitHub Pages serves from ("Deploy from a branch: gh-pages /")."""

LOCALE_INDEXES: tuple[str, ...] = ("index.html", "en/index.html")
"""Pages both ``mkdocs-static-i18n`` locales must produce.

PT-BR at the site root, EN-US under ``/en/``.
"""

BUILD_COMMAND: tuple[str, ...] = (
    "uv",
    "run",
    "--locked",
    "--all-extras",
    "--group",
    "docs",
    "mkdocs",
    "build",
    "--strict",
)
"""The ``make docs-build`` build, on the locked resolution with every extra.

Every extra because ``mkdocstrings`` imports the modules it documents, which
is what the removed workflow's ``uv sync --locked --all-extras --group docs``
installed.
"""


class DeployError(Exception):
    """A check refused the deploy; the message says why."""


def run(root: pathlib.Path, argv: Sequence[str], *, capture: bool = False) -> str:
    """Run a command in the checkout and stop on failure.

    Args:
        root (pathlib.Path): Repository checkout.
        argv (Sequence[str]): The command.
        capture (bool): Capture and return stdout instead of streaming it.

    Returns:
        str: The stripped standard output when captured, else ``""``.

    Raises:
        DeployError: If the command exits non-zero.
    """
    print(f"$ {shlex.join(argv)}", flush=True)
    completed: subprocess.CompletedProcess[str] = subprocess.run(
        list(argv), cwd=root, capture_output=capture, text=True, check=False
    )
    if completed.returncode != 0:
        raise DeployError(f"`{shlex.join(argv)}` exited {completed.returncode}.")
    return completed.stdout.strip() if capture else ""


def missing_locales(site: pathlib.Path) -> list[str]:
    """List the locale index pages absent from a built site.

    Args:
        site (pathlib.Path): The built site directory.

    Returns:
        list[str]: Each entry of :data:`LOCALE_INDEXES` that was not built.
    """
    return [page for page in LOCALE_INDEXES if not (site / page).is_file()]


def require_main(root: pathlib.Path) -> str:
    """Require a clean tree whose ``HEAD`` is ``origin/main``.

    Args:
        root (pathlib.Path): Repository checkout.

    Returns:
        str: The ``HEAD`` commit, used in the ``gh-pages`` commit message.

    Raises:
        DeployError: If the tree is dirty or ``HEAD`` is not ``origin/main``.
    """
    if run(root, ["git", "status", "--porcelain"], capture=True):
        raise DeployError("Working tree dirty. Deploy from a clean checkout of main.")
    run(root, ["git", "fetch", "--quiet", "origin", "main"])
    head: str = run(root, ["git", "rev-parse", "HEAD"], capture=True)
    main: str = run(root, ["git", "rev-parse", "FETCH_HEAD"], capture=True)
    if head != main:
        raise DeployError(
            f"HEAD is {head[:12]} but origin/main is {main[:12]}. "
            "The site publishes what is on main: push or pull first."
        )
    return head


def deploy(root: pathlib.Path, *, dry_run: bool) -> None:
    """Build the site once, check both locales, and push it to ``gh-pages``.

    Args:
        root (pathlib.Path): Repository checkout.
        dry_run (bool): Build and check; skip the push.

    Raises:
        DeployError: If a check refuses or a command fails.
    """
    head: str = require_main(root)
    with tempfile.TemporaryDirectory(prefix="tempest-docs-site-") as scratch:
        site: pathlib.Path = pathlib.Path(scratch) / "site"
        run(root, [*BUILD_COMMAND, "--site-dir", str(site)])
        missing: list[str] = missing_locales(site)
        if missing:
            raise DeployError(f"The build has no {missing}: a locale did not build.")
        print(
            f"  built {sum(1 for _ in site.rglob('*.html'))} HTML pages, both locales"
        )
        if dry_run:
            print(f"  DRY_RUN: skipping the push to {PAGES_BRANCH}")
            return
        run(
            root,
            [
                "uv",
                "run",
                "--locked",
                "--group",
                "docs",
                "ghp-import",
                "--no-jekyll",
                "--push",
                "--force",
                "--remote",
                "origin",
                "--branch",
                PAGES_BRANCH,
                "--message",
                f"docs: deploy {head[:12]}",
                str(site),
            ],
        )


def main(argv: Sequence[str] | None = None) -> int:
    """Deploy the docs, or explain why not.

    Args:
        argv (Sequence[str] | None): Arguments; ``sys.argv[1:]`` when None.

    Returns:
        int: ``0`` on success, ``1`` when a check refused.
    """
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--root", type=pathlib.Path, default=pathlib.Path.cwd())
    parser.add_argument("--dry-run", action="store_true")
    args: argparse.Namespace = parser.parse_args(
        list(sys.argv[1:] if argv is None else argv)
    )
    try:
        deploy(args.root.resolve(), dry_run=args.dry_run)
    except DeployError as error:
        print(f"\nREFUSED: {error}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
