"""The 429 body is the SDK's own error envelope, not loose text."""

from __future__ import annotations

import pytest
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient

from tempest_fastapi_sdk import (
    ErrorResponseSchema,
    NotFoundException,
    RateLimitMiddleware,
    TooManyRequestsException,
    register_exception_handlers,
)
from tempest_fastapi_sdk.api.middlewares.quota import (
    RateLimitRule,
    StaticRateLimitPolicy,
)


def _make_app(**kwargs: object) -> FastAPI:
    """Build an app whose handlers and middleware both answer errors.

    Args:
        **kwargs (object): Forwarded to ``RateLimitMiddleware``.

    Returns:
        FastAPI: An app with ``/ping`` and a ``/missing`` route that
            raises an ``AppException``, so both failure modes are
            reachable from one client.
    """
    app = FastAPI()
    register_exception_handlers(app)
    app.add_middleware(RateLimitMiddleware, **kwargs)

    @app.get("/ping")
    async def ping() -> dict[str, bool]:
        return {"ok": True}

    @app.get("/missing")
    async def missing() -> dict[str, bool]:
        raise NotFoundException("Service not found")

    return app


async def _exhaust(app: FastAPI, hits: int, path: str = "/ping") -> object:
    """Spend the whole budget and return the response that got rejected.

    Args:
        app (FastAPI): The app under test.
        hits (int): How many requests the limit allows.
        path (str): The route to hammer.

    Returns:
        object: The first rejected response.
    """
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        for _ in range(hits):
            await client.get(path)
        return await client.get(path)


@pytest.mark.asyncio
async def test_429_is_json_not_text() -> None:
    app = _make_app(max_requests=1, window_seconds=10.0)
    response = await _exhaust(app, 1)
    assert response.status_code == 429
    assert response.headers["content-type"].startswith("application/json")


@pytest.mark.asyncio
async def test_429_parses_as_the_declared_schema() -> None:
    """``error_responses()`` points 429 at this model; the body now matches."""
    app = _make_app(max_requests=1, window_seconds=10.0)
    response = await _exhaust(app, 1)
    envelope = ErrorResponseSchema.model_validate(response.json())
    assert envelope.code == TooManyRequestsException.code
    assert envelope.detail == "Too many requests"


@pytest.mark.asyncio
async def test_one_envelope_for_every_failure_mode() -> None:
    """The middleware's 429 and a handler's 404 carry the same keys."""
    app = _make_app(max_requests=2, window_seconds=10.0)
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        handled = await client.get("/missing")
        await client.get("/missing")
        rejected = await client.get("/missing")

    assert handled.status_code == 404
    assert rejected.status_code == 429
    assert handled.json().keys() == rejected.json().keys()
    assert handled.json()["code"] == "NOT_FOUND"
    assert rejected.json()["code"] == "TOO_MANY_REQUESTS"


@pytest.mark.asyncio
async def test_details_carry_what_only_headers_had() -> None:
    app = _make_app(max_requests=3, window_seconds=10.0)
    response = await _exhaust(app, 3)
    details = response.json()["details"]
    assert details["limit"] == 3
    assert details["retry_after_seconds"] == int(response.headers["Retry-After"])


@pytest.mark.asyncio
async def test_error_code_is_configurable() -> None:
    app = _make_app(
        max_requests=1,
        window_seconds=10.0,
        error_message="Calma parceiro.",
        error_code="RATE_LIMITED",
    )
    response = await _exhaust(app, 1)
    assert response.json() == {
        "detail": "Calma parceiro.",
        "code": "RATE_LIMITED",
        "details": {"retry_after_seconds": 10, "limit": 1},
    }


@pytest.mark.asyncio
async def test_policy_mode_emits_the_same_envelope() -> None:
    """Both counter backends answer through one code path."""
    app = _make_app(
        policy=StaticRateLimitPolicy(
            [RateLimitRule(max_requests=2, window_seconds=10.0)],
        ),
    )
    response = await _exhaust(app, 2)
    body = response.json()
    assert body["code"] == "TOO_MANY_REQUESTS"
    assert body["details"]["limit"] == 2
    assert body["details"]["retry_after_seconds"] >= 1
