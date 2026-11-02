"""Shared pytest fixtures."""

import contextlib
import os
import shutil
import subprocess
from collections.abc import AsyncGenerator, Callable, Iterator
from pathlib import Path

import pytest
import pytest_asyncio
from sqlalchemy.ext.asyncio import AsyncSession

from tempest_fastapi_sdk.db.connection import AsyncDatabaseManager
from tests._thread_guard import aiosqlite_threads, leaked_since

ROOT = Path(__file__).resolve().parent.parent

os.environ["ENV"] = "test"
"""``EnvironmentSettings.ENV`` has no default; the suite pins its own value.

Set at import, before any test module builds a settings class, and never
inherited from the developer's shell. A test about the missing variable
removes it with ``monkeypatch.delenv``.
"""


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

SITE_BUILD_LOCK: Path = ROOT / ".site-build.lock"
"""File the xdist workers lock so only one of them builds ``site/``.

Outside ``site/`` on purpose: ``mkdocs build`` cleans its output directory.
"""


@contextlib.contextmanager
def _exclusive(lock_path: Path) -> Iterator[None]:
    """Hold an exclusive lock on ``lock_path`` across processes.

    ``fcntl.flock`` blocks until the holder releases it, and the kernel
    releases it when the holder dies, so a crashed worker cannot leave the
    others waiting. Where ``fcntl`` does not exist (Windows) the lock is a
    no-op and every worker builds, as before.

    Args:
        lock_path (Path): The lock file, created when missing.

    Yields:
        None: While the lock is held.
    """
    try:
        import fcntl
    except ImportError:  # pragma: no cover - Windows
        yield
        return
    with lock_path.open("a") as handle:
        fcntl.flock(handle, fcntl.LOCK_EX)
        try:
            yield
        finally:
            fcntl.flock(handle, fcntl.LOCK_UN)


def _site_is_fresh(stamp: Path) -> bool:
    """Tell whether the built site is newer than every docs input.

    Args:
        stamp (Path): ``site/llms.txt``, the last file the build writes.

    Returns:
        bool: ``True`` when ``stamp`` exists and is not older than the
        newest input.
    """
    return stamp.exists() and stamp.stat().st_mtime >= _newest_docs_input_mtime()


def build_once(
    is_fresh: Callable[[], bool],
    build: Callable[[], subprocess.CompletedProcess[str] | None],
    lock_path: Path,
) -> subprocess.CompletedProcess[str] | None:
    """Run ``build`` unless the output is fresh, one process at a time.

    Checks freshness before taking the lock (the common case, a site
    already built, never waits) and again after (a worker that waited finds
    what the holder built and skips).

    Args:
        is_fresh (Callable[[], bool]): Whether the output is up to date.
        build (Callable[[], subprocess.CompletedProcess[str] | None]): Builds
            it.
        lock_path (Path): Lock shared by every process that may build.

    Returns:
        subprocess.CompletedProcess[str] | None: What ``build`` returned, or
        ``None`` when nothing was built.
    """
    if is_fresh():
        return None
    with _exclusive(lock_path):
        if is_fresh():
            return None
        return build()


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

    ``scope="session"`` is per **worker** under pytest-xdist, so every
    worker that drew one of these tests used to build the site at once,
    into the same ``site/``. Measured on a fresh worktree with no other
    load: the 26 docs tests took 253 s against the 300 s ``timeout``, with
    up to 32 ``mkdocs build`` processes alive. With a second ``make check``
    competing for the CPU it passed 300 s, and pytest-timeout killed the
    worker — reported only as ``node down: Not properly terminated``,
    because the timeout banner goes to the worker's stderr, which xdist
    does not forward (#694). The workers now take :data:`SITE_BUILD_LOCK`:
    the first one builds, the others wait and find the site fresh.

    Returns:
        Path: The built ``site/`` directory.
    """
    if shutil.which("uv") is None:  # pragma: no cover - environment-dependent
        pytest.skip("uv is required to build the documentation")
    site = ROOT / "site"
    stamp = site / "llms.txt"
    completed = build_once(
        lambda: _site_is_fresh(stamp),
        lambda: subprocess.run(
            DOCS_BUILD_COMMAND,
            cwd=ROOT,
            capture_output=True,
            text=True,
            check=False,
        ),
        SITE_BUILD_LOCK,
    )
    if (
        completed is not None and completed.returncode != 0
    ):  # pragma: no cover - build failure
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
