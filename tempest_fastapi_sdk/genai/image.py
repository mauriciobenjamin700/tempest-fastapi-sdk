"""Local image generation over HuggingFace diffusers.

`ImageGenerator` loads a diffusion pipeline once and renders on your own
hardware. It mirrors :class:`~tempest_fastapi_sdk.genai.TextGenerator` —
same device/precision resolution, same lazy load, same idle unload, same
Hub pinning keywords — so a service that already self-hosts a language
model gains images without learning a second set of conventions.

Two operations, one loaded pipeline: :meth:`ImageGenerator.generate`
(text to image) and :meth:`ImageGenerator.edit` (image to image). The
second is built with ``AutoPipelineForImage2Image.from_pipe``, which
**reuses the already-loaded components** instead of allocating a second
copy of the weights — an SDXL pipeline is ~7 GB, and loading it twice on
one card is how a service OOMs at the first edit request.

``diffusers`` and ``torch`` are imported inside :meth:`ImageGenerator.load`,
so this module imports without the ``[genai-image]`` extra.

Concurrency defaults to **one** render at a time. Unlike a language model,
a diffusion pipeline saturates the GPU on a single call; running two
concurrently makes both slower and doubles peak VRAM.
"""

from __future__ import annotations

import asyncio
import io
import secrets
from pathlib import Path
from typing import Any

from tempest_fastapi_sdk.genai.hub import ModelRef
from tempest_fastapi_sdk.genai.metrics import GenAIMetrics
from tempest_fastapi_sdk.genai.schemas import (
    GeneratedImage,
    HardwareInfo,
    ImageGenerationConfig,
    ModelDtype,
)
from tempest_fastapi_sdk.genai.text import auto_dtype_name, resolve_device
from tempest_fastapi_sdk.genai.tracing import genai_span
from tempest_fastapi_sdk.utils._lifecycle import ModelLifecycle

_MAX_SEED: int = 2**32 - 1


def _require_diffusers() -> tuple[Any, Any]:
    """Import ``torch`` + ``diffusers`` or raise a helpful error.

    Returns:
        tuple[Any, Any]: ``(torch, diffusers)``.

    Raises:
        ImportError: When the ``[genai-image]`` extra is not installed.
    """
    try:
        import diffusers
        import torch
    except ImportError as exc:
        raise ImportError(
            "Image generation requires the optional [genai-image] extra. "
            "Install with: pip install tempest-fastapi-sdk[genai-image]",
        ) from exc
    return torch, diffusers


def _require_pillow() -> Any:
    """Import ``PIL.Image`` or raise a helpful error.

    Returns:
        Any: The ``PIL.Image`` module.

    Raises:
        ImportError: When Pillow is missing (it ships with
            ``[genai-image]``).
    """
    try:
        from PIL import Image
    except ImportError as exc:
        raise ImportError(
            "Image generation requires the optional [genai-image] extra. "
            "Install with: pip install tempest-fastapi-sdk[genai-image]",
        ) from exc
    return Image


def _load_image(source: Any) -> Any:
    """Normalize an image source to a ``PIL.Image``.

    Args:
        source (Any): A file path (``str`` / :class:`~pathlib.Path`), raw
            ``bytes``, a NumPy ``ndarray`` (HxWxC), or an already-loaded
            ``PIL.Image``.

    Returns:
        PIL.Image.Image: The loaded image.

    Raises:
        ImportError: When Pillow is missing.
        TypeError: When ``source`` is not a supported type.
    """
    image_mod = _require_pillow()
    if isinstance(source, image_mod.Image):
        return source
    if isinstance(source, str | Path):
        return image_mod.open(source)
    if isinstance(source, bytes | bytearray):
        return image_mod.open(io.BytesIO(bytes(source)))
    if source.__class__.__module__ == "numpy":
        return image_mod.fromarray(source)
    raise TypeError(
        f"unsupported image source type: {type(source).__name__!r} "
        "(expected path, bytes, PIL.Image or numpy.ndarray)",
    )


def _encode(image: Any, image_format: str) -> bytes:
    """Encode a ``PIL.Image`` to bytes.

    Args:
        image (Any): The rendered ``PIL.Image``.
        image_format (str): Target encoding, lowercase (``"png"``,
            ``"jpeg"``, ``"webp"``).

    Returns:
        bytes: The encoded image.
    """
    buffer = io.BytesIO()
    image.save(buffer, format=image_format.upper())
    return buffer.getvalue()


