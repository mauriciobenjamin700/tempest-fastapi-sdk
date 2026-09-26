"""The extractor connects to the address it validated, not to a fresh lookup.

Regression tests for DNS rebinding (#315): the SSRF guard resolved the host,
checked the answer, and then let ``httpx`` resolve the name again at connect
time, so a hostname that answered a public IP first and ``127.0.0.1`` second
reached the private address. The HTTPS tests run a real TLS server on
loopback to prove the certificate is still validated against the original
hostname (through SNI), and never against the pinned IP.
"""

from __future__ import annotations

import asyncio
import datetime
import ipaddress
import ssl
from collections.abc import AsyncIterator, Iterable, Sequence
from dataclasses import dataclass, field

import httpcore
import httpx
import pytest
from cryptography import x509
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import ec
from cryptography.x509.oid import NameOID

from tempest_fastapi_sdk.genai.rag import ContentExtractor, extract

PUBLIC_IP: str = "93.184.216.34"
"""Public address the rebinding resolver answers on its first lookup."""

PRIVATE_IP: str = "127.0.0.1"
"""Private address the rebinding resolver answers on every later lookup."""

HTTP_OK: bytes = (
    b"HTTP/1.1 200 OK\r\nContent-Type: text/html\r\nContent-Length: 12\r\n\r\n"
    b"public page."
)
"""Canned response the recording backend replays for every connection."""


class RebindingResolver:
    """Answer a public IP on the first lookup and a private one afterwards.

    Attributes:
        calls (list[str]): Every hostname looked up, in order.
    """

    def __init__(self) -> None:
        """Start with no lookups recorded."""
        self.calls: list[str] = []

    async def __call__(self, host: str) -> list[str]:
        """Resolve ``host`` the way a rebinding attacker's DNS would.

        Args:
            host (str): The hostname being resolved.

        Returns:
            list[str]: ``[PUBLIC_IP]`` the first time, ``[PRIVATE_IP]`` after.
        """
        self.calls.append(host)
        return [PUBLIC_IP] if len(self.calls) == 1 else [PRIVATE_IP]


class RecordingBackend(httpcore.AsyncMockBackend):
    """Network backend that records where each TCP connection would go.

    A hostname reaching ``connect_tcp`` is resolved through ``resolver``,
    exactly like the system lookup the real backend performs at connect
    time; an IP literal is connected as is.

    Attributes:
        connected (list[str]): The address each connection went to.
    """

    def __init__(self, resolver: RebindingResolver) -> None:
        """Replay :data:`HTTP_OK` and resolve names through ``resolver``.

        Args:
            resolver (RebindingResolver): The lookup used for hostnames.
        """
        super().__init__([HTTP_OK])
        self._resolver = resolver
        self.connected: list[str] = []

    async def connect_tcp(
        self,
        host: str,
        port: int,
        timeout: float | None = None,
        local_address: str | None = None,
        socket_options: Iterable[httpcore.SOCKET_OPTION] | None = None,
    ) -> httpcore.AsyncNetworkStream:
        """Record the destination, then hand back the canned stream.

        Args:
            host (str): The host httpcore is connecting to.
            port (int): The destination port.
            timeout (float | None): Connect timeout (unused).
            local_address (str | None): Bind address (unused).
            socket_options (Iterable[httpcore.SOCKET_OPTION] | None): Socket
                options (unused).

        Returns:
            httpcore.AsyncNetworkStream: A stream replaying :data:`HTTP_OK`.
        """
        try:
            address = str(ipaddress.ip_address(host))
        except ValueError:
            address = (await self._resolver(host))[0]
        self.connected.append(address)
        return await super().connect_tcp(host, port, timeout, local_address)


def _recording_client(backend: RecordingBackend) -> httpx.AsyncClient:
    """Build a real ``httpx`` client whose sockets go through ``backend``.

    Args:
        backend (RecordingBackend): The backend that records connections.

    Returns:
        httpx.AsyncClient: A client on the stock transport, with only the
        network backend of its connection pool swapped.
    """
    transport = httpx.AsyncHTTPTransport()
    transport._pool = httpcore.AsyncConnectionPool(network_backend=backend)
    return httpx.AsyncClient(transport=transport)


