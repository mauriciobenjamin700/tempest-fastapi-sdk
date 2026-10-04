"""Will this machine run that model? Hardware probing + capacity checks.

Loading a model that doesn't fit ends in an OOM crash minutes into the
download. This module answers *before* you commit: it probes the host
(:func:`probe_hardware`), estimates a model's memory footprint from its
parameter count and precision (:func:`estimate_model_bytes`), and reports
whether the two are compatible (:func:`can_run`, :func:`recommend`).

Every dependency is optional and lazily used: ``psutil`` for RAM/CPU,
``torch`` for CUDA/MPS detection, ``pynvml`` (``nvidia-ml-py``, in the
``[metrics]`` extra) for per-GPU name and memory, ``huggingface_hub`` to read
a model's parameter count without downloading its weights. Missing pieces degrade
gracefully (no torch → ``has_cuda=False``), so the module imports without
the ``[genai]`` extra — you only need it installed to probe real GPUs.

RAM is the one reading the planner cannot do without on CPU and MPS. On
Linux (WSL and containers included) it is read from ``/proc/meminfo`` when
``psutil`` is absent, so no extra is needed there. On other platforms without
``psutil`` the probe reports ``ram_measured=False`` and the capacity checks
answer "could not measure" instead of "does not fit"; ``psutil`` ships in the
``[metrics]`` extra, not in ``[genai]``.

Both RAM sources report the host's memory, even inside a container. On Linux
the probe then reads the memory limit of the process's cgroup (v2 or v1) and,
when it is below the host's RAM, reports the limit instead and sets
``ram_cgroup_limited=True``, so ``docker run --memory=512m`` is planned as
512 MiB and not as the host's RAM.
"""

from __future__ import annotations

import contextlib
import os
import shutil
import sys

from tempest_fastapi_sdk.genai.schemas import (
    CapacityReport,
    GPUInfo,
    HardwareInfo,
    ModelDtype,
)

# Bytes per parameter for each precision. int4 is ~0.5 but carries some
# per-block scale overhead, so 0.6 is a safer planning number.
_BYTES_PER_PARAM: dict[ModelDtype, float] = {
    ModelDtype.FLOAT32: 4.0,
    ModelDtype.FLOAT16: 2.0,
    ModelDtype.BFLOAT16: 2.0,
    ModelDtype.INT8: 1.0,
    ModelDtype.INT4: 0.6,
}

_CPU_QUANTIZATION_NOTE: str = (
    " bitsandbytes on CPU decodes slower than float32; a GGUF build served"
    " through OllamaGenerator is the faster CPU path."
)
"""Appended to a CPU quantization suggestion.

Measured on Qwen2.5-3B-Instruct with the GPU hidden (torch 2.14,
bitsandbytes 0.50.2, transformers 4.57.6 and 5.18.0, N=3 each): a
``generate()`` capped at 16 new tokens took ~9 s with bitsandbytes int8 or
int4 against ~3 s at float32. llama.cpp on the Q4_K_M GGUF that Ollama
serves for the same model decoded ~24-27 tokens/s.
"""

# Inference needs more than the weights (activations, KV cache, CUDA
# context). Scale the raw weight size by this to plan with headroom.
_INFERENCE_OVERHEAD: float = 1.25

_PSUTIL_INSTALL_HINT: str = "pip install 'tempest-fastapi-sdk[metrics]'"
"""How to get ``psutil``, quoted in the reports when RAM could not be read.

``[metrics]`` is the smallest extra that brings ``psutil`` (with
``nvidia-ml-py``); ``[genai]`` does not, so a ``[genai]``-only install
probes GPUs but not RAM off Linux.
"""

_PROC_MEMINFO_PATH: str = "/proc/meminfo"
"""Kernel memory report read when ``psutil`` is absent.

Ported from psutil's ``_pslinux.virtual_memory``, which reads the same file
(``psutil`` 7.2.2). Inside a container it shows the host's memory, not the
cgroup limit — the same number ``psutil.virtual_memory()`` reports.
"""

_MEMINFO_TOTAL_KEY: str = "MemTotal:"
"""``/proc/meminfo`` key for total RAM. Ported from psutil's ``_pslinux``."""

_MEMINFO_AVAILABLE_KEY: str = "MemAvailable:"
"""``/proc/meminfo`` key for the kernel's available-RAM estimate.

Ported from psutil's ``_pslinux``. Present since Linux 3.14.
"""

_MEMINFO_FREE_KEY: str = "MemFree:"
"""``/proc/meminfo`` key for strictly free RAM. Ported from psutil's ``_pslinux``."""

_MEMINFO_UNIT_BYTES: int = 1024
"""``/proc/meminfo`` reports ``kB``; psutil's ``_pslinux`` multiplies by 1024."""


