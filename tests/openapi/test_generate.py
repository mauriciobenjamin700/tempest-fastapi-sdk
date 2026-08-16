"""End-to-end tests: emit, lint, import and exercise the generated package."""

from __future__ import annotations

import ast
import asyncio
import importlib.util
import json
import shutil
import subprocess
import sys
from pathlib import Path
from types import ModuleType
from typing import Any

import httpx
import pytest
from typer.testing import CliRunner

from tempest_fastapi_sdk.cli.main import app
from tempest_fastapi_sdk.openapi.generate import (
    default_output_dir,
    generate_integration,
    suggest_client_class,
)
from tempest_fastapi_sdk.openapi.loader import SpecError
from tempest_fastapi_sdk.openapi.source import MAX_LINE, unsupported_comment


def _load_package(directory: Path, package: str) -> ModuleType:
    """Import a generated package from disk under a unique name.

    Args:
        directory (Path): Directory holding the generated package.
        package (str): Package (directory) name.

    Returns:
        ModuleType: The imported ``client`` module, with its sibling
        ``schemas`` module importable as a submodule.
    """
    root = str(directory.parent)
    if root not in sys.path:
        sys.path.insert(0, root)
    for stale in [name for name in sys.modules if name.startswith(package)]:
        del sys.modules[stale]
    spec = importlib.util.find_spec(package)
    assert spec is not None
    return importlib.import_module(f"{package}.client")


