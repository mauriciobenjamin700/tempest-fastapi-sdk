"""Tests for the ``openapi-errors`` and ``lint-schemas`` steps of ``tempest check``.

Both commands existed and passed on their own, but ``tempest check`` never
ran them (#718): a project could be green on the gate with a route raising
an exception its OpenAPI never declared, or a schema field with no
description. These tests pin that the gate hands both steps to
``tempest-cli``, in order, and what each step answers for a dirty project,
a clean one, a project without the thing it checks and one that switched
the step off.
"""

from __future__ import annotations

import io
from pathlib import Path
from typing import Any

import pytest
from typer.testing import CliRunner

from tempest_fastapi_sdk.cli import check_steps
from tempest_fastapi_sdk.cli.check_steps import (
    LINT_SCHEMAS_CONFIG_KEY,
    LINT_SCHEMAS_ENV_CONFIG_KEY,
    OPENAPI_ERRORS_CONFIG_KEY,
    check_project_root,
    run_lint_schemas_step,
    run_openapi_errors_step,
)
from tempest_fastapi_sdk.cli.main import app as cli_app

runner = CliRunner()

EXCEPTIONS: str = '''"""Domain exceptions."""

from tempest_fastapi_sdk import NotFoundException


class ItemNotFoundException(NotFoundException):
    """Item does not exist."""

    code = "ITEM_NOT_FOUND"
'''

UNDECLARED_ROUTER: str = '''"""Items."""

from fastapi import APIRouter

from src.core.exceptions import ItemNotFoundException

router = APIRouter()


@router.get("/items/{item_id}")
async def get_item(item_id: str) -> dict[str, str]:
    """Look an item up."""
    raise ItemNotFoundException()
'''

DECLARED_ROUTER: str = '''"""Items."""

from fastapi import APIRouter
from tempest_fastapi_sdk import error_responses

from src.core.exceptions import ItemNotFoundException

router = APIRouter()


@router.get("/items/{item_id}", responses=error_responses(ItemNotFoundException))
async def get_item(item_id: str) -> dict[str, str]:
    """Look an item up."""
    raise ItemNotFoundException()
'''

UNDOCUMENTED_SCHEMA: str = '''"""Schemas."""

from tempest_fastapi_sdk import BaseSchema


class ItemSchema(BaseSchema):
    """An item."""

    name: str = ""

__all__: list[str] = ["ItemSchema"]
'''

DOCUMENTED_SCHEMA: str = '''"""Schemas."""

from pydantic import Field
from tempest_fastapi_sdk import BaseSchema


class ItemSchema(BaseSchema):
    """An item."""

    name: str = Field(title="Name", description="The name.", examples=["a"])

__all__: list[str] = ["ItemSchema"]
'''


def _write(root: Path, relative: str, content: str) -> None:
    """Write ``content`` to ``root / relative``, creating parents.

    Args:
        root (Path): The project root.
        relative (str): The file path under the root.
        content (str): The file content.
    """
    target = root / relative
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(content, encoding="utf-8")


def _routes_project(root: Path, router: str) -> Path:
    """Build a minimal ``src/`` project with one router.

    Args:
        root (Path): Where to build it.
        router (str): The router module source.

    Returns:
        Path: ``root``.
    """
    _write(root, "pyproject.toml", '[project]\nname = "demo"\n')
    _write(root, "src/core/exceptions.py", EXCEPTIONS)
    _write(root, "src/api/routers/items.py", router)
    return root


def _schemas_project(root: Path, schemas: str) -> Path:
    """Build a minimal ``src/`` project with one schemas package.

    Args:
        root (Path): Where to build it.
        schemas (str): The ``src/schemas/__init__.py`` source.

    Returns:
        Path: ``root``.
    """
    _write(root, "pyproject.toml", '[project]\nname = "demo"\n')
    _write(root, "src/__init__.py", "")
    _write(root, "src/schemas/__init__.py", schemas)
    return root


def _run(step: Any, target: Path) -> tuple[int, str]:
    """Run a step against ``target`` and capture its report.

    Args:
        step (Any): ``run_openapi_errors_step`` or ``run_lint_schemas_step``.
        target (Path): The ``check`` target.

    Returns:
        tuple[int, str]: The exit code and the report.
    """
    stream = io.StringIO()
    code = step(str(target), stream=stream)
    return code, stream.getvalue()


