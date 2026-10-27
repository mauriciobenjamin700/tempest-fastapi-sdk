"""Hosted text generation over the OpenAI ``/chat/completions`` wire format.

The wire format is the common denominator: DeepSeek, Groq, Together,
OpenRouter, Mistral, vLLM's server, TGI's OpenAI route and Azure all
document an OpenAI-compatible ``/chat/completions``, so one client reaches
them by changing ``base_url`` and ``model``.

That list is what those providers advertise, not a matrix this repo runs —
the tests drive an ``httpx.MockTransport``, so what they pin is the request
this class builds and the response it reads, not any live provider. A
provider that deviates from the format deviates from these tests too.

:class:`OpenAICompatGenerator` satisfies the same protocols as the
``OllamaGenerator`` —
:class:`~tempest_fastapi_sdk.genai.text.TextBackend`,
:class:`~tempest_fastapi_sdk.genai.text.StructuredTextBackend`
(``chat_structured``) and the agent's
:class:`~tempest_fastapi_sdk.agents.ToolCallingBackend`
(``chat_with_tools``) — so it drops into
:func:`~tempest_fastapi_sdk.genai.make_genai_router`,
:class:`~tempest_fastapi_sdk.genai.AIChatPipeline` and
:class:`~tempest_fastapi_sdk.agents.Agent` beside the local backends.
:class:`OpenAICompatEmbedder` does the same for
:class:`~tempest_fastapi_sdk.genai.rag.SupportsEmbed`. Errors are raised as
the typed :class:`~tempest_fastapi_sdk.genai.HostedAPIError` family.

Two things it does that the local backends cannot:

* **Reports what the call cost.** ``generate_with_usage`` returns the
  provider's own :class:`~tempest_fastapi_sdk.genai.TokenUsage` alongside
  the text. ``generate`` still returns a bare ``str``, because that is what
  the protocol declares — exposing usage is an addition, not a change.
* **Carries provider-specific fields** through ``extra_body``, without this
  module growing a branch per vendor. The case that motivated it, reported
  by a downstream service against DeepSeek and not reproduced here: a hybrid
  reasoning model with thinking **on** by default spends ``max_tokens`` on
  hidden reasoning before the real content, so a budget sized for the answer
  is exhausted there and the completion comes back empty.
  ``extra_body={"thinking": {"type": "disabled"}}`` turns it off.

No vendor SDK is used. This is one POST with a bearer token; a dependency
that wrapped it would bring its own bounds and buy nothing.

Example:

    >>> gen = OpenAICompatGenerator(
    ...     "deepseek-flash",
    ...     api_key="sk-...",
    ...     base_url="https://api.deepseek.com",
    ... )
    >>> text, usage = await gen.generate_with_usage("Explain PIX briefly.")
"""

from __future__ import annotations

import json
import re
from collections.abc import AsyncIterator, Iterable, Mapping
from typing import TYPE_CHECKING, Any, Literal

import httpx

from tempest_fastapi_sdk.genai.completion import (
    Effort,
    StopReason,
    StructuredCompletion,
    build_structured_completion,
)
from tempest_fastapi_sdk.genai.hosted import (
    HostedAPIError,
    hosted_error,
    raise_for_hosted_status,
    sse_data,
)
from tempest_fastapi_sdk.genai.structured import StructuredT, parse_structured
from tempest_fastapi_sdk.genai.tokens import TokenUsage

if TYPE_CHECKING:
    from tempest_fastapi_sdk.genai.metrics import GenAIMetrics
    from tempest_fastapi_sdk.genai.schemas import GenerationConfig
    from tempest_fastapi_sdk.utils.http_client import HTTPClient, RetryPolicy

DEFAULT_OPENAI_URL: str = "https://api.openai.com/v1"
"""Where the wire format originates; override for any other provider.

The ``servers`` entry of the official OpenAPI document
(https://github.com/openai/openai-openapi).
"""

DEFAULT_DEEPSEEK_URL: str = "https://api.deepseek.com"
"""DeepSeek's OpenAI-format API root.

From https://api-docs.deepseek.com/, which posts to
``https://api.deepseek.com/chat/completions``.
"""

DEFAULT_DEEPSEEK_MODEL: str = "deepseek-flash"
"""DeepSeek's default model id.

The ``model`` field of https://api-docs.deepseek.com/api/create-chat-completion
accepts ``deepseek-flash`` or ``deepseek-v4-pro`` when this was written.
"""