class TestGenerateIntegration:
    """The generator writes a complete, conventional package."""

    def test_writes_the_three_files(
        self, billing_spec_file: Path, tmp_path: Path
    ) -> None:
        """A full run emits ``__init__``, ``schemas`` and ``client``."""
        project = tmp_path / "proj"
        (project / "src").mkdir(parents=True)
        result = generate_integration(
            str(billing_spec_file), target=project, name="billing", run_format=False
        )
        assert sorted(path.name for path in result.written) == [
            "__init__.py",
            "client.py",
            "schemas.py",
        ]
        assert result.schema_count == 4
        assert result.operation_count == 4

    def test_default_location_follows_the_source_root(self, tmp_path: Path) -> None:
        """``app/`` layouts are honored, not just ``src/``."""
        src_project = tmp_path / "with_src"
        (src_project / "src").mkdir(parents=True)
        app_project = tmp_path / "with_app"
        (app_project / "app").mkdir(parents=True)
        assert default_output_dir(src_project, "billing") == (
            src_project / "src" / "integrations" / "billing"
        )
        assert default_output_dir(app_project, "billing") == (
            app_project / "app" / "integrations" / "billing"
        )

    def test_out_overrides_the_convention(
        self, billing_spec_file: Path, tmp_path: Path
    ) -> None:
        """``--out`` puts the package wherever the caller asks."""
        destination = tmp_path / "vendor" / "billing"
        result = generate_integration(
            str(billing_spec_file),
            target=tmp_path,
            name="billing",
            out=destination,
            run_format=False,
        )
        assert all(path.parent == destination for path in result.written)

    def test_name_defaults_to_the_spec_title(
        self, billing_spec_file: Path, tmp_path: Path
    ) -> None:
        """``info.title`` names the package when ``--name`` is omitted."""
        result = generate_integration(
            str(billing_spec_file), target=tmp_path, run_format=False
        )
        assert result.written[0].parent.name == "billing_api"

    def test_missing_title_without_name_is_an_error(self, tmp_path: Path) -> None:
        """There is no way to name the package, so it fails explicitly."""
        path = tmp_path / "spec.json"
        path.write_text(
            json.dumps(
                {
                    "openapi": "3.1.0",
                    "info": {"version": "1"},
                    "paths": {"/x": {"get": {"operationId": "x", "responses": {}}}},
                }
            ),
            encoding="utf-8",
        )
        with pytest.raises(SpecError, match="cannot be named automatically"):
            generate_integration(str(path), target=tmp_path, run_format=False)

    def test_schemas_only_skips_the_client(
        self, billing_spec_file: Path, tmp_path: Path
    ) -> None:
        """``--schemas-only`` emits no client module."""
        result = generate_integration(
            str(billing_spec_file),
            target=tmp_path,
            name="billing",
            schemas_only=True,
            run_format=False,
        )
        assert sorted(path.name for path in result.written) == [
            "__init__.py",
            "schemas.py",
        ]

    def test_existing_files_are_skipped_without_force(
        self, billing_spec_file: Path, tmp_path: Path
    ) -> None:
        """A second run refuses to overwrite, matching the other generators."""
        first = generate_integration(
            str(billing_spec_file), target=tmp_path, name="billing", run_format=False
        )
        second = generate_integration(
            str(billing_spec_file), target=tmp_path, name="billing", run_format=False
        )
        assert first.written and not first.skipped
        assert second.written == [] and len(second.skipped) == 3

    def test_force_overwrites(self, billing_spec_file: Path, tmp_path: Path) -> None:
        """``--force`` refreshes a previously generated package."""
        generate_integration(
            str(billing_spec_file), target=tmp_path, name="billing", run_format=False
        )
        again = generate_integration(
            str(billing_spec_file),
            target=tmp_path,
            name="billing",
            force=True,
            run_format=False,
        )
        assert len(again.written) == 3 and again.skipped == []

    def test_regeneration_is_byte_identical(
        self, billing_spec_file: Path, tmp_path: Path
    ) -> None:
        """An unchanged specification produces an unchanged file.

        Without this a refresh would show a spurious diff on every run and
        nobody would be able to see what actually changed.
        """
        first = generate_integration(
            str(billing_spec_file),
            target=tmp_path / "a",
            name="billing",
            run_format=False,
        )
        second = generate_integration(
            str(billing_spec_file),
            target=tmp_path / "b",
            name="billing",
            run_format=False,
        )
        for left, right in zip(
            sorted(first.written), sorted(second.written), strict=True
        ):
            assert left.read_text() == right.read_text()

    def test_unsupported_notes_reach_the_result(
        self, billing_spec_file: Path, tmp_path: Path
    ) -> None:
        """The header parameter the generator skips is reported."""
        result = generate_integration(
            str(billing_spec_file), target=tmp_path, name="billing", run_format=False
        )
        assert any("X-Trace" in note for note in result.unsupported)

    def test_suggest_client_class(self) -> None:
        """The client class name is predictable before generating."""
        assert suggest_client_class("billing") == "BillingClient"


