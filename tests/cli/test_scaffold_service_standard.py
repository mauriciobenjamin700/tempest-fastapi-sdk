"""``tempest new`` writes the service standard of #718, and it passes its own gate.

The standard is the ``transport-backend`` layout the SDK documents in
``docs/service-standard.md``: exceptions as a package with one module per
HTTP status and the contract in the class body, the client-facing text in
``core/constants/messages``, ``core/enums`` and ``core/types`` as packages,
``db/configs/{connection,migrations}.py``, the OpenAPI text in
``api/docs``, ``layers_strict = true``, and a suite split into
``tests/{unit,integration}/<area>/`` with the architecture guards and the
exception contract. Up to 0.348.x the scaffold taught the opposite in
several places (``core/exceptions.py`` with an ``__init__`` building
``message``, flat ``tests/test_smoke.py``), and an AI copying it copied
that.

The last tests run the generated project's own suite after breaking it
on purpose, so the guards it ships are shown to fire — not only to pass.
"""

from __future__ import annotations

import ast
import os
import subprocess
import sys
import tomllib
from pathlib import Path

import pytest
from typer.testing import CliRunner

from tempest_fastapi_sdk.cli.main import app as cli_app

runner = CliRunner()

MOST_EXTRAS: str = "auth,queue,tasks,ssr,admin,email,minio,cache"
"""The extras the other scaffold tests cover together."""

EXCEPTION_MODULES: tuple[str, ...] = (
    "__init__.py",
    "bad_request.py",
    "conflict.py",
    "forbidden.py",
    "not_found.py",
    "unauthorized.py",
)
"""What ``src/core/exceptions/`` holds right after ``tempest new``."""


def _scaffold(tmp_path: Path, extras: str) -> Path:
    """Run ``tempest new demo`` under ``tmp_path`` and return the project.

    Args:
        tmp_path (Path): The parent directory.
        extras (str): The ``--extras`` value; empty for none.

    Returns:
        Path: The generated project root.
    """
    arguments = ["new", "demo", "--path", str(tmp_path)]
    if extras:
        arguments += ["--extras", extras]
    result = runner.invoke(cli_app, arguments)
    assert result.exit_code == 0, result.stdout + result.stderr
    return tmp_path / "demo"


def _child_environment() -> dict[str, str]:
    """Return the environment a child ``tempest`` / ``pytest`` runs under.

    ``ENV=test`` is what the generated ``conftest.py`` pins; the variables
    of this suite's own pytest run (``PYTEST_*``, ``PYTHONPATH``,
    ``DATABASE_URL``) are dropped so the child sees a clean project, and
    the interpreter's ``bin`` leads ``PATH`` so ``ruff``, ``mypy`` and
    ``pytest`` resolve to this virtualenv.

    Returns:
        dict[str, str]: The environment.
    """
    environment = {
        key: value
        for key, value in os.environ.items()
        if not key.startswith("PYTEST_")
        and key not in {"PYTHONPATH", "DATABASE_URL", "COV_CORE_SOURCE"}
    }
    environment["ENV"] = "test"
    bin_dir = str(Path(sys.executable).parent)
    environment["PATH"] = f"{bin_dir}{os.pathsep}{environment.get('PATH', '')}"
    return environment


def _run(project: Path, *command: str) -> subprocess.CompletedProcess[str]:
    """Run a command of this virtualenv inside the generated project.

    Args:
        project (Path): The project root (the working directory).
        *command (str): The executable name in the virtualenv's ``bin``
            and its arguments.

    Returns:
        subprocess.CompletedProcess[str]: The finished process.
    """
    executable = str(Path(sys.executable).parent / command[0])
    return subprocess.run(
        [executable, *command[1:]],
        cwd=project,
        env=_child_environment(),
        capture_output=True,
        text=True,
        timeout=600,
        check=False,
    )


