"""Tests for the role / permission dependency factories."""

from __future__ import annotations

from collections.abc import Callable

import pytest
from fastapi import Depends, FastAPI
from httpx import ASGITransport, AsyncClient

from tempest_fastapi_sdk import (
    JWTUtils,
    make_permission_dependency,
    make_role_dependency,
    register_exception_handlers,
)


def _tokens() -> JWTUtils:
    return JWTUtils(secret="a" * 32)


async def _call(app: FastAPI, *, token: str | None = None) -> int:
    headers: dict[str, str] = {}
    if token is not None:
        headers["Authorization"] = f"Bearer {token}"
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.get("/protected", headers=headers)
    return response.status_code


def _make_app(dep: Callable[..., object]) -> FastAPI:
    app = FastAPI()
    register_exception_handlers(app)

    @app.get("/protected", dependencies=[Depends(dep)])
    async def protected() -> dict[str, bool]:
        return {"ok": True}

    return app


@pytest.mark.asyncio
async def test_role_dep_authorizes_any_match_by_default() -> None:
    tokens = _tokens()
    dep = make_role_dependency(tokens, ["admin", "editor"])
    app = _make_app(dep)
    token = tokens.encode({"sub": "1", "roles": ["editor"]})
    assert await _call(app, token=token) == 200


@pytest.mark.asyncio
async def test_role_dep_rejects_missing_required_role() -> None:
    tokens = _tokens()
    dep = make_role_dependency(tokens, ["admin"])
    app = _make_app(dep)
    token = tokens.encode({"sub": "1", "roles": ["viewer"]})
    assert await _call(app, token=token) == 403


@pytest.mark.asyncio
async def test_role_dep_require_all_needs_every_role() -> None:
    tokens = _tokens()
    dep = make_role_dependency(tokens, ["admin", "editor"], require_all=True)
    app = _make_app(dep)
    token_one = tokens.encode({"sub": "1", "roles": ["admin"]})
    token_both = tokens.encode({"sub": "1", "roles": ["admin", "editor"]})
    assert await _call(app, token=token_one) == 403
    assert await _call(app, token=token_both) == 200


@pytest.mark.asyncio
async def test_role_dep_accepts_string_roles_claim() -> None:
    tokens = _tokens()
    dep = make_role_dependency(tokens, ["admin"])
    app = _make_app(dep)
    token = tokens.encode({"sub": "1", "roles": "admin"})
    assert await _call(app, token=token) == 200


@pytest.mark.asyncio
async def test_role_dep_rejects_missing_token() -> None:
    tokens = _tokens()
    dep = make_role_dependency(tokens, ["admin"])
    app = _make_app(dep)
    assert await _call(app) == 401


@pytest.mark.asyncio
async def test_permission_dep_defaults_to_require_all() -> None:
    tokens = _tokens()
    dep = make_permission_dependency(tokens, ["users:read", "users:write"])
    app = _make_app(dep)
    only_one = tokens.encode({"sub": "1", "permissions": ["users:read"]})
    both = tokens.encode(
        {"sub": "1", "permissions": ["users:read", "users:write"]},
    )
    assert await _call(app, token=only_one) == 403
    assert await _call(app, token=both) == 200


@pytest.mark.asyncio
async def test_permission_dep_custom_claim() -> None:
    tokens = _tokens()
    dep = make_permission_dependency(
        tokens,
        ["x"],
        permissions_claim="scopes",
    )
    app = _make_app(dep)
    token = tokens.encode({"sub": "1", "scopes": ["x"]})
    assert await _call(app, token=token) == 200
