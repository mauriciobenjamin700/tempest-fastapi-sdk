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

The header's value must parse as one IP address
(:func:`ipaddress.ip_address`); anything else — a list, a port, a
hostname, arbitrary text — falls back to the transport peer. Without
that, a client the edge forgot to sanitize could send a fresh string
per request and get a fresh rate-limit budget each time, and every one
of those strings became a key in an in-memory store. The address is
returned in its canonical form, so ``2001:0db8::1`` and ``2001:db8::1``
are one client.
"""

import ipaddress

from starlette.requests import Request
from starlette.types import Scope

_UNKNOWN: str = "unknown"


IPV4_MAPPED_PREFIX: str = "::ffff:"
"""Prefix of an IPv4-mapped IPv6 address in its canonical text form."""


def parse_client_ip(value: str) -> str | None:
    """Return ``value`` as a canonical IP address, or ``None``.

    An IPv4-mapped IPv6 address is rendered with the IPv4 part dotted
    (``::ffff:1.2.3.4``) on every Python version. ``str()`` of the
    address does that only from Python 3.13 on; 3.12 renders the same
    address as ``::ffff:102:304``, so two replicas on different Pythons
    keyed one client under two rate-limit buckets.

    Args:
        value (str): A header value claimed to be one client address.

    Returns:
        str | None: The canonical form (``ipaddress.ip_address`` rendered
        back to text) after stripping whitespace, or ``None`` when the
        value is not exactly one IPv4 or IPv6 address.
    """
    try:
        address = ipaddress.ip_address(value.strip())
    except ValueError:
        return None
    if isinstance(address, ipaddress.IPv6Address) and address.ipv4_mapped is not None:
        scope = f"%{address.scope_id}" if address.scope_id else ""
        return f"{IPV4_MAPPED_PREFIX}{address.ipv4_mapped}{scope}"
    return str(address)


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
            trusted header nor the transport peer is available. A header
            value that is not one IP address is ignored (see
            :func:`parse_client_ip`).
    """
    if trusted_header:
        value = request.headers.get(trusted_header)
        parsed = parse_client_ip(value) if value else None
        if parsed is not None:
            return parsed
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
            A header value that is not one IP address is ignored.
    """
    if trusted_header:
        wanted = trusted_header.lower().encode("latin-1")
        for key, raw in scope.get("headers") or []:
            if key.lower() == wanted and raw:
                parsed = parse_client_ip(raw.decode("latin-1"))
                if parsed is not None:
                    return parsed
                break
    client = scope.get("client")
    if client and len(client) >= 1 and client[0]:
        host: str = client[0]
        return host
    return _UNKNOWN


__all__: list[str] = [
    "get_client_ip",
    "get_client_ip_from_scope",
    "parse_client_ip",
]