@pytest.fixture(scope="module")
def project(tmp_path_factory: pytest.TempPathFactory) -> Path:
    """Scaffold one ``--extras sqlite`` project for the shape tests.

    Args:
        tmp_path_factory (pytest.TempPathFactory): Pytest's temporary
            directory factory.

    Returns:
        Path: The generated project root.
    """
    return _scaffold(tmp_path_factory.mktemp("standard"), "sqlite")


class TestExceptions:
    """``core/exceptions`` is a package by status, contract in the class body."""

    def test_is_a_package_with_one_module_per_status(self, project: Path) -> None:
        """Cenário: o projeto recém-gerado.

        Esperado: ``core/exceptions/`` existe com um módulo por status, e o
        ``core/exceptions.py`` plano de antes não existe mais.
        """
        package = project / "src" / "core" / "exceptions"
        assert sorted(path.name for path in package.glob("*.py")) == list(
            EXCEPTION_MODULES
        )
        assert not (project / "src" / "core" / "exceptions.py").exists()

    def test_example_class_declares_the_contract_without_init(
        self, project: Path
    ) -> None:
        """Cenário: a classe de exemplo em ``not_found.py``, lida pelo AST.

        Esperado: o corpo atribui ``code``, ``message`` e ``field`` e não
        define ``__init__`` — o template antigo montava ``message`` num
        ``__init__`` que recebia o id.
        """
        tree = ast.parse(
            (project / "src" / "core" / "exceptions" / "not_found.py").read_text(
                encoding="utf-8"
            )
        )
        classes = [node for node in tree.body if isinstance(node, ast.ClassDef)]
        assert [node.name for node in classes] == ["ItemNotFoundException"]
        body = classes[0].body
        assigned = {
            target.id
            for node in body
            if isinstance(node, ast.Assign)
            for target in node.targets
            if isinstance(target, ast.Name)
        }
        assert {"code", "message", "field"} <= assigned
        assert not any(
            isinstance(node, ast.FunctionDef | ast.AsyncFunctionDef) for node in body
        )

    def test_message_comes_from_the_errors_constants(self, project: Path) -> None:
        """Cenário: ``not_found.py`` e ``core/constants/messages``.

        Esperado: a mensagem é ``errors.ITEM_NOT_FOUND``, definida em
        ``messages/errors.py``, ao lado de ``messages/successes.py``.
        """
        messages = project / "src" / "core" / "constants" / "messages"
        assert (messages / "errors.py").is_file()
        assert (messages / "successes.py").is_file()
        assert "ITEM_NOT_FOUND: str =" in (messages / "errors.py").read_text(
            encoding="utf-8"
        )
        source = (project / "src" / "core" / "exceptions" / "not_found.py").read_text(
            encoding="utf-8"
        )
        assert "message = errors.ITEM_NOT_FOUND" in source


class TestCorePackages:
    """``core/enums`` and ``core/types`` are packages, not flat modules."""

    @pytest.mark.parametrize("name", ["enums", "types", "constants"])
    def test_is_a_package(self, project: Path, name: str) -> None:
        """Cenário: o pacote ``core/<name>`` do projeto recém-gerado.

        Esperado: é diretório com ``__init__.py``, e o módulo plano
        ``core/<name>.py`` não existe.
        """
        assert (project / "src" / "core" / name / "__init__.py").is_file()
        assert not (project / "src" / "core" / f"{name}.py").exists()