def _read_proc_meminfo(path: str) -> tuple[int, int] | None:
    """Read total and available RAM from a ``/proc/meminfo``-format file.

    Follows psutil's ``_pslinux.virtual_memory`` for the two numbers the
    planner needs, with one deliberate difference: when ``MemAvailable`` is
    missing (kernels older than 3.14) or ``0`` (a kernel bug psutil
    documents), psutil estimates it from the zone watermarks and
    ``MemFree + Cached``; this returns ``None`` instead. A coarse
    ``MemFree + Buffers + Cached`` would overstate what a model load can
    claim, and the planner's whole job is not to promise memory that is not
    there, so "not measured" is the honest answer. Like psutil, an available
    figure above the total (distorted values inside an LXC container) falls
    back to ``MemFree``.

    Args:
        path (str): The file to read, normally ``/proc/meminfo``.

    Returns:
        tuple[int, int] | None: ``(total_bytes, available_bytes)``, or
        ``None`` when the file cannot be read or lacks ``MemTotal`` or a
        usable ``MemAvailable``.
    """
    try:
        with open(path, encoding="ascii", errors="replace") as handle:
            lines = handle.read().splitlines()
    except OSError:
        return None
    values: dict[str, int] = {}
    for line in lines:
        fields = line.split()
        if len(fields) >= 2 and fields[1].isdigit():
            values[fields[0]] = int(fields[1]) * _MEMINFO_UNIT_BYTES
    total = values.get(_MEMINFO_TOTAL_KEY)
    available = values.get(_MEMINFO_AVAILABLE_KEY)
    if not total or not available:
        return None
    if available > total:
        available = values.get(_MEMINFO_FREE_KEY, 0)
    return total, available


_MIB: int = 1024 * 1024
"""Bytes per MiB, the unit a cgroup limit is usually set in (``--memory=512m``)."""

_PROC_SELF_CGROUP_PATH: str = "/proc/self/cgroup"
"""The calling process's cgroup membership, one ``id:controllers:path`` per line.

cgroup v2 has a single line ``0::<path>``; cgroup v1 has one line per
hierarchy, and the memory one lists ``memory`` among its controllers. The
path is relative to the cgroup namespace root: inside a container with its
own cgroup namespace (Docker's default on cgroup v2) it is ``/``.
"""

_PROC_SELF_MOUNTINFO_PATH: str = "/proc/self/mountinfo"
"""Mount table used to find where the cgroup hierarchy is mounted.

Each line is ``id parent major:minor root mountpoint options [optional...] -
fstype source superoptions``. The cgroup path from
:data:`_PROC_SELF_CGROUP_PATH` is resolved under ``mountpoint`` after
stripping ``root``: without a cgroup namespace (Docker on cgroup v1) the
container's memory hierarchy is mounted with ``root=/docker/<id>`` while its
cgroup path is that same ``/docker/<id>``.
"""

_MOUNTINFO_SEPARATOR: str = " - "
"""Separates the per-mount fields from ``fstype source superoptions``."""

_CGROUP_V2_FSTYPE: str = "cgroup2"
"""Filesystem type of a cgroup v2 (unified) mount in ``mountinfo``."""

_CGROUP_V1_FSTYPE: str = "cgroup"
"""Filesystem type of a cgroup v1 hierarchy mount in ``mountinfo``."""

_CGROUP_V1_MEMORY_CONTROLLER: str = "memory"
"""Name of the v1 memory controller in ``/proc/self/cgroup`` and in the
mount's super options."""

_CGROUP_V2_MAX_FILE: str = "memory.max"
"""cgroup v2 hard memory limit; above it the cgroup's OOM killer runs."""

_CGROUP_V2_UNLIMITED: str = "max"
"""What ``memory.max`` reads when no limit is set.

Ported from the kernel's ``seq_puts_memcg_tunable`` (``mm/memcontrol.c``),
which prints ``max`` when the counter is ``PAGE_COUNTER_MAX``.
"""

_CGROUP_V2_CURRENT_FILE: str = "memory.current"
"""cgroup v2 memory charged to the cgroup and its descendants, page cache
included."""

_CGROUP_V2_STAT_FILE: str = "memory.stat"
"""cgroup v2 breakdown of the charged memory (recursive over descendants)."""

_CGROUP_V2_FILE_KEY: str = "file"
"""v2 ``memory.stat`` key for page cache, tmpfs and shared memory included.

Ported from the kernel's ``memory_stats`` table (``mm/memcontrol.c``), where
``file`` is ``NR_FILE_PAGES``.
"""

_CGROUP_V2_SHMEM_KEY: str = "shmem"
"""v2 ``memory.stat`` key for tmpfs and shared memory (``NR_SHMEM``): part of
``file``, but not reclaimable without swap."""

_CGROUP_V1_LIMIT_FILE: str = "memory.limit_in_bytes"
"""cgroup v1 hard memory limit of one hierarchy level."""

_CGROUP_V1_USAGE_FILE: str = "memory.usage_in_bytes"
"""cgroup v1 memory charged to one level and its descendants."""

_CGROUP_V1_STAT_FILE: str = "memory.stat"
"""cgroup v1 breakdown; its ``total_*`` keys include descendants."""

