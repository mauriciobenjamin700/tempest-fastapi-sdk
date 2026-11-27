"""Ollama backend for text generation and embeddings.

Runs generation and embeddings against a local (or remote) `Ollama
<https://ollama.com>`_ daemon over HTTP instead of loading HuggingFace
weights with ``torch``. This is the drop-in alternative to the
``torch``/``transformers`` path: :class:`OllamaGenerator` mirrors the
public surface of :class:`~tempest_fastapi_sdk.genai.text.TextGenerator`
(``generate`` / ``chat`` / ``stream`` / ``unload``) and
:class:`OllamaEmbedder` satisfies the
:class:`~tempest_fastapi_sdk.genai.rag.SupportsEmbed` protocol, so both
plug straight into :func:`~tempest_fastapi_sdk.genai.make_genai_router`
and :class:`~tempest_fastapi_sdk.genai.rag.Retriever` with no other
changes.

Ollama manages model download, quantization and VRAM itself, so there is
no ``load()`` step and no GPU code here — the only dependency is
``httpx`` (the ``[genai-ollama]`` extra). The daemon must already be
running and the model already pulled (``ollama pull <model>``).

Example:

    >>> gen = OllamaGenerator("llama3.2")
    >>> await gen.generate("Explain PIX in one sentence.")
    >>> async for token in gen.stream("Tell me a joke."):
    ...     print(token, end="")
    >>> emb = OllamaEmbedder("nomic-embed-text")
    >>> vectors = await emb.embed(["hello", "world"])
"""

from __future__ import annotations

import copy
import json
from collections.abc import AsyncIterator
from typing import TYPE_CHECKING, Any, Literal

from tempest_fastapi_sdk.genai.generation_cache import (
    AsyncGenerationCache,
    GenerationCache,
    cached_generate,
)
from tempest_fastapi_sdk.genai.metrics import GenAIMetrics
from tempest_fastapi_sdk.genai.schemas import GenerationConfig, _layer_config
from tempest_fastapi_sdk.genai.structured import (
    StructuredT,
    extract_json_object,
    parse_structured,
)
from tempest_fastapi_sdk.genai.tracing import genai_span
from tempest_fastapi_sdk.utils.http_client import HTTPClient, RetryPolicy

if TYPE_CHECKING:
    import httpx

DEFAULT_OLLAMA_URL: str = "http://127.0.0.1:11434"

ThinkLevel = bool | Literal["low", "medium", "high"]
"""The Ollama ``think`` field: on/off, or a reasoning level.

Sent at the top level of ``/api/chat`` and ``/api/generate`` (inside
``options`` the daemon ignores it). ``True``/``False`` switch reasoning on
or off on a model with the thinking capability; ``"low"``/``"medium"``/
``"high"`` pick a level on the models that support levels (``gpt-oss``).
"""

_THINK_LEVELS: frozenset[str] = frozenset({"low", "medium", "high"})

_THINKING_UNSUPPORTED: str = "does not support thinking"
"""Substring of the daemon's error for ``think`` on a model without it.

Reproduced on Ollama 0.30.11 with ``ministral-3:14b`` (``/api/show``
capabilities ``completion``, ``vision``, ``tools``): ``think: true`` on
``/api/chat`` and ``/api/generate``, streamed or not, answered ``400`` with
``{"error": "\\"ministral-3:14b\\" does not support thinking"}`` before
loading the model.
"""

# Maps HuggingFace-style generation kwarg names (what GenerationConfig and
# the router speak) to their Ollama ``options`` equivalents.
_HF_TO_OLLAMA_OPTION: dict[str, str] = {
    "max_new_tokens": "num_predict",
    "temperature": "temperature",
    "top_p": "top_p",
    "top_k": "top_k",
    "repetition_penalty": "repeat_penalty",
}


def _require_httpx() -> Any:
    """Import ``httpx`` or raise a helpful error.

    Returns:
        Any: The imported ``httpx`` module.

    Raises:
        ImportError: When the ``[genai-ollama]`` extra is not installed.
    """
    try:
        import httpx
    except ImportError as exc:
        raise ImportError(
            "The Ollama backend requires the optional [genai-ollama] extra. "
            "Install with: pip install tempest-fastapi-sdk[genai-ollama]",
        ) from exc
    return httpx


def _build_options(
    config: GenerationConfig | None,
    overrides: dict[str, Any],
) -> dict[str, Any]:
    """Merge a :class:`GenerationConfig` and per-call overrides into Ollama options.

    Precedence (lowest to highest): the set fields of ``config``, then the
    explicit per-call ``overrides``. HuggingFace-style names
    (``max_new_tokens``, ``repetition_penalty`` …) are translated to their
    Ollama equivalents (``num_predict``, ``repeat_penalty`` …). ``seed`` and
    ``stop`` are carried through as-is (Ollama accepts both as options), and
    ``do_sample=False`` is expressed as greedy decoding (``temperature=0``)
    unless a temperature is set explicitly.

    Args:
        config (GenerationConfig | None): Typed generation parameters whose
            set fields layer over nothing (Ollama supplies its own defaults).
        overrides (dict[str, Any]): Explicit per-call keyword args, in the
            HuggingFace naming used by the generator surface.

    Returns:
        dict[str, Any]: The Ollama ``options`` payload (may be empty).
    """
    merged: dict[str, Any] = {}
    if config is not None:
        merged.update(config.to_generate_kwargs())
        if config.seed is not None:
            merged["seed"] = config.seed
        if config.stop:
            merged["stop"] = list(config.stop)
    merged.update(overrides)

    options: dict[str, Any] = {}
    for key, value in merged.items():
        if value is None:
            continue
        if key == "do_sample":
            if value is False and "temperature" not in merged:
                options["temperature"] = 0.0
            continue
        options[_HF_TO_OLLAMA_OPTION.get(key, key)] = value
    return options


