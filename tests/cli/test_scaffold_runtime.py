"""What the CLI writes must actually run, not just exist.

``tempest new --extras ssr`` writes a whole ``ui`` layer, and the
scaffolded ``CLAUDE.md`` teaches the seven-step recipe every new domain
follows. These tests import the generated code and serve it — and build
the ``CLAUDE.md`` example for real — so a broken import, a renamed SDK
symbol or an example that drifted fails here rather than in the first
project someone scaffolds.

Everything lives in one module, sharing one scaffold, on purpose:
importing a generated ``src`` package registers its models on the SDK's
declarative base, so a second project under the same module name would
collide with the first.
"""

from __future__ import annotations

import asyncio
import importlib
import os
import re
import sys
import textwrap
from collections.abc import Iterator
from pathlib import Path

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from typer.testing import CliRunner

from tempest_fastapi_sdk.cli.main import app as cli_app

runner = CliRunner()


def _forget_src_modules() -> None:
    """Drop any cached ``src`` package from a previously scaffolded project.

    Other CLI tests scaffold their own project under the same top-level
    package name. Whichever ran first would otherwise stay in
    ``sys.modules`` and shadow this one, so submodules of the project
    under test would not be found.
    """
    for name in [
        module for module in sys.modules if module == "src" or module.startswith("src.")
    ]:
        del sys.modules[name]


@pytest.fixture(scope="module")
def scaffolded(tmp_path_factory: pytest.TempPathFactory) -> Iterator[Path]:
    """Scaffold a project with the ``ssr`` extra and make it importable.

    Module-scoped on purpose: importing the generated ``src`` package
    registers its SQLAlchemy models on the SDK's declarative base, and
    scaffolding a second copy under the same module name would collide
    with the first. ``DATABASE_URL`` is pointed at a file inside the
    temporary directory before the scaffold is imported, so the run
    neither reads nor writes an ``app.db`` in the working directory.

    Args:
        tmp_path_factory (pytest.TempPathFactory): Pytest's temporary
            directory factory.

    Yields:
        Path: The generated project root.
    """
    tmp_path = tmp_path_factory.mktemp("scaffold")
    previous_url = os.environ.get("DATABASE_URL")
    os.environ["DATABASE_URL"] = f"sqlite+aiosqlite:///{tmp_path / 'test.db'}"
    result = runner.invoke(
        cli_app,
        ["new", "demo", "--path", str(tmp_path), "--extras", "ssr"],
    )
    assert result.exit_code == 0, result.stdout + result.stderr
    project = tmp_path / "demo"
    _write_domain_from_claude_md(project)
    _forget_src_modules()
    sys.path.insert(0, str(project))
    importlib.invalidate_caches()
    try:
        yield project
    finally:
        sys.path.remove(str(project))
        _forget_src_modules()
        importlib.invalidate_caches()
        if previous_url is None:
            os.environ.pop("DATABASE_URL", None)
        else:
            os.environ["DATABASE_URL"] = previous_url


def test_generated_layer_serves_a_page_and_its_stylesheet(scaffolded: Path) -> None:
    from tempest_fastapi_sdk.ui.css import make_css_router

    web = importlib.import_module("src.api.routers.web")
    ui = importlib.import_module("src.ui")

    app = FastAPI()
    app.include_router(make_css_router(ui.STYLESHEET, path=ui.CSS_PATH))
    app.include_router(web.router)
    client = TestClient(app)

    page = client.get("/")
    assert page.status_code == 200
    assert page.text.startswith("<!doctype html>")
    assert f'<link rel="stylesheet" href="{ui.CSS_PATH}">' in page.text
    assert 'class="tui-shell"' in page.text

    stylesheet = client.get(ui.CSS_PATH)
    assert stylesheet.status_code == 200
    assert "--t-color-primary" in stylesheet.text
    assert ".page-title" in stylesheet.text


def test_generated_stylesheet_covers_the_generated_markup(scaffolded: Path) -> None:
    """The example page must not ship classes the example sheet lacks."""
    import re

    from tempest_fastapi_sdk.ssr import html_response

    ui = importlib.import_module("src.ui")
    page = importlib.import_module("src.ui.pages").HomePage(title="T")
    markup = html_response(page, title="T").body.decode()

    used = {
        name
        for attribute in re.findall(r'class="([^"]+)"', markup)
        for name in attribute.split()
    }
    assert used
    assert used <= ui.STYLESHEET.class_names(), (
        f"unstyled classes: {sorted(used - ui.STYLESHEET.class_names())}"
    )


def test_generated_base_page_is_the_inheritance_point(scaffolded: Path) -> None:
    from tempest_fastapi_sdk.ui.pages import Page

    layout = importlib.import_module("src.ui.layout")
    pages = importlib.import_module("src.ui.pages")
    assert issubclass(layout.BasePage, Page)
    assert issubclass(pages.HomePage, layout.BasePage)


