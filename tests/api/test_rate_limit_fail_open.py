"""The fail-open wrapper around a rate-limit counter store.

Both halves are pinned here: the bare middleware propagating the outage,
and the wrapper turning it into a served request. The first is what makes
the second necessary, and without it a future change could make the wrapper
redundant without anyone noticing it stopped mattering.
"""

from __future__ import annotations

import logging

import httpx
import pytest
from fastapi import FastAPI

from tempest_fastapi_sdk.api.middlewares.rate_limit import (
    FailOpenRateLimitStore,
    MemoryRateLimitStore,
    RateLimitMiddleware,
    RateLimitResult,
)


class BrokenStore:
    """A counter store that is down, the way Redis is during an incident."""

    async def hit(
        self, key: str, max_requests: int, window_seconds: float
    ) -> RateLimitResult:
        """Fail the way an unreachable backend fails.

        Args:
            key (str): The bucket key.
            max_requests (int): Ignored.
            window_seconds (float): Ignored.

        Returns:
            RateLimitResult: Never — this always raises.

        Raises:
            RuntimeError: Always.
        """
        raise RuntimeError("counter store is down")


def _app(store: object) -> FastAPI:
    """Build an app whose only route is rate limited.

    Args:
        store (object): The counter store to install.

    Returns:
        FastAPI: The application.
    """
    app = FastAPI()

    @app.post("/api/app-errors")
    async def report() -> dict[str, str]:
        """Accept a report.

        Returns:
            dict[str, str]: A fixed body.
        """
        return {"status": "stored"}

    app.add_middleware(RateLimitMiddleware, store=store, max_requests=2)
    return app


async def _post(app: FastAPI) -> httpx.Response:
    """Send one request to the rate-limited route.

    Args:
        app (FastAPI): The application.

    Returns:
        httpx.Response: The response.
    """
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://t") as client:
        return await client.post("/api/app-errors", json={})


async def test_bare_middleware_propagates_a_store_outage() -> None:
    """Without the wrapper, an unreachable store takes the request down.

    Measured, not assumed: this is the behaviour that makes the endpoint
    lose exactly the reports it exists to collect, because the moment the
    counter store is unwell is the moment errors spike.
    """
    with pytest.raises(RuntimeError, match="counter store is down"):
        await _post(_app(BrokenStore()))


async def test_wrapper_serves_the_request_when_the_store_fails() -> None:
    """Losing the report is worse than serving above the ceiling."""
    response = await _post(_app(FailOpenRateLimitStore(BrokenStore())))

    assert response.status_code == 200
    assert response.json() == {"status": "stored"}


async def test_wrapper_logs_that_the_ceiling_is_not_enforced(
    caplog: pytest.LogCaptureFixture,
) -> None:
    """Failing open is not the same as failing silently."""
    with caplog.at_level(logging.WARNING):
        await _post(_app(FailOpenRateLimitStore(BrokenStore())))

    assert any("Rate limit not enforced" in record.message for record in caplog.records)


async def test_wrapper_still_enforces_a_healthy_store() -> None:
    """A working store keeps rejecting past the ceiling.

    The wrapper must not turn the limiter off — only bypass it while the
    backend is unavailable.
    """
    app = _app(FailOpenRateLimitStore(MemoryRateLimitStore()))
    transport = httpx.ASGITransport(app=app)

    async with httpx.AsyncClient(transport=transport, base_url="http://t") as client:
        first = await client.post("/api/app-errors", json={})
        second = await client.post("/api/app-errors", json={})
        third = await client.post("/api/app-errors", json={})

    assert first.status_code == 200
    assert second.status_code == 200
    assert third.status_code == 429