_CGROUP_V1_FILE_KEY: str = "total_cache"
"""v1 ``memory.stat`` key for page cache over the subtree.

Ported from the kernel's ``memcg1_stat_names`` (``mm/memcontrol-v1.c``):
``cache`` is ``NR_FILE_PAGES``, the same counter as v2's ``file``, and the
``total_`` prefix is the hierarchical sum that matches
``memory.usage_in_bytes``.
"""

_CGROUP_V1_SHMEM_KEY: str = "total_shmem"
"""v1 ``memory.stat`` key for tmpfs and shared memory over the subtree."""

_CGROUP_V1_UNLIMITED_MIN: int = 2**63 - 2**16
"""Smallest ``memory.limit_in_bytes`` that means "no limit" on 64-bit.

Ported from the kernel: an unset limit is ``PAGE_COUNTER_MAX``, which is
``LONG_MAX / PAGE_SIZE`` on 64-bit (``include/linux/page_counter.h``), and
``memory.limit_in_bytes`` prints it times ``PAGE_SIZE``
(``mm/memcontrol-v1.c``). That is ``2**63 - 4096`` with 4 KiB pages and
``2**63 - 65536`` with 64 KiB pages, so anything at or above this floor is
the sentinel. A limit at or above the host's RAM is ignored anyway, which
also covers the 32-bit sentinel.
"""


def _read_text(path: str) -> str | None:
    """Return the stripped contents of a small text file, or ``None``.

    Args:
        path (str): The file to read.

    Returns:
        str | None: The contents without surrounding whitespace, or ``None``
        when the file cannot be read.
    """
    try:
        with open(path, encoding="ascii", errors="replace") as handle:
            return handle.read().strip()
    except OSError:
        return None


def _read_int(path: str) -> int | None:
    """Read a file holding one non-negative integer.

    Args:
        path (str): The file to read.

    Returns:
        int | None: The integer, or ``None`` when the file is missing or does
        not hold a plain integer (``max`` included).
    """
    text = _read_text(path)
    if text is None or not text.isdigit():
        return None
    return int(text)


def _read_memory_stat(path: str) -> dict[str, int]:
    """Parse a cgroup ``memory.stat`` file into ``{key: bytes}``.

    Args:
        path (str): The ``memory.stat`` file.

    Returns:
        dict[str, int]: Every ``key value`` line with an integer value; empty
        when the file cannot be read.
    """
    text = _read_text(path)
    stats: dict[str, int] = {}
    if text is None:
        return stats
    for line in text.splitlines():
        fields = line.split()
        if len(fields) == 2 and fields[1].isdigit():
            stats[fields[0]] = int(fields[1])
    return stats


def _cgroup_memory_dirs(
    cgroup_path: str, mountinfo_path: str
) -> tuple[bool, list[str]] | None:
    """Locate the memory cgroup directories that bound this process.

    A cgroup v1 memory hierarchy wins over a v2 mount when both exist,
    because on a hybrid host the memory controller lives in v1 and the
    unified mount carries no memory files. The process's cgroup path is
    resolved under the matching mount (see :data:`_PROC_SELF_MOUNTINFO_PATH`)
    and every ancestor up to the mount point is returned, since a parent's
    limit bounds the child even when the child's own limit is unset.

    Args:
        cgroup_path (str): A ``/proc/self/cgroup``-format file.
        mountinfo_path (str): A ``/proc/self/mountinfo``-format file.

    Returns:
        tuple[bool, list[str]] | None: ``(is_v2, directories)`` with the
        directories ordered from the process's own cgroup up to the mount
        point, or ``None`` when either file is unreadable, the process has
        no memory cgroup, or its path is not visible under the mount.
    """
    cgroup_text = _read_text(cgroup_path)
    mountinfo_text = _read_text(mountinfo_path)
    if cgroup_text is None or mountinfo_text is None:
        return None
    v1_path: str | None = None
    v2_path: str | None = None
    for line in cgroup_text.splitlines():
        parts = line.split(":", 2)
        if len(parts) != 3:
            continue
        hierarchy, controllers, path = parts
        if hierarchy == "0" and controllers == "":
            v2_path = path
        elif _CGROUP_V1_MEMORY_CONTROLLER in controllers.split(","):
            v1_path = path
    v1_mount: tuple[str, str] | None = None
    v2_mount: tuple[str, str] | None = None
    for line in mountinfo_text.splitlines():
        head, separator, tail = line.partition(_MOUNTINFO_SEPARATOR)
        head_fields = head.split()
        tail_fields = tail.split()
        if not separator or len(head_fields) < 5 or not tail_fields:
            continue
        root, mountpoint, fstype = head_fields[3], head_fields[4], tail_fields[0]
        super_options = tail_fields[2].split(",") if len(tail_fields) > 2 else []
        if fstype == _CGROUP_V2_FSTYPE and v2_mount is None:
            v2_mount = (root, mountpoint)
        elif (
            fstype == _CGROUP_V1_FSTYPE
            and _CGROUP_V1_MEMORY_CONTROLLER in super_options
            and v1_mount is None
        ):
            v1_mount = (root, mountpoint)
    if v1_path is not None and v1_mount is not None:
        is_v2, path, (root, mountpoint) = False, v1_path, v1_mount
    elif v2_path is not None and v2_mount is not None:
        is_v2, path, (root, mountpoint) = True, v2_path, v2_mount
    else:
        return None
    if root != "/":
        if path != root and not path.startswith(root.rstrip("/") + "/"):
            return None
        path = path[len(root) :]
    relative = [part for part in path.split("/") if part]
    directories = [
        os.path.join(mountpoint, *relative[:depth])
        for depth in range(len(relative), -1, -1)
    ]
    return is_v2, directories


