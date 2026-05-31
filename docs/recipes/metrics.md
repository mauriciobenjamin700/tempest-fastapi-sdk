# Metrics


`MetricsUtils` collects CPU, memory, disk and NVIDIA GPU usage via `psutil` + `pynvml`. Every method has a sync and an async variant (the async wrapper runs the same code via `asyncio.to_thread`). GPU sampling gracefully degrades to `[]` when `pynvml` or NVIDIA drivers are missing.

Install with `[metrics]`.

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

Individual collectors are also available: `MetricsUtils.cpu(interval=...)`, `MetricsUtils.memory()`, `MetricsUtils.disk(path)`, `MetricsUtils.disks(paths)`, `MetricsUtils.gpus()` — and their `*_async` variants. Each returns a typed dataclass (`CPUMetrics`, `MemoryMetrics`, `DiskMetrics`, `GPUMetrics`, `SystemMetrics`) with a `to_dict()` helper for JSON serialization.

