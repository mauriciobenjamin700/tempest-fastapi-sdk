"""HTML routes: error page on reads, flash-and-redirect on actions (#349)."""

from __future__ import annotations

from collections.abc import Sequence
from typing import ClassVar

import pytest
from fastapi import APIRouter, FastAPI, HTTPException, Request
from fastapi.responses import HTMLResponse
from fastapi.testclient import TestClient
from tempest_core import Stack, Widget

from tempest_fastapi_sdk import (
    ConflictException,
    NotFoundException,
    default_message_catalog,
    register_exception_handlers,
)
from tempest_fastapi_sdk.ssr import (
    GENERIC_ERROR_MESSAGE,
    FlashMiddleware,
    Page,
    get_flashes,
    html_response,
    register_html_error_handlers,
)
from tempest_fastapi_sdk.ui.components import FlashMessages
from tempest_fastapi_sdk.ui.pages import ErrorPage

SECRET = "0123456789abcdef-test-secret"


class AdminBasePage(Page):
    """The panel's base page: shared stylesheet and chrome."""

    stylesheets: ClassVar[Sequence[str]] = ("/static/admin.css",)
    title_suffix: ClassVar[str] = " · painel"

    def shell(self, body: Widget) -> Widget:
        return Stack(tag="div", attrs={"class": "admin-shell"}, children=[body])


class AdminErrorPage(AdminBasePage, ErrorPage):
    """The error page with the panel's chrome."""


class BucketsPage(AdminBasePage):
    """Lists pending flashes so the redirect target shows them."""

    flashes: FlashMessages

    def body(self) -> Widget:
        return self.flashes


def _app(*, with_flash: bool = True, **options: object) -> FastAPI:
    app = FastAPI()
    if with_flash:
        app.add_middleware(FlashMiddleware, secret=SECRET, secure=False)

    @app.get("/admin/buckets")
    async def buckets(request: Request) -> HTMLResponse:
        return html_response(
            BucketsPage(
                title="Buckets",
                flashes=FlashMessages(messages=get_flashes(request)),
            ),
        )

    @app.get("/admin/buckets/{name}")
    async def bucket(name: str) -> HTMLResponse:
        raise NotFoundException(f"Bucket {name} não encontrado.")

    @app.post("/admin/buckets/{name}/delete")
    async def delete(name: str) -> HTMLResponse:
        raise ConflictException("O bucket ainda tem objetos.")

    @app.get("/api/buckets/{name}")
    async def api_bucket(name: str) -> dict[str, str]:
        raise NotFoundException(f"Bucket {name} não encontrado.")

    tagged = APIRouter(tags=["html"])

    @tagged.post("/panel/save")
    async def save() -> HTMLResponse:
        raise ConflictException("Conflito no painel.")

    app.include_router(tagged)
    register_exception_handlers(app)
    register_html_error_handlers(app, **options)  # type: ignore[arg-type]
    return app


def test_api_route_keeps_the_json_envelope() -> None:
    with TestClient(_app(prefixes=["/admin"])) as client:
        response = client.get("/api/buckets/a")
    assert response.status_code == 404
    assert response.headers["content-type"] == "application/json"
    assert response.json()["detail"] == "Bucket a não encontrado."


def test_get_on_html_route_renders_the_error_page_with_the_status() -> None:
    with TestClient(_app(prefixes=["/admin"])) as client:
        response = client.get("/admin/buckets/a")
    assert response.status_code == 404
    assert response.headers["content-type"].startswith("text/html")
    assert "<title>Erro 404</title>" in response.text
    assert "Bucket a não encontrado." in response.text
    assert "tui-alert--error" in response.text


def test_error_page_class_inherits_the_panel_chrome() -> None:
    app = _app(prefixes=["/admin"], error_page=AdminErrorPage)
    with TestClient(app) as client:
        response = client.get("/admin/buckets/a")
    assert response.status_code == 404
    assert "<title>Erro 404 · painel</title>" in response.text
    assert '<link rel="stylesheet" href="/static/admin.css">' in response.text
    assert 'class="admin-shell"' in response.text


def test_unknown_path_under_the_prefix_gets_the_html_404() -> None:
    with TestClient(_app(prefixes=["/admin"])) as client:
        response = client.get("/admin/nothing/here")
    assert response.status_code == 404
    assert "<title>Erro 404</title>" in response.text


def test_unknown_path_outside_the_prefix_keeps_json() -> None:
    with TestClient(_app(prefixes=["/admin"])) as client:
        response = client.get("/nothing")
    assert response.status_code == 404
    assert response.headers["content-type"] == "application/json"


