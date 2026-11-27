"""Constants and type aliases shared by the Ollama backend modules."""

from __future__ import annotations

import logging
from typing import Literal

DEFAULT_OLLAMA_URL: str = "http://127.0.0.1:11434"

ThinkLevel = bool | Literal["low", "medium", "high"]
"""The Ollama ``think`` field: on/off, or a reasoning level.

Sent at the top level of ``/api/chat`` and ``/api/generate`` (inside
``options`` the daemon ignores it). ``True``/``False`` switch reasoning on
or off on a model with the thinking capability; ``"low"``/``"medium"``/
``"high"`` pick a level on the models that support levels (``gpt-oss``).
"""

_THINK_LEVELS: frozenset[str] = frozenset({"low", "medium", "high"})

_LOGGER: logging.Logger = logging.getLogger("tempest_fastapi_sdk.genai.ollama")
"""The logger the Ollama backend writes to.

Named after the package rather than ``__name__`` of this submodule: the
truncation warning has always been emitted on
``tempest_fastapi_sdk.genai.ollama``, and the docs and callers filter on it.
"""

_TRUNCATION_SLACK_TOKENS: int = 16
"""How far above half the window a truncated ``prompt_eval_count`` may land.

Ollama cuts an overflowing prompt to about half the context window and
answers ``200`` with no error. Measured on Ollama 0.30.11 on CPU
(``qwen2.5:0.5b``, ``num_gpu=0``, prompts of 1,000 to 30,000 words, all
larger than the window): the truncated count was half the window plus 2 at
every window tried (1026 at 2048, 2050 at 4096, 4098 at 8192, 8194 at
16384), and the 2026-11-26/27
study saw 2050 (``qwen2.5:3b``, ``qwen2.5-coder:14b``) and 2051
(``lfm2.5-thinking:1.2b``, ``ministral-3:14b``) at 4096. A prompt that fits
reports its own size, so the band stays narrow: a prompt that genuinely
lands in it gets a spurious warning, and one above it — 1243 tokens at
2048 was measured — gets none.
"""

_THINKING_UNSUPPORTED: str = "does not support thinking"
"""Substring of the daemon's error for ``think`` on a model without it.

Reproduced on Ollama 0.30.11 with ``ministral-3:14b`` (``/api/show``
capabilities ``completion``, ``vision``, ``tools``): ``think: true`` on
``/api/chat`` and ``/api/generate``, streamed or not, answered ``400`` with
``{"error": "\\"ministral-3:14b\\" does not support thinking"}`` before
loading the model.
"""

_CONTEXT_EXCEEDED: str = "exceed_context_size_error"
"""The ``type`` the daemon names when ``truncate: false`` meets a long prompt.

Measured on Ollama 0.30.11 on CPU (``qwen2.5:0.5b``, ``num_gpu=0``,
``num_ctx=2048``, a 4,000-word prompt): ``"truncate": false`` on
``/api/chat`` and ``/api/generate``, streamed or not, answered ``400``
before any NDJSON line with ``{"error": "<json>"}``, where ``<json>`` is
``{"error": {"code": 400, "message": "request (18923 tokens) exceeds the
available context size (2048 tokens), try increasing it", "type":
"exceed_context_size_error", "n_prompt_tokens": 18923, "n_ctx": 2048}}``.
"""

_HF_TO_OLLAMA_OPTION: dict[str, str] = {
    "max_new_tokens": "num_predict",
    "temperature": "temperature",
    "top_p": "top_p",
    "top_k": "top_k",
    "repetition_penalty": "repeat_penalty",
}
"""HuggingFace-style generation kwarg names mapped to Ollama ``options``.

The keys are what :class:`~tempest_fastapi_sdk.genai.GenerationConfig` and
the router speak; the values are the daemon's equivalents.
"""
