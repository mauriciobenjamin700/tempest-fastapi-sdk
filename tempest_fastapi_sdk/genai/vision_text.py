"""Local vision-language generation over HuggingFace transformers.

`VisionTextGenerator` is the multimodal sibling of
:class:`~tempest_fastapi_sdk.genai.text.TextGenerator`: it loads an
``AutoModelForVision2Seq`` + ``AutoProcessor`` and generates text conditioned
on one or more images, on your own hardware. It mirrors the ``TextBackend``
surface (``generate`` / ``chat`` are image-optional) so text-only calls keep
working, giving the transformers path the same multimodal reach the
:class:`~tempest_fastapi_sdk.genai.ollama.OllamaGenerator` already has via its
``images`` argument.

Images are accepted as a path, raw ``bytes``, a ``PIL.Image``, or a NumPy
``ndarray`` (same leniency as ``ort-vision-sdk``), normalized to ``PIL.Image``
by :func:`_load_image`. Pillow ships in the ``[genai-vlm]`` extra; ``torch`` /
``transformers`` are the ``[genai]`` extra, imported lazily on
:meth:`VisionTextGenerator.load`.

Processor conventions vary across model families — this class targets the
common ``processor(text=..., images=...)`` interface used by LLaVA and
Qwen2-VL; other families may need a thin adapter.
"""

from __future__ import annotations

import asyncio
import io
import time
from pathlib import Path
from typing import Any

from tempest_fastapi_sdk.genai.schemas import GenerationConfig, HardwareInfo, ModelDtype
from tempest_fastapi_sdk.genai.text import (
    _require_transformers,
    auto_dtype_name,
    resolve_device,
)


def _require_pillow() -> Any:
    """Import ``PIL`` or raise a helpful error.

    Returns:
        Any: The imported ``PIL.Image`` module.

    Raises:
        ImportError: When the ``[genai-vlm]`` extra is not installed.
    """
    try:
        from PIL import Image
    except ImportError as exc:
        raise ImportError(
            "Vision-language generation requires the optional [genai-vlm] "
            "extra. Install with: pip install tempest-fastapi-sdk[genai-vlm]",
        ) from exc
    return Image


