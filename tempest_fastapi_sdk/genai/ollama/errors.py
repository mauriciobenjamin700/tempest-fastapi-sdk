"""Errors the Ollama backend raises and the helpers that raise them."""

from __future__ import annotations

import json
from typing import TYPE_CHECKING, Any

from tempest_fastapi_sdk.genai.ollama._constants import (
    _CONTEXT_EXCEEDED,
    _THINKING_UNSUPPORTED,
    ThinkLevel,
)

if TYPE_CHECKING:
    import httpx


class OllamaError(RuntimeError):
    """The Ollama daemon answered with an ``{"error": ...}`` body.

    The daemon reports some failures (model not pulled, out of memory, a
    runner crash mid-stream) as a JSON object with an ``error`` key — on a
    ``200`` response or as one line of an NDJSON stream. Treating that
    object as a normal reply yields an empty completion, or a stream that
    ends as if it had finished. This exception surfaces it instead.

    Attributes:
        model (str): The model tag the request targeted.
        detail (str): The daemon's error message.
    """

    def __init__(self, model: str, detail: str) -> None:
        """Build the error from the daemon message.

        Args:
            model (str): The model tag the request targeted.
            detail (str): The daemon's ``error`` string.
        """
        super().__init__(f"Ollama error for model {model!r}: {detail}")
        self.model: str = model
        self.detail: str = detail


class OllamaThinkingUnsupportedError(OllamaError):
    """The model has no thinking capability and the request set ``think``.

    The daemon refuses the request with ``400`` and an ``error`` saying the
    model ``does not support thinking`` (reproduced on Ollama 0.30.11 with
    ``ministral-3:14b``). Leave ``think`` as ``None`` for that model.

    Attributes:
        model (str): The model tag the request targeted.
        detail (str): The daemon's error message.
    """

    def __init__(self, model: str, detail: str) -> None:
        """Build the error from the daemon message.

        Args:
            model (str): The model tag the request targeted.
            detail (str): The daemon's ``error`` string.
        """
        super().__init__(model, detail)
        self.args = (
            f"Ollama model {model!r} does not support thinking ({detail}); "
            "leave think=None for this model",
        )


class OllamaContextExceededError(OllamaError):
    """The prompt does not fit the context window and ``truncate`` was ``False``.

    By default the daemon cuts an overflowing prompt to about half the
    window and answers ``200``. With ``truncate=False`` it refuses instead:
    ``400`` with an ``exceed_context_size_error`` that carries the prompt
    size and the window (reproduced on Ollama 0.30.11 on ``/api/chat`` and
    ``/api/generate``, streamed or not, before any line is streamed). Raise
    ``num_ctx`` or shorten the prompt.

    Attributes:
        model (str): The model tag the request targeted.
        detail (str): The daemon's error message.
        prompt_tokens (int | None): The prompt size the daemon counted
            (``n_prompt_tokens``), when it reported one.
        context_window (int | None): The window the request ran with
            (``n_ctx``), when the daemon reported one.
    """

    def __init__(
        self,
        model: str,
        detail: str,
        *,
        prompt_tokens: int | None = None,
        context_window: int | None = None,
    ) -> None:
        """Build the error from the daemon message and the sizes it reported.

        Args:
            model (str): The model tag the request targeted.
            detail (str): The daemon's error message.
            prompt_tokens (int | None): The prompt size in tokens, if known.
            context_window (int | None): The context window in tokens, if
                known.
        """
        super().__init__(model, detail)
        self.prompt_tokens: int | None = prompt_tokens
        self.context_window: int | None = context_window
        if prompt_tokens is not None and context_window is not None:
            sizes = (
                f"the prompt has {prompt_tokens} tokens and the context "
                f"window {context_window}"
            )
        else:
            sizes = detail
        self.args = (
            f"Ollama model {model!r} refused a prompt longer than its context "
            f"window ({sizes}) because truncate=False; raise num_ctx or "
            "shorten the prompt",
        )


