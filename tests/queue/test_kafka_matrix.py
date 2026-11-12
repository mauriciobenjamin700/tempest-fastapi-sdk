"""``test_kafka_live.py`` under more than one FastStream and aiokafka.

``[queue-kafka]`` declares ``faststream[kafka]>=<floor>`` with no ceiling,
so the lock measures the floor and a consumer installs the newest. The
facade reads FastStream's Kafka surface by name — ``group_id``,
``auto_offset_reset``, ``key``, the ``message_id`` it lacks and the
``headers`` it has — so a release that moves any of them breaks the
facade for new installs while the lock stays green.

The floor case also overlays the declared ``aiokafka`` floor. FastStream
itself accepts ``aiokafka>=0.9``, and under 0.9.0 every subscriber fails
to start (``AIOKafkaConsumer.__init__() got an unexpected keyword argument
'group_instance_id'``) — which is why the extra declares its own floor.

Same mechanics as ``tests/db/test_integrity_sqlalchemy_matrix.py``: the
live module reruns in a subprocess with ``uv run --no-sync --with``, as
``python -m pytest`` (the ``pytest`` script's shebang would import the
lock's versions), after a probe confirms the overlaid versions.
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
LIVE_MODULE: str = "tests/queue/test_kafka_live.py"
NEWER: str = "0.7.8"
"""The newest FastStream release when the Kafka facade was measured."""


def _declared_floor(package: str) -> str:
    """Return the floor the ``[queue-kafka]`` extra declares for ``package``.

    Read rather than copied, so raising a floor moves this matrix with it.

    Args:
        package (str): ``"faststream"`` or ``"aiokafka"``.

    Returns:
        str: The version after ``>=``.

    Raises:
        AssertionError: When the extra declares no floor for it.
    """
    extras = tomllib.loads((ROOT / "pyproject.toml").read_text())["project"][
        "optional-dependencies"
    ]
    for requirement in extras["queue-kafka"]:
        match = re.fullmatch(rf"{package}(?:\[[^\]]*\])?>=([\w.]+)", requirement)
        if match:
            return match.group(1)
    raise AssertionError(f"[queue-kafka] declares no {package} floor")


CASES: tuple[dict[str, str], ...] = (
    {
        "faststream": _declared_floor("faststream"),
        "aiokafka": _declared_floor("aiokafka"),
    },
    {"faststream": NEWER},
)
"""Each case pins these packages; everything else comes from the lock."""


def _uv_run(
    case: dict[str, str], *command: str, env: dict[str, str]
) -> subprocess.CompletedProcess[str]:
    """Run ``command`` with the case's versions overlaid on the project.

    Args:
        case (dict[str, str]): Package to version.
        *command (str): The command, starting with ``python``.
        env (dict[str, str]): The subprocess environment.

    Returns:
        subprocess.CompletedProcess[str]: The finished process.
    """
    overlays: list[str] = []
    for package, version in case.items():
        overlays += ["--with", f"{package}=={version}"]
    return subprocess.run(
        ["uv", "run", "--no-sync", *overlays, *command],
        cwd=ROOT,
        env=env,
        capture_output=True,
        text=True,
        timeout=280,
    )


@pytest.mark.docker
@pytest.mark.parametrize(
    "case",
    CASES,
    ids=[",".join(f"{k}={v}" for k, v in case.items()) for case in CASES],
)
def test_live_kafka_suite_passes_under(case: dict[str, str]) -> None:
    """Every live Kafka assertion holds under the case's versions.

    Args:
        case (dict[str, str]): The package versions under test.
    """
    if shutil.which("uv") is None:
        pytest.skip("uv not installed")
    if shutil.which("docker") is None:
        pytest.skip("docker CLI not installed")
    if subprocess.run(["docker", "info"], capture_output=True).returncode != 0:
        pytest.skip("docker daemon not reachable")

    index = CASES.index(case)
    env = {
        **os.environ,
        "TEMPEST_KAFKA_CONTAINER": f"tempest-kafka-matrix-{index}",
        "TEMPEST_KAFKA_PORT": str(59190 + index),
    }

    probe = _uv_run(
        case,
        "python",
        "-c",
        "from importlib.metadata import version; "
        f"print(*(version(p) for p in {list(case)!r}))",
        env=env,
    )
    assert probe.returncode == 0, probe.stderr
    assert probe.stdout.split() == list(case.values())

    run = _uv_run(
        case,
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
    assert run.returncode == 0, f"{case}\n{run.stdout}\n{run.stderr}"
    assert " passed" in run.stdout
    assert "skipped" not in run.stdout, run.stdout