def _cgroup_level_memory(directory: str, *, is_v2: bool) -> tuple[int, int] | None:
    """Read one cgroup level's limit and the memory still available under it.

    Available is ``limit - (usage - reclaimable)``, clamped to
    ``[0, limit]``, where reclaimable is page cache minus shared memory
    (``file - shmem`` on v2, ``total_cache - total_shmem`` on v1). The usage
    files count page cache, which the kernel drops before it OOM-kills, so
    ``limit - usage`` alone undercounts.

    Measured in ``python:3.12-slim`` under ``docker run --memory=512m
    --memory-swap=512m`` (WSL2, kernel 5.15, cgroup v2), allocating in 8 MiB
    steps until the OOM killer: with 300 MiB of page cache, read once (it
    sits in ``inactive_file``) or three times (``active_file``),
    ``limit - usage`` predicted ~183 MiB, this formula ~483 MiB, and the
    process reached 504 MiB either way — so subtracting only
    ``inactive_file`` would have undercounted the second case by ~280 MiB.
    With the 300 MiB written to ``/dev/shm`` instead (``shmem``), the
    process reached 200 MiB, this formula predicted ~191 MiB, and counting
    all of ``file`` would have predicted ~491 MiB. Swap is not counted: a
    model paged out to swap is not a model that runs.

    Args:
        directory (str): The cgroup directory.
        is_v2 (bool): Whether it belongs to a cgroup v2 hierarchy.

    Returns:
        tuple[int, int] | None: ``(limit_bytes, available_bytes)``, or
        ``None`` when this level sets no limit or the limit is unreadable.
        A readable limit with an unreadable usage reports the whole limit as
        available; an unreadable ``memory.stat`` counts nothing as
        reclaimable.
    """
    if is_v2:
        raw_limit = _read_text(os.path.join(directory, _CGROUP_V2_MAX_FILE))
        if raw_limit == _CGROUP_V2_UNLIMITED:
            return None
        limit = _read_int(os.path.join(directory, _CGROUP_V2_MAX_FILE))
        usage = _read_int(os.path.join(directory, _CGROUP_V2_CURRENT_FILE))
        stats = _read_memory_stat(os.path.join(directory, _CGROUP_V2_STAT_FILE))
        file_key, shmem_key = _CGROUP_V2_FILE_KEY, _CGROUP_V2_SHMEM_KEY
    else:
        limit = _read_int(os.path.join(directory, _CGROUP_V1_LIMIT_FILE))
        if limit is not None and limit >= _CGROUP_V1_UNLIMITED_MIN:
            return None
        usage = _read_int(os.path.join(directory, _CGROUP_V1_USAGE_FILE))
        stats = _read_memory_stat(os.path.join(directory, _CGROUP_V1_STAT_FILE))
        file_key, shmem_key = _CGROUP_V1_FILE_KEY, _CGROUP_V1_SHMEM_KEY
    if limit is None:
        return None
    if usage is None:
        return limit, limit
    reclaimable = max(0, stats.get(file_key, 0) - stats.get(shmem_key, 0))
    return limit, min(max(0, limit - usage + reclaimable), limit)


def _read_cgroup_memory(
    cgroup_path: str, mountinfo_path: str
) -> tuple[int, int] | None:
    """Read the tightest memory limit over this process's cgroup ancestry.

    Args:
        cgroup_path (str): A ``/proc/self/cgroup``-format file.
        mountinfo_path (str): A ``/proc/self/mountinfo``-format file.

    Returns:
        tuple[int, int] | None: ``(limit_bytes, available_bytes)``, each the
        smallest over the levels that set a limit, or ``None`` when no level
        sets one (``max`` or the v1 sentinel everywhere) or the cgroup
        cannot be located.
    """
    located = _cgroup_memory_dirs(cgroup_path, mountinfo_path)
    if located is None:
        return None
    is_v2, directories = located
    readings = [
        reading
        for directory in directories
        if (reading := _cgroup_level_memory(directory, is_v2=is_v2)) is not None
    ]
    if not readings:
        return None
    return min(limit for limit, _ in readings), min(free for _, free in readings)


