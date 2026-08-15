"""Tests for serving a compiled tempestweb build from FastAPI."""

from __future__ import annotations

from pathlib import Path

import pytest
from fastapi import FastAPI, Request
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


def test_shell_string_replaces_the_generated_document(
    builds: dict[str, Path],
) -> None:
    """The document an application owns is the one that ships."""
    document = (
        '<!doctype html><html lang="pt-BR"><head><title>meu</title></head></html>'
    )
    with _static_client(builds["wasm"], shell=document) as client:
        response = client.get("/")
    assert response.status_code == 200
    assert response.text == document
    assert response.headers["content-type"].startswith("text/html")
    assert response.headers["cache-control"] == "no-cache"


def test_shell_path_is_read_from_disk(
    builds: dict[str, Path],
    tmp_path: Path,
) -> None:
    custom = tmp_path / "shell.html"
    custom.write_text('<html lang="pt-BR"><body>custom</body></html>', encoding="utf-8")
    with _static_client(builds["wasm"], shell=custom) as client:
        response = client.get("/")
    assert "custom" in response.text


def test_shell_callable_runs_per_request(builds: dict[str, Path]) -> None:
    """What makes a per-response CSP nonce possible."""
    seen: list[int] = []

    def shell() -> str:
        seen.append(len(seen))
        return f'<html lang="pt-BR"><body>nonce-{len(seen)}</body></html>'

    with _static_client(builds["wasm"], shell=shell) as client:
        first = client.get("/")
        second = client.get("/")
    assert "nonce-1" in first.text
    assert "nonce-2" in second.text


def test_shell_callable_may_take_the_request(builds: dict[str, Path]) -> None:
    def shell(request: Request) -> str:
        return f'<html lang="pt-BR"><body>{request.url.path}</body></html>'

    with _static_client(builds["wasm"], shell=shell) as client:
        response = client.get("/deep/link")
    assert "/deep/link" in response.text


def test_shell_also_answers_the_spa_fallback(builds: dict[str, Path]) -> None:
    document = '<html lang="pt-BR"><body>shell</body></html>'
    with _static_client(builds["wasm"], shell=document) as client:
        deep = client.get("/some/client/route")
    assert deep.status_code == 200
    assert deep.text == document


def test_shell_does_not_shadow_a_real_asset(builds: dict[str, Path]) -> None:
    with _static_client(builds["wasm"], shell="<html><body>x</body></html>") as client:
        asset = client.get("/bootstrap.js")
    assert asset.status_code == 200
    assert "<body>x</body>" not in asset.text


def test_shell_string_without_markup_is_rejected(builds: dict[str, Path]) -> None:
    """A path written where a document was expected fails loudly."""
    with pytest.raises(ValueError, match="the HTML document itself"):
        make_web_app_router(builds["wasm"], shell="dist/index.html")
    with pytest.raises(ValueError, match="the HTML document itself"):
        build_web_app(builds["server"], shell="dist/index.html")


def test_build_web_app_serves_a_custom_shell(builds: dict[str, Path]) -> None:
    document = '<!doctype html><html lang="pt-BR"><body>server shell</body></html>'
    app = build_web_app(builds["server"], shell=document)
    with TestClient(app) as client:
        response = client.get("/")
    assert response.status_code == 200
    assert response.text == document


def test_build_web_app_shell_callable_sees_the_request(
    builds: dict[str, Path],
) -> None:
    def shell(request: Request) -> str:
        nonce = request.headers.get("x-nonce", "")
        return (
            f'<html lang="pt-BR"><head><script nonce="{nonce}"></script></head></html>'
        )

    app = build_web_app(builds["server"], shell=shell)
    with TestClient(app) as client:
        response = client.get("/", headers={"x-nonce": "abc123"})
    assert 'nonce="abc123"' in response.text


def test_default_shell_is_unchanged(builds: dict[str, Path]) -> None:
    """Omitting `shell` keeps serving the artifact's own index.html."""
    generated = (builds["wasm"] / "index.html").read_text(encoding="utf-8")
    with _static_client(builds["wasm"]) as client:
        response = client.get("/")
    assert response.text == generated
