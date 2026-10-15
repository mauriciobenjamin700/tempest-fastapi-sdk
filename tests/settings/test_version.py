"""Tests for :func:`tempest_fastapi_sdk.settings.version.read_project_version`.

The helper is the single source of truth the scaffolded ``Settings``
reads its version from, so its failure modes matter as much as its happy
path: a miss has to name the path it looked at, and a file without
``[project].version`` (including ``dynamic``) has to say what is missing
instead of raising a bare ``KeyError``.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from tempest_fastapi_sdk.settings.version import read_project_version


def _write_pyproject(directory: Path, body: str) -> Path:
    """Write a ``pyproject.toml`` with ``body`` and return its path.

    Args:
        directory (Path): Where to create the file; parents are created.
        body (str): The file contents.

    Returns:
        Path: The written ``pyproject.toml``.
    """
    directory.mkdir(parents=True, exist_ok=True)
    path = directory / "pyproject.toml"
    path.write_text(body, encoding="utf-8")
    return path


def test_reads_version_from_an_explicit_path(tmp_path: Path) -> None:
    """An explicit path is read as is."""
    path = _write_pyproject(tmp_path, '[project]\nname = "demo"\nversion = "1.2.3"\n')
    assert read_project_version(path) == "1.2.3"


def test_walks_up_from_cwd_to_find_pyproject(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """With no argument the nearest ancestor's file wins."""
    _write_pyproject(tmp_path, '[project]\nversion = "4.5.6"\n')
    nested = tmp_path / "a" / "b"
    nested.mkdir(parents=True)
    monkeypatch.chdir(nested)
    assert read_project_version() == "4.5.6"


def test_missing_explicit_path_names_it(tmp_path: Path) -> None:
    """A missing explicit file is reported by its own path."""
    missing = tmp_path / "nope" / "pyproject.toml"
    with pytest.raises(FileNotFoundError) as excinfo:
        read_project_version(missing)
    assert str(missing) in str(excinfo.value)


def test_missing_search_names_every_candidate(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The no-argument miss lists the paths the walk visited."""
    nested = tmp_path / "a" / "b"
    nested.mkdir(parents=True)
    monkeypatch.chdir(nested)
    with pytest.raises(FileNotFoundError) as excinfo:
        read_project_version()
    message = str(excinfo.value)
    assert str(nested / "pyproject.toml") in message
    assert str(tmp_path / "pyproject.toml") in message


def test_missing_project_table_cites_the_file(tmp_path: Path) -> None:
    """A file without ``[project]`` names itself and the key."""
    path = _write_pyproject(tmp_path, '[tool.poetry]\nname = "demo"\n')
    with pytest.raises(ValueError) as excinfo:
        read_project_version(path)
    message = str(excinfo.value)
    assert str(path) in message
    assert "version" in message


def test_missing_version_key_cites_the_file(tmp_path: Path) -> None:
    """A ``[project]`` without ``version`` is a ``ValueError``, not a KeyError."""
    path = _write_pyproject(tmp_path, '[project]\nname = "demo"\n')
    with pytest.raises(ValueError) as excinfo:
        read_project_version(path)
    assert str(path) in str(excinfo.value)


def test_dynamic_version_is_rejected(tmp_path: Path) -> None:
    """``dynamic = ["version"]`` leaves nothing to read; say so."""
    path = _write_pyproject(
        tmp_path, '[project]\nname = "demo"\ndynamic = ["version"]\n'
    )
    with pytest.raises(ValueError) as excinfo:
        read_project_version(path)
    assert str(path) in str(excinfo.value)


def test_non_string_version_is_rejected(tmp_path: Path) -> None:
    """A non-string version is refused with its type named."""
    path = _write_pyproject(tmp_path, "[project]\nversion = 123\n")
    with pytest.raises(ValueError) as excinfo:
        read_project_version(path)
    assert "int" in str(excinfo.value)


def test_exported_from_settings_and_top_level() -> None:
    """The helper reaches consumers through both public packages."""
    import tempest_fastapi_sdk
    from tempest_fastapi_sdk import settings as settings_module

    assert settings_module.read_project_version is read_project_version
    assert tempest_fastapi_sdk.read_project_version is read_project_version