class TestGeneratedCodeQuality:
    """The output is code the project's own gates accept."""

    @pytest.fixture
    def generated(self, billing_spec_file: Path, tmp_path: Path) -> Path:
        """Generate the billing package and return its directory.

        Args:
            billing_spec_file (Path): The specification fixture.
            tmp_path (Path): pytest's per-test temporary directory.

        Returns:
            Path: The generated package directory.
        """
        result = generate_integration(
            str(billing_spec_file),
            target=tmp_path,
            name="billing",
            out=tmp_path / "billing",
            run_format=False,
        )
        return result.written[0].parent

    def test_every_module_parses(self, generated: Path) -> None:
        """Emitted source is valid Python."""
        for path in sorted(generated.glob("*.py")):
            ast.parse(path.read_text())

    def test_every_module_class_and_function_has_a_docstring(
        self, generated: Path
    ) -> None:
        """The project requires a docstring on every public surface."""
        missing: list[str] = []
        for path in sorted(generated.glob("*.py")):
            tree = ast.parse(path.read_text())
            if ast.get_docstring(tree) is None:
                missing.append(f"{path.name}: module")
            for node in ast.walk(tree):
                if (
                    isinstance(
                        node, ast.ClassDef | ast.FunctionDef | ast.AsyncFunctionDef
                    )
                    and ast.get_docstring(node) is None
                ):
                    missing.append(f"{path.name}: {node.name}")
        assert missing == []

    def test_no_single_quoted_strings(self, generated: Path) -> None:
        """The project mandates double quotes; ruff format would rewrite."""
        for path in sorted(generated.glob("*.py")):
            assert "= '" not in path.read_text()

    @pytest.mark.skipif(shutil.which("ruff") is None, reason="ruff not on PATH")
    def test_passes_ruff_check(self, generated: Path) -> None:
        """Generated code passes lint **before** the formatting pass.

        The fixture generates with ``run_format=False``, so this asserts
        the emitter itself produces lint-clean code — ``--no-format`` and a
        machine without ruff installed must both still yield a usable
        package.

        ``--isolated`` matters: run against the repo's own configuration,
        ruff would classify ``tempest_fastapi_sdk`` as first-party (the
        package directory is right there) and demand a separate import
        group. In a real consumer project it is an installed dependency,
        which is what the isolated defaults model.

        This is the strongest single assertion in the suite — it caught a
        missing ``UUID`` import, an un-imported enum, an over-long
        docstring line and two import-ordering mistakes that no
        schema-shape assertion would have noticed.
        """
        completed = subprocess.run(
            ["ruff", "check", "--isolated", "--select", "E,F,I,W", "."],
            cwd=generated,
            capture_output=True,
            text=True,
            check=False,
        )
        assert completed.returncode == 0, completed.stdout + completed.stderr

    @pytest.mark.skipif(shutil.which("ruff") is None, reason="ruff not on PATH")
    def test_passes_ruff_format_check(self, generated: Path) -> None:
        """Generated code is already formatted, before any formatting pass."""
        completed = subprocess.run(
            ["ruff", "format", "--isolated", "--check", "."],
            cwd=generated,
            capture_output=True,
            text=True,
            check=False,
        )
        assert completed.returncode == 0, completed.stdout + completed.stderr

    def test_metadata_reaches_the_fields(self, generated: Path) -> None:
        """Descriptions and examples from the spec are in the source.

        This is the issue's core request — the generated module is where
        the integration's documentation lives.
        """
        source = (generated / "schemas.py").read_text()
        assert 'title="Email"' in source
        assert 'description="Primary contact email."' in source
        assert 'examples=["ana@example.com"]' in source

    def test_aliases_and_populate_by_name(self, generated: Path) -> None:
        """camelCase wire names survive as aliases with the opt-in config."""
        source = (generated / "schemas.py").read_text()
        assert 'alias="createdAt"' in source
        assert 'alias="class"' in source
        assert "model_config = ConfigDict(populate_by_name=True)" in source

    def test_collection_field_defaults_to_empty(self, generated: Path) -> None:
        """No ``list[X] | None`` in the output."""
        source = (generated / "schemas.py").read_text()
        assert "default_factory=list" in source
        assert "list[str] | None" not in source


