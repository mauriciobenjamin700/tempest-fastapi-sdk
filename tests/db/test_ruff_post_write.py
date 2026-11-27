"""Regression tests for the ruff post-write hook of generated revisions (#883).

``AlembicHelper.init`` used to write two ``exec`` hooks that spawned
``ruff`` from ``PATH``. On a ``[cli-ops]`` install (no ruff) the
``tempest db revision`` run either died with ``FileNotFoundError`` after the
file was written or, behind a pyenv shim, printed
``pyenv: ruff: command not found`` once per hook. These tests drive the real
CLI in a subprocess, so the hook runs exactly as Alembic spawns it.

Hiding ruff from the subprocess takes two moves: ``PATH`` holds only a
directory the test controls, and a ``sitecustomize`` on ``PYTHONPATH`` makes
``importlib.util.find_spec("ruff")`` answer ``None``, the same answer an
interpreter without the ``ruff`` wheel gives.
"""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

import pytest

from tempest_fastapi_sdk.db import AlembicHelper
from tempest_fastapi_sdk.db._ruff_post_write import main

pytestmark = pytest.mark.skipif(
    sys.platform == "win32", reason="the fake pyenv shim is a POSIX shell script"
)

HIDE_RUFF_SITECUSTOMIZE: str = '''"""Make the ruff wheel invisible to find_spec."""

import importlib.util

_real_find_spec = importlib.util.find_spec


def _find_spec(name, package=None):
    """Answer None for ruff, as an interpreter without the wheel does."""
    if name == "ruff" or name.startswith("ruff."):
        return None
    return _real_find_spec(name, package)


importlib.util.find_spec = _find_spec
'''

PYENV_SHIM: str = """#!/bin/sh
echo "pyenv: ruff: command not found" >&2
exit 127
"""


def _scaffold(root: Path) -> None:
    """Write ``alembic.ini`` + ``alembic/`` under ``root`` with the SDK's init.

    Args:
        root (Path): Project directory that receives the scaffold.
    """
    db_url = f"sqlite+aiosqlite:///{root / 'app.db'}"
    AlembicHelper(config_path=str(root / "alembic.ini"), db_url=db_url).init(
        directory=str(root / "alembic"), db_url=db_url
    )


def _revision(
    root: Path, *, path_dirs: list[Path], hide_ruff: bool
) -> subprocess.CompletedProcess[str]:
    """Run ``tempest db revision -m x --manual`` in a fresh interpreter.

    Args:
        root (Path): Project directory holding ``alembic.ini``.
        path_dirs (list[Path]): The only directories on ``PATH``.
        hide_ruff (bool): Whether the subprocess sees no ``ruff`` wheel.

    Returns:
        subprocess.CompletedProcess[str]: The finished run, output captured.
    """
    env = {key: value for key, value in os.environ.items() if key != "DATABASE_URL"}
    env["PATH"] = os.pathsep.join(str(directory) for directory in path_dirs)
    if hide_ruff:
        site_dir = root / "_hide_ruff"
        site_dir.mkdir()
        (site_dir / "sitecustomize.py").write_text(
            HIDE_RUFF_SITECUSTOMIZE, encoding="utf-8"
        )
        env["PYTHONPATH"] = str(site_dir)
    return subprocess.run(
        [
            sys.executable,
            "-c",
            "from tempest_fastapi_sdk.cli.entrypoint import main; main()",
            "db",
            "revision",
            "-m",
            "x",
            "--manual",
            "--database-url",
            f"sqlite+aiosqlite:///{root / 'app.db'}",
        ],
        cwd=root,
        env=env,
        capture_output=True,
        text=True,
        check=False,
        timeout=120,
    )


def _revision_files(root: Path) -> list[Path]:
    """Return the revision modules Alembic wrote under ``root``.

    Args:
        root (Path): Project directory holding ``alembic/versions``.

    Returns:
        list[Path]: Every ``*.py`` file in ``alembic/versions``.
    """
    return sorted((root / "alembic" / "versions").glob("*.py"))


class TestRevisionWithoutRuff:
    def test_no_ruff_anywhere_creates_revision_without_error(
        self, tmp_path: Path
    ) -> None:
        _scaffold(tmp_path)
        empty_bin = tmp_path / "bin"
        empty_bin.mkdir()

        result = _revision(tmp_path, path_dirs=[empty_bin], hide_ruff=True)
        output = result.stdout + result.stderr

        assert result.returncode == 0, output
        assert "Created revision: x" in output
        assert "command not found" not in output
        assert "FileNotFoundError" not in output
        assert len(_revision_files(tmp_path)) == 1

    def test_broken_pyenv_shim_is_not_run_on_the_revision(self, tmp_path: Path) -> None:
        _scaffold(tmp_path)
        shim_bin = tmp_path / "shims"
        shim_bin.mkdir()
        shim = shim_bin / "ruff"
        shim.write_text(PYENV_SHIM, encoding="utf-8")
        shim.chmod(0o755)

        result = _revision(tmp_path, path_dirs=[shim_bin], hide_ruff=True)
        output = result.stdout + result.stderr

        assert result.returncode == 0, output
        assert "command not found" not in output
        assert len(_revision_files(tmp_path)) == 1


class TestRevisionWithRuff:
    def test_revision_is_formatted_and_fixed(self, tmp_path: Path) -> None:
        """The empty manual revision loses the unused ``op``/``sa`` imports.

        ``ruff check --fix`` removing ``F401`` is the observable proof that
        the hook ran; the interpreter's own ruff wheel is found with an
        empty ``PATH``.
        """
        _scaffold(tmp_path)
        empty_bin = tmp_path / "bin"
        empty_bin.mkdir()

        result = _revision(tmp_path, path_dirs=[empty_bin], hide_ruff=False)
        output = result.stdout + result.stderr

        assert result.returncode == 0, output
        (revision,) = _revision_files(tmp_path)
        text = revision.read_text(encoding="utf-8")
        assert "from alembic import op" not in text
        assert "import sqlalchemy as sa" not in text

    def test_broken_shim_falls_through_to_the_interpreter_ruff(
        self, tmp_path: Path
    ) -> None:
        """A shim first on ``PATH`` does not hide the ruff wheel behind it."""
        _scaffold(tmp_path)
        shim_bin = tmp_path / "shims"
        shim_bin.mkdir()
        shim = shim_bin / "ruff"
        shim.write_text(PYENV_SHIM, encoding="utf-8")
        shim.chmod(0o755)

        result = _revision(tmp_path, path_dirs=[shim_bin], hide_ruff=False)
        output = result.stdout + result.stderr

        assert result.returncode == 0, output
        assert "command not found" not in output
        (revision,) = _revision_files(tmp_path)
        assert "from alembic import op" not in revision.read_text(encoding="utf-8")


class TestMain:
    def test_wrong_argument_count_is_a_usage_error(
        self, capsys: pytest.CaptureFixture[str]
    ) -> None:
        assert main([]) == 2
        assert "usage:" in capsys.readouterr().err
