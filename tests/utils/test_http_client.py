"""Tests for ``HTTPClient`` (httpx wrapper)."""

from __future__ import annotations

import httpx
import pytest

from tempest_fastapi_sdk import (
    REQUEST_ID_HEADER,
    CircuitOpenError,
    HTTPClient,
    RetryPolicy,
)
from tempest_fastapi_sdk.core.context import clear_request_id, set_request_id


def _mock_transport(handler: object) -> httpx.MockTransport:
    """Adapter — accepts a handler with the httpx mock signature."""
    return httpx.MockTransport(handler)  # type: ignore[arg-type]


class TestRetryAndBackoff:
    async def test_success_no_retry(self) -> None:
        calls = {"n": 0}

        def handler(request: httpx.Request) -> httpx.Response:
            calls["n"] += 1
            return httpx.Response(200, json={"ok": True})

        client = HTTPClient(base_url="http://api.test", failure_threshold=0)
        client._client = httpx.AsyncClient(
            base_url="http://api.test",
            transport=_mock_transport(handler),
        )
        try:
            r = await client.get("/ping")
            assert r.status_code == 200
            assert calls["n"] == 1
        finally:
            await client.aclose()

    async def test_5xx_retries_then_succeeds(self) -> None:
        statuses = iter([503, 503, 200])

        def handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(next(statuses))

        client = HTTPClient(
            base_url="http://api.test",
            retry_policy=RetryPolicy(
                max_attempts=3,
                backoff_initial_seconds=0.001,
            ),
            failure_threshold=0,  # disable breaker for this test
        )
        client._client = httpx.AsyncClient(
            base_url="http://api.test", transport=_mock_transport(handler)
        )
        try:
            r = await client.get("/x")
            assert r.status_code == 200
        finally:
            await client.aclose()

    async def test_5xx_exhausts_attempts(self) -> None:
        def handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(502)

        client = HTTPClient(
            base_url="http://api.test",
            retry_policy=RetryPolicy(
                max_attempts=2,
                backoff_initial_seconds=0.001,
            ),
            failure_threshold=0,
        )
        client._client = httpx.AsyncClient(
            base_url="http://api.test", transport=_mock_transport(handler)
        )
        try:
            r = await client.get("/x")
            assert r.status_code == 502
        finally:
            await client.aclose()

    async def test_4xx_never_retries(self) -> None:
        calls = {"n": 0}

        def handler(request: httpx.Request) -> httpx.Response:
            calls["n"] += 1
            return httpx.Response(404)

        client = HTTPClient(base_url="http://api.test", failure_threshold=0)
        client._client = httpx.AsyncClient(
            base_url="http://api.test", transport=_mock_transport(handler)
        )
        try:
            r = await client.get("/missing")
            assert r.status_code == 404
            assert calls["n"] == 1
        finally:
            await client.aclose()


class TestCircuitBreaker:
    async def test_opens_after_threshold(self) -> None:
        def handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(500)

        client = HTTPClient(
            base_url="http://api.test",
            retry_policy=RetryPolicy(
                max_attempts=1,
                backoff_initial_seconds=0.001,
            ),
            failure_threshold=2,
            recovery_seconds=60.0,
        )
        client._client = httpx.AsyncClient(
            base_url="http://api.test", transport=_mock_transport(handler)
        )
        try:
            await client.get("/x")
            await client.get("/x")  # trips the breaker
            with pytest.raises(CircuitOpenError):
                await client.get("/x")
        finally:
            await client.aclose()

    async def test_success_resets_failure_count(self) -> None:
        responses = iter(
            [httpx.Response(500), httpx.Response(200), httpx.Response(500)]
        )

        def handler(request: httpx.Request) -> httpx.Response:
            return next(responses)

        client = HTTPClient(
            base_url="http://api.test",
            retry_policy=RetryPolicy(
                max_attempts=1,
                backoff_initial_seconds=0.001,
            ),
            failure_threshold=2,
        )
        client._client = httpx.AsyncClient(
            base_url="http://api.test", transport=_mock_transport(handler)
        )
        try:
            await client.get("/x")  # 500
            await client.get("/x")  # 200 — counter resets
            await client.get("/x")  # 500 — counter at 1, breaker still closed
            # No CircuitOpenError raised so far.
        finally:
            await client.aclose()


class TestRequestIDPropagation:
    async def test_request_id_attached_when_set(self) -> None:
        captured = {}

        def handler(request: httpx.Request) -> httpx.Response:
            captured["headers"] = dict(request.headers)
            return httpx.Response(200)

        client = HTTPClient(base_url="http://api.test", failure_threshold=0)
        client._client = httpx.AsyncClient(
            base_url="http://api.test", transport=_mock_transport(handler)
        )
        token = set_request_id("trace-xyz")
        try:
            await client.get("/x")
            assert captured["headers"][REQUEST_ID_HEADER.lower()] == "trace-xyz"
        finally:
            clear_request_id(token)
            await client.aclose()

    async def test_request_id_skipped_when_unset(self) -> None:
        captured = {}

        def handler(request: httpx.Request) -> httpx.Response:
            captured["headers"] = dict(request.headers)
            return httpx.Response(200)

        client = HTTPClient(base_url="http://api.test", failure_threshold=0)
        client._client = httpx.AsyncClient(
            base_url="http://api.test", transport=_mock_transport(handler)
        )
        try:
            await client.get("/x")
            assert REQUEST_ID_HEADER.lower() not in captured["headers"]
        finally:
            await client.aclose()


class TestRetryPolicy:
    def test_sleep_grows_exponentially(self) -> None:
        p = RetryPolicy(
            backoff_initial_seconds=0.1,
            backoff_max_seconds=10.0,
        )
        assert p.sleep_for(1) == pytest.approx(0.1)
        assert p.sleep_for(2) == pytest.approx(0.2)
        assert p.sleep_for(3) == pytest.approx(0.4)

    def test_sleep_caps_at_max(self) -> None:
        p = RetryPolicy(
            backoff_initial_seconds=1.0,
            backoff_max_seconds=2.0,
        )
        assert p.sleep_for(10) == pytest.approx(2.0)