class TestGeneratedRuntimeBehavior:
    """The generated package actually works against a mocked transport."""

    @pytest.fixture
    def billing(self, billing_spec_file: Path, tmp_path: Path) -> ModuleType:
        """Generate and import the billing client module.

        Args:
            billing_spec_file (Path): The specification fixture.
            tmp_path (Path): pytest's per-test temporary directory.

        Returns:
            ModuleType: The generated ``client`` module.
        """
        generate_integration(
            str(billing_spec_file),
            target=tmp_path,
            name="billing",
            out=tmp_path / "pkg" / "billing_gen",
            run_format=False,
        )
        return _load_package(tmp_path / "pkg" / "billing_gen", "billing_gen")

    def test_base_url_comes_from_servers(self, billing: ModuleType) -> None:
        """``servers[0].url`` is exported for the caller to use."""
        assert billing.DEFAULT_BASE_URL == "https://api.billing.example.com/v2"

    def test_response_is_validated_into_schemas(self, billing: ModuleType) -> None:
        """A JSON array response comes back as typed models."""
        schemas = importlib.import_module("billing_gen.schemas")

        def handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(
                200,
                json=[
                    {
                        "id": "8f2c1e40-0000-4000-8000-000000000000",
                        "emailAddress": "ana@example.com",
                        "createdAt": "2026-07-25T10:00:00Z",
                        "status": "past_due",
                        "tags": ["vip"],
                        "billingAddress": {"line1": "Rua A", "countryCode": "BR"},
                        "class": "gold",
                    }
                ],
            )

        async def run() -> Any:
            from tempest_fastapi_sdk import HTTPClient

            http = HTTPClient(
                base_url=billing.DEFAULT_BASE_URL,
                transport=httpx.MockTransport(handler),
            )
            async with http:
                return await billing.BillingClient(http).list_customers()

        rows = asyncio.run(run())
        assert isinstance(rows[0], schemas.Customer)
        assert rows[0].email_address == "ana@example.com"
        assert rows[0].class_ == "gold"
        assert rows[0].billing_address.country_code == "BR"
        assert rows[0].tags == ["vip"]

    def test_enum_query_param_sends_its_value(self, billing: ModuleType) -> None:
        """An enum reaches the query string as its value.

        Regression: ``BaseStrEnum`` is a ``str``/``Enum`` mixin whose
        ``str()`` is ``"Class.MEMBER"``, so passing the member straight to
        httpx produced ``status=CustomerStatus.PAST_DUE``.
        """
        schemas = importlib.import_module("billing_gen.schemas")
        seen: dict[str, str] = {}

        def handler(request: httpx.Request) -> httpx.Response:
            seen["url"] = str(request.url)
            return httpx.Response(200, json=[])

        async def run() -> None:
            from tempest_fastapi_sdk import HTTPClient

            http = HTTPClient(
                base_url=billing.DEFAULT_BASE_URL,
                transport=httpx.MockTransport(handler),
            )
            async with http:
                await billing.BillingClient(http).list_customers(
                    page_size=25, status=schemas.CustomerStatus.PAST_DUE
                )

        asyncio.run(run())
        assert "status=past_due" in seen["url"]
        assert "pageSize=25" in seen["url"]

    def test_omitted_query_params_are_absent(self, billing: ModuleType) -> None:
        """A ``None`` argument does not reach the query string."""
        seen: dict[str, str] = {}

        def handler(request: httpx.Request) -> httpx.Response:
            seen["url"] = str(request.url)
            return httpx.Response(200, json=[])

        async def run() -> None:
            from tempest_fastapi_sdk import HTTPClient

            http = HTTPClient(
                base_url=billing.DEFAULT_BASE_URL,
                transport=httpx.MockTransport(handler),
            )
            async with http:
                await billing.BillingClient(http).list_customers()

        asyncio.run(run())
        assert "?" not in seen["url"]

    def test_request_body_is_serialized_with_wire_names(
        self, billing: ModuleType
    ) -> None:
        """The body goes out camelCase, matching the third party."""
        schemas = importlib.import_module("billing_gen.schemas")
        seen: dict[str, Any] = {}

        def handler(request: httpx.Request) -> httpx.Response:
            seen["body"] = json.loads(request.content)
            seen["method"] = request.method
            return httpx.Response(
                201,
                json={
                    "id": "8f2c1e40-0000-4000-8000-000000000001",
                    "emailAddress": "novo@example.com",
                    "createdAt": "2026-07-25T11:00:00Z",
                },
            )

        async def run() -> Any:
            from tempest_fastapi_sdk import HTTPClient

            http = HTTPClient(
                base_url=billing.DEFAULT_BASE_URL,
                transport=httpx.MockTransport(handler),
            )
            async with http:
                return await billing.BillingClient(http).create_customer(
                    body=schemas.CustomerCreate(
                        email_address="novo@example.com",
                        display_name="Novo",
                        billing_address=schemas.Address(
                            line1="Rua B", country_code="BR"
                        ),
                    )
                )

        created = asyncio.run(run())
        assert seen["method"] == "POST"
        assert seen["body"] == {
            "emailAddress": "novo@example.com",
            "displayName": "Novo",
            "billingAddress": {"line1": "Rua B", "countryCode": "BR"},
        }
        assert isinstance(created, schemas.Customer)

    def test_path_parameter_is_interpolated(self, billing: ModuleType) -> None:
        """A path parameter lands in the URL, not the query string."""
        seen: dict[str, str] = {}

        def handler(request: httpx.Request) -> httpx.Response:
            seen["path"] = request.url.path
            return httpx.Response(
                200,
                json={
                    "id": "8f2c1e40-0000-4000-8000-000000000000",
                    "emailAddress": "ana@example.com",
                    "createdAt": "2026-07-25T10:00:00Z",
                },
            )

        async def run() -> None:
            from uuid import UUID

            from tempest_fastapi_sdk import HTTPClient

            http = HTTPClient(
                base_url=billing.DEFAULT_BASE_URL,
                transport=httpx.MockTransport(handler),
            )
            async with http:
                await billing.BillingClient(http).get_customer(
                    UUID("8f2c1e40-0000-4000-8000-000000000000")
                )

        asyncio.run(run())
        assert seen["path"].endswith("/customers/8f2c1e40-0000-4000-8000-000000000000")

    def test_no_content_operation_returns_none(self, billing: ModuleType) -> None:
        """A 204 operation answers ``None``."""

        def handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(204)

        async def run() -> Any:
            from uuid import UUID

            from tempest_fastapi_sdk import HTTPClient

            http = HTTPClient(
                base_url=billing.DEFAULT_BASE_URL,
                transport=httpx.MockTransport(handler),
            )
            async with http:
                return await billing.BillingClient(
                    http
                ).delete_customers_by_customer_id(
                    UUID("8f2c1e40-0000-4000-8000-000000000000")
                )

        assert asyncio.run(run()) is None

    def test_schemas_accept_both_spellings(self, billing: ModuleType) -> None:
        """``populate_by_name`` lets Python names work on input too."""
        schemas = importlib.import_module("billing_gen.schemas")
        by_wire = schemas.Address.model_validate({"line1": "A", "countryCode": "BR"})
        by_python = schemas.Address(line1="A", country_code="BR")
        assert by_wire.country_code == by_python.country_code
        assert by_python.model_dump(by_alias=True)["countryCode"] == "BR"


