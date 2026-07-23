"""Typed schemas for the self-hosted GenAI module."""

from __future__ import annotations

from typing import Any

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


class GenerationConfig(BaseSchema):
    """Typed generation parameters for the local text generator.

    Passed to :class:`~tempest_fastapi_sdk.genai.TextGenerator`.
    Replaces loose ``**kwargs`` at the call site with a validated,
    self-describing, reusable object — build one config and pass it to
    ``generate`` / ``chat`` / ``stream``. Only the fields you set are
    forwarded to ``model.generate`` (unset fields fall through to the
    generator's own defaults), so a partial config layers cleanly on top.

    Example:

        >>> cfg = GenerationConfig(max_new_tokens=512, temperature=0.2)
        >>> await gen.generate("Explain PIX.", config=cfg)

    Attributes:
        max_new_tokens (int | None): Maximum tokens to generate (``> 0``).
        temperature (float | None): Sampling temperature (``0..2``); lower
            is more deterministic.
        top_p (float | None): Nucleus sampling probability mass (``0..1``).
        top_k (int | None): Top-k sampling cutoff (``>= 0``); ``0`` disables.
        repetition_penalty (float | None): Penalty for repeated tokens
            (``> 0``, ``1.0`` = no penalty).
        do_sample (bool | None): Sample (``True``) or use greedy decoding
            (``False``).
        seed (int | None): RNG seed for reproducible sampling.
        stop (list[str]): Stop strings that end generation early.
    """

    max_new_tokens: int | None = Field(
        default=None,
        gt=0,
        title="Max new tokens",
        description="Maximum number of tokens to generate.",
        examples=[256, 512],
    )
    temperature: float | None = Field(
        default=None,
        ge=0.0,
        le=2.0,
        title="Temperature",
        description="Sampling temperature; lower is more deterministic.",
        examples=[0.7, 0.2],
    )
    top_p: float | None = Field(
        default=None,
        ge=0.0,
        le=1.0,
        title="Top-p",
        description="Nucleus sampling probability mass.",
        examples=[0.9],
    )
    top_k: int | None = Field(
        default=None,
        ge=0,
        title="Top-k",
        description="Top-k sampling cutoff (0 disables).",
        examples=[50],
    )
    repetition_penalty: float | None = Field(
        default=None,
        gt=0.0,
        title="Repetition penalty",
        description="Penalty for repeated tokens (1.0 = no penalty).",
        examples=[1.1],
    )
    do_sample: bool | None = Field(
        default=None,
        title="Do sample",
        description="Sample (True) or greedy-decode (False).",
        examples=[True],
    )
    seed: int | None = Field(
        default=None,
        title="Seed",
        description="RNG seed for reproducible sampling.",
        examples=[42],
    )
    stop: list[str] = Field(
        default_factory=list,
        title="Stop strings",
        description="Strings that end generation early.",
    )

    def to_generate_kwargs(self) -> dict[str, Any]:
        """Return only the set fields as ``model.generate`` keyword args.

        ``seed`` and ``stop`` are dropped from the mapping — they are not
        ``transformers`` ``generate`` kwargs. The generator reapplies them
        itself: ``seed`` via ``transformers.set_seed`` and ``stop`` via the
        ``stop_strings`` generation argument (see
        :meth:`~tempest_fastapi_sdk.genai.text.TextGenerator._resolve_control`).

        Returns:
            dict[str, Any]: The explicitly-set generation kwargs.
        """
        data = self.model_dump(exclude_none=True, exclude_unset=True)
        data.pop("seed", None)
        data.pop("stop", None)
        return data


__all__: list[str] = [
    "CapacityReport",
    "GPUInfo",
    "GenerationConfig",
    "HardwareInfo",
    "ModelDtype",
]
