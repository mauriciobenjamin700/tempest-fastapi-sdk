"""Specification prose stays inert inside the generated docstrings.

A description, summary or title from the specification lands between
triple quotes in ``schemas.py``, ``client.py`` and ``__init__.py``. Text that
carries a triple quote used to close the docstring, so the rest of the
sentence became Python that ran when the consumer imported the package.
These tests generate from a specification whose every prose field tries
that, import the result in a fresh interpreter, and check that nothing ran
and that each docstring still says what the specification said.
"""

from __future__ import annotations

import ast
import json
import shutil
import subprocess
import sys
import warnings
from pathlib import Path
from typing import Any

import pytest

from tempest_fastapi_sdk.openapi.generate import generate_integration
from tempest_fastapi_sdk.openapi.loader import SpecError
from tempest_fastapi_sdk.openapi.source import (
    check_generated_source,
    docstring_text,
)

_INTEGRATIONS: Path = (
    Path(__file__).resolve().parents[2] / "tempest_fastapi_sdk" / "integrations"
)
"""The SDK's own integrations, whose generated modules the check must accept."""


def _payload(marker: Path, tag: str) -> str:
    """Build prose that writes ``marker`` if it ever escapes its docstring.

    Args:
        marker (Path): File the escaped code would create.
        tag (str): Text written into the file, naming the field it came from.

    Returns:
        str: The hostile prose — no spaces, so wrapping cannot split it.
    """
    return (
        '"""+__import__("pathlib").Path('
        + json.dumps(str(marker))
        + ').write_text("'
        + tag
        + '").__str__()+"""'
    )


def _spec(marker: Path) -> dict[str, Any]:
    """Build an OpenAPI document whose every prose field carries a payload.

    Args:
        marker (Path): File the payloads would create.

    Returns:
        dict[str, Any]: The document.
    """
    return {
        "openapi": "3.0.3",
        "info": {
            "title": _payload(marker, "title"),
            "version": _payload(marker, "version"),
        },
        "servers": [{"url": 'https://example.test/"+__import__("os").getcwd()+"'}],
        "paths": {
            "/pets/{petId}": {
                "get": {
                    "summary": _payload(marker, "summary"),
                    "description": _payload(marker, "operation"),
                    "parameters": [
                        {
                            "name": "petId",
                            "in": "path",
                            "required": True,
                            "description": _payload(marker, "parameter"),
                            "schema": {"type": "string"},
                        }
                    ],
                    "responses": {
                        "200": {
                            "description": "ok",
                            "content": {
                                "application/json": {
                                    "schema": {"$ref": "#/components/schemas/Pet"}
                                }
                            },
                        }
                    },
                }
            }
        },
        "components": {
            "schemas": {
                "Pet": {
                    "type": "object",
                    "description": _payload(marker, "schema"),
                    "properties": {
                        "name": {
                            "type": "string",
                            "description": _payload(marker, "field") + "\\",
                        }
                    },
                },
                "Empty": {
                    "type": "object",
                    "description": 'Ends with a quote and a backslash "\\',
                },
            }
        },
    }


def _generate(tmp_path: Path, *, run_format: bool) -> tuple[Path, Path]:
    """Generate the hostile specification into ``tmp_path``.

    Args:
        tmp_path (Path): Scratch directory.
        run_format (bool): Whether ruff runs over the output.

    Returns:
        tuple[Path, Path]: The generated package directory and the marker
        file the payloads would create.
    """
    marker = tmp_path / "marker.txt"
    spec_path = tmp_path / "spec.json"
    spec_path.write_text(json.dumps(_spec(marker)), encoding="utf-8")
    package = tmp_path / "gen" / "petstore"
    generate_integration(
        str(spec_path),
        target=tmp_path,
        name="petstore",
        out=package,
        force=True,
        run_format=run_format,
    )
    return package, marker


def _import_in_subprocess(package: Path) -> subprocess.CompletedProcess[str]:
    """Import every generated module in a fresh interpreter.

    Args:
        package (Path): The generated package directory.

    Returns:
        subprocess.CompletedProcess[str]: The finished import.
    """
    return subprocess.run(
        [
            sys.executable,
            "-c",
            "import petstore, petstore.schemas, petstore.client",
        ],
        cwd=package.parent,
        capture_output=True,
        text=True,
        check=False,
        timeout=120,
    )


def _docstrings(path: Path) -> dict[str, str]:
    """Collect the docstring of the module and of every class and function.

    Args:
        path (Path): A generated module.

    Returns:
        dict[str, str]: Node name (``<module>`` for the module) to docstring.
    """
    tree = ast.parse(path.read_text(encoding="utf-8"))
    found: dict[str, str] = {"<module>": ast.get_docstring(tree, clean=False) or ""}
    for node in ast.walk(tree):
        if isinstance(node, (ast.ClassDef, ast.FunctionDef, ast.AsyncFunctionDef)):
            found[node.name] = ast.get_docstring(node, clean=False) or ""
    return found


