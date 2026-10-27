"""Pick the hosted backend from the model id.

A service that should run on Claude today and on Gemini tomorrow wants to
change one environment variable, not the line that builds the backend.
:func:`hosted_generator` makes that true: it reads the provider off the
model id (:func:`resolve_provider`) and builds the generator with the wire
settings that provider documents — the endpoint, the field the output budget
goes to, the JSON mode, and the effort values it accepts. Every generator it
returns implements
:class:`~tempest_fastapi_sdk.genai.StructuredCompletionBackend`, so the call
site does not change either.

The per-provider values are named constants below, each citing where it
was read; the tests pin them, so a change upstream shows up as a failing
test rather than as a silent divergence. The tests drive an
``httpx.MockTransport``: they pin the request this module builds, not the
behavior of a live provider.
"""

from __future__ import annotations

from types import MappingProxyType
from typing import TYPE_CHECKING, Final, Literal

from tempest_fastapi_sdk.genai.anthropic import (
    ANTHROPIC_PROVIDER,
    DEFAULT_ANTHROPIC_URL,
    AnthropicGenerator,
)
from tempest_fastapi_sdk.genai.openai_compat import (
    DEFAULT_DEEPSEEK_URL,
    DEFAULT_OPENAI_URL,
    OpenAICompatGenerator,
)

if TYPE_CHECKING:
    import httpx

    from tempest_fastapi_sdk.utils.http_client import HTTPClient, RetryPolicy

HostedProvider = Literal["anthropic", "openai", "google", "deepseek"]
"""The hosted providers :func:`hosted_generator` knows how to build."""

HOSTED_PROVIDERS: Final[tuple[HostedProvider, ...]] = (
    "anthropic",
    "openai",
    "google",
    "deepseek",
)
"""The values of :data:`HostedProvider`, for checking a provider at runtime."""

DEFAULT_GOOGLE_OPENAI_URL: Final[str] = (
    "https://generativelanguage.googleapis.com/v1beta/openai"
)
"""Gemini's OpenAI-compatible API root; ``/chat/completions`` is appended.

From https://ai.google.dev/gemini-api/docs/openai, whose ``base_url`` is
``https://generativelanguage.googleapis.com/v1beta/openai/``. Stored without
the trailing slash because the generator joins ``/chat/completions`` itself.
"""

GOOGLE_EFFORT_VALUES: Final[MappingProxyType[str, str]] = MappingProxyType(
    {
        "low": "low",
        "medium": "medium",
        "high": "high",
        "xhigh": "high",
        "max": "high",
    },
)
"""Effort values translated to Gemini's ``reasoning_effort``.

https://ai.google.dev/gemini-api/docs/openai documents ``minimal``,
``low``, ``medium`` and ``high`` for ``reasoning_effort``; ``xhigh`` and
``max`` have no Gemini spelling, so they ask for the highest one it has.
"""

OPENAI_MAX_TOKENS_FIELD: Final[str] = "max_completion_tokens"
"""Where the output budget goes on OpenAI.

The official OpenAPI document (https://github.com/openai/openai-openapi,
branch ``main``) marks ``max_tokens`` deprecated in favor of
``max_completion_tokens``. The same value
``HostedGenAISettings.openai_kwargs()`` selects.
"""

MODEL_PREFIXES: Final[tuple[tuple[str, HostedProvider], ...]] = (
    ("claude-", "anthropic"),
    ("gpt-", "openai"),
    ("chatgpt-", "openai"),
    ("o1", "openai"),
    ("o3", "openai"),
    ("o4", "openai"),
    ("gemini-", "google"),
    ("deepseek-", "deepseek"),
)
"""Model-id prefixes and the provider each one belongs to, checked in order.

The prefixes the providers use for their model families. A model id outside
them (a fine-tune, a self-hosted model behind an OpenAI-compatible server)
is not guessed: pass ``provider=`` explicitly.
"""


def resolve_provider(model: str) -> HostedProvider:
    """Read the provider off a model id.

    Args:
        model (str): The model id (``"claude-sonnet-5-5"``, ``"gpt-..."``,
            ``"gemini-..."``, ``"deepseek-..."``).

    Returns:
        HostedProvider: The provider the id belongs to.

    Raises:
        ValueError: When no prefix in :data:`MODEL_PREFIXES` matches.

    Example:
        >>> resolve_provider("claude-haiku-5-5")
        'anthropic'
    """
    normalized = model.strip().lower()
    for prefix, provider in MODEL_PREFIXES:
        if normalized.startswith(prefix):
            return provider
    raise ValueError(
        f"cannot tell the provider of model {model!r} from its id; "
        "pass provider= explicitly (anthropic, openai, google, deepseek).",
    )


