"""Tests for ``tempest generate --standard`` (#718).

Each project is built on disk in ``tmp_path`` and read the way the command
reads it: the exceptions are imported in a child interpreter, so these
tests exercise the real introspection, not a stub of it.
"""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

import pytest
from typer.testing import CliRunner

from tempest_fastapi_sdk.cli.main import app
from tempest_fastapi_sdk.cli.standard import (
    StandardRefusedError,
    apply_standard,
    status_module,
)

runner = CliRunner()

FLAT_EXCEPTIONS: str = '''\
"""Domain exceptions."""

from typing import Any, ClassVar

from tempest_fastapi_sdk import AppException, ConflictException, NotFoundException


class DomainError(AppException):
    """Base of the domain, no status of its own."""

    code = "DOMAIN"


class UserNotFoundError(NotFoundException):
    """No user with that id."""

    message: str = "Usuário não encontrado"
    code: str = "USER_NOT_FOUND"
    field = "user_id"
    details_example: ClassVar[dict[str, Any]] = {"user_id": "x"}


class QuotaExceededError(DomainError):
    """Over quota: a 409 that inherits from a 500 base."""

    status_code = 409
    code = "QUOTA"
    message = "Cota excedida"


class UserEmailAlreadyTakenError(ConflictException):
    """The e-mail belongs to another user."""

    message: str = "Já existe um usuário com esse e-mail"
    code: str = "USER_EMAIL_TAKEN"
'''
"""A flat module with a deep hierarchy and a ``status_code`` override."""


def _project(target: Path, *, root: str = "src", exceptions: str | None = None) -> Path:
    """Write a pre-standard project: pyproject, root package, flat exceptions.

    Args:
        target (Path): Where the project goes.
        root (str): The source root package.
        exceptions (str | None): Content of ``core/exceptions.py``; ``None``
            writes no exceptions at all.

    Returns:
        Path: ``target``.
    """
    (target / root / "core").mkdir(parents=True)
    (target / root / "schemas").mkdir()
    (target / "tests").mkdir()
    (target / "pyproject.toml").write_text(
        '[project]\nname = "old"\nversion = "0.1.0"\n', encoding="utf-8"
    )
    for package in ("", "core", "schemas"):
        (target / root / package / "__init__.py").write_text("", encoding="utf-8")
    if exceptions is not None:
        (target / root / "core" / "exceptions.py").write_text(
            exceptions, encoding="utf-8"
        )
    return target


def _pytest(target: Path, *paths: str) -> subprocess.CompletedProcess[str]:
    """Run pytest inside the generated project.

    Args:
        target (Path): The project root.
        *paths (str): Test paths relative to it.

    Returns:
        subprocess.CompletedProcess[str]: The finished run.
    """
    return subprocess.run(
        [sys.executable, "-m", "pytest", "-q", "-p", "no:cacheprovider", *paths],
        cwd=target,
        capture_output=True,
        text=True,
        timeout=300,
        check=False,
    )


class TestStatusModule:
    def test_the_five_scaffold_statuses_keep_the_scaffold_names(self) -> None:
        """Cenário: os cinco status que o ``tempest new`` escreve.

        Esperado: o mesmo nome de módulo do scaffold.
        """
        names = [status_module(status) for status in (400, 401, 403, 404, 409)]
        assert names == [
            "bad_request",
            "unauthorized",
            "forbidden",
            "not_found",
            "conflict",
        ]

    def test_an_unlisted_status_and_no_status(self) -> None:
        """Cenário: status fora da tabela e classe sem status.

        Esperado: ``status_<code>`` e ``base``.
        """
        assert (status_module(418), status_module(None)) == ("status_418", "base")


