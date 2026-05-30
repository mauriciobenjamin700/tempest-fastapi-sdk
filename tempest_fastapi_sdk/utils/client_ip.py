"""Trusted client-IP resolution for rate limiting and abuse tracking.

The naive approach — reading the leftmost entry of ``X-Forwarded-For``
— is a security hole: that header is client-controlled end to end, so
an attacker can spoof ``X-Forwarded-For: 8.8.8.8`` to dodge per-IP rate
limits, or ``X-Forwarded-For: <victim-ip>`` to get a victim banned.

These helpers resolve the client IP from a SINGLE header that the edge
proxy sets itself (e.g. Nginx ``proxy_set_header X-Real-IP $remote_addr``,
whose value is the TCP peer the edge talks to and therefore cannot be
forged upstream of the edge). When no trusted header is configured, or
it is absent, they fall back to the direct ASGI transport peer.

Rules of thumb:

- Behind a reverse proxy: pass ``trusted_header="x-real-ip"`` (or
  whatever single-hop header your edge sets) and make sure the edge
  OVERWRITES it on every request.
- Multiple proxy hops / CDN: use the CDN's verified header
  (``CF-Connecting-IP``, ``True-Client-IP``, …) — never blind
  ``X-Forwarded-For`` parsing.
- No proxy: leave ``trusted_header=None`` to use the peer address.
"""

from starlette.requests import Request
from starlette.types import Scope

_UNKNOWN: str = "unknown"


def get_client_ip(
    request: Request,
    *,
    trusted_header: str | None = None,
) -> str:
    """Resolve the client IP from a Starlette/FastAPI request.

    Args:
        request (Request): The inbound request.
        trusted_header (str | None): Name of the single edge-set header
            to trust (case-insensitive, e.g. ``"x-real-ip"``). ``None``
            uses the direct transport peer only.

    Returns:
        str: The resolved client IP, or ``"unknown"`` when neither the
            trusted header nor the transport peer is available.
    """
    if trusted_header:
        value = request.headers.get(trusted_header)
        if value:
            return value.strip()
    return request.client.host if request.client else _UNKNOWN


def get_client_ip_from_scope(
    scope: Scope,
    *,
    trusted_header: str | None = None,
) -> str:
    """Resolve the client IP from a raw ASGI scope.

    Mirrors :func:`get_client_ip` for middleware that sees the ASGI
    scope directly instead of a Starlette :class:`Request`.

    Args:
        scope (Scope): The ASGI scope.
        trusted_header (str | None): Name of the single edge-set header
            to trust (case-insensitive). ``None`` uses the transport
            peer only.

    Returns:
        str: The resolved client IP, or ``"unknown"`` when unavailable.
    """
    if trusted_header:
        wanted = trusted_header.lower().encode("latin-1")
        for key, raw in scope.get("headers") or []:
            if key.lower() == wanted and raw:
                return raw.decode("latin-1").strip()
    client = scope.get("client")
    if client and len(client) >= 1 and client[0]:
        return client[0]
    return _UNKNOWN


__all__: list[str] = [
    "get_client_ip",
    "get_client_ip_from_scope",
]
