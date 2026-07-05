"""Tests for the JWT bearer + current-user dependency factories."""

from __future__ import annotations

from typing import Any

import pytest
from fastapi import Depends, FastAPI
from httpx import ASGITransport, AsyncClient

from tempest_fastapi_sdk import (
    JWTUtils,
    make_bearer_token_dependency,
    make_jwt_user_dependency,
    register_exception_handlers,
)


def _make_tokens() -> JWTUtils:
    return JWTUtils(secret="a" * 32)


async def _load_user(subject: str) -> dict[str, str]:
    """Synthetic user loader — echoes the subject back."""
    return {"id": subject, "name": f"user-{subject}"}


def _make_bearer_app(*, soft: bool) -> tuple[FastAPI, JWTUtils]:
    tokens = _make_tokens()
    decode = make_bearer_token_dependency(tokens, soft=soft)

    app = FastAPI()
    register_exception_handlers(app)

    @app.get("/claims")
    async def claims(
        payload: dict[str, Any] | None = Depends(decode),
    ) -> dict[str, Any]:
        return {"payload": payload}

    return app, tokens


def _make_user_app(*, soft: bool) -> tuple[FastAPI, JWTUtils]:
    tokens = _make_tokens()
    current_user = make_jwt_user_dependency(tokens, _load_user, soft=soft)

    app = FastAPI()
    register_exception_handlers(app)

    @app.get("/me")
    async def me(user: dict[str, str] | None = Depends(current_user)) -> dict[str, Any]:
        return {"user": user}

    return app, tokens


@pytest.mark.asyncio
async def test_bearer_dependency_returns_claims_for_valid_token() -> None:
    app, tokens = _make_bearer_app(soft=False)
    token = tokens.encode({"sub": "42"})
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.get(
            "/claims",
            headers={"Authorization": f"Bearer {token}"},
        )
    assert response.status_code == 200
    assert response.json()["payload"]["sub"] == "42"


@pytest.mark.asyncio
async def test_bearer_dependency_rejects_missing_token() -> None:
    app, _ = _make_bearer_app(soft=False)
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.get("/claims")
    assert response.status_code == 401


@pytest.mark.asyncio
async def test_bearer_dependency_rejects_invalid_token() -> None:
    app, _ = _make_bearer_app(soft=False)
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.get(
            "/claims",
            headers={"Authorization": "Bearer not-a-jwt"},
        )
    assert response.status_code == 401


@pytest.mark.asyncio
async def test_bearer_dependency_soft_returns_none_when_missing() -> None:
    app, _ = _make_bearer_app(soft=True)
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.get("/claims")
    assert response.status_code == 200
    assert response.json() == {"payload": None}


@pytest.mark.asyncio
async def test_bearer_dependency_soft_returns_none_when_invalid() -> None:
    app, _ = _make_bearer_app(soft=True)
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.get(
            "/claims",
            headers={"Authorization": "Bearer not-a-jwt"},
        )
    assert response.status_code == 200
    assert response.json() == {"payload": None}


@pytest.mark.asyncio
async def test_jwt_user_dependency_loads_user_from_subject() -> None:
    app, tokens = _make_user_app(soft=False)
    token = tokens.encode({"sub": "abc-123"})
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.get(
            "/me",
            headers={"Authorization": f"Bearer {token}"},
        )
    assert response.status_code == 200
    body = response.json()
    assert body["user"]["id"] == "abc-123"
    assert body["user"]["name"] == "user-abc-123"


@pytest.mark.asyncio
async def test_jwt_user_dependency_rejects_missing_token() -> None:
    app, _ = _make_user_app(soft=False)
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.get("/me")
    assert response.status_code == 401


@pytest.mark.asyncio
async def test_jwt_user_dependency_soft_returns_none() -> None:
    app, _ = _make_user_app(soft=True)
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.get("/me")
    assert response.status_code == 200
    assert response.json() == {"user": None}


@pytest.mark.asyncio
async def test_jwt_user_dependency_rejects_missing_subject() -> None:
    app, tokens = _make_user_app(soft=False)
    # Token without a "sub" claim.
    token = tokens.encode({"role": "admin"})
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.get(
            "/me",
            headers={"Authorization": f"Bearer {token}"},
        )
    assert response.status_code == 401


@pytest.mark.asyncio
async def test_bearer_dependency_reads_token_from_query_param() -> None:
    """Cookieless clients (EventSource) can pass the JWT in the query."""
    tokens = _make_tokens()
    decode = make_bearer_token_dependency(tokens, query_param="access_token")

    app = FastAPI()
    register_exception_handlers(app)

    @app.get("/claims")
    async def claims(
        payload: dict[str, Any] | None = Depends(decode),
    ) -> dict[str, Any]:
        return {"payload": payload}

    token = tokens.encode({"sub": "77"})
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.get(f"/claims?access_token={token}")
    assert response.status_code == 200
    assert response.json()["payload"]["sub"] == "77"


@pytest.mark.asyncio
async def test_bearer_dependency_header_wins_over_query_param() -> None:
    """The Authorization header takes precedence over the query string."""
    tokens = _make_tokens()
    decode = make_bearer_token_dependency(tokens, query_param="access_token")

    app = FastAPI()
    register_exception_handlers(app)

    @app.get("/claims")
    async def claims(
        payload: dict[str, Any] | None = Depends(decode),
    ) -> dict[str, Any]:
        return {"payload": payload}

    header_token = tokens.encode({"sub": "header"})
    query_token = tokens.encode({"sub": "query"})
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.get(
            f"/claims?access_token={query_token}",
            headers={"Authorization": f"Bearer {header_token}"},
        )
    assert response.status_code == 200
    assert response.json()["payload"]["sub"] == "header"


@pytest.mark.asyncio
async def test_jwt_user_dependency_reads_token_from_query_param() -> None:
    """current_user resolves from a query-string token end to end."""
    tokens = _make_tokens()
    current_user = make_jwt_user_dependency(
        tokens,
        _load_user,
        query_param="access_token",
    )

    app = FastAPI()
    register_exception_handlers(app)

    @app.get("/me")
    async def me(user: dict[str, str] | None = Depends(current_user)) -> dict[str, Any]:
        return {"user": user}

    token = tokens.encode({"sub": "sse-1"})
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.get(f"/me?access_token={token}")
    assert response.status_code == 200
    assert response.json()["user"]["id"] == "sse-1"


@pytest.mark.asyncio
async def test_jwt_user_dependency_respects_custom_subject_claim() -> None:
    tokens = _make_tokens()
    current_user = make_jwt_user_dependency(
        tokens,
        _load_user,
        subject_claim="user_id",
    )

    app = FastAPI()
    register_exception_handlers(app)

    @app.get("/me")
    async def me(user: dict[str, str] = Depends(current_user)) -> dict[str, Any]:
        return {"user": user}

    token = tokens.encode({"user_id": "xyz"})
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.get(
            "/me",
            headers={"Authorization": f"Bearer {token}"},
        )
    assert response.status_code == 200
    assert response.json()["user"]["id"] == "xyz"