StructuredOutputMode = Literal["json_schema", "json_object"]
"""How :meth:`OpenAICompatGenerator.chat_structured` asks for JSON.

``"json_schema"`` sends the schema as ``response_format`` (the
``ResponseFormatJsonSchema`` of the OpenAI spec). ``"json_object"`` asks only
for *some* JSON object and states the schema in a system turn — for a
provider whose ``response_format`` has no ``json_schema`` type, like DeepSeek
(https://api-docs.deepseek.com/guides/json_mode).
"""

_SCHEMA_NAME_INVALID = re.compile(r"[^a-zA-Z0-9_-]")
"""Characters a ``json_schema.name`` may not contain (``a-z A-Z 0-9 _ -``)."""

# GenerationConfig speaks HuggingFace; the wire format speaks OpenAI. Only
# the names differ — the meaning is the same on both sides.
_PARAM_NAMES: dict[str, str] = {
    "max_new_tokens": "max_tokens",
    "temperature": "temperature",
    "top_p": "top_p",
    "seed": "seed",
    "stop": "stop",
}

_HF_ONLY_PARAMS: frozenset[str] = frozenset(
    {"do_sample", "top_k", "repetition_penalty"},
)
"""``GenerationConfig`` fields the OpenAI wire format does not define.

OpenAI's API documents none of them and a strict provider refuses the body
(an audit reported ``400`` from OpenAI; the tests here drive a mock transport,
not a live provider), so these are never forwarded under their HuggingFace
name unless the instance was built with ``forward_params`` naming them.
``do_sample=False`` is expressed instead as ``temperature=0`` (greedy), the
one of the three that has a wire equivalent.
"""

_RESERVED_BODY_KEYS: frozenset[str] = frozenset({"model", "messages", "stream"})
"""Body fields this class owns; a per-call keyword may not replace them."""

OPENAI_FINISH_REASONS: dict[str, StopReason] = {
    "stop": "end",
    "length": "max_tokens",
    "content_filter": "refusal",
    "tool_calls": "tool_use",
}
"""``choices[].finish_reason`` values to the normalized :data:`StopReason`.

The values documented for ``finish_reason`` in the official OpenAPI document
(https://github.com/openai/openai-openapi, ``openapi.yaml``): ``stop`` for a
natural stop or a stop sequence, ``length`` for the token budget,
``content_filter`` for content omitted by a filter, ``tool_calls`` for a tool
call. Anything else (the deprecated ``function_call``, a provider's own
value) maps to ``other``.
"""


