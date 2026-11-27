"""The Ollama text generator."""

from __future__ import annotations

import json
from collections.abc import AsyncIterator
from typing import TYPE_CHECKING, Any

from tempest_fastapi_sdk.genai.generation_cache import (
    AsyncGenerationCache,
    GenerationCache,
    cached_generate,
)
from tempest_fastapi_sdk.genai.metrics import GenAIMetrics
from tempest_fastapi_sdk.genai.ollama._client import _require_httpx
from tempest_fastapi_sdk.genai.ollama._constants import (
    DEFAULT_OLLAMA_URL,
    ThinkLevel,
)
from tempest_fastapi_sdk.genai.ollama._requests import _OllamaRequestMixin
from tempest_fastapi_sdk.genai.ollama._validation import (
    _check_think,
    _check_truncate,
)
from tempest_fastapi_sdk.genai.ollama.errors import (
    OllamaFormatIgnoredError,
    _raise_for_refusal,
    _raise_for_status,
    _raise_on_error,
)
from tempest_fastapi_sdk.genai.ollama.schema import _require_every_property
from tempest_fastapi_sdk.genai.schemas import GenerationConfig
from tempest_fastapi_sdk.genai.structured import (
    StructuredT,
    extract_json_object,
    parse_structured,
)
from tempest_fastapi_sdk.genai.tracing import genai_span
from tempest_fastapi_sdk.utils.http_client import HTTPClient, RetryPolicy

if TYPE_CHECKING:
    import httpx