def hosted_generator(
    model: str,
    *,
    api_key: str,
    provider: HostedProvider | None = None,
    base_url: str | None = None,
    timeout: float = 120.0,
    retry_policy: RetryPolicy | None = None,
    http_client: HTTPClient | None = None,
    transport: httpx.AsyncBaseTransport | None = None,
) -> AnthropicGenerator | OpenAICompatGenerator:
    """Build the hosted generator for ``model``.

    ``provider`` defaults to :func:`resolve_provider`; ``base_url`` defaults
    to the provider's documented root. On top of that, each provider gets
    the wire settings it documents:

    * ``anthropic`` — :class:`~tempest_fastapi_sdk.genai.AnthropicGenerator`;
    * ``openai`` — the output budget as :data:`OPENAI_MAX_TOKENS_FIELD` and
      ``json_schema`` structured output;
    * ``google`` — :data:`DEFAULT_GOOGLE_OPENAI_URL`, ``json_schema``
      structured output and :data:`GOOGLE_EFFORT_VALUES`;
    * ``deepseek`` — ``json_object`` structured output (the only JSON mode
      DeepSeek documents) and no ``reasoning_effort`` field.

    ``HostedGenAISettings.hosted_kwargs()`` returns exactly these keyword
    arguments, so building from settings is a splat.

    Args:
        model (str): The model id.
        api_key (str): The provider's API key. Empty raises in the
            generator constructor.
        provider (HostedProvider | None): The provider, when the model id
            does not say it.
        base_url (str | None): API root, for a proxy or a regional endpoint.
        timeout (float): Per-request HTTP timeout in seconds.
        retry_policy (RetryPolicy | None): Retry configuration for the
            lazily-created HTTP client.
        http_client (HTTPClient | None): An injected client for connection
            reuse.
        transport (httpx.AsyncBaseTransport | None): Transport for the
            lazily-created client — an ``httpx.MockTransport`` in tests.

    Returns:
        AnthropicGenerator | OpenAICompatGenerator: The generator.

    Raises:
        ValueError: When the provider cannot be resolved, ``provider`` is
            not one of :data:`HOSTED_PROVIDERS` (a string from configuration
            is not checked by the type), or ``api_key`` is empty.

    Example:
        >>> gen = hosted_generator("claude-haiku-5-5", api_key="sk-ant-...")
        >>> isinstance(gen, AnthropicGenerator)
        True
    """
    resolved = provider or resolve_provider(model)
    if resolved not in HOSTED_PROVIDERS:
        raise ValueError(
            f"unknown provider {resolved!r}; expected one of "
            f"{', '.join(HOSTED_PROVIDERS)}.",
        )
    if resolved == ANTHROPIC_PROVIDER:
        return AnthropicGenerator(
            model,
            api_key=api_key,
            base_url=base_url or DEFAULT_ANTHROPIC_URL,
            timeout=timeout,
            retry_policy=retry_policy,
            http_client=http_client,
            transport=transport,
        )
    if resolved == "openai":
        return OpenAICompatGenerator(
            model,
            api_key=api_key,
            base_url=base_url or DEFAULT_OPENAI_URL,
            timeout=timeout,
            retry_policy=retry_policy,
            http_client=http_client,
            transport=transport,
            provider="openai",
            max_tokens_field=OPENAI_MAX_TOKENS_FIELD,
            structured_output="json_schema",
        )
    if resolved == "google":
        return OpenAICompatGenerator(
            model,
            api_key=api_key,
            base_url=base_url or DEFAULT_GOOGLE_OPENAI_URL,
            timeout=timeout,
            retry_policy=retry_policy,
            http_client=http_client,
            transport=transport,
            provider="google",
            structured_output="json_schema",
            effort_values=GOOGLE_EFFORT_VALUES,
        )
    return OpenAICompatGenerator(
        model,
        api_key=api_key,
        base_url=base_url or DEFAULT_DEEPSEEK_URL,
        timeout=timeout,
        retry_policy=retry_policy,
        http_client=http_client,
        transport=transport,
        provider="deepseek",
        structured_output="json_object",
        effort_field=None,
    )


__all__: list[str] = [
    "DEFAULT_GOOGLE_OPENAI_URL",
    "GOOGLE_EFFORT_VALUES",
    "HOSTED_PROVIDERS",
    "MODEL_PREFIXES",
    "OPENAI_MAX_TOKENS_FIELD",
    "HostedProvider",
    "hosted_generator",
    "resolve_provider",
]
