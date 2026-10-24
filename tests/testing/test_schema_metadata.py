"""``assert_schema_metadata`` / ``tempest lint-schemas`` (#616).

Each case builds the schema shapes the transport service exported without
metadata (370 fields) and the near-misses the check must accept: a field
holding another schema needs no examples, and an inherited field is
reported once, on the class that declares it.
"""

from __future__ import annotations

import io
import sys
import textwrap
from pathlib import Path
from types import ModuleType

import pytest
from pydantic import BaseModel, Field
from typer.testing import CliRunner

from tempest_fastapi_sdk import BaseResponseSchema, BaseSchema
from tempest_fastapi_sdk.cli.main import app as cli_app
from tempest_fastapi_sdk.cli.schema_lint import run_lint_schemas
from tempest_fastapi_sdk.testing import (
    SchemaMetadataFinding,
    assert_schema_metadata,
    schema_metadata_findings,
)

runner = CliRunner()


class AddressSchema(BaseSchema):
    """A nested schema, fully documented."""

    city: str = Field(title="City", description="City name.", examples=["Picos"])


class _PartialBaseSchema(BaseSchema):
    """A private base the module never exports."""

    code: str = Field(title="Code", description="Internal code.")


class OrderSchema(_PartialBaseSchema):
    """An exported schema with every kind of gap."""

    total: int
    note: str = Field(title=" ", description="Free text.", examples=[])
    address: AddressSchema = Field(title="Address", description="Delivery.")
    items: list[AddressSchema] | None = None


class OrderResponseSchema(BaseResponseSchema):
    """Inherits the SDK base, whose fields are documented."""

    total: int = Field(title="Total", description="In cents.", examples=[4990])


def as_module(*schemas: type[BaseModel]) -> ModuleType:
    """Build a module exporting the given schemas.

    Args:
        *schemas (type[BaseModel]): The schemas in ``__all__``.

    Returns:
        ModuleType: The module.
    """
    module = ModuleType("demo.schemas")
    for schema in schemas:
        setattr(module, schema.__name__, schema)
    module.__all__ = [schema.__name__ for schema in schemas]
    return module


class TestFindings:
    def test_every_gap_is_reported_once_on_its_declaring_class(self) -> None:
        """Cenário: campo sem nada, título em branco, exemplos vazios, aninhado.

        Esperado: um achado por campo, na classe que o declara — o ``code``
        da base privada também, uma vez só.
        """
        findings = schema_metadata_findings(
            as_module(OrderSchema, OrderResponseSchema, AddressSchema)
        )
        here = __name__
        assert [
            (finding.schema, finding.field, finding.missing) for finding in findings
        ] == [
            (f"{here}.OrderSchema", "items", ("title", "description")),
            (f"{here}.OrderSchema", "note", ("title", "examples")),
            (f"{here}.OrderSchema", "total", ("title", "description", "examples")),
            (f"{here}._PartialBaseSchema", "code", ("examples",)),
        ]

    def test_a_documented_module_is_clean(self) -> None:
        assert (
            schema_metadata_findings(as_module(AddressSchema, OrderResponseSchema))
            == []
        )

    def test_the_assertion_lists_each_field(self) -> None:
        with pytest.raises(AssertionError) as caught:
            assert_schema_metadata(as_module(OrderSchema))
        message = str(caught.value)
        assert message.startswith(
            "4 schema field(s) without title/description/examples"
        )
        assert (
            f"{__name__}.OrderSchema.total: missing title, description, examples"
            in message
        )

    def test_a_finding_renders_on_one_line(self) -> None:
        finding = SchemaMetadataFinding(schema="m.S", field="f", missing=("title",))
        assert finding.render() == "m.S.f: missing title"


class TestCommand:
    def write_project(self, root: Path, body: str) -> None:
        """Write ``src/schemas/__init__.py`` under ``root``.

        Args:
            root (Path): The project root.
            body (str): The package source.
        """
        package = root / "src" / "schemas"
        package.mkdir(parents=True)
        (root / "src" / "__init__.py").write_text("")
        (package / "__init__.py").write_text(textwrap.dedent(body))

    def forget(self) -> None:
        """Drop the imported ``src`` modules so the next project imports fresh."""
        for name in [
            name for name in sys.modules if name == "src" or name.startswith("src.")
        ]:
            del sys.modules[name]

    def test_reports_and_fails(self, tmp_path: Path) -> None:
        self.write_project(
            tmp_path,
            """
            from tempest_fastapi_sdk import BaseSchema


            class ItemSchema(BaseSchema):
                name: str

            __all__ = ["ItemSchema"]
            """,
        )
        stream = io.StringIO()
        try:
            code = run_lint_schemas(root=str(tmp_path), stream=stream)
        finally:
            self.forget()
        assert code == 1
        assert stream.getvalue().splitlines() == [
            "src.schemas.ItemSchema.name: missing title, description, examples",
            "1 schema field(s) without documentation.",
        ]

    def test_a_package_that_does_not_import_exits_2(self, tmp_path: Path) -> None:
        self.write_project(tmp_path, "import not_a_module\n")
        stream = io.StringIO()
        try:
            code = run_lint_schemas(root=str(tmp_path), stream=stream)
        finally:
            self.forget()
        assert code == 2
        assert stream.getvalue().startswith("cannot import src.schemas:")

    def test_the_scaffold_passes(self, tmp_path: Path) -> None:
        """Cenário: o projeto que o ``tempest new`` escreve.

        Esperado: o comando sai com ``0`` — o scaffold cumpre o que o teste
        dele mesmo cobra.
        """
        result = runner.invoke(cli_app, ["new", "demo", "--path", str(tmp_path)])
        assert result.exit_code == 0, result.stdout
        project = tmp_path / "demo"
        assert (project / "tests" / "test_schemas.py").is_file()
        try:
            checked = runner.invoke(cli_app, ["lint-schemas", "--root", str(project)])
        finally:
            self.forget()
        assert checked.exit_code == 0, checked.stdout
        assert "src.schemas is fully documented" in checked.stdout