class OpenAICompatGenerator:
    """Text generation against any OpenAI-compatible ``/chat/completions``.

    Attributes:
        model (str): The model name as the provider spells it.
        base_url (str): API root, without a trailing slash.
    """

    def __init__(
        self,
        model: str,
        *,
        api_key: str,
        base_url: str = DEFAULT_OPENAI_URL,
        timeout: float = 120.0,
        extra_body: dict[str, Any] | None = None,
        extra_headers: dict[str, str] | None = None,
        http_client: HTTPClient | None = None,
        transport: httpx.AsyncBaseTransport | None = None,
        retry_policy: RetryPolicy | None = None,
        metrics: GenAIMetrics | None = None,
        forward_params: Iterable[str] = (),
        provider: str = "openai-compatible",
        max_tokens_field: str = "max_tokens",
        structured_output: StructuredOutputMode = "json_schema",
        effort_field: str | None = "reasoning_effort",
        effort_values: Mapping[str, str] | None = None,
    ) -> None:
        """Configure the client. No network call happens here.

        Args:
            model (str): Model name as the provider spells it
                (``"deepseek-flash"``, ...).
            api_key (str): Bearer token. Empty raises immediately rather
                than deferring to a 401 from inside whatever background job
                makes the first call — the failure is a configuration
                mistake, and it should surface where the configuration is
                read.
            base_url (str): API root. Provider-specific; the default points
                at OpenAI.
            timeout (float): Per-request HTTP timeout in seconds.
            extra_body (dict[str, Any] | None): Fields merged into every
                request body, for provider extensions the wire format does
                not standardize. Merged **under** the fields this class
                computes, so it cannot silently replace ``model`` or
                ``messages``.
            extra_headers (dict[str, str] | None): Headers added to every
                request (an OpenRouter ``HTTP-Referer``, an Azure
                ``api-key``). ``Authorization`` is always set from
                ``api_key`` and cannot be overridden here.
            http_client (HTTPClient | None): An injected client for
                connection reuse. When ``None``, one is created lazily and
                owned by this instance, which brings retry with exponential
                backoff, a per-host circuit-breaker and ``X-Request-ID``
                propagation.
            transport (httpx.AsyncBaseTransport | None): Explicit transport
                for the lazily-created client — pass an
                ``httpx.MockTransport`` in tests. Ignored when
                ``http_client`` is given.
            retry_policy (RetryPolicy | None): Retry configuration for the
                lazily-created client. ``None`` keeps the ``HTTPClient``
                defaults but does not retry a read timeout: a retry is a new
                request, so the provider starts the generation that outlived
                the timeout again from zero, and the failure would arrive
                after three timeouts instead of one. A connection
                error and a retryable status (``429``, ``5xx``) are still
                retried. Ignored when ``http_client`` is given.
            metrics (GenAIMetrics | None): Optional Prometheus recorder.
            forward_params (Iterable[str]): HuggingFace-only generation
                fields (``top_k``, ``repetition_penalty``, ``do_sample``)
                this provider accepts under the same name — vLLM's server,
                for one, documents ``top_k`` and ``repetition_penalty``.
                Empty by default: those fields are dropped, because a
                strict provider (OpenAI, as reported) refuses the body.
            provider (str): Label carried by the errors this instance raises
                (``"openai"``, ``"deepseek"``, ...), so a log line says which
                account failed.
            max_tokens_field (str): Body field that ``max_new_tokens`` maps
                to. ``"max_tokens"`` by default, which DeepSeek and most
                compatible servers read; the OpenAI spec marks ``max_tokens``
                deprecated in favor of ``max_completion_tokens``, which
                ``HostedGenAISettings.openai_kwargs()`` selects.
            structured_output (StructuredOutputMode): How
                :meth:`chat_structured` asks for JSON — ``"json_schema"``
                (OpenAI) or ``"json_object"`` (DeepSeek).
            effort_field (str | None): Body field that the ``effort`` of
                :meth:`complete_structured` goes to. ``"reasoning_effort"``
                by default, the field the OpenAI spec defines; ``None`` drops
                ``effort`` instead of sending it, for a provider that
                documents no such field (DeepSeek's kwargs in
                :func:`~tempest_fastapi_sdk.genai.hosted_generator`).
            effort_values (Mapping[str, str] | None): Translation of
                :data:`~tempest_fastapi_sdk.genai.Effort` values into the
                provider's own, for a provider that documents a narrower
                set (Gemini's ``GOOGLE_EFFORT_VALUES``). ``None`` sends the
                value as given. A value missing from the mapping is sent as
                given.

        Raises:
            ValueError: When ``api_key`` is empty.
        """
        if not api_key:
            raise ValueError(
                "OpenAICompatGenerator requires an api_key; it was empty. "
                "Read it from settings/environment at construction time.",
            )
        self.model = model
        self.base_url = base_url.rstrip("/")
        self.timeout = timeout
        self.metrics = metrics
        self._api_key = api_key
        self._extra_body = dict(extra_body or {})
        self._extra_headers = dict(extra_headers or {})
        self._forward_params: frozenset[str] = frozenset(forward_params)
        self.provider = provider
        self.max_tokens_field = max_tokens_field
        self.structured_output: StructuredOutputMode = structured_output
        self.effort_field: str | None = effort_field
        self.effort_values: dict[str, str] = dict(effort_values or {})
        self._client: HTTPClient | None = http_client
        self._owns_client: bool = http_client is None
        self._transport = transport
        self._retry_policy = retry_policy

    @property
    def is_loaded(self) -> bool:
        """Return ``True`` — the provider owns model residency.

        Present for surface-compatibility with ``TextGenerator``.

        Returns:
            bool: Always ``True``.
        """
        return True

    def load(self) -> None:
        """No-op load hook, for surface-compatibility with ``TextGenerator``."""
        return None

    def _http(self) -> HTTPClient:
        """Return the HTTP client, creating an owned one on first use.

        Built lazily so constructing the generator never needs a running
        event loop — backends are commonly instantiated at module import,
        before one exists.

        Returns:
            HTTPClient: The resilient client used for API requests.
        """
        if self._client is None:
            from tempest_fastapi_sdk.utils.http_client import HTTPClient, RetryPolicy

            self._client = HTTPClient(
                timeout=self.timeout,
                transport=self._transport,
                retry_policy=self._retry_policy
                or RetryPolicy(retry_on_read_timeout=False),
            )
        return self._client

    def _headers(self) -> dict[str, str]:
        """Build the request headers, with ``Authorization`` last.

        Returns:
            dict[str, str]: Headers for one request.
        """
        headers = dict(self._extra_headers)
        headers["Authorization"] = f"Bearer {self._api_key}"
        return headers

    def _body(
        self,
        messages: list[dict[str, Any]],
        config: GenerationConfig | None,
        overrides: dict[str, Any],
        *,
        stream: bool,
    ) -> dict[str, Any]:
        """Assemble one request body.

        ``extra_body`` goes in first so the computed fields win: a caller
        cannot accidentally redirect the call to another model through it.

        HuggingFace-only fields (``do_sample``, ``top_k``,
        ``repetition_penalty``) are not part of the wire format and a strict
        provider refuses them, so they are dropped unless named in
        ``forward_params``. ``do_sample=False`` without an explicit
        ``temperature`` becomes ``temperature=0``.

        Args:
            messages (list[dict[str, Any]]): The chat turns.
            config (GenerationConfig | None): Typed parameters.
            overrides (dict[str, Any]): Per-call parameters, winning over
                ``config``. HuggingFace-style names are translated; anything
                else is passed through untouched, for provider extensions.
            stream (bool): Whether to ask for a streamed response.

        Returns:
            dict[str, Any]: The JSON body to POST.

        Raises:
            TypeError: When ``overrides`` names ``model``, ``messages`` or
                ``stream`` — fields this class computes and a keyword must
                not silently replace.
        """
        reserved = _RESERVED_BODY_KEYS.intersection(overrides)
        if reserved:
            raise TypeError(
                f"{sorted(reserved)} cannot be passed as a generation keyword; "
                "the model is fixed at construction and the messages and "
                "stream flag are set by the method you call.",
            )
        params: dict[str, Any] = {}
        if config is not None:
            params.update(config.model_dump(exclude_none=True, exclude_unset=True))
        params.update(overrides)

        body: dict[str, Any] = dict(self._extra_body)
        body["model"] = self.model
        body["messages"] = _wire_messages(messages)
        body["stream"] = stream
        for name, value in params.items():
            if value is None or (name == "stop" and not value):
                continue
            if name in _HF_ONLY_PARAMS and name not in self._forward_params:
                if (
                    name == "do_sample"
                    and value is False
                    and params.get("temperature") is None
                ):
                    body["temperature"] = 0.0
                continue
            wire = _PARAM_NAMES.get(name, name)
            if name == "max_new_tokens":
                wire = self.max_tokens_field
            body[wire] = value
        return body

    async def _post(self, body: dict[str, Any]) -> dict[str, Any]:
        """POST one non-streaming request and return the decoded response.

        Args:
            body (dict[str, Any]): The request body.

        Returns:
            dict[str, Any]: The decoded JSON response.

        Raises:
            HostedAPIError: For an error status — a
                :class:`~tempest_fastapi_sdk.genai.HostedAuthenticationError`
                (401/403), :class:`~tempest_fastapi_sdk.genai.HostedRateLimitError`
                (429) or :class:`~tempest_fastapi_sdk.genai.HostedServerError`
                (5xx). All are ``httpx.HTTPStatusError``, which is what this
                method raised before, so existing handlers keep catching them.
        """
        response = await self._http().post(
            f"{self.base_url}/chat/completions",
            json=body,
            headers=self._headers(),
        )
        raise_for_hosted_status(self.provider, response)
        decoded: dict[str, Any] = response.json()
        return decoded

    @staticmethod
    def _content(payload: dict[str, Any]) -> str:
        """Pull the assistant text out of a response.

        Args:
            payload (dict[str, Any]): The decoded response.

        Returns:
            str: ``choices[0].message.content``, or ``""`` when the provider
            returned a choice with no content.
        """
        choices = payload.get("choices") or []
        if not choices:
            return ""
        message = choices[0].get("message") or {}
        return str(message.get("content") or "")

    async def generate_with_usage(
        self,
        prompt: str,
        *,
        config: GenerationConfig | None = None,
        system: str | None = None,
        **kwargs: Any,
    ) -> tuple[str, TokenUsage | None]:
        """Generate a completion and report what it cost.

        Args:
            prompt (str): The input text.
            config (GenerationConfig | None): Typed generation parameters.
            system (str | None): A system turn placed before the prompt.
                Worth using for long instructions: an instruction pasted on
                top of a long document competes with it for attention, while
                a system turn does not.
            **kwargs (Any): Per-call parameters, winning over ``config``.

        Returns:
            tuple[str, TokenUsage | None]: The text, and the provider's
            reported usage — ``None`` when the response carried none.
        """
        messages: list[dict[str, Any]] = []
        if system:
            messages.append({"role": "system", "content": system})
        messages.append({"role": "user", "content": prompt})
        return await self.chat_with_usage(messages, config=config, **kwargs)

    async def chat_with_usage(
        self,
        messages: list[dict[str, Any]],
        *,
        config: GenerationConfig | None = None,
        **kwargs: Any,
    ) -> tuple[str, TokenUsage | None]:
        """Continue a chat and report what it cost.

        Args:
            messages (list[dict[str, Any]]): The chat turns, oldest first.
            config (GenerationConfig | None): Typed generation parameters.
            **kwargs (Any): Per-call parameters, winning over ``config``.

        Returns:
            tuple[str, TokenUsage | None]: The reply, and the provider's
            reported usage.
        """
        body = self._body(messages, config, kwargs, stream=False)
        payload = await self._post(body)
        return self._content(payload), TokenUsage.from_payload(payload.get("usage"))

    async def generate(
        self,
        prompt: str,
        *,
        config: GenerationConfig | None = None,
        **kwargs: Any,
    ) -> str:
        """Generate a completion for ``prompt``.

        The :class:`~tempest_fastapi_sdk.genai.text.TextBackend` signature.
        Use :meth:`generate_with_usage` when the token counts matter.

        Args:
            prompt (str): The input text.
            config (GenerationConfig | None): Typed generation parameters.
            **kwargs (Any): Per-call parameters, winning over ``config``.

        Returns:
            str: The generated text.
        """
        text, _usage = await self.generate_with_usage(
            prompt,
            config=config,
            **kwargs,
        )
        return text

    async def chat(
        self,
        messages: list[dict[str, Any]],
        *,
        config: GenerationConfig | None = None,
        **kwargs: Any,
    ) -> str:
        """Continue a chat and return the reply.

        Args:
            messages (list[dict[str, Any]]): The chat turns, oldest first.
            config (GenerationConfig | None): Typed generation parameters.
            **kwargs (Any): Per-call parameters, winning over ``config``.

        Returns:
            str: The generated reply.
        """
        text, _usage = await self.chat_with_usage(messages, config=config, **kwargs)
        return text

    async def stream(
        self,
        prompt: str,
        *,
        config: GenerationConfig | None = None,
        **kwargs: Any,
    ) -> AsyncIterator[str]:
        """Stream a completion piece by piece.

        Args:
            prompt (str): The input text.
            config (GenerationConfig | None): Typed generation parameters.
            **kwargs (Any): Per-call parameters, winning over ``config``.

        Yields:
            str: Content deltas, in order.

        Raises:
            HostedAPIError: A typed subclass when the stream cannot be
                opened (bad key, rate limit, outage).
        """
        body = self._body(
            [{"role": "user", "content": prompt}],
            config,
            kwargs,
            stream=True,
        )
        try:
            async for line in self._http().stream(
                "POST",
                f"{self.base_url}/chat/completions",
                json=body,
                headers=self._headers(),
            ):
                chunk = _sse_delta(line)
                if chunk:
                    yield chunk
        except HostedAPIError:
            raise
        except httpx.HTTPStatusError as exc:
            raise hosted_error(self.provider, exc.response) from exc

    async def chat_with_tools(
        self,
        messages: list[dict[str, Any]],
        tools: list[dict[str, Any]],
        *,
        config: GenerationConfig | None = None,
        **kwargs: Any,
    ) -> dict[str, Any]:
        """Generate a reply that may call tools (the ``ToolCallingBackend`` method).

        ``tools`` is the ``{"type": "function", "function": {...}}`` list the
        :class:`~tempest_fastapi_sdk.agents.Agent` builds, which is already
        the wire shape. The reply's ``tool_calls`` come back as the provider
        sent them — ``arguments`` a JSON **string**, per the
        ``ChatCompletionMessageToolCall`` schema of the OpenAI spec — so the
        assistant turn the agent appends is replayed unchanged on the next
        call.

        Args:
            messages (list[dict[str, Any]]): The conversation so far,
                including assistant turns with ``tool_calls`` and
                ``role: tool`` results.
            tools (list[dict[str, Any]]): Function specs.
            config (GenerationConfig | None): Typed generation parameters.
            **kwargs (Any): Per-call parameters, winning over ``config``
                (``tool_choice``, for one).

        Returns:
            dict[str, Any]: ``{"role": "assistant", "content": <text>,
            "tool_calls": [...]}`` — ``content`` is ``""`` when the provider
            sent ``null``, and ``tool_calls`` is ``[]`` when it asked for none.
        """
        body = self._body(messages, config, kwargs, stream=False)
        if tools:
            body["tools"] = tools
        payload = await self._post(body)
        choices = payload.get("choices") or []
        first: dict[str, Any] = (choices[0].get("message") if choices else None) or {}
        message: dict[str, Any] = dict(first)
        message["role"] = "assistant"
        message["content"] = str(message.get("content") or "")
        message["tool_calls"] = list(message.get("tool_calls") or [])
        return message

    async def chat_structured(
        self,
        messages: list[dict[str, Any]],
        schema: type[StructuredT],
        *,
        config: GenerationConfig | None = None,
        **kwargs: Any,
    ) -> StructuredT:
        """Generate a reply constrained to a Pydantic ``schema``.

        With ``structured_output="json_schema"`` the schema goes out as
        ``response_format`` (``{"type": "json_schema", "json_schema":
        {"name", "schema"}}``), named after the model class. ``strict`` is
        left at the spec's default (``false``): strict mode accepts only a
        subset of JSON Schema, and a Pydantic model with defaults can fall
        outside it. With ``"json_object"`` the provider is asked for a JSON
        object and the schema is stated in a trailing system turn — DeepSeek's
        JSON mode requires the word "json" in the prompt
        (https://api-docs.deepseek.com/guides/json_mode). Either way the reply
        is validated here against ``schema``.

        Args:
            messages (list[dict[str, Any]]): Chat turns; put the instruction
                in a ``system`` turn and the content in ``user``.
            schema (type[StructuredT]): The Pydantic model to produce.
            config (GenerationConfig | None): Typed generation parameters.
            **kwargs (Any): Per-call parameters, winning over ``config``.

        Returns:
            StructuredT: The validated instance.

        Raises:
            TypeError: When ``response_format`` is passed as a keyword — the
                schema argument is what sets it.
            ValueError: When the reply carries no JSON object (DeepSeek
                documents that JSON mode can return empty content).
            pydantic.ValidationError: When the JSON fails ``schema``.
        """
        body = self._structured_body(
            "chat_structured", messages, schema, config, kwargs
        )
        payload = await self._post(body)
        return parse_structured(self._content(payload), schema)

    def _structured_body(
        self,
        method: str,
        messages: list[dict[str, Any]],
        schema: type[StructuredT],
        config: GenerationConfig | None,
        overrides: dict[str, Any],
    ) -> dict[str, Any]:
        """Assemble the body of a schema-constrained call.

        Args:
            method (str): The public method's name, for the error message.
            messages (list[dict[str, Any]]): The chat turns.
            schema (type[StructuredT]): The Pydantic model to produce.
            config (GenerationConfig | None): Typed parameters.
            overrides (dict[str, Any]): Per-call parameters.

        Returns:
            dict[str, Any]: The JSON body, ``response_format`` included.

        Raises:
            TypeError: When ``response_format`` is in ``overrides`` — the
                schema argument is what sets it.
        """
        if "response_format" in overrides:
            raise TypeError(
                f"{method}() sets response_format from the schema "
                "argument; passing it as a keyword would replace it.",
            )
        json_schema = schema.model_json_schema()
        if self.structured_output == "json_object":
            instruction = (
                "Answer with a single JSON object that validates against this "
                f"JSON schema, and nothing else:\n{json.dumps(json_schema)}"
            )
            turns = [*messages, {"role": "system", "content": instruction}]
            body = self._body(turns, config, overrides, stream=False)
            body["response_format"] = {"type": "json_object"}
            return body
        body = self._body(messages, config, overrides, stream=False)
        name = _SCHEMA_NAME_INVALID.sub("_", schema.__name__)[:64] or "schema"
        body["response_format"] = {
            "type": "json_schema",
            "json_schema": {"name": name, "schema": json_schema},
        }
        return body

    async def complete_structured(
        self,
        messages: list[dict[str, Any]],
        schema: type[StructuredT],
        *,
        effort: Effort | None = None,
        cache_system: bool = False,
        max_tokens: int | None = None,
        config: GenerationConfig | None = None,
        **kwargs: Any,
    ) -> StructuredCompletion[StructuredT]:
        """Generate a reply constrained to ``schema`` and report the outcome.

        Builds the request as :meth:`chat_structured` does, and returns a
        :class:`~tempest_fastapi_sdk.genai.StructuredCompletion` instead of
        raising for an outcome the model produced: a refusal, a reply cut at
        the budget or a reply that fails ``schema`` come back with the usage
        the call was billed for.

        ``finish_reason`` is normalized through :data:`OPENAI_FINISH_REASONS`;
        a non-null ``message.refusal`` (the OpenAI spec's refusal message)
        is a ``refusal`` whatever the ``finish_reason`` says.

        Args:
            messages (list[dict[str, Any]]): Chat turns; images as
                :func:`~tempest_fastapi_sdk.genai.image_part` parts, which
                go out unchanged.
            schema (type[StructuredT]): The Pydantic model to produce.
            effort (Effort | None): Reasoning effort, sent as
                ``effort_field`` after the ``effort_values`` translation;
                dropped when ``effort_field`` is ``None``. ``None`` sends
                nothing.
            cache_system (bool): Accepted for interchangeability with
                :class:`~tempest_fastapi_sdk.genai.AnthropicGenerator` and
                not sent: the OpenAI wire format has no per-message cache
                marker, and what the provider caches is decided on its side.
            max_tokens (int | None): Output budget, sent as
                ``max_tokens_field``. ``None`` leaves the body without it
                unless ``config`` sets ``max_new_tokens``.
            config (GenerationConfig | None): Typed generation parameters.
            **kwargs (Any): Per-call body fields, winning over ``config``.

        Returns:
            StructuredCompletion[StructuredT]: The outcome.

        Raises:
            TypeError: When ``response_format`` is passed as a keyword.
            HostedAPIError: A typed subclass for an error status.
        """
        overrides = dict(kwargs)
        if max_tokens is not None:
            overrides["max_new_tokens"] = max_tokens
        body = self._structured_body(
            "complete_structured", messages, schema, config, overrides
        )
        if effort is not None and self.effort_field is not None:
            body[self.effort_field] = self.effort_values.get(effort, effort)
        payload = await self._post(body)
        choices = payload.get("choices") or []
        choice: dict[str, Any] = choices[0] if choices else {}
        message = choice.get("message") or {}
        raw = choice.get("finish_reason")
        provider_stop = str(raw) if raw is not None else None
        stop: StopReason = OPENAI_FINISH_REASONS.get(provider_stop or "", "other")
        if message.get("refusal"):
            stop = "refusal"
        return build_structured_completion(
            self._content(payload),
            schema,
            usage=TokenUsage.from_payload(payload.get("usage")),
            stop_reason=stop,
            provider_stop_reason=provider_stop,
        )

    async def aclose(self) -> None:
        """Close the HTTP client when this instance owns it.

        An injected client is left open for whoever injected it.
        """
        if self._client is not None and self._owns_client:
            await self._client.aclose()
            self._client = None


