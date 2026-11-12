"""``test_kurrentdb_live.py`` under the declared client floor and a newer client.

The ``kurrentdb`` extra declares ``kurrentdbclient>=<floor>`` with no
ceiling, so a consumer resolves the newest release while this repo's lock
pins one. The store leans on client behaviour the live module asserts —
``WrongCurrentVersionError.actual_version``, ``read_all`` including the
event at ``commit_position``, ``subscribe_to_all`` starting after it, the
include-filter regex construction — so each end of the range reruns the
whole live module.

Same mechanics as ``tests/db/test_integrity_sqlalchemy_matrix.py``: a
subprocess with the version overlaid by ``uv run --no-sync --with``,
``python -m pytest`` (never the ``pytest`` script, whose shebang is the
project interpreter), the overlaid version printed and compared first, and
one container and port per run.
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
LIVE_MODULE: str = "tests/eventsourcing/test_kurrentdb_live.py"
MEASURED_NEWER: str = "1.3.6"
"""The newest client release when the backend shipped (2026-10-31)."""


def _declared_floor() -> str:
    """Return the ``kurrentdbclient`` floor of the ``kurrentdb`` extra.

    Returns:
        str: The version after ``>=``.

    Raises:
        AssertionError: When the extra declares no floor.
    """
    project = tomllib.loads((ROOT / "pyproject.toml").read_text())["project"]
    for requirement in project["optional-dependencies"]["kurrentdb"]:
        match = re.fullmatch(r"kurrentdbclient>=([\w.]+)", requirement)
        if match:
            return match.group(1)
    raise AssertionError("the kurrentdb extra declares no kurrentdbclient floor")


VERSIONS: tuple[str, ...] = (_declared_floor(), MEASURED_NEWER)


def _uv_run(
    version: str, *command: str, env: dict[str, str]
) -> subprocess.CompletedProcess[str]:
    """Run ``command`` with ``kurrentdbclient==version`` overlaid.

    Args:
        version (str): The client version to overlay.
        *command (str): The command, starting with ``python``.
        env (dict[str, str]): The subprocess environment.

    Returns:
        subprocess.CompletedProcess[str]: The finished process.
    """
    return subprocess.run(
        ["uv", "run", "--no-sync", "--with", f"kurrentdbclient=={version}", *command],
        cwd=ROOT,
        env=env,
        capture_output=True,
        text=True,
        timeout=280,
    )


@pytest.mark.docker
@pytest.mark.parametrize("version", VERSIONS)
def test_live_kurrentdb_suite_passes_under(version: str) -> None:
    """Every live assertion holds under ``version`` of the client.

    Args:
        version (str): The ``kurrentdbclient`` version under test.
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
        "TEMPEST_KURRENTDB_CONTAINER": f"tempest-kurrentdb-matrix-{index}",
        "TEMPEST_KURRENTDB_PORT": str(52130 + index),
    }
    env.pop("TEST_KURRENTDB_URI", None)

    probe = _uv_run(
        version,
        "python",
        "-c",
        "import kurrentdbclient; print(kurrentdbclient.__version__)",
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
        "docker",
        "-p",
        "no:cacheprovider",
        "--no-cov",
        "-q",
        env=env,
    )
    assert run.returncode == 0, (
        f"kurrentdbclient=={version}\n{run.stdout}\n{run.stderr}"
    )
    assert " passed" in run.stdout
    assert "skipped" not in run.stdout, run.stdout
