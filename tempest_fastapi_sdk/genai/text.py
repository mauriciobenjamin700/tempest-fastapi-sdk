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
import contextlib
import json
import re
import threading
from collections.abc import AsyncIterator, Awaitable, Callable
from typing import Any, Protocol, runtime_checkable

from tempest_fastapi_sdk.genai._lifecycle import ModelLifecycle
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
    precision_kwarg,
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


def _stop_criteria(transformers: Any, stop_event: threading.Event) -> Any:
    """Build a stopping criterion that watches a thread event.

    ``model.generate`` is a blocking call inside a worker thread, and
    Python cannot interrupt a thread from outside. What it does do is ask
    its stopping criteria after every token — so an event checked there is
    the only way a decision made on the event loop reaches a generation
    already in flight.

    Args:
        transformers (Any): The imported ``transformers`` module.
        stop_event (threading.Event): Set to stop decoding.

    Returns:
        Any: A ``StoppingCriteriaList`` to pass to ``model.generate``.
    """

    class _EventCriteria(transformers.StoppingCriteria):  # type: ignore[misc]
        """Stops decoding once the event is set."""

        def __call__(self, input_ids: Any, scores: Any, **kwargs: Any) -> bool:
            """Answer whether decoding should stop now.

            Args:
                input_ids (Any): Tokens produced so far; unused.
                scores (Any): Current logits; unused.
                **kwargs (Any): Whatever transformers passes along.

            Returns:
                bool: ``True`` once the event is set.
            """
            return stop_event.is_set()

    return transformers.StoppingCriteriaList([_EventCriteria()])


class _StreamEnd:
    """Marks the end of a stream on the consumer's queue."""


_STREAM_END: _StreamEnd = _StreamEnd()


def _consume_result(task: asyncio.Future[None]) -> None:
    """Retrieve a finished producer's outcome so asyncio does not log it.

    Attached whenever the consumer leaves. On the normal path the exception
    was already raised through ``await producer``; when the consumer left
    early the generation is being stopped on purpose, so an exception it
    raises on the way out has no reader and would otherwise surface as
    "Task exception was never retrieved".

    Args:
        task (asyncio.Future[None]): The producer future.
    """
    if not task.cancelled():
        task.exception()


def _callback_streamer(
    transformers: Any,
    tokenizer: Any,
    emit: Callable[[str], None],
    stop_event: threading.Event,
) -> Any:
    """Build a streamer that pushes finalized text to ``emit``.

    ``TextIteratorStreamer`` hands text over through a blocking
    ``queue.Queue``, and iterating it from a coroutine stalls the event
    loop between tokens. Measured with ``Qwen/Qwen2.5-0.5B-Instruct`` on an
    RTX 4070 Ti SUPER, 180 pieces from a warm model, against a ticker that
    sleeps 5 ms: 77 loop ticks in 3.67 s through the blocking iterator,
    728 to 770 ticks in 3.8 to 4.0 s through this callback. Subclassing
    ``TextStreamer`` keeps its detokenization (word boundaries, CJK, the
    prompt skip) and replaces only the delivery, which here is a callback
    the worker thread calls directly.

    Args:
        transformers (Any): The imported ``transformers`` module.
        tokenizer (Any): The tokenizer that decodes the tokens.
        emit (Callable[[str], None]): Receives each finalized piece.
        stop_event (threading.Event): Once set, pieces are no longer
            delivered.

    Returns:
        Any: A ``TextStreamer`` subclass instance.
    """

    class _CallbackStreamer(transformers.TextStreamer):  # type: ignore[misc]
        """Delivers text to a callback instead of printing it."""

        def on_finalized_text(self, text: str, stream_end: bool = False) -> None:
            """Forward one finalized piece unless the stream was stopped.

            Args:
                text (str): The decoded text.
                stream_end (bool): Whether this is the last piece; unused.
            """
            if text and not stop_event.is_set():
                emit(text)

    return _CallbackStreamer(tokenizer, skip_prompt=True, skip_special_tokens=True)


