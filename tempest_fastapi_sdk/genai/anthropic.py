"""Hosted text generation on the Anthropic Messages API.

:class:`AnthropicGenerator` is the Claude counterpart of
:class:`~tempest_fastapi_sdk.genai.OllamaGenerator`: it implements the same
protocols — :class:`~tempest_fastapi_sdk.genai.TextBackend` (``generate`` /
``chat`` / ``stream``),
:class:`~tempest_fastapi_sdk.genai.StructuredTextBackend`
(``chat_structured``) and the agent's
:class:`~tempest_fastapi_sdk.agents.ToolCallingBackend`
(``chat_with_tools``) — with the same message shapes, so a service swaps the
line that builds the backend and nothing else.

That sameness is the work this module does. The Messages API differs from
the OpenAI/Ollama chat shape the rest of the SDK speaks in four places, each
translated here on the way out and on the way back:

* ``system`` is a top-level field, not a turn
  (https://platform.claude.com/docs/en/api/messages);
* the reply is a list of content blocks, with tool calls as ``tool_use``
  blocks and tool results sent back as ``tool_result`` blocks in **one**
  ``user`` turn (https://platform.claude.com/docs/en/agents-and-tools/tool-use/overview);
* the stream is server-sent events typed ``message_start`` /
  ``content_block_delta`` / ``message_delta`` / ``ping`` / ``error``, and an
  error can arrive after the ``200``
  (https://platform.claude.com/docs/en/build-with-claude/streaming);
* structured output is ``output_config.format`` with ``type: json_schema``
  and a schema whose objects close their properties
  (https://platform.claude.com/docs/en/build-with-claude/structured-outputs).

No vendor SDK is used: the request is one POST to ``/v1/messages`` with the
``x-api-key`` and ``anthropic-version`` headers
(https://platform.claude.com/docs/en/api/overview), through the SDK's own
:class:`~tempest_fastapi_sdk.utils.http_client.HTTPClient`.

Example:

    >>> gen = AnthropicGenerator(api_key="sk-ant-...")
    >>> await gen.generate("Explain PIX in one sentence.")
"""

from __future__ import annotations

import json
from collections.abc import AsyncIterator
from typing import TYPE_CHECKING, Any

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
    stream_error,
    strict_json_schema,
)
from tempest_fastapi_sdk.genai.schemas import _layer_config
from tempest_fastapi_sdk.genai.structured import StructuredT, parse_structured
from tempest_fastapi_sdk.genai.tokens import TokenUsage
from tempest_fastapi_sdk.genai.tracing import genai_span

if TYPE_CHECKING:
    from tempest_fastapi_sdk.genai.schemas import GenerationConfig
    from tempest_fastapi_sdk.utils.http_client import HTTPClient, RetryPolicy

DEFAULT_ANTHROPIC_URL: str = "https://api.anthropic.com"
"""API root; requests go to ``{base_url}/v1/messages``.

From https://platform.claude.com/docs/en/api/overview.
"""

ANTHROPIC_VERSION: str = "2023-06-01"
"""The ``anthropic-version`` header value.

The example value of the required header at
https://platform.claude.com/docs/en/api/overview.
"""

DEFAULT_ANTHROPIC_MODEL: str = "claude-opus-5-5"
"""The model used when none is given.

The current Opus model id on the models table
(https://platform.claude.com/docs/en/models/overview) when this
was written. Pass ``model`` to choose another; model ids are never guessed
from a date.
"""

DEFAULT_ANTHROPIC_MAX_TOKENS: int = 16000
"""``max_tokens`` sent when the call sets no ``max_new_tokens``.

The Messages API requires ``max_tokens`` on every request
(https://platform.claude.com/docs/en/api/messages), unlike the OpenAI and
Ollama formats, where it is optional.
"""

ANTHROPIC_PROVIDER: str = "anthropic"
"""The ``provider`` label on the errors this backend raises."""

_PARAM_NAMES: dict[str, str] = {
    "max_new_tokens": "max_tokens",
    "temperature": "temperature",
    "top_p": "top_p",
    "top_k": "top_k",
    "stop": "stop_sequences",
}
"""``GenerationConfig`` names to their Messages API body fields."""

