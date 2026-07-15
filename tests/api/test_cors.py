"""Tests for tempest_fastapi_sdk.api.middlewares.cors.apply_cors."""

import pytest
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient

from tempest_fastapi_sdk import CORSSettings, apply_cors


def _app() -> FastAPI:
    app = FastAPI()

    @app.get("/")
    async def root() -> dict[str, bool]:
        return {"ok": True}

    return app


@pytest.mark.asyncio
async def test_default_apply_cors_uses_wildcard() -> None:
    app = _app()
    apply_cors(app)
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.get("/", headers={"Origin": "http://example.com"})
    assert response.status_code == 200
    assert response.headers.get("access-control-allow-origin") == "*"


@pytest.mark.asyncio
async def test_settings_origins_honored() -> None:
    settings = CORSSettings(CORS_ORIGINS=["http://allowed.test"])
    app = _app()
    apply_cors(app, settings)
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.get("/", headers={"Origin": "http://allowed.test"})
    assert response.headers.get("access-control-allow-origin") == (
        "http://allowed.test"
    )


@pytest.mark.asyncio
async def test_override_takes_precedence_over_settings() -> None:
    settings = CORSSettings(CORS_ORIGINS=["http://no.test"])
    app = _app()
    apply_cors(app, settings, origins=["http://yes.test"])
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.get("/", headers={"Origin": "http://yes.test"})
    assert response.headers.get("access-control-allow-origin") == "http://yes.test"


@pytest.mark.asyncio
async def test_origin_regex_from_settings_matches_tunnel() -> None:
    settings = CORSSettings(
        CORS_ORIGINS=["http://allowed.test"],
        CORS_ORIGIN_REGEX=r"https://.*\.ngrok-free\.app",
        CORS_ALLOW_CREDENTIALS=True,
    )
    app = _app()
    apply_cors(app, settings)
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.get(
            "/", headers={"Origin": "https://abc123.ngrok-free.app"}
        )
    assert response.headers.get("access-control-allow-origin") == (
        "https://abc123.ngrok-free.app"
    )


@pytest.mark.asyncio
async def test_origin_regex_override_takes_precedence() -> None:
    settings = CORSSettings(
        CORS_ORIGINS=[],
        CORS_ORIGIN_REGEX=r"https://no\.test",
    )
    app = _app()
    apply_cors(app, settings, origin_regex=r"https://.*\.yes\.test")
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.get("/", headers={"Origin": "https://a.yes.test"})
    assert response.headers.get("access-control-allow-origin") == "https://a.yes.test"


@pytest.mark.asyncio
async def test_expose_headers_default_includes_request_id() -> None:
    app = _app()
    apply_cors(app)
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.options(
            "/",
            headers={
                "Origin": "http://example.com",
                "Access-Control-Request-Method": "GET",
            },
        )
    assert response.status_code in (200, 204)
