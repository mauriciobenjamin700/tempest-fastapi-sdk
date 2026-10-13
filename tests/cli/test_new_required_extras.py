"""The project ``tempest new`` writes must boot with the extras it pins (#389).

The suite runs with every extra installed, so importing a scaffolded app
in-process proves nothing about the ``pyproject.toml`` next to it: a
project pinned to ``[cache,tasks]`` imported fine here and died in the
consumer's venv with ``ImportError: Admin requires the [admin] extra``.

These tests reproduce the consumer's venv without the network: the
generated app runs in a subprocess whose import system refuses every
distribution that only an *unpinned* extra would have installed. The
admin login is exercised too, because ``[auth]`` is reached there
(``PasswordUtils``), not at import time.
"""

from __future__ import annotations

import importlib.metadata
import re
import subprocess
import sys
import textwrap
import tomllib
from pathlib import Path

import pytest
from packaging.requirements import Requirement
from typer.testing import CliRunner

from tempest_fastapi_sdk.cli.main import app as cli_app

runner = CliRunner()

SDK_DIST = "tempest-fastapi-sdk"
"""Distribution name whose extras the scaffold pins."""

_PINNED_EXTRAS = re.compile(r'"tempest-fastapi-sdk(?:\[(?P<extras>[^\]]*)\])?>=')
"""Captures the extras of the SDK requirement in a generated ``pyproject.toml``."""

_EXTRA_MARKER = re.compile(r"""extra\s*==\s*["']([^"']+)["']""")
"""Reads the extra name out of a requirement marker."""


def _canonical(name: str) -> str:
    """Normalize a distribution name the way PEP 503 does.

    Args:
        name (str): A distribution name as written in metadata.

    Returns:
        str: The lower-cased name with runs of ``-``, ``_`` and ``.``
        collapsed to ``-``.
    """
    return re.sub(r"[-_.]+", "-", name).lower()


def _requirements_by_extra() -> dict[str | None, set[str]]:
    """Group the SDK's declared requirements by the extra that pulls them.

    Returns:
        dict[str | None, set[str]]: Canonical distribution names keyed by
        extra; the ``None`` key holds the unconditional dependencies.
    """
    grouped: dict[str | None, set[str]] = {}
    for line in importlib.metadata.requires(SDK_DIST) or []:
        requirement = Requirement(line)
        marker = str(requirement.marker) if requirement.marker else ""
        match = _EXTRA_MARKER.search(marker)
        key = match.group(1) if match else None
        grouped.setdefault(key, set()).add(_canonical(requirement.name))
    return grouped


def _installed_closure(roots: set[str]) -> set[str]:
    """Follow unconditional installed requirements from ``roots``.

    A distribution that an unpinned extra declares may still reach the
    consumer as a dependency of something pinned; it must stay
    importable here too, or the test would fail for a reason the real
    venv does not have.

    Args:
        roots (set[str]): Canonical distribution names to start from.

    Returns:
        set[str]: ``roots`` plus every installed distribution they
        require without an extra marker.
    """
    seen: set[str] = set()
    pending: list[str] = list(roots)
    while pending:
        name = pending.pop()
        if name in seen:
            continue
        seen.add(name)
        try:
            lines = importlib.metadata.requires(name) or []
        except importlib.metadata.PackageNotFoundError:
            continue
        for line in lines:
            requirement = Requirement(line)
            marker = str(requirement.marker) if requirement.marker else ""
            if "extra" in marker:
                continue
            if requirement.marker and not requirement.marker.evaluate():
                continue
            pending.append(_canonical(requirement.name))
    return seen


def _blocked_modules(pinned: set[str], project_deps: set[str]) -> set[str]:
    """Top-level import names a venv with only ``pinned`` extras lacks.

    Args:
        pinned (set[str]): The extras the generated project pins.
        project_deps (set[str]): Canonical names of the project's own
            dependencies (``uvicorn``, ``aiosqlite``), which its venv
            installs regardless of the SDK extras.

    Returns:
        set[str]: Import names provided only by distributions of extras
        the project does not pin.
    """
    grouped = _requirements_by_extra()
    allowed_roots = {_canonical(SDK_DIST)} | grouped.get(None, set()) | project_deps
    for extra in pinned:
        allowed_roots |= grouped.get(extra, set())
    allowed = _installed_closure(allowed_roots)
    missing = {
        name for extra, names in grouped.items() if extra is not None for name in names
    } - allowed
    blocked: set[str] = set()
    for module, distributions in importlib.metadata.packages_distributions().items():
        if {_canonical(d) for d in distributions} <= missing:
            blocked.add(module)
    return blocked


