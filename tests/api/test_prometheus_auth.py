"""Tests for protecting ``make_prometheus_router`` (issue #439).

Two shipped defects motivate this module. The docstring suggested
``dependencies=[Depends(require_x_token)]``, which the router wrapped a
second time (``Depends(Depends(...))``) and FastAPI refused at build time
with ``AssertionError``. The "fixed" call, ``dependencies=[require_x_token]``,
built fine but made FastAPI read ``secret`` and ``token`` from the query
string, so every scrape answered ``422`` unless the secret went into the
URL. The recipe example is executed here exactly as published, and the
prose of the recipe and the docstring is scanned so neither form returns.
"""

from __future__ import annotations

import inspect
import re
from collections.abc import Callable
from pathlib import Path
from typing import Any

import pytest
import yaml
from fastapi import Depends, FastAPI, Security
from httpx import ASGITransport, AsyncClient

from tempest_fastapi_sdk import (
    make_prometheus_registry,
    make_prometheus_router,
    make_token_dependency,
    register_exception_handlers,
    require_x_token,
)

DOCS_DIR: Path = Path(__file__).resolve().parents[2] / "docs" / "recipes"
RECIPES: tuple[Path, ...] = (DOCS_DIR / "metrics.md", DOCS_DIR / "metrics.en.md")
SECRET: str = "scrape-secret"
FORBIDDEN_PATTERNS: tuple[re.Pattern[str], ...] = (
    re.compile(r"Depends\(\s*require_x_token"),
    re.compile(r"dependencies\s*=\s*\[\s*require_x_token"),
    re.compile(r"dependencies\s*=\s*\[\s*Depends\(\s*Depends\("),
)


def _blocks(text: str, language: str) -> list[str]:
    """Extract the fenced code blocks of one language from markdown.

    Args:
        text (str): The markdown source.
        language (str): The fence info string (``python``, ``yaml``).

    Returns:
        list[str]: The bodies of every matching block, in order.
    """
    return re.findall(rf"^```{language}\n(.*?)^```", text, flags=re.M | re.S)


def _documented_factory(recipe: Path) -> Callable[[str], FastAPI]:
    """Execute the recipe's protected ``create_app`` and return it.

    Args:
        recipe (Path): The recipe page to read.

    Returns:
        Callable[[str], FastAPI]: The ``create_app(metrics_token)`` the
        page publishes.
    """
    candidates = [
        block
        for block in _blocks(recipe.read_text(encoding="utf-8"), "python")
        if "make_prometheus_router(" in block and "def create_app" in block
    ]
    assert len(candidates) == 1, f"{recipe.name}: expected one create_app block"
    namespace: dict[str, Any] = {}
    exec(compile(candidates[0], str(recipe), "exec"), namespace)
    factory: Callable[[str], FastAPI] = namespace["create_app"]
    return factory


async def _scrape(app: FastAPI, headers: dict[str, str] | None = None) -> int:
    """Scrape ``GET /metrics`` once and return the status code.

    Args:
        app (FastAPI): The app under test.
        headers (dict[str, str] | None): Request headers to send.

    Returns:
        int: The HTTP status of the scrape.
    """
    async with AsyncClient(
        transport=ASGITransport(app=app), base_url="http://t"
    ) as client:
        response = await client.get("/metrics", headers=headers or {})
    return response.status_code


def _app_with(dependencies: list[Any]) -> FastAPI:
    """Build an app mounting the metrics router with ``dependencies``.

    Args:
        dependencies (list[Any]): The ``dependencies=`` value under test.

    Returns:
        FastAPI: The wired app.
    """
    app = FastAPI()
    register_exception_handlers(app)
    app.include_router(
        make_prometheus_router(
            registry=make_prometheus_registry(),
            dependencies=dependencies,
        )
    )
    return app