def _require_every_property(schema: dict[str, Any]) -> dict[str, Any]:
    """Return a copy of ``schema`` whose every object requires all its properties.

    Pydantic lists in ``required`` only the fields without a default, and the
    daemon's constrained decoder lets the model omit every other one. The
    reply then validates — the default fills the gap — and is wrong, with no
    signal to the caller. Measured in the 2026-11-26/27 local-LLM study
    (Ollama 0.30.11, 33 extraction cases over 3 schemas, temperature 0):
    under the plain schema ``gpt-oss:20b`` never emitted a
    ``Field(default_factory=list)`` field and answered ``[]`` for it in all
    11 orders; requiring every property took per-field accuracy from 70.8
    to 95.4 on that model, and from 69.2 to 87.3 (``qwen2.5:3b``), 56.6 to
    74.3 (``lfm2.5-thinking:1.2b``), 90.0 to 95.7 (``qwen2.5-coder:14b``)
    and 84.4 to 89.9 (``ministral-3:14b``).

    An optional field keeps the ``anyOf [..., {"type": "null"}]`` Pydantic
    already emits for ``X | None``, so requiring it only makes the model
    state ``null``. A field with a non-null default becomes required on the
    wire; the reply is still validated against the original model.

    The walk follows only the keywords that hold sub-schemas, so a field
    literally named ``properties`` is treated as a field.

    Args:
        schema (dict[str, Any]): A schema from ``model_json_schema()``.

    Returns:
        dict[str, Any]: The tightened copy; ``schema`` is not modified.
    """
    tightened: dict[str, Any] = copy.deepcopy(schema)
    _require_node_properties(tightened)
    return tightened


def _require_node_properties(node: dict[str, Any]) -> None:
    """Apply :func:`_require_every_property` to one schema node, in place.

    Args:
        node (dict[str, Any]): A schema node.
    """
    properties = node.get("properties")
    if isinstance(properties, dict):
        node["required"] = list(properties)
        for child in properties.values():
            if isinstance(child, dict):
                _require_node_properties(child)
    for keyword in ("$defs", "definitions"):
        holder = node.get(keyword)
        if isinstance(holder, dict):
            for child in holder.values():
                if isinstance(child, dict):
                    _require_node_properties(child)
    for keyword in ("anyOf", "allOf", "oneOf", "prefixItems"):
        holder = node.get(keyword)
        if isinstance(holder, list):
            for child in holder:
                if isinstance(child, dict):
                    _require_node_properties(child)
    for keyword in ("items", "additionalProperties", "not"):
        child = node.get(keyword)
        if isinstance(child, dict):
            _require_node_properties(child)


class OllamaError(RuntimeError):
    """The Ollama daemon answered with an ``{"error": ...}`` body.

    The daemon reports some failures (model not pulled, out of memory, a
    runner crash mid-stream) as a JSON object with an ``error`` key — on a
    ``200`` response or as one line of an NDJSON stream. Treating that
    object as a normal reply yields an empty completion, or a stream that
    ends as if it had finished. This exception surfaces it instead.

    Attributes:
        model (str): The model tag the request targeted.
        detail (str): The daemon's error message.
    """

    def __init__(self, model: str, detail: str) -> None:
        """Build the error from the daemon message.

        Args:
            model (str): The model tag the request targeted.
            detail (str): The daemon's ``error`` string.
        """
        super().__init__(f"Ollama error for model {model!r}: {detail}")
        self.model: str = model
        self.detail: str = detail


class OllamaThinkingUnsupportedError(OllamaError):
    """The model has no thinking capability and the request set ``think``.

    The daemon refuses the request with ``400`` and an ``error`` saying the
    model ``does not support thinking`` (reproduced on Ollama 0.30.11 with
    ``ministral-3:14b``). Leave ``think`` as ``None`` for that model.

    Attributes:
        model (str): The model tag the request targeted.
        detail (str): The daemon's error message.
    """

    def __init__(self, model: str, detail: str) -> None:
        """Build the error from the daemon message.

        Args:
            model (str): The model tag the request targeted.
            detail (str): The daemon's ``error`` string.
        """
        super().__init__(model, detail)
        self.args = (
            f"Ollama model {model!r} does not support thinking ({detail}); "
            "leave think=None for this model",
        )


class OllamaFormatIgnoredError(OllamaError, ValueError):
    """A structured call came back as free text instead of the JSON asked for.

    The request carried the schema as ``format`` and the reply holds no JSON
    object, so the daemon did not constrain the decoder. Reproduced on Ollama
    0.30.11: ``ornith:35b`` with ``think=false`` answered
    ``**Nome:** Ana Costa ...`` with ``200`` in 3 of 3 calls, while the same
    request with ``think`` omitted or ``true`` returned the object 3 of 3.
    ``ornith-1.5:9b``, ``gpt-oss:20b`` and ``ministral-3:14b`` honoured
    ``format`` with ``think=false`` (3 of 3 each).

    Subclasses ``ValueError``, which is what a reply without a JSON object
    raised before, so code already catching that keeps working.

    Attributes:
        model (str): The model tag the request targeted.
        detail (str): What went wrong.
        content (str): The start of the reply (first 500 characters).
        think (ThinkLevel | None): The ``think`` value the request carried.
    """

    def __init__(self, model: str, content: str, think: ThinkLevel | None) -> None:
        """Build the error from the reply.

        Args:
            model (str): The model tag the request targeted.
            content (str): The reply content.
            think (ThinkLevel | None): The ``think`` value sent, if any.
        """
        detail = (
            "the reply holds no JSON object, so the format constraint was not applied"
        )
        if think is False:
            detail += (
                "; think=False makes some models ignore format (measured: "
                "ornith:35b), so omit think or set it to True for this model"
            )
        super().__init__(model, detail)
        self.content: str = content[:500]
        self.think: ThinkLevel | None = think


def _check_think(think: ThinkLevel | None) -> None:
    """Reject a ``think`` value the daemon does not define.

    Args:
        think (ThinkLevel | None): The value to check.

    Raises:
        ValueError: When ``think`` is neither ``None``, a bool nor one of
            ``"low"``, ``"medium"``, ``"high"``.
    """
    if think is None or isinstance(think, bool) or think in _THINK_LEVELS:
        return
    raise ValueError(
        f"think must be None, a bool or one of {sorted(_THINK_LEVELS)}, got {think!r}",
    )


def _raise_if_thinking_unsupported(model: str, response: httpx.Response) -> None:
    """Raise :class:`OllamaThinkingUnsupportedError` for the daemon's refusal.

    Args:
        model (str): The model tag, used in the message.
        response (httpx.Response): A non-success response whose body is
            already read.

    Raises:
        OllamaThinkingUnsupportedError: When the body's ``error`` says the
            model does not support thinking.
    """
    try:
        data = response.json()
    except ValueError:
        return
    if isinstance(data, dict):
        error = data.get("error")
        if isinstance(error, str) and _THINKING_UNSUPPORTED in error:
            raise OllamaThinkingUnsupportedError(model, error)


