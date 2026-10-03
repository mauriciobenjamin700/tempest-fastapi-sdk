"""The generated compose's ``prod`` profile, booted for real.

Opt-in (``make test-docker``): scaffolds a project with ``tempest new``,
copies ``.env.example`` to ``.env`` untouched (so it still points every
backend at ``localhost`` and ``DATABASE_URL`` at SQLite) and runs
``docker compose --profile prod up -d --build --wait``. The claims under
test are the ones a unit test on the YAML text cannot make: the ``api``
service is absent without the profile, the image builds and turns
healthy, ``/health/readiness`` answers ``200`` on the published port, and
the app talks to the compose Postgres rather than the SQLite default the
``.env`` carries.

The build installs ``tempest-fastapi-sdk`` from PyPI (the scaffold pins
``>=`` the running version), so it needs network access and that version
published.

The extras are ``auth,admin,cache,tasks``: ``--extras`` replaces the
``auth,admin`` default instead of adding to it, and the scaffolded
``app.py`` mounts the admin panel, which imports only with ``[admin]``.
"""

from __future__ import annotations

import json
import shutil
import socket
import subprocess
import urllib.request
from collections.abc import Iterator
from pathlib import Path

import pytest

from tempest_fastapi_sdk.cli.new import scaffold

PROJECT: str = "tempest_compose_prod_probe"
API_PORT: int = 58417
INFRA_PORTS: tuple[int, ...] = (5432, 6379, 5672, 15672)
"""Loopback ports the generated infra publishes for ``cache,tasks``."""

pytestmark = [pytest.mark.docker, pytest.mark.timeout(900)]


def _port_busy(port: int) -> bool:
    """Tell whether something already listens on ``127.0.0.1:port``.

    Args:
        port (int): The TCP port to probe.

    Returns:
        bool: ``True`` when a connection succeeds.
    """
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as probe:
        probe.settimeout(0.5)
        return probe.connect_ex(("127.0.0.1", port)) == 0


def _compose(project_dir: Path, *args: str) -> subprocess.CompletedProcess[str]:
    """Run ``docker compose`` inside the scaffolded project.

    Args:
        project_dir (Path): Directory holding ``docker-compose.yaml``.
        *args (str): Arguments after ``docker compose``.

    Returns:
        subprocess.CompletedProcess[str]: The finished process.
    """
    return subprocess.run(
        ["docker", "compose", *args],
        cwd=project_dir,
        capture_output=True,
        text=True,
        timeout=900,
    )


@pytest.fixture(scope="module")
def project_dir(tmp_path_factory: pytest.TempPathFactory) -> Iterator[Path]:
    """Scaffold the project and boot it under the ``prod`` profile.

    Args:
        tmp_path_factory (pytest.TempPathFactory): Source of the parent dir.

    Yields:
        Path: The scaffolded project directory, with the stack running.
    """
    if shutil.which("docker") is None:
        pytest.skip("docker CLI not installed")
    if subprocess.run(["docker", "info"], capture_output=True).returncode != 0:
        pytest.skip("docker daemon not reachable")
    busy = [port for port in (*INFRA_PORTS, API_PORT) if _port_busy(port)]
    if busy:
        pytest.skip(f"ports already in use on 127.0.0.1: {busy}")

    parent = tmp_path_factory.mktemp("compose-prod")
    scaffold(
        name=PROJECT,
        path=str(parent),
        bind_host="127.0.0.1",
        bind_port=API_PORT,
        extras="auth,admin,cache,tasks",
        force=False,
    )
    target = parent / PROJECT
    shutil.copy(target / ".env.example", target / ".env")
    try:
        started = _compose(target, "--profile", "prod", "up", "-d", "--build", "--wait")
        if started.returncode != 0:
            logs = _compose(target, "--profile", "prod", "logs", "--tail", "60")
            pytest.fail(
                f"compose up failed:\n{started.stderr[-3000:]}\n{logs.stdout[-3000:]}"
            )
        yield target
    finally:
        _compose(target, "--profile", "prod", "down", "-v", "--rmi", "local")


def test_api_is_absent_without_the_profile(project_dir: Path) -> None:
    plain = _compose(project_dir, "config", "--services")
    prod = _compose(project_dir, "--profile", "prod", "config", "--services")
    assert "api" not in plain.stdout.split()
    assert "api" in prod.stdout.split()


def test_readiness_answers_200_on_the_published_port(project_dir: Path) -> None:
    url = f"http://127.0.0.1:{API_PORT}/health/readiness"
    with urllib.request.urlopen(url, timeout=10) as response:
        status = response.status
        payload = json.loads(response.read())
    assert status == 200
    assert payload["checks"] == {"database": True}


def test_api_talks_to_the_compose_postgres(project_dir: Path) -> None:
    api_ip = subprocess.run(
        [
            "docker",
            "inspect",
            "-f",
            "{{range .NetworkSettings.Networks}}{{.IPAddress}}{{end}}",
            f"{PROJECT}-api",
        ],
        capture_output=True,
        text=True,
        check=True,
    ).stdout.strip()
    clients = _compose(
        project_dir,
        "exec",
        "-T",
        "postgres",
        "psql",
        "-U",
        "app",
        "-d",
        PROJECT,
        "-tAc",
        f"select client_addr from pg_stat_activity where datname = '{PROJECT}'",
    )
    assert api_ip
    assert api_ip in clients.stdout.split()


def test_sqlite_default_from_env_is_not_used(project_dir: Path) -> None:
    app_dir = subprocess.run(
        ["docker", "exec", f"{PROJECT}-api", "test", "-d", "/app/src"],
        capture_output=True,
    )
    sqlite_file = subprocess.run(
        ["docker", "exec", f"{PROJECT}-api", "test", "-e", "/app/app.db"],
        capture_output=True,
    )
    assert app_dir.returncode == 0
    assert sqlite_file.returncode != 0
