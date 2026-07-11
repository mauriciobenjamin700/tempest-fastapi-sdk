"""Tests for BusinessMetrics — custom app metrics on /metrics."""

from __future__ import annotations

from fastapi import FastAPI
from fastapi.testclient import TestClient

from tempest_fastapi_sdk import (
    BusinessMetrics,
    make_prometheus_registry,
    make_prometheus_router,
)


def test_custom_metrics_appear_on_endpoint() -> None:
    registry = make_prometheus_registry()
    metrics = BusinessMetrics(registry, namespace="shop")

    orders = metrics.counter("orders_total", "Orders placed", labelnames=["status"])
    orders.labels(status="paid").inc()
    orders.labels(status="paid").inc()

    depth = metrics.gauge("queue_depth", "Queue depth")
    depth.set(7)

    duration = metrics.histogram("job_seconds", "Job duration", buckets=[0.1, 1, 10])
    duration.observe(0.3)

    app = FastAPI()
    app.include_router(make_prometheus_router(registry=registry))
    body = TestClient(app).get("/metrics").text

    # Namespaced names present.
    assert "shop_orders_total" in body
    assert 'status="paid"' in body
    assert "shop_queue_depth 7.0" in body
    assert "shop_job_seconds_bucket" in body


def test_creation_is_deduplicated() -> None:
    registry = make_prometheus_registry()
    metrics = BusinessMetrics(registry)
    # Same name twice returns the same metric (no Duplicated timeseries).
    first = metrics.counter("things_total", "Things")
    second = metrics.counter("things_total", "Things")
    assert first is second


def test_no_namespace() -> None:
    registry = make_prometheus_registry()
    metrics = BusinessMetrics(registry)
    metrics.counter("bare_total", "Bare").inc()

    app = FastAPI()
    app.include_router(make_prometheus_router(registry=registry))
    body = TestClient(app).get("/metrics").text
    assert "bare_total" in body
