"""How ``tempest db`` finds the database URL.

The resolution shipped accepting exactly one settings-instance name and
reading only ``os.environ``, then erasing the cause with
``except Exception: return None``. Measured against a service that named
its instance ``config`` and kept ``DATABASE_URL`` in ``.env``::

    resolved: None
    import settings -> ImportError: cannot import name 'settings' from
                       'src.core.settings'
    import config: OK -> postgresql+asyncpg://...

The message printed on that failure named the two conditions the project
already satisfied ("set DATABASE_URL, or run inside a project with
src/core/settings.py"), so the reader concluded the bug was elsewhere.

Every case here drives ``_resolve_database_url`` from a real project
tree, because the defect was about what the function does to a tree that
follows the pattern.
"""

import sys
from collections.abc import Iterator
from pathlib import Path

import pytest

from tempest_fastapi_sdk.cli.db import _resolve_database_url

SETTINGS_MODULE: str = '''"""Service settings."""

from tempest_fastapi_sdk.settings import DatabaseSettings


class Settings(DatabaseSettings):
    """Settings for the test service."""


{name} = Settings(DATABASE_URL="postgresql+asyncpg://u:p@localhost/{name}")
'''
"""A settings module whose URL is passed at construction.

Explicit init arguments outrank every other ``pydantic-settings``
source, so a URL that comes back from here provably travelled the
settings path and not the ``.env`` fallback — the two would otherwise
be indistinguishable, since the settings instance reads ``.env`` too.
"""


@pytest.fixture(autouse=True)
def _isolate(monkeypatch: pytest.MonkeyPatch) -> Iterator[None]:
    """Clear the env var and unload project modules between cases.

    ``importlib`` caches by dotted name, so a ``src.core.settings``
    imported from one tmp dir would answer for the next one.

    Args:
        monkeypatch (pytest.MonkeyPatch): Env and path patcher.

    Yields:
        None: Control, with the environment isolated.
    """
    monkeypatch.delenv("DATABASE_URL", raising=False)
    yield
    for name in [
        module for module in sys.modules if module.split(".")[0] in {"src", "app"}
    ]:
        sys.modules.pop(name, None)


def _project(root: Path, *, code_root: str = "src", name: str = "settings") -> None:
    """Write a minimal service tree with a settings module.

    Args:
        root (Path): The project root.
        code_root (str): ``src`` or ``app``.
        name (str): The name bound to the settings instance.
    """
    package: Path = root / code_root / "core"
    package.mkdir(parents=True)
    (root / code_root / "__init__.py").write_text("")
    (package / "__init__.py").write_text("")
    (package / "settings.py").write_text(SETTINGS_MODULE.format(name=name))


