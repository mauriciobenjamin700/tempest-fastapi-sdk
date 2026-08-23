"""Defects found generating against a real specification, pinned as tests.

Every case here produced a package that did not import, did not lint, or
silently changed what the specification said. The end-to-end tests
generate with ``run_format=False`` on purpose: the ``ruff --fix`` pass the
command runs afterwards used to hide three of these, so the emitter itself
has to be correct.
"""

from __future__ import annotations

import ast
import importlib
import importlib.util
import shutil
import subprocess
import sys
import textwrap
from pathlib import Path
from types import ModuleType
from typing import Any

import pytest
from pydantic import BaseModel

from tempest_fastapi_sdk.openapi.emit_client import _docstring_lines as _client_doc
from tempest_fastapi_sdk.openapi.emit_schemas import (
    _argument_lines,
    _literal,
)
from tempest_fastapi_sdk.openapi.emit_schemas import (
    _docstring_lines as _schema_doc,
)
from tempest_fastapi_sdk.openapi.generate import generate_integration
from tempest_fastapi_sdk.openapi.ir import OperationIR
from tempest_fastapi_sdk.openapi.naming import (
    MAX_ENUM_MEMBER,
    enum_member_name,
    field_name,
    unique,
)
from tempest_fastapi_sdk.openapi.parse import parse_spec
from tempest_fastapi_sdk.openapi.source import string_literal as _string_literal
from tests.openapi.conftest import HOSTILE_DESCRIPTION, HOSTILE_ENUM_VALUE

_MAX_LINE: int = 88


def _model_with_field(schemas: ModuleType, field: str) -> type[BaseModel]:
    """Find the exported model declaring a given field.

    Args:
        schemas (ModuleType): The generated ``schemas`` module.
        field (str): The Python field name to look for.

    Returns:
        type[BaseModel]: The model that declares it.

    ``__all__`` also carries the generated enums, and ``model_fields`` on an
    ``Enum`` subclass raises ``AttributeError`` from ``EnumMeta.__getattr__``
    rather than being absent, so the lookup has to be guarded.
    """
    for name in schemas.__all__:
        exported = getattr(schemas, name)
        if not (isinstance(exported, type) and issubclass(exported, BaseModel)):
            continue
        if field in exported.model_fields:
            return exported
    raise AssertionError(f"no exported model declares {field!r}")


def _import_generated(directory: Path, package: str) -> ModuleType:
    """Import a generated package's ``schemas`` module from disk.

    Args:
        directory (Path): Directory holding the generated package.
        package (str): Package (directory) name.

    Returns:
        ModuleType: The imported ``schemas`` module.
    """
    root = str(directory.parent)
    if root not in sys.path:
        sys.path.insert(0, root)
    for stale in [name for name in sys.modules if name.startswith(package)]:
        del sys.modules[stale]
    assert importlib.util.find_spec(package) is not None
    return importlib.import_module(f"{package}.schemas")


class TestStringLiterals:
    """Text from the specification survives the trip into source."""

    @pytest.mark.parametrize(
        "value",
        [
            'a "quoted" word',
            "an apostrophe's place",
            "a backslash \\# escape",
            "two\nlines",
            "a\ttab",
            "a \x01 control character",
            "plain",
        ],
    )
    def test_round_trips_through_eval(self, value: str) -> None:
        """Every rendered literal evaluates back to the original text.

        The shortcut this replaced interpolated the value raw whenever
        ``repr`` had used single quotes — which is every string without an
        apostrophe — so a newline emitted an unterminated literal and a
        backslash changed the value in silence.
        """
        assert ast.literal_eval(_string_literal(value)) == value

    def test_double_quoted_by_default(self) -> None:
        """The project mandates double quotes, ``repr`` prefers single."""
        assert _string_literal("plain") == '"plain"'
        assert _string_literal("an apostrophe's place") == '"an apostrophe\'s place"'

    def test_switches_to_single_quotes_to_avoid_escapes(self) -> None:
        """``ruff format`` normalizes this way; disagreeing fails its check."""
        assert _string_literal('a "quoted" word') == "'a \"quoted\" word'"

    def test_nested_containers_are_rebuilt(self) -> None:
        """Strings inside a list or dict example get escaped too."""
        value: dict[str, Any] = {"escapes": ["\\#", 'a "b"'], "n": 1}
        rendered = _literal(value)
        assert ast.literal_eval(rendered) == value
        assert rendered.startswith('{"escapes": ')


