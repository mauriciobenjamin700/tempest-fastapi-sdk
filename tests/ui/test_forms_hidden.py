"""Tests for real hidden fields (`{"ui": {"control": "hidden"}}`, #340)."""

from __future__ import annotations

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse, Response
from fastapi.testclient import TestClient
from pydantic import BaseModel, Field
from pydantic.json_schema import JsonDict
from tempestweb.html import render_to_html

from tempest_fastapi_sdk.ui.forms import fields_for, form_for, parse_form

HIDDEN: JsonDict = {"ui": {"control": "hidden"}}


class RowActionSchema(BaseModel):
    """A per-row action form: every value comes from the page, none is typed."""

    prefix: str = Field(max_length=64, json_schema_extra=HIDDEN)
    key: str = Field(max_length=1024, json_schema_extra=HIDDEN)
    position: int = Field(ge=0, json_schema_extra=HIDDEN)


VALUES: dict[str, str | int] = {"prefix": "docs/", "key": "docs/a.txt", "position": 3}


def _html(**options: object) -> str:
    """Render a form for :class:`RowActionSchema`.

    Args:
        **options (object): Forwarded to :func:`form_for`.

    Returns:
        str: The rendered HTML.
    """
    return render_to_html(form_for(RowActionSchema, action="/remove", **options))


def test_short_string_renders_a_bare_hidden_input() -> None:
    html = _html(values=VALUES)
    assert '<input type="hidden" name="prefix" value="docs/" />' in html


def test_long_string_does_not_turn_into_a_textarea() -> None:
    html = _html(values=VALUES)
    assert '<input type="hidden" name="key" value="docs/a.txt" />' in html
    assert "<textarea" not in html


def test_integer_renders_a_bare_hidden_input() -> None:
    html = _html(values=VALUES)
    assert '<input type="hidden" name="position" value="3" />' in html


def test_hidden_fields_render_no_label_hint_or_wrapper() -> None:
    class NoteSchema(BaseModel):
        """A hidden field that also carries a description."""

        key: str = Field(description="Object key.", json_schema_extra=HIDDEN)

    html = render_to_html(form_for(NoteSchema, action="/x", values={"key": "k"}))
    assert "<label" not in html
    assert "<small" not in html
    assert "tui-field" not in html
    assert "Object key." not in html


def test_hidden_spec_drops_validation_attributes() -> None:
    specs = {spec.name: spec for spec in fields_for(RowActionSchema)}
    assert specs["key"].control == "hidden"
    assert specs["key"].constraints == {}
    assert specs["position"].constraints == {}


def test_hidden_field_errors_join_the_form_level_errors() -> None:
    html = _html(values=VALUES, errors={"position": ["must be positive"]})
    assert '<div class="tui-form__errors" role="alert">' in html
    assert "<p>Position: must be positive</p>" in html


def test_omit_drops_the_field_and_hidden_true_keeps_that_meaning() -> None:
    class ServerOwnedSchema(BaseModel):
        """Two spellings of "leave this field out of the form"."""

        title: str
        owner_id: str = Field(default="", json_schema_extra={"ui": {"omit": True}})
        tenant_id: str = Field(default="", json_schema_extra={"ui": {"hidden": True}})

    assert [spec.name for spec in fields_for(ServerOwnedSchema)] == ["title"]


def test_hidden_values_go_through_parse_form() -> None:
    app = FastAPI()

    @app.post("/remove")
    async def remove(request: Request) -> Response:
        """Echo the parsed row action."""
        result = await parse_form(RowActionSchema, request)
        if not result.ok:
            return JSONResponse({"errors": result.errors}, status_code=422)
        return JSONResponse(result.unwrap().model_dump())

    client = TestClient(app)
    ok = client.post(
        "/remove",
        data={"prefix": "docs/", "key": "docs/a.txt", "position": "3"},
    )
    assert ok.status_code == 200
    assert ok.json() == {"prefix": "docs/", "key": "docs/a.txt", "position": 3}

    bad = client.post(
        "/remove",
        data={"prefix": "docs/", "key": "docs/a.txt", "position": "-1"},
    )
    assert bad.status_code == 422
    assert list(bad.json()["errors"]) == ["position"]
