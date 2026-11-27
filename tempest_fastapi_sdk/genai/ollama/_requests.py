"""Request bodies, cache keys and single calls of the Ollama generator."""

from __future__ import annotations

from typing import Any

from tempest_fastapi_sdk.genai.ollama._client import _build_options
from tempest_fastapi_sdk.genai.ollama._constants import ThinkLevel
from tempest_fastapi_sdk.genai.ollama._truncation import _OllamaTruncationMixin
from tempest_fastapi_sdk.genai.ollama.errors import (
    _raise_for_status,
    _raise_on_error,
)
from tempest_fastapi_sdk.genai.schemas import GenerationConfig, _layer_config


class _OllamaRequestMixin(_OllamaTruncationMixin):
    """Build ``/api/generate`` and ``/api/chat`` bodies and run one call.

    Attributes:
        config (GenerationConfig | None): Default generation parameters.
        options (dict[str, Any]): Raw Ollama ``options`` for every request.
    """

    config: GenerationConfig | None
    options: dict[str, Any]

    def _key_params(
        self,
        config: GenerationConfig | None,
        overrides: dict[str, Any],
        images: list[str] | None = None,
        *,
        think: ThinkLevel | None = None,
        truncate: bool | None = None,
    ) -> dict[str, Any]:
        """Merge config, overrides, images, think and truncate into the cache key.

        The defaults are part of the key: a different context window, a
        different default config or a different ``think`` produces
        different text, so a generator must not answer from another's
        cached completions. ``truncate`` is in it too: a completion cached
        from a cut prompt must not answer a ``truncate=False`` call, which
        would have failed.
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
        if truncate is not None:
            params["truncate"] = truncate
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
        truncate: bool | None = None,
    ) -> tuple[str, int | None, int | None]:
        """Run one ``/api/generate`` call, returning text + Ollama token counts."""
        payload = self._request_payload(
            prompt, config, overrides, stream=False, think=think, truncate=truncate
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
        await self._warn_if_truncated(payload, data)
        return (
            str(data.get("response", "")),
            data.get("prompt_eval_count"),
            data.get("eval_count"),
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
        await self._warn_if_truncated(payload, data)
        message: dict[str, Any] = data.get("message") or {}
        return (
            str(message.get("content", "")),
            data.get("prompt_eval_count"),
            data.get("eval_count"),
        )

    def _request_payload(
        self,
        prompt: str,
        config: GenerationConfig | None,
        overrides: dict[str, Any],
        *,
        stream: bool,
        think: ThinkLevel | None = None,
        truncate: bool | None = None,
    ) -> dict[str, Any]:
        """Build the ``/api/generate`` request body.

        Args:
            prompt (str): The input prompt.
            config (GenerationConfig | None): Typed generation parameters.
            overrides (dict[str, Any]): Per-call overrides.
            stream (bool): Whether to request a streamed response.
            think (ThinkLevel | None): Resolved top-level ``think``.
            truncate (bool | None): Resolved top-level ``truncate``.

        Returns:
            dict[str, Any]: The JSON payload for the daemon.
        """
        payload: dict[str, Any] = {
            "model": self.model,
            "prompt": prompt,
            "stream": stream,
        }
        self._apply_common(payload, config, overrides, think=think, truncate=truncate)
        return payload

    def _apply_common(
        self,
        payload: dict[str, Any],
        config: GenerationConfig | None,
        overrides: dict[str, Any],
        *,
        think: ThinkLevel | None = None,
        truncate: bool | None = None,
    ) -> None:
        """Attach ``options``, ``keep_alive``, ``think`` and ``truncate`` to a payload.

        Args:
            payload (dict[str, Any]): The request body to mutate in place.
            config (GenerationConfig | None): Typed generation parameters.
            overrides (dict[str, Any]): Per-call overrides.
            think (ThinkLevel | None): Resolved ``think``, sent at the top
                level when not ``None``.
            truncate (bool | None): Resolved ``truncate``, sent at the top
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
        if truncate is not None:
            payload["truncate"] = truncate