class TestMigration:
    def test_classes_land_in_the_module_of_the_status_they_answer(
        self, tmp_path: Path
    ) -> None:
        """Cenário: hierarquia funda e ``status_code`` sobrescrito na classe.

        Esperado: cada classe vai para o módulo do status lido importando,
        não da base — ``QuotaExceededError`` herda de uma base 500 e vai
        para ``conflict.py`` —, e o módulo plano some.
        """
        target = _project(tmp_path, exceptions=FLAT_EXCEPTIONS)
        report = apply_standard(target, force=False)
        assert report.moved == {
            "DomainError": "internal",
            "QuotaExceededError": "conflict",
            "UserEmailAlreadyTakenError": "conflict",
            "UserNotFoundError": "not_found",
        }
        package = target / "src" / "core" / "exceptions"
        assert sorted(path.name for path in package.glob("*.py")) == [
            "__init__.py",
            "conflict.py",
            "internal.py",
            "not_found.py",
        ]
        assert not (target / "src" / "core" / "exceptions.py").exists()
        assert "from .internal import DomainError" in (
            package / "conflict.py"
        ).read_text(encoding="utf-8")

    def test_the_public_import_and_the_frozen_contract_pass(
        self, tmp_path: Path
    ) -> None:
        """Cenário: a suíte gerada rodando sobre o projeto migrado.

        Esperado: ``from src.core.exceptions import X`` resolve e o
        contrato congelado passa, com o status e a mensagem de hoje.
        """
        target = _project(tmp_path, exceptions=FLAT_EXCEPTIONS)
        apply_standard(target, force=False)
        contract = (target / "tests/unit/core/test_exceptions_contract.py").read_text(
            encoding="utf-8"
        )
        assert '"Cota excedida"' in contract
        assert '"Usuário não encontrado"' in contract
        result = _pytest(target, "tests/unit/core")
        assert result.returncode == 0, result.stdout + result.stderr

    def test_a_class_with_init_is_warned(self, tmp_path: Path) -> None:
        """Cenário: exceção antiga que monta a mensagem no ``__init__``.

        Esperado: aviso nomeando a classe, já que o contrato congelado
        falha para ela.
        """
        legacy = FLAT_EXCEPTIONS + (
            "\n\nclass LegacyError(NotFoundException):\n"
            '    """Builds its message at raise time."""\n\n'
            "    def __init__(self, item: str) -> None:\n"
            '        super().__init__(f"{item} missing", code="LEGACY")\n'
        )
        target = _project(tmp_path, exceptions=legacy)
        report = apply_standard(target, force=False)
        assert any(
            warning.startswith("LegacyError defines __init__")
            for warning in report.warnings
        )

    def test_a_relative_import_gains_a_level(self, tmp_path: Path) -> None:
        """Cenário: o módulo plano importa ``from .messages import ...``.

        Esperado: o módulo movido importa ``from ..messages import ...``
        (está um pacote mais fundo) e o pacote importa.
        """
        source = (
            '"""Exceptions."""\n\n'
            "from tempest_fastapi_sdk import NotFoundException\n\n"
            "from .messages import ITEM_MISSING\n\n\n"
            "class ItemMissingError(NotFoundException):\n"
            '    """No item."""\n\n'
            '    code = "ITEM_MISSING"\n'
            "    message = ITEM_MISSING\n"
        )
        target = _project(tmp_path, exceptions=source)
        (target / "src/core/messages.py").write_text(
            'ITEM_MISSING: str = "Item ausente"\n', encoding="utf-8"
        )
        apply_standard(target, force=False)
        moved = (target / "src/core/exceptions/not_found.py").read_text(
            encoding="utf-8"
        )
        assert "from ..messages import ITEM_MISSING" in moved
        assert _pytest(target, "tests/unit/core").returncode == 0


