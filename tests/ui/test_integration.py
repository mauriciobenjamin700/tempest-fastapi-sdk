"""End-to-end tests: a small full-stack app built from the ``ui`` layer."""

from __future__ import annotations

from fastapi import FastAPI, Request
from fastapi.responses import RedirectResponse, Response
from fastapi.testclient import TestClient
from pydantic import BaseModel, EmailStr, Field
from tempest_core import Widget

from tempest_fastapi_sdk.ssr import html_response
from tempest_fastapi_sdk.ui import app_stylesheet
from tempest_fastapi_sdk.ui.components import Card, NavBar, NavItem
from tempest_fastapi_sdk.ui.css import make_css_router
from tempest_fastapi_sdk.ui.forms import FormResult, form_for, parse_form
from tempest_fastapi_sdk.ui.layout import Shell
from tempest_fastapi_sdk.ui.pages import Page

CSS_PATH = "/static/app.css"


class SignupSchema(BaseModel):
    """The signup payload, shared by the form and the route."""

    email: EmailStr
    password: str = Field(min_length=8)


class BasePage(Page):
    """Chrome shared by every page of the test app."""

    def shell(self, body: Widget) -> Widget:
        return Shell(
            children=[body],
            header=NavBar(items=[NavItem(label="Início", href="/")], active_href="/"),
        )


class SignupPage(BasePage):
    """The signup screen, rendering a generated form."""

    result: FormResult[SignupSchema] | None = None

    def body(self) -> Widget:
        return Card(
            title="Cadastro",
            children=[
                form_for(
                    SignupSchema,
                    action="/signup",
                    values=self.result.values if self.result else None,
                    errors=self.result.errors if self.result else None,
                    form_errors=self.result.form_errors if self.result else (),
                ),
            ],
        )


def _app() -> TestClient:
    """Build the test application.

    Returns:
        TestClient: A client bound to an app serving the page, the form
        round trip and the generated stylesheet.
    """
    app = FastAPI()
    app.include_router(make_css_router(app_stylesheet(), path=CSS_PATH))

    @app.get("/signup")
    async def signup_form() -> Response:
        """Render the empty signup form."""
        return html_response(
            SignupPage(title="Cadastro"),
            title="Cadastro",
            stylesheets=[CSS_PATH],
        )

    @app.post("/signup")
    async def signup(request: Request) -> Response:
        """Validate the submission, re-rendering the form on failure."""
        result = await parse_form(SignupSchema, request)
        if not result.ok:
            return html_response(
                SignupPage(title="Cadastro", result=result),
                title="Cadastro",
                status_code=422,
                stylesheets=[CSS_PATH],
            )
        return RedirectResponse("/", status_code=303)

    return TestClient(app)


def test_get_renders_a_full_document_linking_the_stylesheet() -> None:
    response = _app().get("/signup")
    assert response.status_code == 200
    body = response.text
    assert body.startswith("<!doctype html>")
    assert f'<link rel="stylesheet" href="{CSS_PATH}">' in body
    assert "<title>Cadastro</title>" in body
    assert '<form method="post" action="/signup"' in body
    assert 'name="email"' in body and 'name="password"' in body


def test_invalid_post_re_renders_with_errors_and_input_kept() -> None:
    response = _app().post("/signup", data={"email": "nope", "password": "123"})
    assert response.status_code == 422
    body = response.text
    assert 'aria-invalid="true"' in body
    assert 'value="nope"' in body
    assert "at least 8 characters" in body


def test_valid_post_redirects() -> None:
    response = _app().post(
        "/signup",
        data={"email": "ana@example.com", "password": "12345678"},
        follow_redirects=False,
    )
    assert response.status_code == 303
    assert response.headers["location"] == "/"


def test_stylesheet_defines_every_class_the_page_uses() -> None:
    """The bundled sheet actually covers the bundled markup."""
    import re

    client = _app()
    page = client.get("/signup").text
    defined = app_stylesheet().class_names()
    used = {
        name
        for attribute in re.findall(r'class="([^"]+)"', page)
        for name in attribute.split()
    }
    assert used
    assert used <= defined, f"unstyled classes: {sorted(used - defined)}"


def test_head_argument_is_injected_verbatim() -> None:
    app = FastAPI()

    @app.get("/")
    async def home() -> Response:
        """Render a page with an extra head tag."""
        return html_response(
            SignupPage(title="X"),
            title="X",
            head='<meta name="robots" content="noindex">',
        )

    body = TestClient(app).get("/").text
    assert '<meta name="robots" content="noindex">' in body


def test_fragment_response_skips_the_document_shell() -> None:
    app = FastAPI()

    @app.get("/fragment")
    async def fragment() -> Response:
        """Render the form as an HTMX-style fragment."""
        return html_response(
            form_for(SignupSchema, action="/signup"),
            document=False,
            stylesheets=[CSS_PATH],
        )

    body = TestClient(app).get("/fragment").text
    assert body.startswith("<form")
    assert "<!doctype html>" not in body
    assert "link rel" not in body
