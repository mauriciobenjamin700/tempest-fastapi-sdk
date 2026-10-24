"""``TST001`` to ``TST004``: the shape of the project's suite (#616).

The transport service had 1380 of 1394 tests without the
``Cenário:``/``Esperado:`` docstring, 3 tests that checked nothing, and a
``uuid4()`` in a ``parametrize`` that kept the suite off ``-n auto``.
"""

from __future__ import annotations

import textwrap
from pathlib import Path

from tempest_fastapi_sdk.cli.layer_lint import RULES, LayerFinding, lint_layers

SUITE_RULES: frozenset[str] = frozenset({"TST001", "TST002", "TST003", "TST004"})
"""The rules this file pins."""

DOCUMENTED: str = '"""Cenário: um pedido pago. Esperado: o recibo sai."""'
"""A one-line docstring in the shape ``TST001`` asks for.

One line, so ``textwrap.dedent`` keeps the written test file parseable.
"""


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


def suite_codes(target: Path) -> list[tuple[str, int, str, str | None]]:
    """Run the suite rules and reduce the findings.

    Args:
        target (Path): The check target.

    Returns:
        list[tuple[str, int, str, str | None]]: ``(file, line, code,
        subject)`` per finding.
    """
    findings: list[LayerFinding] = lint_layers(target, rules=SUITE_RULES)
    return [(item.path.name, item.line, item.code, item.subject) for item in findings]


class TestSeverity:
    def test_the_new_rules_enter_as_warnings(self) -> None:
        assert {RULES[code].severity for code in SUITE_RULES} == {"warning"}


class TestDocstringShape:
    def test_missing_or_partial_docstrings_are_reported(self, tmp_path: Path) -> None:
        write_source(
            tmp_path,
            "tests/test_orders.py",
            f'''
            def test_without_docstring() -> None:
                assert True


            def test_half() -> None:
                """Cenário: só a metade."""
                assert True


            class TestReceipt:
                def test_documented(self) -> None:
                    {DOCUMENTED}
                    assert True

                def test_plain(self) -> None:
                    """Receipt is issued."""
                    assert True


            class Helper:
                def test_not_collected(self) -> None:
                    pass
            ''',
        )
        assert suite_codes(tmp_path) == [
            ("test_orders.py", 1, "TST001", "test_without_docstring"),
            ("test_orders.py", 5, "TST001", "test_half"),
            ("test_orders.py", 15, "TST001", "TestReceipt.test_plain"),
        ]

    def test_only_collected_files_are_read(self, tmp_path: Path) -> None:
        write_source(tmp_path, "tests/conftest.py", "def test_helper() -> None: ...\n")
        write_source(tmp_path, "tests/helpers.py", "def test_like() -> None: ...\n")
        write_source(
            tmp_path,
            "tests/orders_test.py",
            "def test_x() -> None:\n    assert True\n",
        )
        assert suite_codes(tmp_path) == [("orders_test.py", 1, "TST001", "test_x")]


class TestChecksSomething:
    def test_a_test_without_any_check_is_reported(self, tmp_path: Path) -> None:
        """Cenário: os três jeitos de checar e um teste que não checa nada.

        Esperado: só o último sai como ``TST002``.
        """
        write_source(
            tmp_path,
            "tests/test_checks.py",
            f"""
            import pytest


            def test_raises() -> None:
                {DOCUMENTED}
                with pytest.raises(ValueError):
                    int("x")


            async def test_mock(client: object) -> None:
                {DOCUMENTED}
                client.send.assert_awaited_once()


            def test_assert() -> None:
                {DOCUMENTED}
                assert 1 + 1 == 2


            def test_nothing() -> None:
                {DOCUMENTED}
                int("1")
            """,
        )
        assert suite_codes(tmp_path) == [
            ("test_checks.py", 20, "TST002", "test_nothing")
        ]


class TestBroadRaises:
    def test_exception_and_base_exception_are_reported(self, tmp_path: Path) -> None:
        write_source(
            tmp_path,
            "tests/test_raises.py",
            f"""
            import pytest


            def test_broad() -> None:
                {DOCUMENTED}
                with pytest.raises(Exception):
                    int("x")
                with pytest.raises((ValueError, BaseException)):
                    int("x")
                with pytest.raises(ValueError, match="invalid"):
                    int("x")
            """,
        )
        assert suite_codes(tmp_path) == [
            ("test_raises.py", 6, "TST003", "Exception"),
            ("test_raises.py", 8, "TST003", "BaseException"),
        ]


class TestDeterministicParametrize:
    def test_a_random_value_that_reaches_the_id_is_reported(
        self, tmp_path: Path
    ) -> None:
        """Cenário: as formas que o ``pytest -n 2`` medido derrubou.

        Esperado: ``randint`` cru, ``str(uuid4())``, ``uuid4().hex`` e
        ``datetime.now()`` em ``ids=`` / ``id=`` saem como ``TST004``.
        """
        write_source(
            tmp_path,
            "tests/test_ids.py",
            f"""
            import random
            from datetime import datetime
            from uuid import uuid4

            import pytest


            @pytest.mark.parametrize("n", [random.randint(0, 9)])
            def test_int(n: int) -> None:
                {DOCUMENTED}
                assert n >= 0


            @pytest.mark.parametrize("v", [str(uuid4())])
            def test_str(v: str) -> None:
                {DOCUMENTED}
                assert v


            @pytest.mark.parametrize("v", [uuid4().hex])
            def test_hex(v: str) -> None:
                {DOCUMENTED}
                assert v


            @pytest.mark.parametrize("n", [1], ids=[str(datetime.now())])
            def test_ids(n: int) -> None:
                {DOCUMENTED}
                assert n


            @pytest.mark.parametrize("n", [pytest.param(1, id=f"{{uuid4()}}")])
            def test_param_id(n: int) -> None:
                {DOCUMENTED}
                assert n
            """,
        )
        assert [item[1:3] for item in suite_codes(tmp_path)] == [
            (8, "TST004"),
            (14, "TST004"),
            (20, "TST004"),
            (26, "TST004"),
            (32, "TST004"),
        ]

    def test_an_object_ided_by_position_is_clean(self, tmp_path: Path) -> None:
        """Cenário: ``uuid4()`` e ``datetime.now()`` crus, em tupla e em ``param``.

        Esperado: limpo — o pytest dá a eles o id ``v0``, ``v1``, igual em
        todo worker (medido com ``pytest -n 2``).
        """
        write_source(
            tmp_path,
            "tests/test_ids.py",
            f"""
            from datetime import datetime
            from uuid import uuid4

            import pytest


            @pytest.mark.parametrize(
                "v",
                [uuid4(), datetime.now(), (uuid4(), 1), pytest.param(uuid4())],
            )
            def test_value(v: object) -> None:
                {DOCUMENTED}
                assert v is not None


            @pytest.mark.parametrize("n", [1, 2], ids=["one", "two"])
            def test_fixed(n: int) -> None:
                {DOCUMENTED}
                assert n
            """,
        )
        assert suite_codes(tmp_path) == []
