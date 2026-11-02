"""Concurrent xdist workers build the docs site once, not once each (#694).

``built_site`` is ``scope="session"``, which pytest-xdist runs per worker.
Every worker that drew a docs test built ``site/`` at the same time, so the
docs tests took 253 s against a 300 s timeout, and a second ``make check``
on the machine pushed them over it: pytest-timeout killed the worker and
the run showed only ``node down: Not properly terminated``.
"""

from __future__ import annotations

import multiprocessing
import time
from pathlib import Path

from tests.conftest import build_once

WORKERS: int = 6


def _worker(directory: str) -> None:
    """Act as one xdist worker: build the output unless it is fresh.

    Args:
        directory (str): Shared directory for the lock, output and log.
    """
    root = Path(directory)
    output = root / "site.stamp"
    log = root / "builds.log"

    def build() -> None:
        with log.open("a") as handle:
            handle.write("build\n")
        time.sleep(0.3)
        output.write_text("done")

    build_once(output.exists, build, root / "build.lock")


def test_concurrent_workers_build_once(tmp_path: Path) -> None:
    context = multiprocessing.get_context("spawn")
    processes = [
        context.Process(target=_worker, args=(str(tmp_path),)) for _ in range(WORKERS)
    ]
    for process in processes:
        process.start()
    for process in processes:
        process.join(timeout=60)

    assert all(process.exitcode == 0 for process in processes)
    assert (tmp_path / "builds.log").read_text().count("build") == 1


def test_a_fresh_output_skips_the_lock(tmp_path: Path) -> None:
    """The common case — a site already built — never waits on the lock."""
    (tmp_path / "site.stamp").write_text("done")

    result = build_once(
        lambda: True,
        lambda: (_ for _ in ()).throw(AssertionError("built")),
        tmp_path / "missing-dir" / "build.lock",
    )

    assert result is None