_BOOT_SCRIPT = textwrap.dedent(
    """
    import asyncio
    import importlib.abc
    import sys

    BLOCKED = frozenset({blocked!r})


    class _Blocker(importlib.abc.MetaPathFinder):
        def find_spec(self, fullname, path=None, target=None):
            if fullname.partition(".")[0] in BLOCKED:
                raise ModuleNotFoundError(f"No module named {{fullname!r}}")
            return None


    sys.meta_path.insert(0, _Blocker())
    sys.path.insert(0, {project!r})

    from fastapi.testclient import TestClient
    from sqlalchemy.ext.asyncio import create_async_engine

    from src.core.settings import settings
    from src.db.models import BaseModel
    from src.server import app


    async def _create_tables():
        engine = create_async_engine(settings.DATABASE_URL)
        async with engine.begin() as conn:
            await conn.run_sync(BaseModel.metadata.create_all)
        await engine.dispose()


    asyncio.run(_create_tables())
    with TestClient(app) as client:
        assert client.get("/health/readiness").status_code == 200
        if {admin!r}:
            assert client.get("/admin/login").status_code == 200
            response = client.post(
                "/admin/login",
                data={{"identifier": "nobody@example.com", "password": "wrong-pass"}},
                follow_redirects=False,
            )
            print("login", response.status_code)
        else:
            assert client.get("/admin/login").status_code == 404
            print("no admin")
    """
)
"""Boots the generated app, then submits the admin login once when mounted."""


def _scaffold(tmp_path: Path, extras: str) -> tuple[Path, set[str], set[str]]:
    """Run ``tempest new demo --extras <extras>`` and read what it pinned.

    Args:
        tmp_path (Path): Parent directory for the project.
        extras (str): The ``--extras`` value to pass.

    Returns:
        tuple[Path, set[str], set[str]]: The project root, the pinned SDK
        extras and the canonical names of its other runtime dependencies.
    """
    result = runner.invoke(
        cli_app,
        ["new", "demo", "--path", str(tmp_path), "--extras", extras],
    )
    assert result.exit_code == 0, result.stdout + result.stderr
    project = tmp_path / "demo"
    pyproject = (project / "pyproject.toml").read_text(encoding="utf-8")
    match = _PINNED_EXTRAS.search(pyproject)
    assert match is not None, pyproject
    raw = match.group("extras") or ""
    declared = tomllib.loads(pyproject)["project"]["dependencies"]
    others = {
        _canonical(Requirement(line).name)
        for line in declared
        if not line.startswith(SDK_DIST)
    }
    return project, {part for part in raw.split(",") if part}, others


def _boot(
    project: Path,
    tmp_path: Path,
    blocked: set[str],
    *,
    admin: bool = True,
) -> subprocess.CompletedProcess[str]:
    """Run :data:`_BOOT_SCRIPT` for ``project`` with ``blocked`` unimportable.

    Args:
        project (Path): The generated project root.
        tmp_path (Path): Scratch directory for the database and logs.
        blocked (set[str]): Top-level import names to refuse.
        admin (bool): Whether the project mounts the admin panel, so the
            script logs in once, or must answer ``404`` on ``/admin/login``.

    Returns:
        subprocess.CompletedProcess[str]: The finished interpreter run.
    """
    script = _BOOT_SCRIPT.format(
        blocked=sorted(blocked), project=str(project), admin=admin
    )
    return subprocess.run(
        [sys.executable, "-c", script],
        cwd=project,
        env={
            "PATH": "",
            "DATABASE_URL": f"sqlite+aiosqlite:///{tmp_path / 'boot.db'}",
            "LOG_DIR": str(tmp_path / "logs"),
        },
        capture_output=True,
        text=True,
        timeout=180,
        check=False,
    )


@pytest.mark.parametrize("extras", ["admin,cache,tasks", "admin"])
def test_generated_app_boots_with_only_the_pinned_extras(
    tmp_path: Path,
    extras: str,
) -> None:
    """The app and its admin login run without any unpinned extra."""
    project, pinned, project_deps = _scaffold(tmp_path, extras)
    assert pinned >= {"admin", "auth"}
    completed = _boot(project, tmp_path, _blocked_modules(pinned, project_deps))
    assert completed.returncode == 0, completed.stderr[-2000:]
    assert "login " in completed.stdout


