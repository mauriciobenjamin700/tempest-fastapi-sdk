"""Tell a materialized response body from one that may never end.

Middleware that reads a response body — to hash it, to store it, to replay
it later — has to consume ``body_iterator`` to the end. For a body the
handler already built, that terminates immediately. For a long-lived stream
it does not terminate at all: the generator behind a ``text/event-stream``
yields until the *client* disconnects, so the middleware never returns, the
response is never constructed, and no header ever reaches the proxy. From
outside, the request hangs until the proxy's read timeout and answers
``504``.

Two middlewares in this package drain a body, and both shipped hanging on
the SDK's own routes: :class:`ResponseCacheMiddleware` on the ``GET`` SSE
stream :func:`make_chat_router` mounts, and :class:`IdempotencyMiddleware`
on the ``POST`` ``text/event-stream`` :func:`make_genai_router` mounts.
This module is the one place that decides what is safe to drain.

**Read the header, not the attribute.** The response a handler returns is
not the object ``dispatch`` receives: ``BaseHTTPMiddleware`` consumes the
handler's ASGI messages and rebuilds a ``_StreamingResponse`` from them,
and that rebuild carries the headers but not ``media_type``. Measured on
the real ASGI path, ``starlette`` 1.6.0::

    GET /sse   -> _StreamingResponse  media_type=None
                  content-type='text/event-stream; charset=utf-8'
                  content-length=None
    GET /json  -> _StreamingResponse  media_type=None
                  content-type='application/json'
                  content-length='11'

``sse_response(...)`` inspected directly *does* report
``media_type='text/event-stream'`` — which is what makes the wrong version
of this check survive review. Inside a middleware it is ``None`` for every
response, so ``response.media_type == "text/event-stream"`` is a branch
that never runs: it reads like a fix and changes nothing.
"""

from __future__ import annotations

from starlette.responses import Response

UNBUFFERED_MEDIA_TYPES: frozenset[str] = frozenset(
    {
        "text/event-stream",
        "multipart/x-mixed-replace",
    }
)
"""Media types whose bodies are produced until the client goes away.

``text/event-stream`` is SSE. ``multipart/x-mixed-replace`` is the
long-poll image/motion-JPEG push that predates it; both are written as
"emit forever", not "emit a document".
"""


def response_media_type(response: Response) -> str:
    """Return ``response``'s media type, read from the header it actually has.

    Args:
        response (Response): The downstream response, as handed to a
            ``BaseHTTPMiddleware.dispatch`` implementation.

    Returns:
        str: The lowercased media type with any ``; charset=...`` parameter
        stripped, or ``""`` when the response carries no ``Content-Type``.
    """
    content_type = response.headers.get("content-type", "")
    return content_type.split(";", 1)[0].strip().lower()


def is_unbounded_stream(response: Response) -> bool:
    """Return whether draining ``response`` could block until the client leaves.

    Two signals, either of which is disqualifying:

    * The media type is one that is written to stream indefinitely
      (:data:`UNBUFFERED_MEDIA_TYPES`).
    * There is no ``Content-Length``. A handler that knows its body's size
      declares it; one that does not is generating the body as it goes, and
      nothing bounds how long that takes or how large it gets.

    The second signal is deliberately broad, and it costs a finite streamed
    response its ``ETag`` and its cache entry. That trade is not close: a
    body nobody buffers is one nobody wanted buffered, and the failure it
    prevents — a route that answers nothing until the proxy times out — is
    far worse than the cache hit it forgoes.

    Args:
        response (Response): The downstream response, before its
            ``body_iterator`` has been read.

    Returns:
        bool: ``True`` when the body must be passed through untouched.
    """
    if response_media_type(response) in UNBUFFERED_MEDIA_TYPES:
        return True
    return response.headers.get("content-length") is None


__all__: list[str] = [
    "UNBUFFERED_MEDIA_TYPES",
    "is_unbounded_stream",
    "response_media_type",
]
