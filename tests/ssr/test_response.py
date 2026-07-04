"""Unit tests for html_response."""

from __future__ import annotations

import pytest
from fastapi.responses import HTMLResponse
from tempest_core import Column, Text, Widget

from tempest_fastapi_sdk.ssr import Page, html_response


class DemoPage(Page):
    """A page used across response tests."""

    name: str

    def body(self) -> Widget:
        return Column(children=[Text(content=f"Hello {self.name}")])


def _page() -> DemoPage:
    return DemoPage(title="Demo", name="Ana")


def test_returns_html_response() -> None:
    resp = html_response(_page(), title="Demo")
    assert isinstance(resp, HTMLResponse)


def test_media_type_is_text_html() -> None:
    resp = html_response(_page(), title="Demo")
    assert resp.media_type == "text/html"
    assert resp.headers["content-type"].startswith("text/html")


def test_status_code_passthrough() -> None:
    resp = html_response(_page(), title="Demo", status_code=201)
    assert resp.status_code == 201


def test_document_true_emits_full_document() -> None:
    resp = html_response(_page(), title="Demo")
    body = resp.body.decode()
    assert "<!doctype html>" in body.lower()
    assert "<title>Demo</title>" in body
    assert "Hello Ana" in body


def test_document_true_without_title_raises() -> None:
    with pytest.raises(ValueError, match="requires a `title`"):
        html_response(_page())


def test_document_false_returns_fragment() -> None:
    resp = html_response(_page(), document=False)
    body = resp.body.decode()
    assert "<!doctype" not in body.lower()
    assert "<html" not in body.lower()
    assert "Hello Ana" in body


def test_document_false_ignores_title() -> None:
    # No title required for a fragment; must not raise.
    resp = html_response(_page(), document=False, title=None)
    assert "Hello Ana" in resp.body.decode()


def test_htmx_injects_local_script_not_cdn() -> None:
    resp = html_response(_page(), title="Demo", htmx=True)
    body = resp.body.decode()
    assert '<script src="/_ssr/htmx.js" defer></script>' in body
    assert "unpkg.com" not in body
    assert "cdn" not in body.lower()


def test_no_htmx_script_when_disabled() -> None:
    resp = html_response(_page(), title="Demo", htmx=False)
    body = resp.body.decode()
    assert "/_ssr/htmx.js" not in body


def test_lang_attribute_passthrough() -> None:
    resp = html_response(_page(), title="Demo", lang="en-US")
    assert 'lang="en-US"' in resp.body.decode()
