# Métricas {#metrics}


`MetricsUtils` coleta uso de CPU, memória, disco e GPU NVIDIA via `psutil` + `pynvml`. Todo método tem uma variante sync e uma async (o wrapper async executa o mesmo código via `asyncio.to_thread`). A amostragem de GPU degrada de forma elegante para `[]` quando `pynvml` ou os drivers NVIDIA estão ausentes.

Instale com `[metrics]`.

```python
from tempest_fastapi_sdk import MetricsUtils

# Synchronous, blocking call
snapshot = MetricsUtils.snapshot(disk_paths=["/", "/data"], cpu_interval=0.1)
print(snapshot.cpu.percent, snapshot.memory.percent)
for disk in snapshot.disks:
    print(disk.path, disk.percent)
for gpu in snapshot.gpus:
    print(gpu.name, gpu.utilization_percent, gpu.memory_used_bytes)

# Async — runs every collector concurrently via asyncio.gather
snapshot = await MetricsUtils.snapshot_async(disk_paths=["/"])


@router.get("/metrics")
async def metrics() -> dict[str, Any]:
    snap = await MetricsUtils.snapshot_async()
    return snap.to_dict()
```

Coletores individuais também estão disponíveis: `MetricsUtils.cpu(interval=...)`, `MetricsUtils.memory()`, `MetricsUtils.disk(path)`, `MetricsUtils.disks(paths)`, `MetricsUtils.gpus()` — e suas variantes `*_async`. Cada um retorna uma dataclass tipada (`CPUMetrics`, `MemoryMetrics`, `DiskMetrics`, `GPUMetrics`, `SystemMetrics`) com um helper `to_dict()` para serialização em JSON.
