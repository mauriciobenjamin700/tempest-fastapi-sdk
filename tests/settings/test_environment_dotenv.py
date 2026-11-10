"""``ENV=test`` from outside the ``.env`` stops the ``.env`` being read (#718).

A suite pins ``ENV=test`` so it never inherits the developer's
configuration. Before the fix the ``.env`` in the working directory was
read anyway, and every value the suite did not pin leaked into it.
"""

from __future__ import annotations

from collections.abc import Mapping
from pathlib import Path
from typing import ClassVar

import pytest
from pydantic_settings import SettingsError

from tempest_fastapi_sdk import BaseAppSettings, EnvironmentSettings


class Probe(EnvironmentSettings, BaseAppSettings):
    """Settings with one field the ``.env`` sets.

    Attributes:
        FOO (str): ``"default"`` unless a source sets it.
        RENAMED (str): A field read under a current and a deprecated name.
    """

    DEPRECATED_ENV_ALIASES: ClassVar[Mapping[str, str]] = {"OLD_RENAMED": "RENAMED"}

    FOO: str = "default"
    RENAMED: str = "default"


@pytest.fixture(autouse=True)
def project_dir(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """Run each test in a fresh directory with no ``ENV``/``FOO`` exported.

    Args:
        tmp_path (Path): Per-test directory.
        monkeypatch (pytest.MonkeyPatch): Environment patcher.

    Returns:
        Path: The working directory the ``.env`` is written to.
    """
    monkeypatch.chdir(tmp_path)
    for name in ("ENV", "FOO", "RENAMED", "OLD_RENAMED"):
        monkeypatch.delenv(name, raising=False)
    return tmp_path


def write_dotenv(directory: Path, env: str) -> None:
    """Write a ``.env`` that sets ``ENV`` and ``FOO``.

    Args:
        directory (Path): Where the file goes.
        env (str): The ``ENV`` value written in the file.
    """
    (directory / ".env").write_text(f"ENV={env}\nFOO=from-dotenv\n")


class TestTestEnvironmentSkipsDotenv:
    def test_env_var_test_ignores_the_dotenv(
        self,
        project_dir: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        write_dotenv(project_dir, "development")
        monkeypatch.setenv("ENV", "test")
        settings = Probe()
        assert settings.ENV == "test"
        assert settings.FOO == "default"

    def test_constructor_test_ignores_the_dotenv(self, project_dir: Path) -> None:
        write_dotenv(project_dir, "development")
        settings = Probe(ENV="test")
        assert settings.FOO == "default"

    def test_constructor_wins_over_the_environment(
        self,
        project_dir: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        write_dotenv(project_dir, "development")
        monkeypatch.setenv("ENV", "test")
        settings = Probe(ENV="development")
        assert settings.ENV == "development"
        assert settings.FOO == "from-dotenv"

    def test_process_environment_still_applies(
        self,
        project_dir: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        write_dotenv(project_dir, "development")
        monkeypatch.setenv("ENV", "test")
        monkeypatch.setenv("FOO", "from-environment")
        assert Probe().FOO == "from-environment"

    def test_alias_clash_in_the_environment_still_refuses(
        self,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        monkeypatch.setenv("ENV", "test")
        monkeypatch.setenv("RENAMED", "a")
        monkeypatch.setenv("OLD_RENAMED", "b")
        with pytest.raises(SettingsError, match="RENAMED and OLD_RENAMED"):
            Probe()


class TestOtherEnvironmentsReadDotenv:
    @pytest.mark.parametrize("env", ["development", "production"])
    def test_non_test_env_var_reads_the_dotenv(
        self,
        env: str,
        project_dir: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        write_dotenv(project_dir, "development")
        monkeypatch.setenv("ENV", env)
        settings = Probe()
        assert env == settings.ENV
        assert settings.FOO == "from-dotenv"

    def test_test_written_only_in_the_dotenv_keeps_the_file(
        self,
        project_dir: Path,
    ) -> None:
        write_dotenv(project_dir, "test")
        settings = Probe()
        assert settings.ENV == "test"
        assert settings.FOO == "from-dotenv"
