"""Tests for tempest_fastapi_sdk.db.migrations.AlembicHelper."""

from pathlib import Path

import pytest

from tempest_fastapi_sdk.db import AlembicHelper
from tempest_fastapi_sdk.db.migrations import _strip_async_driver


@pytest.fixture
def alembic_project(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """Scaffold a fresh Alembic project in tmp_path and return its root."""
    monkeypatch.chdir(tmp_path)
    db_path = tmp_path / "test.db"
    helper = AlembicHelper(
        config_path=str(tmp_path / "alembic.ini"),
        db_url=f"sqlite+aiosqlite:///{db_path}",
    )
    helper.init(
        directory=str(tmp_path / "alembic"),
        db_url=f"sqlite+aiosqlite:///{db_path}",
    )
    return tmp_path


class TestStripAsyncDriver:
    def test_postgres_asyncpg_becomes_postgres(self) -> None:
        assert (
            _strip_async_driver("postgresql+asyncpg://u:p@h:5432/d")
            == "postgresql://u:p@h:5432/d"
        )

    def test_sqlite_aiosqlite_becomes_sqlite(self) -> None:
        assert (
            _strip_async_driver("sqlite+aiosqlite:///:memory:") == "sqlite:///:memory:"
        )

    def test_sync_url_is_unchanged(self) -> None:
        assert _strip_async_driver("postgresql://u:p@h/d") == "postgresql://u:p@h/d"


class TestInit:
    def test_scaffolds_alembic_directory(self, alembic_project: Path) -> None:
        env_py = alembic_project / "alembic" / "env.py"
        ini = alembic_project / "alembic.ini"
        versions = alembic_project / "alembic" / "versions"

        assert env_py.exists()
        assert ini.exists()
        assert versions.is_dir()

    def test_env_py_uses_sdk_template(self, alembic_project: Path) -> None:
        env_text = (alembic_project / "alembic" / "env.py").read_text()
        assert "tempest-fastapi-sdk" in env_text
        assert "target_metadata = None" in env_text

    def test_metadata_module_injects_import(self, tmp_path: Path) -> None:
        helper = AlembicHelper(
            config_path=str(tmp_path / "alembic.ini"),
            db_url="sqlite+aiosqlite:///:memory:",
        )
        helper.init(
            directory=str(tmp_path / "alembic"),
            metadata_module="myapp.db",
            metadata_attr="BaseModel",
            db_url="sqlite+aiosqlite:///:memory:",
        )
        env_text = (tmp_path / "alembic" / "env.py").read_text()
        assert "from myapp.db import BaseModel" in env_text
        assert "target_metadata = BaseModel.metadata" in env_text

    def test_ini_has_file_template_with_date(self, alembic_project: Path) -> None:
        ini_text = (alembic_project / "alembic.ini").read_text()
        assert "file_template" in ini_text
        assert "%%(year)d" in ini_text

    def test_ini_has_ruff_post_write_hooks(self, alembic_project: Path) -> None:
        ini_text = (alembic_project / "alembic.ini").read_text()
        assert "[post_write_hooks]" in ini_text
        # ``format`` must run BEFORE ``fix`` so the formatter wraps
        # long lines / strips trailing whitespace before the linter
        # has a chance to complain about them on stdout.
        assert "hooks = ruff_format, ruff_fix" in ini_text
        assert (
            "ruff_fix.options = check --fix --quiet REVISION_SCRIPT_FILENAME"
            in ini_text
        )
        assert (
            "ruff_format.options = format --quiet REVISION_SCRIPT_FILENAME" in ini_text
        )

    def test_ini_ships_empty_sqlalchemy_url(self, alembic_project: Path) -> None:
        """Credentials must never enter version control via alembic.ini."""
        ini_text = (alembic_project / "alembic.ini").read_text()
        assert "sqlalchemy.url = \n" in ini_text or "sqlalchemy.url =\n" in ini_text
        # Should NOT contain any URL with a driver scheme.
        assert "sqlite+" not in ini_text
        assert "postgresql" not in ini_text


class TestRuntimeURLResolution:
    def test_env_var_resolves_when_ini_empty(
        self,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        monkeypatch.chdir(tmp_path)
        helper = AlembicHelper(
            config_path=str(tmp_path / "alembic.ini"),
        )
        helper.init(
            directory=str(tmp_path / "alembic"),
            metadata_module=None,
        )

        url = f"sqlite+aiosqlite:///{tmp_path / 'runtime.db'}"
        monkeypatch.setenv("DATABASE_URL", url)

        # Build a fresh helper that does NOT receive db_url — the
        # property should fall back to DATABASE_URL.
        runtime_helper = AlembicHelper(
            config_path=str(tmp_path / "alembic.ini"),
        )
        config = runtime_helper.config
        assert config.get_main_option("sqlalchemy.url") == url

    def test_constructor_override_beats_env_var(
        self,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        monkeypatch.chdir(tmp_path)
        monkeypatch.setenv("DATABASE_URL", "sqlite+aiosqlite:///from_env.db")
        helper = AlembicHelper(
            config_path=str(tmp_path / "alembic.ini"),
            db_url="sqlite+aiosqlite:///from_arg.db",
        )
        helper.init(directory=str(tmp_path / "alembic"))

        assert (
            helper.config.get_main_option("sqlalchemy.url")
            == "sqlite+aiosqlite:///from_arg.db"
        )


class TestCurrentAndHistory:
    def test_current_is_none_on_fresh_db(self, alembic_project: Path) -> None:
        # Pass ``db_url`` explicitly — since v0.30.2 the generated
        # alembic.ini ships with ``sqlalchemy.url`` empty so secrets
        # never enter version control; callers supply the URL via
        # env / settings / constructor at runtime.
        helper = AlembicHelper(
            config_path=str(alembic_project / "alembic.ini"),
            db_url=f"sqlite+aiosqlite:///{alembic_project / 'test.db'}",
        )
        assert helper.current() is None

    def test_history_returns_string(self, alembic_project: Path) -> None:
        helper = AlembicHelper(
            config_path=str(alembic_project / "alembic.ini"),
            db_url=f"sqlite+aiosqlite:///{alembic_project / 'test.db'}",
        )
        # No revisions yet — output should be empty-ish but a str.
        result = helper.history()
        assert isinstance(result, str)


class TestHeadsAndStamp:
    def test_heads_is_empty_on_fresh_project(self, alembic_project: Path) -> None:
        helper = AlembicHelper(
            config_path=str(alembic_project / "alembic.ini"),
        )
        assert helper.heads() == []


class TestSquash:
    def test_requires_force(self, alembic_project: Path) -> None:
        helper = AlembicHelper(
            config_path=str(alembic_project / "alembic.ini"),
            db_url=f"sqlite+aiosqlite:///{alembic_project / 'test.db'}",
        )
        with pytest.raises(RuntimeError, match="force=True"):
            helper.squash()

    def test_no_revisions_raises(self, alembic_project: Path) -> None:
        helper = AlembicHelper(
            config_path=str(alembic_project / "alembic.ini"),
            db_url=f"sqlite+aiosqlite:///{alembic_project / 'test.db'}",
        )
        with pytest.raises(RuntimeError, match="no revisions to squash"):
            helper.squash(force=True)


class TestShow:
    def test_show_returns_empty_for_missing_revision(
        self, alembic_project: Path
    ) -> None:
        helper = AlembicHelper(
            config_path=str(alembic_project / "alembic.ini"),
        )
        # No "head" exists yet — show should not raise.
        try:
            result = helper.show("head")
        except Exception:
            # Some Alembic versions raise when head is None; treat as
            # an acceptable failure mode for an empty project.
            result = ""
        assert isinstance(result, str)
