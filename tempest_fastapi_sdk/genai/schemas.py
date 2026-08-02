"""Typed schemas for the self-hosted GenAI module."""

from __future__ import annotations

from itertools import takewhile
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


class ImageGenerationConfig(BaseSchema):
    """Typed parameters for local image generation.

    Passed to :class:`~tempest_fastapi_sdk.genai.ImageGenerator`. Only the
    fields you set are forwarded to the diffusion pipeline, so a partial
    config layers on top of the model's own defaults — which matters here
    more than for text, because the right step count and guidance scale
    differ by an order of magnitude between a distilled turbo model and a
    full SDXL.

    Example:

        >>> cfg = ImageGenerationConfig(steps=4, guidance_scale=0.0, seed=7)
        >>> images = await generator.generate("a red bicycle", config=cfg)

    Attributes:
        negative_prompt (str | None): What to steer away from. Ignored by
            models that do not implement classifier-free guidance.
        width (int | None): Output width in pixels; must suit the model
            (multiples of 8, and SDXL expects ~1024).
        height (int | None): Output height in pixels.
        steps (int | None): Denoising steps. Distilled/turbo models want
            1-8; full models want 20-50.
        guidance_scale (float | None): How hard to follow the prompt.
            Turbo models want ``0.0``; full models want 5-9.
        seed (int | None): RNG seed. Set it and the same prompt gives the
            same image on the same hardware.
        num_images (int): How many images to generate per call.
    """

    negative_prompt: str | None = Field(
        default=None,
        title="Negative prompt",
        description="What to steer the image away from.",
        examples=["blurry, watermark"],
    )
    width: int | None = Field(
        default=None,
        gt=0,
        title="Width",
        description="Output width in pixels.",
        examples=[512, 1024],
    )
    height: int | None = Field(
        default=None,
        gt=0,
        title="Height",
        description="Output height in pixels.",
        examples=[512, 1024],
    )
    steps: int | None = Field(
        default=None,
        gt=0,
        title="Steps",
        description="Denoising steps; turbo models want 1-8, full ones 20-50.",
        examples=[4, 30],
    )
    guidance_scale: float | None = Field(
        default=None,
        ge=0.0,
        title="Guidance scale",
        description="Prompt adherence; 0.0 for turbo models, 5-9 for full ones.",
        examples=[0.0, 7.5],
    )
    seed: int | None = Field(
        default=None,
        title="Seed",
        description="RNG seed for a reproducible image.",
        examples=[7],
    )
    num_images: int = Field(
        default=1,
        gt=0,
        title="Number of images",
        description="How many images to generate per call.",
        examples=[1, 4],
    )

    def to_pipeline_kwargs(self) -> dict[str, Any]:
        """Return the explicitly-set pipeline keywords.

        ``seed`` is excluded: diffusers takes reproducibility through a
        ``torch.Generator``, not a keyword, so the generator builds one from
        it. ``steps`` and ``num_images`` are renamed to the diffusers
        spellings (``num_inference_steps`` / ``num_images_per_prompt``) —
        the SDK keeps the short names because they are what the parameter
        actually is.

        Returns:
            dict[str, Any]: Keywords to splat into the pipeline call.
        """
        data = self.model_dump(exclude_none=True, exclude_unset=True)
        data.pop("seed", None)
        if "steps" in data:
            data["num_inference_steps"] = data.pop("steps")
        if "num_images" in data:
            data["num_images_per_prompt"] = data.pop("num_images")
        return data


class GeneratedImage(BaseSchema):
    """One rendered image plus what it takes to render it again.

    The seed travels with the image on purpose. Diffusion is deterministic
    given a seed, so returning the seed the run actually used is the
    difference between "nice image, gone forever" and "nice image, here is
    how to get it back" — and when the caller passes no seed, only the
    generator knows which one was drawn.

    Attributes:
        data (bytes): The encoded image (PNG unless ``image_format`` says
            otherwise).
        image_format (str): The encoding, lowercase (``"png"``).
        seed (int): The seed this image was rendered with.
        width (int): Pixel width of the result.
        height (int): Pixel height of the result.
    """

    data: bytes = Field(
        title="Image bytes",
        description="The encoded image data.",
        examples=[b"<binary image data>"],
    )
    image_format: str = Field(
        default="png",
        title="Format",
        description="Encoding of the bytes, lowercase.",
        examples=["png"],
    )
    seed: int = Field(
        title="Seed",
        description="The seed this image was rendered with.",
        examples=[7],
    )
    width: int = Field(
        title="Width",
        description="Pixel width of the result.",
        examples=[512],
    )
    height: int = Field(
        title="Height",
        description="Pixel height of the result.",
        examples=[512],
    )


DTYPE_KWARG_RENAMED_IN: tuple[int, int] = (4, 56)
"""First ``transformers`` release that takes ``dtype`` on ``from_pretrained``.

Verified against the released wheels, not from memory: 4.55.0 rejects
``dtype`` and never warns; 4.56.0 accepts it *and* logs
``` `torch_dtype` is deprecated! Use `dtype` instead! ``` for the old name;
5.x keeps both with the same warning. The SDK supports ``>=4.44``, so the
keyword has to be chosen at call time rather than hard-coded.
"""


def precision_kwarg(dtype: Any) -> dict[str, Any]:
    """Return the ``from_pretrained`` precision keyword for this install.

    Passing ``torch_dtype`` to a modern ``transformers`` still works, and
    prints a deprecation line on every single load — noise the SDK emits
    into the logs of every service that hosts a model. Passing ``dtype`` to
    an older one is worse than noisy: unknown keywords are forwarded to the
    config, so the precision would be silently ignored.

    Only for ``transformers`` loaders. The ``diffusers`` pipelines took
    ``dtype`` much later and the SDK still supports ``diffusers>=0.31``, so
    the image path keeps sending ``torch_dtype``.

    Args:
        dtype (Any): The resolved ``torch`` dtype to load the weights under.

    Returns:
        dict[str, Any]: A single-entry kwargs dict, keyed ``dtype`` on
        transformers >= 4.56 and ``torch_dtype`` below it. With no
        ``transformers`` installed there is nothing to load either, so the
        current name is returned rather than raising here — the real
        ``ImportError``, with its install hint, comes from the loader.
    """
    try:
        import transformers
    except ImportError:
        return {"dtype": dtype}

    parts: list[int] = []
    for chunk in transformers.__version__.split(".")[:2]:
        digits = "".join(takewhile(str.isdigit, chunk))
        parts.append(int(digits) if digits else 0)
    version = [*parts, 0, 0][:2]
    key = "dtype" if tuple(version) >= DTYPE_KWARG_RENAMED_IN else "torch_dtype"
    return {key: dtype}


__all__: list[str] = [
    "DTYPE_KWARG_RENAMED_IN",
    "CapacityReport",
    "GPUInfo",
    "GeneratedImage",
    "GenerationConfig",
    "HardwareInfo",
    "ImageGenerationConfig",
    "ModelDtype",
    "precision_kwarg",
]
