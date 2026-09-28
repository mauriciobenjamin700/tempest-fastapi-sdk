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
import threading
import time
from collections.abc import Iterator
from pathlib import Path

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from typer.testing import CliRunner

from tempest_fastapi_sdk.cli.main import app as cli_app

runner = CliRunner()

TIMEOUT_TIMER_PREFIX = "pytest_timeout "
"""Name prefix of the per-item timer thread ``timeout_method = "thread"`` starts.

Ported from pytest-timeout's ``pytest_timeout_set_timer``, which names the
``threading.Timer`` ``"%s %s" % (__name__, item.nodeid)``.
"""


def _forget_src_modules() -> None:
    """Drop any cached ``src`` package from a previously scaffolded project.

    Other CLI tests scaffold their own project under the same top-level
    package name. Whichever ran first would otherwise stay in
    ``sys.modules`` and shadow this one, so submodules of the project
    under test would not be found.

    The names are read from ``list(sys.modules)``, a snapshot taken in one
    C-level call, rather than by iterating the live dict: any thread still
    running from an earlier test may import while this runs, and iterating
    ``sys.modules`` itself can then raise ``RuntimeError: dictionary changed
    size during iteration`` — seen once in 2000 passes with another thread
    importing under ``sys.setswitchinterval(1e-6)``, never over the snapshot
    in the same 2000. It is rare enough that this proves possibility, not a
    rate.
    """
    for name in [
        module
        for module in list(sys.modules)
        if module == "src" or module.startswith("src.")
    ]:
        del sys.modules[name]


def _disconnect_scaffold_database() -> None:
    """Dispose the engine the scaffolded ``resources.db`` opened, if any.

    The recipe tests call ``resources.db.connect()`` and the requests they
    make leave pooled ``aiosqlite`` connections behind. Each one owns a
    worker thread that stays blocked on its queue until the connection is
    closed, so without this every scaffold run leaves a thread alive for
    the rest of the session, and whatever finalizes that connection later
    (a ``gc`` pass inside ``_forget_src_modules``, say) runs in the middle
    of an unrelated teardown.
    """
    resources = sys.modules.get("src.api.dependencies.resources")
    database = getattr(resources, "db", None)
    if database is not None:
        asyncio.run(database.disconnect())


def _wait_for_threads_to_end(
    before: set[threading.Thread],
    *,
    deadline_seconds: float = 10.0,
) -> list[threading.Thread]:
    """Poll until every thread started after ``before`` has finished.

    Polling instead of a single check because ``AnyIO`` worker threads stop
    from a callback on their portal's loop and may still be unwinding when
    the teardown gets here; the deadline bounds a failure, not a success.
    The ``pytest-timeout`` timer of the item running the teardown is not
    the module's and is left out.

    Args:
        before (set[threading.Thread]): Threads alive when the scaffold
            fixture started.
        deadline_seconds (float): How long to wait before giving up.

    Returns:
        list[threading.Thread]: The threads still alive at the deadline;
        empty when everything the module started has ended.
    """
    limit = time.monotonic() + deadline_seconds
    while True:
        alive = [
            thread
            for thread in threading.enumerate()
            if thread not in before
            and thread.is_alive()
            and not thread.name.startswith(TIMEOUT_TIMER_PREFIX)
        ]
        if not alive or time.monotonic() > limit:
            return alive
        time.sleep(0.05)