class TestCyclicModels:
    """Mutually-referencing models are rebuilt so the module imports."""

    def test_cycle_emits_model_rebuild_and_imports(self, tmp_path: Path) -> None:
        """An A/B cycle produces an importable module.

        Without the trailing ``model_rebuild()`` calls the forward
        references never resolve and the first validation fails.
        """
        path = tmp_path / "spec.json"
        path.write_text(
            json.dumps(
                {
                    "openapi": "3.1.0",
                    "info": {"title": "Cyc", "version": "1"},
                    "paths": {},
                    "components": {
                        "schemas": {
                            "A": {
                                "type": "object",
                                "properties": {"b": {"$ref": "#/components/schemas/B"}},
                            },
                            "B": {
                                "type": "object",
                                "properties": {"a": {"$ref": "#/components/schemas/A"}},
                            },
                        }
                    },
                }
            ),
            encoding="utf-8",
        )
        generate_integration(
            str(path),
            target=tmp_path,
            name="cyc",
            out=tmp_path / "pkg" / "cyc_gen",
            run_format=False,
        )
        source = (tmp_path / "pkg" / "cyc_gen" / "schemas.py").read_text()
        assert "A.model_rebuild()" in source
        assert "B.model_rebuild()" in source

        _load_package(tmp_path / "pkg" / "cyc_gen", "cyc_gen")
        schemas = importlib.import_module("cyc_gen.schemas")
        nested = schemas.A.model_validate({"b": {"a": None}})
        assert nested.b is not None


