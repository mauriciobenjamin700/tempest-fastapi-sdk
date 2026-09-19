"""Tests for ``tempest routes``."""

from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest
from typer.testing import CliRunner

from tempest_fastapi_sdk.cli.main import app

runner = CliRunner()

_SERVER_MODULE = """
from fastapi import APIRouter, Depends, FastAPI


def require_admin() -> None:
    return None


items = APIRouter(prefix="/api/items", tags=["items"])


@items.get("/{item_id}")
async def read_item(item_id: int) -> dict[str, int]:
    return {"item_id": item_id}


@items.post("/")
async def create_item() -> dict[str, str]:
    return {"status": "created"}


web = APIRouter()


@web.get("/painel", include_in_schema=False)
async def painel() -> str:
    return "<html></html>"


app = FastAPI()


@app.get("/health")
async def health() -> dict[str, str]:
    return {"status": "ok"}


app.include_router(items, dependencies=[Depends(require_admin)])
app.include_router(web)
"""


@pytest.fixture
def service(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """Write a scaffold-shaped service and stand inside it.

    The imported ``src`` package is dropped from ``sys.modules`` around
    the test: two tests writing a different ``src/server.py`` into two
    tmp dirs would otherwise share the first one's module object.
    """
    for name in [n for n in sys.modules if n == "src" or n.startswith("src.")]:
        del sys.modules[name]
    (tmp_path / "src").mkdir()
    (tmp_path / "src" / "__init__.py").write_text("", encoding="utf-8")
    (tmp_path / "src" / "server.py").write_text(_SERVER_MODULE, encoding="utf-8")
    monkeypatch.chdir(tmp_path)
    monkeypatch.syspath_prepend(str(tmp_path))
    yield tmp_path
    for name in [n for n in sys.modules if n == "src" or n.startswith("src.")]:
        del sys.modules[name]


class TestListing:
    def test_lists_routes_of_an_included_router(self, service: Path) -> None:
        """The whole point: a mounted router's paths must show up.

        FastAPI 0.141.1 keeps an included router as one ``_IncludedRouter``
        entry, so a comprehension over ``app.routes`` lists none of these.
        """
        result = runner.invoke(app, ["routes"])
        assert result.exit_code == 0, result.stdout + result.stderr
        assert "/api/items/{item_id}" in result.stdout
        assert "/api/items/" in result.stdout
        assert "/health" in result.stdout

    def test_lists_routes_kept_out_of_the_schema(self, service: Path) -> None:
        result = runner.invoke(app, ["routes"])
        assert "/painel" in result.stdout
        painel_line = next(
            line for line in result.stdout.splitlines() if "/painel" in line
        )
        assert painel_line.split()[3] == "no"

    def test_reports_the_guards_inherited_from_the_include(
        self,
        service: Path,
    ) -> None:
        result = runner.invoke(app, ["routes", "--match", "/api/items"])
        assert result.exit_code == 0, result.stdout + result.stderr
        assert "require_admin" in result.stdout
        health = runner.invoke(app, ["routes", "--match", "/health"])
        assert "require_admin" not in health.stdout

    def test_hides_fastapi_docs_routes_by_default(self, service: Path) -> None:
        result = runner.invoke(app, ["routes"])
        assert "/openapi.json" not in result.stdout
        assert "/docs" not in result.stdout

    def test_all_shows_the_docs_routes(self, service: Path) -> None:
        result = runner.invoke(app, ["routes", "--all"])
        assert "/openapi.json" in result.stdout
        assert "/docs" in result.stdout

    def test_head_is_not_listed_as_its_own_row(self, service: Path) -> None:
        result = runner.invoke(app, ["routes", "--all"])
        assert " HEAD " not in result.stdout


class TestFilters:
    def test_method_filter(self, service: Path) -> None:
        result = runner.invoke(app, ["routes", "--method", "post"])
        assert result.exit_code == 0, result.stdout + result.stderr
        rows = [line for line in result.stdout.splitlines()[1:] if line.strip()]
        assert rows
        assert all(row.startswith("POST") for row in rows)

    def test_no_match_exits_one(self, service: Path) -> None:
        result = runner.invoke(app, ["routes", "--match", "/nope"])
        assert result.exit_code == 1
        assert "no route matched" in result.stderr


class TestJson:
    def test_json_payload(self, service: Path) -> None:
        result = runner.invoke(app, ["routes", "--json"])
        assert result.exit_code == 0, result.stdout + result.stderr
        payload = json.loads(result.stdout)
        entry = next(item for item in payload if item["path"] == "/health")
        assert entry["method"] == "GET"
        assert entry["name"] == "health"
        assert entry["include_in_schema"] is True
        assert entry["guards"] == []


class TestAppResolution:
    def test_explicit_spec(self, service: Path) -> None:
        result = runner.invoke(app, ["routes", "--app", "src.server:app"])
        assert result.exit_code == 0, result.stdout + result.stderr
        assert "/health" in result.stdout

    def test_factory_spec(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        (tmp_path / "factory_app.py").write_text(
            "from fastapi import FastAPI\n\n\n"
            "def create_app() -> FastAPI:\n"
            "    application = FastAPI()\n\n"
            "    @application.get('/ping')\n"
            "    async def ping() -> dict[str, str]:\n"
            "        return {}\n\n"
            "    return application\n",
            encoding="utf-8",
        )
        monkeypatch.chdir(tmp_path)
        monkeypatch.syspath_prepend(str(tmp_path))
        result = runner.invoke(app, ["routes", "--app", "factory_app:create_app"])
        assert result.exit_code == 0, result.stdout + result.stderr
        assert "/ping" in result.stdout

    def test_unknown_spec_reports_the_cause(self, service: Path) -> None:
        result = runner.invoke(app, ["routes", "--app", "src.nope:app"])
        assert result.exit_code == 2
        assert "src.nope" in result.stderr

    def test_no_app_anywhere_lists_what_was_tried(
        self,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        monkeypatch.chdir(tmp_path)
        result = runner.invoke(app, ["routes"])
        assert result.exit_code == 2
        assert "no FastAPI app found" in result.stderr
