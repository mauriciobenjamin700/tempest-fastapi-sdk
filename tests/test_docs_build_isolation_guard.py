"""The docs build never touches the environment the suite runs in.

``built_site`` rebuilds a stale ``site/`` with ``uv run``. Without
``--isolated`` that ``uv run`` syncs the project environment it inherits —
under ``make test-matrix`` the matrix venv — and, with ``UV_PYTHON`` set to
another Python, recreated it mid-run: the 3.13 matrix reported 637 failures
and 2142 errors, every one a module deleted from under a running worker.
"""

from __future__ import annotations

from tests.conftest import DOCS_BUILD_COMMAND


def test_the_docs_build_runs_isolated() -> None:
    assert DOCS_BUILD_COMMAND[:3] == ["uv", "run", "--isolated"]


def test_the_guard_fires_on_the_shape_that_shipped() -> None:
    """The command as it was: a plain ``uv run`` that syncs the test venv."""
    shipped = ["uv", "run", "--group", "docs", "mkdocs", "build", "--strict", "-q"]
    assert shipped[:3] != ["uv", "run", "--isolated"]