_DROPPED_PARAMS: frozenset[str] = frozenset(
    {"do_sample", "repetition_penalty", "seed"},
)
"""``GenerationConfig`` fields with no Messages API equivalent.

Dropped instead of forwarded, because the API answers an unknown body field
with a ``400``. ``do_sample=False`` is **not** turned into ``temperature=0``
the way the OpenAI-compatible backend does it: the current models reject
``temperature`` itself with a ``400``
(https://platform.claude.com/docs/en/api/errors), so the translation would
break every greedy call.
"""

_RESERVED_BODY_KEYS: frozenset[str] = frozenset(
    {"model", "messages", "stream", "system", "tools"},
)
"""Body fields this class computes; a per-call keyword may not replace them."""

ANTHROPIC_STOP_REASONS: dict[str, StopReason] = {
    "end_turn": "end",
    "stop_sequence": "end",
    "max_tokens": "max_tokens",
    "refusal": "refusal",
    "tool_use": "tool_use",
}
"""Messages API ``stop_reason`` values to the normalized :data:`StopReason`.

From the stop-reason table of https://platform.claude.com/docs/en/api/messages
(``end_turn``, ``max_tokens``, ``stop_sequence``, ``tool_use``,
``pause_turn``, ``refusal``). ``pause_turn`` and any value added later map to
``other``.
"""

_DATA_URL_PREFIX: str = "data:"
"""Prefix of an inline image URL, ``data:<media_type>;base64,<data>``."""


def _arguments(raw: Any) -> dict[str, Any]:
    """Decode tool-call arguments into the dict ``tool_use.input`` takes.

    Args:
        raw (Any): A dict, or the JSON string the OpenAI wire format uses.

    Returns:
        dict[str, Any]: The arguments; ``{}`` when they do not decode to an
        object.
    """
    if isinstance(raw, dict):
        return raw
    if isinstance(raw, str) and raw.strip():
        try:
            decoded = json.loads(raw)
        except ValueError:
            return {}
        return decoded if isinstance(decoded, dict) else {}
    return {}


def _content_text(content: Any) -> str:
    """Flatten a turn's ``content`` to text.

    Args:
        content (Any): A string, ``None``, or a list of OpenAI-style parts.

    Returns:
        str: The text it carries.
    """
    if content is None:
        return ""
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        parts: list[str] = []
        for part in content:
            if isinstance(part, dict) and part.get("type") == "text":
                parts.append(str(part.get("text") or ""))
            elif isinstance(part, str):
                parts.append(part)
        return "".join(parts)
    return str(content)


def to_anthropic_image(part: dict[str, Any]) -> dict[str, Any]:
    """Translate an OpenAI ``image_url`` content part into a Messages API block.

    A ``data:<media_type>;base64,<data>`` URL becomes a ``base64`` image
    source and any other URL a ``url`` source — the two source types of the
    vision guide (https://platform.claude.com/docs/en/build-with-claude/vision).

    Args:
        part (dict[str, Any]): ``{"type": "image_url", "image_url": {"url":
            ...}}``, or with ``image_url`` as the bare URL string.

    Returns:
        dict[str, Any]: ``{"type": "image", "source": {...}}``.

    Raises:
        ValueError: When the URL is empty, or a ``data:`` URL is not
            base64-encoded.
    """
    raw = part.get("image_url")
    url = str((raw.get("url") if isinstance(raw, dict) else raw) or "")
    if not url:
        raise ValueError("image_url part without a url")
    if not url.startswith(_DATA_URL_PREFIX):
        return {"type": "image", "source": {"type": "url", "url": url}}
    header, _, data = url[len(_DATA_URL_PREFIX) :].partition(",")
    media_type, _, encoding = header.partition(";")
    if encoding != "base64" or not data:
        raise ValueError("image data URL must be base64-encoded")
    return {
        "type": "image",
        "source": {"type": "base64", "media_type": media_type, "data": data},
    }


def _translate_parts(content: list[Any]) -> list[dict[str, Any]]:
    """Translate the parts of a list ``content`` into Messages API blocks.

    ``image_url`` parts become ``image`` blocks; bare strings become ``text``
    blocks; every other part (``text``, or a native Messages API block) is
    passed through untouched.

    Args:
        content (list[Any]): The turn's content parts.

    Returns:
        list[dict[str, Any]]: The blocks.
    """
    blocks: list[dict[str, Any]] = []
    for part in content:
        if isinstance(part, str):
            blocks.append({"type": "text", "text": part})
        elif isinstance(part, dict) and part.get("type") == "image_url":
            blocks.append(to_anthropic_image(part))
        elif isinstance(part, dict):
            blocks.append(part)
    return blocks


