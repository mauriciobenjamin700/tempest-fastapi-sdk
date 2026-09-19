"""Tests for ``tempest openapi-export``."""

from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest
from typer.testing import CliRunner

from tempest_fastapi_sdk.cli.main import app

runner = CliRunner()

_SERVER_MODULE = """
from fastapi import APIRouter, FastAPI

items = APIRouter(prefix="/api/items")


@items.get("/{item_id}")
async def read_item(item_id: int) -> dict[str, int]:
    return {"item_id": item_id}


app = FastAPI(title="Demo", version="1.2.3")
app.include_router(items)
"""

_EXTRA_ROUTE = """

@app.delete("/api/items/{item_id}")
async def delete_item(item_id: int) -> None:
    return None
"""


def _write_server(root: Path, source: str) -> None:
    """Write ``src/server.py`` with the given module source.

    Args:
        root (Path): The project root.
        source (str): The module body.
    """
    (root / "src").mkdir(exist_ok=True)
    (root / "src" / "__init__.py").write_text("", encoding="utf-8")
    (root / "src" / "server.py").write_text(source, encoding="utf-8")


@pytest.fixture
def service(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """Stand inside a scaffold-shaped service exposing ``src.server:app``."""
    for name in [n for n in sys.modules if n == "src" or n.startswith("src.")]:
        del sys.modules[name]
    _write_server(tmp_path, _SERVER_MODULE)
    monkeypatch.chdir(tmp_path)
    monkeypatch.syspath_prepend(str(tmp_path))
    yield tmp_path
    for name in [n for n in sys.modules if n == "src" or n.startswith("src.")]:
        del sys.modules[name]


class TestExport:
    def test_stdout_holds_the_document(self, service: Path) -> None:
        result = runner.invoke(app, ["openapi-export"])
        assert result.exit_code == 0, result.stdout + result.stderr
        document = json.loads(result.stdout)
        assert document["info"]["title"] == "Demo"
        assert "/api/items/{item_id}" in document["paths"]

    def test_out_writes_the_file(self, service: Path) -> None:
        target = service / "contracts" / "openapi.json"
        result = runner.invoke(app, ["openapi-export", "--out", str(target)])
        assert result.exit_code == 0, result.stdout + result.stderr
        assert "1 operation(s)" in result.stdout
        assert json.loads(target.read_text(encoding="utf-8"))["info"]["version"] == (
            "1.2.3"
        )

    def test_yaml_needs_no_json_braces(self, service: Path) -> None:
        pytest.importorskip("yaml")
        import yaml

        target = service / "openapi.yaml"
        result = runner.invoke(
            app,
            ["openapi-export", "--out", str(target), "--yaml"],
        )
        assert result.exit_code == 0, result.stdout + result.stderr
        document = yaml.safe_load(target.read_text(encoding="utf-8"))
        assert document["info"]["title"] == "Demo"


class TestCheck:
    def test_up_to_date(self, service: Path) -> None:
        target = service / "openapi.json"
        runner.invoke(app, ["openapi-export", "--out", str(target)])

        result = runner.invoke(app, ["openapi-export", "--out", str(target), "--check"])
        assert result.exit_code == 0, result.stdout + result.stderr
        assert "up to date" in result.stdout

    def test_names_the_added_operation(
        self,
        service: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """A new route must be named, not reported as 'the spec changed'."""
        target = service / "openapi.json"
        runner.invoke(app, ["openapi-export", "--out", str(target)])

        for name in [n for n in sys.modules if n == "src" or n.startswith("src.")]:
            del sys.modules[name]
        _write_server(service, _SERVER_MODULE + _EXTRA_ROUTE)

        result = runner.invoke(app, ["openapi-export", "--out", str(target), "--check"])
        assert result.exit_code == 1
        assert "+ DELETE /api/items/{item_id}" in result.stderr
        assert "out of date" in result.stderr

    def test_check_without_out_is_a_usage_error(self, service: Path) -> None:
        result = runner.invoke(app, ["openapi-export", "--check"])
        assert result.exit_code == 2
        assert "--check needs --out" in result.stderr

    def test_missing_file_says_how_to_create_it(self, service: Path) -> None:
        result = runner.invoke(
            app,
            ["openapi-export", "--out", str(service / "absent.json"), "--check"],
        )
        assert result.exit_code == 2
        assert "does not exist yet" in result.stderr

    def test_unparsable_file_is_reported(self, service: Path) -> None:
        target = service / "openapi.json"
        target.write_text("{not json", encoding="utf-8")
        result = runner.invoke(app, ["openapi-export", "--out", str(target), "--check"])
        assert result.exit_code == 2
        assert "could not parse" in result.stderr
