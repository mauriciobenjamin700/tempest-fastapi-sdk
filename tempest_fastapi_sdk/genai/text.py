"""Local LLM text generation over HuggingFace transformers.

`TextGenerator` loads a causal LM once and runs generation on your own
hardware. It resolves the device and precision automatically, supports
int8/int4 quantization (``[genai-quant]``), lazily loads the weights on
first use, streams tokens, and can free VRAM when idle.

The heavy imports (``torch`` / ``transformers``) are deferred to
:meth:`TextGenerator.load`, so this module imports without the ``[genai]``
extra — the device/precision resolution helpers are usable and testable
on their own. Blocking generation runs in ``asyncio.to_thread`` so it
never blocks the event loop.
"""

from __future__ import annotations

import asyncio
import time
from collections.abc import AsyncIterator
from typing import Any

from tempest_fastapi_sdk.genai.hardware import probe_hardware
from tempest_fastapi_sdk.genai.schemas import HardwareInfo, ModelDtype

_QUANTIZATIONS: frozenset[ModelDtype] = frozenset({ModelDtype.INT8, ModelDtype.INT4})


def resolve_device(device: str, hardware: HardwareInfo | None = None) -> str:
    """Resolve ``"auto"`` to a concrete device, or pass a fixed one through.

    Args:
        device (str): ``"auto"``, ``"cuda"``, ``"mps"`` or ``"cpu"``.
        hardware (HardwareInfo | None): Injected snapshot (tests); probed
            when ``None``.

    Returns:
        str: The concrete device — CUDA → MPS → CPU for ``"auto"``.
    """
    if device != "auto":
        return device
    hw = hardware or probe_hardware()
    if hw.has_cuda and hw.gpus:
        return "cuda"
    if hw.has_mps:
        return "mps"
    return "cpu"


def auto_dtype_name(device: str) -> str:
    """Return the default compute precision name for ``device``.

    Args:
        device (str): The concrete device.

    Returns:
        str: ``"bfloat16"`` on CUDA/MPS, ``"float32"`` on CPU (which has
        no fast half-precision path).
    """
    return "float32" if device == "cpu" else "bfloat16"


def _require_transformers() -> tuple[Any, Any]:
    """Import ``torch`` + ``transformers`` or raise a helpful error.

    Returns:
        tuple[Any, Any]: ``(torch, transformers)``.

    Raises:
        ImportError: When the ``[genai]`` extra is not installed.
    """
    try:
        import torch
        import transformers
    except ImportError as exc:
        raise ImportError(
            "Text generation requires the optional [genai] extra. "
            "Install with: pip install tempest-fastapi-sdk[genai]",
        ) from exc
    return torch, transformers


