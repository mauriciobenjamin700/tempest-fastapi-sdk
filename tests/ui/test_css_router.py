"""Tests for serving a typed stylesheet over HTTP."""

from __future__ import annotations

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from tempest_fastapi_sdk.ui.css import (
    Rule,
    StyleSheet,
    css_response,
    make_css_router,
    stylesheet_links,
)

SHEET: StyleSheet = StyleSheet(rules=[Rule(".card", declarations={"padding": "16px"})])


def _client(**options: str) -> TestClient:
    """Build a test client serving :data:`SHEET`.

    Args:
        **options (str): Forwarded to :func:`make_css_router`.

    Returns:
        TestClient: A client bound to an app with the CSS router.
    """
    app = FastAPI()
    app.include_router(make_css_router(SHEET, **options))
    return TestClient(app)


def test_serves_css_with_etag_and_cache_control() -> None:
    response = _client().get("/static/app.css")
    assert response.status_code == 200
    assert response.headers["content-type"].startswith("text/css")
    assert response.headers["cache-control"] == "public, max-age=3600"
    assert response.headers["etag"] == SHEET.etag()
    assert ".card" in response.text


def test_conditional_request_answers_304() -> None:
    client = _client()
    etag = client.get("/static/app.css").headers["etag"]
    conditional = client.get("/static/app.css", headers={"If-None-Match": etag})
    assert conditional.status_code == 304
    assert conditional.text == ""


def test_stale_etag_gets_the_body() -> None:
    response = _client().get("/static/app.css", headers={"If-None-Match": '"stale"'})
    assert response.status_code == 200
    assert ".card" in response.text


def test_custom_path_and_cache_control() -> None:
    client = _client(path="/assets/site.css", cache_control="no-store")
    response = client.get("/assets/site.css")
    assert response.status_code == 200
    assert response.headers["cache-control"] == "no-store"


def test_relative_path_is_rejected() -> None:
    with pytest.raises(ValueError, match="must start with"):
        make_css_router(SHEET, path="static/app.css")


def test_stylesheet_is_hidden_from_the_schema() -> None:
    app = FastAPI()
    app.include_router(make_css_router(SHEET))
    assert app.openapi()["paths"] == {}


def test_css_response_carries_headers() -> None:
    response = css_response(SHEET, cache_control="max-age=60")
    assert response.headers["etag"] == SHEET.etag()
    assert response.headers["cache-control"] == "max-age=60"
    assert response.media_type == "text/css; charset=utf-8"


def test_stylesheet_links_escapes_quotes() -> None:
    assert stylesheet_links("/a.css", "/b.css") == (
        '<link rel="stylesheet" href="/a.css"><link rel="stylesheet" href="/b.css">'
    )
    assert stylesheet_links('/a".css') == '<link rel="stylesheet" href="/a&quot;.css">'
