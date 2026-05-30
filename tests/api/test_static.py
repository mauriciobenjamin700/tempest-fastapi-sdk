"""Tests for tempest_fastapi_sdk.api.static.HardenedStaticFiles."""

from pathlib import Path

from fastapi import FastAPI
from fastapi.testclient import TestClient

from tempest_fastapi_sdk import (
    DEFAULT_STATIC_SECURITY_HEADERS,
    HardenedStaticFiles,
)


def _client(tmp_path: Path, **kwargs: object) -> TestClient:
    (tmp_path / "f.txt").write_text("hello")
    app = FastAPI()
    app.mount(
        "/static",
        HardenedStaticFiles(directory=str(tmp_path), **kwargs),
        name="static",
    )
    return TestClient(app)


class TestHardenedStaticFiles:
    def test_serves_file(self, tmp_path: Path) -> None:
        resp = _client(tmp_path).get("/static/f.txt")
        assert resp.status_code == 200
        assert resp.text == "hello"

    def test_stamps_default_security_headers(self, tmp_path: Path) -> None:
        resp = _client(tmp_path).get("/static/f.txt")
        assert resp.headers["X-Content-Type-Options"] == "nosniff"
        assert "sandbox" in resp.headers["Content-Security-Policy"]
        assert resp.headers["Cross-Origin-Resource-Policy"] == "same-site"

    def test_custom_headers_override(self, tmp_path: Path) -> None:
        resp = _client(
            tmp_path,
            security_headers={"X-Custom": "1"},
        ).get("/static/f.txt")
        assert resp.headers["X-Custom"] == "1"
        # Default headers are replaced wholesale, not merged.
        assert "X-Content-Type-Options" not in resp.headers

    def test_default_headers_constant_shape(self) -> None:
        assert DEFAULT_STATIC_SECURITY_HEADERS["X-Content-Type-Options"] == ("nosniff")