class TestLongArguments:
    """A ``Field`` argument past the line budget is split, not left long."""

    def test_split_preserves_the_text_exactly(self) -> None:
        """Adjacent literals concatenate back to the specification's wording."""
        argument = f"description={_string_literal(HOSTILE_DESCRIPTION)}"
        lines = _argument_lines(argument, "        ")
        assert all(len(line) <= _MAX_LINE for line in lines)
        source = textwrap.dedent("\n".join(lines))
        expression = source.strip().removeprefix("description=").removesuffix(",")
        assert ast.literal_eval(expression) == HOSTILE_DESCRIPTION

    def test_splits_only_as_far_as_the_budget_needs(self) -> None:
        """One piece is a legitimate split.

        This pinned the opposite invariant until a real specification
        disproved it. Forcing a second piece made ``ruff format`` **join
        them back**, which is the same failure as leaving the line long,
        approached from the other side: a lone parenthesized literal is
        un-parenthesized only when the collapsed line then fits, and a
        collapsed line that fits is one the emitter never split.
        """
        lines = _argument_lines(f'description="{"x" * 70}"', "        ")
        assert all(len(line) <= _MAX_LINE for line in lines)
        assert len([line for line in lines if line.strip().startswith('"')]) == 1

    def test_short_argument_stays_on_one_line(self) -> None:
        """The split only happens when the flat form overruns."""
        assert _argument_lines('title="Email"', "        ") == [
            '        title="Email",'
        ]


class TestSchemaDocstring:
    """A wrapped docstring has to survive ``ruff format`` pulling quotes up."""

    _SUMMARY: str = (
        "Allowed values for "
        "OrderTransactionPaymentPaymentMethodTransactionSecurityStatus."
    )

    def test_wraps_to_at_least_two_content_lines(self) -> None:
        """One content line is where the formatter undoes the wrap.

        Mercado Pago's enum names reach 61 characters, so the summary wraps
        to exactly one content line at 88 columns. ``ruff format`` then
        joins the closing quotes back onto it without re-checking the
        budget, and the generated line ships at 91.
        """
        lines = _schema_doc(self._SUMMARY, "    ")
        assert all(len(line) <= _MAX_LINE for line in lines)
        content = [line for line in lines if line.strip() != '"""']
        assert len(content) >= 2

    @pytest.mark.skipif(shutil.which("ruff") is None, reason="ruff not on PATH")
    def test_formatter_leaves_the_wrap_alone(self, tmp_path: Path) -> None:
        """The real formatter is the authority, not the emitter's arithmetic."""
        lines = _schema_doc(self._SUMMARY, "    ")
        source = "class Probe:\n" + "\n".join(lines) + '\n\n    A = "a"\n'
        module = tmp_path / "probe.py"
        module.write_text(source, encoding="utf-8")
        completed = subprocess.run(
            ["ruff", "format", "--isolated", "--line-length", "88", str(module)],
            capture_output=True,
            text=True,
            check=False,
        )
        assert completed.returncode == 0, completed.stdout + completed.stderr
        formatted = module.read_text(encoding="utf-8").splitlines()
        long_lines = [line for line in formatted if len(line) > _MAX_LINE]
        assert long_lines == [], long_lines

    def test_short_summary_stays_on_one_line(self) -> None:
        """The re-wrap only fires when the flat form overruns."""
        assert _schema_doc("Allowed values for Status.", "    ") == [
            '    """Allowed values for Status."""'
        ]


class TestClientDocstring:
    """The emitted docstring is valid, non-warning Python."""

    def test_backslash_in_prose_makes_it_raw(self) -> None:
        """``\\#`` is not a Python escape.

        ``W605`` before 3.12, a ``SyntaxWarning`` from it.

        The generator's own ``ruff --fix`` pass adds the ``r`` prefix, so
        this only ever failed for a caller passing ``--no-format``.
        """
        operation = OperationIR(
            name="list_charges",
            http_method="get",
            path="/charges",
            summary="Encode (%, \\#, /) before sending.",
            description="",
            parameters=(),
            body_annotation=None,
            body_required=True,
            response_annotation=None,
            success_status="204",
            error_statuses=(),
        )
        assert _client_doc(operation)[0].startswith('        r"""')

    def test_plain_prose_is_not_raw(self) -> None:
        """The prefix is added only when something needs it."""
        operation = OperationIR(
            name="list_charges",
            http_method="get",
            path="/charges",
            summary="List the charges.",
            description="",
            parameters=(),
            body_annotation=None,
            body_required=True,
            response_annotation=None,
            success_status="204",
            error_statuses=(),
        )
        assert _client_doc(operation)[0].startswith('        """')


class TestNames:
    """Generated identifiers are valid and idiomatic Python."""

    def test_class_collision_stays_capwords(self) -> None:
        """``Transaction_2`` is not CapWords and fails the consumer's ``N801``."""
        taken: set[str] = {"Transaction"}
        assert unique("Transaction", taken, separator="") == "Transaction2"

    def test_field_collision_keeps_snake_case(self) -> None:
        """The default separator is still the right one for a field."""
        taken: set[str] = {"reference"}
        assert unique("reference", taken) == "reference_2"

    def test_leading_digit_is_prefixed(self) -> None:
        """``2fa`` is not an identifier; ``_2fa`` would be a Pydantic private attr."""
        assert field_name("2fa") == "field_2fa"

    def test_long_enum_member_is_capped(self) -> None:
        """A name built from a sentence-long value overruns before the value does."""
        member = enum_member_name(HOSTILE_ENUM_VALUE)
        assert len(member) <= MAX_ENUM_MEMBER
        assert member.isidentifier()
        assert not member.endswith("_")


