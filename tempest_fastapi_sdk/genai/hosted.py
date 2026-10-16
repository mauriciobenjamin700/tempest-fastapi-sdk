"""What the hosted text backends share: typed errors and the wire helpers.

:class:`~tempest_fastapi_sdk.genai.AnthropicGenerator` and
:class:`~tempest_fastapi_sdk.genai.OpenAICompatGenerator` speak two different
wire formats, but fail the same three ways, and a caller wants to react to
those three differently:

* the key is wrong (``401``/``403``) — repeating the call will not help;
* the account is throttled (``429``) — wait ``retry-after`` and repeat;
* the provider is down or overloaded (``5xx``, Anthropic's ``529``) — repeat
  later, or fail over to another backend.

Each error class below is still an :class:`httpx.HTTPStatusError`, which is
what :class:`~tempest_fastapi_sdk.genai.OpenAICompatGenerator` raised before
these existed, so an ``except httpx.HTTPStatusError`` written against it keeps
catching everything.

The status codes and error-body shapes come from each provider's
documentation, read when this module was written:

* Anthropic: https://platform.claude.com/docs/en/api/errors — error body
  ``{"type": "error", "error": {"type", "message"}, "request_id"}``, a
  ``request-id`` header on every response, ``529`` ``overloaded_error``, and
  errors that can arrive **inside** a ``200`` stream as ``event: error``
  (https://platform.claude.com/docs/en/build-with-claude/streaming).
* OpenAI: the ``ErrorResponse`` schema of the official OpenAPI document
  (https://github.com/openai/openai-openapi, ``openapi.yaml``) —
  ``{"error": {"message", "type", "param", "code"}}``.
* DeepSeek: https://api-docs.deepseek.com/quick_start/error_codes — ``401``
  authentication, ``402`` balance, ``429`` rate limit, ``500``/``503``
  server.
"""

from __future__ import annotations

import copy
import json
from typing import Any

import httpx

ANTHROPIC_STREAM_ERROR_STATUS: dict[str, int] = {
    "invalid_request_error": 400,
    "authentication_error": 401,
    "billing_error": 402,
    "permission_error": 403,
    "not_found_error": 404,
    "conflict_error": 409,
    "request_too_large": 413,
    "rate_limit_error": 429,
    "api_error": 500,
    "timeout_error": 504,
    "overloaded_error": 529,
}
"""Anthropic error ``type`` to the HTTP status it stands for.

Ported from the status list at https://platform.claude.com/docs/en/api/errors.
Used for an error that arrives mid-stream, after the ``200`` was already
sent: the event carries only the ``type``, and the streaming page says an
``overloaded_error`` there "would normally correspond to an HTTP 529".
"""

_UNSUPPORTED_SCHEMA_KEYWORDS: frozenset[str] = frozenset(
    {
        "minimum",
        "maximum",
        "exclusiveMinimum",
        "exclusiveMaximum",
        "multipleOf",
        "minLength",
        "maxLength",
        "maxItems",
        "uniqueItems",
    },
)
"""JSON-schema keywords Anthropic structured outputs do not accept.

The numerical, string-length and array constraints listed as unsupported in
the structured-outputs guide
(https://platform.claude.com/docs/en/build-with-claude/structured-outputs),
which accepts ``minItems`` only as ``0`` or ``1`` and no other array
constraint.
Removing them from the schema sent to the API loses nothing: the reply is
still validated against the original Pydantic model on this side.
"""


class HostedAPIError(httpx.HTTPStatusError):
    """A hosted provider answered with an error status.

    Subclass of :class:`httpx.HTTPStatusError`, so existing handlers keep
    working; the attributes below are what that base class made the caller
    dig out of the response by hand.

    Attributes:
        provider (str): Which backend raised it (``"anthropic"``,
            ``"openai"``, ``"deepseek"``, or ``"openai-compatible"``).
        status_code (int): The HTTP status. For an error that arrived inside
            a stream it is the status the provider documents for that error
            ``type``.
        error_type (str | None): The provider's own error type
            (``"rate_limit_error"``, ``"invalid_request_error"``, ...), when
            the body carried one.
        detail (str): The provider's error message, or the response text
            when the body was not the documented JSON.
        request_id (str | None): The provider's request id, for a support
            ticket. Read from the ``request-id`` header or the body's
            ``request_id``.
        retry_after (float | None): Seconds the provider asked to wait,
            from a numeric ``retry-after`` header. ``None`` when absent or
            given as an HTTP date.
    """

    def __init__(
        self,
        message: str,
        *,
        request: httpx.Request,
        response: httpx.Response,
        provider: str,
        error_type: str | None = None,
        detail: str = "",
        request_id: str | None = None,
        retry_after: float | None = None,
    ) -> None:
        """Build the error.

        Args:
            message (str): The exception message.
            request (httpx.Request): The request that failed.
            response (httpx.Response): The error response.
            provider (str): Which backend raised it.
            error_type (str | None): The provider's error type.
            detail (str): The provider's error message.
            request_id (str | None): The provider's request id.
            retry_after (float | None): Seconds to wait before retrying.
        """
        super().__init__(message, request=request, response=response)
        self.provider: str = provider
        self.status_code: int = response.status_code
        self.error_type: str | None = error_type
        self.detail: str = detail
        self.request_id: str | None = request_id
        self.retry_after: float | None = retry_after