def _clamp_to_cgroup(total: int, available: int) -> tuple[int, int, bool]:
    """Bound a host RAM reading by this process's cgroup memory limit.

    The cgroup applies only when its limit is below the host's total: a
    limit at or above the host's RAM cannot bind first, and the host's own
    numbers stand.

    Args:
        total (int): Host RAM total in bytes.
        available (int): Host RAM available in bytes.

    Returns:
        tuple[int, int, bool]: ``(total, available, cgroup_limited)``. When
        limited, ``total`` is the cgroup limit and ``available`` the smaller
        of the host's and the cgroup's available bytes.
    """
    cgroup = _read_cgroup_memory(_PROC_SELF_CGROUP_PATH, _PROC_SELF_MOUNTINFO_PATH)
    if cgroup is None or cgroup[0] >= total:
        return total, available, False
    limit, cgroup_available = cgroup
    return limit, min(available, cgroup_available), True


def _is_linux() -> bool:
    """Return whether this process runs on Linux (WSL and containers too).

    Reads ``sys.platform`` through a plain ``str`` so the check stays a
    runtime decision: mypy treats ``sys.platform.startswith`` as a platform
    guard and would mark the other branch unreachable, and tests patch
    ``sys.platform`` to exercise macOS and Windows.

    Returns:
        bool: ``True`` when ``sys.platform`` starts with ``"linux"``.
    """
    platform: str = sys.platform
    return platform.startswith("linux")


def _read_ram() -> tuple[int, int] | None:
    """Measure total and available RAM: ``psutil`` first, then ``/proc``.

    Returns:
        tuple[int, int] | None: ``(total_bytes, available_bytes)``, or
        ``None`` when neither ``psutil`` nor (on Linux) ``/proc/meminfo``
        could answer.
    """
    try:
        import psutil

        mem = psutil.virtual_memory()
        return int(mem.total), int(mem.available)
    except Exception:
        pass
    if _is_linux():
        return _read_proc_meminfo(_PROC_MEMINFO_PATH)
    return None


def _ram_unmeasured_reason() -> str:
    """Explain, for this platform, why RAM was not measured and how to fix it.

    Returns:
        str: On Linux, that both ``psutil`` and ``/proc/meminfo`` failed; on
        any other platform, that measuring RAM there requires ``psutil``.
        Either way it ends with the install command.
    """
    if _is_linux():
        return (
            f"psutil is missing or failed and {_PROC_MEMINFO_PATH} could not "
            f"be read; install psutil ({_PSUTIL_INSTALL_HINT})"
        )
    return f"on {sys.platform} measuring RAM requires psutil ({_PSUTIL_INSTALL_HINT})"


def bytes_per_param(dtype: ModelDtype) -> float:
    """Return the planning bytes-per-parameter for ``dtype``.

    Args:
        dtype (ModelDtype): The weight precision.

    Returns:
        float: Bytes each parameter occupies at that precision.
    """
    return _BYTES_PER_PARAM[dtype]


def estimate_model_bytes(
    num_params: int,
    dtype: ModelDtype = ModelDtype.BFLOAT16,
    *,
    overhead: float = _INFERENCE_OVERHEAD,
) -> int:
    """Estimate the memory a model needs to run.

    Args:
        num_params (int): The model's parameter count (e.g. ``7_000_000_000``
            for a 7B model).
        dtype (ModelDtype): The precision it will be loaded in.
        overhead (float): Multiplier over raw weight size to account for
            activations / KV cache / runtime context. Defaults to ``1.25``.

    Returns:
        int: Estimated bytes required at inference time.

    Raises:
        ValueError: When ``num_params`` is not positive.
    """
    if num_params <= 0:
        raise ValueError("num_params must be positive")
    return int(num_params * bytes_per_param(dtype) * overhead)


def _nvml_gpus(expected_count: int) -> list[GPUInfo] | None:
    """Read per-GPU name and memory through NVML, without touching CUDA.

    ``torch.cuda.mem_get_info`` and ``torch.cuda.get_device_name`` both
    initialize the CUDA runtime, which creates a context on the device —
    measured at +209 MiB of VRAM on an RTX 4070 Ti SUPER (driver 591.86,
    torch 2.14.0+cu130, WSL2) for a process that only wanted to *ask*.
    NVML answers the same two questions from the driver with no context.

    NVML numbers devices in PCI order while CUDA's default is fastest-first
    and ``CUDA_VISIBLE_DEVICES`` can hide or reorder them, so the NVML answer
    is only used when it cannot disagree with CUDA's indices: no
    ``CUDA_VISIBLE_DEVICES`` set and the same device count.

    Args:
        expected_count (int): ``torch.cuda.device_count()``.

    Returns:
        list[GPUInfo] | None: One entry per GPU, or ``None`` when NVML is
        unavailable or its view might not match CUDA's indices.
    """
    if os.environ.get("CUDA_VISIBLE_DEVICES") is not None:
        return None
    try:
        import pynvml
    except ImportError:
        return None
    try:
        pynvml.nvmlInit()
    except Exception:
        return None
    try:
        if int(pynvml.nvmlDeviceGetCount()) != expected_count:
            return None
        gpus: list[GPUInfo] = []
        for index in range(expected_count):
            handle = pynvml.nvmlDeviceGetHandleByIndex(index)
            raw_name = pynvml.nvmlDeviceGetName(handle)
            memory = pynvml.nvmlDeviceGetMemoryInfo(handle)
            gpus.append(
                GPUInfo(
                    index=index,
                    name=raw_name.decode() if isinstance(raw_name, bytes) else raw_name,
                    vram_total_bytes=int(memory.total),
                    vram_free_bytes=int(memory.free),
                ),
            )
        return gpus
    except Exception:
        return None
    finally:
        with contextlib.suppress(Exception):
            pynvml.nvmlShutdown()