class OllamaFormatIgnoredError(OllamaError, ValueError):
    """A structured call came back as free text instead of the JSON asked for.

    The request carried the schema as ``format`` and the reply holds no JSON
    object, so the daemon did not constrain the decoder. Reproduced on Ollama
    0.30.11: ``ornith:35b`` with ``think=false`` answered
    ``**Nome:** Ana Costa ...`` with ``200`` in 3 of 3 calls, while the same
    request with ``think`` omitted or ``true`` returned the object 3 of 3.
    ``ornith-1.5:9b``, ``gpt-oss:20b`` and ``ministral-3:14b`` honoured
    ``format`` with ``think=false`` (3 of 3 each).

    Subclasses ``ValueError``, which is what a reply without a JSON object
    raised before, so code already catching that keeps working.

    Attributes:
        model (str): The model tag the request targeted.
        detail (str): What went wrong.
        content (str): The start of the reply (first 500 characters).
        think (ThinkLevel | None): The ``think`` value the request carried.
    """

    def __init__(self, model: str, content: str, think: ThinkLevel | None) -> None:
        """Build the error from the reply.

        Args:
            model (str): The model tag the request targeted.
            content (str): The reply content.
            think (ThinkLevel | None): The ``think`` value sent, if any.
        """
        detail = (
            "the reply holds no JSON object, so the format constraint was not applied"
        )
        if think is False:
            detail += (
                "; think=False makes some models ignore format (measured: "
                "ornith:35b), so omit think or set it to True for this model"
            )
        super().__init__(model, detail)
        self.content: str = content[:500]
        self.think: ThinkLevel | None = think


def _int_or_none(value: object) -> int | None:
    """Return ``value`` when it is a plain int, else ``None``.

    Args:
        value (object): A decoded JSON value.

    Returns:
        int | None: The int, or ``None`` for anything else (bools included).
    """
    if isinstance(value, int) and not isinstance(value, bool):
        return value
    return None


def _context_exceeded(model: str, error: str) -> OllamaContextExceededError | None:
    """Build the context error when ``error`` is the daemon's overflow refusal.

    The daemon nests a JSON document inside the ``error`` string (see
    ``_CONTEXT_EXCEEDED``); the sizes come from it when it parses, and the
    error is still recognised by its ``type`` name when it does not.

    Args:
        model (str): The model tag, used in the message.
        error (str): The outer body's ``error`` string.

    Returns:
        OllamaContextExceededError | None: The error to raise, or ``None``
        when ``error`` is some other failure.
    """
    if _CONTEXT_EXCEEDED not in error:
        return None
    try:
        inner = json.loads(error)
    except ValueError:
        inner = None
    details = inner.get("error") if isinstance(inner, dict) else None
    if not isinstance(details, dict):
        return OllamaContextExceededError(model, error)
    message = details.get("message")
    return OllamaContextExceededError(
        model,
        message if isinstance(message, str) else error,
        prompt_tokens=_int_or_none(details.get("n_prompt_tokens")),
        context_window=_int_or_none(details.get("n_ctx")),
    )


def _raise_for_refusal(model: str, response: httpx.Response) -> None:
    """Raise the SDK error for a daemon refusal the SDK knows by name.

    Args:
        model (str): The model tag, used in the message.
        response (httpx.Response): A non-success response whose body is
            already read.

    Raises:
        OllamaThinkingUnsupportedError: When the body's ``error`` says the
            model does not support thinking.
        OllamaContextExceededError: When the body's ``error`` is an
            ``exceed_context_size_error``.
    """
    try:
        data = response.json()
    except ValueError:
        return
    if not isinstance(data, dict):
        return
    error = data.get("error")
    if not isinstance(error, str):
        return
    if _THINKING_UNSUPPORTED in error:
        raise OllamaThinkingUnsupportedError(model, error)
    exceeded = _context_exceeded(model, error)
    if exceeded is not None:
        raise exceeded


def _raise_for_status(model: str, response: httpx.Response) -> None:
    """Raise for a non-success response, naming the refusals the SDK knows.

    Args:
        model (str): The model tag, used in the message.
        response (httpx.Response): The daemon's response.

    Raises:
        OllamaThinkingUnsupportedError: When the daemon refused ``think``.
        OllamaContextExceededError: When ``truncate=False`` met a prompt
            longer than the context window.
        httpx.HTTPStatusError: For any other non-success status.
    """
    if response.is_error:
        _raise_for_refusal(model, response)
    response.raise_for_status()


def _raise_on_error(model: str, data: dict[str, Any]) -> None:
    """Raise :class:`OllamaError` when ``data`` is a daemon error object.

    Args:
        model (str): The model tag, used in the message.
        data (dict[str, Any]): A decoded response body or NDJSON line.

    Raises:
        OllamaError: When ``data`` carries a non-empty ``error`` key.
    """
    error = data.get("error")
    if error:
        raise OllamaError(model, str(error))