class HostedAuthenticationError(HostedAPIError):
    """The key was rejected (``401``) or lacks permission (``403``).

    Not worth retrying: the same key fails the same way.
    """


class HostedRateLimitError(HostedAPIError):
    """The account is being throttled (``429``).

    Retry after :attr:`retry_after` seconds when it is set. Anthropic notes
    that a ``429`` caused by the monthly spend cap carries no ``retry-after``
    and keeps failing until access resumes
    (https://platform.claude.com/docs/en/api/errors).
    """


class HostedServerError(HostedAPIError):
    """The provider failed or is overloaded (``5xx``, including ``529``).

    Retryable later, or a reason to fail over to another backend.
    """


def _error_class(status: int) -> type[HostedAPIError]:
    """Pick the exception class for a status code.

    Args:
        status (int): The HTTP status.

    Returns:
        type[HostedAPIError]: The most specific class for it.
    """
    if status in (401, 403):
        return HostedAuthenticationError
    if status == 429:
        return HostedRateLimitError
    if status >= 500:
        return HostedServerError
    return HostedAPIError


def _retry_after(response: httpx.Response) -> float | None:
    """Read a numeric ``retry-after`` header.

    Args:
        response (httpx.Response): The error response.

    Returns:
        float | None: The seconds to wait, or ``None`` when the header is
        absent or not a number (the HTTP-date form is not parsed).
    """
    raw = response.headers.get("retry-after")
    if raw is None:
        return None
    try:
        return float(raw)
    except ValueError:
        return None


def _decoded_body(response: httpx.Response) -> dict[str, Any]:
    """Decode the error body, tolerating a body that is not JSON.

    A streamed response whose body was never read raises
    ``httpx.ResponseNotRead`` on ``.json()``; that is treated as "no body"
    rather than replacing the provider's error with an unrelated one.

    Args:
        response (httpx.Response): The error response.

    Returns:
        dict[str, Any]: The decoded body, or ``{}``.
    """
    try:
        decoded = response.json()
    except (ValueError, httpx.ResponseNotRead):
        return {}
    return decoded if isinstance(decoded, dict) else {}


def hosted_error(provider: str, response: httpx.Response) -> HostedAPIError:
    """Translate an error response into the matching typed exception.

    Reads both documented body shapes — Anthropic's
    ``{"error": {"type", "message"}, "request_id"}`` and OpenAI's
    ``{"error": {"message", "type", "param", "code"}}`` — because they nest
    the same two fields under the same key.

    Args:
        provider (str): Which backend is raising.
        response (httpx.Response): The non-success response.

    Returns:
        HostedAPIError: The exception to raise (not raised here).
    """
    body = _decoded_body(response)
    error = body.get("error")
    error_type: str | None = None
    detail = ""
    if isinstance(error, dict):
        raw_type = error.get("type")
        error_type = str(raw_type) if raw_type else None
        detail = str(error.get("message") or "")
    if not detail:
        try:
            detail = response.text[:500]
        except httpx.ResponseNotRead:
            detail = ""
    request_id = response.headers.get("request-id") or body.get("request_id")
    status = response.status_code
    label = f"{error_type}: {detail}" if error_type else detail
    message = f"{provider} answered {status}" + (f" ({label})" if label else "")
    return _error_class(status)(
        message,
        request=response.request,
        response=response,
        provider=provider,
        error_type=error_type,
        detail=detail,
        request_id=str(request_id) if request_id else None,
        retry_after=_retry_after(response),
    )


def raise_for_hosted_status(provider: str, response: httpx.Response) -> None:
    """Raise the typed error when ``response`` is not a success.

    Args:
        provider (str): Which backend is checking.
        response (httpx.Response): The response to check.

    Raises:
        HostedAPIError: A subclass matching the status, when it is ``>= 400``.
    """
    if response.is_success:
        return
    raise hosted_error(provider, response)


