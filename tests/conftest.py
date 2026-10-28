"""Shared pytest fixtures."""

import shutil
import subprocess
from collections.abc import AsyncGenerator, Iterator
from pathlib import Path

import pytest
import pytest_asyncio
from sqlalchemy.ext.asyncio import AsyncSession

from tempest_fastapi_sdk.db.connection import AsyncDatabaseManager
from tests._thread_guard import aiosqlite_threads, leaked_since

ROOT = Path(__file__).resolve().parent.parent


@pytest.fixture(autouse=True)
def _no_leaked_aiosqlite_threads(request: pytest.FixtureRequest) -> Iterator[None]:
    """Fail the test that leaves an ``aiosqlite`` worker thread running.

    Requested first by every test, so it tears down last — after the
    test's own fixtures had their chance to dispose their engines. See
    :mod:`tests._thread_guard` for why a leak is a defect, not noise
    (#650).

    A test marked ``keeps_aiosqlite_threads`` is skipped: a module- or
    session-scoped fixture keeps its connection open across tests on
    purpose and checks for leaked threads at its own teardown (the
    ``scaffolded`` fixture of ``tests/cli/test_scaffold_runtime.py``).

    Args:
        request (pytest.FixtureRequest): The requesting test.

    Yields:
        None: Control to the test.
    """
    if request.node.get_closest_marker("keeps_aiosqlite_threads"):
        yield
        return
    before = aiosqlite_threads()
    yield
    leaked = leaked_since(before)
    if leaked:
        pytest.fail(
            f"{len(leaked)} aiosqlite worker thread(s) still running after "
            "teardown: dispose the AsyncEngine (await engine.dispose()) or "
            "disconnect the AsyncDatabaseManager the test opened",
            pytrace=False,
        )


def _newest_docs_input_mtime() -> float:
    """Return the mtime of the newest input a docs build depends on.

    The package source counts as an input: ``mkdocstrings`` renders the
    reference from docstrings, so exporting a new symbol changes the built
    HTML without touching anything under ``docs/``. Leaving it out made the
    reference guard read a stale page and report brand-new exports as
    undocumented — a false failure that costs more than the rebuild.

    Returns:
        float: Epoch seconds of the most recently touched file among
        ``mkdocs.yml``, ``docs/``, ``mkdocs_hooks/`` and the package source.
    """
    newest = (ROOT / "mkdocs.yml").stat().st_mtime
    for directory in ("docs", "mkdocs_hooks"):
        for path in (ROOT / directory).rglob("*"):
            if path.is_file():
                newest = max(newest, path.stat().st_mtime)
    for path in (ROOT / "tempest_fastapi_sdk").rglob("*.py"):
        newest = max(newest, path.stat().st_mtime)
    return newest


DOCS_BUILD_COMMAND: list[str] = [
    "uv",
    "run",
    "--isolated",
    "--group",
    "docs",
    "mkdocs",
    "build",
    "--strict",
    "-q",
]
"""How :func:`built_site` builds the site, isolated from the test environment."""


@pytest.fixture(scope="session")
def built_site() -> Path:
    """Build the docs once per session and return the site directory.

    An existing ``site/`` is reused only when it is newer than every docs
    input. Trusting it unconditionally made a stale local build fail the
    ``docs``-marked tests with a list of "missing" pages that were in fact
    present — a false regression report that costs far more than the rebuild
    it saves. CI never hit it because CI starts with no ``site/`` at all.

    The build runs in an ephemeral environment (``uv run --isolated``), never
    in the one the suite is running in. A plain ``uv run`` syncs the project
    environment it inherits: under ``make test-matrix`` that is the matrix
    venv, and with ``UV_PYTHON`` pointing at another Python it recreated the
    3.13 venv as 3.12 with only the default groups in the middle of the run —
    every other worker lost ``aiosqlite`` and the extras, and the matrix
    reported 637 failures and 2142 errors that the code did not have.

    Returns:
        Path: The built ``site/`` directory.
    """
    if shutil.which("uv") is None:  # pragma: no cover - environment-dependent
        pytest.skip("uv is required to build the documentation")
    site = ROOT / "site"
    stamp = site / "llms.txt"
    fresh = stamp.exists() and stamp.stat().st_mtime >= _newest_docs_input_mtime()
    if not fresh:
        completed = subprocess.run(
            DOCS_BUILD_COMMAND,
            cwd=ROOT,
            capture_output=True,
            text=True,
            check=False,
        )
        if completed.returncode != 0:  # pragma: no cover - build failure
            pytest.fail(f"mkdocs build failed:\n{completed.stdout}{completed.stderr}")
    return site


@pytest_asyncio.fixture
async def db() -> AsyncGenerator[AsyncDatabaseManager]:
    """Yield a fresh in-memory SQLite database for each test."""
    manager = AsyncDatabaseManager("sqlite+aiosqlite:///:memory:")
    await manager.connect()
    await manager.create_tables()
    try:
        yield manager
    finally:
        await manager.drop_tables()
        await manager.disconnect()


@pytest_asyncio.fixture
async def session(
    db: AsyncDatabaseManager,
) -> AsyncGenerator[AsyncSession]:
    """Yield a managed AsyncSession bound to the in-memory database."""
    async with db.get_session_context() as session:
        yield session
