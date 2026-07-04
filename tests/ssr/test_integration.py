"""Integration tests: html_response + make_htmx_router in a real FastAPI app."""

from __future__ import annotations

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from tempest_core import Column, Row, Text, Widget

from tempest_fastapi_sdk.ssr import Page, html_response, make_htmx_router


class HomePage(Page):
    """A concrete page rendering a semantic document."""

    user: str

    def body(self) -> Widget:
        return Column(
            tag="main",
            children=[
                Text(content=f"Welcome {self.user}", tag="h1"),
                Text(content="home body", tag="p"),
            ],
        )


class BasePage(Page):
    """A shared layout: nav + main. Concrete pages subclass it."""

    def shell(self, body: Widget) -> Widget:
        return Column(
            tag="body",
            children=[
                Row(tag="nav", children=[Text(content="MyApp")]),
                Column(tag="main", children=[body]),
            ],
        )


class DashboardPage(BasePage):
    """A concrete page inheriting the shared shell."""

    def body(self) -> Widget:
        return Text(content="dashboard-content", tag="h2")


class ReportsPage(BasePage):
    """A second concrete page inheriting the same shell."""

    def body(self) -> Widget:
        return Text(content="reports-content", tag="h2")


class EscapePage(Page):
    """A page whose body contains characters that must be escaped."""

    def body(self) -> Widget:
        return Text(content="<script>alert('x')</script>", tag="p")


@pytest.fixture
def app() -> FastAPI:
    application = FastAPI()

    @application.get("/")
    def home() -> object:
        return html_response(
            HomePage(title="Home", user="Ana"), title="Home", htmx=True
        )

    @application.get("/fragment")
    def fragment() -> object:
        return html_response(HomePage(title="X", user="Bob"), document=False)

    @application.get("/dashboard")
    def dashboard() -> object:
        return html_response(DashboardPage(title="Dashboard"), title="Dashboard")

    @application.get("/reports")
    def reports() -> object:
        return html_response(ReportsPage(title="Reports"), title="Reports")

    @application.get("/escape")
    def escape() -> object:
        return html_response(EscapePage(title="Escape"), title="Escape")

    application.include_router(make_htmx_router())
    return application


@pytest.fixture
def client(app: FastAPI) -> TestClient:
    return TestClient(app)


def test_full_document_route(client: TestClient) -> None:
    resp = client.get("/")
    assert resp.status_code == 200
    assert resp.headers["content-type"].startswith("text/html")
    body = resp.text
    assert "<!doctype html>" in body.lower()
    assert "<title>Home</title>" in body
    assert "<main" in body
    assert "<h1>Welcome Ana</h1>" in body
    # HTMX served locally, never a CDN.
    assert '<script src="/_ssr/htmx.js" defer></script>' in body
    assert "unpkg.com" not in body


def test_fragment_route_is_bare(client: TestClient) -> None:
    resp = client.get("/fragment")
    assert resp.status_code == 200
    assert resp.headers["content-type"].startswith("text/html")
    body = resp.text
    assert "<!doctype" not in body.lower()
    assert "<html" not in body.lower()
    assert "Welcome Bob" in body


def test_htmx_router_serves_local_asset(client: TestClient) -> None:
    resp = client.get("/_ssr/htmx.js")
    assert resp.status_code == 200
    assert resp.headers["content-type"].startswith("application/javascript")
    assert len(resp.content) > 0
    # The bundled htmx source defines the htmx global.
    assert b"htmx" in resp.content


def test_inherited_shell_is_shared_bodies_differ(client: TestClient) -> None:
    dash = client.get("/dashboard").text
    rep = client.get("/reports").text
    # Both share the inherited chrome (nav + MyApp brand).
    for page in (dash, rep):
        assert "<nav" in page
        assert "MyApp" in page
    # But the bodies differ.
    assert "dashboard-content" in dash
    assert "dashboard-content" not in rep
    assert "reports-content" in rep
    assert "reports-content" not in dash


def test_escaping_holds_end_to_end(client: TestClient) -> None:
    resp = client.get("/escape")
    body = resp.text
    assert "<script>alert" not in body
    assert "&lt;script&gt;" in body
