"""Shared pytest fixtures."""

import shutil
import subprocess
from collections.abc import AsyncGenerator
from pathlib import Path

import pytest
import pytest_asyncio
from sqlalchemy.ext.asyncio import AsyncSession

from tempest_fastapi_sdk.db.connection import AsyncDatabaseManager

ROOT = Path(__file__).resolve().parent.parent


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


@pytest.fixture(scope="session")
def built_site() -> Path:
    """Build the docs once per session and return the site directory.

    An existing ``site/`` is reused only when it is newer than every docs
    input. Trusting it unconditionally made a stale local build fail the
    ``docs``-marked tests with a list of "missing" pages that were in fact
    present — a false regression report that costs far more than the rebuild
    it saves. CI never hit it because CI starts with no ``site/`` at all.

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
            ["uv", "run", "--group", "docs", "mkdocs", "build", "--strict", "-q"],
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