class TestRegistration:
    """``tempest check`` hands the SDK's steps to the shared gate."""

    def test_check_runs_the_three_steps_in_order(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Cenário: ``tempest check`` com o gate do ``tempest-cli`` trocado.

        Esperado: os passos extras chegam na ordem ``lint-layers``,
        ``openapi-errors``, ``lint-schemas``, cada um ligado à sua função.
        """
        import tempest_cli.lint

        seen: dict[str, Any] = {}

        def fake_full_check(target: str, **options: Any) -> int:
            seen["steps"] = list(options["extra_steps"])
            return 0

        monkeypatch.setattr(tempest_cli.lint, "run_full_check", fake_full_check)
        result = runner.invoke(cli_app, ["check", str(tmp_path)])
        assert result.exit_code == 0, result.output
        steps = seen["steps"]
        assert [step.name for step in steps] == [
            "lint-layers",
            "openapi-errors",
            "lint-schemas",
        ]
        assert steps[1].run is check_steps.run_openapi_errors_step
        assert steps[2].run is check_steps.run_lint_schemas_step

    def test_a_failing_step_fails_the_gate(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Cenário: só os passos extras rodam, sobre rota com 404 não declarado.

        Esperado: o gate sai com 1 e o relatório nomeia a rota.
        """
        import tempest_cli.lint

        project = _routes_project(tmp_path, UNDECLARED_ROUTER)

        def only_extra_steps(target: str, **options: Any) -> int:
            for step in options["extra_steps"]:
                if step.name == "lint-layers":
                    continue
                code = int(step.run(target))
                if code:
                    return code
            return 0

        monkeypatch.setattr(tempest_cli.lint, "run_full_check", only_extra_steps)
        result = runner.invoke(cli_app, ["check", str(project)])
        assert result.exit_code == 1, result.output
        assert "GET /items/{item_id}" in result.output
        assert "undocumented: ItemNotFoundException" in result.output


class TestOpenAPIErrorsStep:
    """``openapi-errors`` inside ``tempest check``."""

    def test_undeclared_exception_fails(self, tmp_path: Path) -> None:
        """Cenário: rota levanta ``ItemNotFoundException`` sem declarar.

        Esperado: exit 1 e o relatório com a rota e a exceção.
        """
        code, report = _run(
            run_openapi_errors_step, _routes_project(tmp_path, UNDECLARED_ROUTER)
        )
        assert code == 1
        assert "GET /items/{item_id}" in report
        assert "undocumented: ItemNotFoundException" in report
        assert "1 route(s) with drift, 1 undocumented exception(s)." in report

    def test_declared_exception_passes(self, tmp_path: Path) -> None:
        """Cenário: a mesma rota com ``responses=error_responses(...)``.

        Esperado: exit 0 e a mensagem de sucesso do comando.
        """
        code, report = _run(
            run_openapi_errors_step, _routes_project(tmp_path, DECLARED_ROUTER)
        )
        assert code == 0, report
        assert "Every route's declared errors match its flow" in report

    def test_source_root_target_resolves_the_project(self, tmp_path: Path) -> None:
        """Cenário: ``tempest check src``.

        Esperado: a raiz do projeto é o pai de ``src``, e a rota é vista.
        """
        project = _routes_project(tmp_path, UNDECLARED_ROUTER)
        assert check_project_root(str(project / "src")) == project
        code, _ = _run(run_openapi_errors_step, project / "src")
        assert code == 1

    def test_project_without_sources_is_skipped(self, tmp_path: Path) -> None:
        """Cenário: diretório sem ``src/`` nem ``app/``.

        Esperado: exit 0 com aviso de que pulou, em vez de falhar.
        """
        code, report = _run(run_openapi_errors_step, tmp_path)
        assert code == 0
        assert report.startswith("skipped: no src/ or app/")

    def test_opt_out_skips(self, tmp_path: Path) -> None:
        """Cenário: ``check_openapi_errors = false`` com drift no projeto.

        Esperado: exit 0 e o aviso nomeando a chave.
        """
        project = _routes_project(tmp_path, UNDECLARED_ROUTER)
        _write(
            project,
            "pyproject.toml",
            f"[tool.tempest]\n{OPENAPI_ERRORS_CONFIG_KEY} = false\n",
        )
        code, report = _run(run_openapi_errors_step, project)
        assert code == 0
        assert f"[tool.tempest] {OPENAPI_ERRORS_CONFIG_KEY} = false" in report

    def test_invalid_switch_is_an_error(self, tmp_path: Path) -> None:
        """Cenário: ``check_openapi_errors = "no"``.

        Esperado: exit 2 e a mensagem dizendo o que é aceito.
        """
        project = _routes_project(tmp_path, DECLARED_ROUTER)
        _write(
            project,
            "pyproject.toml",
            f'[tool.tempest]\n{OPENAPI_ERRORS_CONFIG_KEY} = "no"\n',
        )
        code, report = _run(run_openapi_errors_step, project)
        assert code == 2
        assert "must be true or false" in report


class TestLintSchemasStep:
    """``lint-schemas`` inside ``tempest check``."""

    def test_undocumented_field_fails(self, tmp_path: Path) -> None:
        """Cenário: campo de schema sem ``title``/``description``/``examples``.

        Esperado: exit 1 e a linha do campo no relatório.
        """
        code, report = _run(
            run_lint_schemas_step, _schemas_project(tmp_path, UNDOCUMENTED_SCHEMA)
        )
        assert code == 1, report
        assert "src.schemas.ItemSchema.name: missing" in report

    def test_documented_field_passes(self, tmp_path: Path) -> None:
        """Cenário: o mesmo campo com os três metadados.

        Esperado: exit 0 e a mensagem de sucesso.
        """
        code, report = _run(
            run_lint_schemas_step, _schemas_project(tmp_path, DOCUMENTED_SCHEMA)
        )
        assert code == 0, report
        assert "src.schemas is fully documented" in report

    def test_import_runs_in_a_child_process(self, tmp_path: Path) -> None:
        """Cenário: o step importa ``src.schemas`` do projeto.

        Esperado: o módulo do projeto não entra no processo que roda o
        ``pytest`` em seguida.
        """
        import sys

        project = _schemas_project(tmp_path, DOCUMENTED_SCHEMA)
        before = sys.modules.get("src.schemas")
        code, _ = _run(run_lint_schemas_step, project)
        assert code == 0
        assert sys.modules.get("src.schemas") is before

    def test_env_defaults_to_test_when_unset(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Cenário: o pacote recusa importar sem ``ENV``, e o shell não tem.

        Esperado: o filho recebe ``ENV=test`` e o import passa.
        """
        monkeypatch.delenv("ENV", raising=False)
        guard = 'import os\nassert os.environ["ENV"] == "test"\n'
        project = _schemas_project(tmp_path, guard + DOCUMENTED_SCHEMA)
        code, report = _run(run_lint_schemas_step, project)
        assert code == 0, report

    def test_env_from_the_shell_wins(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Cenário: o shell exporta ``ENV=staging``.

        Esperado: o filho vê ``staging``, não o default.
        """
        monkeypatch.setenv("ENV", "staging")
        guard = 'import os\nassert os.environ["ENV"] == "staging"\n'
        project = _schemas_project(tmp_path, guard + DOCUMENTED_SCHEMA)
        code, report = _run(run_lint_schemas_step, project)
        assert code == 0, report

    def test_broken_import_fails(self, tmp_path: Path) -> None:
        """Cenário: ``src.schemas`` levanta no import.

        Esperado: exit 2 com o traceback no relatório.
        """
        project = _schemas_project(tmp_path, "raise RuntimeError('boom')\n")
        code, report = _run(run_lint_schemas_step, project)
        assert code == 2
        assert "cannot import src.schemas" in report
        assert "RuntimeError: boom" in report

    def test_project_without_schemas_is_skipped(self, tmp_path: Path) -> None:
        """Cenário: projeto ``src/`` sem pacote ``schemas``.

        Esperado: exit 0 com o aviso de que pulou.
        """
        _write(tmp_path, "src/__init__.py", "")
        code, report = _run(run_lint_schemas_step, tmp_path)
        assert code == 0
        assert report.startswith("skipped: no src.schemas package")

    def test_opt_out_skips(self, tmp_path: Path) -> None:
        """Cenário: ``check_lint_schemas = false`` com campo sem documentação.

        Esperado: exit 0 sem importar o pacote.
        """
        project = _schemas_project(tmp_path, UNDOCUMENTED_SCHEMA)
        _write(
            project,
            "pyproject.toml",
            f"[tool.tempest]\n{LINT_SCHEMAS_CONFIG_KEY} = false\n",
        )
        code, report = _run(run_lint_schemas_step, project)
        assert code == 0
        assert f"[tool.tempest] {LINT_SCHEMAS_CONFIG_KEY} = false" in report


REQUIRED_SETTINGS: str = '''"""Settings with a required secret (#736)."""

import os

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """Reads a ``.env`` unless ``ENV=test``, like ``EnvironmentSettings``."""

    model_config = SettingsConfigDict(
        env_file=None if os.environ.get("ENV") == "test" else ".env",
    )

    ADMIN_SECRET_KEY: str


settings: Settings = Settings()
'''


class TestLintSchemasEnvironment:
    """#736: a required setting no longer breaks the step under ``ENV=test``."""

    def _project(self, root: Path, tempest: str = "") -> Path:
        """Build a project whose ``src`` builds settings with a required secret.

        Args:
            root (Path): Where to build it.
            tempest (str): Extra ``[tool.tempest]`` lines.

        Returns:
            Path: ``root``.
        """
        project = _schemas_project(root, DOCUMENTED_SCHEMA)
        _write(project, "src/__init__.py", "from src.core.settings import settings\n")
        _write(project, "src/core/__init__.py", "")
        _write(project, "src/core/settings.py", REQUIRED_SETTINGS)
        _write(project, ".env", "ADMIN_SECRET_KEY=from-dotenv\n")
        _write(
            project,
            "pyproject.toml",
            f'[project]\nname = "demo"\n\n[tool.tempest]\n{tempest}',
        )
        return project

    def test_a_missing_required_setting_explains_the_environment(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Cenário: o shell sem ``ENV`` nem o segredo; o ``.env`` tem o segredo.

        Esperado: exit 2 e, depois do traceback, o hint dizendo que rodou com
        ``ENV=test``, que o ``.env`` foi descartado e as duas saídas.
        """
        monkeypatch.delenv("ENV", raising=False)
        monkeypatch.delenv("ADMIN_SECRET_KEY", raising=False)
        code, report = _run(run_lint_schemas_step, self._project(tmp_path))
        assert code == 2
        assert "ADMIN_SECRET_KEY" in report
        assert "with ENV=test (ENV=test discards the .env" in report
        assert f"[tool.tempest] {LINT_SCHEMAS_ENV_CONFIG_KEY} = " in report
        assert f"{LINT_SCHEMAS_CONFIG_KEY} = false" in report

    def test_the_configured_environment_reaches_the_child(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Cenário: ``lint_schemas_env = { ADMIN_SECRET_KEY = "lint-only" }``.

        Esperado: o import passa e o step dá 0.
        """
        monkeypatch.delenv("ENV", raising=False)
        monkeypatch.delenv("ADMIN_SECRET_KEY", raising=False)
        project = self._project(
            tmp_path,
            f'{LINT_SCHEMAS_ENV_CONFIG_KEY} = {{ ADMIN_SECRET_KEY = "lint-only" }}\n',
        )
        code, report = _run(run_lint_schemas_step, project)
        assert code == 0, report

    def test_the_shell_wins_over_the_configured_value(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Cenário: o shell exporta o segredo e o ``pyproject`` também o define.

        Esperado: o filho vê o valor do shell.
        """
        monkeypatch.delenv("ENV", raising=False)
        monkeypatch.setenv("ADMIN_SECRET_KEY", "from-shell")
        project = self._project(
            tmp_path,
            f'{LINT_SCHEMAS_ENV_CONFIG_KEY} = {{ ADMIN_SECRET_KEY = "lint-only" }}\n',
        )
        _write(
            project,
            "src/schemas/__init__.py",
            'import os\nassert os.environ["ADMIN_SECRET_KEY"] == "from-shell"\n'
            + DOCUMENTED_SCHEMA,
        )
        code, report = _run(run_lint_schemas_step, project)
        assert code == 0, report

    def test_a_non_string_value_is_an_error(self, tmp_path: Path) -> None:
        """Cenário: ``lint_schemas_env = { PORT = 8000 }``.

        Esperado: exit 2 nomeando a chave, sem rodar o filho.
        """
        project = self._project(
            tmp_path, f"{LINT_SCHEMAS_ENV_CONFIG_KEY} = {{ PORT = 8000 }}\n"
        )
        code, report = _run(run_lint_schemas_step, project)
        assert code == 2
        assert f"{LINT_SCHEMAS_ENV_CONFIG_KEY} must be a table of strings" in report
