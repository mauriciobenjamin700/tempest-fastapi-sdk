"""Detection of a prompt the Ollama daemon truncated without an error."""

from __future__ import annotations

from typing import Any

from tempest_fastapi_sdk.genai.ollama._client import (
    _OllamaClientMixin,
    _require_httpx,
)
from tempest_fastapi_sdk.genai.ollama._constants import (
    _LOGGER,
    _TRUNCATION_SLACK_TOKENS,
)
from tempest_fastapi_sdk.utils.http_client import CircuitOpenError


class _OllamaTruncationMixin(_OllamaClientMixin):
    """Warn when the daemon cut a prompt to fit its context window.

    Attributes:
        _loaded_num_ctx (int | None): The loaded model's context length
            from ``/api/ps``, looked up once and cached.
    """

    _loaded_num_ctx: int | None

    async def _warn_if_truncated(
        self,
        payload: dict[str, Any],
        data: dict[str, Any],
    ) -> None:
        """Log a warning when the daemon truncated the prompt.

        Ollama cuts a prompt longer than the context window and answers
        ``200`` with no error, so the only trace is the ``prompt_eval_count``
        it reports: the truncated prompt lands at half the window plus a few
        tokens (see ``_TRUNCATION_SLACK_TOKENS``). The window is the request's
        ``num_ctx`` when it set one; otherwise it is the ``context_length``
        the daemon reports for the loaded model in ``/api/ps``, looked up
        once per generator — a request without ``num_ctx`` always runs at
        the daemon's default. Counting tokens on this side would need the
        model's tokenizer, so only the daemon's count is used. A failed
        lookup skips the check rather than failing the call.

        Args:
            payload (dict[str, Any]): The request body that was sent.
            data (dict[str, Any]): The decoded reply (or final stream line).
        """
        count = data.get("prompt_eval_count")
        if not isinstance(count, int) or count <= 0:
            return
        window = await self._context_window(payload)
        if window is None:
            return
        half = window // 2
        if half <= count <= half + _TRUNCATION_SLACK_TOKENS or count >= window:
            _LOGGER.warning(
                "Ollama model %r evaluated %d prompt tokens with a %d-token "
                "context window: an overflowing prompt is cut to about half "
                "the window without an error, so this prompt was likely "
                "truncated. Raise num_ctx on the generator.",
                self.model,
                count,
                window,
                extra={
                    "model": self.model,
                    "prompt_eval_count": count,
                    "num_ctx": window,
                },
            )

    async def _context_window(self, payload: dict[str, Any]) -> int | None:
        """Return the context window a request ran with, when it is known.

        Args:
            payload (dict[str, Any]): The request body that was sent.

        Returns:
            int | None: The request's ``num_ctx`` option, else the loaded
            model's ``context_length`` from ``/api/ps``, else ``None``.
        """
        options = payload.get("options") or {}
        num_ctx = options.get("num_ctx")
        if isinstance(num_ctx, int) and num_ctx > 0:
            return num_ctx
        if self._loaded_num_ctx is None:
            self._loaded_num_ctx = await self._lookup_loaded_context()
        return self._loaded_num_ctx

    async def _lookup_loaded_context(self) -> int | None:
        """Read this model's ``context_length`` from the daemon's ``/api/ps``.

        Returns:
            int | None: The loaded context length, or ``None`` when the
            model is not listed or the lookup fails.
        """
        httpx_module = _require_httpx()
        try:
            response = await self._http().get(f"{self.base_url}/api/ps")
            response.raise_for_status()
            listing = response.json()
        except (httpx_module.HTTPError, ValueError, CircuitOpenError) as exc:
            _LOGGER.debug("Ollama /api/ps lookup failed: %s", exc)
            return None
        models = listing.get("models") if isinstance(listing, dict) else None
        if not isinstance(models, list):
            return None
        names = {self.model, f"{self.model}:latest"}
        for entry in models:
            if not isinstance(entry, dict):
                continue
            if entry.get("name") in names or entry.get("model") in names:
                length = entry.get("context_length")
                if isinstance(length, int) and length > 0:
                    return length
        return None
