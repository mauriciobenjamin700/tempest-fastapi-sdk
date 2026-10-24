"""Severity, rule selection and declared exceptions of ``lint-layers`` (#616).

The project shape is the one the existing rules already catch: a
``NewsService`` that does not inherit ``BaseService`` (``SVC001``) next to
a module-level helper (``SVC002``). What changes between tests is how the
run is asked for and what ``tests/architecture_exceptions.py`` declares.
"""

from __future__ import annotations

import io
import re
import textwrap
from pathlib import Path

import pytest
from typer.testing import CliRunner

from tempest_fastapi_sdk.cli import layer_lint
from tempest_fastapi_sdk.cli.layer_lint import (
    EXCEPTIONS_PATH,
    RULES,
    LayerFinding,
    find_exceptions_file,
    lint_layers,
    read_exceptions,
    run_lint_layers,
)
from tempest_fastapi_sdk.cli.main import app as cli_app

runner = CliRunner()

SERVICE: str = """
class NewsService:
    pass


def page_envelope() -> None:
    pass
"""
"""``SVC001`` on line 1 and ``SVC002`` on line 5 of ``src/services/news.py``."""


def _write(root: Path, relative: str, source: str) -> Path:
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


def _exceptions(root: Path, body: str) -> Path:
    """Write ``tests/architecture_exceptions.py`` with ``body`` as the dict.

    Args:
        root (Path): The project root.
        body (str): The dict entries, one per line.

    Returns:
        Path: The written file.
    """
    entries = textwrap.indent(textwrap.dedent(body).strip(), "    ")
    return _write(
        root,
        EXCEPTIONS_PATH,
        f"ARCHITECTURE_EXCEPTIONS: dict[str, str] = {{\n{entries}\n}}\n",
    )


def _codes(findings: list[LayerFinding]) -> list[tuple[str, int, str]]:
    """Reduce findings to ``(file name, line, code)``.

    Args:
        findings (list[LayerFinding]): The findings.

    Returns:
        list[tuple[str, int, str]]: One tuple per finding.
    """
    return [(item.path.name, item.line, item.code) for item in findings]


@pytest.fixture
def project(tmp_path: Path) -> Path:
    """A project whose only service breaks ``SVC001`` and ``SVC002``.

    Args:
        tmp_path (Path): Pytest's temporary directory.

    Returns:
        Path: The project root.
    """
    _write(tmp_path, "src/services/news.py", SERVICE)
    return tmp_path


class TestRegistry:
    def test_every_code_the_checker_emits_is_registered(self) -> None:
        """Cenário: um código novo em ``layer_lint.py`` sem entrada em ``RULES``.

        Esperado: o código emitido aparece no fonte e está no registro, senão
        ``--rule`` o recusaria e a severidade cairia no default silencioso.
        """
        source = Path(layer_lint.__file__).read_text(encoding="utf-8")
        emitted = set(re.findall(r'code="([A-Z]+\d{3})"', source))
        assert emitted
        assert emitted <= set(RULES)

    def test_existing_rules_stay_errors(self) -> None:
        for code in ("SVC001", "SVC002", "SVC003", "REPO001", "ENUM001"):
            assert RULES[code].severity == "error"
        assert RULES["RTR001"].severity == RULES["RTR002"].severity == "error"


class TestDeclaredExceptions:
    def test_a_path_entry_excuses_every_finding_of_that_code(
        self, project: Path
    ) -> None:
        _exceptions(project, '"SVC001 src/services/news.py": "static catalogue",')
        assert _codes(lint_layers(project)) == [("news.py", 5, "SVC002")]

    def test_a_subject_narrows_the_entry(self, project: Path) -> None:
        _exceptions(
            project,
            '"SVC002 src/services/news.py::page_envelope": "kept for the export",',
        )
        assert _codes(lint_layers(project)) == [("news.py", 1, "SVC001")]

    def test_a_wrong_subject_excuses_nothing_and_is_stale(self, project: Path) -> None:
        _exceptions(project, '"SVC002 src/services/news.py::other": "typo",')
        assert _codes(lint_layers(project)) == [
            ("news.py", 1, "SVC001"),
            ("news.py", 5, "SVC002"),
            ("architecture_exceptions.py", 2, "EXC002"),
        ]

    def test_a_source_root_target_reads_the_parent_tests(self, project: Path) -> None:
        _exceptions(project, '"SVC001 src/services/news.py": "static catalogue",')
        assert find_exceptions_file(project / "src") == project / EXCEPTIONS_PATH
        assert _codes(lint_layers(project / "src")) == [("news.py", 5, "SVC002")]

    @pytest.mark.parametrize(
        ("entry", "fragment"),
        [
            ('"SVC001 src/services/news.py": "",', "needs a reason"),
            ('"SVC001 src/services/news.py": REASON,', "needs a reason"),
            ('"SVC999 src/services/news.py": "why",', "unknown rule code `SVC999`"),
            ('"EXC002 src/services/news.py": "why",', "unknown rule code `EXC002`"),
            ('"SVC001": "why",', "is not `<CODE> <path>`"),
            ("**OTHER,", "string literal"),
        ],
        ids=["empty", "name", "unknown", "exc", "no-path", "spread"],
    )
    def test_an_unusable_entry_is_an_error(
        self, project: Path, entry: str, fragment: str
    ) -> None:
        path = _exceptions(project, entry)
        entries, problems = read_exceptions(path)
        assert entries == []
        assert [item.code for item in problems] == ["EXC001"]
        assert fragment in problems[0].message
        assert problems[0].severity == "error"

    def test_a_key_written_twice_is_an_error(self, project: Path) -> None:
        path = _exceptions(
            project,
            """
            "SVC001 src/services/news.py": "first",
            "SVC001 src/services/news.py": "second",
            """,
        )
        entries, problems = read_exceptions(path)
        assert [entry.reason for entry in entries] == ["first"]
        assert [(item.line, item.code) for item in problems] == [(3, "EXC001")]

    @pytest.mark.parametrize(
        "source",
        ["ARCHITECTURE_EXCEPTIONS = dict(a='b')\n", "OTHER = {}\n", "def (:\n"],
        ids=["call", "missing", "broken"],
    )
    def test_a_file_without_the_dict_is_an_error(
        self, project: Path, source: str
    ) -> None:
        _write(project, EXCEPTIONS_PATH, source)
        assert ("architecture_exceptions.py", 1, "EXC001") in _codes(
            lint_layers(project)
        )