class ImageGenerator:
    """A lazily-loaded local diffusion pipeline with idle unload.

    Example:

        >>> gen = ImageGenerator("stabilityai/sdxl-turbo")
        >>> images = await gen.generate(
        ...     "a lighthouse at dawn",
        ...     config=ImageGenerationConfig(steps=4, guidance_scale=0.0),
        ... )
        >>> Path("out.png").write_bytes(images[0].data)
        >>> gen.unload()

    Attributes:
        model_id (str): The HuggingFace model id.
        device (str): The resolved device (``cuda`` / ``mps`` / ``cpu``).
        dtype (ModelDtype): The resolved compute precision.
        source (ModelRef): The resolved weight identity forwarded to
            ``from_pretrained``.
        image_format (str): Encoding of the returned bytes.
        pipeline_kwargs (dict[str, Any]): Extra ``from_pretrained``
            keywords, applied last so they override what the SDK computes.
        idle_unload_seconds (float | None): Idle threshold used by
            :meth:`unload_if_idle`.
    """

    def __init__(
        self,
        model_id: str,
        *,
        device: str = "auto",
        dtype: str | ModelDtype = "auto",
        cache_dir: str | None = None,
        hf_token: str | None = None,
        revision: str | None = None,
        local_files_only: bool | None = None,
        trust_remote_code: bool = False,
        image_format: str = "png",
        pipeline_kwargs: dict[str, Any] | None = None,
        max_concurrent: int = 1,
        idle_unload_seconds: float | None = None,
        hardware: HardwareInfo | None = None,
        metrics: GenAIMetrics | None = None,
    ) -> None:
        """Configure the generator (does not load weights yet).

        Args:
            model_id (str): HuggingFace diffusion model id.
            device (str): ``"auto"`` / ``"cuda"`` / ``"mps"`` / ``"cpu"``.
            dtype (str | ModelDtype): Compute precision, or ``"auto"``
                (bf16 on GPU, fp32 on CPU).
            cache_dir (str | None): Where the downloaded weights are
                written and read back from. ``None`` uses the
                ``huggingface_hub`` default — ``$HF_HOME/hub``, or
                ``~/.cache/huggingface/hub`` when ``HF_HOME`` is unset —
                which is why the second run of a script starts instantly
                instead of downloading again. Point it at a mounted
                volume when the process is a container, so the layer does
                not re-download the model on every restart.
            hf_token (str | None): Hub token for gated or private
                repositories. ``None`` falls back to ``HF_TOKEN`` in the
                environment; without either, anonymous downloads work but
                are rate-limited (the Hub says so on stderr).
            revision (str | None): Branch, tag or commit sha to load;
                ``None`` follows the moving Hub default.
            local_files_only (bool | None): Load from the cache without
                touching the network — what an air-gapped or deploy-frozen
                host wants. ``None`` (the default) takes ``GENAI_OFFLINE``
                from the environment; passing the argument overrides it.
            trust_remote_code (bool): Allow the repository's own Python to
                run at load time.
            image_format (str): Encoding of the returned bytes — ``"png"``
                (lossless, the default), ``"jpeg"`` or ``"webp"``.
            pipeline_kwargs (dict[str, Any] | None): Extra keywords for
                ``from_pretrained``, for the load-time decisions the SDK
                does not model. The three that come up constantly:
                ``{"safety_checker": None}`` skips the extra CLIP that
                Stable Diffusion 1.x repositories bundle (it costs memory
                and can blank an image); ``{"variant": "fp16"}`` fetches
                the half-precision weights, roughly halving the download;
                ``{"use_safetensors": True}`` refuses a pickle checkpoint.
                Keys here win over the ones the SDK computes, so this is
                also the way to override ``torch_dtype``.
            max_concurrent (int): Simultaneous renders. Defaults to ``1``
                because one diffusion call already saturates the GPU.
            idle_unload_seconds (float | None): When set,
                :meth:`unload_if_idle` frees the pipeline after this many
                idle seconds.
            hardware (HardwareInfo | None): Injected snapshot for device
                resolution (tests); probed when ``None``.
            metrics (GenAIMetrics | None): Optional Prometheus metrics;
                when set, :meth:`generate` / :meth:`edit` record request
                count and latency (op ``"image"`` / ``"image_edit"``).

        Raises:
            ValueError: When ``max_concurrent`` is not positive.
        """
        if max_concurrent <= 0:
            raise ValueError("max_concurrent must be positive")
        self.model_id = model_id
        self.device = resolve_device(device, hardware)
        self.dtype = (
            ModelDtype(auto_dtype_name(self.device))
            if dtype == "auto"
            else ModelDtype(dtype)
        )
        self.source = ModelRef(
            model_id=model_id,
            revision=revision,
            cache_dir=cache_dir,
            token=hf_token,
            local_files_only=local_files_only,
            trust_remote_code=trust_remote_code,
        )
        self.image_format = image_format.lower()
        self.pipeline_kwargs: dict[str, Any] = dict(pipeline_kwargs or {})
        self.idle_unload_seconds = idle_unload_seconds
        self.metrics = metrics
        self._pipeline: Any = None
        self._edit_pipeline: Any = None
        self._semaphore = asyncio.Semaphore(max_concurrent)
        self._lifecycle = ModelLifecycle(
            build=self._build,
            release=self._release,
            is_loaded=lambda: self._pipeline is not None,
        )

    @property
    def is_loaded(self) -> bool:
        """Return ``True`` once the pipeline is in memory.

        Returns:
            bool: Whether :meth:`load` has run without a later
            :meth:`unload`.
        """
        return self._pipeline is not None

    @property
    def seconds_idle(self) -> float:
        """Return seconds since the pipeline was last in use.

        A render in flight counts as use for its whole duration, so this
        reads ``0.0`` while one runs.

        Returns:
            float: Idle time in seconds.
        """
        return self._lifecycle.seconds_idle()

    @property
    def pipeline(self) -> Any:
        """Return the underlying diffusers pipeline (escape hatch).

        Use it to swap the scheduler, attach a LoRA or enable a memory
        optimization the SDK does not wrap. Loads on first access, which
        blocks for the whole download and build.

        Returns:
            Any: The loaded ``AutoPipelineForText2Image``.
        """
        self.load()
        return self._pipeline

    def load(self) -> None:
        """Download (if needed) and load the diffusion pipeline.

        Idempotent — a no-op once loaded. Called automatically by
        :meth:`generate` / :meth:`edit`, on their worker thread. Safe to
        call from several threads at once: concurrent callers on a cold
        instance wait for one build.

        Raises:
            ImportError: When the ``[genai-image]`` extra is missing.
        """
        self._lifecycle.load()

    def _build(self) -> None:  # pragma: no cover - needs torch + a real model
        """Build the pipeline; called once, under the load lock.

        Raises:
            ImportError: When the ``[genai-image]`` extra is missing.
        """
        torch, diffusers = _require_diffusers()
        kwargs: dict[str, Any] = {
            "torch_dtype": getattr(torch, self.dtype.value),
            **self.source.loader_kwargs(),
            **self.pipeline_kwargs,
        }
        pipeline = diffusers.AutoPipelineForText2Image.from_pretrained(
            self.model_id,
            **kwargs,
        )
        self._pipeline = pipeline.to(self.device)

    def unload(self) -> None:
        """Free the pipeline and its memory (VRAM/RAM).

        Safe to call when not loaded. After this, the next render reloads
        the weights. While renders are in flight the release waits for
        them: the last one to finish drops the pipeline.
        """
        self._lifecycle.unload()

    def _release(self) -> None:
        """Drop both pipelines and return cached CUDA memory."""
        self._pipeline = None
        self._edit_pipeline = None
        try:  # pragma: no cover - only meaningful with torch + CUDA
            import torch

            if torch.cuda.is_available():
                torch.cuda.empty_cache()
        except ImportError:
            pass

    def unload_if_idle(self) -> bool:
        """Free the pipeline when it has been idle past the threshold.

        Returns:
            bool: ``True`` when this call unloaded the pipeline, ``False``
            when it was already free, still in use, or no
            ``idle_unload_seconds`` was configured.
        """
        return self._lifecycle.unload_if_idle(self.idle_unload_seconds)

    def _resolve_seed(self, config: ImageGenerationConfig | None) -> int:
        """Return the seed to render with, drawing one when unset.

        A seed is always chosen — never left to the pipeline's ambient RNG
        — so every returned image carries a value that reproduces it.

        Args:
            config (ImageGenerationConfig | None): The call's config.

        Returns:
            int: The seed for this render.
        """
        if config is not None and config.seed is not None:
            return config.seed
        return secrets.randbelow(_MAX_SEED)

    def _run_sync(
        self,
        kwargs: dict[str, Any],
        seed: int,
        *,
        edit_source: Any = None,
    ) -> list[GeneratedImage]:
        """Load if needed, run a pipeline and encode its output.

        Everything blocking — the first-call load, building the
        image-to-image pipeline, decoding the input image and the render
        itself — happens here, on the worker thread, inside the
        lifecycle's ``use()`` block so an unload cannot free the pipeline
        mid-render.

        Args:
            kwargs (dict[str, Any]): Pipeline keywords, already merged.
            seed (int): The seed to bind to the torch generator.
            edit_source (Any): The starting image for an edit; ``None``
                renders from text.

        Returns:
            list[GeneratedImage]: One entry per rendered image.
        """
        with self._lifecycle.use():
            torch, _ = _require_diffusers()
            if edit_source is None:
                pipeline = self._pipeline
            else:
                pipeline = self._load_edit_pipeline()
                kwargs = {**kwargs, "image": _load_image(edit_source).convert("RGB")}
            generator = torch.Generator(device=self.device).manual_seed(seed)
            result = pipeline(generator=generator, **kwargs)
            return [
                GeneratedImage(
                    data=_encode(image, self.image_format),
                    image_format=self.image_format,
                    seed=seed,
                    width=image.width,
                    height=image.height,
                )
                for image in result.images
            ]

    async def _render(
        self,
        kwargs: dict[str, Any],
        seed: int,
        *,
        edit_source: Any = None,
    ) -> list[GeneratedImage]:
        """Run the blocking pipeline off the event loop, one at a time.

        Args:
            kwargs (dict[str, Any]): Pipeline keywords, already merged.
            seed (int): The seed for this render.
            edit_source (Any): The starting image for an edit, or ``None``.

        Returns:
            list[GeneratedImage]: The encoded results.
        """
        async with self._semaphore:
            return await asyncio.to_thread(
                self._run_sync,
                kwargs,
                seed,
                edit_source=edit_source,
            )

    async def generate(
        self,
        prompt: str,
        *,
        config: ImageGenerationConfig | None = None,
    ) -> list[GeneratedImage]:
        """Render one or more images from a text prompt.

        Runs the blocking pipeline — including the first-call load — in a
        worker thread, capped by the concurrency semaphore, so it never
        blocks the event loop.

        Example:

            >>> images = await generator.generate("a red bicycle")
            >>> images[0].seed
            418223901

        Args:
            prompt (str): What to draw.
            config (ImageGenerationConfig | None): Size, steps, guidance,
                seed and count. Unset fields fall through to the model's
                own defaults, which differ by an order of magnitude
                between a turbo model and a full one.

        Returns:
            list[GeneratedImage]: The rendered images, each carrying the
            seed that reproduces it.

        Raises:
            ImportError: When the ``[genai-image]`` extra is missing.
        """
        seed = self._resolve_seed(config)
        kwargs: dict[str, Any] = {"prompt": prompt}
        if config is not None:
            kwargs.update(config.to_pipeline_kwargs())
        async with genai_span("image", self.model_id):
            if self.metrics is None:
                return await self._render(kwargs, seed)
            async with self.metrics.track(self.model_id, "image"):
                return await self._render(kwargs, seed)

    def _load_edit_pipeline(self) -> Any:  # pragma: no cover - needs a model
        """Build the image-to-image pipeline over the loaded components.

        ``from_pipe`` shares the already-loaded UNet, VAE and text encoders
        rather than reading a second copy off disk, so enabling edits costs
        no additional VRAM. Built under the load lock, so two concurrent
        first edits build it once.

        Returns:
            Any: The ``AutoPipelineForImage2Image``.
        """
        with self._lifecycle.load_lock:
            if self._edit_pipeline is None:
                _, diffusers = _require_diffusers()
                self._edit_pipeline = diffusers.AutoPipelineForImage2Image.from_pipe(
                    self._pipeline,
                )
            return self._edit_pipeline

    async def edit(
        self,
        prompt: str,
        image: Any,
        *,
        strength: float = 0.8,
        config: ImageGenerationConfig | None = None,
    ) -> list[GeneratedImage]:
        """Redraw an existing image under a new prompt (image-to-image).

        The load, the image-to-image pipeline build and the decoding of
        ``image`` all run on the worker thread with the render, so none of
        them blocks the event loop.

        Example:

            >>> edited = await generator.edit(
            ...     "the same room, at night",
            ...     "room.png",
            ...     strength=0.6,
            ... )

        Args:
            prompt (str): What the result should depict.
            image (Any): The starting image — a path, ``bytes``, a
                ``PIL.Image`` or a NumPy array.
            strength (float): How far to move from the input, ``0..1``.
                Low values keep the composition; ``1.0`` ignores the input
                almost entirely.
            config (ImageGenerationConfig | None): Steps, guidance, seed
                and count. ``width``/``height`` are usually left unset
                here — the input image sets the size.

        Returns:
            list[GeneratedImage]: The redrawn images.

        Raises:
            ImportError: When the ``[genai-image]`` extra is missing.
            TypeError: When ``image`` is not a supported source type.
            ValueError: When ``strength`` is outside ``0..1``.
        """
        if not 0.0 <= strength <= 1.0:
            raise ValueError("strength must be between 0.0 and 1.0")
        seed = self._resolve_seed(config)
        kwargs: dict[str, Any] = {"prompt": prompt, "strength": strength}
        if config is not None:
            kwargs.update(config.to_pipeline_kwargs())
        async with genai_span("image_edit", self.model_id):
            if self.metrics is None:
                return await self._render(kwargs, seed, edit_source=image)
            async with self.metrics.track(self.model_id, "image_edit"):
                return await self._render(kwargs, seed, edit_source=image)


__all__: list[str] = ["ImageGenerator"]
