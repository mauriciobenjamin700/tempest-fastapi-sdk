"""Tests for the ``tempest check-config`` CLI command."""

import sys
import types
from collections.abc import Iterator
from typing import Any

import pytest
from typer.testing import CliRunner

from tempest_fastapi_sdk.cli.main import app

runner = CliRunner()


class Settings:
    def __init__(self, **kwargs: Any) -> None:
        self.__dict__.update(kwargs)


def _install(name: str, settings: Settings) -> None:
    module = types.ModuleType(name)
    module.settings = settings  # type: ignore[attr-defined]
    sys.modules[name] = module


@pytest.fixture
def cleanup_modules() -> Iterator[None]:
    yield
    for name in list(sys.modules):
        if name.startswith("_fake_cfg_"):
            del sys.modules[name]


def test_clean_config_exits_zero(cleanup_modules: None) -> None:
    _install(
        "_fake_cfg_ok",
        Settings(
            TOKEN_SECRET="x" * 40,
            DEBUG=False,
            DATABASE_URL="postgresql+asyncpg://db",
        ),
    )
    result = runner.invoke(app, ["check-config", "--settings", "_fake_cfg_ok:settings"])
    assert result.exit_code == 0
    assert "no issues" in result.stdout


def test_warning_does_not_fail_by_default(cleanup_modules: None) -> None:
    _install("_fake_cfg_warn", Settings(TOKEN_SECRET=""))
    result = runner.invoke(
        app, ["check-config", "--settings", "_fake_cfg_warn:settings"]
    )
    # An empty secret is a WARNING; default fail-level is ERROR → exit 0.
    assert result.exit_code == 0
    assert "TOKEN_SECRET" in result.stdout


def test_fail_level_warning_makes_it_exit_nonzero(cleanup_modules: None) -> None:
    _install("_fake_cfg_warn2", Settings(TOKEN_SECRET=""))
    result = runner.invoke(
        app,
        [
            "check-config",
            "--settings",
            "_fake_cfg_warn2:settings",
            "--fail-level",
            "warning",
        ],
    )
    assert result.exit_code == 1


def test_bad_fail_level_is_rejected(cleanup_modules: None) -> None:
    _install("_fake_cfg_x", Settings())
    result = runner.invoke(
        app,
        ["check-config", "--settings", "_fake_cfg_x:settings", "--fail-level", "nope"],
    )
    assert result.exit_code != 0