class GenerationStoppedError(RuntimeError):
    """Raised when a generation was stopped through its ``stop_event``.

    Local generation runs in a worker thread, and a thread cannot be
    cancelled from outside — so cancelling the awaiting coroutine leaves
    the GPU producing tokens for a reply nobody will read. The
    ``stop_event`` is how the decision reaches the thread, and this is
    what the thread raises once it has honoured it: the work stopped
    because it was told to, not because it went wrong.
    """


@runtime_checkable
class StructuredTextBackend(Protocol):
    """A backend that answers with a validated schema instead of prose.

    Both :class:`TextGenerator` and
    :class:`~tempest_fastapi_sdk.genai.ollama.OllamaGenerator` implement
    it, which is the point: a service that reads documents into schemas
    can be typed against this and run on a local model or on a daemon
    without a line changing at the call site. The messages list rather
    than a prompt string is part of the contract — extraction quality
    depends on the instruction sitting in its own ``system`` turn.
    """

    async def chat_structured(
        self,
        messages: list[dict[str, Any]],
        schema: type[StructuredT],
        *,
        config: GenerationConfig | None = ...,
        **kwargs: Any,
    ) -> StructuredT:
        """Return a reply validated against ``schema``.

        Args:
            messages (list[dict[str, Any]]): Chat turns, each
                ``{"role": ..., "content": ...}``.
            schema (type[StructuredT]): The Pydantic model to produce.
            config (GenerationConfig | None): Generation parameters.
            **kwargs (Any): Extra generation parameters.

        Returns:
            StructuredT: The validated instance.
        """
        ...


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


def _sampling_warpers(
    transformers: Any,
    generation_config: Any,
    device: Any,
) -> list[Any]:
    """Build the sampling warpers ``generate`` applies for ``generation_config``.

    Ported from transformers' ``GenerationMixin._get_logits_processor``
    (the ``if generation_config.do_sample:`` block, 4.57.6 and 5.17.0),
    restricted to multinomial sampling (``num_beams == 1``, so
    ``min_tokens_to_keep`` is always 1). Same classes, same conditions, same
    order; ``TopHLogitsWarper`` only exists from 5.x on, so it is added only
    when both the class and the ``top_h`` field are present.
    ``tests/genai/test_text_seed.py`` compares this list against the one the
    installed transformers builds, so upstream drift fails there instead of
    silently changing the seeded distribution.

    Args:
        transformers (Any): The imported ``transformers`` module.
        generation_config (Any): The effective ``GenerationConfig`` of the
            call, as ``generate`` resolves it (see :func:`_apply_seed`).
        device (Any): The device the logits live on (``EtaLogitsWarper``
            keeps a tensor there).

    Returns:
        list[Any]: The warpers, in the order ``generate`` applies them.
    """
    gc = generation_config
    warpers: list[Any] = []
    if gc.temperature is not None and gc.temperature != 1.0:
        warpers.append(transformers.TemperatureLogitsWarper(gc.temperature))
    top_h: Any = getattr(gc, "top_h", None)
    if top_h is not None and hasattr(transformers, "TopHLogitsWarper"):
        warpers.append(transformers.TopHLogitsWarper(top_h=top_h))
    if gc.top_k is not None and gc.top_k != 0:
        warpers.append(
            transformers.TopKLogitsWarper(top_k=gc.top_k, min_tokens_to_keep=1),
        )
    if gc.top_p is not None and gc.top_p < 1.0:
        warpers.append(
            transformers.TopPLogitsWarper(top_p=gc.top_p, min_tokens_to_keep=1),
        )
    if gc.min_p is not None:
        warpers.append(
            transformers.MinPLogitsWarper(min_p=gc.min_p, min_tokens_to_keep=1),
        )
    if gc.typical_p is not None and gc.typical_p < 1.0:
        warpers.append(
            transformers.TypicalLogitsWarper(mass=gc.typical_p, min_tokens_to_keep=1),
        )
    if gc.epsilon_cutoff is not None and 0.0 < gc.epsilon_cutoff < 1.0:
        warpers.append(
            transformers.EpsilonLogitsWarper(
                epsilon=gc.epsilon_cutoff,
                min_tokens_to_keep=1,
            ),
        )
    if gc.eta_cutoff is not None and 0.0 < gc.eta_cutoff < 1.0:
        warpers.append(
            transformers.EtaLogitsWarper(
                epsilon=gc.eta_cutoff,
                min_tokens_to_keep=1,
                device=device,
            ),
        )
    return warpers