class TestRebindingIsClosed:
    """The address that passed the check is the address that is connected."""

    async def test_second_lookup_never_reaches_the_private_address(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        import trafilatura

        monkeypatch.setattr(trafilatura, "extract", lambda html: html)
        resolver = RebindingResolver()
        backend = RecordingBackend(resolver)
        client = _recording_client(backend)
        extractor = ContentExtractor(http_client=client, resolver=resolver)
        result = await extractor.extract("http://rebind.example/article")
        await client.aclose()
        assert backend.connected == [PUBLIC_IP]
        assert PRIVATE_IP not in backend.connected
        assert resolver.calls == ["rebind.example"]
        assert result.text == "public page."

    async def test_request_keeps_the_hostname_in_host_and_sni(self) -> None:
        seen: list[httpx.Request] = []

        async def resolver(host: str) -> list[str]:
            return {"a.example": ["93.184.216.34"], "b.example": ["1.1.1.1"]}[host]

        def handler(request: httpx.Request) -> httpx.Response:
            seen.append(request)
            if request.url.path == "/start":
                return httpx.Response(
                    302, headers={"Location": "https://b.example:8443/next"}
                )
            return httpx.Response(200, text="<html>done</html>")

        client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
        extractor = ContentExtractor(http_client=client, resolver=resolver)
        await extractor.extract("http://a.example/start")
        await client.aclose()
        assert [str(r.url) for r in seen] == [
            "http://93.184.216.34/start",
            "https://1.1.1.1:8443/next",
        ]
        assert [r.headers["host"] for r in seen] == ["a.example", "b.example:8443"]
        assert [r.extensions.get("sni_hostname") for r in seen] == [
            "a.example",
            "b.example",
        ]

    async def test_redirect_hop_is_pinned_to_its_own_lookup(self) -> None:
        seen: list[str] = []
        lookups: list[str] = []

        async def resolver(host: str) -> list[str]:
            lookups.append(host)
            return ["93.184.216.34"] if host == "a.example" else ["10.0.0.7"]

        def handler(request: httpx.Request) -> httpx.Response:
            seen.append(str(request.url))
            return httpx.Response(302, headers={"Location": "http://b.example/"})

        client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
        extractor = ContentExtractor(http_client=client, resolver=resolver)
        result = await extractor.extract("http://a.example/")
        await client.aclose()
        assert result.failed is True
        assert seen == ["http://93.184.216.34/"]
        assert lookups == ["a.example", "b.example"]

    async def test_ipv6_answer_is_bracketed_in_the_pinned_url(self) -> None:
        seen: list[httpx.Request] = []

        async def resolver(host: str) -> list[str]:
            return ["2606:2800:220:1:248:1893:25c8:1946"]

        def handler(request: httpx.Request) -> httpx.Response:
            seen.append(request)
            return httpx.Response(200, text="<html>v6</html>")

        client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
        extractor = ContentExtractor(http_client=client, resolver=resolver)
        await extractor.extract("http://v6.example/")
        await client.aclose()
        assert str(seen[0].url) == "http://[2606:2800:220:1:248:1893:25c8:1946]/"
        assert seen[0].headers["host"] == "v6.example"

    async def test_allow_private_networks_does_not_pin(self) -> None:
        seen: list[httpx.Request] = []

        def handler(request: httpx.Request) -> httpx.Response:
            seen.append(request)
            return httpx.Response(200, text="<html>wiki</html>")

        client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
        extractor = ContentExtractor(http_client=client, allow_private_networks=True)
        await extractor.extract("http://wiki.intranet/")
        await client.aclose()
        assert str(seen[0].url) == "http://wiki.intranet/"
        assert "sni_hostname" not in seen[0].extensions


@dataclass(slots=True)
class TLSAuthority:
    """A throwaway CA plus the client context that trusts it.

    Attributes:
        key (ec.EllipticCurvePrivateKey): The CA signing key.
        cert (x509.Certificate): The self-signed CA certificate.
        client_context (ssl.SSLContext): Verifying context trusting only it.
    """

    key: ec.EllipticCurvePrivateKey
    cert: x509.Certificate
    client_context: ssl.SSLContext

    def server_context(
        self, names: Sequence[str], tmp_path_factory: pytest.TempPathFactory
    ) -> ssl.SSLContext:
        """Issue a leaf certificate for ``names`` and load it in a context.

        Args:
            names (Sequence[str]): DNS names the leaf certificate covers.
            tmp_path_factory (pytest.TempPathFactory): Where the PEM files go.

        Returns:
            ssl.SSLContext: A server context presenting the leaf.
        """
        key = ec.generate_private_key(ec.SECP256R1())
        now = datetime.datetime.now(datetime.UTC)
        cert = (
            x509.CertificateBuilder()
            .subject_name(
                x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, names[0])])
            )
            .issuer_name(self.cert.subject)
            .public_key(key.public_key())
            .serial_number(x509.random_serial_number())
            .not_valid_before(now - datetime.timedelta(minutes=5))
            .not_valid_after(now + datetime.timedelta(hours=1))
            .add_extension(
                x509.SubjectAlternativeName([x509.DNSName(n) for n in names]),
                critical=False,
            )
            .sign(self.key, hashes.SHA256())
        )
        directory = tmp_path_factory.mktemp("tls")
        cert_path = directory / "leaf.pem"
        key_path = directory / "leaf.key"
        cert_path.write_bytes(cert.public_bytes(serialization.Encoding.PEM))
        key_path.write_bytes(
            key.private_bytes(
                serialization.Encoding.PEM,
                serialization.PrivateFormat.PKCS8,
                serialization.NoEncryption(),
            )
        )
        context = ssl.create_default_context(ssl.Purpose.CLIENT_AUTH)
        context.load_cert_chain(cert_path, key_path)
        return context