def _sse_delta(line: str) -> str:
    """Extract the content delta from one server-sent-event line.

    The stream is ``data: {json}`` lines with a ``data: [DONE]`` sentinel and
    blank lines between events. Anything that is not a decodable data line
    yields nothing rather than raising: a keep-alive comment must not abort a
    generation.

    Args:
        line (str): One raw line from the response body.

    Returns:
        str: The delta content, or ``""`` when this line carries none.
    """
    decoded = sse_data(line)
    if decoded is None:
        return ""
    choices = decoded.get("choices") or []
    if not choices:
        return ""
    delta = choices[0].get("delta") or {}
    return str(delta.get("content") or "")


def _wire_messages(messages: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Bring tool-call history into the shape the OpenAI wire format requires.

    The spec types a tool call's ``arguments`` as a JSON **string** and
    requires ``type: "function"``; a conversation started on another backend
    (Ollama, ``AnthropicGenerator``) carries ``arguments`` as a dict and may
    omit ``type``. Turns without ``tool_calls`` are passed through as the
    same objects.

    Args:
        messages (list[dict[str, Any]]): The conversation.

    Returns:
        list[dict[str, Any]]: The conversation, with tool calls normalized.
    """
    out: list[dict[str, Any]] = []
    for message in messages:
        calls = message.get("tool_calls")
        if not calls:
            out.append(message)
            continue
        normalized: list[dict[str, Any]] = []
        for call in calls:
            function = dict(call.get("function") or {})
            arguments = function.get("arguments")
            if not isinstance(arguments, str):
                function["arguments"] = json.dumps(arguments or {})
            entry = dict(call)
            entry["type"] = entry.get("type") or "function"
            entry["function"] = function
            normalized.append(entry)
        out.append({**message, "tool_calls": normalized})
    return out


class OpenAICompatEmbedder:
    """Embeddings over an OpenAI-compatible ``/embeddings`` endpoint.

    Satisfies :class:`~tempest_fastapi_sdk.genai.rag.SupportsEmbed`, so it
    takes the place of :class:`~tempest_fastapi_sdk.genai.OllamaEmbedder` in
    a :class:`~tempest_fastapi_sdk.genai.rag.Retriever`. The request and
    response shapes are the ``CreateEmbeddingRequest`` /
    ``CreateEmbeddingResponse`` schemas of the official OpenAPI document
    (https://github.com/openai/openai-openapi): ``input`` is a list of at most
    2048 strings, and each returned item carries its ``index``.

    Attributes:
        model (str): The embedding model id.
        base_url (str): API root, without a trailing slash.
        dimensions (int | None): Requested vector size, for models that
            accept one.
    """

    def __init__(
        self,
        model: str,
        *,
        api_key: str,
        base_url: str = DEFAULT_OPENAI_URL,
        timeout: float = 60.0,
        dimensions: int | None = None,
        extra_headers: dict[str, str] | None = None,
        http_client: HTTPClient | None = None,
        transport: httpx.AsyncBaseTransport | None = None,
        retry_policy: RetryPolicy | None = None,
        provider: str = "openai-compatible",
    ) -> None:
        """Configure the client. No network call happens here.

        Args:
            model (str): Embedding model id, e.g. ``"text-embedding-3-small"``
                (the example the OpenAI spec uses).
            api_key (str): Bearer token. Empty raises here.
            base_url (str): API root; ``/embeddings`` is appended.
            timeout (float): Per-request HTTP timeout in seconds.
            dimensions (int | None): Sent as ``dimensions`` when set.
            extra_headers (dict[str, str] | None): Headers added to every
                request; ``Authorization`` always comes from ``api_key``.
            http_client (HTTPClient | None): An injected client; one is
                created lazily and owned by this instance when ``None``.
            transport (httpx.AsyncBaseTransport | None): Transport for the
                lazily-created client (tests). Ignored with ``http_client``.
            retry_policy (RetryPolicy | None): Retry configuration for the
                lazily-created client; ``None`` uses the ``HTTPClient``
                defaults.
            provider (str): Label carried by the errors this raises.

        Raises:
            ValueError: When ``api_key`` is empty.
        """
        if not api_key:
            raise ValueError(
                "OpenAICompatEmbedder requires an api_key; it was empty.",
            )
        self.model = model
        self.base_url = base_url.rstrip("/")
        self.timeout = timeout
        self.dimensions = dimensions
        self.provider = provider
        self._api_key = api_key
        self._extra_headers = dict(extra_headers or {})
        self._client: HTTPClient | None = http_client
        self._owns_client: bool = http_client is None
        self._transport = transport
        self._retry_policy = retry_policy

    def _http(self) -> HTTPClient:
        """Return the HTTP client, creating an owned one on first use.

        Returns:
            HTTPClient: The resilient client used for API requests.
        """
        if self._client is None:
            from tempest_fastapi_sdk.utils.http_client import HTTPClient

            self._client = HTTPClient(
                timeout=self.timeout,
                transport=self._transport,
                retry_policy=self._retry_policy,
            )
        return self._client

    def unload(self) -> None:
        """No-op unload hook, for the registry's ``Unloadable`` protocol."""
        return None

    async def aclose(self) -> None:
        """Close the HTTP client when this instance owns it."""
        if self._client is not None and self._owns_client:
            await self._client.aclose()
            self._client = None

    async def embed(
        self,
        texts: str | list[str],
        *,
        batch_size: int = 256,
    ) -> list[list[float]]:
        """Embed one or many texts into vectors.

        Args:
            texts (str | list[str]): A single text or a list of texts.
            batch_size (int): Texts per request, at most 2048 (the spec's
                ``maxItems`` for ``input``).

        Returns:
            list[list[float]]: One vector per input text, in input order —
            ordered by the returned ``index``, not by arrival. ``[]`` for
            empty input.

        Raises:
            ValueError: When ``batch_size`` is outside ``1..2048``.
            HostedAPIError: A typed subclass for any error status.
        """
        if not 1 <= batch_size <= 2048:
            raise ValueError("batch_size must be between 1 and 2048")
        items = [texts] if isinstance(texts, str) else list(texts)
        if not items:
            return []
        headers = dict(self._extra_headers)
        headers["Authorization"] = f"Bearer {self._api_key}"
        vectors: list[list[float]] = []
        for start in range(0, len(items), batch_size):
            batch = items[start : start + batch_size]
            body: dict[str, Any] = {"model": self.model, "input": batch}
            if self.dimensions is not None:
                body["dimensions"] = self.dimensions
            response = await self._http().post(
                f"{self.base_url}/embeddings",
                json=body,
                headers=headers,
            )
            raise_for_hosted_status(self.provider, response)
            data: list[dict[str, Any]] = response.json().get("data") or []
            ordered = sorted(data, key=lambda item: int(item.get("index", 0)))
            vectors.extend(
                [float(x) for x in item.get("embedding") or []] for item in ordered
            )
        return vectors


__all__: list[str] = [
    "DEFAULT_DEEPSEEK_MODEL",
    "DEFAULT_DEEPSEEK_URL",
    "DEFAULT_OPENAI_URL",
    "OPENAI_FINISH_REASONS",
    "OpenAICompatEmbedder",
    "OpenAICompatGenerator",
    "StructuredOutputMode",
]