def probe_hardware(*, cache_dir: str | None = None) -> HardwareInfo:
    """Snapshot the host's CPU, RAM, GPU and disk.

    GPU name and memory come from NVML when ``pynvml`` is installed, so a
    web process that probes (the ``/models`` endpoint, a health check) does
    not create a CUDA context on every GPU. Without NVML it falls back to
    ``torch.cuda.mem_get_info``, which does initialize CUDA in the calling
    process. ``has_cuda`` always comes from ``torch.cuda.is_available()``,
    which does not create a context.

    Args:
        cache_dir (str | None): Directory whose free space to report
            (where models are downloaded). Defaults to the current working
            directory when ``None``.

    Returns:
        HardwareInfo: The current resource picture. A reading that could
        not be taken is reported as unmeasured, not as empty. RAM comes
        from ``psutil`` and, failing that on Linux, from ``/proc/meminfo``;
        when neither answers ``ram_measured`` is ``False`` and the RAM
        fields are ``0``. Both sources report the host's memory; on Linux
        the reading is then bounded by the process's cgroup memory limit
        (v2 ``memory.max`` or v1 ``memory.limit_in_bytes``, over the whole
        ancestry) when that limit is below the host's RAM, and
        ``ram_cgroup_limited`` says so.
        When the disk query raises ``OSError``, ``disk_measured`` is
        ``False``. Without ``torch`` no GPU is reported
        (``has_cuda=False``, ``gpus=[]``).
    """
    cpu_cores = os.cpu_count() or 1
    ram = _read_ram()
    ram_total, ram_available = ram if ram is not None else (0, 0)
    ram_cgroup_limited = False
    if ram is not None and _is_linux():
        ram_total, ram_available, ram_cgroup_limited = _clamp_to_cgroup(
            ram_total, ram_available
        )

    has_cuda = False
    gpus: list[GPUInfo] = []
    has_mps = False
    try:
        import torch

        has_cuda = bool(torch.cuda.is_available())
        if has_cuda:
            count = int(torch.cuda.device_count())
            nvml = _nvml_gpus(count)
            if nvml is not None:
                gpus = nvml
            else:
                for index in range(count):
                    free, total = torch.cuda.mem_get_info(index)
                    gpus.append(
                        GPUInfo(
                            index=index,
                            name=torch.cuda.get_device_name(index),
                            vram_total_bytes=int(total),
                            vram_free_bytes=int(free),
                        ),
                    )
        has_mps = bool(
            getattr(torch.backends, "mps", None) and torch.backends.mps.is_available(),
        )
    except ImportError:
        pass

    disk_free = 0
    disk_measured = False
    with contextlib.suppress(OSError):
        disk_free = int(shutil.disk_usage(cache_dir or os.getcwd()).free)
        disk_measured = True

    return HardwareInfo(
        cpu_cores=cpu_cores,
        ram_total_bytes=ram_total,
        ram_available_bytes=ram_available,
        ram_measured=ram is not None,
        ram_cgroup_limited=ram_cgroup_limited,
        has_cuda=has_cuda,
        gpus=gpus,
        has_mps=has_mps,
        disk_free_bytes=disk_free,
        disk_measured=disk_measured,
    )


def fetch_num_params(model_id: str, *, token: str | None = None) -> int | None:
    """Read a model's parameter count from the Hub, without downloading it.

    Uses ``huggingface_hub`` safetensors metadata when available.

    Args:
        model_id (str): The Hub model id (e.g. ``"Qwen/Qwen2.5-7B"``).
        token (str | None): Optional Hub token for gated/private models.

    Returns:
        int | None: The total parameter count, or ``None`` when it can't
        be determined (no ``huggingface_hub``, offline, or the model
        exposes no safetensors metadata).
    """
    try:
        from huggingface_hub import HfApi
    except ImportError:
        return None
    try:
        info = HfApi().model_info(model_id, token=token)
    except Exception:
        return None
    safetensors = getattr(info, "safetensors", None)
    if safetensors is not None and getattr(safetensors, "total", None):
        return int(safetensors.total)
    return None