class TextGenerator:
    """A lazily-loaded local causal LM with streaming and idle unload.

    Example:

        >>> gen = TextGenerator("Qwen/Qwen2.5-7B-Instruct", quantization="int4")
        >>> await gen.generate("Explain PIX in one sentence.")
        >>> async for token in gen.stream("..."):
        ...     ...
        >>> gen.unload()   # free VRAM

    Attributes:
        model_id (str): The HuggingFace model id.
        device (str): The resolved device (``cuda`` / ``mps`` / ``cpu``).
        dtype (ModelDtype): The resolved compute precision.
        quantization (ModelDtype | None): int8/int4 when quantized.
        idle_unload_seconds (float | None): Idle threshold used by
            :meth:`unload_if_idle`.
    """

    def __init__(
        self,
        model_id: str,
        *,
        device: str = "auto",
        dtype: str | ModelDtype = "auto",
        quantization: str | ModelDtype | None = None,
        cache_dir: str | None = None,
        hf_token: str | None = None,
        idle_unload_seconds: float | None = None,
        hardware: HardwareInfo | None = None,
    ) -> None:
        """Configure the generator (does not load weights yet).

        Args:
            model_id (str): HuggingFace model id.
            device (str): ``"auto"`` (default) / ``"cuda"`` / ``"mps"`` /
                ``"cpu"``.
            dtype (str | ModelDtype): Compute precision, or ``"auto"``
                (bf16 on GPU, fp32 on CPU).
            quantization (str | ModelDtype | None): ``"int8"`` / ``"int4"``
                to quantize (needs ``[genai-quant]``), or ``None``.
            cache_dir (str | None): Where to cache downloaded weights.
            hf_token (str | None): Hub token for gated/private models.
            idle_unload_seconds (float | None): When set, :meth:`unload_if_idle`
                frees the model after this many idle seconds.
            hardware (HardwareInfo | None): Injected snapshot for device
                resolution (tests); probed when ``None``.

        Raises:
            ValueError: When ``quantization`` is not int8/int4.
        """
        self.model_id = model_id
        self.device = resolve_device(device, hardware)
        resolved_dtype = (
            ModelDtype(auto_dtype_name(self.device))
            if dtype == "auto"
            else ModelDtype(dtype)
        )
        self.dtype = resolved_dtype
        self.quantization: ModelDtype | None = (
            None if quantization is None else ModelDtype(quantization)
        )
        if self.quantization is not None and self.quantization not in _QUANTIZATIONS:
            raise ValueError("quantization must be 'int8', 'int4' or None")
        self.cache_dir = cache_dir
        self.hf_token = hf_token
        self.idle_unload_seconds = idle_unload_seconds
        self._model: Any = None
        self._tokenizer: Any = None
        self._last_used: float = time.monotonic()

    @property
    def is_loaded(self) -> bool:
        """Return ``True`` once the weights are in memory.

        Returns:
            bool: Whether :meth:`load` has run without a later :meth:`unload`.
        """
        return self._model is not None

    @property
    def seconds_idle(self) -> float:
        """Return seconds since the last generation (or load).

        Returns:
            float: Idle time in seconds.
        """
        return time.monotonic() - self._last_used

    def _touch(self) -> None:
        """Mark the model as just used (resets the idle clock)."""
        self._last_used = time.monotonic()

    def load(self) -> None:  # pragma: no cover - needs torch + a real model
        """Download (if needed) and load the model + tokenizer into memory.

        Idempotent — a no-op once loaded. Called automatically by
        :meth:`generate` / :meth:`stream` / :meth:`chat`.

        Raises:
            ImportError: When the ``[genai]`` (or ``[genai-quant]``) extra
                is missing.
        """
        if self.is_loaded:
            return
        torch, transformers = _require_transformers()
        kwargs: dict[str, Any] = {
            "cache_dir": self.cache_dir,
            "token": self.hf_token,
        }
        if self.quantization is not None:
            bits = 8 if self.quantization is ModelDtype.INT8 else 4
            kwargs["quantization_config"] = transformers.BitsAndBytesConfig(
                **{f"load_in_{bits}bit": True},
            )
            kwargs["device_map"] = "auto"
        else:
            kwargs["torch_dtype"] = getattr(torch, self.dtype.value)
            kwargs["device_map"] = self.device if self.device != "cpu" else None

        self._tokenizer = transformers.AutoTokenizer.from_pretrained(
            self.model_id,
            cache_dir=self.cache_dir,
            token=self.hf_token,
        )
        self._model = transformers.AutoModelForCausalLM.from_pretrained(
            self.model_id,
            **kwargs,
        )
        if self.quantization is None and self.device == "cpu":
            self._model = self._model.to("cpu")
        self._touch()

    def unload(self) -> None:
        """Free the model and its memory (VRAM/RAM).

        Safe to call when not loaded. After this, the next generation call
        reloads the weights.
        """
        if self._model is None:
            return
        self._model = None
        self._tokenizer = None
        try:  # pragma: no cover - only meaningful with torch + CUDA
            import torch

            if torch.cuda.is_available():
                torch.cuda.empty_cache()
        except ImportError:
            pass

    def unload_if_idle(self) -> bool:
        """Unload the model when it has been idle past the threshold.

        Call periodically (e.g. from a ``@tq.interval`` task) to reclaim
        VRAM between bursts. A no-op when ``idle_unload_seconds`` is unset,
        the model isn't loaded, or it isn't idle enough yet.

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

    def _generate_sync(  # pragma: no cover - needs torch + a real model
        self,
        prompt: str,
        **kwargs: Any,
    ) -> str:
        """Run blocking generation and return the completion text."""
        self.load()
        inputs = self._tokenizer(prompt, return_tensors="pt").to(self._model.device)
        output = self._model.generate(**inputs, **self._gen_kwargs(kwargs))
        text = self._tokenizer.decode(
            output[0][inputs["input_ids"].shape[1] :],
            skip_special_tokens=True,
        )
        self._touch()
        return str(text)

    def _gen_kwargs(self, overrides: dict[str, Any]) -> dict[str, Any]:
        """Merge generation defaults with per-call overrides."""
        defaults: dict[str, Any] = {
            "max_new_tokens": 256,
            "temperature": 0.7,
            "top_p": 0.9,
            "do_sample": True,
        }
        defaults.update(overrides)
        return defaults

    async def generate(self, prompt: str, **kwargs: Any) -> str:
        """Generate a completion for ``prompt``.

        Runs the blocking model in a worker thread so the event loop stays
        free.

        Args:
            prompt (str): The input text.
            **kwargs (Any): Generation overrides (``max_new_tokens``,
                ``temperature``, ``top_p``, …) forwarded to
                ``model.generate``.

        Returns:
            str: The generated text (prompt stripped).
        """
        return await asyncio.to_thread(self._generate_sync, prompt, **kwargs)

    async def chat(self, messages: list[dict[str, str]], **kwargs: Any) -> str:
        """Generate a reply for a chat ``messages`` list.

        Applies the tokenizer's chat template (roles ``system`` / ``user``
        / ``assistant``) before generating.

        Args:
            messages (list[dict[str, str]]): Chat turns, each
                ``{"role": ..., "content": ...}``.
            **kwargs (Any): Generation overrides.

        Returns:
            str: The assistant reply.
        """
        return await asyncio.to_thread(self._chat_sync, messages, **kwargs)

    def _chat_sync(  # pragma: no cover - needs torch + a real model
        self,
        messages: list[dict[str, str]],
        **kwargs: Any,
    ) -> str:
        """Blocking chat generation via the tokenizer chat template."""
        self.load()
        prompt = self._tokenizer.apply_chat_template(
            messages,
            tokenize=False,
            add_generation_prompt=True,
        )
        return self._generate_sync(prompt, **kwargs)

    async def stream(  # pragma: no cover - needs torch + a real model
        self,
        prompt: str,
        **kwargs: Any,
    ) -> AsyncIterator[str]:
        """Stream the completion token by token.

        Args:
            prompt (str): The input text.
            **kwargs (Any): Generation overrides.

        Yields:
            str: Text pieces as they are produced.
        """
        self.load()
        _torch, transformers = _require_transformers()
        streamer = transformers.TextIteratorStreamer(
            self._tokenizer,
            skip_prompt=True,
            skip_special_tokens=True,
        )
        inputs = self._tokenizer(prompt, return_tensors="pt").to(self._model.device)
        gen_kwargs = {**self._gen_kwargs(kwargs), **inputs, "streamer": streamer}

        import threading

        thread = threading.Thread(target=self._model.generate, kwargs=gen_kwargs)
        thread.start()
        try:
            for piece in streamer:
                if piece:
                    yield piece
                await asyncio.sleep(0)
        finally:
            thread.join()
            self._touch()


__all__: list[str] = [
    "TextGenerator",
    "auto_dtype_name",
    "resolve_device",
]