@pytest.fixture(scope="module")
def authority() -> TLSAuthority:
    """Create a CA and a client context that trusts nothing else.

    Returns:
        TLSAuthority: The CA key, certificate and verifying client context.
    """
    key = ec.generate_private_key(ec.SECP256R1())
    name = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, "tempest test CA")])
    now = datetime.datetime.now(datetime.UTC)
    cert = (
        x509.CertificateBuilder()
        .subject_name(name)
        .issuer_name(name)
        .public_key(key.public_key())
        .serial_number(x509.random_serial_number())
        .not_valid_before(now - datetime.timedelta(minutes=5))
        .not_valid_after(now + datetime.timedelta(hours=1))
        .add_extension(x509.BasicConstraints(ca=True, path_length=None), critical=True)
        .sign(key, hashes.SHA256())
    )
    client_context = ssl.create_default_context(
        cadata=cert.public_bytes(serialization.Encoding.PEM).decode("ascii")
    )
    return TLSAuthority(key=key, cert=cert, client_context=client_context)


@dataclass(slots=True)
class LoopbackTLSServer:
    """Keep-alive HTTPS server on ``127.0.0.1`` recording what it was sent.

    Attributes:
        port (int): The port the server listens on.
        sni (list[str | None]): The SNI name of every TLS handshake.
        hosts (list[str]): The ``Host`` header of every request served.
    """

    port: int = 0
    sni: list[str | None] = field(default_factory=list)
    hosts: list[str] = field(default_factory=list)

    async def handle(
        self, reader: asyncio.StreamReader, writer: asyncio.StreamWriter
    ) -> None:
        """Serve requests on one connection until the client goes away.

        Args:
            reader (asyncio.StreamReader): The decrypted request stream.
            writer (asyncio.StreamWriter): The decrypted response stream.
        """
        try:
            while True:
                head = await reader.readuntil(b"\r\n\r\n")
                for line in head.decode("latin-1").split("\r\n"):
                    if line.lower().startswith("host:"):
                        self.hosts.append(line.split(":", 1)[1].strip())
                writer.write(
                    b"HTTP/1.1 200 OK\r\nContent-Type: text/html\r\n"
                    b"Content-Length: 11\r\n\r\ntls reached"
                )
                await writer.drain()
        except (asyncio.IncompleteReadError, ConnectionError, ssl.SSLError):
            pass
        finally:
            writer.close()


@pytest.fixture
async def tls_server(
    request: pytest.FixtureRequest,
    authority: TLSAuthority,
    tmp_path_factory: pytest.TempPathFactory,
) -> AsyncIterator[LoopbackTLSServer]:
    """Start a loopback HTTPS server presenting a cert for the given names.

    The names come from ``@pytest.mark.parametrize(..., indirect=True)``.

    Args:
        request (pytest.FixtureRequest): Carries the DNS names in ``param``.
        authority (TLSAuthority): The CA that signs the server certificate.
        tmp_path_factory (pytest.TempPathFactory): Where the PEM files go.

    Yields:
        LoopbackTLSServer: The running server and its records.
    """
    names: Sequence[str] = request.param
    context = authority.server_context(names, tmp_path_factory)
    state = LoopbackTLSServer()

    def record_sni(
        _sock: ssl.SSLObject, name: str | None, _ctx: ssl.SSLContext
    ) -> None:
        """Record the SNI name the client sent.

        Args:
            _sock (ssl.SSLObject): The connection being handshaken.
            name (str | None): The SNI name, ``None`` when none was sent.
            _ctx (ssl.SSLContext): The server context.
        """
        state.sni.append(name)

    context.sni_callback = record_sni
    server = await asyncio.start_server(state.handle, PRIVATE_IP, 0, ssl=context)
    state.port = server.sockets[0].getsockname()[1]
    yield state
    server.close()
    await server.wait_closed()