def _device_capacity(hardware: HardwareInfo, device: str) -> int | None:
    """Return the available bytes on ``device`` for ``hardware``.

    Both ``mps`` and ``cpu`` report system RAM: Apple's unified memory means
    the GPU shares it rather than having a pool of its own.

    Args:
        hardware (HardwareInfo): The probed hardware.
        device (str): The device to size.

    Returns:
        int | None: Available bytes on that device, or ``None`` when the
        device is sized from system RAM and RAM was not measured
        (``hardware.ram_measured`` is ``False``). ``None`` is "unknown",
        never "zero free".
    """
    if device == "cuda" and hardware.gpus:
        return max(gpu.vram_free_bytes for gpu in hardware.gpus)
    if not hardware.ram_measured:
        return None
    return hardware.ram_available_bytes


def _pick_device(hardware: HardwareInfo) -> str:
    """Pick the best available device for ``hardware``."""
    if hardware.has_cuda and hardware.gpus:
        return "cuda"
    if hardware.has_mps:
        return "mps"
    return "cpu"


def _native_dtype(device: str) -> ModelDtype:
    """Return the precision an unquantized model is loaded in on ``device``.

    This is the single source of truth shared with
    :func:`~tempest_fastapi_sdk.genai.auto_dtype_name`, which is what
    ``TextGenerator(dtype="auto")`` loads with, so the planner sizes the
    weights the generator will actually hold. CPU has no fast
    half-precision path, so it loads ``float32`` — twice the bytes of the
    ``bfloat16`` used on CUDA and MPS.

    Args:
        device (str): The concrete device (``"cuda"``, ``"mps"`` or
            ``"cpu"``).

    Returns:
        ModelDtype: ``FLOAT32`` on CPU, ``BFLOAT16`` elsewhere.
    """
    return ModelDtype.FLOAT32 if device == "cpu" else ModelDtype.BFLOAT16


def can_run(
    *,
    num_params: int | None = None,
    model_id: str | None = None,
    dtype: ModelDtype | None = None,
    device: str = "auto",
    hardware: HardwareInfo | None = None,
    token: str | None = None,
) -> CapacityReport:
    """Report whether the host can run a model, and what to do if not.

    Provide the model size either directly (``num_params``) or by
    ``model_id`` (looked up on the Hub). ``device="auto"`` picks CUDA →
    MPS → CPU.

    Args:
        num_params (int | None): The model's parameter count. Takes
            precedence over ``model_id``.
        model_id (str | None): Hub id to look the parameter count up from
            when ``num_params`` is not given.
        dtype (ModelDtype | None): The precision to plan for. ``None`` (the
            default) plans for the precision ``TextGenerator(dtype="auto")``
            loads on the chosen device: ``bfloat16`` on CUDA/MPS,
            ``float32`` on CPU. Pass a value to plan for an explicit
            ``TextGenerator(dtype=...)`` or ``quantization=...``.
        device (str): ``"auto"``, ``"cuda"``, ``"mps"`` or ``"cpu"``.
        hardware (HardwareInfo | None): Inject a snapshot (tests, or to
            reuse one probe); defaults to a fresh :func:`probe_hardware`.
        token (str | None): Hub token for the ``model_id`` lookup.

    Returns:
        CapacityReport: The verdict, chosen device, estimate vs available,
        headroom and a suggestion when it doesn't fit. When the free memory
        of the device could not be measured (CPU or MPS with no RAM
        reading), ``memory_measured`` is ``False``, ``fits`` is ``False``
        because nothing was verified, and the suggestion says how to get a
        reading on this platform — not to shrink the model. When the RAM
        that sizes ``device`` was bounded by a cgroup limit
        (``hardware.ram_cgroup_limited``), ``reason`` ends by naming the
        limit in MiB.

    Raises:
        ValueError: When neither ``num_params`` nor a resolvable
            ``model_id`` is available.
    """
    hw = hardware or probe_hardware()
    params = num_params
    if params is None and model_id is not None:
        params = fetch_num_params(model_id, token=token)
    if params is None:
        raise ValueError(
            "Provide num_params, or a model_id whose parameter count can be "
            "read from the Hub (huggingface_hub installed + reachable).",
        )

    chosen = _pick_device(hw) if device == "auto" else device
    planned = dtype if dtype is not None else _native_dtype(chosen)
    estimated = estimate_model_bytes(params, planned)
    available = _device_capacity(hw, chosen)
    if available is None:
        return CapacityReport(
            fits=False,
            device=chosen,
            dtype=planned,
            estimated_bytes=estimated,
            available_bytes=0,
            headroom_pct=0.0,
            reason=(
                f"~{estimated / 1e9:.1f} GB needed at {planned.value}; free "
                f"RAM on {chosen} could not be measured, so the fit is "
                "unknown, not negative."
            ),
            suggestion=(
                f"Free RAM was not measured: {_ram_unmeasured_reason()}. "
                "Then check again."
            ),
            memory_measured=False,
        )
    fits = estimated <= available
    headroom = ((available - estimated) / available * 100) if available else -100.0

    if fits:
        reason = (
            f"~{estimated / 1e9:.1f} GB needed at {planned.value} fits the "
            f"~{available / 1e9:.1f} GB free on {chosen}."
        )
        suggestion = None
    else:
        reason = (
            f"~{estimated / 1e9:.1f} GB needed at {planned.value} exceeds the "
            f"~{available / 1e9:.1f} GB free on {chosen}."
        )
        suggestion = _suggest(hw, params, planned, chosen)
    if hw.ram_cgroup_limited and not (chosen == "cuda" and hw.gpus):
        reason += (
            f" RAM is limited by the container's cgroup to "
            f"{hw.ram_total_bytes / _MIB:.0f} MiB."
        )

    return CapacityReport(
        fits=fits,
        device=chosen,
        dtype=planned,
        estimated_bytes=estimated,
        available_bytes=available,
        headroom_pct=round(headroom, 1),
        reason=reason,
        suggestion=suggestion,
    )