class TestOpenapiClientCommand:
    """``tempest openapi-client`` wires the generator to the CLI."""

    def test_generates_and_reports(
        self, billing_spec_file: Path, tmp_path: Path
    ) -> None:
        """The happy path prints what it wrote and the counts."""
        result = CliRunner().invoke(
            app,
            [
                "openapi-client",
                str(billing_spec_file),
                "--name",
                "billing",
                "--out",
                str(tmp_path / "billing"),
                "--no-format",
            ],
        )
        assert result.exit_code == 0, result.output
        assert "schemas.py" in result.output
        assert "4 schema(s), 4 operation(s)." in result.output

    def test_reports_unsupported_constructs(
        self, billing_spec_file: Path, tmp_path: Path
    ) -> None:
        """A skipped construct is surfaced, never silent."""
        result = CliRunner().invoke(
            app,
            [
                "openapi-client",
                str(billing_spec_file),
                "--name",
                "billing",
                "--out",
                str(tmp_path / "billing"),
                "--no-format",
            ],
        )
        assert "could not be modelled" in result.output
        assert "X-Trace" in result.output

    def test_spec_error_is_a_clean_message(self, tmp_path: Path) -> None:
        """A bad specification exits 2 with a message, not a traceback."""
        result = CliRunner().invoke(
            app, ["openapi-client", str(tmp_path / "nope.json"), "--name", "x"]
        )
        assert result.exit_code == 2
        assert "No such specification file" in result.output

    def test_malformed_header_is_reported(
        self, billing_spec_file: Path, tmp_path: Path
    ) -> None:
        """A ``--header`` typo fails before any file is written."""
        result = CliRunner().invoke(
            app,
            [
                "openapi-client",
                str(billing_spec_file),
                "--header",
                "Authorization Bearer x",
            ],
        )
        assert result.exit_code == 2
        assert "Malformed --header" in result.output

    def test_second_run_without_force_exits_one(
        self, billing_spec_file: Path, tmp_path: Path
    ) -> None:
        """Nothing written and files skipped is a failure, not a no-op."""
        arguments = [
            "openapi-client",
            str(billing_spec_file),
            "--name",
            "billing",
            "--out",
            str(tmp_path / "billing"),
            "--no-format",
        ]
        runner = CliRunner()
        assert runner.invoke(app, arguments).exit_code == 0
        second = runner.invoke(app, arguments)
        assert second.exit_code == 1
        assert "pass --force to overwrite" in second.output

    def test_force_succeeds_on_a_second_run(
        self, billing_spec_file: Path, tmp_path: Path
    ) -> None:
        """``--force`` refreshes the package."""
        arguments = [
            "openapi-client",
            str(billing_spec_file),
            "--name",
            "billing",
            "--out",
            str(tmp_path / "billing"),
            "--no-format",
        ]
        runner = CliRunner()
        runner.invoke(app, arguments)
        assert runner.invoke(app, [*arguments, "--force"]).exit_code == 0

    def test_schemas_only_flag(self, billing_spec_file: Path, tmp_path: Path) -> None:
        """``--schemas-only`` emits no client module."""
        destination = tmp_path / "billing"
        result = CliRunner().invoke(
            app,
            [
                "openapi-client",
                str(billing_spec_file),
                "--name",
                "billing",
                "--out",
                str(destination),
                "--schemas-only",
                "--no-format",
            ],
        )
        assert result.exit_code == 0
        assert not (destination / "client.py").exists()


