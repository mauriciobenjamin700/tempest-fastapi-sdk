"""Unit tests for the shared settings discovery (#490)."""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

from tempest_fastapi_sdk.utils._settings_discovery import (
    SettingsDiscovery,
    discover_settings,
    ensure_project_on_path,
    project_database_url,
    read_dotenv_key,
    settings_instances,
)

_INSTANCE_MODULE: str = (
    "from tempest_fastapi_sdk.settings import DatabaseSettings\n\n\n"
    "class Settings(DatabaseSettings):\n"
    "    pass\n\n\n"
    'settings = Settings(DATABASE_URL="sqlite+aiosqlite:///./inst.db")\n'
)

_FACTORY_MODULE: str = (
    "from tempest_fastapi_sdk.settings import DatabaseSettings\n\n\n"
    "class Settings(DatabaseSettings):\n"
    "    pass\n\n\n"
    "def get_settings() -> Settings:\n"
    '    return Settings(DATABASE_URL="sqlite+aiosqlite:///./factory.db")\n'
)


def _forget_project_modules() -> None:
    """Drop ``src`` / ``app`` and their submodules from the import cache."""
    for name in list(sys.modules):
        if name in {"src", "app"} or name.startswith(("src.", "app.")):
            del sys.modules[name]


def _write_settings(root: Path, source: str, *, code_root: str = "src") -> None:
    """Write ``core/settings.py`` under the chosen code root.

    Args:
        root (Path): The project root.
        source (str): The module source.
        code_root (str): ``src`` or ``app``.
    """
    package: Path = root / code_root / "core"
    package.mkdir(parents=True)
    (root / code_root / "__init__.py").write_text("", encoding="utf-8")
    (package / "__init__.py").write_text("", encoding="utf-8")
    (package / "settings.py").write_text(source, encoding="utf-8")


class TestReadDotenvKey:
    """``.env`` is a first-class source; parse the ordinary forms."""

    def test_plain_assignment(self, tmp_path: Path) -> None:
        env = tmp_path / ".env"
        env.write_text("DATABASE_URL=sqlite+aiosqlite:///./a.db\n")
        assert read_dotenv_key(env, "DATABASE_URL") == "sqlite+aiosqlite:///./a.db"

    def test_quoted_and_exported(self, tmp_path: Path) -> None:
        env = tmp_path / ".env"
        env.write_text('export DATABASE_URL="postgresql://u:p@h/db"\n')
        assert read_dotenv_key(env, "DATABASE_URL") == "postgresql://u:p@h/db"

    def test_comments_and_other_keys_are_ignored(self, tmp_path: Path) -> None:
        env = tmp_path / ".env"
        env.write_text(
            "# DATABASE_URL=commented\n"
            "OTHER=1\n"
            "DATABASE_URL=sqlite+aiosqlite:///./real.db\n"
        )
        assert read_dotenv_key(env, "DATABASE_URL") == "sqlite+aiosqlite:///./real.db"

    def test_missing_file_reads_none(self, tmp_path: Path) -> None:
        assert read_dotenv_key(tmp_path / ".env", "DATABASE_URL") is None

    def test_empty_assignment_reads_none(self, tmp_path: Path) -> None:
        env = tmp_path / ".env"
        env.write_text("DATABASE_URL=\n")
        assert read_dotenv_key(env, "DATABASE_URL") is None


class TestSettingsInstances:
    """A ``BaseSettings`` instance is found by type, name is a preference."""

    def test_preferred_name_wins(self) -> None:
        from types import SimpleNamespace

        from tempest_fastapi_sdk.settings import DatabaseSettings

        other = DatabaseSettings(DATABASE_URL="sqlite+aiosqlite:///./other.db")
        preferred = DatabaseSettings(DATABASE_URL="sqlite+aiosqlite:///./main.db")
        module = SimpleNamespace(config=other, settings=preferred)
        found = settings_instances(module)
        assert found[0] is preferred

    def test_instance_found_without_a_conventional_name(self) -> None:
        from types import SimpleNamespace

        from tempest_fastapi_sdk.settings import DatabaseSettings

        value = DatabaseSettings(DATABASE_URL="sqlite+aiosqlite:///./x.db")
        module = SimpleNamespace(app_settings=value)
        assert settings_instances(module) == [value]