class TestDatabaseConfigs:
    """``db/configs`` owns the manager factory and the boot migration."""

    def test_connection_hides_bind_parameters(self, project: Path) -> None:
        """Cenário: ``db/configs/connection.py`` e o ``resources.py`` que o usa.

        Esperado: ``make_db_manager`` constrói com ``hide_parameters=True`` e
        o singleton de ``resources`` sai dele.
        """
        connection = (project / "src" / "db" / "configs" / "connection.py").read_text(
            encoding="utf-8"
        )
        assert "def make_db_manager(" in connection
        assert "hide_parameters=True" in connection
        resources = (
            project / "src" / "api" / "dependencies" / "resources.py"
        ).read_text(encoding="utf-8")
        assert "db: AsyncDatabaseManager = make_db_manager(settings)" in resources

    def test_lifespan_migrates_through_run_migrations(self, project: Path) -> None:
        """Cenário: ``db/configs/migrations.py`` e o lifespan de ``app.py``.

        Esperado: o lifespan chama ``run_migrations`` sob
        ``DATABASE_MIGRATE_ON_STARTUP`` e não monta o ``AlembicHelper``
        ele mesmo — a migração mora num lugar só.
        """
        migrations = (project / "src" / "db" / "configs" / "migrations.py").read_text(
            encoding="utf-8"
        )
        assert "async def run_migrations(" in migrations
        app = (project / "src" / "api" / "app.py").read_text(encoding="utf-8")
        assert "if settings.DATABASE_MIGRATE_ON_STARTUP:" in app
        assert "await run_migrations(settings.DATABASE_URL)" in app
        assert "AlembicHelper" not in app


class TestApiDocs:
    """The OpenAPI text lives in ``api/docs``, out of the router."""

    def test_router_spreads_an_endpoint_doc(self, project: Path) -> None:
        """Cenário: ``api/docs`` e o router da rota raiz.

        Esperado: ``EndpointDoc`` em ``api/docs/base.py``, uma constante por
        handler em ``api/docs/service_info.py`` e o decorator espalhando-a.
        """
        docs = project / "src" / "api" / "docs"
        assert "class EndpointDoc(TypedDict):" in (docs / "base.py").read_text(
            encoding="utf-8"
        )
        assert "INDEX: EndpointDoc = {" in (docs / "service_info.py").read_text(
            encoding="utf-8"
        )
        router = (project / "src" / "api" / "routers" / "service_info.py").read_text(
            encoding="utf-8"
        )
        assert '@router.get("/", **service_info.INDEX)' in router

    def test_summary_reaches_the_openapi_document(self, project: Path) -> None:
        """Cenário: o ``openapi.json`` do app gerado, num processo filho.

        Esperado: a operação ``GET /api/`` traz o ``summary`` e a
        ``description`` do ``EndpointDoc``, não o nome da função.
        """
        completed = _run(
            project,
            "python",
            "-c",
            "import json; from src.server import app; "
            "print(json.dumps(app.openapi()['paths']['/api/']['get']))",
        )
        assert completed.returncode == 0, completed.stderr
        assert '"summary": "Describe the service"' in completed.stdout
        assert "read from the TITLE setting" in completed.stdout


class TestSuiteLayout:
    """The suite is split by kind and area, with the architecture guards."""

    def test_pyproject_turns_layers_strict_on(self, project: Path) -> None:
        """Cenário: o ``[tool.tempest]`` do ``pyproject.toml`` gerado.

        Esperado: ``layers_strict = true`` — os avisos de camada falham o
        ``tempest check`` desde o primeiro commit.
        """
        pyproject = tomllib.loads(
            (project / "pyproject.toml").read_text(encoding="utf-8")
        )
        assert pyproject["tool"]["tempest"]["layers_strict"] is True

    def test_tests_are_split_by_kind_and_area(self, project: Path) -> None:
        """Cenário: a árvore ``tests/`` do projeto recém-gerado.

        Esperado: ``unit/architecture``, ``unit/core`` e
        ``integration/system``, cada pasta com ``__init__.py``, e nenhum
        teste solto na raiz de ``tests/``.
        """
        tests = project / "tests"
        for directory in ("unit/architecture", "unit/core", "integration/system"):
            assert (tests / directory / "__init__.py").is_file(), directory
        assert sorted(path.name for path in tests.glob("test_*.py")) == []

    @pytest.mark.parametrize(
        ("relative", "call"),
        [
            ("unit/architecture/test_layers.py", "lint_layers("),
            ("unit/architecture/test_openapi_errors.py", "analyze_paths("),
            ("unit/architecture/test_schemas.py", "assert_schema_metadata("),
            ("unit/core/test_exceptions_contract.py", "FROZEN_CONTRACT"),
        ],
    )
    def test_architecture_guard_is_shipped(
        self, project: Path, relative: str, call: str
    ) -> None:
        """Cenário: cada guard que a suíte gerada traz.

        Esperado: o arquivo existe e chama a ferramenta do SDK que o
        ``tempest check`` também roda.
        """
        assert call in (project / "tests" / relative).read_text(encoding="utf-8")


