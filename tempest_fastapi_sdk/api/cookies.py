"""Secure-by-default cookie helpers for auth flows.

Thin wrappers over ``Response.set_cookie`` / ``delete_cookie`` that flip
the security flags to safe defaults (``HttpOnly``, ``Secure``,
``SameSite``) so a token cookie can't be read by JS or replayed
cross-site by accident. The ``set``/``clear`` flags are kept in sync so
the browser actually drops the cookie on logout (a ``delete_cookie``
whose attributes don't match the original is silently ignored).

Typical use — short-lived access cookie at ``/`` plus a refresh cookie
scoped to the refresh endpoint so it is never sent on ordinary calls:

    set_cookie(response, "access_token", access, max_age=900)
    set_cookie(
        response, "refresh_token", refresh,
        max_age=1209600, path="/api/auth",
    )
"""

from typing import Literal

from starlette.responses import Response

SameSite = Literal["lax", "strict", "none"]


def set_cookie(
    response: Response,
    name: str,
    value: str,
    *,
    max_age: int | None = None,
    path: str = "/",
    domain: str | None = None,
    http_only: bool = True,
    secure: bool = True,
    samesite: SameSite = "lax",
) -> None:
    """Set a security-hardened cookie on ``response``.

    Args:
        response (Response): The outgoing response.
        name (str): Cookie name.
        value (str): Cookie value (e.g. a JWT).
        max_age (int | None): Lifetime in seconds. ``None`` (or ``0``)
            emits a session cookie with no ``Max-Age``. Mirror your
            token's TTL so the browser drops it when the token expires.
        path (str): Cookie path. Scope sensitive cookies (refresh
            tokens) to their endpoint to shrink the blast radius.
        domain (str | None): Cookie domain. ``None`` binds to the
            current host.
        http_only (bool): Block JS access (``document.cookie``). Default
            ``True``.
        secure (bool): Require HTTPS. Default ``True``; mandatory when
            ``samesite="none"``.
        samesite (SameSite): ``"lax"`` (default), ``"strict"`` or
            ``"none"``. Cross-site SPAs need ``"none"`` (plus
            ``secure=True``).
    """
    response.set_cookie(
        key=name,
        value=value,
        max_age=max_age if max_age else None,
        path=path,
        domain=domain,
        httponly=http_only,
        secure=secure,
        samesite=samesite,
    )


def clear_cookie(
    response: Response,
    name: str,
    *,
    path: str = "/",
    domain: str | None = None,
    http_only: bool = True,
    secure: bool = True,
    samesite: SameSite = "lax",
) -> None:
    """Delete a cookie previously set with :func:`set_cookie`.

    The flags must match those used when the cookie was set, otherwise
    the browser ignores the deletion. Pass the same ``path`` / ``domain``
    / ``samesite`` / ``secure`` / ``http_only`` you used originally.

    Args:
        response (Response): The outgoing response.
        name (str): Cookie name.
        path (str): Path the cookie was set on.
        domain (str | None): Domain the cookie was set on.
        http_only (bool): Must match the original flag.
        secure (bool): Must match the original flag.
        samesite (SameSite): Must match the original attribute.
    """
    response.delete_cookie(
        key=name,
        path=path,
        domain=domain,
        httponly=http_only,
        secure=secure,
        samesite=samesite,
    )


__all__: list[str] = [
    "SameSite",
    "clear_cookie",
    "set_cookie",
]