@pytest.mark.parametrize("run_format", [False, True])
class TestGeneratedPackage:
    """End to end: generate, import, and read the docstrings back."""

    def test_importing_runs_nothing(self, tmp_path: Path, run_format: bool) -> None:
        """No payload executes when the generated package is imported."""
        if run_format and shutil.which("ruff") is None:
            pytest.skip("ruff not on PATH")
        package, marker = _generate(tmp_path, run_format=run_format)
        completed = _import_in_subprocess(package)
        assert completed.returncode == 0, completed.stderr
        assert not marker.exists(), marker.read_text(encoding="utf-8")

    def test_every_module_passes_the_check(
        self, tmp_path: Path, run_format: bool
    ) -> None:
        """The written modules hold no statement the emitters never write."""
        if run_format and shutil.which("ruff") is None:
            pytest.skip("ruff not on PATH")
        package, _ = _generate(tmp_path, run_format=run_format)
        for module in sorted(package.glob("*.py")):
            check_generated_source(module.read_text(encoding="utf-8"), module.name)

    def test_docstrings_keep_the_specification_text(
        self, tmp_path: Path, run_format: bool
    ) -> None:
        """Escaping changes the source, never the docstring's value."""
        if run_format and shutil.which("ruff") is None:
            pytest.skip("ruff not on PATH")
        package, marker = _generate(tmp_path, run_format=run_format)
        schemas = _docstrings(package / "schemas.py")
        client = _docstrings(package / "client.py")
        flat = " ".join(" ".join(text.split()) for text in schemas.values())
        flat += " ".join(" ".join(text.split()) for text in client.values())

        assert schemas["Pet"].startswith(_payload(marker, "schema"))
        assert _payload(marker, "field") + "\\" in flat
        assert schemas["Empty"].startswith('Ends with a quote and a backslash "\\')
        method = next(name for name in client if name.startswith("get"))
        assert client[method].startswith(_payload(marker, "summary"))
        assert _payload(marker, "operation") in client[method]
        assert _payload(marker, "parameter") in " ".join(client[method].split())
        assert _payload(marker, "title") in client["<module>"]
        assert _payload(marker, "version") in " ".join(client.values())

    def test_base_url_is_a_plain_literal(
        self, tmp_path: Path, run_format: bool
    ) -> None:
        """``servers[0].url`` is rendered through the literal escaper."""
        if run_format and shutil.which("ruff") is None:
            pytest.skip("ruff not on PATH")
        package, _ = _generate(tmp_path, run_format=run_format)
        tree = ast.parse((package / "client.py").read_text(encoding="utf-8"))
        values = [
            node.value
            for node in tree.body
            if isinstance(node, ast.AnnAssign)
            and isinstance(node.target, ast.Name)
            and node.target.id == "DEFAULT_BASE_URL"
        ]
        assert len(values) == 1
        assert isinstance(values[0], ast.Constant)
        assert values[0].value == 'https://example.test/"+__import__("os").getcwd()+"'


class TestDocstringText:
    """The escaper round-trips through a real docstring."""

    @pytest.mark.parametrize(
        "text",
        [
            '"""',
            '""""',
            '"""""',
            'ends with a quote"',
            "ends with a backslash\\",
            'a backslash before quotes \\"""',
            "an invalid escape \\# stays text",
            "a \x00 nul and a \x7f delete",
            'mid-sentence "quotes" stay as they are',
        ],
    )
    def test_round_trips(self, text: str) -> None:
        """The parsed docstring equals the original text, with no warning."""
        source = '"""' + docstring_text(text) + '"""\n'
        with warnings.catch_warnings():
            warnings.simplefilter("error")
            module = ast.parse(source)
        assert len(module.body) == 1
        assert ast.literal_eval(source) == text

    def test_ordinary_prose_is_unchanged(self) -> None:
        """Only what could take part in a delimiter is touched."""
        text = 'The payer\'s "reference" field.'
        assert docstring_text(text) == text


class TestCheckGeneratedSource:
    """The defense-in-depth pass refuses what an escape gap would emit."""

    @pytest.mark.parametrize(
        ("source", "reason"),
        [
            (
                'class Pet:\n    """"""+__import__("os").getcwd()+""""""\n',
                "docstring",
            ),
            ('def f():\n    """x"""; g()\n', "one line"),
            ('"""Doc."""\nx = __import__("os")\n', "`__import__`"),
            ('"""Doc."""\npath = f"/a/{len(b)}"\n', "f-string"),
            ('"""Doc."""\n"a" + "b"\n', "unexpected expression"),
            ('"""Doc."""\n"a".join(b)\n', "unexpected expression"),
            ('"""unterminated\n', "does not parse"),
        ],
    )
    def test_refuses(self, source: str, reason: str) -> None:
        """Each shape escaped prose would produce is a ``SpecError``."""
        with pytest.raises(SpecError, match=reason):
            check_generated_source(source, "probe.py")

    @pytest.mark.parametrize(
        "path",
        sorted(
            path
            for path in _INTEGRATIONS.rglob("*.py")
            if "Do not edit by hand" in path.read_text(encoding="utf-8")
        ),
        ids=lambda path: str(path.relative_to(_INTEGRATIONS)),
    )
    def test_accepts_the_shipped_generated_modules(self, path: Path) -> None:
        """No false positive on what the generators write today."""
        check_generated_source(path.read_text(encoding="utf-8"), path.name)

    def test_generation_writes_nothing_when_the_check_fails(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """A refused module leaves the output directory untouched."""
        from tempest_fastapi_sdk.openapi import generate as generate_module

        marker = tmp_path / "marker.txt"
        spec_path = tmp_path / "spec.json"
        spec_path.write_text(json.dumps(_spec(marker)), encoding="utf-8")
        monkeypatch.setattr(
            generate_module,
            "emit_client",
            lambda client: '"""Doc."""\n"a" + "b"\n',
        )
        out = tmp_path / "gen" / "petstore"
        with pytest.raises(SpecError):
            generate_integration(
                str(spec_path),
                target=tmp_path,
                name="petstore",
                out=out,
                force=True,
                run_format=False,
            )
        assert not out.exists()