class TestPathParameters:
    """The path template and the signature agree."""

    def test_undeclared_placeholder_is_synthesized(
        self, hostile_document: dict[str, Any]
    ) -> None:
        """Otherwise the emitted f-string references an undefined name."""
        spec = parse_spec(hostile_document, client_name="hostile")
        operation = next(
            item for item in spec.client.operations if item.name == "get_receipt"
        )
        assert [p.name for p in operation.path_parameters] == ["receipt_id"]
        assert any("no parameter declares" in note for note in spec.unsupported)

    def test_declared_but_absent_parameter_is_skipped(
        self, hostile_document: dict[str, Any]
    ) -> None:
        """An argument the request never carries is worse than a missing one."""
        spec = parse_spec(hostile_document, client_name="hostile")
        operation = next(
            item for item in spec.client.operations if item.name == "get_account_charge"
        )
        assert "expand" not in {p.wire_name for p in operation.parameters}
        assert any("absent from the path template" in n for n in spec.unsupported)

    def test_order_follows_the_template_not_the_spec(
        self, hostile_document: dict[str, Any]
    ) -> None:
        """Path parameters are the method's only positional arguments."""
        spec = parse_spec(hostile_document, client_name="hostile")
        operation = next(
            item for item in spec.client.operations if item.name == "get_account_charge"
        )
        assert [p.wire_name for p in operation.path_parameters] == [
            "accountId",
            "chargeId",
        ]


class TestHostileGeneration:
    """End to end: the hostile specification yields a usable package."""

    @pytest.fixture
    def generated(self, hostile_spec_file: Path, tmp_path: Path) -> Path:
        """Generate the hostile package without the formatting pass.

        Args:
            hostile_spec_file (Path): The specification fixture.
            tmp_path (Path): pytest's per-test temporary directory.

        Returns:
            Path: The generated package directory.
        """
        result = generate_integration(
            str(hostile_spec_file),
            target=tmp_path,
            name="hostile",
            out=tmp_path / "pkg" / "hostile_gen",
            run_format=False,
        )
        return result.written[0].parent

    @pytest.mark.skipif(shutil.which("ruff") is None, reason="ruff not on PATH")
    def test_passes_ruff_check(self, generated: Path) -> None:
        """Lint-clean before any fixing pass, class names included.

        ``N`` is selected on top of the billing suite's ``E,F,I,W``
        because the collision suffix is exactly what it catches.
        """
        completed = subprocess.run(
            ["ruff", "check", "--isolated", "--select", "E,F,I,W,N", "."],
            cwd=generated,
            capture_output=True,
            text=True,
            check=False,
        )
        assert completed.returncode == 0, completed.stdout + completed.stderr

    @pytest.mark.skipif(shutil.which("ruff") is None, reason="ruff not on PATH")
    def test_passes_ruff_format_check(self, generated: Path) -> None:
        """Already formatted — the split literals are what a person writes."""
        completed = subprocess.run(
            ["ruff", "format", "--isolated", "--check", "."],
            cwd=generated,
            capture_output=True,
            text=True,
            check=False,
        )
        assert completed.returncode == 0, completed.stdout + completed.stderr

    def test_no_line_overruns_the_budget(self, generated: Path) -> None:
        """``ruff format`` never breaks a string, so the emitter must."""
        for path in sorted(generated.glob("*.py")):
            long_lines = [
                line
                for line in path.read_text(encoding="utf-8").splitlines()
                if len(line) > _MAX_LINE
            ]
            assert long_lines == [], f"{path.name}: {long_lines}"

    def test_imports_and_keeps_the_text_verbatim(self, generated: Path) -> None:
        """The description reaches the model exactly as the spec wrote it."""
        schemas = _import_generated(generated, "hostile_gen")
        model = _model_with_field(schemas, "reference")
        field = model.model_fields["reference"]
        assert field.description == HOSTILE_DESCRIPTION
        assert field.title == 'The "reference"'
        assert field.examples == [
            {"value": 'a "quoted" one', "escapes": ["\\#", "line\nbreak"]}
        ]

    def test_long_enum_value_is_intact(self, generated: Path) -> None:
        """A value past the budget is parenthesized, not truncated."""
        schemas = _import_generated(generated, "hostile_gen")
        values = set(schemas.ChargeStatus.values())
        assert HOSTILE_ENUM_VALUE in values

    def test_digit_field_keeps_its_wire_name(self, generated: Path) -> None:
        """The rename is invisible on the wire — the aliases carry it.

        Both halves are asserted because they are emitted separately:
        ``alias`` alone would rename the parameter a type-checker sees, so
        the wire name lives in ``validation_alias`` (reading) and
        ``serialization_alias`` (writing) instead.
        """
        schemas = _import_generated(generated, "hostile_gen")
        model = _model_with_field(schemas, "field_2fa")
        assert model.model_fields["field_2fa"].validation_alias == "2fa"
        assert model.model_fields["field_2fa"].serialization_alias == "2fa"
