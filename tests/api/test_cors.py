"""Tests for tempest_fastapi_sdk.api.middlewares.cors.apply_cors."""

import pytest
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient
from pydantic import ValidationError

from tempest_fastapi_sdk import CORSSettings, EnvironmentSettings, apply_cors


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


class _ProductionCORS(EnvironmentSettings, CORSSettings):
    """``CORSSettings`` composed with the production guard."""


async def _reflected_headers(
    settings: CORSSettings, origin: str
) -> tuple[str | None, str | None]:
    """Send a cross-origin GET from an untrusted origin through ``apply_cors``.

    Args:
        settings (CORSSettings): The configuration under test.
        origin (str): The ``Origin`` header the untrusted page sends.

    Returns:
        tuple[str | None, str | None]: The ``Access-Control-Allow-Origin``
        and ``Access-Control-Allow-Credentials`` response headers.
    """
    app = _app()
    apply_cors(app, settings)
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.get("/", headers={"Origin": origin})
    return (
        response.headers.get("access-control-allow-origin"),
        response.headers.get("access-control-allow-credentials"),
    )


class TestCredentialsReflectingOriginsInProduction:
    """``ENV=production`` refuses every rule that echoes any origin with credentials."""

    @pytest.mark.parametrize(
        ("overrides", "origin", "fragment"),
        [
            (
                {
                    "CORS_ORIGINS": ["https://app.example.com"],
                    "CORS_ORIGIN_REGEX": ".*",
                },
                "https://evil.example",
                "a CORS_ORIGIN_REGEX that accepts arbitrary origins",
            ),
            (
                {
                    "CORS_ORIGINS": ["https://app.example.com"],
                    "CORS_ORIGIN_REGEX": "https://.*",
                },
                "https://evil.example",
                "a CORS_ORIGIN_REGEX that accepts arbitrary origins",
            ),
            (
                {"CORS_ORIGINS": ["https://app.example.com", "null"]},
                "null",
                '"null" in CORS_ORIGINS',
            ),
            ({"CORS_ORIGINS": ["*"]}, "https://evil.example", '"*" in CORS_ORIGINS'),
        ],
    )
    @pytest.mark.asyncio
    async def test_refused_in_production(
        self, overrides: dict[str, object], origin: str, fragment: str
    ) -> None:
        """The dev build reflects the untrusted origin; production refuses it."""
        development: _ProductionCORS = _ProductionCORS.model_validate(
            {"ENV": "development", "CORS_ALLOW_CREDENTIALS": True, **overrides}
        )
        assert await _reflected_headers(development, origin) == (origin, "true")
        with pytest.raises(ValidationError) as caught:
            _ProductionCORS.model_validate(
                {"ENV": "production", "CORS_ALLOW_CREDENTIALS": True, **overrides}
            )
        assert f"CORS_ALLOW_CREDENTIALS: true with {fragment}" in str(caught.value)

    @pytest.mark.parametrize(
        "regex", [r"https://.*\.vercel\.app", r"https://(app|admin)\.example\.com"]
    )
    def test_anchored_regex_with_credentials_builds(self, regex: str) -> None:
        """A regex pinned to the service's own domains is still accepted."""
        settings: _ProductionCORS = _ProductionCORS(
            ENV="production",
            CORS_ORIGINS=["https://app.example.com"],
            CORS_ORIGIN_REGEX=regex,
            CORS_ALLOW_CREDENTIALS=True,
        )
        assert settings.production_violations() == []

    def test_regex_without_credentials_is_not_refused(self) -> None:
        """Without credentials an open regex only grants anonymous reads."""
        settings: _ProductionCORS = _ProductionCORS(
            ENV="production",
            CORS_ORIGINS=["https://app.example.com"],
            CORS_ORIGIN_REGEX=".*",
        )
        assert settings.production_violations() == []