@pytest.mark.parametrize("recipe", RECIPES, ids=lambda p: p.name)
class TestDocumentedExample:
    """The published example mounts and only answers with the header."""

    async def test_scrape_without_header_is_401(self, recipe: Path) -> None:
        assert await _scrape(_documented_factory(recipe)(SECRET)) == 401

    async def test_scrape_with_wrong_header_is_401(self, recipe: Path) -> None:
        app = _documented_factory(recipe)(SECRET)
        assert await _scrape(app, {"X-Token": "wrong"}) == 401

    async def test_scrape_with_header_is_200(self, recipe: Path) -> None:
        app = _documented_factory(recipe)(SECRET)
        assert await _scrape(app, {"X-Token": SECRET}) == 200

    async def test_secret_in_query_does_not_pass(self, recipe: Path) -> None:
        app = _documented_factory(recipe)(SECRET)
        async with AsyncClient(
            transport=ASGITransport(app=app), base_url="http://t"
        ) as client:
            response = await client.get(
                "/metrics", params={"secret": SECRET, "token": SECRET}
            )
        assert response.status_code == 401

    def test_scrape_config_sends_the_header_the_dependency_reads(
        self, recipe: Path
    ) -> None:
        configs = [
            yaml.safe_load(block)
            for block in _blocks(recipe.read_text(encoding="utf-8"), "yaml")
            if "scrape_configs" in block
        ]
        assert configs, f"{recipe.name}: no scrape config block"
        for config in configs:
            for job in config["scrape_configs"]:
                assert "X-Token" in job["http_headers"]

    def test_recipe_states_default_is_open(self, recipe: Path) -> None:
        text = recipe.read_text(encoding="utf-8")
        assert "**sem autenticação nenhuma**" in text or (
            "**no authentication at all**" in text
        )

    def test_recipe_never_suggests_a_broken_form(self, recipe: Path) -> None:
        text = recipe.read_text(encoding="utf-8")
        for pattern in FORBIDDEN_PATTERNS:
            assert not pattern.search(text), f"{recipe.name}: {pattern.pattern}"


class TestDocstring:
    """The docstring's own example is the working form."""

    def test_docstring_never_suggests_a_broken_form(self) -> None:
        doc = inspect.getdoc(make_prometheus_router) or ""
        for pattern in FORBIDDEN_PATTERNS:
            assert not pattern.search(doc), pattern.pattern
        assert "make_token_dependency(" in doc

    def test_patterns_fire_on_the_shipped_docstring(self) -> None:
        shipped = "typically ``[Depends(require_x_token)]`` so the"
        assert any(pattern.search(shipped) for pattern in FORBIDDEN_PATTERNS)


class TestDependencyItems:
    """Each ``dependencies=`` item is a callable or a ready ``Depends``."""

    async def test_default_is_unauthenticated(self) -> None:
        app = FastAPI()
        app.include_router(make_prometheus_router(registry=make_prometheus_registry()))
        assert await _scrape(app) == 200

    async def test_bare_callable_is_wrapped(self) -> None:
        app = _app_with([make_token_dependency(SECRET)])
        assert await _scrape(app) == 401
        assert await _scrape(app, {"X-Token": SECRET}) == 200

    async def test_ready_depends_is_not_wrapped_twice(self) -> None:
        app = _app_with([Depends(make_token_dependency(SECRET))])
        assert await _scrape(app) == 401
        assert await _scrape(app, {"X-Token": SECRET}) == 200

    async def test_ready_security_is_accepted(self) -> None:
        app = _app_with([Security(make_token_dependency(SECRET))])
        assert await _scrape(app) == 401
        assert await _scrape(app, {"X-Token": SECRET}) == 200

    async def test_empty_secret_disables_the_check(self) -> None:
        assert await _scrape(_app_with([make_token_dependency("")])) == 200

    @pytest.mark.parametrize(
        "item",
        [require_x_token, Depends(require_x_token)],
        ids=["bare", "depends"],
    )
    def test_require_x_token_is_refused(self, item: Any) -> None:
        with pytest.raises(TypeError, match="make_token_dependency"):
            make_prometheus_router(
                registry=make_prometheus_registry(), dependencies=[item]
            )