class TestPriorityOrder:
    """The flag beats the env var, which beats the project."""

    def test_explicit_flag_wins(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("DATABASE_URL", "sqlite+aiosqlite:///./env.db")
        assert _resolve_database_url("sqlite+aiosqlite:///./flag.db") == (
            "sqlite+aiosqlite:///./flag.db"
        )

    def test_env_var_beats_dotenv(
        self,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """``pydantic-settings`` gives the environment precedence too."""
        (tmp_path / ".env").write_text("DATABASE_URL=sqlite+aiosqlite:///./file.db\n")
        monkeypatch.chdir(tmp_path)
        monkeypatch.setenv("DATABASE_URL", "sqlite+aiosqlite:///./env.db")
        assert _resolve_database_url(None) == "sqlite+aiosqlite:///./env.db"


class TestSettingsInstanceName:
    """The instance's type is the test; its name is a convention."""

    @pytest.mark.parametrize("name", ["settings", "config", "app_settings"])
    def test_any_name_resolves(
        self,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
        name: str,
    ) -> None:
        """``settings`` was the only name accepted; ``config`` raised."""
        _project(tmp_path, name=name)
        monkeypatch.chdir(tmp_path)
        assert _resolve_database_url(None) == (
            f"postgresql+asyncpg://u:p@localhost/{name}"
        )

    def test_app_is_a_code_root_too(
        self,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """The layout rule says the root is ``src`` **or** ``app``."""
        _project(tmp_path, code_root="app", name="config")
        monkeypatch.chdir(tmp_path)
        assert _resolve_database_url(None) == (
            "postgresql+asyncpg://u:p@localhost/config"
        )

    def test_the_settings_instance_outranks_the_dotenv_file(
        self,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """Step 3 before step 4, so the service's own sources decide."""
        _project(tmp_path, name="config")
        (tmp_path / ".env").write_text("DATABASE_URL=sqlite+aiosqlite:///./raw.db\n")
        monkeypatch.chdir(tmp_path)
        assert _resolve_database_url(None) == (
            "postgresql+asyncpg://u:p@localhost/config"
        )


FACTORY_SETTINGS_MODULE: str = '''"""Service settings with a factory, no instance."""

from functools import lru_cache

from tempest_fastapi_sdk.settings import DatabaseSettings


class Settings(DatabaseSettings):
    """Settings for the test service."""


@lru_cache
def get_settings() -> Settings:
    """Build the settings from an explicit URL."""
    return Settings(DATABASE_URL="postgresql+asyncpg://u:p@localhost/factory")
'''
"""A settings module that exposes ``get_settings()`` and no instance.

The shape the SDK documents: a cached accessor instead of a module-level
instance. Before #490 every resolution path looked only for an instance,
so a project written this way found nothing and reported "no
src/core/settings.py" while the file sat in front of the operator.
"""


def _factory_project(root: Path, *, code_root: str = "src") -> None:
    """Write a minimal service tree whose settings module has a factory.

    Args:
        root (Path): The project root.
        code_root (str): ``src`` or ``app``.
    """
    package: Path = root / code_root / "core"
    package.mkdir(parents=True)
    (root / code_root / "__init__.py").write_text("")
    (package / "__init__.py").write_text("")
    (package / "settings.py").write_text(FACTORY_SETTINGS_MODULE)


class TestSettingsFactory:
    """A zero-arg factory is the documented accessor; find it too."""

    @pytest.mark.parametrize("code_root", ["src", "app"])
    def test_get_settings_resolves(
        self,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
        code_root: str,
    ) -> None:
        _factory_project(tmp_path, code_root=code_root)
        monkeypatch.chdir(tmp_path)
        assert _resolve_database_url(None) == (
            "postgresql+asyncpg://u:p@localhost/factory"
        )

    def test_factory_outranks_the_dotenv_file(
        self,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """Step 3 before step 4 still holds for the factory shape."""
        _factory_project(tmp_path)
        (tmp_path / ".env").write_text("DATABASE_URL=sqlite+aiosqlite:///./raw.db\n")
        monkeypatch.chdir(tmp_path)
        assert _resolve_database_url(None) == (
            "postgresql+asyncpg://u:p@localhost/factory"
        )

    def test_a_generic_annotated_factory_resolves(
        self,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """Any zero-arg callable returning ``BaseSettings`` counts."""
        package: Path = tmp_path / "src" / "core"
        package.mkdir(parents=True)
        (tmp_path / "src" / "__init__.py").write_text("")
        (package / "__init__.py").write_text("")
        (package / "settings.py").write_text(
            "from tempest_fastapi_sdk.settings import DatabaseSettings\n\n\n"
            "class Settings(DatabaseSettings):\n"
            "    pass\n\n\n"
            "def build_settings() -> Settings:\n"
            '    return Settings(DATABASE_URL="postgresql+asyncpg://u:p@host/g")\n'
        )
        monkeypatch.chdir(tmp_path)
        assert _resolve_database_url(None) == ("postgresql+asyncpg://u:p@host/g")

    def test_a_raising_factory_falls_back_to_dotenv(
        self,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """One broken accessor does not abort the whole lookup."""
        package: Path = tmp_path / "src" / "core"
        package.mkdir(parents=True)
        (tmp_path / "src" / "__init__.py").write_text("")
        (package / "__init__.py").write_text("")
        (package / "settings.py").write_text(
            "from tempest_fastapi_sdk.settings import DatabaseSettings\n\n\n"
            "class Settings(DatabaseSettings):\n"
            "    pass\n\n\n"
            "def get_settings() -> Settings:\n"
            '    raise RuntimeError("no env")\n'
        )
        (tmp_path / ".env").write_text("DATABASE_URL=sqlite+aiosqlite:///./fall.db\n")
        monkeypatch.chdir(tmp_path)
        assert _resolve_database_url(None) == "sqlite+aiosqlite:///./fall.db"


class TestDotenvFallback:
    """The value lives in ``.env`` in development, and is read there."""

    def test_read_without_any_settings_module(
        self,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        (tmp_path / ".env").write_text(
            "# comment\n\nDATABASE_URL=sqlite+aiosqlite:///./only_dotenv.db\n"
        )
        monkeypatch.chdir(tmp_path)
        assert _resolve_database_url(None) == "sqlite+aiosqlite:///./only_dotenv.db"

    @pytest.mark.parametrize(
        "line",
        [
            'DATABASE_URL="sqlite+aiosqlite:///./q.db"',
            "DATABASE_URL='sqlite+aiosqlite:///./q.db'",
            "export DATABASE_URL=sqlite+aiosqlite:///./q.db",
            "DATABASE_URL = sqlite+aiosqlite:///./q.db",
        ],
    )
    def test_assignment_forms(
        self,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
        line: str,
    ) -> None:
        (tmp_path / ".env").write_text(f"{line}\n")
        monkeypatch.chdir(tmp_path)
        assert _resolve_database_url(None) == "sqlite+aiosqlite:///./q.db"

    def test_another_key_is_not_mistaken_for_it(
        self,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        (tmp_path / ".env").write_text("TEST_DATABASE_URL=sqlite:///./other.db\n")
        monkeypatch.chdir(tmp_path)
        assert _resolve_database_url(None) is None

    def test_empty_value_is_not_a_url(
        self,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        (tmp_path / ".env").write_text("DATABASE_URL=\n")
        monkeypatch.chdir(tmp_path)
        assert _resolve_database_url(None) is None


class TestTheCauseIsReported:
    """``except Exception: return None`` erased the only clue there was."""

    def test_import_failure_is_printed(
        self,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
        capsys: pytest.CaptureFixture[str],
    ) -> None:
        package: Path = tmp_path / "src" / "core"
        package.mkdir(parents=True)
        (tmp_path / "src" / "__init__.py").write_text("")
        (package / "__init__.py").write_text("")
        (package / "settings.py").write_text(
            'raise RuntimeError("DATABASE_URL missing")\n'
        )
        monkeypatch.chdir(tmp_path)

        assert _resolve_database_url(None) is None
        stderr: str = capsys.readouterr().err
        assert "src.core.settings" in stderr
        assert "DATABASE_URL missing" in stderr

    def test_a_module_without_a_settings_instance_says_so(
        self,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
        capsys: pytest.CaptureFixture[str],
    ) -> None:
        package: Path = tmp_path / "src" / "core"
        package.mkdir(parents=True)
        (tmp_path / "src" / "__init__.py").write_text("")
        (package / "__init__.py").write_text("")
        (package / "settings.py").write_text("VALUE = 1\n")
        monkeypatch.chdir(tmp_path)

        assert _resolve_database_url(None) is None
        assert "no pydantic_settings.BaseSettings instance" in (capsys.readouterr().err)

    def test_nothing_is_printed_when_resolution_succeeds(
        self,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
        capsys: pytest.CaptureFixture[str],
    ) -> None:
        """Diagnostics are for the failure, not for every command."""
        (tmp_path / ".env").write_text("DATABASE_URL=sqlite+aiosqlite:///./ok.db\n")
        monkeypatch.chdir(tmp_path)

        assert _resolve_database_url(None) == "sqlite+aiosqlite:///./ok.db"
        assert capsys.readouterr().err == ""


class TestNothingToFind:
    """An empty tree still resolves to ``None``, for ``alembic.ini``."""

    def test_bare_directory(
        self,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        monkeypatch.chdir(tmp_path)
        assert _resolve_database_url(None) is None