def _raise_for_status(model: str, response: httpx.Response) -> None:
    """Raise for a non-success response, naming the thinking refusal.

    Args:
        model (str): The model tag, used in the message.
        response (httpx.Response): The daemon's response.

    Raises:
        OllamaThinkingUnsupportedError: When the daemon refused ``think``.
        httpx.HTTPStatusError: For any other non-success status.
    """
    if response.is_error:
        _raise_if_thinking_unsupported(model, response)
    response.raise_for_status()


def _raise_on_error(model: str, data: dict[str, Any]) -> None:
    """Raise :class:`OllamaError` when ``data`` is a daemon error object.

    Args:
        model (str): The model tag, used in the message.
        data (dict[str, Any]): A decoded response body or NDJSON line.

    Raises:
        OllamaError: When ``data`` carries a non-empty ``error`` key.
    """
    error = data.get("error")
    if error:
        raise OllamaError(model, str(error))


class _OllamaClientMixin:
    """Shared HTTP-client lifecycle for the Ollama backend classes."""

    def __init__(
        self,
        model: str,
        *,
        base_url: str = DEFAULT_OLLAMA_URL,
        timeout: float = 120.0,
        keep_alive: str | float | None = None,
        http_client: HTTPClient | None = None,
        transport: httpx.AsyncBaseTransport | None = None,
        retry_policy: RetryPolicy | None = None,
        metrics: GenAIMetrics | None = None,
        generation_cache: GenerationCache | AsyncGenerationCache | None = None,
    ) -> None:
        """Configure the connection to the Ollama daemon.

        Args:
            model (str): The Ollama model tag (must already be pulled, e.g.
                ``"llama3.2"`` or ``"nomic-embed-text"``).
            base_url (str): Base URL of the Ollama daemon.
            timeout (float): Per-request HTTP timeout in seconds.
            keep_alive (str | float | None): Ollama ``keep_alive`` value
                controlling how long the model stays resident (e.g.
                ``"5m"``, ``0`` to unload immediately). ``None`` uses the
                daemon default.
            http_client (HTTPClient | None): An injected
                :class:`~tempest_fastapi_sdk.utils.http_client.HTTPClient`
                (connection reuse). When ``None``, one is created lazily and
                owned by this instance, giving every daemon call retry,
                exponential backoff, a per-host circuit-breaker and
                ``X-Request-ID`` propagation for free.
            transport (httpx.AsyncBaseTransport | None): Explicit transport
                for the lazily-created client — pass an ``httpx.MockTransport``
                in tests. Ignored when ``http_client`` is given.
            retry_policy (RetryPolicy | None): Retry configuration for the
                lazily-created client. ``None`` uses the ``HTTPClient``
                defaults **except** ``retry_on_read_timeout=False``: the
                daemon aborts a request the client stopped waiting for, so
                a retried read timeout redoes the whole generation (or
                embedding batch) and times out again. A connection error
                and a retryable status are still retried. An explicit
                policy is used as given. Ignored when ``http_client`` is
                given.
            generation_cache (GenerationCache | AsyncGenerationCache | None):
                Optional prompt→completion cache used by
                :meth:`OllamaGenerator.generate`; only deterministic calls
                (``do_sample=False`` / ``temperature=0``) are cached.
        """
        self.model = model
        self.base_url = base_url.rstrip("/")
        self.timeout = timeout
        self.keep_alive = keep_alive
        self.metrics = metrics
        self.generation_cache = generation_cache
        self._client: HTTPClient | None = http_client
        self._owns_client: bool = http_client is None
        self._transport = transport
        self._retry_policy = retry_policy

    def _http(self) -> HTTPClient:
        """Return the HTTP client, creating an owned one on first use.

        Returns:
            HTTPClient: The resilient client used for daemon requests.
        """
        if self._client is None:
            _require_httpx()
            self._client = HTTPClient(
                timeout=self.timeout,
                transport=self._transport,
                retry_policy=self._retry_policy
                or RetryPolicy(retry_on_read_timeout=False),
            )
        return self._client

    async def aclose(self) -> None:
        """Close the HTTP client when this instance owns it.

        Injected clients are left open for the caller to manage.
        """
        if self._client is not None and self._owns_client:
            await self._client.aclose()
            self._client = None

    def unload(self) -> None:
        """No-op unload hook for registry compatibility.

        Ollama owns model residency (see ``keep_alive``); there is no local
        model to free. Present so an instance satisfies the
        :class:`~tempest_fastapi_sdk.genai.registry.Unloadable` protocol.
        """
        return None