def test_post_redirects_back_with_the_message_as_flash() -> None:
    with TestClient(_app(prefixes=["/admin"]), follow_redirects=False) as client:
        response = client.post(
            "/admin/buckets/a/delete",
            headers={"referer": "http://testserver/admin/buckets"},
        )
        assert response.status_code == 303
        assert response.headers["location"] == "/admin/buckets"
        page = client.get("/admin/buckets")
    assert "O bucket ainda tem objetos." in page.text
    assert "tui-alert--error" in page.text


def test_post_with_foreign_referer_goes_to_the_prefix() -> None:
    with TestClient(_app(prefixes=["/admin"]), follow_redirects=False) as client:
        response = client.post(
            "/admin/buckets/a/delete",
            headers={"referer": "https://evil.example/admin/buckets"},
        )
    assert response.status_code == 303
    assert response.headers["location"] == "/admin"


def test_fallback_overrides_the_prefix() -> None:
    app = _app(prefixes=["/admin"], fallback="/admin/buckets")
    with TestClient(app, follow_redirects=False) as client:
        response = client.post("/admin/buckets/a/delete")
    assert response.headers["location"] == "/admin/buckets"


def test_tagged_route_is_html_too() -> None:
    with TestClient(_app(tags=["html"]), follow_redirects=False) as client:
        response = client.post(
            "/panel/save",
            headers={"referer": "http://testserver/panel"},
        )
        api = client.get("/api/buckets/a")
    assert response.status_code == 303
    assert response.headers["location"] == "/panel"
    assert api.headers["content-type"] == "application/json"


def test_post_without_flash_middleware_renders_the_error_page() -> None:
    with TestClient(_app(with_flash=False, prefixes=["/admin"])) as client:
        response = client.post("/admin/buckets/a/delete")
    assert response.status_code == 409
    assert "O bucket ainda tem objetos." in response.text


def test_catalog_localization_reaches_the_html_page() -> None:
    app = FastAPI()

    @app.get("/admin/x")
    async def x() -> HTMLResponse:
        raise NotFoundException()

    register_exception_handlers(app, catalog=default_message_catalog())
    register_html_error_handlers(app, prefixes=["/admin"])
    with TestClient(app) as client:
        pt = client.get("/admin/x", headers={"accept-language": "pt-BR"})
        en = client.get("/admin/x", headers={"accept-language": "en"})
    assert pt.status_code == en.status_code == 404
    assert "Recurso não encontrado" in pt.text
    assert "Resource not found" in en.text


def test_non_string_detail_uses_the_generic_message() -> None:
    app = FastAPI()

    @app.get("/admin/x")
    async def x() -> HTMLResponse:
        raise HTTPException(status_code=400, detail={"k": "v"})

    register_html_error_handlers(app, prefixes=["/admin"])
    with TestClient(app) as client:
        response = client.get("/admin/x")
    assert response.status_code == 400
    assert GENERIC_ERROR_MESSAGE in response.text


def test_headers_of_the_json_answer_are_kept_on_the_page() -> None:
    app = FastAPI()

    @app.get("/admin/x")
    async def x() -> HTMLResponse:
        raise HTTPException(
            status_code=429, detail="Calma.", headers={"Retry-After": "7"}
        )

    register_html_error_handlers(app, prefixes=["/admin"])
    with TestClient(app) as client:
        response = client.get("/admin/x")
    assert response.status_code == 429
    assert response.headers["retry-after"] == "7"
    assert response.headers["content-type"].startswith("text/html")


def test_needs_a_prefix_or_a_tag() -> None:
    with pytest.raises(ValueError, match="prefixes= or tags="):
        register_html_error_handlers(FastAPI())


def test_title_template_changes_the_error_title() -> None:
    class EnglishErrorPage(ErrorPage):
        title_template: ClassVar[str] = "Error {status_code}"

    app = _app(prefixes=["/admin"], error_page=EnglishErrorPage)
    with TestClient(app) as client:
        response = client.get("/admin/buckets/a")
    assert "<title>Error 404</title>" in response.text
    assert "<h1>Error 404</h1>" in response.text


def test_head_on_html_route_is_treated_as_a_read() -> None:
    """FastAPI answers HEAD on a GET route with 405; the answer is a page."""
    with TestClient(_app(prefixes=["/admin"]), follow_redirects=False) as client:
        response = client.head("/admin/buckets/a")
    assert response.status_code == 405
    assert "location" not in response.headers
    assert response.headers["content-type"].startswith("text/html")
