"""Typed schemas for the self-hosted GenAI module."""

from __future__ import annotations

from pydantic import Field

from tempest_fastapi_sdk.core import BaseStrEnum
from tempest_fastapi_sdk.schemas.base import BaseSchema


class ModelDtype(BaseStrEnum):
    """Weight precision a model is loaded in.

    Fewer bytes per parameter → smaller memory footprint, at some quality
    cost. ``int8`` / ``int4`` require quantization (the ``[genai-quant]``
    extra).

    * ``FLOAT32`` — 4 bytes/param (full precision).
    * ``FLOAT16`` / ``BFLOAT16`` — 2 bytes/param (the usual GPU default).
    * ``INT8`` — 1 byte/param (quantized).
    * ``INT4`` — ~0.5 byte/param (quantized).
    """

    FLOAT32 = "float32"
    FLOAT16 = "float16"
    BFLOAT16 = "bfloat16"
    INT8 = "int8"
    INT4 = "int4"


class GPUInfo(BaseSchema):
    """One CUDA device's memory picture.

    Attributes:
        index (int): The CUDA device index.
        name (str): The device name (e.g. ``"NVIDIA RTX 4090"``).
        vram_total_bytes (int): Total VRAM on the device.
        vram_free_bytes (int): Currently free VRAM.
    """

    index: int
    name: str
    vram_total_bytes: int
    vram_free_bytes: int


class HardwareInfo(BaseSchema):
    """A snapshot of the host's compute resources.

    Attributes:
        cpu_cores (int): Logical CPU cores.
        ram_total_bytes (int): Total system RAM.
        ram_available_bytes (int): Currently available system RAM.
        has_cuda (bool): Whether a CUDA GPU is usable via torch.
        gpus (list[GPUInfo]): Per-CUDA-device memory (empty without CUDA).
        has_mps (bool): Whether Apple Metal (MPS) is available.
        disk_free_bytes (int): Free space on the model cache filesystem.
    """

    cpu_cores: int
    ram_total_bytes: int
    ram_available_bytes: int
    has_cuda: bool = False
    gpus: list[GPUInfo] = Field(default_factory=list)
    has_mps: bool = False
    disk_free_bytes: int = 0


class CapacityReport(BaseSchema):
    """The verdict of whether the host can run a given model.

    Attributes:
        fits (bool): Whether the model is expected to fit on ``device``.
        device (str): The chosen device — ``"cuda"``, ``"mps"`` or
            ``"cpu"``.
        dtype (ModelDtype): The precision the estimate assumes.
        estimated_bytes (int): Estimated memory the model needs (weights +
            inference overhead).
        available_bytes (int): Memory available on ``device``.
        headroom_pct (float): ``(available - estimated) / available * 100``;
            negative when it doesn't fit.
        reason (str): Human-readable explanation of the verdict.
        suggestion (str | None): A concrete next step when it doesn't fit
            (e.g. quantize, offload to CPU), or ``None`` when it fits.
    """

    fits: bool
    device: str
    dtype: ModelDtype
    estimated_bytes: int
    available_bytes: int
    headroom_pct: float
    reason: str
    suggestion: str | None = None


__all__: list[str] = [
    "CapacityReport",
    "GPUInfo",
    "HardwareInfo",
    "ModelDtype",
]
