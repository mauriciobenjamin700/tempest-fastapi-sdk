"""FastAPI dependency that gates a route behind a signed URL."""

from __future__ import annotations

from collections.abc import Callable, Coroutine
from typing import Any

from fastapi import Query, Request

from tempest_fastapi_sdk.exceptions.signed_url import InvalidSignedURLException
from tempest_fastapi_sdk.utils.signed_url import (
    SIGNED_URL_EXPIRES_PARAM,
    SIGNED_URL_SIGNATURE_PARAM,
    _require_material,
    verify_path,
)


def make_signed_path_dependency(
    *,
    secret: str,
    purpose: str,
) -> Callable[..., Coroutine[Any, Any, None]]:
    """Build a dependency that accepts only URLs signed by :func:`sign_path`.

    The returned coroutine reads ``expires`` and ``signature`` from the query
    string and verifies them against the **decoded request path**
    (``request.scope["path"]``) — the same string :func:`sign_path` signed and
    the route matched. Attach it with ``dependencies=[Depends(...)]`` and the
    handler only runs for an authentic, unexpired URL; nothing else about the
    caller is checked, because the mapper that issued the URL already
    authorized them.

    Both query parameters are declared, so they show up in the OpenAPI schema
    of the route. They are read as strings and validated here rather than by
    FastAPI, so a missing or non-numeric ``expires`` answers the same ``403``
    as a forged one instead of a ``422`` that names the parameter. That
    includes an ``expires`` longer than Python's int-conversion limit (4300
    digits), whose ``int()`` raises ``ValueError`` and would otherwise be a
    ``500``.

    Args:
        secret (str): The secret the URLs are signed with.
        purpose (str): The purpose the URLs are signed for. A URL signed for
            another purpose is rejected.

    Returns:
        Callable[..., Coroutine[Any, Any, None]]: An async FastAPI dependency
        that returns ``None`` on success and raises
        :class:`InvalidSignedURLException` or
        :class:`ExpiredSignedURLException` (both ``403``) otherwise.

    Raises:
        ValueError: If ``secret`` or ``purpose`` is empty — at build time, so
            a missing setting fails the boot instead of every request.
    """
    _require_material(secret, purpose)

    async def _verify_signed_path(
        request: Request,
        expires: str = Query(
            default="",
            alias=SIGNED_URL_EXPIRES_PARAM,
            description="Expiry of the signed URL, in unix seconds.",
        ),
        signature: str = Query(
            default="",
            alias=SIGNED_URL_SIGNATURE_PARAM,
            description="HMAC-SHA256 of the path and expiry, URL-safe base64.",
        ),
    ) -> None:
        if not (expires.isascii() and expires.isdigit()) or not signature:
            raise InvalidSignedURLException()
        try:
            expires_at = int(expires)
        except ValueError:
            raise InvalidSignedURLException() from None
        verify_path(
            request.scope["path"],
            expires=expires_at,
            signature=signature,
            secret=secret,
            purpose=purpose,
        )

    _verify_signed_path.__doc__ = (
        f"Verify the signed URL issued for purpose {purpose!r}."
    )
    return _verify_signed_path


__all__: list[str] = [
    "make_signed_path_dependency",
]
