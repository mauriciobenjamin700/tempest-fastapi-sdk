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
import json
import re
import time
from collections.abc import AsyncIterator, Awaitable, Callable
from typing import Any, Protocol, runtime_checkable

from tempest_fastapi_sdk.genai.generation_cache import (
    AsyncGenerationCache,
    GenerationCache,
    cached_generate,
)
from tempest_fastapi_sdk.genai.hardware import probe_hardware
from tempest_fastapi_sdk.genai.hub import ModelRef
from tempest_fastapi_sdk.genai.metrics import GenAIMetrics
from tempest_fastapi_sdk.genai.schemas import (
    GenerationConfig,
    HardwareInfo,
    ModelDtype,
)
from tempest_fastapi_sdk.genai.structured import (
    StructuredT,
    build_prefix_allowed_tokens_fn,
    parse_structured,
)
from tempest_fastapi_sdk.genai.tracing import genai_span

_QUANTIZATIONS: frozenset[ModelDtype] = frozenset({ModelDtype.INT8, ModelDtype.INT4})

_TOOL_CALL_RE: re.Pattern[str] = re.compile(
    r"<tool_call>\s*(\{.*?\})\s*</tool_call>",
    re.DOTALL,
)


def _coerce_tool_call(raw: str) -> dict[str, Any] | None:
    """Parse one raw JSON tool-call object into the pipeline call shape.

    Accepts the two conventions instruct models emit — ``{"name", "arguments"}``
    (Qwen / Hermes) and ``{"name", "parameters"}`` (Llama) — and normalizes
    them to ``{"type": "function", "function": {"name", "arguments"}}`` so the
    result matches what :class:`OllamaGenerator.chat_with_tools` returns.

    Args:
        raw (str): A single JSON object as text.

    Returns:
        dict[str, Any] | None: The normalized call, or ``None`` when ``raw`` is
        not a JSON object carrying a ``name``.
    """
    try:
        obj: Any = json.loads(raw)
    except (ValueError, TypeError):
        return None
    if not isinstance(obj, dict):
        return None
    name = obj.get("name")
    if not name:
        return None
    arguments: Any = obj.get("arguments")
    if arguments is None:
        arguments = obj.get("parameters") or {}
    if isinstance(arguments, str):
        try:
            arguments = json.loads(arguments)
        except ValueError:
            arguments = {}
    return {"type": "function", "function": {"name": str(name), "arguments": arguments}}


def _parse_tool_calls(text: str) -> tuple[str, list[dict[str, Any]]]:
    """Split a model completion into clean content and any tool calls.

    Recognizes ``<tool_call>{...}</tool_call>`` blocks (Qwen / Hermes, one or
    more) and, failing that, a bare top-level JSON object (Llama-style). When
    no tool call is found the text is returned unchanged with an empty list.

    Args:
        text (str): The raw generated completion.

    Returns:
        tuple[str, list[dict[str, Any]]]: ``(content, tool_calls)`` where each
        call has the ``{"function": {"name", "arguments"}}`` shape.
    """
    matches = list(_TOOL_CALL_RE.finditer(text))
    if matches:
        content = _TOOL_CALL_RE.sub("", text).strip()
        calls = [call for m in matches if (call := _coerce_tool_call(m.group(1)))]
        return content, calls
    stripped = text.strip()
    if stripped.startswith("{"):
        call = _coerce_tool_call(stripped)
        if call is not None:
            return "", [call]
    return text, []