class _SeededSampler:
    """Logits processor that draws the next token from a private generator.

    ``model.generate`` takes no per-call ``torch.Generator`` (neither
    transformers 4.57.6 nor 5.17.0 has one) and samples with the
    process-wide RNG. This processor makes a seeded call independent of it:
    it applies the call's sampling warpers itself, draws the token with
    ``torch.multinomial(..., generator=own)``, and returns scores that are
    ``0`` for that token and ``-inf`` everywhere else. ``generate`` then
    runs its own warpers over that one-hot row (they keep the only finite
    entry) and its global ``multinomial`` can only pick the same token — it
    still advances the global RNG, but no longer decides anything.

    Custom processors run after the built-in ones that are not warpers
    (repetition penalty, ``prefix_allowed_tokens_fn`` constraints, …), so
    the distribution sampled here is the one ``generate`` would sample.

    Attributes:
        seed (int): The seed the private generator starts from.
    """

    def __init__(self, torch: Any, seed: int, warpers: list[Any]) -> None:
        """Initialize the sampler.

        Args:
            torch (Any): The imported ``torch`` module.
            seed (int): Seed for the private generator.
            warpers (list[Any]): The sampling warpers from
                :func:`_sampling_warpers`, applied before the draw.
        """
        self.seed: int = seed
        self._torch: Any = torch
        self._warpers: list[Any] = warpers
        self._generator: Any = None

    def __call__(self, input_ids: Any, scores: Any) -> Any:
        """Sample one token per row and return one-hot scores for it.

        The generator is created on the first call, on the device of the
        logits, so a CUDA model samples with a CUDA generator.

        Args:
            input_ids (Any): The token ids so far (``LongTensor``).
            scores (Any): The next-token logits (``FloatTensor``).

        Returns:
            Any: Scores with ``0`` at the sampled token and ``-inf``
            elsewhere.
        """
        torch = self._torch
        for warper in self._warpers:
            scores = warper(input_ids, scores)
        if self._generator is None:
            self._generator = torch.Generator(device=scores.device)
            self._generator.manual_seed(self.seed)
        probs = torch.nn.functional.softmax(scores, dim=-1)
        tokens = torch.multinomial(probs, num_samples=1, generator=self._generator)
        one_hot = torch.full_like(scores, float("-inf"))
        return one_hot.scatter(-1, tokens, 0.0)


