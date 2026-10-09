"""The suite runs locally before every tag, because CI no longer runs it.

Since 2026-10-09 the repository's CI only publishes: the test workflows
(``ci.yml``, ``nightly-model.yml``) are gone and ``release-pypi.yml`` builds,
audits and uploads. That moves the whole safety net to ``make release``,
which must run ``make check`` before it creates the tag -- drop that line
and a tag ships code no suite ran, with nothing anywhere going red.

Blind spot: the guard reads the Makefile and the workflow files, not a
human who tags by hand with ``git tag``.
"""

from __future__ import annotations

import pathlib

ROOT: pathlib.Path = pathlib.Path(__file__).resolve().parent.parent
WORKFLOWS: pathlib.Path = ROOT / ".github" / "workflows"


def release_recipe(makefile: str) -> str:
    """Extract the recipe of the ``release`` target.

    Args:
        makefile (str): The Makefile's content.

    Returns:
        str: The lines from ``release:`` up to the next blank line.
    """
    return makefile.split("\nrelease:", 1)[1].split("\n\n", 1)[0]


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


class TestMakeReleaseRunsTheSuite:
    """``make release`` is the gate."""

    def test_check_runs_before_the_tag(self) -> None:
        """The committed Makefile runs the suite before tagging."""
        makefile: str = (ROOT / "Makefile").read_text(encoding="utf-8")
        assert check_runs_before_tag(release_recipe(makefile))

    def test_fires_without_the_check(self) -> None:
        """A recipe that skips straight to the tag is refused."""
        recipe: str = '\t$(MAKE) audit\n\t$(MAKE) smoke\n\tgit tag "v$(VERSION)"'
        assert not check_runs_before_tag(recipe)

    def test_fires_when_check_comes_after_the_tag(self) -> None:
        """Testing after tagging protects nothing."""
        recipe: str = '\tgit tag "v$(VERSION)"\n\t$(MAKE) check'
        assert not check_runs_before_tag(recipe)


class TestCiOnlyPublishes:
    """No workflow runs the suite; the release workflow still builds."""

    def test_no_workflow_runs_pytest(self) -> None:
        """A test step creeping back into CI would make the local gate look optional."""
        offending: list[str] = [
            path.name
            for path in sorted(WORKFLOWS.glob("*.yml"))
            if "pytest" in path.read_text(encoding="utf-8")
        ]
        assert offending == []

    def test_release_workflow_builds_and_publishes(self) -> None:
        """The one job CI keeps is still there."""
        text: str = (WORKFLOWS / "release-pypi.yml").read_text(encoding="utf-8")
        assert "run: uv build" in text
        assert "pypa/gh-action-pypi-publish@" in text
