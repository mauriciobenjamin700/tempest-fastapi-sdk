"""Tests for tempest_fastapi_sdk.api.dependencies.auth."""

import pytest
from fastapi import Depends, FastAPI
from httpx import ASGITransport, AsyncClient

from tempest_fastapi_sdk import (
    UnauthorizedException,
    make_token_dependency,
    register_exception_handlers,
    require_x_token,
)


def _make_app(secret: str) -> FastAPI:
    app = FastAPI()
    register_exception_handlers(app)
    require_token = make_token_dependency(secret)

    @app.get("/protected", dependencies=[Depends(require_token)])
    async def protected() -> dict[str, bool]:
        return {"ok": True}

    return app


@pytest.mark.asyncio
async def test_valid_token_passes() -> None:
    app = _make_app("s3cret")
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.get("/protected", headers={"X-Token": "s3cret"})
    assert response.status_code == 200
    assert response.json() == {"ok": True}


@pytest.mark.asyncio
async def test_invalid_token_rejected() -> None:
    app = _make_app("s3cret")
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.get("/protected", headers={"X-Token": "wrong"})
    assert response.status_code == 401


@pytest.mark.asyncio
async def test_missing_token_rejected() -> None:
    app = _make_app("s3cret")
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.get("/protected")
    assert response.status_code == 401


@pytest.mark.asyncio
async def test_empty_secret_disables_check() -> None:
    app = _make_app("")
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.get("/protected")
    assert response.status_code == 200


@pytest.mark.asyncio
async def test_imperative_helper_valid() -> None:
    await require_x_token("abc", "abc")


@pytest.mark.asyncio
async def test_imperative_helper_invalid() -> None:
    with pytest.raises(UnauthorizedException):
        await require_x_token("abc", "wrong")


@pytest.mark.asyncio
async def test_imperative_helper_empty_secret_passes() -> None:
    await require_x_token("", "whatever")
