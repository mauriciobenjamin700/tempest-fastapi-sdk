"""Tests for tempest_fastapi_sdk.api.middlewares.RequestIDMiddleware."""

import uuid

import pytest
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient

from tempest_fastapi_sdk import RequestIDMiddleware, get_request_id


def _make_app() -> FastAPI:
    app = FastAPI()
    app.add_middleware(RequestIDMiddleware)

    @app.get("/echo")
    async def echo() -> dict[str, str | None]:
        return {"request_id": get_request_id()}

    return app


@pytest.mark.asyncio
async def test_inbound_header_is_propagated() -> None:
    app = _make_app()
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.get("/echo", headers={"X-Request-ID": "abc-123"})
    assert response.status_code == 200
    assert response.json() == {"request_id": "abc-123"}
    assert response.headers["X-Request-ID"] == "abc-123"


@pytest.mark.asyncio
async def test_missing_header_generates_uuid() -> None:
    app = _make_app()
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.get("/echo")
    rid = response.headers["X-Request-ID"]
    uuid.UUID(rid)
    assert response.json() == {"request_id": rid}


@pytest.mark.asyncio
async def test_context_is_isolated_between_requests() -> None:
    app = _make_app()
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        first = await client.get("/echo", headers={"X-Request-ID": "first"})
        second = await client.get("/echo", headers={"X-Request-ID": "second"})
    assert first.json()["request_id"] == "first"
    assert second.json()["request_id"] == "second"
    assert get_request_id() is None


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "malicious",
    [
        "abc\r\nX-Injected: 1",
        "abc\nlevel=CRITICAL",
        "abc with spaces",
        "abc;rm -rf /",
        "x" * 200,  # exceeds 128-char cap
    ],
)
async def test_malicious_header_replaced_with_uuid(malicious: str) -> None:
    """Reject CRLF and other unsafe characters to block log/header injection."""
    app = _make_app()
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.get("/echo", headers={"X-Request-ID": malicious})
    rid = response.headers["X-Request-ID"]
    uuid.UUID(rid)  # must be a fresh UUID, not the attacker's value
    assert rid != malicious
    assert response.json()["request_id"] == rid


@pytest.mark.asyncio
async def test_custom_header_name() -> None:
    app = FastAPI()
    app.add_middleware(RequestIDMiddleware, header_name="X-Correlation-ID")

    @app.get("/echo")
    async def echo() -> dict[str, str | None]:
        return {"request_id": get_request_id()}

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.get(
            "/echo",
            headers={"X-Correlation-ID": "trace-9"},
        )
    assert response.headers["X-Correlation-ID"] == "trace-9"
    assert response.json() == {"request_id": "trace-9"}
