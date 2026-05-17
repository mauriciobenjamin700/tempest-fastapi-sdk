"""Tests for the make_tool_spec_router helper."""

from __future__ import annotations

from typing import Any

import pytest
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient

from tempest_fastapi_sdk import make_tool_spec_router


async def _make_request(app: FastAPI, path: str = "/tool-spec") -> dict[str, Any]:
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.get(path)
    assert response.status_code == 200
    return response.json()


@pytest.mark.asyncio
async def test_static_dict_spec_is_served_verbatim() -> None:
    app = FastAPI()
    app.include_router(
        make_tool_spec_router({"service": "demo", "tools": []}),
    )
    body = await _make_request(app)
    assert body == {"service": "demo", "tools": []}


@pytest.mark.asyncio
async def test_sync_callable_spec_is_called_each_request() -> None:
    counter: int = 0

    def provider() -> dict[str, int]:
        nonlocal counter
        counter += 1
        return {"counter": counter}

    app = FastAPI()
    app.include_router(make_tool_spec_router(provider))

    first = await _make_request(app)
    second = await _make_request(app)
    assert first == {"counter": 1}
    assert second == {"counter": 2}


@pytest.mark.asyncio
async def test_async_callable_spec_is_awaited() -> None:
    async def provider() -> dict[str, str]:
        return {"async": "yes"}

    app = FastAPI()
    app.include_router(make_tool_spec_router(provider))
    body = await _make_request(app)
    assert body == {"async": "yes"}


@pytest.mark.asyncio
async def test_custom_path_is_respected() -> None:
    app = FastAPI()
    app.include_router(
        make_tool_spec_router({"v": 1}, path="/manifest"),
    )
    body = await _make_request(app, path="/manifest")
    assert body == {"v": 1}