class TestRefusal:
    def test_a_module_level_statement_refuses_before_writing(
        self, tmp_path: Path
    ) -> None:
        """Cenário: constante de módulo no arquivo de exceções.

        Esperado: ``StandardRefusedError`` com a linha, e nada escrito —
        nem o pacote aditivo, nem a migração.
        """
        source = FLAT_EXCEPTIONS + '\nDEFAULT_MESSAGE: str = "x"\n'
        target = _project(tmp_path, exceptions=source)
        with pytest.raises(StandardRefusedError, match=r"exceptions\.py:\d+"):
            apply_standard(target, force=False)
        assert (target / "src/core/exceptions.py").is_file()
        assert not (target / "src/core/constants").exists()
        assert not (target / "tests/unit").exists()

    def test_an_import_cycle_between_status_modules_refuses(
        self, tmp_path: Path
    ) -> None:
        """Cenário: 404 → 409 → 404 na cadeia de herança.

        Esperado: recusa nomeando o ciclo, sem escrever.
        """
        source = (
            '"""Exceptions."""\n\n'
            "from tempest_fastapi_sdk import NotFoundException\n\n\n"
            "class AError(NotFoundException):\n"
            '    code = "A"\n\n\n'
            "class BError(AError):\n"
            "    status_code = 409\n"
            '    code = "B"\n\n\n'
            "class CError(BError):\n"
            "    status_code = 404\n"
            '    code = "C"\n'
        )
        target = _project(tmp_path, exceptions=source)
        with pytest.raises(StandardRefusedError, match="conflict -> not_found"):
            apply_standard(target, force=False)
        assert (target / "src/core/exceptions.py").is_file()

    def test_the_cli_exits_1_with_the_reason(self, tmp_path: Path) -> None:
        """Cenário: a mesma recusa pelo comando.

        Esperado: exit 1 e a mensagem no stderr.
        """
        target = _project(
            tmp_path, exceptions=FLAT_EXCEPTIONS + '\nDEFAULT_MESSAGE: str = "x"\n'
        )
        result = runner.invoke(app, ["generate", "--standard", "--path", str(target)])
        assert result.exit_code == 1
        assert "only imports, classes and __all__" in result.output


class TestAdditive:
    def test_a_flat_module_is_never_shadowed(self, tmp_path: Path) -> None:
        """Cenário: o projeto já tem ``core/constants.py``.

        Esperado: o pacote ``core/constants/`` não é escrito, e o motivo
        aparece no relatório.
        """
        target = _project(tmp_path, exceptions=FLAT_EXCEPTIONS)
        (target / "src/core/constants.py").write_text("X: int = 1\n", encoding="utf-8")
        report = apply_standard(target, force=False)
        assert not (target / "src/core/constants").exists()
        reasons = {
            reason for path, reason in report.skipped if "constants" in str(path)
        }
        assert reasons == {"src/core/constants.py exists and would be shadowed"}

    def test_a_project_without_exceptions_gets_the_scaffold_package(
        self, tmp_path: Path
    ) -> None:
        """Cenário: projeto sem nenhuma exceção.

        Esperado: o pacote do ``tempest new`` e o contrato dele, que passa.
        """
        target = _project(tmp_path)
        apply_standard(target, force=False)
        assert (target / "src/core/exceptions/not_found.py").is_file()
        assert _pytest(target, "tests/unit/core").returncode == 0

    def test_a_second_run_changes_nothing(self, tmp_path: Path) -> None:
        """Cenário: rodar duas vezes.

        Esperado: a segunda não escreve nem move nada.
        """
        target = _project(tmp_path, exceptions=FLAT_EXCEPTIONS)
        apply_standard(target, force=False)
        second = apply_standard(target, force=False)
        assert (second.written, second.moved, second.removed) == ([], {}, [])

    def test_an_app_root_gets_app_imports(self, tmp_path: Path) -> None:
        """Cenário: projeto com raiz ``app/``.

        Esperado: os arquivos gerados importam de ``app``, e o contrato
        passa.
        """
        target = _project(tmp_path, root="app", exceptions=FLAT_EXCEPTIONS)
        apply_standard(target, force=False)
        contract = (target / "tests/unit/core/test_exceptions_contract.py").read_text(
            encoding="utf-8"
        )
        assert "from app.core import exceptions" in contract
        assert "src" not in (
            target / "tests/unit/architecture/test_schemas.py"
        ).read_text(encoding="utf-8")
        assert _pytest(target, "tests/unit/core").returncode == 0