def _load_image(source: Any) -> Any:
    """Normalize an image source to a ``PIL.Image``.

    Args:
        source (Any): A file path (``str`` / :class:`~pathlib.Path`), raw
            ``bytes``, a NumPy ``ndarray`` (HxWxC), or an already-loaded
            ``PIL.Image``.

    Returns:
        PIL.Image.Image: The loaded RGB image.

    Raises:
        ImportError: When the ``[genai-vlm]`` extra is missing.
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


class VisionTextGenerator:
    """A lazily-loaded local vision-language model with idle unload.

    Example:

        >>> gen = VisionTextGenerator("llava-hf/llava-1.5-7b-hf")
        >>> await gen.generate("Describe this image.", images=["photo.jpg"])

    Attributes:
        model_id (str): The HuggingFace model id.
        device (str): The resolved device (``cuda`` / ``mps`` / ``cpu``).
        dtype (ModelDtype): The resolved compute precision.
        idle_unload_seconds (float | None): Idle threshold for
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
        idle_unload_seconds: float | None = None,
        hardware: HardwareInfo | None = None,
    ) -> None:
        """Configure the generator (does not load weights yet).

        Args:
            model_id (str): HuggingFace vision-language model id.
            device (str): ``"auto"`` / ``"cuda"`` / ``"mps"`` / ``"cpu"``.
            dtype (str | ModelDtype): Compute precision, or ``"auto"``.
            cache_dir (str | None): Where to cache downloaded weights.
            hf_token (str | None): Hub token for gated/private models.
            idle_unload_seconds (float | None): When set,
                :meth:`unload_if_idle` frees the model after this idle window.
            hardware (HardwareInfo | None): Injected snapshot for device
                resolution (tests); probed when ``None``.
        """
        self.model_id = model_id
        self.device = resolve_device(device, hardware)
        self.dtype = (
            ModelDtype(auto_dtype_name(self.device))
            if dtype == "auto"
            else ModelDtype(dtype)
        )
        self.cache_dir = cache_dir
        self.hf_token = hf_token
        self.idle_unload_seconds = idle_unload_seconds
        self._model: Any = None
        self._processor: Any = None
        self._last_used: float = time.monotonic()

    @property
    def is_loaded(self) -> bool:
        """Return ``True`` once the weights are in memory."""
        return self._model is not None

    @property
    def seconds_idle(self) -> float:
        """Return seconds since the last generation (or load)."""
        return time.monotonic() - self._last_used

    def _touch(self) -> None:
        """Mark the model as just used (resets the idle clock)."""
        self._last_used = time.monotonic()

    def load(self) -> None:  # pragma: no cover - needs torch + a real model
        """Download (if needed) and load the model + processor into memory.

        Idempotent — a no-op once loaded. Called automatically by
        :meth:`generate` / :meth:`chat`.

        Raises:
            ImportError: When the ``[genai]`` extra is missing.
        """
        if self.is_loaded:
            return
        torch, transformers = _require_transformers()
        self._processor = transformers.AutoProcessor.from_pretrained(
            self.model_id,
            cache_dir=self.cache_dir,
            token=self.hf_token,
        )
        self._model = transformers.AutoModelForVision2Seq.from_pretrained(
            self.model_id,
            cache_dir=self.cache_dir,
            token=self.hf_token,
            torch_dtype=getattr(torch, self.dtype.value),
            device_map=self.device if self.device != "cpu" else None,
        )
        if self.device == "cpu":
            self._model = self._model.to("cpu")
        self._touch()

    def unload(self) -> None:
        """Free the model and its memory (VRAM/RAM). Safe when not loaded."""
        if self._model is None:
            return
        self._model = None
        self._processor = None
        try:  # pragma: no cover - only meaningful with torch + CUDA
            import torch

            if torch.cuda.is_available():
                torch.cuda.empty_cache()
        except ImportError:
            pass

    def unload_if_idle(self) -> bool:
        """Unload the model when idle past ``idle_unload_seconds``.

        Returns:
            bool: ``True`` when it unloaded, ``False`` otherwise.
        """
        if (
            self.idle_unload_seconds is None
            or not self.is_loaded
            or self.seconds_idle < self.idle_unload_seconds
        ):
            return False
        self.unload()
        return True

    def _gen_kwargs(
        self,
        overrides: dict[str, Any],
        config: GenerationConfig | None,
    ) -> dict[str, Any]:
        """Merge generation defaults with an optional config and overrides."""
        merged: dict[str, Any] = {"max_new_tokens": 256}
        if config is not None:
            merged.update(config.to_generate_kwargs())
        merged.update(overrides)
        return merged

    async def generate(
        self,
        prompt: str,
        *,
        images: list[Any] | None = None,
        config: GenerationConfig | None = None,
        **kwargs: Any,
    ) -> str:
        """Generate a completion for ``prompt`` conditioned on ``images``.

        Args:
            prompt (str): The text prompt (include the model's image
                placeholder token if its processor requires one).
            images (list[Any] | None): Images as path / bytes / PIL / ndarray;
                ``None`` for a text-only call.
            config (GenerationConfig | None): Typed generation parameters.
            **kwargs (Any): Generation overrides (win over ``config``).

        Returns:
            str: The generated text.
        """
        return await asyncio.to_thread(
            self._generate_sync,
            prompt,
            images,
            config,
            kwargs,
        )

    def _generate_sync(  # pragma: no cover - needs torch + a real model
        self,
        prompt: str,
        images: list[Any] | None,
        config: GenerationConfig | None,
        overrides: dict[str, Any],
    ) -> str:
        """Run blocking multimodal generation and return the completion."""
        self.load()
        pil_images = [_load_image(image) for image in images] if images else None
        inputs = self._processor(
            text=prompt,
            images=pil_images,
            return_tensors="pt",
        ).to(self._model.device)
        output = self._model.generate(**inputs, **self._gen_kwargs(overrides, config))
        generated = output[0][inputs["input_ids"].shape[1] :]
        text = self._processor.decode(generated, skip_special_tokens=True)
        self._touch()
        return str(text)

    async def chat(
        self,
        messages: list[dict[str, Any]],
        *,
        images: list[Any] | None = None,
        config: GenerationConfig | None = None,
        **kwargs: Any,
    ) -> str:
        """Generate a reply for a chat ``messages`` list with optional images.

        Applies the processor's chat template (which inserts the image
        placeholder tokens) before generating.

        Args:
            messages (list[dict[str, Any]]): Chat turns; image content is
                referenced per the model's template.
            images (list[Any] | None): Images for the turn.
            config (GenerationConfig | None): Typed generation parameters.
            **kwargs (Any): Generation overrides (win over ``config``).

        Returns:
            str: The assistant reply.
        """
        return await asyncio.to_thread(
            self._chat_sync,
            messages,
            images,
            config,
            kwargs,
        )

    def _chat_sync(  # pragma: no cover - needs torch + a real model
        self,
        messages: list[dict[str, Any]],
        images: list[Any] | None,
        config: GenerationConfig | None,
        overrides: dict[str, Any],
    ) -> str:
        """Blocking multimodal chat generation via the processor template."""
        self.load()
        prompt = self._processor.apply_chat_template(
            messages,
            tokenize=False,
            add_generation_prompt=True,
        )
        return self._generate_sync(prompt, images, config, overrides)


__all__: list[str] = [
    "VisionTextGenerator",
]
