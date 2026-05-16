"""Context variables propagated across the request lifecycle."""

from contextvars import ContextVar, Token

request_id_ctx: ContextVar[str | None] = ContextVar(
    "tempest_request_id",
    default=None,
)
"""Per-request correlation identifier.

Set by :class:`tempest_fastapi_sdk.api.middlewares.RequestIDMiddleware`
on every inbound HTTP request, consumed by
:class:`tempest_fastapi_sdk.core.logging.JSONFormatter` so every log
line carries the originating request ID.
"""


def get_request_id() -> str | None:
    """Return the current request ID, or ``None`` outside a request.

    Returns:
        str | None: The active correlation ID, or ``None`` when not set.
    """
    return request_id_ctx.get()


def set_request_id(value: str) -> Token[str | None]:
    """Bind a request ID for the current async context.

    Args:
        value (str): The correlation identifier to set.

    Returns:
        Token[str | None]: A token that can be passed to
        :func:`clear_request_id` to restore the previous value.
    """
    return request_id_ctx.set(value)


def clear_request_id(token: Token[str | None]) -> None:
    """Reset the request ID using the token returned by :func:`set_request_id`.

    Args:
        token (Token[str | None]): The token obtained from a previous
            :func:`set_request_id` call.
    """
    request_id_ctx.reset(token)


__all__: list[str] = [
    "clear_request_id",
    "get_request_id",
    "request_id_ctx",
    "set_request_id",
]