class OllamaGenerator(_OllamaClientMixin):
    """Text generation backed by an Ollama daemon.

    A drop-in alternative to
    :class:`~tempest_fastapi_sdk.genai.text.TextGenerator`: it exposes the
    same ``generate`` / ``chat`` / ``stream`` surface (so it slots into
    :func:`~tempest_fastapi_sdk.genai.make_genai_router`) but delegates
    inference to Ollama over HTTP. No ``torch``, no local weights, no
    ``load()`` — the daemon must be running and the model pulled.

    Attributes:
        model (str): The Ollama model tag.
        base_url (str): The daemon base URL.
        config (GenerationConfig | None): Default generation parameters,
            layered under every call's own.
        options (dict[str, Any]): Raw Ollama ``options`` sent with every
            request, ``num_ctx`` included when set.
        think (ThinkLevel | None): Default top-level ``think`` field, or
            ``None`` to send none.
    """

    def __init__(
        self,
        model: str,
        *,
        base_url: str = DEFAULT_OLLAMA_URL,
        timeout: float = 120.0,
        keep_alive: str | float | None = None,
        http_client: HTTPClient | None = None,
        transport: httpx.AsyncBaseTransport | None = None,
        retry_policy: RetryPolicy | None = None,
        metrics: GenAIMetrics | None = None,
        generation_cache: GenerationCache | AsyncGenerationCache | None = None,
        config: GenerationConfig | None = None,
        num_ctx: int | None = None,
        options: dict[str, Any] | None = None,
        think: ThinkLevel | None = None,
    ) -> None:
        """Configure the daemon connection and the per-request defaults.

        The connection arguments are the ones every Ollama client takes;
        the last three exist because some callers pass no options at all.
        The :class:`~tempest_fastapi_sdk.agents.Agent` loop calls
        ``chat_with_tools(messages, tools)`` and nothing else, so before
        these a generator driving an agent always ran on the daemon's
        defaults — including its context window, which truncates the
        prompt **silently**. Measured on Ollama 0.30.11 with
        ``lfm2.5-thinking:1.2b``: a 35,018-token chat came back with
        ``prompt_eval_count`` 2,051 and no error under the default, and
        35,018 with ``num_ctx=65536``. An agent's prompt is the system
        turn, every tool spec and every tool result so far, so it reaches
        that ceiling within a few steps.

        Args:
            model (str): The Ollama model tag (must already be pulled).
            base_url (str): Base URL of the Ollama daemon.
            timeout (float): Per-request HTTP timeout in seconds.
            keep_alive (str | float | None): Ollama ``keep_alive`` value;
                ``None`` uses the daemon default.
            http_client (HTTPClient | None): An injected client; one is
                created lazily and owned by this instance when ``None``.
            transport (httpx.AsyncBaseTransport | None): Transport for the
                lazily-created client (tests). Ignored with ``http_client``.
            retry_policy (RetryPolicy | None): Retry configuration for the
                lazily-created client. ``None`` keeps the ``HTTPClient``
                defaults but does not retry a read timeout: the daemon
                aborts the generation when the client gives up, so a retry
                redoes it from zero and the failure would arrive after
                three timeouts instead of one. Ignored with ``http_client``.
            metrics (GenAIMetrics | None): Optional Prometheus metrics.
            generation_cache (GenerationCache | AsyncGenerationCache | None):
                Optional prompt→completion cache for deterministic calls.
            config (GenerationConfig | None): Default generation parameters
                for every call. A call's own ``config`` is layered over it
                field by field, and its keyword overrides win over both.
            num_ctx (int | None): Context window in tokens, sent as the
                ``num_ctx`` option on every request. ``None`` leaves the
                daemon's default.
            options (dict[str, Any] | None): Raw Ollama ``options`` sent on
                every request (``num_thread``, ``num_gpu``, …), under the
                ones a call's config or overrides produce.
            think (ThinkLevel | None): Default ``think`` field for every
                request, sent at the top level of the body (inside
                ``options`` the daemon ignores it). ``None`` sends nothing
                and leaves the model's default; a call's own ``think``
                wins over it. Measured in the 2026-11-26/27 local-LLM study
                (Ollama 0.30.11, temperature 0): ``gpt-oss:20b`` with
                ``"low"`` solved 97.1% of 34 tool tasks at p50 1.44 s,
                against 94.1% at 1.68 s for its default (medium) and 97.1%
                at 3.59 s for ``"high"``; ``ornith-1.5:9b`` with ``False``
                scored 93.3 per field on 33 extraction cases at p50
                1.34 s, against 95.0 at 5.60 s with ``True``.

        Raises:
            ValueError: When ``num_ctx`` is not positive, is given both as
                an argument and inside ``options``, or ``think`` is not a
                bool or one of ``"low"``, ``"medium"``, ``"high"``.
        """
        super().__init__(
            model,
            base_url=base_url,
            timeout=timeout,
            keep_alive=keep_alive,
            http_client=http_client,
            transport=transport,
            retry_policy=retry_policy,
            metrics=metrics,
            generation_cache=generation_cache,
        )
        merged: dict[str, Any] = dict(options or {})
        if num_ctx is not None:
            if num_ctx <= 0:
                raise ValueError("num_ctx must be positive")
            if "num_ctx" in merged:
                raise ValueError("num_ctx given both as an argument and in options")
            merged["num_ctx"] = num_ctx
        _check_think(think)
        self.config = config
        self.options: dict[str, Any] = merged
        self.think: ThinkLevel | None = think

    @property
    def is_loaded(self) -> bool:
        """Return ``True`` — the daemon owns model residency.

        Provided for surface-compatibility with ``TextGenerator``; Ollama
        loads/unloads models on demand, so from the caller's perspective the
        backend is always ready.

        Returns:
            bool: Always ``True``.
        """
        return True

    def load(self) -> None:
        """No-op load hook.

        ``TextGenerator`` loads weights here; Ollama does it lazily on the
        first request, so there is nothing to do.
        """
        return None

    async def generate(
        self,
        prompt: str,
        *,
        config: GenerationConfig | None = None,
        images: list[str] | None = None,
        think: ThinkLevel | None = None,
        **kwargs: Any,
    ) -> str:
        """Generate a completion for ``prompt``.

        Args:
            prompt (str): The input text.
            config (GenerationConfig | None): Typed generation parameters;
                its set fields are mapped to Ollama options.
            images (list[str] | None): Base64-encoded images for a
                multimodal model (e.g. ``llava``, ``llama3.2-vision``);
                forwarded as the Ollama ``images`` field. ``None`` for a
                text-only prompt.
            think (ThinkLevel | None): Top-level ``think`` for this call;
                ``None`` uses the generator's default.
            **kwargs (Any): Per-call generation overrides (HuggingFace-style
                names such as ``max_new_tokens`` / ``temperature``); these
                win over ``config``.

        Returns:
            str: The generated text.

        Raises:
            OllamaThinkingUnsupportedError: When ``think`` is set and the
                model has no thinking capability.
            OllamaError: When the daemon answers with an ``error`` body
                (e.g. the model is not pulled), even on a ``200``.
            httpx.HTTPStatusError: When the daemon answers a non-success
                status.
        """
        _check_think(think)
        think = think if think is not None else self.think
        params = self._key_params(config, kwargs, images, think=think)

        async def _produce() -> str:
            async with genai_span("generate", self.model) as trace_span:
                if self.metrics is None:
                    text, prompt_tokens, eval_tokens = await self._generate_measured(
                        prompt, config, images, dict(kwargs), think=think
                    )
                else:
                    async with self.metrics.track(self.model, "generate") as span:
                        (
                            text,
                            prompt_tokens,
                            eval_tokens,
                        ) = await self._generate_measured(
                            prompt, config, images, dict(kwargs), think=think
                        )
                        span.tokens_in = prompt_tokens
                        span.tokens_out = eval_tokens
                trace_span.tokens_in = prompt_tokens
                trace_span.tokens_out = eval_tokens
                return text

        return await cached_generate(
            self.generation_cache,
            self.model,
            prompt,
            params,
            _produce,
        )

    def _key_params(
        self,
        config: GenerationConfig | None,
        overrides: dict[str, Any],
        images: list[str] | None = None,
        *,
        think: ThinkLevel | None = None,
    ) -> dict[str, Any]:
        """Merge config, overrides, images and think into the cache-key parameters.

        The defaults are part of the key: a different context window, a
        different default config or a different ``think`` produces
        different text, so a generator must not answer from another's
        cached completions.
        """
        config = _layer_config(self.config, config)
        params: dict[str, Any] = {}
        if self.options:
            params["options"] = dict(self.options)
        if config is not None:
            params.update(config.model_dump(exclude_none=True, exclude_unset=True))
        params.update(overrides)
        if images:
            params["images"] = images
        if think is not None:
            params["think"] = think
        return params

    async def _generate_once(
        self,
        prompt: str,
        config: GenerationConfig | None,
        images: list[str] | None,
        overrides: dict[str, Any],
    ) -> str:
        """Run one ``/api/generate`` call and return the completion text."""
        text, _prompt_tokens, _eval_tokens = await self._generate_measured(
            prompt,
            config,
            images,
            overrides,
        )
        return text

    async def _generate_measured(
        self,
        prompt: str,
        config: GenerationConfig | None,
        images: list[str] | None,
        overrides: dict[str, Any],
        *,
        think: ThinkLevel | None = None,
    ) -> tuple[str, int | None, int | None]:
        """Run one ``/api/generate`` call, returning text + Ollama token counts."""
        payload = self._request_payload(
            prompt, config, overrides, stream=False, think=think
        )
        if images:
            payload["images"] = images
        response = await self._http().post(
            f"{self.base_url}/api/generate",
            json=payload,
        )
        _raise_for_status(self.model, response)
        data: dict[str, Any] = response.json()
        _raise_on_error(self.model, data)
        return (
            str(data.get("response", "")),
            data.get("prompt_eval_count"),
            data.get("eval_count"),
        )

    async def chat(
        self,
        messages: list[dict[str, Any]],
        *,
        config: GenerationConfig | None = None,
        think: ThinkLevel | None = None,
        **kwargs: Any,
    ) -> str:
        """Generate a reply for a chat ``messages`` list.

        Args:
            messages (list[dict[str, Any]]): Chat turns, each
                ``{"role": ..., "content": ...}``. A turn may also carry an
                ``"images": [<base64>, ...]`` key for multimodal models —
                it is forwarded verbatim to Ollama.
            config (GenerationConfig | None): Typed generation parameters.
            think (ThinkLevel | None): Top-level ``think`` for this call;
                ``None`` uses the generator's default.
            **kwargs (Any): Per-call generation overrides (win over
                ``config``).

        Honors ``generation_cache`` (deterministic calls only, keyed apart
        from :meth:`generate` so a prompt that equals the serialized
        messages never collides) and records ``metrics`` like
        :meth:`generate`, matching
        :meth:`~tempest_fastapi_sdk.genai.text.TextGenerator.chat`.

        Returns:
            str: The assistant reply.

        Raises:
            OllamaThinkingUnsupportedError: When ``think`` is set and the
                model has no thinking capability.
            OllamaError: When the daemon answers with an ``error`` body.
            httpx.HTTPStatusError: When the daemon answers a non-success
                status.
        """
        payload: dict[str, Any] = {
            "model": self.model,
            "messages": messages,
            "stream": False,
        }
        _check_think(think)
        think = think if think is not None else self.think
        self._apply_common(payload, config, kwargs, think=think)

        async def _produce() -> str:
            async with genai_span("chat", self.model) as trace_span:
                if self.metrics is None:
                    text, prompt_tokens, eval_tokens = await self._chat_measured(
                        payload
                    )
                else:
                    async with self.metrics.track(self.model, "chat") as span:
                        text, prompt_tokens, eval_tokens = await self._chat_measured(
                            payload
                        )
                        span.tokens_in = prompt_tokens
                        span.tokens_out = eval_tokens
                trace_span.tokens_in = prompt_tokens
                trace_span.tokens_out = eval_tokens
                return text

        return await cached_generate(
            self.generation_cache,
            self.model,
            json.dumps(messages, sort_keys=True, default=str),
            self._key_params(config, kwargs, think=think),
            _produce,
            operation="chat",
        )

    async def _chat_measured(
        self,
        payload: dict[str, Any],
    ) -> tuple[str, int | None, int | None]:
        """Run one ``/api/chat`` call, returning text + Ollama token counts.

        Args:
            payload (dict[str, Any]): The complete ``/api/chat`` body.

        Returns:
            tuple[str, int | None, int | None]: The reply content, the
            ``prompt_eval_count`` and the ``eval_count``.

        Raises:
            OllamaError: When the daemon answers with an ``error`` body.
        """
        response = await self._http().post(f"{self.base_url}/api/chat", json=payload)
        _raise_for_status(self.model, response)
        data: dict[str, Any] = response.json()
        _raise_on_error(self.model, data)
        message: dict[str, Any] = data.get("message") or {}
        return (
            str(message.get("content", "")),
            data.get("prompt_eval_count"),
            data.get("eval_count"),
        )

    async def generate_structured(
        self,
        prompt: str,
        schema: type[StructuredT],
        *,
        system: str | None = None,
        config: GenerationConfig | None = None,
        think: ThinkLevel | None = None,
        **kwargs: Any,
    ) -> StructuredT:
        """Generate a completion constrained to a Pydantic ``schema``.

        Passes the schema as the Ollama ``format`` field (the daemon enforces
        JSON-schema-valid output natively) and parses the reply into an
        instance of ``schema``. No ``[genai-structured]`` extra needed — the
        constraint lives in the daemon. The posted schema lists every
        property in ``required`` at every level, defaulted ones included,
        so the model cannot skip a field and let its default stand in for
        the answer; optional fields stay nullable.

        The request goes to ``/api/chat``, not ``/api/generate``. That is not
        a style choice: on a harmony/reasoning model such as ``gpt-oss``,
        ``/api/generate`` with a ``format`` constraint answers ``200 OK``
        with a non-zero ``eval_count`` and an **empty** ``response`` — the
        reply lands in a reasoning channel the endpoint does not surface.
        Measured against ``gpt-oss:20b``: ``/api/generate`` without
        ``format`` works, ``/api/generate`` with ``format`` returns empty,
        ``/api/chat`` with ``format`` returns the JSON in
        ``message.content``. Non-reasoning models behave the same on either
        endpoint, so ``/api/chat`` is correct for both.

        Args:
            prompt (str): The input text. With a schema in hand the model
                does not need to be told to answer as JSON — the daemon
                constrains the decoder.
            schema (type[StructuredT]): The Pydantic model to produce.
            system (str | None): Instruction sent as its own system turn
                ahead of ``prompt``. Prefer it over prefixing the
                instruction to a long document: a several-thousand-token
                ``prompt`` buries an instruction placed above it, and the
                model answers about the document while ignoring the ask.
            config (GenerationConfig | None): Typed generation parameters.
            think (ThinkLevel | None): Top-level ``think`` for this call;
                ``None`` uses the generator's default. ``False`` makes some
                models ignore ``format`` (see
                :class:`OllamaFormatIgnoredError`).
            **kwargs (Any): Per-call generation overrides (win over ``config``).

        Returns:
            StructuredT: The validated instance.

        Raises:
            OllamaFormatIgnoredError: When the reply holds no JSON object,
                so the daemon did not apply the ``format`` constraint.
            OllamaThinkingUnsupportedError: When ``think`` is set and the
                model has no thinking capability.
            ValueError: When the daemon answers with no content.
            pydantic.ValidationError: When the JSON fails ``schema`` validation.
        """
        messages: list[dict[str, Any]] = []
        if system:
            messages.append({"role": "system", "content": system})
        messages.append({"role": "user", "content": prompt})

        payload: dict[str, Any] = {
            "model": self.model,
            "messages": messages,
            "stream": False,
            "format": _require_every_property(schema.model_json_schema()),
        }
        _check_think(think)
        think = think if think is not None else self.think
        self._apply_common(payload, config, kwargs, think=think)

        async with genai_span("generate_structured", self.model):
            response = await self._http().post(
                f"{self.base_url}/api/chat",
                json=payload,
            )
            _raise_for_status(self.model, response)
            data: dict[str, Any] = response.json()
            _raise_on_error(self.model, data)
            message: dict[str, Any] = data.get("message") or {}
            content = str(message.get("content") or "")
            if not content.strip():
                raise ValueError(
                    f"model {self.model} answered with no content "
                    f"(eval_count={data.get('eval_count')}); check that the "
                    "model supports constrained structured output",
                )
            if extract_json_object(content) is None:
                raise OllamaFormatIgnoredError(self.model, content, think)
            return parse_structured(content, schema)

    async def chat_structured(
        self,
        messages: list[dict[str, Any]],
        schema: type[StructuredT],
        *,
        config: GenerationConfig | None = None,
        think: ThinkLevel | None = None,
        **kwargs: Any,
    ) -> StructuredT:
        """Generate a chat reply constrained to a Pydantic ``schema``.

        The messages list is what makes a system instruction separable
        from the payload being read. That separation helps, but it is the
        smaller half of the story: the first thing a long document needs
        is a context window that fits it. Ollama defaults ``num_ctx`` to
        4096 tokens and truncates the rest silently, and under truncation
        both shapes degrade — the concatenated one worse, because the
        instruction competes for room with the document. Measured over a
        24,086-character document with 20 extractable items, three runs
        each: at ``num_ctx=4096`` the system turn returned 0, 6 and 5
        items against 1 and 2 (and one empty reply) concatenated; at
        ``num_ctx=16384`` both returned all 20, every run. Size the window
        first, then keep the instruction in its own turn.

        ``format`` is posted at the **top level** of the request body,
        which is where the daemon reads it. Sending it inside ``options``
        — what happens when a schema is passed as a keyword to
        :meth:`chat` — is ignored silently: the call returns ``200`` with
        free text, and the caller finds out at validation time, or worse,
        at a parse that happens to succeed.

        As in :meth:`generate_structured`, the posted schema lists every
        property in ``required`` at every level; optional fields stay
        nullable and the reply is validated against ``schema`` itself.

        Args:
            messages (list[dict[str, Any]]): Chat turns, each
                ``{"role": ..., "content": ...}``. Put the instruction in
                a ``system`` turn and the content being read in ``user``.
            schema (type[StructuredT]): The Pydantic model to produce.
            config (GenerationConfig | None): Typed generation parameters.
            think (ThinkLevel | None): Top-level ``think`` for this call;
                ``None`` uses the generator's default. ``False`` makes some
                models ignore ``format`` (see
                :class:`OllamaFormatIgnoredError`).
            **kwargs (Any): Per-call generation overrides (win over
                ``config``).

        Returns:
            StructuredT: The validated instance.

        Raises:
            TypeError: When ``format`` is passed as a keyword — the
                schema argument is what sets it, and a keyword would be
                dropped into ``options`` and ignored.
            OllamaFormatIgnoredError: When the reply holds no JSON object,
                so the daemon did not apply the ``format`` constraint.
            OllamaThinkingUnsupportedError: When ``think`` is set and the
                model has no thinking capability.
            pydantic.ValidationError: When the JSON fails ``schema``
                validation.

        Example:
            ```python
            from pydantic import BaseModel

            from tempest_fastapi_sdk.genai import OllamaGenerator


            class Invoice(BaseModel):
                number: str
                total_cents: int


            async def read(document: str) -> Invoice:
                "Extract the invoice fields from a document."
                generator = OllamaGenerator(model="gpt-oss:20b")
                return await generator.chat_structured(
                    [
                        {"role": "system", "content": "Extract the invoice fields."},
                        {"role": "user", "content": document},
                    ],
                    Invoice,
                )
            ```
        """
        if "format" in kwargs:
            raise TypeError(
                "chat_structured() sets `format` from the schema argument; "
                "passing format= would be routed into `options` and ignored "
                "by the daemon.",
            )
        payload: dict[str, Any] = {
            "model": self.model,
            "messages": messages,
            "stream": False,
            "format": _require_every_property(schema.model_json_schema()),
        }
        _check_think(think)
        think = think if think is not None else self.think
        self._apply_common(payload, config, kwargs, think=think)
        async with genai_span("chat", self.model):
            response = await self._http().post(
                f"{self.base_url}/api/chat",
                json=payload,
            )
            _raise_for_status(self.model, response)
            data: dict[str, Any] = response.json()
            _raise_on_error(self.model, data)
            message: dict[str, Any] = data.get("message") or {}
            content = str(message.get("content") or "")
            if extract_json_object(content) is None:
                raise OllamaFormatIgnoredError(self.model, content, think)
            return parse_structured(content, schema)

    async def chat_with_tools(
        self,
        messages: list[dict[str, Any]],
        tools: list[dict[str, Any]],
        *,
        config: GenerationConfig | None = None,
        think: ThinkLevel | None = None,
        **kwargs: Any,
    ) -> dict[str, Any]:
        """Generate a chat reply with tool-calling enabled.

        Posts to ``/api/chat`` with the same payload as :meth:`chat` plus a
        ``tools`` field (OpenAI-style function specs) and returns the whole
        response ``message`` dict — so the caller can inspect both its
        ``content`` and any ``tool_calls`` the model emitted.

        Args:
            messages (list[dict[str, Any]]): Chat turns, each
                ``{"role": ..., "content": ...}`` (a turn may carry
                ``tool_calls`` or be a ``{"role": "tool", ...}`` result).
            tools (list[dict[str, Any]]): Tool specifications in the Ollama /
                OpenAI ``{"type": "function", "function": {...}}`` shape.
            config (GenerationConfig | None): Typed generation parameters.
            think (ThinkLevel | None): Top-level ``think`` for this call;
                ``None`` uses the generator's default.
            **kwargs (Any): Per-call generation overrides (win over
                ``config``).

        Returns:
            dict[str, Any]: The full response ``message`` dict (with
            ``content`` and an optional ``tool_calls`` list), or an empty
            dict when the daemon returns no message.

        Raises:
            OllamaThinkingUnsupportedError: When ``think`` is set and the
                model has no thinking capability.
            OllamaError: When the daemon answers with an ``error`` body.
        """
        payload: dict[str, Any] = {
            "model": self.model,
            "messages": messages,
            "stream": False,
            "tools": tools,
        }
        _check_think(think)
        self._apply_common(
            payload,
            config,
            kwargs,
            think=think if think is not None else self.think,
        )
        response = await self._http().post(f"{self.base_url}/api/chat", json=payload)
        _raise_for_status(self.model, response)
        data: dict[str, Any] = response.json()
        _raise_on_error(self.model, data)
        return data.get("message") or {}

    async def stream(
        self,
        prompt: str,
        *,
        config: GenerationConfig | None = None,
        think: ThinkLevel | None = None,
        **kwargs: Any,
    ) -> AsyncIterator[str]:
        """Stream the completion piece by piece.

        Args:
            prompt (str): The input text.
            config (GenerationConfig | None): Typed generation parameters.
            think (ThinkLevel | None): Top-level ``think`` for this call;
                ``None`` uses the generator's default.
            **kwargs (Any): Per-call generation overrides (win over
                ``config``).

        Yields:
            str: Text pieces as the daemon produces them.

        Raises:
            OllamaThinkingUnsupportedError: When ``think`` is set and the
                model has no thinking capability.
            OllamaError: When a stream line is an ``{"error": ...}`` object —
                the daemon's way of failing mid-generation. Pieces already
                yielded stay yielded; the stream does not end as if done.
        """
        _check_think(think)
        payload = self._request_payload(
            prompt,
            config,
            kwargs,
            stream=True,
            think=think if think is not None else self.think,
        )
        httpx_module = _require_httpx()
        lines = self._http().stream(
            "POST",
            f"{self.base_url}/api/generate",
            json=payload,
        )
        try:
            async for line in lines:
                if not line.strip():
                    continue
                chunk: dict[str, Any] = json.loads(line)
                _raise_on_error(self.model, chunk)
                piece = chunk.get("response")
                if piece:
                    yield str(piece)
                if chunk.get("done"):
                    break
        except httpx_module.HTTPStatusError as exc:
            _raise_if_thinking_unsupported(self.model, exc.response)
            raise

    def _request_payload(
        self,
        prompt: str,
        config: GenerationConfig | None,
        overrides: dict[str, Any],
        *,
        stream: bool,
        think: ThinkLevel | None = None,
    ) -> dict[str, Any]:
        """Build the ``/api/generate`` request body.

        Args:
            prompt (str): The input prompt.
            config (GenerationConfig | None): Typed generation parameters.
            overrides (dict[str, Any]): Per-call overrides.
            stream (bool): Whether to request a streamed response.
            think (ThinkLevel | None): Resolved top-level ``think``.

        Returns:
            dict[str, Any]: The JSON payload for the daemon.
        """
        payload: dict[str, Any] = {
            "model": self.model,
            "prompt": prompt,
            "stream": stream,
        }
        self._apply_common(payload, config, overrides, think=think)
        return payload

    def _apply_common(
        self,
        payload: dict[str, Any],
        config: GenerationConfig | None,
        overrides: dict[str, Any],
        *,
        think: ThinkLevel | None = None,
    ) -> None:
        """Attach ``options``, ``keep_alive`` and ``think`` to a request payload.

        Args:
            payload (dict[str, Any]): The request body to mutate in place.
            config (GenerationConfig | None): Typed generation parameters.
            overrides (dict[str, Any]): Per-call overrides.
            think (ThinkLevel | None): Resolved ``think``, sent at the top
                level when not ``None``.
        """
        options = {
            **self.options,
            **_build_options(_layer_config(self.config, config), overrides),
        }
        if options:
            payload["options"] = options
        if self.keep_alive is not None:
            payload["keep_alive"] = self.keep_alive
        if think is not None:
            payload["think"] = think