def _resolve_control(
    overrides: dict[str, Any],
    config: GenerationConfig | None,
) -> tuple[int | None, list[str]]:
    """Extract ``seed`` + ``stop`` strings, popping them out of ``overrides``.

    ``seed`` and ``stop`` are not ``model.generate`` keyword arguments —
    ``transformers`` refuses an unknown one with ``ValueError`` — so they
    are removed from ``overrides`` here, before the rest is merged into the
    generation kwargs, and returned for the caller to apply via
    :func:`_apply_seed` and :func:`_apply_stop_strings`. Per-call overrides
    win over ``config``.

    Shared by :class:`TextGenerator` and
    :class:`~tempest_fastapi_sdk.genai.vision_text.VisionTextGenerator`.

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


def _apply_stop_strings(
    gen_kwargs: dict[str, Any],
    stop: list[str],
    tokenizer: Any,
) -> None:
    """Wire resolved stop strings into the ``model.generate`` kwargs.

    Adds the ``stop_strings`` + ``tokenizer`` pair that ``transformers``
    (>= 4.44) turns into a ``StopStringCriteria``. Decoding ends on the
    token that completes a stop string, and that string stays in the
    decoded text — the output is not trimmed.

    Shared by :class:`TextGenerator` and
    :class:`~tempest_fastapi_sdk.genai.vision_text.VisionTextGenerator`.

    Args:
        gen_kwargs (dict[str, Any]): The ``model.generate`` kwargs,
            updated in place.
        stop (list[str]): Resolved stop strings; empty is a no-op.
        tokenizer (Any): The tokenizer ``StopStringCriteria`` reads the
            vocabulary from (a processor's ``tokenizer`` for a VLM).
    """
    if stop:
        gen_kwargs["stop_strings"] = stop
        gen_kwargs["tokenizer"] = tokenizer


def _apply_seed(
    torch: Any,
    transformers: Any,
    model: Any,
    seed: int | None,
    gen_kwargs: dict[str, Any],
) -> None:
    """Make a seeded call reproducible without touching the global RNG.

    Resolves the call's effective ``GenerationConfig`` with the model's own
    ``_prepare_generation_config`` — the step ``generate`` runs first, so
    the resolution matches it on every version: on 4.57 the model's
    defaults updated with ``gen_kwargs``, on 5.x the global defaults
    (``top_k=50`` …) filling what the model's config leaves unset. It is a
    private method; if a future transformers drops or reshapes it, the call
    falls back to ``transformers.set_seed`` (reproducible serially, not
    under concurrency) instead of sampling from a distribution that no
    longer matches ``generate``'s. For plain multinomial sampling
    it appends a :class:`_SeededSampler` to the call's
    ``logits_processor``, so the draw comes from a generator private to
    this call: two concurrent calls with the same seed give the same text
    as one call run alone, and unseeded calls in the same process keep
    drawing from an RNG nobody reset. Greedy decoding needs no seed and is
    left alone.

    Every other sampling mode (beam sampling, assisted/prompt-lookup
    decoding, DoLa) samples in code a logits processor cannot steer — a
    one-hot row would break beam sampling's draw without replacement — so
    those keep ``transformers.set_seed``, which reseeds the process-wide
    RNGs and is reproducible only while no other sampling generation runs
    at the same time.

    Shared by :class:`TextGenerator` and
    :class:`~tempest_fastapi_sdk.genai.vision_text.VisionTextGenerator`.

    Args:
        torch (Any): The imported ``torch`` module.
        transformers (Any): The imported ``transformers`` module.
        model (Any): The loaded model whose ``generate`` will run.
        seed (int | None): The resolved seed; ``None`` is a no-op.
        gen_kwargs (dict[str, Any]): The ``model.generate`` kwargs,
            updated in place.
    """
    if seed is None:
        return
    try:
        effective: Any = model._prepare_generation_config(None, **gen_kwargs)[0]
    except (AttributeError, TypeError):
        transformers.set_seed(seed)
        return
    if not effective.do_sample:
        return
    mode: Any = effective.get_generation_mode(gen_kwargs.get("assistant_model"))
    if mode != transformers.generation.GenerationMode.SAMPLE:
        transformers.set_seed(seed)
        return
    sampler = _SeededSampler(
        torch,
        seed,
        _sampling_warpers(transformers, effective, model.device),
    )
    processors: list[Any] = list(gen_kwargs.get("logits_processor") or [])
    gen_kwargs["logits_processor"] = transformers.LogitsProcessorList(
        [*processors, sampler],
    )


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
        local_files_only: bool | None = None,
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
            revision (str | None): Branch, tag or commit sha to load.
                ``None`` follows the Hub default, which moves when the
                author pushes; pin a sha (see
                :func:`~tempest_fastapi_sdk.genai.resolve_revision`) for a
                reproducible deployment.
            local_files_only (bool | None): Load from the cache without
                touching the network — what an air-gapped or deploy-frozen
                host wants. ``None`` (the default) takes ``GENAI_OFFLINE``
                from the environment; passing the argument overrides it.
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
        self._lifecycle = ModelLifecycle(
            build=self._build,
            release=self._release,
            is_loaded=lambda: self._model is not None,
        )

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

    def _cache_identity(self) -> dict[str, Any]:
        """Return the weight identity beyond ``model_id`` that keys the cache.

        Two generators of the same model id at a different ``revision`` or
        ``quantization`` produce different text, so neither may answer from
        the other's cached completions.

        Returns:
            dict[str, Any]: ``revision`` and ``quantization`` (``None`` when
            unset, which the key builder ignores).
        """
        return {
            "revision": self.source.revision,
            "quantization": None
            if self.quantization is None
            else self.quantization.value,
        }

    @property
    def is_loaded(self) -> bool:
        """Return ``True`` once the weights are in memory.

        Returns:
            bool: Whether :meth:`load` has run without a later :meth:`unload`.
        """
        return self._model is not None

    @property
    def seconds_idle(self) -> float:
        """Return seconds since the model was last in use.

        A generation in flight counts as use for its whole duration, so
        this reads ``0.0`` while one runs — a long generation is never
        mistaken for an idle model.

        Returns:
            float: Idle time in seconds.
        """
        return self._lifecycle.seconds_idle()

    def load(self) -> None:
        """Download (if needed) and load the model + tokenizer into memory.

        Idempotent — a no-op once loaded. Called automatically by
        :meth:`generate` / :meth:`stream` / :meth:`chat`. Safe to call from
        several threads at once: concurrent callers on a cold instance
        wait for one build instead of each running ``from_pretrained``.
        It blocks for the whole download and build, so call it through
        ``asyncio.to_thread`` from async code.

        Raises:
            ImportError: When the ``[genai]`` (or ``[genai-quant]``) extra
                is missing.
        """
        self._lifecycle.load()

    def _build(self) -> None:
        """Load the tokenizer and weights; called once, under the load lock.

        Raises:
            ImportError: When the ``[genai]`` (or ``[genai-quant]``) extra
                is missing.
        """
        torch, transformers = _require_transformers()
        kwargs: dict[str, Any] = self.source.loader_kwargs()
        if self.quantization is not None:
            bits = 8 if self.quantization is ModelDtype.INT8 else 4
            kwargs["quantization_config"] = transformers.BitsAndBytesConfig(
                **{f"load_in_{bits}bit": True},
            )
            kwargs["device_map"] = "auto"
        else:
            kwargs.update(precision_kwarg(getattr(torch, self.dtype.value)))
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

    def unload(self) -> None:
        """Free the model and its memory (VRAM/RAM).

        Safe to call when not loaded. After this, the next generation call
        reloads the weights. While generations are in flight the release
        waits for them: the last one to finish drops the weights, so a
        running call never has its model freed underneath it.
        """
        self._lifecycle.unload()

    def _release(self) -> None:
        """Drop the weights and tokenizer and return cached CUDA memory."""
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
        the model isn't loaded, a generation is in flight, or it isn't
        idle enough yet.

        Returns:
            bool: ``True`` when it unloaded, ``False`` otherwise.
        """
        return self._lifecycle.unload_if_idle(self.idle_unload_seconds)

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
                popped by :func:`_resolve_control`).
            config (GenerationConfig | None): Typed config layered over defaults.
            stop (list[str]): Resolved stop strings, wired by
                :func:`_apply_stop_strings`.
            tokenizer (Any): The tokenizer required alongside ``stop_strings``.

        Returns:
            dict[str, Any]: The merged generation kwargs.
        """
        gen = self._gen_kwargs(overrides, config)
        _apply_stop_strings(gen, stop, tokenizer)
        return gen

    def _generate_sync(
        self,
        prompt: str,
        config: GenerationConfig | None,
        overrides: dict[str, Any],
        stop_event: threading.Event | None = None,
    ) -> str:
        """Run blocking generation and return the completion text.

        The whole call runs inside the lifecycle's ``use()`` block, which
        keeps the weights resident until the text is decoded.

        ``seed`` is applied by :func:`_apply_seed`: plain sampling draws
        from a private per-call generator, so concurrent calls do not
        disturb each other.

        Raises:
            GenerationStoppedError: When ``stop_event`` was set while the
                model was decoding.
        """
        with self._lifecycle.use():
            torch, transformers = _require_transformers()
            seed, stop = _resolve_control(overrides, config)
            inputs = self._tokenizer(prompt, return_tensors="pt").to(
                self._model.device,
            )
            gen_kwargs = self._assemble_kwargs(overrides, config, stop, self._tokenizer)
            _apply_seed(torch, transformers, self._model, seed, gen_kwargs)
            if stop_event is not None:
                gen_kwargs["stopping_criteria"] = _stop_criteria(
                    transformers,
                    stop_event,
                )
            output = self._model.generate(**inputs, **gen_kwargs)
            if stop_event is not None and stop_event.is_set():
                raise GenerationStoppedError(
                    f"generation with {self.model_id} was stopped",
                )
            text = self._tokenizer.decode(
                output[0][inputs["input_ids"].shape[1] :],
                skip_special_tokens=True,
            )
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
        stop_event: threading.Event | None = None,
        **kwargs: Any,
    ) -> str:
        """Generate a completion for ``prompt``.

        Runs the blocking model in a worker thread so the event loop stays
        free — which is also why stopping it needs ``stop_event``:
        cancelling the coroutine that awaits this leaves the thread
        decoding, and the GPU busy, for a reply nobody will read.

        Args:
            prompt (str): The input text.
            config (GenerationConfig | None): Typed generation parameters;
                its set fields layer over the defaults.
            stop_event (threading.Event | None): Set it to stop decoding
                at the next token. Pair it with
                :func:`~tempest_fastapi_sdk.tasks.run_cancellable`, which
                sets it for you when the work is cancelled.
            **kwargs (Any): Generation overrides (``max_new_tokens``,
                ``temperature``, ``top_p``, …) forwarded to
                ``model.generate``; these win over ``config``.

        Returns:
            str: The generated text (prompt stripped).

        Raises:
            GenerationStoppedError: When ``stop_event`` was set mid-flight.
        """
        return await self._tracked(
            "generate",
            lambda: cached_generate(
                self.generation_cache,
                self.model_id,
                prompt,
                self._key_params(config, kwargs),
                lambda: asyncio.to_thread(
                    self._generate_sync, prompt, config, dict(kwargs), stop_event
                ),
                identity=self._cache_identity(),
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
        stop_event: threading.Event | None = None,
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
            stop_event (threading.Event | None): Set it to stop decoding
                at the next token.
            **kwargs (Any): Generation overrides (win over ``config``).

        Returns:
            str: The assistant reply.

        Raises:
            GenerationStoppedError: When ``stop_event`` was set mid-flight.
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
                    self._chat_sync, messages, config, dict(kwargs), stop_event
                ),
                operation="chat",
                identity=self._cache_identity(),
            ),
        )

    def _chat_sync(  # pragma: no cover - needs torch + a real model
        self,
        messages: list[dict[str, str]],
        config: GenerationConfig | None,
        overrides: dict[str, Any],
        stop_event: threading.Event | None = None,
    ) -> str:
        """Blocking chat generation via the tokenizer chat template."""
        with self._lifecycle.use():
            prompt = self._chat_prompt(messages)
            return self._generate_sync(prompt, config, overrides, stop_event)

    def _chat_prompt(  # pragma: no cover - needs torch + a real model
        self,
        messages: list[dict[str, Any]],
    ) -> str:
        """Render chat turns into the model's own prompt format.

        Args:
            messages (list[dict[str, Any]]): The chat turns.

        Returns:
            str: The rendered prompt, ready for generation.
        """
        return str(
            self._tokenizer.apply_chat_template(
                messages,
                tokenize=False,
                add_generation_prompt=True,
            ),
        )

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
        with self._lifecycle.use():
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
        stop_event: threading.Event | None = None,
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
            stop_event (threading.Event | None): Set it to stop decoding at
                the next token.
            **kwargs (Any): Generation overrides (win over ``config``).

        Returns:
            StructuredT: The validated instance.

        Raises:
            ImportError: When ``constrained`` is ``True`` and the
                ``[genai-structured]`` extra is missing.
            ValueError: When the output carries no JSON object.
            GenerationStoppedError: When ``stop_event`` was set mid-flight.
            pydantic.ValidationError: When the JSON fails ``schema`` validation.
        """
        return await asyncio.to_thread(
            self._generate_structured_sync,
            prompt,
            schema,
            config,
            kwargs,
            constrained,
            stop_event,
        )

    async def chat_structured(
        self,
        messages: list[dict[str, Any]],
        schema: type[StructuredT],
        *,
        config: GenerationConfig | None = None,
        constrained: bool = True,
        stop_event: threading.Event | None = None,
        **kwargs: Any,
    ) -> StructuredT:
        """Generate a chat reply constrained to a Pydantic ``schema``.

        The same call
        :meth:`~tempest_fastapi_sdk.genai.ollama.OllamaGenerator.chat_structured`
        answers, so a service that reads documents into schemas runs on a
        local model or on a daemon without a line changing at the call
        site — see :class:`StructuredTextBackend`. The turns matter:
        concatenating a long instruction ahead of a long document makes a
        model start answering *with* the document, while the same
        instruction in its own ``system`` turn is respected. Sharing that
        contract across backends is what makes the two swappable in
        practice rather than only in type.

        Where the two differ is how the schema is enforced. The daemon
        takes a JSON schema and constrains decoding itself; here the
        constraint is a ``lm-format-enforcer`` token filter
        (``[genai-structured]`` extra), and both paths end in the same
        :func:`~tempest_fastapi_sdk.genai.structured.parse_structured`.

        Args:
            messages (list[dict[str, Any]]): Chat turns, each
                ``{"role": ..., "content": ...}``. Put the instruction in
                a ``system`` turn and the content being read in ``user``.
            schema (type[StructuredT]): The Pydantic model to produce.
            config (GenerationConfig | None): Typed generation parameters.
            constrained (bool): Enforce the schema during decoding (needs
                the ``[genai-structured]`` extra) or only parse afterwards.
            stop_event (threading.Event | None): Set it to stop decoding at
                the next token.
            **kwargs (Any): Generation overrides (win over ``config``).

        Returns:
            StructuredT: The validated instance.

        Raises:
            ImportError: When ``constrained`` is ``True`` and the
                ``[genai-structured]`` extra is missing.
            ValueError: When the output carries no JSON object.
            GenerationStoppedError: When ``stop_event`` was set mid-flight.
            pydantic.ValidationError: When the JSON fails ``schema``
                validation.
        """
        return await asyncio.to_thread(
            self._chat_structured_sync,
            messages,
            schema,
            config,
            kwargs,
            constrained,
            stop_event,
        )

    def _chat_structured_sync(  # pragma: no cover - needs torch + a real model
        self,
        messages: list[dict[str, Any]],
        schema: type[StructuredT],
        config: GenerationConfig | None,
        overrides: dict[str, Any],
        constrained: bool,
        stop_event: threading.Event | None = None,
    ) -> StructuredT:
        """Blocking schema-constrained chat generation."""
        with self._lifecycle.use():
            return self._generate_structured_sync(
                self._chat_prompt(messages),
                schema,
                config,
                overrides,
                constrained,
                stop_event,
            )

    def _generate_structured_sync(  # pragma: no cover - needs torch + a real model
        self,
        prompt: str,
        schema: type[StructuredT],
        config: GenerationConfig | None,
        overrides: dict[str, Any],
        constrained: bool,
        stop_event: threading.Event | None = None,
    ) -> StructuredT:
        """Blocking schema-constrained generation."""
        with self._lifecycle.use():
            call_overrides = dict(overrides)
            if constrained:
                call_overrides["prefix_allowed_tokens_fn"] = (
                    build_prefix_allowed_tokens_fn(self._tokenizer, schema)
                )
            text = self._generate_sync(prompt, config, call_overrides, stop_event)
        return parse_structured(text, schema)

    def _stream_sync(
        self,
        prompt: str,
        config: GenerationConfig | None,
        overrides: dict[str, Any],
        stop_event: threading.Event,
        emit: Callable[[str], None],
    ) -> None:
        """Run a streaming generation on a worker thread.

        Loading, tokenizing and decoding all happen here, off the event
        loop. Each finalized piece of text goes to ``emit``, and the
        ``stop_event`` criterion ends decoding at the next token once the
        consumer goes away.

        Args:
            prompt (str): The input text.
            config (GenerationConfig | None): Typed generation parameters.
            overrides (dict[str, Any]): Per-call generation overrides.
            stop_event (threading.Event): Set by the consumer to stop.
            emit (Callable[[str], None]): Receives each text piece; must be
                safe to call from this thread.
        """
        with self._lifecycle.use():
            torch, transformers = _require_transformers()
            seed, stop = _resolve_control(overrides, config)
            streamer = _callback_streamer(
                transformers,
                self._tokenizer,
                emit,
                stop_event,
            )
            inputs = self._tokenizer(prompt, return_tensors="pt").to(
                self._model.device,
            )
            call_kwargs = self._assemble_kwargs(
                overrides,
                config,
                stop,
                self._tokenizer,
            )
            _apply_seed(torch, transformers, self._model, seed, call_kwargs)
            gen_kwargs: dict[str, Any] = {
                **call_kwargs,
                **inputs,
                "streamer": streamer,
                "stopping_criteria": _stop_criteria(transformers, stop_event),
            }
            self._model.generate(**gen_kwargs)

    async def stream(
        self,
        prompt: str,
        *,
        config: GenerationConfig | None = None,
        **kwargs: Any,
    ) -> AsyncIterator[str]:
        """Stream the completion token by token.

        The generation runs on a worker thread and hands each piece to the
        event loop through ``call_soon_threadsafe``, so the loop never
        blocks — not on the first-call load, not while waiting for the next
        token. Closing the iterator early (a client disconnecting, a
        ``break``, ``aclose()``) sets an internal stop event that the
        model checks after every token, so decoding stops within one token
        instead of running to ``max_new_tokens`` for nobody; the close
        itself returns without waiting for the worker thread.

        Measured with ``Qwen/Qwen2.5-0.5B-Instruct`` on an RTX 4070 Ti
        SUPER: the first (cold) stream used to stall the loop for 4190 ms
        while the weights loaded on it, now 164 to 193 ms over three runs;
        closing after 5 of 200 tokens used to block for 3532 ms, now about
        0.01 ms, with the worker thread done 28 ms later.

        Args:
            prompt (str): The input text.
            config (GenerationConfig | None): Typed generation parameters.
            **kwargs (Any): Generation overrides (win over ``config``).

        Yields:
            str: Text pieces as they are produced.
        """
        loop = asyncio.get_running_loop()
        queue: asyncio.Queue[str | _StreamEnd] = asyncio.Queue()
        stop_event = threading.Event()

        def _emit(piece: str) -> None:
            """Forward one piece to the loop's queue from the worker thread.

            A loop that closed while the thread was still decoding raises
            ``RuntimeError`` here; nobody is left to read the piece, and the
            stop event already ends the generation at the next token, so
            the piece is dropped instead of crashing the worker.

            Args:
                piece (str): The text to deliver.
            """
            with contextlib.suppress(RuntimeError):
                loop.call_soon_threadsafe(queue.put_nowait, piece)

        def _produce() -> None:
            """Run the generation, always signalling the end to the loop."""
            try:
                self._stream_sync(prompt, config, dict(kwargs), stop_event, _emit)
            finally:
                with contextlib.suppress(RuntimeError):
                    loop.call_soon_threadsafe(queue.put_nowait, _STREAM_END)

        producer = asyncio.ensure_future(asyncio.to_thread(_produce))
        try:
            while True:
                item = await queue.get()
                if isinstance(item, _StreamEnd):
                    break
                yield item
            await producer
        finally:
            stop_event.set()
            producer.add_done_callback(_consume_result)


__all__: list[str] = [
    "TextBackend",
    "TextGenerator",
    "auto_dtype_name",
    "resolve_device",
]
