"""Tests for serving a compiled tempestweb build from FastAPI."""

from __future__ import annotations

from pathlib import Path

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from tempestweb.cli import build_artifact, scaffold_project

from tempest_fastapi_sdk.ssr import (
    build_web_app,
    detect_build_mode,
    make_web_app_router,
)


@pytest.fixture(scope="module")
def builds(tmp_path_factory: pytest.TempPathFactory) -> dict[str, Path]:
    """Scaffold a tempestweb project and build both artifacts once."""
    parent = tmp_path_factory.mktemp("tw")
    scaffold_project("demo", parent=str(parent))
    root = parent / "demo"
    wasm = build_artifact(str(root), mode="wasm").out_dir
    server = build_artifact(str(root), mode="server").out_dir
    return {"wasm": Path(wasm), "server": Path(server)}


def test_detect_build_mode(builds: dict[str, Path]) -> None:
    assert detect_build_mode(builds["wasm"]) == "wasm"
    assert detect_build_mode(builds["server"]) == "server"


def test_detect_build_mode_rejects_non_build(tmp_path: Path) -> None:
    with pytest.raises(ValueError, match="not a tempestweb build"):
        detect_build_mode(tmp_path)


def _static_client(directory: Path, **kwargs: object) -> TestClient:
    app = FastAPI()
    app.include_router(make_web_app_router(directory, **kwargs))  # type: ignore[arg-type]
    return TestClient(app)


def test_serves_index_at_root(builds: dict[str, Path]) -> None:
    with _static_client(builds["wasm"]) as client:
        response = client.get("/")
    assert response.status_code == 200
    assert '<div id="app">' in response.text
    assert response.headers["cache-control"] == "no-cache"


def test_serves_asset_with_media_type(builds: dict[str, Path]) -> None:
    with _static_client(builds["wasm"]) as client:
        response = client.get("/bootstrap.js")
    assert response.status_code == 200
    assert response.headers["content-type"].startswith("text/javascript")
    assert response.headers["cache-control"] == "public, max-age=3600"


def test_service_worker_headers(builds: dict[str, Path]) -> None:
    with _static_client(builds["wasm"]) as client:
        response = client.get("/sw.js")
    assert response.status_code == 200
    # Root-served worker may claim the whole origin scope, always revalidated.
    assert response.headers["service-worker-allowed"] == "/"
    assert response.headers["cache-control"] == "no-cache"


def test_wasm_archive_media_type(builds: dict[str, Path]) -> None:
    with _static_client(builds["wasm"]) as client:
        response = client.get("/tempestweb-pkg.zip")
    assert response.status_code == 200
    assert response.headers["content-type"] == "application/zip"


def test_spa_history_fallback(builds: dict[str, Path]) -> None:
    with _static_client(builds["wasm"]) as client:
        response = client.get("/some/client/route")
    assert response.status_code == 200
    assert '<div id="app">' in response.text


def test_spa_fallback_disabled_returns_404(builds: dict[str, Path]) -> None:
    with _static_client(builds["wasm"], spa_fallback=False) as client:
        response = client.get("/does-not-exist")
    assert response.status_code == 404


def test_api_route_wins_when_included_last(builds: dict[str, Path]) -> None:
    app = FastAPI()

    @app.get("/api/ping")
    async def ping() -> dict[str, str]:
        return {"pong": "ok"}

    app.include_router(make_web_app_router(builds["wasm"]))
    with TestClient(app) as client:
        assert client.get("/api/ping").json() == {"pong": "ok"}
        # An unknown path still falls back to the SPA shell.
        assert '<div id="app">' in client.get("/anything").text


def test_custom_security_headers(builds: dict[str, Path]) -> None:
    csp = {"Content-Security-Policy": "default-src 'self'"}
    with _static_client(builds["wasm"], security_headers=csp) as client:
        response = client.get("/")
    assert response.headers["content-security-policy"] == "default-src 'self'"


def test_router_rejects_server_build(builds: dict[str, Path]) -> None:
    with pytest.raises(ValueError, match=r"server.*build"):
        make_web_app_router(builds["server"])


def test_build_web_app_hosts_server_build(builds: dict[str, Path]) -> None:
    app = build_web_app(builds["server"], title="demo")
    paths = {getattr(route, "path", None) for route in app.routes}
    assert "/ws" in paths
    assert "/sse" in paths
    with TestClient(app) as client:
        index = client.get("/")
        assert index.status_code == 200
        assert '<div id="app">' in index.text
        asset = client.get("/static/tempestweb.js")
        assert asset.status_code == 200


def test_build_web_app_rejects_wasm_build(builds: dict[str, Path]) -> None:
    with pytest.raises(ValueError, match=r"wasm.*build"):
        build_web_app(builds["wasm"])