class TestRuleSelection:
    def test_only_the_selected_rule_is_reported(self, project: Path) -> None:
        assert _codes(lint_layers(project, rules={"SVC002"})) == [
            ("news.py", 5, "SVC002")
        ]

    def test_a_stale_entry_of_another_rule_is_not_reported(self, project: Path) -> None:
        _exceptions(project, '"REPO001 src/db/repositories/gone.py": "removed",')
        assert _codes(lint_layers(project, rules={"SVC002"})) == [
            ("news.py", 5, "SVC002")
        ]
        assert ("architecture_exceptions.py", 2, "EXC002") in _codes(
            lint_layers(project)
        )

    def test_a_stale_entry_of_the_selected_rule_is_reported(
        self, project: Path
    ) -> None:
        """Cenário: ``--rule REPO001`` com uma entrada de REPO001 que já não casa.

        Esperado: o ``EXC002`` aparece — a migração de uma regra por vez é
        justamente quando a entrada velha precisa ser vista.
        """
        _exceptions(project, '"REPO001 src/db/repositories/gone.py": "removed",')
        assert _codes(lint_layers(project, rules={"REPO001"})) == [
            ("architecture_exceptions.py", 2, "EXC002")
        ]

    def test_an_unusable_entry_shows_whatever_the_selection(
        self, project: Path
    ) -> None:
        _exceptions(project, '"SVC001 src/services/news.py": "",')
        assert ("architecture_exceptions.py", 2, "EXC001") in _codes(
            lint_layers(project, rules={"SVC002"})
        )


class TestRunner:
    def test_a_warning_alone_passes_and_is_tagged(
        self, project: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        _exceptions(
            project,
            """
            "SVC001 src/services/news.py": "static catalogue",
            "SVC002 src/services/news.py": "export helper",
            "REPO001 src/db/repositories/gone.py": "removed",
            """,
        )
        monkeypatch.chdir(project)
        stream = io.StringIO()
        assert run_lint_layers(".", strict=False, stream=stream) == 0
        lines = stream.getvalue().splitlines()
        assert lines[0].startswith(
            "tests/architecture_exceptions.py:4: EXC002 (warning)"
        )
        assert lines[-1] == "1 layer finding(s): 0 error(s), 1 warning(s)."

    def test_strict_fails_on_a_warning(self, project: Path) -> None:
        _exceptions(
            project,
            """
            "SVC001 src/services/news.py": "static catalogue",
            "SVC002 src/services/news.py": "export helper",
            "REPO001 src/db/repositories/gone.py": "removed",
            """,
        )
        assert run_lint_layers(str(project), strict=True, stream=io.StringIO()) == 1

    def test_strict_comes_from_the_pyproject(self, project: Path) -> None:
        _exceptions(project, '"REPO001 src/db/repositories/gone.py": "removed",')
        _write(project, "src/services/news.py", "")
        assert run_lint_layers(str(project), stream=io.StringIO()) == 0
        _write(project, "pyproject.toml", "[tool.tempest]\nlayers_strict = true\n")
        assert run_lint_layers(str(project), stream=io.StringIO()) == 1

    def test_an_invalid_strict_value_is_a_usage_error(self, project: Path) -> None:
        _write(project, "pyproject.toml", '[tool.tempest]\nlayers_strict = "yes"\n')
        stream = io.StringIO()
        assert run_lint_layers(str(project), stream=stream) == 2
        assert "layers_strict must be true or false" in stream.getvalue()

    def test_an_unknown_rule_code_is_a_usage_error(self, project: Path) -> None:
        stream = io.StringIO()
        assert run_lint_layers(str(project), rules=["SVC9"], stream=stream) == 2
        assert stream.getvalue().startswith("unknown rule code(s): SVC9;")

    def test_the_command_takes_rule_and_strict(
        self, project: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.chdir(project)
        only = runner.invoke(cli_app, ["lint-layers", "--rule", "svc002"])
        assert only.exit_code == 1
        assert "SVC002" in only.stdout
        assert "SVC001" not in only.stdout
        _exceptions(project, '"REPO001 src/db/repositories/gone.py": "removed",')
        _write(project, "src/services/news.py", "")
        lenient = runner.invoke(cli_app, ["lint-layers"])
        strict = runner.invoke(cli_app, ["lint-layers", "--strict"])
        assert (lenient.exit_code, strict.exit_code) == (0, 1)