class OllamaGenerator(_OllamaRequestMixin):
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
        truncate (bool | None): Default top-level ``truncate`` field, or
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
        truncate: bool | None = None,
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
                daemon's default (4096 without ``OLLAMA_CONTEXT_LENGTH``).
                Either way, a reply whose ``prompt_eval_count`` shows the
                daemon truncated the prompt logs a ``WARNING`` on the
                ``tempest_fastapi_sdk.genai.ollama`` logger.
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
            truncate (bool | None): Default ``truncate`` field for every
                request, sent at the top level of the body. ``False``
                makes a prompt longer than the context window fail with
                :class:`OllamaContextExceededError` instead of being cut
                to about half the window; ``True`` is the daemon's own
                default (measured on Ollama 0.30.11: the same
                ``prompt_eval_count`` as omitting it). ``None`` sends
                nothing; a call's own ``truncate`` wins over it.

        Raises:
            ValueError: When ``num_ctx`` is not positive, is given both as
                an argument and inside ``options``, ``think`` is not a
                bool or one of ``"low"``, ``"medium"``, ``"high"``, or
                ``truncate`` is not a bool.
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
        _check_truncate(truncate)
        self.config = config
        self.options: dict[str, Any] = merged
        self.think: ThinkLevel | None = think
        self.truncate: bool | None = truncate
        self._loaded_num_ctx: int | None = None

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
        truncate: bool | None = None,
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
            truncate (bool | None): Top-level ``truncate`` for this call;
                ``None`` uses the generator's default.
            **kwargs (Any): Per-call generation overrides (HuggingFace-style
                names such as ``max_new_tokens`` / ``temperature``); these
                win over ``config``.

        Returns:
            str: The generated text.

        Raises:
            OllamaThinkingUnsupportedError: When ``think`` is set and the
                model has no thinking capability.
            OllamaContextExceededError: When ``truncate`` is ``False``
                and the prompt does not fit the context window.
            OllamaError: When the daemon answers with an ``error`` body
                (e.g. the model is not pulled), even on a ``200``.
            httpx.HTTPStatusError: When the daemon answers a non-success
                status.
        """
        _check_think(think)
        _check_truncate(truncate)
        think = think if think is not None else self.think
        truncate = truncate if truncate is not None else self.truncate
        params = self._key_params(
            config, kwargs, images, think=think, truncate=truncate
        )

        async def _produce() -> str:
            async with genai_span("generate", self.model) as trace_span:
                if self.metrics is None:
                    text, prompt_tokens, eval_tokens = await self._generate_measured(
                        prompt,
                        config,
                        images,
                        dict(kwargs),
                        think=think,
                        truncate=truncate,
                    )
                else:
                    async with self.metrics.track(self.model, "generate") as span:
                        (
                            text,
                            prompt_tokens,
                            eval_tokens,
                        ) = await self._generate_measured(
                            prompt,
                            config,
                            images,
                            dict(kwargs),
                            think=think,
                            truncate=truncate,
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

    async def chat(
        self,
        messages: list[dict[str, Any]],
        *,
        config: GenerationConfig | None = None,
        think: ThinkLevel | None = None,
        truncate: bool | None = None,
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
            truncate (bool | None): Top-level ``truncate`` for this call;
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
            OllamaContextExceededError: When ``truncate`` is ``False``
                and the prompt does not fit the context window.
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
        _check_truncate(truncate)
        think = think if think is not None else self.think
        truncate = truncate if truncate is not None else self.truncate
        self._apply_common(payload, config, kwargs, think=think, truncate=truncate)

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
            self._key_params(config, kwargs, think=think, truncate=truncate),
            _produce,
            operation="chat",
        )

    async def generate_structured(
        self,
        prompt: str,
        schema: type[StructuredT],
        *,
        system: str | None = None,
        config: GenerationConfig | None = None,
        think: ThinkLevel | None = None,
        truncate: bool | None = None,
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
            truncate (bool | None): Top-level ``truncate`` for this call;
                ``None`` uses the generator's default.
            **kwargs (Any): Per-call generation overrides (win over ``config``).

        Returns:
            StructuredT: The validated instance.

        Raises:
            OllamaFormatIgnoredError: When the reply holds no JSON object,
                so the daemon did not apply the ``format`` constraint.
            OllamaThinkingUnsupportedError: When ``think`` is set and the
                model has no thinking capability.
            OllamaContextExceededError: When ``truncate`` is ``False``
                and the prompt does not fit the context window.
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
        _check_truncate(truncate)
        think = think if think is not None else self.think
        truncate = truncate if truncate is not None else self.truncate
        self._apply_common(payload, config, kwargs, think=think, truncate=truncate)

        async with genai_span("generate_structured", self.model):
            response = await self._http().post(
                f"{self.base_url}/api/chat",
                json=payload,
            )
            _raise_for_status(self.model, response)
            data: dict[str, Any] = response.json()
            _raise_on_error(self.model, data)
            await self._warn_if_truncated(payload, data)
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
        truncate: bool | None = None,
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
            truncate (bool | None): Top-level ``truncate`` for this call;
                ``None`` uses the generator's default.
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
            OllamaContextExceededError: When ``truncate`` is ``False``
                and the prompt does not fit the context window.
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
        _check_truncate(truncate)
        think = think if think is not None else self.think
        truncate = truncate if truncate is not None else self.truncate
        self._apply_common(payload, config, kwargs, think=think, truncate=truncate)
        async with genai_span("chat", self.model):
            response = await self._http().post(
                f"{self.base_url}/api/chat",
                json=payload,
            )
            _raise_for_status(self.model, response)
            data: dict[str, Any] = response.json()
            _raise_on_error(self.model, data)
            await self._warn_if_truncated(payload, data)
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
        truncate: bool | None = None,
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
            truncate (bool | None): Top-level ``truncate`` for this call;
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
            OllamaContextExceededError: When ``truncate`` is ``False``
                and the prompt does not fit the context window.
            OllamaError: When the daemon answers with an ``error`` body.
        """
        payload: dict[str, Any] = {
            "model": self.model,
            "messages": messages,
            "stream": False,
            "tools": tools,
        }
        _check_think(think)
        _check_truncate(truncate)
        self._apply_common(
            payload,
            config,
            kwargs,
            think=think if think is not None else self.think,
            truncate=truncate if truncate is not None else self.truncate,
        )
        response = await self._http().post(f"{self.base_url}/api/chat", json=payload)
        _raise_for_status(self.model, response)
        data: dict[str, Any] = response.json()
        _raise_on_error(self.model, data)
        await self._warn_if_truncated(payload, data)
        return data.get("message") or {}

    async def stream(
        self,
        prompt: str,
        *,
        config: GenerationConfig | None = None,
        think: ThinkLevel | None = None,
        truncate: bool | None = None,
        **kwargs: Any,
    ) -> AsyncIterator[str]:
        """Stream the completion piece by piece.

        Args:
            prompt (str): The input text.
            config (GenerationConfig | None): Typed generation parameters.
            think (ThinkLevel | None): Top-level ``think`` for this call;
                ``None`` uses the generator's default.
            truncate (bool | None): Top-level ``truncate`` for this call;
                ``None`` uses the generator's default.
            **kwargs (Any): Per-call generation overrides (win over
                ``config``).

        Yields:
            str: Text pieces as the daemon produces them.

        Raises:
            OllamaThinkingUnsupportedError: When ``think`` is set and the
                model has no thinking capability.
            OllamaContextExceededError: When ``truncate`` is ``False``
                and the prompt does not fit the context window.
            OllamaError: When a stream line is an ``{"error": ...}`` object —
                the daemon's way of failing mid-generation. Pieces already
                yielded stay yielded; the stream does not end as if done.
        """
        _check_think(think)
        _check_truncate(truncate)
        payload = self._request_payload(
            prompt,
            config,
            kwargs,
            stream=True,
            think=think if think is not None else self.think,
            truncate=truncate if truncate is not None else self.truncate,
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
                    await self._warn_if_truncated(payload, chunk)
                    break
        except httpx_module.HTTPStatusError as exc:
            _raise_for_refusal(self.model, exc.response)
            raise
