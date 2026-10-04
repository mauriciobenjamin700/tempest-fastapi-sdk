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
        fields are ``0``. Both sources read the same file on Linux, so in a
        container they report the host's memory, not the cgroup limit.
        When the disk query raises ``OSError``, ``disk_measured`` is
        ``False``. Without ``torch`` no GPU is reported
        (``has_cuda=False``, ``gpus=[]``).
    """
    cpu_cores = os.cpu_count() or 1
    ram = _read_ram()
    ram_total, ram_available = ram if ram is not None else (0, 0)

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
        reading on this platform — not to shrink the model.

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
