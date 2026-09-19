"""Tests for ``tempest integrations``.

The verification call per provider is a hand-kept table, so the guard
that matters is the one asserting each named method still exists on the
generated client: a provider renaming an endpoint has to fail here, not
in an operator's terminal.
"""

from __future__ import annotations

import importlib
import sys
from pathlib import Path
from typing import Any

import pytest
from typer.testing import CliRunner

from tempest_fastapi_sdk.cli.integrations import INTEGRATIONS
from tempest_fastapi_sdk.cli.main import app

runner = CliRunner()

_OPENPIX_SETTINGS = """
from tempest_fastapi_sdk.settings import OpenPixSettings, ServerSettings


class Settings(ServerSettings, OpenPixSettings):
    pass


settings = Settings()
"""

_BARE_SETTINGS = """
from tempest_fastapi_sdk.settings import ServerSettings


class Settings(ServerSettings):
    pass


settings = Settings()
"""


def _write_project(root: Path, source: str) -> None:
    """Write a project whose settings module holds ``source``.

    Args:
        root (Path): Project root.
        source (str): Body of ``src/core/settings.py``.
    """
    (root / "src" / "core").mkdir(parents=True, exist_ok=True)
    (root / "src" / "__init__.py").write_text("", encoding="utf-8")
    (root / "src" / "core" / "__init__.py").write_text("", encoding="utf-8")
    (root / "src" / "core" / "settings.py").write_text(source, encoding="utf-8")


@pytest.fixture(autouse=True)
def forget_project_modules() -> None:
    """Drop the tmp project's package and restore ``sys.path``."""
    original_path = list(sys.path)
    for name in [n for n in sys.modules if n == "src" or n.startswith("src.")]:
        del sys.modules[name]
    yield
    for name in [n for n in sys.modules if n == "src" or n.startswith("src.")]:
        del sys.modules[name]
    sys.path[:] = original_path


class TestTableIntegrity:
    def test_every_verify_method_exists_on_its_client(self) -> None:
        """The table names endpoints on generated clients; drift fails here."""
        for integration in INTEGRATIONS:
            if not integration.verify_method:
                continue
            module_name, _, class_name = integration.import_path.partition(":")
            client = getattr(importlib.import_module(module_name), class_name)
            assert hasattr(client, integration.verify_method), (
                f"{integration.name}: {class_name} has no {integration.verify_method}()"
            )

    def test_every_verify_method_takes_no_argument(self) -> None:
        """A verification call that needs an id is not a verification call."""
        import inspect

        for integration in INTEGRATIONS:
            if not integration.verify_method:
                continue
            module_name, _, class_name = integration.import_path.partition(":")
            client = getattr(importlib.import_module(module_name), class_name)
            signature = inspect.signature(getattr(client, integration.verify_method))
            required = [
                parameter
                for name, parameter in signature.parameters.items()
                if name != "self" and parameter.default is inspect.Parameter.empty
            ]
            assert not required, f"{integration.name}: {required}"

    def test_every_settings_mapper_exists(self) -> None:
        from tempest_fastapi_sdk import settings as settings_module

        for integration in INTEGRATIONS:
            if not integration.kwargs_mapper:
                continue
            mixin = getattr(settings_module, integration.settings_mixin)
            assert hasattr(mixin, integration.kwargs_mapper)
            assert integration.credential_field in mixin.model_fields


class TestList:
    def test_lists_every_bundled_client(self, tmp_path: Path, monkeypatch: Any) -> None:
        monkeypatch.chdir(tmp_path)
        result = runner.invoke(app, ["integrations", "list"])
        assert result.exit_code == 0, result.stdout + result.stderr
        for integration in INTEGRATIONS:
            assert integration.name in result.stdout

    def test_reports_the_credential_state(
        self,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        _write_project(tmp_path, _OPENPIX_SETTINGS)
        monkeypatch.chdir(tmp_path)
        monkeypatch.setenv("OPENPIX_APP_ID", "app-id-123")

        result = runner.invoke(app, ["integrations", "list"])
        assert "OPENPIX_APP_ID set" in result.stdout


class TestVerify:
    def test_unknown_name_lists_the_known_ones(
        self,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        monkeypatch.chdir(tmp_path)
        result = runner.invoke(app, ["integrations", "verify", "paypal"])
        assert result.exit_code == 2
        assert "openpix" in result.stderr

    def test_project_without_the_mixin_exits_two(
        self,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        _write_project(tmp_path, _BARE_SETTINGS)
        monkeypatch.chdir(tmp_path)
        result = runner.invoke(app, ["integrations", "verify", "openpix"])
        assert result.exit_code == 2
        assert "OpenPixSettings" in result.stderr

    def test_empty_credential_exits_two(
        self,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        _write_project(tmp_path, _OPENPIX_SETTINGS)
        monkeypatch.chdir(tmp_path)
        monkeypatch.setenv("OPENPIX_APP_ID", "")
        result = runner.invoke(app, ["integrations", "verify", "openpix"])
        assert result.exit_code == 2
        assert "empty" in result.stderr

    def test_integration_without_a_mixin_needs_a_base_url(
        self,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        monkeypatch.chdir(tmp_path)
        result = runner.invoke(app, ["integrations", "verify", "zap"])
        assert result.exit_code == 2
        assert "--base-url" in result.stderr

    def test_provider_refusal_exits_one(
        self,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """A wrong key has to read as the provider's refusal, not a traceback."""
        _write_project(tmp_path, _OPENPIX_SETTINGS)
        monkeypatch.chdir(tmp_path)
        monkeypatch.setenv("OPENPIX_APP_ID", "definitely-not-a-key")

        class _Boom:
            """Client whose verification call always refuses."""

            def __init__(self, http: Any) -> None:
                self.http = http

            async def get_company(self) -> None:
                """Raise the way an unauthorized call does."""
                message = "401 Unauthorized"
                raise RuntimeError(message)

        monkeypatch.setattr(
            "tempest_fastapi_sdk.integrations.payment.openpix.OpenPixClient",
            _Boom,
        )
        result = runner.invoke(
            app,
            ["integrations", "verify", "openpix", "--timeout", "1"],
        )
        assert result.exit_code == 1
        assert "401 Unauthorized" in result.stderr
