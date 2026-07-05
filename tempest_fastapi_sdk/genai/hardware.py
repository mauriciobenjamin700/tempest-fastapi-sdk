"""Will this machine run that model? Hardware probing + capacity checks.

Loading a model that doesn't fit ends in an OOM crash minutes into the
download. This module answers *before* you commit: it probes the host
(:func:`probe_hardware`), estimates a model's memory footprint from its
parameter count and precision (:func:`estimate_model_bytes`), and reports
whether the two are compatible (:func:`can_run`, :func:`recommend`).

Every dependency is optional and lazily used: ``psutil`` for RAM/CPU,
``torch`` for CUDA/MPS detection, ``huggingface_hub`` to read a model's
parameter count without downloading its weights. Missing pieces degrade
gracefully (no torch → ``has_cuda=False``), so the module imports without
the ``[genai]`` extra — you only need it installed to probe real GPUs.
"""

from __future__ import annotations

import contextlib
import os
import shutil

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

# Inference needs more than the weights (activations, KV cache, CUDA
# context). Scale the raw weight size by this to plan with headroom.
_INFERENCE_OVERHEAD: float = 1.25


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


def probe_hardware(*, cache_dir: str | None = None) -> HardwareInfo:
    """Snapshot the host's CPU, RAM, GPU and disk.

    Args:
        cache_dir (str | None): Directory whose free space to report
            (where models are downloaded). Defaults to the current working
            directory when ``None``.

    Returns:
        HardwareInfo: The current resource picture. Fields that need an
        absent optional dependency fall back to safe defaults (``0`` /
        ``False`` / empty).
    """
    cpu_cores = os.cpu_count() or 1
    ram_total = 0
    ram_available = 0
    try:
        import psutil

        mem = psutil.virtual_memory()
        ram_total = int(mem.total)
        ram_available = int(mem.available)
    except ImportError:
        pass

    has_cuda = False
    gpus: list[GPUInfo] = []
    has_mps = False
    try:
        import torch

        has_cuda = bool(torch.cuda.is_available())
        if has_cuda:
            for index in range(torch.cuda.device_count()):
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
    with contextlib.suppress(OSError):
        disk_free = int(shutil.disk_usage(cache_dir or os.getcwd()).free)

    return HardwareInfo(
        cpu_cores=cpu_cores,
        ram_total_bytes=ram_total,
        ram_available_bytes=ram_available,
        has_cuda=has_cuda,
        gpus=gpus,
        has_mps=has_mps,
        disk_free_bytes=disk_free,
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


def _device_capacity(hardware: HardwareInfo, device: str) -> int:
    """Return the available bytes on ``device`` for ``hardware``."""
    if device == "cuda" and hardware.gpus:
        return max(gpu.vram_free_bytes for gpu in hardware.gpus)
    # MPS shares system RAM; CPU uses system RAM.
    return hardware.ram_available_bytes


def _pick_device(hardware: HardwareInfo) -> str:
    """Pick the best available device for ``hardware``."""
    if hardware.has_cuda and hardware.gpus:
        return "cuda"
    if hardware.has_mps:
        return "mps"
    return "cpu"


def can_run(
    *,
    num_params: int | None = None,
    model_id: str | None = None,
    dtype: ModelDtype = ModelDtype.BFLOAT16,
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
        dtype (ModelDtype): The precision to plan for.
        device (str): ``"auto"``, ``"cuda"``, ``"mps"`` or ``"cpu"``.
        hardware (HardwareInfo | None): Inject a snapshot (tests, or to
            reuse one probe); defaults to a fresh :func:`probe_hardware`.
        token (str | None): Hub token for the ``model_id`` lookup.

    Returns:
        CapacityReport: The verdict, chosen device, estimate vs available,
        headroom and a suggestion when it doesn't fit.

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
    estimated = estimate_model_bytes(params, dtype)
    available = _device_capacity(hw, chosen)
    fits = estimated <= available
    headroom = ((available - estimated) / available * 100) if available else -100.0

    if fits:
        reason = (
            f"~{estimated / 1e9:.1f} GB needed at {dtype.value} fits the "
            f"~{available / 1e9:.1f} GB free on {chosen}."
        )
        suggestion = None
    else:
        reason = (
            f"~{estimated / 1e9:.1f} GB needed at {dtype.value} exceeds the "
            f"~{available / 1e9:.1f} GB free on {chosen}."
        )
        suggestion = _suggest(hw, params, dtype, chosen)

    return CapacityReport(
        fits=fits,
        device=chosen,
        dtype=dtype,
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
    """Return the best next step when a model doesn't fit as asked."""
    # Try a smaller precision on the same device.
    order = [ModelDtype.BFLOAT16, ModelDtype.INT8, ModelDtype.INT4]
    available = _device_capacity(hardware, device)
    for candidate in order:
        if bytes_per_param(candidate) >= bytes_per_param(dtype):
            continue
        if estimate_model_bytes(num_params, candidate) <= available:
            return (
                f"Quantize to {candidate.value} (needs "
                f"~{estimate_model_bytes(num_params, candidate) / 1e9:.1f} GB) "
                f"to fit {device}."
            )
    # Fall back to CPU RAM if we were on GPU.
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
    """Pick the best precision that fits, from bf16 down to int4.

    Tries ``bfloat16`` → ``int8`` → ``int4`` on the auto-selected device
    and returns the first :class:`CapacityReport` that fits (or the int4
    report when nothing fits, so the caller sees the closest option).

    Args:
        num_params (int | None): The model's parameter count.
        model_id (str | None): Hub id to look the count up from.
        hardware (HardwareInfo | None): Injected snapshot; defaults to a
            fresh probe.
        token (str | None): Hub token for the lookup.

    Returns:
        CapacityReport: The recommended configuration.
    """
    hw = hardware or probe_hardware()
    report = None
    for dtype in (ModelDtype.BFLOAT16, ModelDtype.INT8, ModelDtype.INT4):
        report = can_run(
            num_params=num_params,
            model_id=model_id,
            dtype=dtype,
            hardware=hw,
            token=token,
        )
        if report.fits:
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