def stream_error(
    provider: str,
    url: str,
    error_type: str,
    detail: str,
) -> HostedAPIError:
    """Build the typed error for a failure reported inside a ``200`` stream.

    The HTTP status was already ``200`` when the error arrived, so the
    response attached here is synthetic: its status is the one Anthropic
    documents for that error ``type`` (``overloaded_error`` -> ``529``),
    falling back to ``500`` for a type the table does not know.

    Args:
        provider (str): Which backend is raising.
        url (str): The URL the stream was opened against.
        error_type (str): The ``error.type`` of the event.
        detail (str): The ``error.message`` of the event.

    Returns:
        HostedAPIError: The exception to raise (not raised here).
    """
    status = ANTHROPIC_STREAM_ERROR_STATUS.get(error_type, 500)
    request = httpx.Request("POST", url)
    response = httpx.Response(
        status,
        json={"type": "error", "error": {"type": error_type, "message": detail}},
        request=request,
    )
    return _error_class(status)(
        f"{provider} failed mid-stream ({error_type}: {detail})",
        request=request,
        response=response,
        provider=provider,
        error_type=error_type,
        detail=detail,
    )


def sse_data(line: str) -> dict[str, Any] | None:
    """Decode the JSON payload of one server-sent-event ``data:`` line.

    Both wire formats put one JSON object per ``data:`` line; Anthropic adds
    an ``event:`` line before it whose name the JSON ``type`` repeats, so the
    ``event:`` lines, blank separators, comments and the OpenAI
    ``data: [DONE]`` sentinel all decode to ``None`` here. A line that is not
    valid JSON also yields ``None`` rather than raising: a keep-alive must not
    abort a generation.

    Args:
        line (str): One raw line of the response body.

    Returns:
        dict[str, Any] | None: The decoded payload, or ``None``.
    """
    stripped = line.strip()
    if not stripped.startswith("data:"):
        return None
    payload = stripped[len("data:") :].strip()
    if not payload or payload == "[DONE]":
        return None
    try:
        decoded = json.loads(payload)
    except ValueError:
        return None
    return decoded if isinstance(decoded, dict) else None


def strict_json_schema(schema: dict[str, Any]) -> dict[str, Any]:
    """Adapt a Pydantic JSON schema to Anthropic structured outputs.

    Anthropic requires ``additionalProperties: false`` on every object and
    rejects numerical, string-length and array constraints (``minItems``
    above ``1`` included)
    (https://platform.claude.com/docs/en/build-with-claude/structured-outputs).
    A Pydantic schema sets neither rule, so this returns a copy where every
    object closes its properties and those constraints are gone. The reply is
    still validated against the original model, so a dropped constraint is
    enforced on this side instead of the provider's.

    Args:
        schema (dict[str, Any]): A schema from ``model_json_schema()``.

    Returns:
        dict[str, Any]: The adapted copy; ``schema`` is not modified.
    """
    adapted: dict[str, Any] = copy.deepcopy(schema)
    _close_schema(adapted)
    return adapted


def _close_schema(node: dict[str, Any]) -> None:
    """Apply :func:`strict_json_schema` to one schema node, in place.

    Walks only the keywords that hold sub-schemas, so a property literally
    named ``minimum`` keeps its name: ``properties`` maps names to schemas and
    is never itself stripped.

    Args:
        node (dict[str, Any]): A schema node.
    """
    for keyword in _UNSUPPORTED_SCHEMA_KEYWORDS.intersection(node):
        del node[keyword]
    min_items = node.get("minItems")
    if isinstance(min_items, int) and min_items > 1:
        del node["minItems"]
    properties = node.get("properties")
    is_object = node.get("type") == "object" or isinstance(properties, dict)
    if is_object and not isinstance(node.get("additionalProperties"), dict):
        node["additionalProperties"] = False
    if isinstance(properties, dict):
        for child in properties.values():
            if isinstance(child, dict):
                _close_schema(child)
    for keyword in ("$defs", "definitions"):
        holder = node.get(keyword)
        if isinstance(holder, dict):
            for child in holder.values():
                if isinstance(child, dict):
                    _close_schema(child)
    for keyword in ("anyOf", "allOf", "oneOf", "prefixItems"):
        holder = node.get(keyword)
        if isinstance(holder, list):
            for child in holder:
                if isinstance(child, dict):
                    _close_schema(child)
    for keyword in ("items", "additionalProperties", "not"):
        child = node.get(keyword)
        if isinstance(child, dict):
            _close_schema(child)


__all__: list[str] = [
    "ANTHROPIC_STREAM_ERROR_STATUS",
    "HostedAPIError",
    "HostedAuthenticationError",
    "HostedRateLimitError",
    "HostedServerError",
    "hosted_error",
    "raise_for_hosted_status",
    "sse_data",
    "stream_error",
    "strict_json_schema",
]
