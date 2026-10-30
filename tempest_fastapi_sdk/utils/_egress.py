"""Shared egress policy for outbound requests to untrusted destinations.

Several SDK components send a request to a URL that someone else chose: the
RAG :class:`~tempest_fastapi_sdk.genai.rag.ContentExtractor` fetches pages a
search engine or a user named, and the
:class:`~tempest_fastapi_sdk.webpush.WebPushDispatcher` posts to the
endpoint a browser subscriber registered. Both must refuse destinations
inside the server's own network. This module is the single place that
decides what "public" means, so every caller agrees on the answer.
"""

from __future__ import annotations

import asyncio
import ipaddress
import socket
from collections.abc import Awaitable, Callable, Sequence

Resolver = Callable[[str], Awaitable[Sequence[str]]]
"""Async callable mapping a hostname to the IP addresses it resolves to."""


async def system_resolver(host: str) -> list[str]:
    """Resolve ``host`` through the event loop's ``getaddrinfo``.

    Args:
        host (str): The hostname to resolve.

    Returns:
        list[str]: Every address the system resolver returned.
    """
    loop = asyncio.get_running_loop()
    infos = await loop.getaddrinfo(host, None, type=socket.SOCK_STREAM)
    return [str(info[4][0]) for info in infos]


def is_public_address(address: str) -> bool:
    """Return ``True`` when ``address`` is a globally routable unicast IP.

    Refuses loopback, private (RFC 1918 / ULA), link-local (including the
    ``169.254.169.254`` cloud metadata endpoint), shared (CGNAT), reserved,
    unspecified and multicast addresses, for IPv4 and IPv6. IPv4-mapped
    IPv6 (``::ffff:127.0.0.1``) is judged by the IPv4 address it wraps.

    Args:
        address (str): The textual IP address (an IPv6 zone id is ignored).

    Returns:
        bool: Whether the address may be contacted.

    Raises:
        ValueError: When ``address`` is not an IP address.
    """
    ip: ipaddress.IPv4Address | ipaddress.IPv6Address = ipaddress.ip_address(
        address.split("%", 1)[0]
    )
    if isinstance(ip, ipaddress.IPv6Address) and ip.ipv4_mapped is not None:
        ip = ip.ipv4_mapped
    return ip.is_global and not ip.is_multicast


__all__: list[str] = [
    "Resolver",
    "is_public_address",
    "system_resolver",
]
