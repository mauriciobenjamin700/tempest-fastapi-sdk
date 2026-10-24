"""``STR001`` and ``STR002``: nested functions and private module helpers (#616).

Both rules read the source root and the project's ``tests/``: in the
transport service the suite held most of them (174 nested functions and
76 private helpers, against 18 and 2 in ``src``).
"""

from __future__ import annotations

import textwrap
from pathlib import Path

from tempest_fastapi_sdk.cli.layer_lint import (
    EXCEPTIONS_PATH,
    RULES,
    LayerFinding,
    find_exceptions_file,
    lint_layers,
    project_root,
)

STRUCTURE_RULES: frozenset[str] = frozenset({"STR001", "STR002"})
"""The rules this file pins."""


def write_source(root: Path, relative: str, source: str) -> Path:
    """Write a dedented source file under ``root``.

    Args:
        root (Path): The project root.
        relative (str): Path of the file below ``root``.
        source (str): The file content.

    Returns:
        Path: The written file.
    """
    path = root / relative
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(textwrap.dedent(source).lstrip(), encoding="utf-8")
    return path


def structure_codes(target: Path) -> list[tuple[str, int, str, str | None]]:
    """Run the structure rules and reduce the findings.

    Args:
        target (Path): The check target.

    Returns:
        list[tuple[str, int, str, str | None]]: ``(file, line, code,
        subject)`` per finding.
    """
    findings: list[LayerFinding] = lint_layers(target, rules=STRUCTURE_RULES)
    return [(item.path.name, item.line, item.code, item.subject) for item in findings]


class TestSeverity:
    def test_the_new_rules_enter_as_warnings(self) -> None:
        assert {RULES[code].severity for code in STRUCTURE_RULES} == {"warning"}


class TestNestedFunction:
    def test_functions_inside_functions_and_methods(self, tmp_path: Path) -> None:
        """Cenário: ``def`` dentro de função, de método e de classe local.

        Esperado: um ``STR001`` por função aninhada, com o caminho pontuado
        como subject; método de classe de módulo e lambda passam.
        """
        write_source(
            tmp_path,
            "src/utils/retry.py",
            """
            def with_retry(attempts: int) -> object:
                async def wrapper() -> None:
                    def log() -> None: ...
                return wrapper


            class Scheduler:
                def schedule(self) -> object:
                    class Job:
                        def run(self) -> None: ...
                    return Job


                def keep(self) -> object:
                    return lambda: None
            """,
        )
        assert structure_codes(tmp_path) == [
            ("retry.py", 2, "STR001", "with_retry.wrapper"),
            ("retry.py", 3, "STR001", "with_retry.wrapper.log"),
            ("retry.py", 10, "STR001", "Scheduler.schedule.Job.run"),
        ]

    def test_the_suite_is_read_too(self, tmp_path: Path) -> None:
        """Cenário: rota de teste montada dentro da função de teste.

        Esperado: o ``tests/`` do projeto entra na varredura.
        """
        write_source(
            tmp_path,
            "tests/test_app.py",
            """
            def test_route() -> None:
                async def handler() -> dict[str, str]:
                    return {}
            """,
        )
        assert structure_codes(tmp_path) == [
            ("test_app.py", 2, "STR001", "test_route.handler")
        ]


class TestPrivateModuleFunction:
    def test_private_helpers_in_src_and_tests(self, tmp_path: Path) -> None:
        write_source(
            tmp_path,
            "src/utils/text.py",
            """
            def _slug(value: str) -> str:
                return value


            async def _fetch() -> None: ...


            def slug(value: str) -> str:
                return value


            def __getattr__(name: str) -> object: ...


            class Text:
                def _strip(self) -> None: ...
            """,
        )
        write_source(tmp_path, "tests/helpers.py", "def _write() -> None: ...\n")
        assert structure_codes(tmp_path) == [
            ("text.py", 1, "STR002", "_slug"),
            ("text.py", 5, "STR002", "_fetch"),
            ("helpers.py", 1, "STR002", "_write"),
        ]


class TestProjectRoot:
    def test_a_source_root_target_reads_the_suite_beside_it(
        self, tmp_path: Path
    ) -> None:
        write_source(tmp_path, "tests/helpers.py", "def _write() -> None: ...\n")
        assert project_root(tmp_path / "src") == tmp_path
        assert structure_codes(tmp_path / "src") == [
            ("helpers.py", 1, "STR002", "_write")
        ]

    def test_a_project_root_never_reads_its_parent(self, tmp_path: Path) -> None:
        """Cenário: um projeto dentro de um monorepo com ``tests/`` na raiz.

        Esperado: nem a suíte nem as exceções do diretório de cima entram.
        """
        write_source(tmp_path, "tests/helpers.py", "def _write() -> None: ...\n")
        write_source(tmp_path, EXCEPTIONS_PATH, "ARCHITECTURE_EXCEPTIONS = {}\n")
        service = tmp_path / "service"
        write_source(service, "src/utils/text.py", "def slug() -> None: ...\n")
        assert project_root(service) == service
        assert find_exceptions_file(service) is None
        assert structure_codes(service) == []