def to_anthropic_messages(
    messages: list[dict[str, Any]],
) -> tuple[str | None, list[dict[str, Any]]]:
    """Translate an OpenAI/Ollama-shaped conversation to the Messages API.

    * ``system`` turns are lifted out and joined into the top-level
      ``system`` field, which is where the Messages API reads it.
    * An assistant turn with ``tool_calls`` becomes ``text`` + ``tool_use``
      blocks; each call keeps its ``id``, and a call without one (history
      written by a backend that does not assign ids) gets ``call_<n>``.
    * ``role: tool`` results become ``tool_result`` blocks, and consecutive
      results are merged into **one** ``user`` turn — the tool-use guide asks
      for all results of a turn in a single message. A result without a
      ``tool_call_id`` answers the oldest unanswered call, in order.
    * A turn whose ``content`` is a list keeps it as blocks: OpenAI
      ``image_url`` parts (as :func:`~tempest_fastapi_sdk.genai.image_part`
      builds them) become ``image`` blocks through
      :func:`to_anthropic_image`, and native Messages API blocks are passed
      through untouched.

    Args:
        messages (list[dict[str, Any]]): The conversation, oldest first.

    Returns:
        tuple[str | None, list[dict[str, Any]]]: The ``system`` text (or
        ``None``) and the translated messages.
    """
    system_parts: list[str] = []
    out: list[dict[str, Any]] = []
    pending: list[str] = []
    counter = 0
    for message in messages:
        role = str(message.get("role") or "user")
        content = message.get("content")
        if role == "system":
            text = _content_text(content)
            if text:
                system_parts.append(text)
            continue
        if role == "tool":
            call_id = message.get("tool_call_id")
            if call_id:
                tool_use_id = str(call_id)
                if tool_use_id in pending:
                    pending.remove(tool_use_id)
            elif pending:
                tool_use_id = pending.pop(0)
            else:
                counter += 1
                tool_use_id = f"call_{counter}"
            block = {
                "type": "tool_result",
                "tool_use_id": tool_use_id,
                "content": _content_text(content),
            }
            last = out[-1] if out else None
            if (
                last is not None
                and last["role"] == "user"
                and isinstance(last["content"], list)
                and last["content"]
                and all(item.get("type") == "tool_result" for item in last["content"])
            ):
                last["content"].append(block)
            else:
                out.append({"role": "user", "content": [block]})
            continue
        calls = message.get("tool_calls") or []
        if role == "assistant" and calls:
            blocks: list[dict[str, Any]] = []
            text = _content_text(content)
            if text:
                blocks.append({"type": "text", "text": text})
            for call in calls:
                function = call.get("function") or {}
                call_id = call.get("id")
                if not call_id:
                    counter += 1
                    call_id = f"call_{counter}"
                pending.append(str(call_id))
                blocks.append(
                    {
                        "type": "tool_use",
                        "id": str(call_id),
                        "name": str(function.get("name") or ""),
                        "input": _arguments(function.get("arguments")),
                    },
                )
            out.append({"role": "assistant", "content": blocks})
            continue
        if isinstance(content, list) and any(
            isinstance(item, dict) and item.get("type") == "image_url"
            for item in content
        ):
            out.append({"role": role, "content": _translate_parts(content)})
        elif isinstance(content, list) and all(
            isinstance(item, dict) and "type" in item for item in content
        ):
            out.append({"role": role, "content": content})
        else:
            out.append({"role": role, "content": _content_text(content)})
    system = "\n\n".join(system_parts) if system_parts else None
    return system, out


