"""Fetch a page and extract its main text (HTML → clean text).

Search snippets are thin; to give an LLM real ground truth you fetch each
result and pull the article body out of the HTML. Uses ``trafilatura``
(the leviathan choice) for cleaning. Failures never raise — a page that
times out or yields nothing comes back as ``failed=True`` with empty
text, so no source is silently dropped.

The URLs fed to the extractor usually come from a search engine or from a
user, so the fetch is treated as untrusted egress: only ``http``/``https``
is allowed, every hop (the first request and each redirect) must resolve
to public addresses, redirects are followed by hand up to a bound, and the
body is streamed with a byte cap.
"""

from __future__ import annotations

import asyncio
import ipaddress
import socket
from collections.abc import Awaitable, Callable, Sequence
from dataclasses import dataclass
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    import httpx

ALLOWED_SCHEMES: frozenset[str] = frozenset({"http", "https"})
"""URL schemes :class:`ContentExtractor` is willing to fetch."""

DEFAULT_MAX_REDIRECTS: int = 5
"""Redirect hops followed before a fetch is given up."""

DEFAULT_MAX_RESPONSE_BYTES: int = 5 * 1024 * 1024
"""Largest response body (5 MiB) read before a fetch is given up."""

REDIRECT_STATUSES: frozenset[int] = frozenset({301, 302, 303, 307, 308})
"""HTTP statuses whose ``Location`` the extractor follows."""

Resolver = Callable[[str], Awaitable[Sequence[str]]]
"""Async callable mapping a hostname to the IP addresses it resolves to."""


class _BlockedDestinationError(Exception):
    """Raised internally when a hop is refused by the egress guard."""


@dataclass(slots=True)
class ExtractionResult:
    """Outcome of fetching + extracting one URL.

    Attributes:
        text (str): The cleaned page body, or ``""`` on failure.
        failed (bool): ``True`` when the page couldn't be fetched, was
            refused (private destination, disallowed scheme, too many
            redirects, body over the cap) or no text could be extracted.
    """

    text: str
    failed: bool


def _require_trafilatura() -> object:
    """Import ``trafilatura`` or raise a helpful error.

    Returns:
        object: The ``trafilatura`` module.

    Raises:
        ImportError: When the ``[genai-rag]`` extra is not installed.
    """
    try:
        import trafilatura
    except ImportError as exc:
        raise ImportError(
            "Content extraction requires the optional [genai-rag] extra. "
            "Install with: pip install tempest-fastapi-sdk[genai-rag]",
        ) from exc
    return trafilatura


async def _system_resolver(host: str) -> list[str]:
    """Resolve ``host`` through the event loop's ``getaddrinfo``.

    Args:
        host (str): The hostname to resolve.

    Returns:
        list[str]: Every address the system resolver returned.
    """
    loop = asyncio.get_running_loop()
    infos = await loop.getaddrinfo(host, None, type=socket.SOCK_STREAM)
    return [str(info[4][0]) for info in infos]


def _is_public_address(address: str) -> bool:
    """Return ``True`` when ``address`` is a globally routable unicast IP.

    Refuses loopback, private (RFC 1918 / ULA), link-local (including the
    ``169.254.169.254`` cloud metadata endpoint), shared (CGNAT), reserved,
    unspecified and multicast addresses, for IPv4 and IPv6. IPv4-mapped
    IPv6 (``::ffff:127.0.0.1``) is judged by the IPv4 address it wraps.

    Args:
        address (str): The textual IP address (an IPv6 zone id is ignored).

    Returns:
        bool: Whether the address may be fetched.
    """
    ip: ipaddress.IPv4Address | ipaddress.IPv6Address = ipaddress.ip_address(
        address.split("%", 1)[0]
    )
    if isinstance(ip, ipaddress.IPv6Address) and ip.ipv4_mapped is not None:
        ip = ip.ipv4_mapped
    return ip.is_global and not ip.is_multicast


