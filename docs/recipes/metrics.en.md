# Metrics

The SDK offers **two complementary metrics paths**:

1. **Prometheus RED/USE for HTTP** (`PrometheusMiddleware` + `make_prometheus_router`) — listens to every request, increments `http_requests_total` + a latency histogram + `http_requests_in_progress`, and exposes it all on `GET /metrics` in Prometheus text format ready to be scraped by your Prometheus / Grafana / Datadog.
2. **On-demand system snapshots** (`MetricsUtils`) — collects CPU / memory / disk / NVIDIA GPU stats for a custom endpoint (internal debug page, /oncall, etc.). No built-in Prometheus exporter — the goal is the instant snapshot.

Use **#1** in production always. Add **#2** when you need to inspect the host where the app runs.

## #1 Prometheus HTTP — `[prometheus]` extra

Install with `[prometheus]` (pulls `prometheus-client`). The middleware measures every request; the router serves the scrape endpoint.

```bash
uv add "tempest-fastapi-sdk[prometheus]>=0.89.0"
```

```python
# src/api/app.py
from fastapi import FastAPI

from tempest_fastapi_sdk import (
    PrometheusMiddleware,
    make_prometheus_registry,
    make_prometheus_router,
)


def create_app() -> FastAPI:
    app = FastAPI(title="my-service")

    # Per-app registry — avoids collisions with other global prometheus-client users.
    registry = make_prometheus_registry()

    app.add_middleware(PrometheusMiddleware, registry=registry)
    app.include_router(make_prometheus_router(registry=registry))
    return app
```

Done. `GET /metrics` now returns something like:

```text
# HELP http_requests_total Total HTTP requests
# TYPE http_requests_total counter
http_requests_total{method="GET",path="/api/users",status="200"} 142.0
http_requests_total{method="POST",path="/auth/signup",status="201"} 7.0
# HELP http_request_duration_seconds HTTP request latency
# TYPE http_request_duration_seconds histogram
http_request_duration_seconds_bucket{le="0.005",method="GET",path="/api/users"} 89.0
...
http_requests_in_progress{method="GET"} 2.0
```

Default buckets (`DEFAULT_LATENCY_BUCKETS`) cover 5ms → 30s — fits typical APIs. Override with `PrometheusMiddleware(registry=..., latency_buckets=(0.001, 0.005, 0.025, 0.1, 0.5, 2, 10))` when your workload is more granular.

!!! tip "Path normalization"
    The `path` label uses the route template (`/api/users/{user_id}`), not the concrete path, so cardinality doesn't explode with unique UUIDs. That comes from FastAPI/Starlette — no config needed on your end.

### Scrape config

`prometheus.yml`:

```yaml
scrape_configs:
  - job_name: my-service
    metrics_path: /metrics
    static_configs:
      - targets: ["my-service:8000"]
```

Or via compose:

```yaml
services:
  my-service:
    image: ...
    ports: ["8000:8000"]
  prometheus:
    image: prom/prometheus:latest
    volumes:
      - ./prometheus.yml:/etc/prometheus/prometheus.yml
    ports: ["9090:9090"]
```

### Business metrics — `BusinessMetrics`

The middleware covers HTTP (RED). For **your domain** metrics — orders,
queue depth, job duration — use `BusinessMetrics`: a typed factory of
`Counter` / `Gauge` / `Histogram` bound to the **same** registry, so it
all shows up on the same `GET /metrics`.

```python
from tempest_fastapi_sdk import BusinessMetrics, make_prometheus_registry

registry = make_prometheus_registry()
metrics = BusinessMetrics(registry, namespace="shop")

orders = metrics.counter("orders_total", "Orders placed", labelnames=["status"])
queue = metrics.gauge("queue_depth", "Items in queue")
job = metrics.histogram("job_seconds", "Job duration", buckets=[0.1, 1, 10])

orders.labels(status="paid").inc()
queue.set(await repo.count({"status": "pending"}))
job.observe(elapsed)
```

Shows on `/metrics` as `shop_orders_total{status="paid"}`, `shop_queue_depth`,
`shop_job_seconds_bucket`. Creating the same name twice returns the **same**
metric (no `Duplicated timeseries` on reload/tests).

!!! note "No magic"
    The returned objects are the real `prometheus_client`
    `Counter`/`Gauge`/`Histogram` — `.inc()` / `.set()` / `.observe()` /
    `.labels(...)` behave as upstream documents. `BusinessMetrics` only binds
    them to the registry + namespace, hiding nothing.

## #2 System snapshots — `[metrics]` extra

`MetricsUtils` collects CPU, memory, disk and NVIDIA GPU usage via `psutil` + `pynvml`. Every method has a sync and an async variant (the async wrapper runs the same code via `asyncio.to_thread`). GPU sampling gracefully degrades to `[]` when `pynvml` or NVIDIA drivers are missing.

Install with `[metrics]`.

```python
# src/api/routers/system.py
from typing import Any

from fastapi import APIRouter

from tempest_fastapi_sdk import MetricsUtils

router = APIRouter()


@router.get("/system-metrics")
async def system_metrics() -> dict[str, Any]:
    """JSON snapshot. NOT the Prometheus endpoint — that one is /metrics."""
    snapshot = await MetricsUtils.snapshot_async(disk_paths=["/", "/data"])
    return snapshot.to_dict()
```

!!! warning "Don't mount this at `/metrics`"
    This endpoint is **not** the Prometheus one — mounting it on the same path collides with `make_prometheus_router` when both are active. Use `/system-metrics`, `/admin/sysinfo`, or some restricted oncall prefix.

!!! warning "`MetricsUtils.cpu(interval=...)` blocks the event loop"
    The sync call spends `interval` seconds sampling — the `cpu_async` wrapper avoids the block by running in a thread. Always prefer `MetricsUtils.snapshot_async()` from handlers.

### Individual collectors

```python
snapshot = await MetricsUtils.snapshot_async(disk_paths=["/"])

print(snapshot.cpu.percent, snapshot.memory.percent)
for disk in snapshot.disks:
    print(disk.path, disk.percent)
for gpu in snapshot.gpus:
    print(gpu.name, gpu.utilization_percent, gpu.memory_used_bytes)
```

Individual collectors are also available: `MetricsUtils.cpu(interval=...)`, `MetricsUtils.memory()`, `MetricsUtils.disk(path)`, `MetricsUtils.disks(paths)`, `MetricsUtils.gpus()` — plus their `*_async` variants. Each one returns a typed dataclass (`CPUMetrics`, `MemoryMetrics`, `DiskMetrics`, `GPUMetrics`, `SystemMetrics`) with a `to_dict()` helper for JSON serialization.

## Recap

- **Path #1 (`[prometheus]`)** — `PrometheusMiddleware` + `make_prometheus_router` expose RED/USE series (`http_requests_total`, `http_request_duration_seconds`, `http_requests_in_progress`) on a scrape-ready `GET /metrics`. This is what you turn on in production.
- **Path #2 (`[metrics]`)** — `MetricsUtils` gives an instant CPU / memory / disk / GPU snapshot on a custom endpoint. No Prometheus exporter — it's the on-demand photo of the host.

## Next steps

- [Observability](observability.en.md) — how to combine metrics with request-id, structured logging, and the typed `HTTPClient`.
- [Logging](logging.en.md) — structured logging, per-level files, and the `/logs` endpoint.