def _unsupported_document() -> dict[str, Any]:
    """Build a specification carrying one gap of each markable kind.

    Returns:
        dict[str, Any]: The OpenAPI document — a `not` schema and an
        `items`-less array (both field-level), a non-JSON request body and
        an undeclared path placeholder (both operation-level).
    """
    return {
        "openapi": "3.0.3",
        "info": {"title": "Marked API", "version": "1.0.0"},
        "paths": {
            "/things/{thingId}": {
                "post": {
                    "operationId": "createThing",
                    "summary": "Create a thing.",
                    "requestBody": {
                        "content": {
                            "multipart/form-data": {"schema": {"type": "object"}}
                        }
                    },
                    "responses": {"204": {"description": "ok"}},
                }
            }
        },
        "components": {
            "schemas": {
                "Thing": {
                    "type": "object",
                    "properties": {
                        "weird": {"not": {"type": "string"}},
                        "loose": {"type": "array"},
                        "alsoWeird": {"not": {"type": "integer"}},
                    },
                }
            }
        },
    }


class TestUnsupportedMarker:
    """A gap is marked in the file, not only in the terminal.

    The command's summary scrolls away. Someone opening ``schemas.py``
    months later and finding an ``Any`` needs the reason next to it, which
    is what these tests pin — the marker was promised in the docs for
    several releases while never being emitted at all.
    """

    @pytest.fixture
    def generated(self, tmp_path: Path) -> Path:
        """Generate the package without the formatting pass.

        Args:
            tmp_path (Path): pytest's per-test temporary directory.

        Returns:
            Path: The generated package directory.
        """
        spec = tmp_path / "marked.json"
        spec.write_text(json.dumps(_unsupported_document()), encoding="utf-8")
        result = generate_integration(
            str(spec),
            target=tmp_path,
            name="marked",
            out=tmp_path / "pkg" / "marked_gen",
            run_format=False,
        )
        return result.written[0].parent

    def test_field_gap_is_marked_above_the_field(self, generated: Path) -> None:
        """The reason sits next to the ``Any`` it explains."""
        schemas = (generated / "schemas.py").read_text(encoding="utf-8")
        lines = schemas.splitlines()
        index = next(i for i, line in enumerate(lines) if "weird: Any" in line)
        assert "# openapi: unsupported" in lines[index - 1]
        assert "no Python equivalent" in lines[index - 1]

    def test_every_affected_field_is_marked(self, generated: Path) -> None:
        """Two fields hitting a gap each get their own comment.

        The summary de-duplicates; the markers must not, or only the first
        field of a repeated gap would carry its reason.
        """
        schemas = (generated / "schemas.py").read_text(encoding="utf-8")
        assert schemas.count("# openapi: unsupported") == 3

    def test_operation_gap_is_marked_above_the_method(self, generated: Path) -> None:
        """A non-JSON body and a synthesized path argument both show up.

        The comments wrap, so each assertion picks a phrase that cannot
        straddle a line break.
        """
        client = (generated / "client.py").read_text(encoding="utf-8")
        head = client[: client.index("async def create_thing")]
        assert head.count("# openapi: unsupported") == 2
        assert "multipart/form-data" in head
        assert "generated as a required str" in head

    def test_marked_output_still_passes_ruff(self, generated: Path) -> None:
        """A comment `ruff format` would move makes the file unstable."""
        ruff = shutil.which("ruff")
        if ruff is None:
            pytest.skip("ruff not on PATH")
        for arguments in (
            [ruff, "check", "--isolated", "--select", "E,F,I,W,N", "."],
            [ruff, "format", "--isolated", "--check", "."],
        ):
            completed = subprocess.run(
                arguments,
                cwd=generated,
                capture_output=True,
                text=True,
                check=False,
            )
            assert completed.returncode == 0, completed.stdout + completed.stderr

    def test_clean_spec_emits_no_marker(
        self, billing_spec_file: Path, tmp_path: Path
    ) -> None:
        """The marker only appears where something was actually lost."""
        result = generate_integration(
            str(billing_spec_file),
            target=tmp_path,
            name="billing",
            out=tmp_path / "pkg" / "billing_gen",
            run_format=False,
        )
        schemas = (result.written[0].parent / "schemas.py").read_text(encoding="utf-8")
        assert "# openapi: unsupported" not in schemas


