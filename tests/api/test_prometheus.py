"""Tests for the Prometheus metrics router + middleware."""

from __future__ import annotations

from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient

from tempest_fastapi_sdk import (
    PrometheusMiddleware,
    make_prometheus_registry,
    make_prometheus_router,
)


def _build_app() -> tuple[FastAPI, object]:
    """Build a tiny app with the Prometheus middleware + router wired."""
    registry = make_prometheus_registry()
    app = FastAPI()
    app.add_middleware(PrometheusMiddleware, registry=registry)
    app.include_router(make_prometheus_router(registry=registry))

    @app.get("/ping")
    async def ping() -> dict[str, str]:
        return {"pong": "ok"}

    @app.get("/items/{item_id}")
    async def get_item(item_id: int) -> dict[str, int]:
        return {"item_id": item_id}

    return app, registry


class TestPrometheusEndpoint:
    async def test_metrics_endpoint_returns_exposition(self) -> None:
        app, _ = _build_app()
        async with AsyncClient(
            transport=ASGITransport(app=app), base_url="http://t"
        ) as c:
            r1 = await c.get("/ping")
            r2 = await c.get("/items/42")
            metrics = await c.get("/metrics")

        assert r1.status_code == 200
        assert r2.status_code == 200
        assert metrics.status_code == 200
        body = metrics.text
        assert "http_requests_total" in body
        assert "http_request_duration_seconds" in body
        assert "http_requests_in_progress" in body

    async def test_path_label_uses_route_template(self) -> None:
        app, _ = _build_app()
        async with AsyncClient(
            transport=ASGITransport(app=app), base_url="http://t"
        ) as c:
            await c.get("/items/1")
            await c.get("/items/2")
            await c.get("/items/3")
            metrics = await c.get("/metrics")

        # Cardinality must stay bounded — `/items/{item_id}` template,
        # not /items/1, /items/2, /items/3.
        assert 'path="/items/{item_id}"' in metrics.text
        assert 'path="/items/1"' not in metrics.text

    async def test_counter_increments_per_status(self) -> None:
        app, _ = _build_app()
        async with AsyncClient(
            transport=ASGITransport(app=app), base_url="http://t"
        ) as c:
            await c.get("/ping")
            await c.get("/ping")
            await c.get("/nonexistent")
            metrics = await c.get("/metrics")

        body = metrics.text
        # Both 200 and 404 series should be present.
        assert 'status="200"' in body
        assert 'status="404"' in body

    async def test_in_progress_gauge_stays_bounded(self) -> None:
        app, _ = _build_app()
        async with AsyncClient(
            transport=ASGITransport(app=app), base_url="http://t"
        ) as c:
            await c.get("/ping")
            await c.get("/ping")
            metrics = await c.get("/metrics")

        # While /metrics is being served the gauge reads 1.0 for the
        # in-flight request — but it must never grow unbounded across
        # multiple requests handled in sequence.
        for line in metrics.text.splitlines():
            if line.startswith("http_requests_in_progress{"):
                value = float(line.rsplit(" ", 1)[1])
                assert 0.0 <= value <= 1.0