class ContentExtractor:
    """Fetch URLs and extract their main text via ``trafilatura``.

    The ``httpx.AsyncClient`` is injected so the connection pool is shared.

    By default every fetch is guarded against server-side request forgery:
    the scheme must be ``http``/``https``, the host (an IP literal, or every
    address a hostname resolves to) must be public, and the same check
    runs again on each redirect hop, which the extractor follows by hand up
    to ``max_redirects``. The body is streamed and the fetch is abandoned
    once it exceeds ``max_response_bytes``. Any refusal comes back as
    ``failed=True``, like every other failure.

    The address check and the connection are two separate lookups, so a
    hostname whose DNS answer changes between them (DNS rebinding) is not
    covered; when that matters, route egress through a proxy or firewall
    that enforces the same rule at connect time.

    Attributes:
        user_agent (str): ``User-Agent`` header sent with each fetch.
        timeout (float): Per-request timeout in seconds.
        allow_private_networks (bool): Whether the private-address check
            is disabled.
        max_redirects (int): Redirect hops followed before giving up.
        max_response_bytes (int): Largest body read before giving up.
    """

    def __init__(
        self,
        *,
        http_client: httpx.AsyncClient,
        user_agent: str = "tempest-fastapi-sdk/genai",
        timeout: float = 10.0,
        allow_private_networks: bool = False,
        max_redirects: int = DEFAULT_MAX_REDIRECTS,
        max_response_bytes: int = DEFAULT_MAX_RESPONSE_BYTES,
        resolver: Resolver | None = None,
    ) -> None:
        """Initialize the extractor.

        Args:
            http_client (httpx.AsyncClient): Injected async client. Its own
                ``follow_redirects`` setting is overridden per request —
                redirects are always followed by the extractor, one checked
                hop at a time.
            user_agent (str): ``User-Agent`` for fetches.
            timeout (float): Per-request timeout in seconds.
            allow_private_networks (bool): Skip the private-address check,
                for an extractor that must read intranet pages. The scheme
                check, the redirect bound and the body cap still apply.
                Only turn this on when the URLs are not attacker-controlled.
            max_redirects (int): Redirect hops followed before the fetch
                is reported as failed.
            max_response_bytes (int): Largest body, in bytes, read before
                the fetch is reported as failed.
            resolver (Resolver | None): Async hostname → addresses lookup
                used by the private-address check. ``None`` uses the event
                loop's ``getaddrinfo``.
        """
        self._http = http_client
        self.user_agent = user_agent
        self.timeout = timeout
        self.allow_private_networks = allow_private_networks
        self.max_redirects = max_redirects
        self.max_response_bytes = max_response_bytes
        self._resolver: Resolver = resolver or _system_resolver

    async def _check_destination(self, url: httpx.URL) -> None:
        """Refuse a hop whose scheme or resolved addresses are not allowed.

        Args:
            url (httpx.URL): The URL about to be requested.

        Raises:
            _BlockedDestinationError: When the scheme is not ``http``/
                ``https``, the host is empty, or (unless private networks
                are allowed) any resolved address is not public.
        """
        if url.scheme not in ALLOWED_SCHEMES or not url.host:
            raise _BlockedDestinationError(str(url))
        if self.allow_private_networks:
            return
        addresses: Sequence[str]
        try:
            addresses = [str(ipaddress.ip_address(url.host))]
        except ValueError:
            addresses = await self._resolver(url.host)
        if not addresses or not all(_is_public_address(a) for a in addresses):
            raise _BlockedDestinationError(str(url))

    async def _fetch_html(self, url: str) -> str:
        """Fetch ``url`` under the egress guard and return the decoded body.

        Args:
            url (str): The page to fetch.

        Returns:
            str: The response body, decoded with its declared charset
            (UTF-8 when none is declared).

        Raises:
            _BlockedDestinationError: When a hop is refused, the redirect
                bound is exceeded or the body is over the cap.
            httpx.HTTPError: On transport or HTTP status errors.
        """
        import httpx

        current = httpx.URL(url)
        for _ in range(self.max_redirects + 1):
            await self._check_destination(current)
            async with self._http.stream(
                "GET",
                current,
                headers={"User-Agent": self.user_agent},
                timeout=self.timeout,
                follow_redirects=False,
            ) as response:
                location = response.headers.get("location")
                if response.status_code in REDIRECT_STATUSES and location:
                    current = current.join(location)
                    continue
                response.raise_for_status()
                declared = response.headers.get("content-length", "")
                if declared.isdigit() and int(declared) > self.max_response_bytes:
                    raise _BlockedDestinationError(str(current))
                body = bytearray()
                async for chunk in response.aiter_bytes():
                    body.extend(chunk)
                    if len(body) > self.max_response_bytes:
                        raise _BlockedDestinationError(str(current))
                encoding = response.charset_encoding or "utf-8"
                return bytes(body).decode(encoding, errors="replace")
        raise _BlockedDestinationError(str(current))

    async def extract(self, url: str) -> ExtractionResult:
        """Fetch ``url`` and return its extracted main text.

        Never raises: fetch/extraction failures and refused destinations
        come back as ``ExtractionResult(text="", failed=True)``. The
        CPU-bound ``trafilatura`` pass runs in a worker thread
        (``asyncio.to_thread``), so a large page does not stall the event
        loop.

        Args:
            url (str): The page to fetch.

        Returns:
            ExtractionResult: The extracted text or a failure marker.
        """
        trafilatura = _require_trafilatura()
        try:
            html = await self._fetch_html(url)
        except Exception:
            return ExtractionResult(text="", failed=True)

        extracted: str | None = await asyncio.to_thread(
            trafilatura.extract,  # type: ignore[attr-defined]
            html,
        )
        text = extracted or ""
        return ExtractionResult(text=text, failed=not text)

    async def extract_many(
        self,
        urls: Sequence[str],
        *,
        concurrency: int = 5,
    ) -> list[ExtractionResult]:
        """Extract many URLs concurrently, capped at ``concurrency``.

        Results are returned in the same order as ``urls``. Individual
        failures are absorbed into their :class:`ExtractionResult` (never
        raised), so one bad page can't sink the batch.

        Args:
            urls (Sequence[str]): The pages to fetch.
            concurrency (int): Max simultaneous fetches.

        Returns:
            list[ExtractionResult]: One result per URL, in input order.
        """
        semaphore = asyncio.Semaphore(concurrency)

        async def _one(url: str) -> ExtractionResult:
            async with semaphore:
                return await self.extract(url)

        return await asyncio.gather(*[_one(url) for url in urls])


__all__: list[str] = [
    "ContentExtractor",
    "ExtractionResult",
]