class TestUnionComponentsRoundTrip:
    """A ``oneOf`` component survives generation, import and serialization.

    The unit tests pin the parser's decision; this one pins the outcome a
    consumer sees. The shape that shipped — an empty model plus
    ``extra="ignore"`` — passes every static check and drops the data at
    runtime, so the assertion that matters is on the serialized body.
    """

    @pytest.fixture
    def orders(self, tmp_path: Path) -> ModuleType:
        """Generate and import a package whose payload is a ``oneOf``.

        Args:
            tmp_path (Path): pytest's per-test temporary directory.

        Returns:
            ModuleType: The generated ``schemas`` module.
        """
        document: dict[str, Any] = {
            "openapi": "3.1.0",
            "info": {"title": "Orders", "version": "1"},
            "paths": {},
            "components": {
                "schemas": {
                    "BuyerPayload": {
                        "description": "name plus one of taxID or email.",
                        "oneOf": [
                            {
                                "type": "object",
                                "required": ["name", "taxID"],
                                "properties": {
                                    "name": {"type": "string"},
                                    "taxID": {"type": "string"},
                                },
                            },
                            {
                                "type": "object",
                                "required": ["name", "email"],
                                "properties": {
                                    "name": {"type": "string"},
                                    "email": {"type": "string"},
                                },
                            },
                        ],
                    },
                    "OrderPayload": {
                        "type": "object",
                        "required": ["reference"],
                        "properties": {
                            "reference": {"type": "string"},
                            "buyer": {"$ref": "#/components/schemas/BuyerPayload"},
                        },
                    },
                }
            },
        }
        spec_file = tmp_path / "orders.json"
        spec_file.write_text(json.dumps(document), encoding="utf-8")
        generate_integration(
            str(spec_file),
            target=tmp_path,
            name="orders",
            out=tmp_path / "pkg" / "orders_gen",
            run_format=False,
        )
        _load_package(tmp_path / "pkg" / "orders_gen", "orders_gen")
        return importlib.import_module("orders_gen.schemas")

    def test_the_payload_reaches_the_wire(self, orders: ModuleType) -> None:
        """``model_dump`` carries the buyer instead of an empty object."""
        payload = orders.OrderPayload(
            reference="order-1",
            buyer=orders.BuyerPayload(name="Ana", taxID="11111111111"),
        )
        assert payload.model_dump(by_alias=True, exclude_none=True) == {
            "reference": "order-1",
            "buyer": {"name": "Ana", "taxID": "11111111111"},
        }

    def test_the_variant_fields_are_optional(self, orders: ModuleType) -> None:
        """Either combination validates; only ``name`` is demanded."""
        by_email = orders.BuyerPayload(name="Ana", email="ana@example.com")
        assert by_email.model_dump(by_alias=True, exclude_none=True) == {
            "name": "Ana",
            "email": "ana@example.com",
        }


class TestUnsupportedComment:
    """The generated marker comment stays inside the line budget.

    It wraps with a four-character ``"#   "`` prefix on continuation lines,
    so wrapping has to reserve those four characters. When it did not, a long
    enough note produced generated code that failed ``E501`` — in the
    consumer's own lint run, on a line nobody wrote by hand.
    """

    def test_a_long_note_wraps_within_the_budget(self) -> None:
        """Every emitted line fits, however long the note is."""
        note = (
            "oneOf in 'CustomerPayload' merged into one model — every variant's "
            "properties are accepted together, so 'exactly one variant' is not "
            "enforced and the caller can send a combination no variant allows"
        )

        lines = unsupported_comment((note,), "    ")

        assert lines
        assert all(len(line) <= MAX_LINE for line in lines), [
            line for line in lines if len(line) > MAX_LINE
        ]