class OllamaEmbedder(_OllamaClientMixin):
    """Embeddings backed by an Ollama daemon.

    Satisfies :class:`~tempest_fastapi_sdk.genai.rag.SupportsEmbed`, so it
    plugs into :class:`~tempest_fastapi_sdk.genai.rag.Retriever` and the
    ``/embed`` endpoint of
    :func:`~tempest_fastapi_sdk.genai.make_genai_router` in place of the
    ``torch``-backed :class:`~tempest_fastapi_sdk.genai.Embedder`. Pull an
    embedding model first (e.g. ``ollama pull nomic-embed-text``).

    Asymmetric models such as ``nomic-embed-text`` were trained with a task
    prefix on each side; pass them as ``query_prefix`` / ``passage_prefix``
    (or splat a preset such as
    :data:`~tempest_fastapi_sdk.genai.NOMIC_EMBED_TEXT_PREFIXES`) and the
    ``Retriever`` applies each one through :meth:`embed_query` and
    :meth:`embed_passages`. Changing the prefixes changes every vector, so
    an existing index has to be rebuilt.

    Attributes:
        model (str): The Ollama embedding model tag.
        base_url (str): The daemon base URL.
        query_prefix (str): Prepended by :meth:`embed_query`.
        passage_prefix (str): Prepended by :meth:`embed_passages`.
    """

    def __init__(
        self,
        model: str,
        *,
        base_url: str = DEFAULT_OLLAMA_URL,
        timeout: float = 120.0,
        keep_alive: str | float | None = None,
        http_client: HTTPClient | None = None,
        transport: httpx.AsyncBaseTransport | None = None,
        retry_policy: RetryPolicy | None = None,
        metrics: GenAIMetrics | None = None,
        generation_cache: GenerationCache | AsyncGenerationCache | None = None,
        query_prefix: str = "",
        passage_prefix: str = "",
    ) -> None:
        """Configure the daemon connection and the task prefixes.

        Args:
            model (str): The Ollama embedding model tag (must already be
                pulled, e.g. ``"nomic-embed-text"``).
            base_url (str): Base URL of the Ollama daemon.
            timeout (float): Per-request HTTP timeout in seconds.
            keep_alive (str | float | None): Ollama ``keep_alive`` value;
                ``None`` uses the daemon default.
            http_client (HTTPClient | None): An injected client; when
                ``None`` one is created lazily and owned by this instance.
            transport (httpx.AsyncBaseTransport | None): Explicit transport
                for the lazily-created client (``httpx.MockTransport`` in
                tests). Ignored when ``http_client`` is given.
            retry_policy (RetryPolicy | None): Retry configuration for the
                lazily-created client. Ignored when ``http_client`` is given.
            metrics (GenAIMetrics | None): Optional Prometheus metrics.
            generation_cache (GenerationCache | AsyncGenerationCache | None):
                Accepted for signature compatibility with the shared client
                base; embeddings never read it.
            query_prefix (str): Text prepended to every input of
                :meth:`embed_query`. ``nomic-embed-text`` wants
                ``"search_query: "``. :meth:`embed` never applies it.
            passage_prefix (str): Text prepended to every input of
                :meth:`embed_passages` (the corpus side);
                ``nomic-embed-text`` wants ``"search_document: "``.
        """
        super().__init__(
            model,
            base_url=base_url,
            timeout=timeout,
            keep_alive=keep_alive,
            http_client=http_client,
            transport=transport,
            retry_policy=retry_policy,
            metrics=metrics,
            generation_cache=generation_cache,
        )
        self.query_prefix: str = query_prefix
        self.passage_prefix: str = passage_prefix

    async def embed_query(
        self,
        texts: str | list[str],
        *,
        batch_size: int = 32,
    ) -> list[list[float]]:
        """Embed search queries, prepending ``query_prefix`` to each one.

        :class:`~tempest_fastapi_sdk.genai.rag.Retriever` calls this for the
        query when the embedder has it.

        Args:
            texts (str | list[str]): A single query or a list of queries.
            batch_size (int): Max texts sent to the daemon per request.

        Returns:
            list[list[float]]: One vector per input query, in input order.
        """
        items = [texts] if isinstance(texts, str) else list(texts)
        return await self.embed(
            [f"{self.query_prefix}{text}" for text in items],
            batch_size=batch_size,
        )

    async def embed_passages(
        self,
        texts: str | list[str],
        *,
        batch_size: int = 32,
    ) -> list[list[float]]:
        """Embed corpus passages, prepending ``passage_prefix`` to each one.

        :class:`~tempest_fastapi_sdk.genai.rag.Retriever` calls this when it
        indexes chunks, so stored vectors and query vectors carry the role
        markers the model was trained with.

        Args:
            texts (str | list[str]): A single passage or a list of passages.
            batch_size (int): Max texts sent to the daemon per request.

        Returns:
            list[list[float]]: One vector per input passage, in input order.
        """
        items = [texts] if isinstance(texts, str) else list(texts)
        return await self.embed(
            [f"{self.passage_prefix}{text}" for text in items],
            batch_size=batch_size,
        )

    async def embed(
        self,
        texts: str | list[str],
        *,
        batch_size: int = 32,
    ) -> list[list[float]]:
        """Embed one or many texts into vectors.

        Args:
            texts (str | list[str]): A single text or a list of texts.
            batch_size (int): Max texts sent to the daemon per request.

        Returns:
            list[list[float]]: One vector per input text, in input order.
                Returns an empty list for empty input.
        """
        items = [texts] if isinstance(texts, str) else list(texts)
        if not items:
            return []

        async with genai_span("embed", self.model):
            client = self._http()
            vectors: list[list[float]] = []
            for start in range(0, len(items), batch_size):
                batch = items[start : start + batch_size]
                payload: dict[str, Any] = {"model": self.model, "input": batch}
                if self.keep_alive is not None:
                    payload["keep_alive"] = self.keep_alive
                response = await client.post(f"{self.base_url}/api/embed", json=payload)
                response.raise_for_status()
                data: dict[str, Any] = response.json()
                _raise_on_error(self.model, data)
                embeddings: list[list[float]] = data.get("embeddings") or []
                vectors.extend([float(x) for x in vector] for vector in embeddings)
            return vectors


__all__: list[str] = [
    "DEFAULT_OLLAMA_URL",
    "OllamaEmbedder",
    "OllamaError",
    "OllamaFormatIgnoredError",
    "OllamaGenerator",
    "OllamaThinkingUnsupportedError",
    "ThinkLevel",
]
