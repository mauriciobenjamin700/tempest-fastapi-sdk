"""Tests for serving a typed stylesheet over HTTP."""

from __future__ import annotations

import os
import subprocess
import sys

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
    assert response.headers["cache-control"] == "no-cache"
    assert response.headers["etag"] == SHEET.etag()
    assert ".card" in response.text


def test_versioned_url_is_cached_immutably() -> None:
    response = _client().get(SHEET.url())
    assert response.status_code == 200
    assert response.headers["cache-control"] == "public, max-age=31536000, immutable"
    assert response.headers["etag"] == SHEET.etag()
    assert ".card" in response.text


def test_stale_version_is_revalidated_not_cached_long() -> None:
    """A version this process does not serve must never be marked immutable.

    During a rolling deploy a page rendered by a new replica can reach an
    old one; caching the old sheet under the new URL would pin it for a
    year.
    """
    response = _client().get("/static/app.css?v=000000000000")
    assert response.status_code == 200
    assert response.headers["cache-control"] == "no-cache"


def test_versioned_conditional_request_keeps_the_long_policy() -> None:
    response = _client().get(SHEET.url(), headers={"If-None-Match": SHEET.etag()})
    assert response.status_code == 304
    assert response.headers["cache-control"].endswith("immutable")


def test_versioned_cache_control_is_configurable() -> None:
    client = _client(versioned_cache_control="public, max-age=60")
    response = client.get(SHEET.url())
    assert response.headers["cache-control"] == "public, max-age=60"


def test_url_carries_the_content_version() -> None:
    assert SHEET.url("/assets/site.css") == f"/assets/site.css?v={SHEET.version()}"
    assert SHEET.etag().strip('"').startswith(SHEET.version())
    assert len(SHEET.version()) == 12


def test_changing_a_rule_changes_the_url() -> None:
    changed = StyleSheet(rules=[Rule(".card", declarations={"padding": "17px"})])
    assert changed.url() != SHEET.url()
    same = StyleSheet(rules=[Rule(".card", declarations={"padding": "16px"})])
    assert same.url() == SHEET.url()


def test_url_rejects_relative_path_and_query() -> None:
    with pytest.raises(ValueError, match="must start with"):
        SHEET.url("static/app.css")
    with pytest.raises(ValueError, match="query string"):
        SHEET.url("/static/app.css?x=1")


_URL_SCRIPT = (
    "from tempest_fastapi_sdk.ui import app_stylesheet\n"
    "print(app_stylesheet().url('/static/app.css'))\n"
)


def test_same_sheet_yields_the_same_url_across_processes() -> None:
    """Two interpreters render the default sheet to the same versioned URL.

    Each replica of a service computes the URL on its own, so a page from
    one replica must name the sheet another replica serves. The URL is
    computed in two fresh subprocesses (distinct hash seeds, so any
    dependence on ``set``/``dict`` hashing order would show) and compared
    with this process's value.
    """
    from tempest_fastapi_sdk.ui import app_stylesheet

    urls: list[str] = []
    for seed in ("1", "2"):
        completed = subprocess.run(
            [sys.executable, "-c", _URL_SCRIPT],
            capture_output=True,
            text=True,
            check=True,
            env={**os.environ, "PYTHONHASHSEED": seed},
            timeout=120,
        )
        urls.append(completed.stdout.strip())

    assert urls[0] == urls[1] == app_stylesheet().url("/static/app.css")


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


def test_css_response_defaults_to_revalidation() -> None:
    assert css_response(SHEET).headers["cache-control"] == "no-cache"
