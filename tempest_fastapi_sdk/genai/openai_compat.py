"""Hosted text generation over the OpenAI ``/chat/completions`` wire format.

The wire format is the common denominator: DeepSeek, Groq, Together,
OpenRouter, Mistral, vLLM's server, TGI's OpenAI route and Azure all
document an OpenAI-compatible ``/chat/completions``, so one client reaches
them by changing ``base_url`` and ``model``.

That list is what those providers advertise, not a matrix this repo runs —
the tests drive an ``httpx.MockTransport``, so what they pin is the request
this class builds and the response it reads, not any live provider. A
provider that deviates from the format deviates from these tests too.

:class:`OpenAICompatGenerator` satisfies
:class:`~tempest_fastapi_sdk.genai.text.TextBackend` — the protocol's own
docstring names "a hosted API" as the case it is meant to be filled with —
so it drops into :func:`~tempest_fastapi_sdk.genai.make_genai_router` and
:class:`~tempest_fastapi_sdk.genai.AIChatPipeline` beside the local
``TextGenerator`` and the ``OllamaGenerator``.

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
    ...     "deepseek-chat",
    ...     api_key="sk-...",
    ...     base_url="https://api.deepseek.com",
    ... )
    >>> text, usage = await gen.generate_with_usage("Explain PIX briefly.")
"""

from __future__ import annotations

import json
from collections.abc import AsyncIterator
from typing import TYPE_CHECKING, Any

from tempest_fastapi_sdk.genai.tokens import TokenUsage

if TYPE_CHECKING:
    import httpx

    from tempest_fastapi_sdk.genai.metrics import GenAIMetrics
    from tempest_fastapi_sdk.genai.schemas import GenerationConfig
    from tempest_fastapi_sdk.utils.http_client import HTTPClient, RetryPolicy

DEFAULT_OPENAI_URL: str = "https://api.openai.com/v1"
"""Where the wire format originates; override for any other provider."""

# GenerationConfig speaks HuggingFace; the wire format speaks OpenAI. Only
# the names differ — the meaning is the same on both sides.
_PARAM_NAMES: dict[str, str] = {
    "max_new_tokens": "max_tokens",
    "temperature": "temperature",
    "top_p": "top_p",
    "seed": "seed",
    "stop": "stop",
}


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
    ) -> None:
        """Configure the client. No network call happens here.

        Args:
            model (str): Model name as the provider spells it
                (``"gpt-4o-mini"``, ``"deepseek-chat"``, ...).
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
                lazily-created client. Ignored when ``http_client`` is given.
            metrics (GenAIMetrics | None): Optional Prometheus recorder.

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
            from tempest_fastapi_sdk.utils.http_client import HTTPClient

            self._client = HTTPClient(
                timeout=self.timeout,
                transport=self._transport,
                retry_policy=self._retry_policy,
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

        Args:
            messages (list[dict[str, Any]]): The chat turns.
            config (GenerationConfig | None): Typed parameters.
            overrides (dict[str, Any]): Per-call parameters, winning over
                ``config``. HuggingFace-style names are translated; anything
                else is passed through untouched, for provider extensions.
            stream (bool): Whether to ask for a streamed response.

        Returns:
            dict[str, Any]: The JSON body to POST.
        """
        params: dict[str, Any] = {}
        if config is not None:
            params.update(config.model_dump(exclude_none=True, exclude_unset=True))
        params.update(overrides)

        body: dict[str, Any] = dict(self._extra_body)
        body["model"] = self.model
        body["messages"] = messages
        body["stream"] = stream
        for name, value in params.items():
            if value is None or (name == "stop" and not value):
                continue
            body[_PARAM_NAMES.get(name, name)] = value
        return body

    async def _post(self, body: dict[str, Any]) -> dict[str, Any]:
        """POST one non-streaming request and return the decoded response.

        Args:
            body (dict[str, Any]): The request body.

        Returns:
            dict[str, Any]: The decoded JSON response.

        Raises:
            httpx.HTTPStatusError: Propagated unchanged for an error status
                (401 bad key, 429 rate limit, 5xx provider outage). The
                caller decides how to log and whether to retry; translating
                it here would hide the status code that says which.
        """
        response = await self._http().post(
            f"{self.base_url}/chat/completions",
            json=body,
            headers=self._headers(),
        )
        response.raise_for_status()
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
        """
        body = self._body(
            [{"role": "user", "content": prompt}],
            config,
            kwargs,
            stream=True,
        )
        async for line in self._http().stream(
            "POST",
            f"{self.base_url}/chat/completions",
            json=body,
            headers=self._headers(),
        ):
            chunk = _sse_delta(line)
            if chunk:
                yield chunk

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
    stripped = line.strip()
    if not stripped.startswith("data:"):
        return ""
    payload = stripped[len("data:") :].strip()
    if not payload or payload == "[DONE]":
        return ""
    try:
        decoded = json.loads(payload)
    except ValueError:
        return ""
    choices = decoded.get("choices") or []
    if not choices:
        return ""
    delta = choices[0].get("delta") or {}
    return str(delta.get("content") or "")


__all__: list[str] = [
    "DEFAULT_OPENAI_URL",
    "OpenAICompatGenerator",
]
