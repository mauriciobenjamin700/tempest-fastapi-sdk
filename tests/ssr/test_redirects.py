"""redirect_back refuses every Referer that would make an open redirect (#349)."""

from __future__ import annotations

import pytest
from fastapi import FastAPI, Request
from fastapi.responses import RedirectResponse
from fastapi.testclient import TestClient

from tempest_fastapi_sdk.ssr import redirect_back

FALLBACK = "/admin"


def _client() -> TestClient:
    app = FastAPI()

    @app.post("/admin/action")
    async def action(request: Request) -> RedirectResponse:
        return redirect_back(request, fallback=FALLBACK, allowed_prefix="/admin")

    @app.post("/anywhere")
    async def anywhere(request: Request) -> RedirectResponse:
        return redirect_back(request, fallback="/")

    return TestClient(app, follow_redirects=False)


def _location(referer: str | None, path: str = "/admin/action") -> str:
    headers = {} if referer is None else {"referer": referer}
    response = _client().post(path, headers=headers)
    assert response.status_code == 303
    return response.headers["location"]


def test_same_host_path_under_prefix_is_followed() -> None:
    assert _location("http://testserver/admin/buckets") == "/admin/buckets"


def test_query_string_is_kept() -> None:
    assert (
        _location("http://testserver/admin/buckets?page=2&q=a")
        == "/admin/buckets?page=2&q=a"
    )


def test_location_is_relative_even_when_the_referer_passes() -> None:
    location = _location("https://testserver/admin/buckets")
    assert location.startswith("/")
    assert "testserver" not in location


def test_prefix_itself_is_followed() -> None:
    assert _location("http://testserver/admin") == "/admin"


def test_missing_referer_goes_to_fallback() -> None:
    assert _location(None) == FALLBACK


@pytest.mark.parametrize(
    "referer",
    [
        pytest.param("https://evil.example/admin/buckets", id="other-host"),
        pytest.param("https://testserver.evil.example/admin", id="suffix-host"),
        pytest.param("https://testserver@evil.example/admin", id="userinfo-host"),
        pytest.param("http://testserver:8080/admin", id="other-port"),
        pytest.param("/admin/buckets", id="no-host"),
        pytest.param("//evil.example/admin", id="protocol-relative"),
        pytest.param("javascript:alert(1)//testserver/admin", id="javascript-scheme"),
        pytest.param("data:text/html,<script>x</script>", id="data-scheme"),
        pytest.param("ftp://testserver/admin", id="ftp-scheme"),
        pytest.param("http://testserver/public", id="outside-prefix"),
        pytest.param("http://testserver/administrator", id="prefix-without-boundary"),
        pytest.param("http://testserver//evil.example/admin", id="double-slash-path"),
        pytest.param("http://testserver/admin/../public", id="dot-dot-segment"),
        pytest.param("http://testserver/admin/%2e%2e/public", id="encoded-dot-dot"),
        pytest.param("http://testserver/%2Fevil.example/admin", id="encoded-slash"),
        pytest.param("http://testserver/admin\\..\\public", id="backslash"),
        pytest.param("http://testserver/admin/%0d%0aSet-Cookie:x", id="crlf"),
        pytest.param("not a url at all", id="garbage"),
        pytest.param("http://[::1", id="unparseable"),
    ],
)
def test_unsafe_referer_goes_to_fallback(referer: str) -> None:
    assert _location(referer) == FALLBACK


def test_default_prefix_accepts_any_path_on_this_host() -> None:
    assert _location("http://testserver/public/x", path="/anywhere") == "/public/x"
    assert _location("https://evil.example/public", path="/anywhere") == "/"