class TestDiscoverSettings:
    """Discovery is probed under both code roots, instance before factory."""

    @pytest.fixture(autouse=True)
    def _clean(self) -> None:
        _forget_project_modules()

    @pytest.mark.parametrize("code_root", ["src", "app"])
    def test_factory_is_discovered(self, tmp_path: Path, code_root: str) -> None:
        _write_settings(tmp_path, _FACTORY_MODULE, code_root=code_root)
        discovery = discover_settings(tmp_path)
        assert discovery.has_settings_file is True
        assert len(discovery.candidates) == 1
        assert (
            discovery.candidates[0].DATABASE_URL == "sqlite+aiosqlite:///./factory.db"
        )

    def test_instance_outranks_the_factory(self, tmp_path: Path) -> None:
        _write_settings(tmp_path, _INSTANCE_MODULE + "\n" + _FACTORY_MODULE)
        discovery = discover_settings(tmp_path)
        assert discovery.candidates[0].DATABASE_URL == "sqlite+aiosqlite:///./inst.db"

    def test_missing_file_reports_nothing(self, tmp_path: Path) -> None:
        discovery = discover_settings(tmp_path)
        assert discovery == SettingsDiscovery()

    def test_a_useless_module_is_noted_but_the_file_counts(
        self,
        tmp_path: Path,
    ) -> None:
        _write_settings(tmp_path, "VALUE = 1\n")
        discovery = discover_settings(tmp_path)
        assert discovery.has_settings_file is True
        assert discovery.candidates == []
        assert any(
            "no pydantic_settings.BaseSettings" in note for note in discovery.notes
        )

    def test_an_import_error_is_noted(self, tmp_path: Path) -> None:
        _write_settings(tmp_path, 'raise RuntimeError("boom")\n')
        discovery = discover_settings(tmp_path)
        assert discovery.has_settings_file is True
        assert any("boom" in note for note in discovery.notes)


class TestProjectDatabaseUrl:
    """The settings URL beats ``.env``; a useless module still reads ``.env``."""

    @pytest.fixture(autouse=True)
    def _clean(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        _forget_project_modules()
        monkeypatch.chdir(tmp_path)

    def test_settings_url_wins_over_dotenv(self, tmp_path: Path) -> None:
        _write_settings(tmp_path, _INSTANCE_MODULE)
        (tmp_path / ".env").write_text("DATABASE_URL=sqlite+aiosqlite:///./raw.db\n")
        assert project_database_url(tmp_path) == "sqlite+aiosqlite:///./inst.db"

    def test_dotenv_is_the_fallback(self, tmp_path: Path) -> None:
        _write_settings(tmp_path, "VALUE = 1\n")
        (tmp_path / ".env").write_text("DATABASE_URL=sqlite+aiosqlite:///./raw.db\n")
        assert project_database_url(tmp_path) == "sqlite+aiosqlite:///./raw.db"

    def test_no_settings_url_is_noted(self, tmp_path: Path) -> None:

        _write_settings(
            tmp_path,
            "from tempest_fastapi_sdk.settings import DatabaseSettings\n"
            "settings = DatabaseSettings(DATABASE_URL='')\n",
        )
        notes: list[str] = []
        assert project_database_url(tmp_path, notes=notes) is None
        assert any("no non-empty DATABASE_URL" in note for note in notes)

    def test_nothing_anywhere_is_none(self, tmp_path: Path) -> None:
        assert project_database_url(tmp_path) is None


class TestEnsureProjectOnPath:
    """The root is moved to the front, never duplicated."""

    def test_root_is_first(self, tmp_path: Path) -> None:
        ensure_project_on_path(tmp_path)
        ensure_project_on_path(tmp_path)
        assert sys.path[0] == str(tmp_path.resolve())
        assert sys.path.count(str(tmp_path.resolve())) == 1