def _write_domain_from_claude_md(project: Path) -> None:
    """Materialise the ``CLAUDE.md`` example into the scaffolded project.

    The seven numbered code blocks are written to the exact paths the
    document names, plus the re-exports it demands and the domain
    exceptions from its error section. Nothing is edited on the way in —
    an example that no longer works fails the test that follows.

    Args:
        project (Path): The scaffolded project root.
    """
    blocks = [
        textwrap.dedent(block)
        for block in re.findall(
            r"```python\n(.*?)```",
            (project / "CLAUDE.md").read_text(encoding="utf-8"),
            re.DOTALL,
        )
    ]
    listing = blocks[8]
    pagination_imports = "".join(
        line + "\n"
        for line in listing.split("router: APIRouter", 1)[0].splitlines()
        if "BasePagination" in line
    )
    router_source = (
        pagination_imports
        + blocks[6]
        + "\n"
        + listing.split('tags=["products"])', 1)[1]
    )

    for relative, code in {
        "src/schemas/product.py": blocks[0],
        "src/db/models/product.py": blocks[1],
        "src/db/repositories/product.py": blocks[2],
        "src/services/product.py": blocks[3],
        "src/controllers/product.py": blocks[4],
        "src/api/dependencies/controllers.py": blocks[5],
        "src/api/routers/product.py": router_source,
    }.items():
        destination = project.joinpath(*relative.split("/"))
        destination.parent.mkdir(parents=True, exist_ok=True)
        destination.write_text(code, encoding="utf-8")

    for relative, content in {
        "src/schemas/__init__.py": (
            "from src.schemas.product import (\n"
            "    ProductCreateSchema as ProductCreateSchema,\n"
            ")\n"
            "from src.schemas.product import (\n"
            "    ProductResponseSchema as ProductResponseSchema,\n"
            ")\n\n"
            '__all__: list[str] = ["ProductCreateSchema", "ProductResponseSchema"]\n'
        ),
        "src/db/repositories/__init__.py": (
            "from src.db.repositories.product import (\n"
            "    ProductRepository as ProductRepository,\n"
            ")\n\n"
            '__all__: list[str] = ["ProductRepository"]\n'
        ),
        "src/services/__init__.py": (
            "from src.services.product import ProductService as ProductService\n\n"
            '__all__: list[str] = ["ProductService"]\n'
        ),
        "src/controllers/__init__.py": (
            "from src.controllers.product import (\n"
            "    ProductController as ProductController,\n"
            ")\n\n"
            '__all__: list[str] = ["ProductController"]\n'
        ),
    }.items():
        project.joinpath(*relative.split("/")).write_text(content, encoding="utf-8")

    models_init = project / "src" / "db" / "models" / "__init__.py"
    models_init.write_text(
        models_init.read_text(encoding="utf-8")
        + "\nfrom src.db.models.product import ProductModel as ProductModel\n"
        + '\n__all__ = [*__all__, "ProductModel"]\n',
        encoding="utf-8",
    )

    exceptions = project / "src" / "core" / "exceptions.py"
    exceptions.write_text(
        exceptions.read_text(encoding="utf-8").replace(
            "from tempest_fastapi_sdk import AppException, NotFoundException",
            "from tempest_fastapi_sdk import (\n"
            "    AppException,\n"
            "    ConflictException,\n"
            "    NotFoundException,\n"
            ")",
        )
        + blocks[7].replace(
            "from tempest_fastapi_sdk import ConflictException, NotFoundException\n",
            "",
        ),
        encoding="utf-8",
    )


def test_claude_md_recipe_produces_a_working_domain(scaffolded: Path) -> None:
    """The seven-step recipe, written verbatim, serves real requests."""
    from tempest_fastapi_sdk import BaseModel, register_exception_handlers

    resources = importlib.import_module("src.api.dependencies.resources")
    router = importlib.import_module("src.api.routers.product").router

    async def prepare() -> None:
        """Connect the manager and create the tables."""
        await resources.db.connect()
        async with resources.db.engine.begin() as connection:
            await connection.run_sync(BaseModel.metadata.create_all)

    asyncio.run(prepare())

    app = FastAPI()
    register_exception_handlers(app)
    app.include_router(router, prefix="/api")
    client = TestClient(app)

    created = client.post(
        "/api/products",
        json={"name": "Camisa", "price_cents": 4990},
    )
    assert created.status_code == 201, created.text
    assert created.json()["name"] == "Camisa"

    duplicate = client.post(
        "/api/products",
        json={"name": "Camisa", "price_cents": 4990},
    )
    assert duplicate.status_code == 409
    assert duplicate.json()["code"] == "PRODUCT_NAME_TAKEN"


def test_claude_md_pagination_block_matches_the_sdk_envelope(
    scaffolded: Path,
) -> None:
    """The paginated listing block returns the SDK envelope verbatim."""
    from tempest_fastapi_sdk import BaseModel, register_exception_handlers

    resources = importlib.import_module("src.api.dependencies.resources")
    router = importlib.import_module("src.api.routers.product").router

    async def prepare() -> None:
        """Connect the manager and create the tables."""
        await resources.db.connect()
        async with resources.db.engine.begin() as connection:
            await connection.run_sync(BaseModel.metadata.create_all)

    asyncio.run(prepare())

    app = FastAPI()
    register_exception_handlers(app)
    app.include_router(router, prefix="/api")
    client = TestClient(app)
    for index in range(3):
        client.post(
            "/api/products",
            json={"name": f"page-{index}", "price_cents": index * 100},
        )

    page = client.get("/api/products?page=1&page_size=2")
    assert page.status_code == 200, page.text
    body = page.json()
    assert set(body) == {"items", "total", "page", "page_size", "pages"}
    assert len(body["items"]) == 2
    assert body["page_size"] == 2
