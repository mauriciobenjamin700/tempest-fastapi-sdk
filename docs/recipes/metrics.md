# Métricas

O SDK oferece **dois caminhos complementares** de métricas:

1. **Prometheus de RED/USE para HTTP** (`PrometheusMiddleware` + `make_prometheus_router`) — escuta cada request, calcula `http_requests_total` + histograma de latência + `http_requests_in_progress`, expõe tudo num `GET /metrics` no formato texto do Prometheus pronto pra ser raspado pelo seu Prometheus / Grafana / Datadog.
2. **Snapshots de sistema sob demanda** (`MetricsUtils`) — coleta CPU / memória / disco / GPU NVIDIA pra um endpoint custom (debug page interna, /oncall, etc.). Sem export Prometheus integrado — o objetivo é dar a foto instantânea.

Use o **#1** em produção sempre. Adicione o **#2** quando precisar inspecionar o host onde a app roda.

## #1 Prometheus HTTP — extra `[prometheus]`

Instale com `[prometheus]` (puxa `prometheus-client`). O middleware mede toda request; o router serve o endpoint de scrape.

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

    # Registry isolado por app — evita colisões com outros prometheus-client globais.
    registry = make_prometheus_registry()

    app.add_middleware(PrometheusMiddleware, registry=registry)
    app.include_router(make_prometheus_router(registry=registry))
    return app
```

Pronto. `GET /metrics` agora devolve algo como:

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

Buckets padrão (`DEFAULT_LATENCY_BUCKETS`) cobrem 5ms → 30s — adequado pra APIs típicas. Sobrescreva com `PrometheusMiddleware(registry=..., latency_buckets=(0.001, 0.005, 0.025, 0.1, 0.5, 2, 10))` quando seu workload é mais granular.

!!! tip "Path normalization"
    O `path` label usa o template da rota (`/api/users/{user_id}`), não o path concreto, pra não explodir a cardinalidade com UUIDs únicos. Isso vem do FastAPI/Starlette — você não precisa configurar nada.

### Scrape config

`prometheus.yml`:

```yaml
scrape_configs:
  - job_name: my-service
    metrics_path: /metrics
    static_configs:
      - targets: ["my-service:8000"]
```

Ou em compose:

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

### Métricas de negócio — `BusinessMetrics`

O middleware cobre HTTP (RED). Pras métricas do **seu domínio** — pedidos,
profundidade de fila, duração de job — use `BusinessMetrics`: uma fábrica
tipada de `Counter` / `Gauge` / `Histogram` ligada ao **mesmo** registry,
então tudo sai no mesmo `GET /metrics`.

```python
from tempest_fastapi_sdk import BusinessMetrics, make_prometheus_registry

registry = make_prometheus_registry()
metrics = BusinessMetrics(registry, namespace="shop")

orders = metrics.counter("orders_total", "Pedidos criados", labelnames=["status"])
queue = metrics.gauge("queue_depth", "Itens na fila")
job = metrics.histogram("job_seconds", "Duração do job", buckets=[0.1, 1, 10])

orders.labels(status="paid").inc()
queue.set(await repo.count({"status": "pending"}))
job.observe(elapsed)
```

Sai no `/metrics` como `shop_orders_total{status="paid"}`, `shop_queue_depth`,
`shop_job_seconds_bucket`. Criar duas vezes com o mesmo nome devolve a
**mesma** métrica (sem `Duplicated timeseries` em reload/testes).

!!! note "Sem mágica"
    Os objetos retornados são os `Counter`/`Gauge`/`Histogram` de verdade do
    `prometheus_client` — `.inc()` / `.set()` / `.observe()` / `.labels(...)`
    funcionam como na doc oficial. O `BusinessMetrics` só amarra ao registry
    e ao namespace, sem esconder nada.

## #2 Snapshot de sistema — extra `[metrics]`

`MetricsUtils` coleta uso de CPU, memória, disco e GPU NVIDIA via `psutil` + `pynvml`. Todo método tem variante sync e async (o wrapper async roda o mesmo código via `asyncio.to_thread`). A amostragem de GPU degrada graciosamente para `[]` quando `pynvml` ou os drivers da NVIDIA estão ausentes.

Instale com `[metrics]`.

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

!!! warning "Não monte no path `/metrics`"
    Esse endpoint **não** é o do Prometheus — montar no mesmo path conflita com o `make_prometheus_router` quando os dois estão ativos. Use `/system-metrics`, `/admin/sysinfo`, ou algum prefixo restrito de oncall.

!!! warning "`MetricsUtils.cpu(interval=...)` bloqueia o event loop"
    A chamada sync gasta `interval` segundos amostrando — o wrapper `cpu_async` evita o bloqueio rodando em thread. Sempre prefira `MetricsUtils.snapshot_async()` em handlers.

### Coletores individuais

```python
snapshot = await MetricsUtils.snapshot_async(disk_paths=["/"])

print(snapshot.cpu.percent, snapshot.memory.percent)
for disk in snapshot.disks:
    print(disk.path, disk.percent)
for gpu in snapshot.gpus:
    print(gpu.name, gpu.utilization_percent, gpu.memory_used_bytes)
```

Os coletores individuais também estão disponíveis: `MetricsUtils.cpu(interval=...)`, `MetricsUtils.memory()`, `MetricsUtils.disk(path)`, `MetricsUtils.disks(paths)`, `MetricsUtils.gpus()` — e suas variantes `*_async`. Cada um retorna uma dataclass tipada (`CPUMetrics`, `MemoryMetrics`, `DiskMetrics`, `GPUMetrics`, `SystemMetrics`) com um helper `to_dict()` para serialização JSON.

## Recap

- **Caminho #1 (`[prometheus]`)** — `PrometheusMiddleware` + `make_prometheus_router` expõem séries RED/USE (`http_requests_total`, `http_request_duration_seconds`, `http_requests_in_progress`) num `GET /metrics` pronto pra scrape. É o que você liga em produção.
- **Caminho #2 (`[metrics]`)** — `MetricsUtils` entrega um snapshot instantâneo de CPU / memória / disco / GPU num endpoint custom. Sem exporter Prometheus — é a foto sob demanda do host.

## Próximos passos

- [Observabilidade](observability.md) — como combinar métricas com request-id, logging estruturado e o `HTTPClient` tipado.
- [Logging](logging.md) — logging estruturado, arquivos por nível e o endpoint `/logs`.