@pytest.fixture
def loopback_allowed(monkeypatch: pytest.MonkeyPatch) -> None:
    """Let the guard accept ``127.0.0.1`` so the loopback server is reachable.

    Pinning still runs: only the public-address predicate is relaxed, the
    resolution and the pinned connection are the production path.

    Args:
        monkeypatch (pytest.MonkeyPatch): Patches the address predicate.
    """
    import trafilatura

    monkeypatch.setattr(extract, "_is_public_address", lambda _address: True)
    monkeypatch.setattr(trafilatura, "extract", lambda html: html)


async def _loopback_resolver(host: str) -> list[str]:
    """Resolve every name to the loopback TLS server.

    Args:
        host (str): The hostname being resolved.

    Returns:
        list[str]: ``[PRIVATE_IP]``.
    """
    return [PRIVATE_IP]


@pytest.mark.usefixtures("loopback_allowed")
class TestHTTPSStillValidatesTheHostname:
    """Pinning to an IP does not weaken certificate validation."""

    @pytest.mark.parametrize("tls_server", [["pinned.test"]], indirect=True)
    async def test_certificate_for_the_hostname_is_accepted(
        self, tls_server: LoopbackTLSServer, authority: TLSAuthority
    ) -> None:
        client = httpx.AsyncClient(verify=authority.client_context)
        extractor = ContentExtractor(http_client=client, resolver=_loopback_resolver)
        result = await extractor.extract(f"https://pinned.test:{tls_server.port}/")
        await client.aclose()
        assert result.text == "tls reached"
        assert tls_server.sni == ["pinned.test"]
        assert tls_server.hosts == [f"pinned.test:{tls_server.port}"]

    @pytest.mark.parametrize("tls_server", [["other.test"]], indirect=True)
    async def test_certificate_for_another_name_is_refused(
        self, tls_server: LoopbackTLSServer, authority: TLSAuthority
    ) -> None:
        client = httpx.AsyncClient(verify=authority.client_context)
        extractor = ContentExtractor(http_client=client, resolver=_loopback_resolver)
        result = await extractor.extract(f"https://pinned.test:{tls_server.port}/")
        await client.aclose()
        assert result.failed is True
        assert tls_server.sni == ["pinned.test"]
        assert tls_server.hosts == []

    @pytest.mark.parametrize("tls_server", [["a.test"]], indirect=True)
    async def test_pooled_connection_is_not_reused_for_another_hostname(
        self, tls_server: LoopbackTLSServer, authority: TLSAuthority
    ) -> None:
        client = httpx.AsyncClient(verify=authority.client_context)
        extractor = ContentExtractor(http_client=client, resolver=_loopback_resolver)
        first = await extractor.extract(f"https://a.test:{tls_server.port}/")
        second = await extractor.extract(f"https://b.test:{tls_server.port}/")
        await client.aclose()
        assert first.text == "tls reached"
        assert second.failed is True
        assert tls_server.sni == ["a.test", "b.test"]

    @pytest.mark.parametrize("tls_server", [["a.test", "b.test"]], indirect=True)
    async def test_hostnames_sharing_an_ip_each_get_their_own_handshake(
        self, tls_server: LoopbackTLSServer, authority: TLSAuthority
    ) -> None:
        client = httpx.AsyncClient(verify=authority.client_context)
        extractor = ContentExtractor(http_client=client, resolver=_loopback_resolver)
        first = await extractor.extract(f"https://a.test:{tls_server.port}/")
        second = await extractor.extract(f"https://b.test:{tls_server.port}/")
        again = await extractor.extract(f"https://b.test:{tls_server.port}/")
        await client.aclose()
        assert [first.text, second.text, again.text] == ["tls reached"] * 3
        assert tls_server.sni == ["a.test", "b.test"]
        assert tls_server.hosts == [
            f"a.test:{tls_server.port}",
            f"b.test:{tls_server.port}",
            f"b.test:{tls_server.port}",
            f"b.test:{tls_server.port}",
        ]