def to_anthropic_tools(tools: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Translate OpenAI/Ollama function specs into Messages API tools.

    ``{"type": "function", "function": {name, description, parameters}}``
    becomes ``{name, description, input_schema}``. A spec that already has
    ``input_schema`` is passed through, so Anthropic-only tools still work.

    Args:
        tools (list[dict[str, Any]]): The specs, as
            :meth:`~tempest_fastapi_sdk.agents.AgentTool.to_spec` builds them.

    Returns:
        list[dict[str, Any]]: The ``tools`` field of the request.
    """
    translated: list[dict[str, Any]] = []
    for spec in tools:
        if "input_schema" in spec:
            translated.append(spec)
            continue
        function = spec.get("function") or spec
        tool: dict[str, Any] = {
            "name": str(function.get("name") or ""),
            "input_schema": function.get("parameters")
            or {"type": "object", "properties": {}},
        }
        description = function.get("description")
        if description:
            tool["description"] = str(description)
        translated.append(tool)
    return translated


def anthropic_usage(payload: Any) -> TokenUsage | None:
    """Read a Messages API ``usage`` object into a :class:`TokenUsage`.

    ``input_tokens`` there is only the **uncached** remainder; the prompt
    also includes ``cache_creation_input_tokens`` and
    ``cache_read_input_tokens``
    (https://platform.claude.com/docs/en/build-with-claude/prompt-caching).
    So :attr:`TokenUsage.input_tokens` is the sum of the three,
    :attr:`TokenUsage.cache_hit_tokens` is the cache read — the same meaning
    those fields have for the OpenAI-compatible providers — and
    :attr:`TokenUsage.cache_write_tokens` is the cache write, which the
    Messages API bills above the base input rate.

    Args:
        payload (Any): The ``usage`` object, or ``None``.

    Returns:
        TokenUsage | None: The usage, or ``None`` when ``payload`` is not a
        mapping.
    """
    if not isinstance(payload, dict):
        return None
    uncached = int(payload.get("input_tokens", 0) or 0)
    written = int(payload.get("cache_creation_input_tokens", 0) or 0)
    read = int(payload.get("cache_read_input_tokens", 0) or 0)
    output_tokens = int(payload.get("output_tokens", 0) or 0)
    input_tokens = uncached + written + read
    return TokenUsage(
        input_tokens=input_tokens,
        output_tokens=output_tokens,
        total_tokens=input_tokens + output_tokens,
        cache_hit_tokens=read,
        cache_write_tokens=written,
    )


class AnthropicGenerator:
    """Text generation, tool calling and structured output on Claude.

    Attributes:
        model (str): The model id.
        base_url (str): API root, without a trailing slash.
        max_tokens (int): ``max_tokens`` used when a call sets no
            ``max_new_tokens``.
        config (GenerationConfig | None): Default generation parameters,
            layered under every call's own.
    """

    def __init__(
        self,
        model: str = DEFAULT_ANTHROPIC_MODEL,
        *,
        api_key: str,
        base_url: str = DEFAULT_ANTHROPIC_URL,
        timeout: float = 120.0,
        max_tokens: int = DEFAULT_ANTHROPIC_MAX_TOKENS,
        config: GenerationConfig | None = None,
        extra_body: dict[str, Any] | None = None,
        extra_headers: dict[str, str] | None = None,
        anthropic_version: str = ANTHROPIC_VERSION,
        http_client: HTTPClient | None = None,
        transport: httpx.AsyncBaseTransport | None = None,
        retry_policy: RetryPolicy | None = None,
    ) -> None:
        """Configure the client. No network call happens here.

        Args:
            model (str): Model id (``"claude-opus-5-5"``,
                ``"claude-sonnet-5-5"``, ...).
            api_key (str): The API key, sent as ``x-api-key``. Empty raises
                here, where the configuration is read, instead of as a
                ``401`` from inside the first background job.
            base_url (str): API root; ``/v1/messages`` is appended.
            timeout (float): Per-request HTTP timeout in seconds. A long
                non-streaming generation can outlive it; prefer
                :meth:`stream` for long outputs, as the streaming guide
                recommends.
            max_tokens (int): Default ``max_tokens``, required by the API on
                every call. ``max_new_tokens`` in a config or keyword wins.
            config (GenerationConfig | None): Default generation parameters
                for every call. A call's own ``config`` is layered over it
                field by field, and keyword overrides win over both.
            extra_body (dict[str, Any] | None): Fields merged into every
                request body, **under** the computed ones — the place for
                ``thinking``, ``output_config.effort`` or ``metadata``.
            extra_headers (dict[str, str] | None): Headers added to every
                request (``anthropic-beta``, ``anthropic-workspace-id``).
                ``x-api-key`` and ``anthropic-version`` always come from the
                arguments above.
            anthropic_version (str): The ``anthropic-version`` header.
            http_client (HTTPClient | None): An injected client for
                connection reuse. When ``None`` one is created lazily and
                owned by this instance, with retry, backoff, a per-host
                circuit-breaker and ``X-Request-ID`` propagation.
            transport (httpx.AsyncBaseTransport | None): Transport for the
                lazily-created client — an ``httpx.MockTransport`` in tests.
                Ignored with ``http_client``.
            retry_policy (RetryPolicy | None): Retry configuration for the
                lazily-created client. ``None`` retries ``429``, the common
                ``5xx`` **and** ``529`` (Anthropic's ``overloaded_error``),
                but not a read timeout: the provider stops the generation
                when the client gives up, so a retry starts it again from
                zero. Ignored with ``http_client``.

        Raises:
            ValueError: When ``api_key`` is empty or ``max_tokens`` is not
                positive.
        """
        if not api_key:
            raise ValueError(
                "AnthropicGenerator requires an api_key; it was empty. "
                "Read it from settings/environment at construction time.",
            )
        if max_tokens <= 0:
            raise ValueError("max_tokens must be positive")
        self.model = model
        self.base_url = base_url.rstrip("/")
        self.timeout = timeout
        self.max_tokens = max_tokens
        self.config = config
        self._api_key = api_key
        self._version = anthropic_version
        self._extra_body = dict(extra_body or {})
        self._extra_headers = dict(extra_headers or {})
        self._client: HTTPClient | None = http_client
        self._owns_client: bool = http_client is None
        self._transport = transport
        self._retry_policy = retry_policy

    @property
    def url(self) -> str:
        """Return the Messages endpoint.

        Returns:
            str: ``{base_url}/v1/messages``.
        """
        return f"{self.base_url}/v1/messages"

    @property
    def is_loaded(self) -> bool:
        """Return ``True`` — the provider owns model residency.

        Returns:
            bool: Always ``True``.
        """
        return True

    def load(self) -> None:
        """No-op load hook, for surface-compatibility with ``TextGenerator``."""
        return None

    def unload(self) -> None:
        """No-op unload hook, for the registry's ``Unloadable`` protocol."""
        return None

    def _http(self) -> HTTPClient:
        """Return the HTTP client, creating an owned one on first use.

        Returns:
            HTTPClient: The resilient client used for API requests.
        """
        if self._client is None:
            from tempest_fastapi_sdk.utils.http_client import HTTPClient, RetryPolicy

            self._client = HTTPClient(
                timeout=self.timeout,
                transport=self._transport,
                retry_policy=self._retry_policy
                or RetryPolicy(
                    retry_on_read_timeout=False,
                    retry_statuses=frozenset({429, 500, 502, 503, 504, 529}),
                ),
            )
        return self._client

    async def aclose(self) -> None:
        """Close the HTTP client when this instance owns it."""
        if self._client is not None and self._owns_client:
            await self._client.aclose()
            self._client = None

    def _headers(self) -> dict[str, str]:
        """Build the request headers, credentials last so they cannot be overridden.

        Returns:
            dict[str, str]: Headers for one request.
        """
        headers = dict(self._extra_headers)
        headers["x-api-key"] = self._api_key
        headers["anthropic-version"] = self._version
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

        Args:
            messages (list[dict[str, Any]]): The conversation, in the
                OpenAI/Ollama shape; translated here.
            config (GenerationConfig | None): Typed parameters, layered over
                the instance default.
            overrides (dict[str, Any]): Per-call parameters, winning over
                ``config``. ``GenerationConfig`` names are translated; any
                other key is passed through as a body field.
            stream (bool): Whether to ask for a streamed response.

        Returns:
            dict[str, Any]: The JSON body to POST.

        Raises:
            TypeError: When ``overrides`` names a field this class computes.
        """
        reserved = _RESERVED_BODY_KEYS.intersection(overrides)
        if reserved:
            raise TypeError(
                f"{sorted(reserved)} cannot be passed as a generation keyword; "
                "the model is fixed at construction and the conversation, "
                "system prompt, tools and stream flag are set by the method "
                "you call.",
            )
        params: dict[str, Any] = {}
        layered = _layer_config(self.config, config)
        if layered is not None:
            params.update(layered.model_dump(exclude_none=True, exclude_unset=True))
        params.update(overrides)

        system, translated = to_anthropic_messages(messages)
        body: dict[str, Any] = dict(self._extra_body)
        body["model"] = self.model
        body["messages"] = translated
        body["max_tokens"] = self.max_tokens
        if system is not None:
            body["system"] = system
        if stream:
            body["stream"] = True
        for name, value in params.items():
            if value is None or name in _DROPPED_PARAMS:
                continue
            if name == "stop" and not value:
                continue
            body[_PARAM_NAMES.get(name, name)] = value
        return body

    async def _post(self, body: dict[str, Any]) -> dict[str, Any]:
        """POST one non-streaming request and return the decoded message.

        Args:
            body (dict[str, Any]): The request body.

        Returns:
            dict[str, Any]: The decoded Message.

        Raises:
            HostedAPIError: A typed subclass for any error status.
        """
        response = await self._http().post(
            self.url,
            json=body,
            headers=self._headers(),
        )
        raise_for_hosted_status(ANTHROPIC_PROVIDER, response)
        decoded: dict[str, Any] = response.json()
        return decoded

    @staticmethod
    def _text(payload: dict[str, Any]) -> str:
        """Join the ``text`` blocks of a reply.

        Thinking, ``tool_use`` and other block types are skipped. A reply
        that stopped with ``stop_reason: "refusal"`` can carry no text
        block; that comes back as ``""``.

        Args:
            payload (dict[str, Any]): The decoded Message.

        Returns:
            str: The reply text.
        """
        blocks = payload.get("content") or []
        return "".join(
            str(block.get("text") or "")
            for block in blocks
            if isinstance(block, dict) and block.get("type") == "text"
        )

    async def _send(
        self,
        operation: str,
        body: dict[str, Any],
    ) -> tuple[dict[str, Any], TokenUsage | None]:
        """Send a body under a tracing span and read its usage.

        Args:
            operation (str): The span operation (``"chat"``, ...).
            body (dict[str, Any]): The request body.

        Returns:
            tuple[dict[str, Any], TokenUsage | None]: The Message and its
            usage.
        """
        async with genai_span(operation, self.model) as span:
            payload = await self._post(body)
            usage = anthropic_usage(payload.get("usage"))
            if usage is not None:
                span.tokens_in = usage.input_tokens
                span.tokens_out = usage.output_tokens
            return payload, usage

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
                ``system`` turns become the top-level ``system`` field.
            config (GenerationConfig | None): Typed generation parameters.
            **kwargs (Any): Per-call parameters, winning over ``config``.

        Returns:
            tuple[str, TokenUsage | None]: The reply, and the usage the API
            reported (cache reads and writes included in ``input_tokens``).
        """
        body = self._body(messages, config, kwargs, stream=False)
        payload, usage = await self._send("chat", body)
        return self._text(payload), usage

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
            system (str | None): System prompt, sent as the top-level
                ``system`` field.
            **kwargs (Any): Per-call parameters, winning over ``config``.

        Returns:
            tuple[str, TokenUsage | None]: The text and its usage.
        """
        messages: list[dict[str, Any]] = []
        if system:
            messages.append({"role": "system", "content": system})
        messages.append({"role": "user", "content": prompt})
        return await self.chat_with_usage(messages, config=config, **kwargs)

    async def generate(
        self,
        prompt: str,
        *,
        config: GenerationConfig | None = None,
        **kwargs: Any,
    ) -> str:
        """Generate a completion for ``prompt`` (the ``TextBackend`` method).

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
        """Continue a chat and return the reply (the ``TextBackend`` method).

        Args:
            messages (list[dict[str, Any]]): The chat turns, oldest first.
            config (GenerationConfig | None): Typed generation parameters.
            **kwargs (Any): Per-call parameters, winning over ``config``.

        Returns:
            str: The reply.
        """
        text, _usage = await self.chat_with_usage(messages, config=config, **kwargs)
        return text

    async def chat_with_tools(
        self,
        messages: list[dict[str, Any]],
        tools: list[dict[str, Any]],
        *,
        config: GenerationConfig | None = None,
        **kwargs: Any,
    ) -> dict[str, Any]:
        """Generate a reply that may call tools (the ``ToolCallingBackend`` method).

        Tools go out in the Messages API shape and ``tool_use`` blocks come
        back as OpenAI-style ``tool_calls`` — the shape the
        :class:`~tempest_fastapi_sdk.agents.Agent` loop reads and appends
        back to the conversation, which :func:`to_anthropic_messages`
        translates again on the next call. Each call keeps the ``tool_use``
        id, so its result is matched by id.

        Args:
            messages (list[dict[str, Any]]): The conversation so far,
                including assistant turns with ``tool_calls`` and
                ``role: tool`` results.
            tools (list[dict[str, Any]]): ``{"type": "function", ...}``
                specs (or Messages API tools, passed through).
            config (GenerationConfig | None): Typed generation parameters.
            **kwargs (Any): Per-call parameters, winning over ``config``
                (``tool_choice``, for one).

        Returns:
            dict[str, Any]: ``{"role": "assistant", "content": <text>,
            "tool_calls": [...]}``, each call being
            ``{"id", "type": "function", "function": {"name", "arguments"}}``
            with ``arguments`` as a dict.
        """
        body = self._body(messages, config, kwargs, stream=False)
        if tools:
            body["tools"] = to_anthropic_tools(tools)
        payload, _usage = await self._send("chat", body)
        calls: list[dict[str, Any]] = []
        for block in payload.get("content") or []:
            if isinstance(block, dict) and block.get("type") == "tool_use":
                calls.append(
                    {
                        "id": str(block.get("id") or ""),
                        "type": "function",
                        "function": {
                            "name": str(block.get("name") or ""),
                            "arguments": _arguments(block.get("input")),
                        },
                    },
                )
        return {
            "role": "assistant",
            "content": self._text(payload),
            "tool_calls": calls,
        }

    async def chat_structured(
        self,
        messages: list[dict[str, Any]],
        schema: type[StructuredT],
        *,
        config: GenerationConfig | None = None,
        **kwargs: Any,
    ) -> StructuredT:
        """Generate a reply constrained to a Pydantic ``schema``.

        The schema goes out as ``output_config.format`` (``json_schema``),
        adapted by :func:`~tempest_fastapi_sdk.genai.hosted.strict_json_schema`
        to what the API accepts; the reply is validated against the original
        model, constraints the adaptation removed included. An
        ``output_config`` in ``extra_body`` (an ``effort``, say) is kept and
        only its ``format`` is set.

        Args:
            messages (list[dict[str, Any]]): Chat turns; put the instruction
                in a ``system`` turn and the content in ``user``.
            schema (type[StructuredT]): The Pydantic model to produce.
            config (GenerationConfig | None): Typed generation parameters.
            **kwargs (Any): Per-call parameters, winning over ``config``.

        Returns:
            StructuredT: The validated instance.

        Raises:
            TypeError: When ``output_config`` is passed as a keyword — the
                schema argument is what sets its format.
            ValueError: When the reply carries no JSON object (a refusal,
                or a reply cut at ``max_tokens``).
            pydantic.ValidationError: When the JSON fails ``schema``.
        """
        if "output_config" in kwargs:
            raise TypeError(
                "chat_structured() sets output_config.format from the schema "
                "argument; set other output_config fields through extra_body.",
            )
        body = self._body(messages, config, kwargs, stream=False)
        output_config = dict(body.get("output_config") or {})
        output_config["format"] = {
            "type": "json_schema",
            "schema": strict_json_schema(schema.model_json_schema()),
        }
        body["output_config"] = output_config
        payload, _usage = await self._send("chat", body)
        return parse_structured(self._text(payload), schema)

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

        Builds the request as :meth:`chat_structured` does
        (``output_config.format``), and returns a
        :class:`~tempest_fastapi_sdk.genai.StructuredCompletion` instead of
        raising for an outcome the model produced: a refusal, a reply cut at
        ``max_tokens`` or a reply that fails ``schema`` come back with the
        usage the call was billed for. ``stop_reason`` is normalized through
        :data:`ANTHROPIC_STOP_REASONS`, and the reply is parsed only after a
        natural stop.

        Args:
            messages (list[dict[str, Any]]): Chat turns; images as
                :func:`~tempest_fastapi_sdk.genai.image_part` parts.
            schema (type[StructuredT]): The Pydantic model to produce.
            effort (Effort | None): Sent as ``output_config.effort``.
                ``None`` keeps whatever ``extra_body`` sets, or nothing.
            cache_system (bool): Send the system prompt as one ``text``
                block marked ``cache_control: {"type": "ephemeral"}``
                (https://platform.claude.com/docs/en/build-with-claude/prompt-caching).
                Ignored when the conversation has no ``system`` turn.
            max_tokens (int | None): ``max_tokens`` of this call, replacing
                the instance default.
            config (GenerationConfig | None): Typed generation parameters.
            **kwargs (Any): Per-call body fields, winning over ``config``.

        Returns:
            StructuredCompletion[StructuredT]: The outcome.

        Raises:
            TypeError: When ``output_config`` is passed as a keyword.
            HostedAPIError: A typed subclass for an error status.
        """
        if "output_config" in kwargs:
            raise TypeError(
                "complete_structured() sets output_config from the schema and "
                "effort arguments; set other output_config fields through "
                "extra_body.",
            )
        body = self._body(messages, config, kwargs, stream=False)
        output_config = dict(body.get("output_config") or {})
        output_config["format"] = {
            "type": "json_schema",
            "schema": strict_json_schema(schema.model_json_schema()),
        }
        if effort is not None:
            output_config["effort"] = effort
        body["output_config"] = output_config
        if max_tokens is not None:
            body["max_tokens"] = max_tokens
        system = body.get("system")
        if cache_system and isinstance(system, str):
            body["system"] = [
                {
                    "type": "text",
                    "text": system,
                    "cache_control": {"type": "ephemeral"},
                },
            ]
        payload, usage = await self._send("chat", body)
        raw = payload.get("stop_reason")
        provider_stop = str(raw) if raw is not None else None
        return build_structured_completion(
            self._text(payload),
            schema,
            usage=usage,
            stop_reason=ANTHROPIC_STOP_REASONS.get(provider_stop or "", "other"),
            provider_stop_reason=provider_stop,
        )

    async def stream(
        self,
        prompt: str,
        *,
        config: GenerationConfig | None = None,
        **kwargs: Any,
    ) -> AsyncIterator[str]:
        """Stream a completion (the ``TextBackend`` method).

        Reads the server-sent events of the streaming guide: text arrives as
        ``content_block_delta`` events with a ``text_delta``; ``ping`` and
        the other event types carry no text and are skipped.

        Args:
            prompt (str): The input text.
            config (GenerationConfig | None): Typed generation parameters.
            **kwargs (Any): Per-call parameters, winning over ``config``.

        Yields:
            str: Text deltas, in order.

        Raises:
            HostedAPIError: A typed subclass when the stream cannot be
                opened, or when an ``error`` event arrives mid-stream
                (``overloaded_error`` raises :class:`HostedServerError` with
                ``status_code`` 529). Text already yielded stays yielded.
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
                self.url,
                json=body,
                headers=self._headers(),
            ):
                event = sse_data(line)
                if event is None:
                    continue
                kind = event.get("type")
                if kind == "error":
                    error = event.get("error") or {}
                    raise stream_error(
                        ANTHROPIC_PROVIDER,
                        self.url,
                        str(error.get("type") or "api_error"),
                        str(error.get("message") or ""),
                    )
                if kind == "content_block_delta":
                    delta = event.get("delta") or {}
                    if delta.get("type") == "text_delta":
                        text = str(delta.get("text") or "")
                        if text:
                            yield text
                elif kind == "message_stop":
                    return
        except HostedAPIError:
            raise
        except httpx.HTTPStatusError as exc:
            raise hosted_error(ANTHROPIC_PROVIDER, exc.response) from exc


__all__: list[str] = [
    "ANTHROPIC_PROVIDER",
    "ANTHROPIC_STOP_REASONS",
    "ANTHROPIC_VERSION",
    "DEFAULT_ANTHROPIC_MAX_TOKENS",
    "DEFAULT_ANTHROPIC_MODEL",
    "DEFAULT_ANTHROPIC_URL",
    "AnthropicGenerator",
    "anthropic_usage",
    "to_anthropic_image",
    "to_anthropic_messages",
    "to_anthropic_tools",
]
