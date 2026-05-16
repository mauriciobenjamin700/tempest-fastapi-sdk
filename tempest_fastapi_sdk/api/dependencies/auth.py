"""Authentication dependencies (shared-secret token validation)."""

from __future__ import annotations

import hmac
from collections.abc import Callable, Coroutine
from typing import Any

from fastapi import Header

from tempest_fastapi_sdk.exceptions.unauthorized import UnauthorizedException


def make_token_dependency(
    secret: str,
    *,
    header_name: str = "X-Token",
    error_message: str = "Invalid or missing token",
) -> Callable[..., Coroutine[Any, Any, None]]:
    """Build a FastAPI dependency that validates a shared-secret header.

    The returned coroutine compares the inbound header value with
    ``secret`` using :func:`hmac.compare_digest` (constant-time). An
    empty ``secret`` disables the check entirely — intentional for
    local development; production deployments should always provide
    a non-empty value.

    Args:
        secret (str): The shared secret to compare against. Empty
            string disables enforcement.
        header_name (str): The header to read. Defaults to
            ``"X-Token"``.
        error_message (str): Message attached to the raised
            :class:`UnauthorizedException`.

    Returns:
        Callable[..., Coroutine[Any, Any, None]]: An async FastAPI
        dependency that raises :class:`UnauthorizedException` on
        mismatch and returns ``None`` on success.
    """
    alias = header_name

    async def _require_token(token: str = Header(default="", alias=alias)) -> None:
        if not secret:
            return
        if not hmac.compare_digest(token, secret):
            raise UnauthorizedException(message=error_message)

    _require_token.__doc__ = (
        f"Validate the {alias} header against the configured shared secret."
    )
    return _require_token


async def require_x_token(
    secret: str,
    token: str,
    *,
    error_message: str = "Invalid or missing token",
) -> None:
    """Imperative variant of :func:`make_token_dependency`.

    Useful when validation happens outside the FastAPI dependency
    pipeline (e.g. from a websocket handler or background task).

    Args:
        secret (str): The shared secret.
        token (str): The token to verify.
        error_message (str): Message attached on failure.

    Raises:
        UnauthorizedException: When ``secret`` is non-empty and the
            tokens do not match in constant time.
    """
    if not secret:
        return
    if not hmac.compare_digest(token, secret):
        raise UnauthorizedException(message=error_message)


__all__: list[str] = [
    "make_token_dependency",
    "require_x_token",
]
