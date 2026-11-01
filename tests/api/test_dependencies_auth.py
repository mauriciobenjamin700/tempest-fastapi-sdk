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


def _make_app(secret: str, *, allow_unauthenticated: bool = False) -> FastAPI:
    app = FastAPI()
    register_exception_handlers(app)
    require_token = make_token_dependency(
        secret, allow_unauthenticated=allow_unauthenticated
    )

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


@pytest.mark.parametrize("secret", ["", "   "])
def test_empty_secret_is_refused_at_build(secret: str) -> None:
    """An empty secret used to open the gate in silence; now it fails loud.

    A ``TOKEN_SECRET`` left empty in a deployed ``.env`` made every route
    behind the dependency public, with nothing in the logs.
    """
    with pytest.raises(ValueError, match="allow_unauthenticated=True"):
        make_token_dependency(secret)


@pytest.mark.asyncio
async def test_empty_secret_with_explicit_opt_out_passes() -> None:
    """The local-run escape hatch is spelled out, not implied."""
    app = _make_app("", allow_unauthenticated=True)
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.get("/protected")
    assert response.status_code == 200


@pytest.mark.asyncio
async def test_opt_out_does_not_weaken_a_real_secret() -> None:
    """``allow_unauthenticated`` only matters when the secret is empty."""
    app = _make_app("s3cret", allow_unauthenticated=True)
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.get("/protected")
    assert response.status_code == 401


@pytest.mark.asyncio
async def test_imperative_helper_valid() -> None:
    await require_x_token("abc", "abc")


@pytest.mark.asyncio
async def test_imperative_helper_invalid() -> None:
    with pytest.raises(UnauthorizedException):
        await require_x_token("abc", "wrong")


@pytest.mark.asyncio
async def test_imperative_helper_empty_secret_is_refused() -> None:
    """Same contract as the dependency: empty means misconfigured."""
    with pytest.raises(ValueError, match="require_x_token"):
        await require_x_token("", "whatever")


@pytest.mark.asyncio
async def test_imperative_helper_empty_secret_with_opt_out_passes() -> None:
    await require_x_token("", "whatever", allow_unauthenticated=True)
