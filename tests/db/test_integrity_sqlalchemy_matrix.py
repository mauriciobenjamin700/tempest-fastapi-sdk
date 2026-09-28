"""``test_integrity_live.py`` under more than one SQLAlchemy.

The package declares ``sqlalchemy[asyncio]>=2.0.52`` with no ceiling, so
a consumer resolves whatever is newest while this repo's lock stays on
the floor. The two disagree about where the Postgres ``DETAIL`` lives:
measured against Postgres 16 with ``asyncpg`` 0.31.0, SQLAlchemy 2.0.52
keeps it in ``str(error.orig)`` and 2.1.1 drops it from there (#367).
The live suite passed on the lock while every unique violation parsed
to ``columns=()`` for anyone who had installed the SDK that month.

Each case reruns the live module in a subprocess with one SQLAlchemy
overlaid on the project environment by ``uv run --with``. Two details
keep that from being vacuous, and both were measured:

* The subprocess runs ``python -m pytest``, never ``pytest``. The
  ``pytest`` script's shebang is the project ``.venv`` interpreter, so
  ``uv run --with sqlalchemy==2.1.1 pytest`` imports 2.0.52 and passes.
* Before the tests run, the overlaid version is printed and compared, so
  a resolution that silently kept the lock's SQLAlchemy fails here.

``--no-sync`` keeps the subprocess from re-syncing ``.venv`` to the
default groups, which would drop the extras ``uv sync --all-extras``
installed.
"""

from __future__ import annotations

import os
import re
import shutil
import subprocess
import tomllib
from pathlib import Path

import pytest

ROOT: Path = Path(__file__).resolve().parents[2]
LIVE_MODULE: str = "tests/db/test_integrity_live.py"
FIRST_MEASURED_21: str = "2.1.1"
"""The first 2.1 release measured to drop ``DETAIL`` from ``str(orig)``."""


def _declared_floor() -> str:
    """Return the SQLAlchemy floor ``pyproject.toml`` declares.

    Read rather than copied, so raising the floor moves this matrix with
    it instead of leaving it measuring a version nobody can install.

    Returns:
        str: The version after ``>=`` in the base dependency.

    Raises:
        AssertionError: When the base dependencies declare no floor.
    """
    project = tomllib.loads((ROOT / "pyproject.toml").read_text())["project"]
    for requirement in project["dependencies"]:
        match = re.fullmatch(r"sqlalchemy(?:\[[^\]]*\])?>=([\w.]+)", requirement)
        if match:
            return match.group(1)
    raise AssertionError("pyproject.toml declares no sqlalchemy floor")


VERSIONS: tuple[str, ...] = (_declared_floor(), FIRST_MEASURED_21)


def _uv_run(
    version: str, *command: str, env: dict[str, str]
) -> subprocess.CompletedProcess[str]:
    """Run ``command`` with ``sqlalchemy==version`` overlaid on the project.

    Args:
        version (str): The SQLAlchemy version to overlay.
        *command (str): The command, starting with ``python``.
        env (dict[str, str]): The subprocess environment.

    Returns:
        subprocess.CompletedProcess[str]: The finished process.
    """
    return subprocess.run(
        ["uv", "run", "--no-sync", "--with", f"sqlalchemy=={version}", *command],
        cwd=ROOT,
        env=env,
        capture_output=True,
        text=True,
        timeout=240,
    )


@pytest.mark.docker
@pytest.mark.parametrize("version", VERSIONS)
def test_live_integrity_suite_passes_under(version: str) -> None:
    """Every live assertion holds under ``version``, SQLite included.

    Args:
        version (str): The SQLAlchemy version under test.
    """
    if shutil.which("uv") is None:
        pytest.skip("uv not installed")
    if shutil.which("docker") is None:
        pytest.skip("docker CLI not installed")
    if subprocess.run(["docker", "info"], capture_output=True).returncode != 0:
        pytest.skip("docker daemon not reachable")

    index = VERSIONS.index(version)
    env = {
        **os.environ,
        "TEMPEST_INTEGRITY_CONTAINER": f"tempest-integrity-matrix-{index}",
        "TEMPEST_INTEGRITY_PORT": str(55440 + index),
    }

    probe = _uv_run(
        version,
        "python",
        "-c",
        "import sqlalchemy; print(sqlalchemy.__version__)",
        env=env,
    )
    assert probe.returncode == 0, probe.stderr
    assert probe.stdout.strip() == version

    run = _uv_run(
        version,
        "python",
        "-m",
        "pytest",
        LIVE_MODULE,
        "-m",
        "docker or not docker",
        "-p",
        "no:cacheprovider",
        "--no-cov",
        "-q",
        env=env,
    )
    assert run.returncode == 0, f"sqlalchemy=={version}\n{run.stdout}\n{run.stderr}"
    assert " passed" in run.stdout
    assert "skipped" not in run.stdout, run.stdout