@pytest.fixture(scope="module")
def scaffolded(tmp_path_factory: pytest.TempPathFactory) -> Iterator[Path]:
    """Scaffold a project with the ``ssr`` extra and make it importable.

    Module-scoped on purpose: importing the generated ``src`` package
    registers its SQLAlchemy models on the SDK's declarative base, and
    scaffolding a second copy under the same module name would collide
    with the first. ``DATABASE_URL`` is pointed at a file inside the
    temporary directory before the scaffold is imported, so the run
    neither reads nor writes an ``app.db`` in the working directory.

    The teardown disposes the scaffold's database before forgetting its
    modules, then fails if any thread the module started is still alive:
    an ``aiosqlite`` worker left running here outlived the module and
    kept its connection open for the rest of the session (#337).

    Args:
        tmp_path_factory (pytest.TempPathFactory): Pytest's temporary
            directory factory.

    Yields:
        Path: The generated project root.
    """
    threads_before = set(threading.enumerate())
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
        try:
            _disconnect_scaffold_database()
        finally:
            sys.path.remove(str(project))
            _forget_src_modules()
            importlib.invalidate_caches()
            if previous_url is None:
                os.environ.pop("DATABASE_URL", None)
            else:
                os.environ["DATABASE_URL"] = previous_url
    leaked = _wait_for_threads_to_end(threads_before)
    assert not leaked, f"threads outlived the scaffold module: {leaked}"


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
    assert f"{ui.CSS_PATH}?v={ui.STYLESHEET.version()}" == ui.CSS_URL
    assert f'<link rel="stylesheet" href="{ui.CSS_URL}">' in page.text
    assert 'class="tui-shell"' in page.text

    stylesheet = client.get(ui.CSS_URL)
    assert stylesheet.status_code == 200
    assert stylesheet.headers["cache-control"].endswith("immutable")
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

    Every recipe block is written to the path its own first line names
    (``# src/db/models/product.py``), plus the re-exports the document
    demands and the domain exceptions from its error section. Nothing is
    edited on the way in — an example that no longer works fails the
    test that follows.

    Blocks are located by that header rather than by position: indexing
    them made adding one block to the recipe break every later mapping,
    silently writing the wrong file into the project under test.

    ``src/db/configs/names.py`` is the one block the document asks you to
    **extend**, not to write — the file already holds the user table's
    name — so it is applied as an edit, together with the re-export the
    recipe demands next to it.

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
    by_path: dict[str, str] = {}
    for block in blocks:
        first = block.strip().splitlines()[0]
        if first.startswith("# src/"):
            by_path[first.removeprefix("# ").strip()] = block
    listing = next(
        block
        for block in blocks
        if 'tags=["products"])' in block and "BasePagination" in block
    )
    pagination_imports = "".join(
        line + "\n"
        for line in listing.split("router: APIRouter", 1)[0].splitlines()
        if "BasePagination" in line
    )
    router_source = (
        pagination_imports
        + by_path["src/api/routers/product.py"]
        + "\n"
        + listing.split('tags=["products"])', 1)[1]
    )

    names = project / "src" / "db" / "configs" / "names.py"
    names.write_text(
        names.read_text(encoding="utf-8").replace(
            '__all__: list[str] = [\n    "USER_TABLE_NAME",\n]',
            'PRODUCT_TABLE_NAME = "products"\n\n'
            "__all__: list[str] = [\n"
            '    "PRODUCT_TABLE_NAME",\n'
            '    "USER_TABLE_NAME",\n'
            "]",
        ),
        encoding="utf-8",
    )
    configs_init = project / "src" / "db" / "configs" / "__init__.py"
    configs_init.write_text(
        configs_init.read_text(encoding="utf-8")
        .replace(
            "from src.db.configs.names import USER_TABLE_NAME",
            "from src.db.configs.names import PRODUCT_TABLE_NAME, USER_TABLE_NAME",
        )
        .replace(
            '__all__: list[str] = [\n    "USER_TABLE_NAME",\n]',
            "__all__: list[str] = [\n"
            '    "PRODUCT_TABLE_NAME",\n'
            '    "USER_TABLE_NAME",\n'
            "]",
        ),
        encoding="utf-8",
    )

    written = {
        path: code
        for path, code in by_path.items()
        if path not in {"src/api/routers/product.py", "src/db/configs/names.py"}
    }
    written["src/api/routers/product.py"] = router_source
    for relative, code in written.items():
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
        + by_path["src/core/exceptions.py"].replace(
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


def test_table_names_come_from_one_symbol(scaffolded: Path) -> None:
    """``__tablename__`` reads the constant, not a literal.

    A table name shows up in at least two places — the model's
    ``__tablename__`` and the string inside every ``ForeignKey`` that
    points at it — and SQLAlchemy resolves the FK target only when it
    configures the mappers. So a rename that misses one side fails at
    application startup, or inside a migration pointing at a table that
    no longer exists, rather than where it was typed.
    """
    from src.db.configs import USER_TABLE_NAME  # type: ignore[import-not-found]
    from src.db.models import UserModel  # type: ignore[import-not-found]

    assert UserModel.__tablename__ is USER_TABLE_NAME
    assert UserModel.__table__.name == "users"

    source = (scaffolded / "src" / "db" / "models" / "user.py").read_text()
    assert "__tablename__ = USER_TABLE_NAME" in source
    assert '__tablename__ = "users"' not in source


def test_the_names_module_imports_nothing_from_the_project(
    scaffolded: Path,
) -> None:
    """It is only strings, which is what keeps it out of import cycles.

    The models import from here; nothing is imported back. An import of
    the project inside this module would put it in the cycle the layout
    exists to avoid.
    """
    source = (scaffolded / "src" / "db" / "configs" / "names.py").read_text()

    assert "import" not in source.split('"""')[-1]
    assert "USER_TABLE_NAME" in source