def _suggest(
    hardware: HardwareInfo,
    num_params: int,
    dtype: ModelDtype,
    device: str,
) -> str:
    """Return the best next step when a model doesn't fit as asked.

    The ladder starts at the device's native precision (see
    :func:`_native_dtype`) and only goes down, so on CPU — which loads
    ``float32`` — the first rung below is ``int8``, never ``bfloat16``:
    ``bfloat16`` is not a quantization, and ``TextGenerator`` rejects it
    as ``quantization=``.

    Args:
        hardware (HardwareInfo): The probed hardware.
        num_params (int): The model's parameter count.
        dtype (ModelDtype): The precision that did not fit.
        device (str): The device it did not fit on.

    Returns:
        str: The suggested next step.
    """
    order = [_native_dtype(device), ModelDtype.INT8, ModelDtype.INT4]
    available = _device_capacity(hardware, device) or 0
    for candidate in order:
        if bytes_per_param(candidate) >= bytes_per_param(dtype):
            continue
        if estimate_model_bytes(num_params, candidate) <= available:
            suggestion = (
                f"Quantize to {candidate.value} (needs "
                f"~{estimate_model_bytes(num_params, candidate) / 1e9:.1f} GB) "
                f"to fit {device}."
            )
            if device == "cpu" and candidate in (ModelDtype.INT8, ModelDtype.INT4):
                suggestion += _CPU_QUANTIZATION_NOTE
            return suggestion
    if device == "cuda" and not hardware.ram_measured:
        return (
            "Model does not fit this GPU even at int4, and a CPU offload is "
            f"unchecked because free RAM was not measured: "
            f"{_ram_unmeasured_reason()}. Or use a smaller model."
        )
    if (
        device == "cuda"
        and estimate_model_bytes(num_params, ModelDtype.INT4)
        <= hardware.ram_available_bytes
    ):
        return "Offload to CPU (device='cpu') with int4 — slower but fits RAM."
    return (
        "Model is too large for this host even quantized; use a smaller "
        "model or add memory."
    )


def recommend(
    *,
    num_params: int | None = None,
    model_id: str | None = None,
    hardware: HardwareInfo | None = None,
    token: str | None = None,
) -> CapacityReport:
    """Pick the best precision that fits, from the native one down to int4.

    Tries the precision ``TextGenerator(dtype="auto")`` loads on the
    auto-selected device (``bfloat16`` on CUDA/MPS, ``float32`` on CPU),
    then ``int8``, then ``int4``, and returns the first
    :class:`CapacityReport` that fits (or the int4 report when nothing
    fits, so the caller sees the closest option). A report whose ``dtype``
    is ``int8``/``int4`` maps to ``TextGenerator(quantization=...)``; any
    other ``dtype`` is what ``dtype="auto"`` already loads.

    Args:
        num_params (int | None): The model's parameter count.
        model_id (str | None): Hub id to look the count up from.
        hardware (HardwareInfo | None): Injected snapshot; defaults to a
            fresh probe.
        token (str | None): Hub token for the lookup.

    Returns:
        CapacityReport: The recommended configuration. When the free memory
        of the device could not be measured (``memory_measured=False``), it
        is the report at the native precision: the ladder needs a number to
        step down from, and stepping to ``int4`` on an unknown would
        recommend a slower, lossier load for nothing.

    Raises:
        ValueError: When neither ``num_params`` nor a resolvable
            ``model_id`` is available.

    Notes:
        Two fallbacks are tried in order: a smaller precision on the same
        device first, since that keeps the model on the accelerator, and
        only then CPU RAM when the current device is a GPU.
    """
    hw = hardware or probe_hardware()
    params = num_params
    if params is None and model_id is not None:
        params = fetch_num_params(model_id, token=token)
    native = _native_dtype(_pick_device(hw))
    report = None
    for dtype in (native, ModelDtype.INT8, ModelDtype.INT4):
        report = can_run(num_params=params, dtype=dtype, hardware=hw)
        if report.fits or not report.memory_measured:
            return report
    assert report is not None
    return report


__all__: list[str] = [
    "bytes_per_param",
    "can_run",
    "estimate_model_bytes",
    "fetch_num_params",
    "probe_hardware",
    "recommend",
]