@pytest.mark.parametrize("extras", ["", "postgres"])
def test_a_service_without_users_boots_without_admin_or_auth(
    tmp_path: Path,
    extras: str,
) -> None:
    """``admin`` and ``auth`` are opt-in: no ``UserModel``, panel or JWT (#522).

    ``bcrypt`` and ``jwt`` are blocked as the consumer's venv would lack
    them, and the app still boots.
    """
    project, pinned, project_deps = _scaffold(tmp_path, extras)
    assert not pinned & {"admin", "auth"}
    assert not (project / "src" / "db" / "models" / "user.py").exists()
    for relative in (
        "src/api/app.py",
        "src/core/settings.py",
        "src/db/models/__init__.py",
        ".env.example",
    ):
        text = (project / relative).read_text(encoding="utf-8")
        for absent in ("UserModel", "JWTSettings", "JWT_SECRET", "AdminSite"):
            assert absent not in text, (relative, absent)
    blocked = _blocked_modules(pinned, project_deps)
    assert {"bcrypt", "jwt"} <= blocked
    completed = _boot(project, tmp_path, blocked, admin=False)
    assert completed.returncode == 0, completed.stderr[-2000:]
    assert "no admin" in completed.stdout


_TASKS_IMPORT_SCRIPT = textwrap.dedent(
    """
    import importlib.abc
    import sys

    BLOCKED = frozenset({blocked!r})


    class _Blocker(importlib.abc.MetaPathFinder):
        def find_spec(self, fullname, path=None, target=None):
            if fullname.partition(".")[0] in BLOCKED:
                raise ModuleNotFoundError(f"No module named {{fullname!r}}")
            return None


    sys.meta_path.insert(0, _Blocker())
    sys.path.insert(0, {project!r})

    from src.tasks import broker
    from src.tasks.jobs import example_job

    print("broker", type(broker).__name__, callable(example_job.enqueue))
    """
)
"""Imports the generated task layer the way the API and the worker do."""


def test_tasks_redis_project_imports_its_tasks_without_env_or_rabbitmq(
    tmp_path: Path,
) -> None:
    """``--extras tasks-redis`` boots on Redis, with no ``.env`` at all (#521).

    The mixin's ``TASKIQ_BROKER_URL`` default is an ``amqp://`` URL; a
    project pinned to ``[tasks-redis]`` used to read it, pick RabbitMQ and
    die asking for ``[tasks]``. The module runs with ``taskiq_aio_pika``
    unimportable, as in the consumer's venv, and without any environment
    variable naming the broker.
    """
    project, pinned, project_deps = _scaffold(tmp_path, "tasks-redis")
    assert (project / "src" / "tasks" / "jobs.py").is_file()
    assert not (project / ".env").exists()
    blocked = _blocked_modules(pinned, project_deps)
    assert "taskiq_aio_pika" in blocked
    completed = subprocess.run(
        [
            sys.executable,
            "-c",
            _TASKS_IMPORT_SCRIPT.format(blocked=sorted(blocked), project=str(project)),
        ],
        cwd=project,
        env={"PATH": "", "LOG_DIR": ""},
        capture_output=True,
        text=True,
        timeout=180,
        check=False,
    )
    assert completed.returncode == 0, completed.stderr[-2000:]
    assert "broker RedisStreamBroker" in completed.stdout


@pytest.mark.parametrize(
    ("dropped", "message"),
    [
        ("admin", "Admin requires the [admin] extra"),
        ("auth", "PasswordUtils requires the [auth] extra"),
    ],
)
def test_harness_reproduces_each_missing_required_extra(
    tmp_path: Path,
    dropped: str,
    message: str,
) -> None:
    """Dropping a required extra fails the way the consumer's venv did.

    Guards the harness itself: if the blocker stopped blocking, or the
    login stopped reaching ``PasswordUtils``, the boot test above would
    pass for any pinned set.
    """
    project, pinned, project_deps = _scaffold(tmp_path, "admin,cache,tasks")
    blocked = _blocked_modules(pinned - {dropped}, project_deps)
    completed = _boot(project, tmp_path, blocked)
    assert completed.returncode != 0
    assert message in completed.stderr
