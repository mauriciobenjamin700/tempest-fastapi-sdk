"""HTTP-client lifecycle and option mapping shared by the Ollama classes."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from tempest_fastapi_sdk.genai.generation_cache import (
    AsyncGenerationCache,
    GenerationCache,
)
from tempest_fastapi_sdk.genai.metrics import GenAIMetrics
from tempest_fastapi_sdk.genai.ollama._constants import (
    _HF_TO_OLLAMA_OPTION,
    DEFAULT_OLLAMA_URL,
)
from tempest_fastapi_sdk.genai.schemas import GenerationConfig
from tempest_fastapi_sdk.utils.http_client import HTTPClient, RetryPolicy

if TYPE_CHECKING:
    import httpx


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