@pytest.mark.parametrize("extras", ["sqlite", MOST_EXTRAS], ids=["sqlite", "most"])
def test_the_generated_project_passes_tempest_check(
    tmp_path: Path, extras: str
) -> None:
    """Cenário: ``tempest check`` sobre um projeto recém-gerado.

    Esperado: sai com ``0`` — ruff, format, mypy, ``lint-layers`` estrito,
    ``openapi-errors``, ``lint-schemas`` e a suíte gerada, com os nove
    testes dela passando.
    """
    project = _scaffold(tmp_path, extras)
    completed = _run(project, "tempest", "check")
    output = completed.stdout + completed.stderr
    assert completed.returncode == 0, output
    assert "Layer rules: no findings." in output
    assert "Every route's declared errors match its flow" in output
    assert "src.schemas is fully documented" in output
    assert "9 passed" in output


def test_the_contract_test_fails_for_an_exception_without_an_entry(
    tmp_path: Path,
) -> None:
    """Cenário: uma exceção nova re-exportada sem entrada no contrato.

    Esperado: o teste de contrato gerado falha nomeando a classe — o guard
    dispara, não só passa.
    """
    project = _scaffold(tmp_path, "sqlite")
    not_found = project / "src" / "core" / "exceptions" / "not_found.py"
    not_found.write_text(
        not_found.read_text(encoding="utf-8")
        + "\n\nclass OrderNotFoundException(NotFoundException):\n"
        '    """Probe."""\n\n'
        '    code = "ORDER_NOT_FOUND"\n',
        encoding="utf-8",
    )
    package = project / "src" / "core" / "exceptions" / "__init__.py"
    package.write_text(
        package.read_text(encoding="utf-8")
        + "\nfrom .not_found import OrderNotFoundException as OrderNotFoundException\n"
        '\n__all__ = [*__all__, "OrderNotFoundException"]\n',
        encoding="utf-8",
    )
    completed = _run(
        project,
        "python",
        "-m",
        "pytest",
        "-q",
        "-p",
        "no:cacheprovider",
        "tests/unit/core",
    )
    assert completed.returncode == 1, completed.stdout + completed.stderr
    assert "OrderNotFoundException" in completed.stdout


def test_the_layer_test_fails_on_a_nested_function(tmp_path: Path) -> None:
    """Cenário: uma função aninhada (``STR001``, um aviso) num módulo de ``src``.

    Esperado: o ``test_layers.py`` gerado falha — avisos contam na suíte
    como contam no ``tempest check`` com ``layers_strict = true``.
    """
    project = _scaffold(tmp_path, "sqlite")
    composition = project / "src" / "scripts" / "composition.py"
    composition.write_text(
        composition.read_text(encoding="utf-8") + "\n\ndef build_probe() -> int:\n"
        '    """Probe."""\n\n'
        "    def inner() -> int:\n"
        '        """Inner."""\n'
        "        return 1\n\n"
        "    return inner()\n",
        encoding="utf-8",
    )
    completed = _run(
        project,
        "python",
        "-m",
        "pytest",
        "-q",
        "-p",
        "no:cacheprovider",
        "tests/unit/architecture/test_layers.py",
    )
    assert completed.returncode == 1, completed.stdout + completed.stderr
    assert "STR001" in completed.stdout
