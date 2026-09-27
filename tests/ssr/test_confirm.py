"""Confirmation of destructive actions without inline script (#348)."""

from __future__ import annotations

from typing import Any

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from pydantic import BaseModel
from tempest_core import Button, Text

from tempest_fastapi_sdk.ssr import (
    CONFIRM_ATTRIBUTE,
    confirm,
    html_response,
    htmx,
    make_htmx_router,
)
from tempest_fastapi_sdk.ui.forms import form_for

SCRIPT_TAG = '<script src="/_ssr/confirm.js" defer></script>'


class DeleteSchema(BaseModel):
    """An empty form: the action is the whole point."""


def _body(widget: Any, **kwargs: Any) -> str:
    return bytes(html_response(widget, title="T", **kwargs).body).decode()


def test_confirm_helper_returns_the_data_attribute() -> None:
    assert confirm("Remover foto.jpg?") == {"data-confirm": "Remover foto.jpg?"}
    assert CONFIRM_ATTRIBUTE == "data-confirm"


def test_confirm_helper_refuses_a_blank_message() -> None:
    with pytest.raises(ValueError, match="non-blank"):
        confirm("   ")


def test_form_for_emits_data_confirm_on_the_form() -> None:
    body = _body(
        form_for(DeleteSchema, action="/buckets/a/delete", confirm="Remover a?"),
    )
    assert '<form method="post" action="/buckets/a/delete"' in body
    assert 'data-confirm="Remover a?"' in body


def test_form_for_without_confirm_emits_nothing() -> None:
    body = _body(form_for(DeleteSchema, action="/x"))
    assert "data-confirm" not in body
    assert "/_ssr/confirm.js" not in body


def test_explicit_attrs_win_over_confirm() -> None:
    body = _body(
        form_for(
            DeleteSchema,
            action="/x",
            confirm="Pergunta A",
            attrs={"data-confirm": "Pergunta B"},
        ),
    )
    assert 'data-confirm="Pergunta B"' in body
    assert "Pergunta A" not in body


def test_form_for_refuses_a_blank_confirm() -> None:
    with pytest.raises(ValueError, match="non-blank"):
        form_for(DeleteSchema, action="/x", confirm="")


def test_message_is_escaped_as_attribute_data() -> None:
    hostile = 'x"); alert(1); ("<b>'
    body = _body(form_for(DeleteSchema, action="/x", confirm=f"Remover {hostile}?"))
    assert 'data-confirm="Remover x&quot;); alert(1); (&quot;&lt;b&gt;?"' in body
    assert "<script>" not in body.replace(SCRIPT_TAG, "")


def test_script_is_included_automatically_when_the_tree_uses_it() -> None:
    body = _body(form_for(DeleteSchema, action="/x", confirm="Remover?"))
    assert SCRIPT_TAG in body
    assert body.index(SCRIPT_TAG) < body.index("</head>")


def test_button_carries_the_attribute_through_attrs() -> None:
    body = _body(Button(label="Remover", attrs=confirm("Remover tudo?")))
    assert 'data-confirm="Remover tudo?"' in body
    assert SCRIPT_TAG in body


def test_no_script_without_the_attribute() -> None:
    assert SCRIPT_TAG not in _body(Text(content="nada"))


def test_confirm_true_forces_the_script() -> None:
    assert SCRIPT_TAG in _body(Text(content="nada"), confirm=True)


def test_confirm_false_leaves_the_script_out() -> None:
    body = _body(form_for(DeleteSchema, action="/x", confirm="Remover?"), confirm=False)
    assert SCRIPT_TAG not in body
    assert 'data-confirm="Remover?"' in body


def test_htmx_documents_include_the_listener_for_swapped_fragments() -> None:
    assert SCRIPT_TAG in _body(Text(content="nada"), htmx=True)


def test_fragment_never_carries_the_script() -> None:
    body = bytes(
        html_response(
            form_for(DeleteSchema, action="/x", confirm="Remover?"),
            document=False,
        ).body,
    ).decode()
    assert "<script" not in body
    assert 'data-confirm="Remover?"' in body


def test_hx_confirm_stays_the_htmx_attribute() -> None:
    assert htmx(post="/x", confirm="Certeza?") == {
        "hx-post": "/x",
        "hx-confirm": "Certeza?",
    }


def test_router_serves_the_listener_locally() -> None:
    app = FastAPI()
    app.include_router(make_htmx_router())
    with TestClient(app) as client:
        response = client.get("/_ssr/confirm.js")
        assert client.get("/_ssr/htmx.js").status_code == 200
    assert response.status_code == 200
    assert response.headers["content-type"].startswith("application/javascript")
    script = response.text
    assert "getAttribute(ATTRIBUTE)" in script
    assert 'var ATTRIBUTE = "data-confirm"' in script
    assert "window.confirm(message)" in script
    assert "eval" not in script
    assert "innerHTML" not in script
