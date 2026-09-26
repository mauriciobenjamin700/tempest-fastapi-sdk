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

Each hop connects to the address that passed the check, not to a second
lookup of the name: the request URL carries the validated IP, while the
``Host`` header and the TLS SNI name (``sni_hostname``) keep the original
hostname, so the certificate is still validated against that name. A DNS
answer that changes between the check and the connection (DNS rebinding)
therefore never reaches the connection.
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

MAX_TLS_NAME_RETRIES: int = 3
"""Pooled connections to the same IP discarded, per hop, for a wrong TLS name.

A connection the pool opened for another hostname that resolved to the
same IP carries that hostname's TLS session. The check runs once the
response headers arrive, so the request line and headers have already gone
to that server; the extractor then refuses to read the body and retries
the hop. Each retry closes one such connection, so after this many the hop
is reported as failed.
"""

Resolver = Callable[[str], Awaitable[Sequence[str]]]
"""Async callable mapping a hostname to the IP addresses it resolves to."""


class _BlockedDestinationError(Exception):
    """Raised internally when a hop is refused by the egress guard."""


class _WrongTLSNameError(Exception):
    """Raised internally when a response arrived on another host's TLS session."""


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

    The connection goes to the address that passed the check: the hop is
    sent to ``scheme://<validated-ip>:<port>`` with the original ``Host``
    header and ``sni_hostname`` extension, so a DNS answer that changes
    after the check (DNS rebinding) is never connected, and HTTPS still
    validates the certificate against the hostname. Because the pool keys
    connections by IP, an HTTPS response that arrives on a connection whose
    TLS session was negotiated for another hostname is discarded and the
    hop retried on a fresh connection (see :data:`MAX_TLS_NAME_RETRIES`).
    Through an HTTP proxy, httpcore 1.0.9 tunnels to the pinned IP and
    does not send ``sni_hostname``, so certificate validation fails and
    HTTPS fetches come back ``failed=True``; ``allow_private_networks=True``
    turns pinning off along with the address check, leaving the proxy as
    the egress guard.

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
                used by the private-address check, called once per hop;
                the first address it returns is the one the hop connects
                to. ``None`` uses the event loop's ``getaddrinfo``.
        """
        self._http = http_client
        self.user_agent = user_agent
        self.timeout = timeout
        self.allow_private_networks = allow_private_networks
        self.max_redirects = max_redirects
        self.max_response_bytes = max_response_bytes
        self._resolver: Resolver = resolver or _system_resolver

    async def _pin_destination(self, url: httpx.URL) -> str | None:
        """Refuse a disallowed hop and return the address to connect to.

        Args:
            url (httpx.URL): The URL about to be requested.

        Returns:
            str | None: The validated IP the hop must connect to, or
            ``None`` when the URL is sent unchanged (private networks are
            allowed, or the host is already an IP literal).

        Raises:
            _BlockedDestinationError: When the scheme is not ``http``/
                ``https``, the host is empty, or (unless private networks
                are allowed) any resolved address is not public.
        """
        if url.scheme not in ALLOWED_SCHEMES or not url.host:
            raise _BlockedDestinationError(str(url))
        if self.allow_private_networks:
            return None
        try:
            literal = str(ipaddress.ip_address(url.host))
        except ValueError:
            literal = None
        if literal is not None:
            if not _is_public_address(literal):
                raise _BlockedDestinationError(str(url))
            return None
        addresses = await self._resolver(url.raw_host.decode("ascii"))
        if not addresses or not all(_is_public_address(a) for a in addresses):
            raise _BlockedDestinationError(str(url))
        return str(addresses[0]).split("%", 1)[0]

    @staticmethod
    def _check_tls_name(response: httpx.Response, sni_hostname: str) -> None:
        """Refuse a response read over a TLS session for another hostname.

        Only runs when the transport exposes the TLS object (httpcore does,
        through the ``network_stream`` extension); a transport without one,
        such as ``httpx.MockTransport``, is taken as is.

        Args:
            response (httpx.Response): The response whose headers arrived.
            sni_hostname (str): The hostname the TLS session must be for.

        Raises:
            _WrongTLSNameError: When the session was negotiated for another
                name, which happens when the pool reuses a connection it
                opened for a different hostname on the same IP.
        """
        stream = response.extensions.get("network_stream")
        if stream is None:
            return
        ssl_object = stream.get_extra_info("ssl_object")
        if ssl_object is None:
            return
        if ssl_object.server_hostname != sni_hostname:
            raise _WrongTLSNameError(sni_hostname)

    async def _fetch_html(self, url: str) -> str:
        """Fetch ``url`` under the egress guard and return the decoded body.

        Each hop is pinned to the address :meth:`_pin_destination` validated;
        redirects are resolved against the original URL, so the next hop is
        checked and pinned on its own hostname. An HTTPS response that
        arrives over another hostname's TLS session is abandoned unread,
        which makes httpcore close that connection, and the hop is retried
        up to :data:`MAX_TLS_NAME_RETRIES` times.

        Args:
            url (str): The page to fetch.

        Returns:
            str: The response body, decoded with its declared charset
            (UTF-8 when none is declared).

        Raises:
            _BlockedDestinationError: When a hop is refused, the redirect
                bound is exceeded, the body is over the cap, or no
                connection with the right TLS name could be obtained.
            httpx.HTTPError: On transport or HTTP status errors.
        """
        import httpx

        current = httpx.URL(url)
        for _ in range(self.max_redirects + 1):
            address = await self._pin_destination(current)
            target = current
            headers = {"User-Agent": self.user_agent}
            extensions: dict[str, str] = {}
            if address is not None:
                target = current.copy_with(host=address)
                headers["Host"] = current.netloc.decode("ascii")
                extensions["sni_hostname"] = current.raw_host.decode("ascii")
            for _attempt in range(MAX_TLS_NAME_RETRIES + 1):
                try:
                    async with self._http.stream(
                        "GET",
                        target,
                        headers=headers,
                        timeout=self.timeout,
                        follow_redirects=False,
                        extensions=extensions,
                    ) as response:
                        if target.scheme == "https" and address is not None:
                            self._check_tls_name(response, extensions["sni_hostname"])
                        location = response.headers.get("location")
                        if response.status_code in REDIRECT_STATUSES and location:
                            current = current.join(location)
                            break
                        return await self._read_body(response, current)
                except _WrongTLSNameError:
                    continue
            else:
                raise _BlockedDestinationError(str(current))
        raise _BlockedDestinationError(str(current))

    async def _read_body(self, response: httpx.Response, url: httpx.URL) -> str:
        """Read a final response under the byte cap and decode it.

        Args:
            response (httpx.Response): The streamed, non-redirect response.
            url (httpx.URL): The original (unpinned) URL, for error context.

        Returns:
            str: The body decoded with its declared charset (UTF-8 when none
            is declared).

        Raises:
            _BlockedDestinationError: When the body is over the cap.
            httpx.HTTPStatusError: On a 4xx/5xx status.
        """
        response.raise_for_status()
        declared = response.headers.get("content-length", "")
        if declared.isdigit() and int(declared) > self.max_response_bytes:
            raise _BlockedDestinationError(str(url))
        body = bytearray()
        async for chunk in response.aiter_bytes():
            body.extend(chunk)
            if len(body) > self.max_response_bytes:
                raise _BlockedDestinationError(str(url))
        encoding = response.charset_encoding or "utf-8"
        return bytes(body).decode(encoding, errors="replace")

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
