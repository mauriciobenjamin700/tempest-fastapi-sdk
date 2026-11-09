"""The release runs on the maintainer's machine, end to end.

Since 2026-10-09 no CI ran the suite: ``make release`` runs ``make check``
before it creates the tag, and dropping that line ships a tag no suite ran.
Since 2026-11-09 no CI runs anything at all: the account was locked for
billing, the v0.346.0 tag reached the remote and nothing published, and the
three workflows (``release-pypi.yml``, ``docs.yml``, ``audit.yml``) and the
Dependabot config were deleted. Publishing is ``make publish``
(``scripts/publish.py``) and the site is ``make docs-deploy``
(``scripts/deploy_docs.py``).

A workflow file that comes back -- restored from history, copied from
another repository -- would quietly bring the dependency back: a disabled
workflow is one click from running again. So the guard refuses any.

Blind spot: the guard reads the Makefile and the tree, not a human who tags
by hand with ``git tag`` or uploads with ``uv publish`` directly.
"""

from __future__ import annotations

import pathlib

ROOT: pathlib.Path = pathlib.Path(__file__).resolve().parent.parent


def target_recipe(makefile: str, target: str) -> str:
    """Extract one target's recipe from the Makefile.

    Args:
        makefile (str): The Makefile's content.
        target (str): The target name.

    Returns:
        str: The lines from ``<target>:`` up to the next blank line.
    """
    return makefile.split(f"\n{target}:", 1)[1].split("\n\n", 1)[0]


def check_runs_before_tag(recipe: str) -> bool:
    """Tell whether ``make check`` runs before ``git tag`` in a recipe.

    Args:
        recipe (str): The ``release`` recipe.

    Returns:
        bool: ``True`` when ``$(MAKE) check`` appears and precedes ``git tag``.
    """
    check: int = recipe.find("$(MAKE) check")
    tag: int = recipe.find('git tag "v$(VERSION)"')
    return check != -1 and tag != -1 and check < tag


def github_actions_files(root: pathlib.Path) -> list[str]:
    """List every file that would make GitHub run something for this repository.

    Args:
        root (pathlib.Path): Repository root.

    Returns:
        list[str]: Root-relative paths of workflow files and Dependabot
        configs, sorted; empty when there are none.
    """
    github: pathlib.Path = root / ".github"
    found: list[pathlib.Path] = []
    workflows: pathlib.Path = github / "workflows"
    if workflows.is_dir():
        found.extend(path for path in workflows.rglob("*") if path.is_file())
    found.extend(
        path
        for name in ("dependabot.yml", "dependabot.yaml")
        if (path := github / name).is_file()
    )
    return sorted(path.relative_to(root).as_posix() for path in found)


class TestMakeReleaseRunsTheSuite:
    """``make release`` is the gate."""

    def test_check_runs_before_the_tag(self) -> None:
        """The committed Makefile runs the suite before tagging."""
        makefile: str = (ROOT / "Makefile").read_text(encoding="utf-8")
        assert check_runs_before_tag(target_recipe(makefile, "release"))

    def test_fires_without_the_check(self) -> None:
        """A recipe that skips straight to the tag is refused."""
        recipe: str = '\t$(MAKE) audit\n\t$(MAKE) smoke\n\tgit tag "v$(VERSION)"'
        assert not check_runs_before_tag(recipe)

    def test_fires_when_check_comes_after_the_tag(self) -> None:
        """Testing after tagging protects nothing."""
        recipe: str = '\tgit tag "v$(VERSION)"\n\t$(MAKE) check'
        assert not check_runs_before_tag(recipe)


class TestNothingRunsOnGitHubActions:
    """No workflow and no Dependabot config live in the repository."""

    def test_the_repository_has_none(self) -> None:
        """The three workflows and ``dependabot.yml`` stay deleted."""
        assert github_actions_files(ROOT) == []

    def test_fires_on_a_restored_workflow(self, tmp_path: pathlib.Path) -> None:
        """The exact file that failed to publish v0.346.0, put back."""
        workflows: pathlib.Path = tmp_path / ".github" / "workflows"
        workflows.mkdir(parents=True)
        (workflows / "release-pypi.yml").write_text("on: push\n", encoding="utf-8")
        assert github_actions_files(tmp_path) == [".github/workflows/release-pypi.yml"]

    def test_fires_on_any_extension_and_on_dependabot(
        self, tmp_path: pathlib.Path
    ) -> None:
        """``.yaml`` runs as well as ``.yml``, and Dependabot runs jobs too."""
        workflows: pathlib.Path = tmp_path / ".github" / "workflows"
        workflows.mkdir(parents=True)
        (workflows / "docs.yaml").write_text("on: push\n", encoding="utf-8")
        (tmp_path / ".github" / "dependabot.yml").write_text(
            "version: 2\n", encoding="utf-8"
        )
        assert github_actions_files(tmp_path) == [
            ".github/dependabot.yml",
            ".github/workflows/docs.yaml",
        ]

    def test_issue_templates_are_not_workflows(self, tmp_path: pathlib.Path) -> None:
        """``.github/`` itself stays: issue and PR templates cost nothing."""
        templates: pathlib.Path = tmp_path / ".github" / "ISSUE_TEMPLATE"
        templates.mkdir(parents=True)
        (templates / "docs.yml").write_text("name: docs\n", encoding="utf-8")
        assert github_actions_files(tmp_path) == []


class TestTheLocalTargetsReplaceTheWorkflows:
    """``make publish`` and ``make docs-deploy`` exist and call their scripts."""

    def test_publish_runs_the_script_and_dry_run_is_opt_in(self) -> None:
        """``DRY_RUN`` is the only thing that adds ``--dry-run``."""
        makefile: str = (ROOT / "Makefile").read_text(encoding="utf-8")
        recipe: str = target_recipe(makefile, "publish")
        assert "scripts/publish.py --version $(VERSION)" in recipe
        assert "$(if $(DRY_RUN),--dry-run)" in recipe
        assert recipe.count("--dry-run") == 1

    def test_docs_deploy_runs_the_script(self) -> None:
        """The site goes to ``gh-pages`` through the script that checks the build."""
        makefile: str = (ROOT / "Makefile").read_text(encoding="utf-8")
        recipe: str = target_recipe(makefile, "docs-deploy")
        assert "scripts/deploy_docs.py" in recipe
        assert "$(if $(DRY_RUN),--dry-run)" in recipe

    def test_release_points_at_publish(self) -> None:
        """The tag is not the end: ``make release`` tells you what comes next."""
        makefile: str = (ROOT / "Makefile").read_text(encoding="utf-8")
        recipe: str = target_recipe(makefile, "release")
        assert "make publish VERSION=$(VERSION)" in recipe
        assert "make docs-deploy" in recipe
