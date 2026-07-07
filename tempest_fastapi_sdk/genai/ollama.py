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

import json
from collections.abc import AsyncIterator
from typing import TYPE_CHECKING, Any

from tempest_fastapi_sdk.genai.schemas import GenerationConfig

if TYPE_CHECKING:
    import httpx

DEFAULT_OLLAMA_URL: str = "http://127.0.0.1:11434"

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


class _OllamaClientMixin:
    """Shared HTTP-client lifecycle for the Ollama backend classes."""

    def __init__(
        self,
        model: str,
        *,
        base_url: str = DEFAULT_OLLAMA_URL,
        timeout: float = 120.0,
        keep_alive: str | float | None = None,
        http_client: httpx.AsyncClient | None = None,
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
            http_client (httpx.AsyncClient | None): An injected client
                (tests / connection reuse). When ``None``, one is created
                lazily and owned by this instance.
        """
        self.model = model
        self.base_url = base_url.rstrip("/")
        self.timeout = timeout
        self.keep_alive = keep_alive
        self._client: httpx.AsyncClient | None = http_client
        self._owns_client: bool = http_client is None

    def _http(self) -> httpx.AsyncClient:
        """Return the HTTP client, creating an owned one on first use.

        Returns:
            httpx.AsyncClient: The client used for daemon requests.
        """
        if self._client is None:
            httpx = _require_httpx()
            self._client = httpx.AsyncClient(timeout=self.timeout)
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
    """

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
            **kwargs (Any): Per-call generation overrides (HuggingFace-style
                names such as ``max_new_tokens`` / ``temperature``); these
                win over ``config``.

        Returns:
            str: The generated text.
        """
        payload = self._request_payload(prompt, config, kwargs, stream=False)
        if images:
            payload["images"] = images
        response = await self._http().post(
            f"{self.base_url}/api/generate",
            json=payload,
        )
        response.raise_for_status()
        data: dict[str, Any] = response.json()
        return str(data.get("response", ""))

    async def chat(
        self,
        messages: list[dict[str, Any]],
        *,
        config: GenerationConfig | None = None,
        **kwargs: Any,
    ) -> str:
        """Generate a reply for a chat ``messages`` list.

        Args:
            messages (list[dict[str, Any]]): Chat turns, each
                ``{"role": ..., "content": ...}``. A turn may also carry an
                ``"images": [<base64>, ...]`` key for multimodal models —
                it is forwarded verbatim to Ollama.
            config (GenerationConfig | None): Typed generation parameters.
            **kwargs (Any): Per-call generation overrides (win over
                ``config``).

        Returns:
            str: The assistant reply.
        """
        payload: dict[str, Any] = {
            "model": self.model,
            "messages": messages,
            "stream": False,
        }
        self._apply_common(payload, config, kwargs)
        response = await self._http().post(f"{self.base_url}/api/chat", json=payload)
        response.raise_for_status()
        data: dict[str, Any] = response.json()
        message: dict[str, Any] = data.get("message") or {}
        return str(message.get("content", ""))

    async def stream(
        self,
        prompt: str,
        *,
        config: GenerationConfig | None = None,
        **kwargs: Any,
    ) -> AsyncIterator[str]:
        """Stream the completion piece by piece.

        Args:
            prompt (str): The input text.
            config (GenerationConfig | None): Typed generation parameters.
            **kwargs (Any): Per-call generation overrides (win over
                ``config``).

        Yields:
            str: Text pieces as the daemon produces them.
        """
        payload = self._request_payload(prompt, config, kwargs, stream=True)
        async with self._http().stream(
            "POST",
            f"{self.base_url}/api/generate",
            json=payload,
        ) as response:
            response.raise_for_status()
            async for line in response.aiter_lines():
                if not line.strip():
                    continue
                chunk: dict[str, Any] = json.loads(line)
                piece = chunk.get("response")
                if piece:
                    yield str(piece)
                if chunk.get("done"):
                    break

    def _request_payload(
        self,
        prompt: str,
        config: GenerationConfig | None,
        overrides: dict[str, Any],
        *,
        stream: bool,
    ) -> dict[str, Any]:
        """Build the ``/api/generate`` request body.

        Args:
            prompt (str): The input prompt.
            config (GenerationConfig | None): Typed generation parameters.
            overrides (dict[str, Any]): Per-call overrides.
            stream (bool): Whether to request a streamed response.

        Returns:
            dict[str, Any]: The JSON payload for the daemon.
        """
        payload: dict[str, Any] = {
            "model": self.model,
            "prompt": prompt,
            "stream": stream,
        }
        self._apply_common(payload, config, overrides)
        return payload

    def _apply_common(
        self,
        payload: dict[str, Any],
        config: GenerationConfig | None,
        overrides: dict[str, Any],
    ) -> None:
        """Attach ``options`` and ``keep_alive`` to a request payload.

        Args:
            payload (dict[str, Any]): The request body to mutate in place.
            config (GenerationConfig | None): Typed generation parameters.
            overrides (dict[str, Any]): Per-call overrides.
        """
        options = _build_options(config, overrides)
        if options:
            payload["options"] = options
        if self.keep_alive is not None:
            payload["keep_alive"] = self.keep_alive


class OllamaEmbedder(_OllamaClientMixin):
    """Embeddings backed by an Ollama daemon.

    Satisfies :class:`~tempest_fastapi_sdk.genai.rag.SupportsEmbed`, so it
    plugs into :class:`~tempest_fastapi_sdk.genai.rag.Retriever` and the
    ``/embed`` endpoint of
    :func:`~tempest_fastapi_sdk.genai.make_genai_router` in place of the
    ``torch``-backed :class:`~tempest_fastapi_sdk.genai.Embedder`. Pull an
    embedding model first (e.g. ``ollama pull nomic-embed-text``).

    Attributes:
        model (str): The Ollama embedding model tag.
        base_url (str): The daemon base URL.
    """

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
            embeddings: list[list[float]] = data.get("embeddings") or []
            vectors.extend([float(x) for x in vector] for vector in embeddings)
        return vectors


__all__: list[str] = [
    "DEFAULT_OLLAMA_URL",
    "OllamaEmbedder",
    "OllamaGenerator",
]