@runtime_checkable
class TextBackend(Protocol):
    """The text-generation surface consumers depend on.

    Both the ``torch``/``transformers``
    :class:`~tempest_fastapi_sdk.genai.text.TextGenerator` and the
    :class:`~tempest_fastapi_sdk.genai.ollama.OllamaGenerator` implement
    this protocol, so either can be handed to
    :func:`~tempest_fastapi_sdk.genai.make_genai_router` as the text
    backend. Implement these three methods to plug in any other engine
    (vLLM, TGI, a hosted API, …).
    """

    async def generate(
        self,
        prompt: str,
        *,
        config: GenerationConfig | None = ...,
        **kwargs: Any,
    ) -> str:
        """Return a completion for ``prompt``."""
        ...

    async def chat(
        self,
        messages: list[dict[str, str]],
        *,
        config: GenerationConfig | None = ...,
        **kwargs: Any,
    ) -> str:
        """Return a reply for a chat ``messages`` list.

        Args:
            messages (list[dict[str, str]]): The chat turns, oldest first.
            config (GenerationConfig | None): Generation parameters; ``None``
                uses the defaults.
            **kwargs (Any): Extra generation parameters, overriding ``config``.

        Returns:
            str: The generated completion.
        """
        ...

    def stream(
        self,
        prompt: str,
        *,
        config: GenerationConfig | None = ...,
        **kwargs: Any,
    ) -> AsyncIterator[str]:
        """Stream a completion for ``prompt`` piece by piece.

        Args:
            prompt (str): The prompt to complete.
            config (GenerationConfig | None): Generation parameters; ``None``
                uses the defaults.
            **kwargs (Any): Extra generation parameters, overriding ``config``.

        Returns:
            AsyncIterator[str]: The completion, streamed token by token.
        """
        ...


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
        source (ModelRef): The resolved weight identity (id, revision,
            cache, token, offline/remote-code flags) forwarded to every
            ``from_pretrained`` call.
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
        revision: str | None = None,
        local_files_only: bool = False,
        trust_remote_code: bool = False,
        idle_unload_seconds: float | None = None,
        hardware: HardwareInfo | None = None,
        generation_cache: GenerationCache | AsyncGenerationCache | None = None,
        metrics: GenAIMetrics | None = None,
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
            revision (str | None): Branch, tag or commit sha to load.
                ``None`` follows the Hub default, which moves when the
                author pushes; pin a sha (see
                :func:`~tempest_fastapi_sdk.genai.resolve_revision`) for a
                reproducible deployment.
            local_files_only (bool): Load from the cache without touching
                the network — what an air-gapped or deploy-frozen host
                wants.
            trust_remote_code (bool): Allow the repository's own Python to
                run at load time. Required by some architectures, and it
                executes code you did not review, so it stays opt-in.
            idle_unload_seconds (float | None): When set, :meth:`unload_if_idle`
                frees the model after this many idle seconds.
            hardware (HardwareInfo | None): Injected snapshot for device
                resolution (tests); probed when ``None``.
            generation_cache (GenerationCache | AsyncGenerationCache | None):
                Optional prompt→completion cache. Only **deterministic**
                generations (``do_sample=False`` / ``temperature=0``) are
                cached; sampling calls always run the model.
            metrics (GenAIMetrics | None): Optional Prometheus metrics;
                when set, ``generate`` / ``chat`` record request count and
                latency (op ``"generate"`` / ``"chat"``).

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
        self.source = ModelRef(
            model_id=model_id,
            revision=revision,
            cache_dir=cache_dir,
            token=hf_token,
            local_files_only=local_files_only,
            trust_remote_code=trust_remote_code,
        )
        self.idle_unload_seconds = idle_unload_seconds
        self.generation_cache = generation_cache
        self.metrics = metrics
        self._model: Any = None
        self._tokenizer: Any = None
        self._last_used: float = time.monotonic()

    def _key_params(
        self,
        config: GenerationConfig | None,
        overrides: dict[str, Any],
    ) -> dict[str, Any]:
        """Merge config + overrides into the parameters that key the cache."""
        params: dict[str, Any] = {}
        if config is not None:
            params.update(config.model_dump(exclude_none=True, exclude_unset=True))
        params.update(overrides)
        return params

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
        kwargs: dict[str, Any] = self.source.loader_kwargs()
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
            **self.source.loader_kwargs(),
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

    def _resolve_control(
        self,
        overrides: dict[str, Any],
        config: GenerationConfig | None,
    ) -> tuple[int | None, list[str]]:
        """Extract ``seed`` + ``stop`` strings, popping them out of ``overrides``.

        ``seed`` and ``stop`` are not ``model.generate`` keyword arguments, so
        they are removed from ``overrides`` here — before :meth:`_gen_kwargs`
        merges the rest — and returned for the caller to apply via
        ``transformers.set_seed`` and the ``stop_strings`` generation argument.
        Per-call overrides win over ``config``.

        Args:
            overrides (dict[str, Any]): Per-call keyword args; ``seed`` and
                ``stop`` are popped out in place when present.
            config (GenerationConfig | None): Typed config supplying the
                fallback ``seed`` / ``stop`` when the overrides omit them.

        Returns:
            tuple[int | None, list[str]]: The resolved ``(seed, stop)``.
        """
        seed: int | None = overrides.pop("seed", None)
        stop: list[str] | None = overrides.pop("stop", None)
        if config is not None:
            if seed is None:
                seed = config.seed
            if not stop:
                stop = list(config.stop)
        return seed, list(stop) if stop else []

    def _assemble_kwargs(
        self,
        overrides: dict[str, Any],
        config: GenerationConfig | None,
        stop: list[str],
        tokenizer: Any,
    ) -> dict[str, Any]:
        """Build the final ``model.generate`` kwargs, wiring stop strings.

        Args:
            overrides (dict[str, Any]): Per-call overrides (seed/stop already
                popped by :meth:`_resolve_control`).
            config (GenerationConfig | None): Typed config layered over defaults.
            stop (list[str]): Resolved stop strings; when non-empty, adds the
                ``stop_strings`` + ``tokenizer`` arguments (transformers >= 4.44
                ``StopStringCriteria``).
            tokenizer (Any): The tokenizer required alongside ``stop_strings``.

        Returns:
            dict[str, Any]: The merged generation kwargs.
        """
        gen = self._gen_kwargs(overrides, config)
        if stop:
            gen["stop_strings"] = stop
            gen["tokenizer"] = tokenizer
        return gen

    def _generate_sync(  # pragma: no cover - needs torch + a real model
        self,
        prompt: str,
        config: GenerationConfig | None,
        overrides: dict[str, Any],
    ) -> str:
        """Run blocking generation and return the completion text."""
        self.load()
        _torch, transformers = _require_transformers()
        seed, stop = self._resolve_control(overrides, config)
        if seed is not None:
            transformers.set_seed(seed)
        inputs = self._tokenizer(prompt, return_tensors="pt").to(self._model.device)
        output = self._model.generate(
            **inputs,
            **self._assemble_kwargs(overrides, config, stop, self._tokenizer),
        )
        text = self._tokenizer.decode(
            output[0][inputs["input_ids"].shape[1] :],
            skip_special_tokens=True,
        )
        self._touch()
        return str(text)

    def _gen_kwargs(
        self,
        overrides: dict[str, Any],
        config: GenerationConfig | None = None,
    ) -> dict[str, Any]:
        """Merge generation defaults with an optional config and overrides.

        Precedence (lowest to highest): built-in defaults, the set fields
        of ``config`` (a :class:`GenerationConfig`), then explicit
        per-call ``overrides``.

        Args:
            overrides (dict[str, Any]): Explicit per-call keyword args.
            config (GenerationConfig | None): A typed config whose set
                fields layer over the defaults.

        Returns:
            dict[str, Any]: The merged generation kwargs.
        """
        merged: dict[str, Any] = {
            "max_new_tokens": 256,
            "temperature": 0.7,
            "top_p": 0.9,
            "do_sample": True,
        }
        if config is not None:
            merged.update(config.to_generate_kwargs())
        merged.update(overrides)
        return merged

    async def generate(
        self,
        prompt: str,
        *,
        config: GenerationConfig | None = None,
        **kwargs: Any,
    ) -> str:
        """Generate a completion for ``prompt``.

        Runs the blocking model in a worker thread so the event loop stays
        free.

        Args:
            prompt (str): The input text.
            config (GenerationConfig | None): Typed generation parameters;
                its set fields layer over the defaults.
            **kwargs (Any): Generation overrides (``max_new_tokens``,
                ``temperature``, ``top_p``, …) forwarded to
                ``model.generate``; these win over ``config``.

        Returns:
            str: The generated text (prompt stripped).
        """
        return await self._tracked(
            "generate",
            lambda: cached_generate(
                self.generation_cache,
                self.model_id,
                prompt,
                self._key_params(config, kwargs),
                lambda: asyncio.to_thread(
                    self._generate_sync, prompt, config, dict(kwargs)
                ),
            ),
        )

    async def _tracked(
        self,
        op: str,
        run: Callable[[], Awaitable[str]],
    ) -> str:
        """Run ``run`` inside an OTel span, recording metrics when set.

        The span is emitted whenever an OpenTelemetry provider is configured
        (see :class:`~tempest_fastapi_sdk.genai.tracing.genai_span`); metrics
        are recorded only when a :class:`GenAIMetrics` was injected. Both are
        no-ops otherwise, so the hot path stays free.
        """
        async with genai_span(op, self.model_id):
            if self.metrics is None:
                return await run()
            async with self.metrics.track(self.model_id, op):
                return await run()

    async def chat(
        self,
        messages: list[dict[str, str]],
        *,
        config: GenerationConfig | None = None,
        **kwargs: Any,
    ) -> str:
        """Generate a reply for a chat ``messages`` list.

        Applies the tokenizer's chat template (roles ``system`` / ``user``
        / ``assistant``) before generating. Honors the generation cache
        (deterministic calls) and metrics like :meth:`generate`.

        Args:
            messages (list[dict[str, str]]): Chat turns, each
                ``{"role": ..., "content": ...}``.
            config (GenerationConfig | None): Typed generation parameters.
            **kwargs (Any): Generation overrides (win over ``config``).

        Returns:
            str: The assistant reply.
        """
        cache_prompt = json.dumps(messages, sort_keys=True, default=str)
        return await self._tracked(
            "chat",
            lambda: cached_generate(
                self.generation_cache,
                self.model_id,
                cache_prompt,
                self._key_params(config, kwargs),
                lambda: asyncio.to_thread(
                    self._chat_sync, messages, config, dict(kwargs)
                ),
            ),
        )

    def _chat_sync(  # pragma: no cover - needs torch + a real model
        self,
        messages: list[dict[str, str]],
        config: GenerationConfig | None,
        overrides: dict[str, Any],
    ) -> str:
        """Blocking chat generation via the tokenizer chat template."""
        self.load()
        prompt = self._tokenizer.apply_chat_template(
            messages,
            tokenize=False,
            add_generation_prompt=True,
        )
        return self._generate_sync(prompt, config, overrides)

    async def chat_with_tools(
        self,
        messages: list[dict[str, Any]],
        tools: list[dict[str, Any]],
        *,
        config: GenerationConfig | None = None,
        **kwargs: Any,
    ) -> dict[str, Any]:
        """Generate a chat reply with tool-calling enabled.

        Renders the tokenizer's chat template with the tool specs (transformers
        >= 4.44 ``apply_chat_template(tools=...)``), generates, then parses any
        tool call the model emitted out of the completion. The return shape
        mirrors :meth:`OllamaGenerator.chat_with_tools`, so the same
        :class:`~tempest_fastapi_sdk.genai.pipeline.AIChatPipeline` tool loop
        drives either backend.

        Args:
            messages (list[dict[str, Any]]): Chat turns (a turn may carry
                ``tool_calls`` or be a ``{"role": "tool", ...}`` result).
            tools (list[dict[str, Any]]): Tool specs in the
                ``{"type": "function", "function": {...}}`` shape (as produced
                by :meth:`~tempest_fastapi_sdk.genai.pipeline.Tool.to_spec`).
            config (GenerationConfig | None): Typed generation parameters.
            **kwargs (Any): Generation overrides (win over ``config``).

        Returns:
            dict[str, Any]: ``{"content": str, "tool_calls": list}`` where each
            call has the ``{"function": {"name", "arguments"}}`` shape;
            ``tool_calls`` is empty when the model returned plain text.
        """
        return await asyncio.to_thread(
            self._chat_with_tools_sync,
            messages,
            tools,
            config,
            kwargs,
        )

    def _chat_with_tools_sync(  # pragma: no cover - needs torch + a real model
        self,
        messages: list[dict[str, Any]],
        tools: list[dict[str, Any]],
        config: GenerationConfig | None,
        overrides: dict[str, Any],
    ) -> dict[str, Any]:
        """Blocking tool-calling generation via the tokenizer chat template."""
        self.load()
        prompt = self._tokenizer.apply_chat_template(
            messages,
            tools=tools,
            tokenize=False,
            add_generation_prompt=True,
        )
        text = self._generate_sync(prompt, config, overrides)
        content, tool_calls = _parse_tool_calls(text)
        return {"content": content, "tool_calls": tool_calls}

    async def generate_structured(
        self,
        prompt: str,
        schema: type[StructuredT],
        *,
        config: GenerationConfig | None = None,
        constrained: bool = True,
        **kwargs: Any,
    ) -> StructuredT:
        """Generate a completion constrained to a Pydantic ``schema``.

        When ``constrained`` is ``True`` (default) the generation is bound by a
        ``lm-format-enforcer`` token filter (``[genai-structured]`` extra) so
        the model can only emit schema-valid JSON; the result is then parsed
        into an instance of ``schema``. Set ``constrained=False`` for
        best-effort parsing without the extra (the model may still stray, in
        which case parsing raises).

        Args:
            prompt (str): The input text (instruct the model to answer as JSON).
            schema (type[StructuredT]): The Pydantic model to produce.
            config (GenerationConfig | None): Typed generation parameters.
            constrained (bool): Enforce the schema during decoding (needs the
                ``[genai-structured]`` extra) or only parse afterwards.
            **kwargs (Any): Generation overrides (win over ``config``).

        Returns:
            StructuredT: The validated instance.

        Raises:
            ImportError: When ``constrained`` is ``True`` and the
                ``[genai-structured]`` extra is missing.
            ValueError: When the output carries no JSON object.
            pydantic.ValidationError: When the JSON fails ``schema`` validation.
        """
        return await asyncio.to_thread(
            self._generate_structured_sync,
            prompt,
            schema,
            config,
            kwargs,
            constrained,
        )

    def _generate_structured_sync(  # pragma: no cover - needs torch + a real model
        self,
        prompt: str,
        schema: type[StructuredT],
        config: GenerationConfig | None,
        overrides: dict[str, Any],
        constrained: bool,
    ) -> StructuredT:
        """Blocking schema-constrained generation."""
        self.load()
        call_overrides = dict(overrides)
        if constrained:
            call_overrides["prefix_allowed_tokens_fn"] = build_prefix_allowed_tokens_fn(
                self._tokenizer,
                schema,
            )
        text = self._generate_sync(prompt, config, call_overrides)
        return parse_structured(text, schema)

    async def stream(  # pragma: no cover - needs torch + a real model
        self,
        prompt: str,
        *,
        config: GenerationConfig | None = None,
        **kwargs: Any,
    ) -> AsyncIterator[str]:
        """Stream the completion token by token.

        Args:
            prompt (str): The input text.
            config (GenerationConfig | None): Typed generation parameters.
            **kwargs (Any): Generation overrides (win over ``config``).

        Yields:
            str: Text pieces as they are produced.
        """
        self.load()
        _torch, transformers = _require_transformers()
        seed, stop = self._resolve_control(kwargs, config)
        if seed is not None:
            transformers.set_seed(seed)
        streamer = transformers.TextIteratorStreamer(
            self._tokenizer,
            skip_prompt=True,
            skip_special_tokens=True,
        )
        inputs = self._tokenizer(prompt, return_tensors="pt").to(self._model.device)
        gen_kwargs = {
            **self._assemble_kwargs(kwargs, config, stop, self._tokenizer),
            **inputs,
            "streamer": streamer,
        }

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
    "TextBackend",
    "TextGenerator",
    "auto_dtype_name",
    "resolve_device",
]
