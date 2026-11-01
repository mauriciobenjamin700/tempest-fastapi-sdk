"""The Web Push dispatcher treats a subscriber-chosen endpoint as untrusted.

Regression tests for the endpoint egress policy: the subscription schema
only checked ``https`` and a non-empty host, so an endpoint naming an
internal address was dispatched to, a ``3xx`` from the push host was
followed to wherever ``Location`` pointed, and ``pywebpush`` ran with no
timeout. The network tests run the real ``pywebpush`` and ``requests``
stack against loopback servers; only the public-address predicate is
relaxed so loopback can stand in for a public host.
"""

from __future__ import annotations

import asyncio
import base64
import datetime
import gc
import ssl
from collections.abc import AsyncIterator, Sequence
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import pytest

pywebpush = pytest.importorskip("pywebpush")

from cryptography import x509  # noqa: E402
from cryptography.hazmat.primitives import hashes, serialization  # noqa: E402
from cryptography.hazmat.primitives.asymmetric import ec  # noqa: E402
from cryptography.x509.oid import NameOID  # noqa: E402

from tempest_fastapi_sdk.webpush import dispatcher as dispatcher_module  # noqa: E402
from tempest_fastapi_sdk.webpush.dispatcher import (  # noqa: E402
    DEFAULT_WEBPUSH_TIMEOUT,
    WebPushDispatcher,
    WebPushEndpointRefusedError,
    WebPushError,
)
from tempest_fastapi_sdk.webpush.schemas import (  # noqa: E402
    WebPushKeysSchema,
    WebPushSubscriptionSchema,
)

LOOPBACK: str = "127.0.0.1"
"""Address every loopback server listens on."""

PUSH_HOST: str = "push.test"
"""Hostname the endpoint names; only the test resolver knows it."""


def _b64url(raw: bytes) -> str:
    """Encode bytes as unpadded base64url.

    Args:
        raw (bytes): The bytes to encode.

    Returns:
        str: The unpadded base64url text.
    """
    return base64.urlsafe_b64encode(raw).decode("ascii").rstrip("=")


def _vapid_private_key() -> str:
    """Generate a VAPID private key in the base64url DER form pywebpush reads.

    Returns:
        str: The encoded private key.
    """
    key = ec.generate_private_key(ec.SECP256R1())
    der = key.private_bytes(
        serialization.Encoding.DER,
        serialization.PrivateFormat.PKCS8,
        serialization.NoEncryption(),
    )
    return _b64url(der)


def _subscription(endpoint: str) -> WebPushSubscriptionSchema:
    """Build a subscription with real encryption keys.

    Args:
        endpoint (str): The push endpoint.

    Returns:
        WebPushSubscriptionSchema: A subscription pywebpush can encrypt for.
    """
    point = (
        ec.generate_private_key(ec.SECP256R1())
        .public_key()
        .public_bytes(
            serialization.Encoding.X962,
            serialization.PublicFormat.UncompressedPoint,
        )
    )
    return WebPushSubscriptionSchema(
        endpoint=endpoint,
        keys=WebPushKeysSchema(p256dh=_b64url(point), auth=_b64url(b"0" * 16)),
    )


def _fixed_resolver(*addresses: str) -> Any:
    """Build a resolver answering ``addresses`` for every name.

    Args:
        *addresses (str): The addresses to answer.

    Returns:
        Any: An async resolver.
    """

    async def resolver(host: str) -> list[str]:
        """Answer the fixed addresses.

        Args:
            host (str): The hostname being resolved.

        Returns:
            list[str]: The configured addresses.
        """
        return list(addresses)

    return resolver


class _Recorder:
    """Stand-in for ``pywebpush.webpush`` that records each call.

    Attributes:
        calls (list[dict[str, Any]]): Keyword arguments of every call.
    """

    def __init__(self) -> None:
        """Start with no calls recorded."""
        self.calls: list[dict[str, Any]] = []

    def __call__(self, **kwargs: Any) -> None:
        """Record the call.

        Args:
            **kwargs (Any): The arguments pywebpush would receive.
        """
        self.calls.append(kwargs)


@pytest.fixture
def recorder(monkeypatch: pytest.MonkeyPatch) -> _Recorder:
    """Replace ``pywebpush.webpush`` with a recorder.

    Args:
        monkeypatch (pytest.MonkeyPatch): Swaps the function.

    Returns:
        _Recorder: The recorder installed.
    """
    fake = _Recorder()
    monkeypatch.setattr(pywebpush, "webpush", fake)
    return fake


class TestNonPublicEndpointsAreRefused:
    """Internal destinations never reach ``pywebpush``."""

    @pytest.mark.asyncio
    @pytest.mark.parametrize(
        "endpoint",
        [
            "https://169.254.169.254/latest/meta-data/",
            "https://10.1.2.3/x",
            "https://[::1]/",
            "https://127.0.0.1:6443/",
            "https://[::ffff:127.0.0.1]/",
        ],
    )
    async def test_ip_literal(self, recorder: _Recorder, endpoint: str) -> None:
        dispatcher = WebPushDispatcher(
            "priv",
            vapid_subject="mailto:ops@x.com",
            resolver=_fixed_resolver("93.184.216.34"),
        )
        with pytest.raises(WebPushError, match="non-public"):
            await dispatcher.send(_subscription(endpoint), "p")
        assert recorder.calls == []

    @pytest.mark.asyncio
    @pytest.mark.parametrize(
        "answer",
        [("127.0.0.1",), ("93.184.216.34", "10.0.0.1"), ()],
    )
    async def test_hostname_resolving_to_a_private_address(
        self, recorder: _Recorder, answer: Sequence[str]
    ) -> None:
        dispatcher = WebPushDispatcher(
            "priv",
            vapid_subject="mailto:ops@x.com",
            resolver=_fixed_resolver(*answer),
        )
        with pytest.raises(WebPushError, match="non-public"):
            await dispatcher.send(_subscription("https://localhost:6443/"), "p")
        assert recorder.calls == []

    @pytest.mark.asyncio
    async def test_resolution_failure_is_a_delivery_error(
        self, recorder: _Recorder
    ) -> None:
        async def failing(host: str) -> list[str]:
            raise OSError("no such host")

        dispatcher = WebPushDispatcher(
            "priv", vapid_subject="mailto:ops@x.com", resolver=failing
        )
        with pytest.raises(WebPushError, match="resolve"):
            await dispatcher.send(_subscription("https://push.example/x"), "p")
        assert recorder.calls == []

    @pytest.mark.asyncio
    async def test_send_many_prunes_an_internal_ip_literal_endpoint(
        self, recorder: _Recorder
    ) -> None:
        """A non-public IP literal can never be delivered, so it is pruned."""
        dispatcher = WebPushDispatcher(
            "priv",
            vapid_subject="mailto:ops@x.com",
            resolver=_fixed_resolver("93.184.216.34"),
        )
        gone = await dispatcher.send_many(
            [_subscription("https://10.0.0.1/a"), _subscription("https://ok.test/b")],
            "p",
        )
        assert gone == ["https://10.0.0.1/a"]
        assert [c["subscription_info"]["endpoint"] for c in recorder.calls] == [
            "https://ok.test/b"
        ]

    @pytest.mark.asyncio
    async def test_send_many_keeps_an_endpoint_refused_by_a_dns_answer(
        self, recorder: _Recorder
    ) -> None:
        """A DNS answer can change, so that refusal is logged, not pruned."""
        dispatcher = WebPushDispatcher(
            "priv",
            vapid_subject="mailto:ops@x.com",
            resolver=_fixed_resolver("10.0.0.1"),
        )
        gone = await dispatcher.send_many([_subscription("https://push.test/a")], "p")
        assert gone == []
        assert recorder.calls == []

    @pytest.mark.asyncio
    async def test_repeated_dns_refusals_are_pruned_on_the_threshold(
        self, recorder: _Recorder
    ) -> None:
        """A host that keeps resolving inside the network is pruned at last."""
        dispatcher = WebPushDispatcher(
            "priv",
            vapid_subject="mailto:ops@x.com",
            resolver=_fixed_resolver("10.0.0.1"),
        )
        rebinding = [_subscription("https://push.test/a")]
        assert await dispatcher.send_many(rebinding, "p") == []
        assert await dispatcher.send_many(rebinding, "p") == []
        assert await dispatcher.send_many(rebinding, "p") == ["https://push.test/a"]
        assert recorder.calls == []

    @pytest.mark.asyncio
    async def test_a_delivery_resets_the_dns_refusal_count(
        self, recorder: _Recorder
    ) -> None:
        """Only consecutive refusals count; one good answer starts over."""
        answers = ["10.0.0.1", "10.0.0.1", "93.184.216.34", "10.0.0.1", "10.0.0.1"]

        async def _changing(host: str) -> list[str]:
            """Answer the next address in ``answers``.

            Args:
                host (str): Ignored.

            Returns:
                list[str]: One address.
            """
            return [answers.pop(0)]

        dispatcher = WebPushDispatcher(
            "priv", vapid_subject="mailto:ops@x.com", resolver=_changing
        )
        sub = [_subscription("https://push.test/a")]
        results = [await dispatcher.send_many(sub, "p") for _ in range(5)]
        assert results == [[], [], [], [], []]
        assert len(recorder.calls) == 1

    @pytest.mark.asyncio
    @pytest.mark.parametrize(
        "endpoint",
        [
            "https://fcm.googleapis.com/fcm/send/x",
            "https://updates.push.services.mozilla.com/wpush/v2/x",
            "https://web.push.apple.com/x",
            "https://wns2-par02p.notify.windows.com/w/?token=x",
        ],
    )
    async def test_a_browser_push_service_is_never_pruned_by_dns(
        self, recorder: _Recorder, endpoint: str
    ) -> None:
        """A resolver mapping a real push service inward is the deployment's bug."""
        dispatcher = WebPushDispatcher(
            "priv",
            vapid_subject="mailto:ops@x.com",
            resolver=_fixed_resolver("10.0.0.1"),
        )
        sub = [_subscription(endpoint)]
        assert [await dispatcher.send_many(sub, "p") for _ in range(5)] == [[]] * 5

    @pytest.mark.asyncio
    async def test_none_keeps_dns_refusals_forever(self, recorder: _Recorder) -> None:
        dispatcher = WebPushDispatcher(
            "priv",
            vapid_subject="mailto:ops@x.com",
            resolver=_fixed_resolver("10.0.0.1"),
            prune_after_dns_refusals=None,
        )
        sub = [_subscription("https://push.test/a")]
        assert [await dispatcher.send_many(sub, "p") for _ in range(5)] == [[]] * 5

    def test_the_tracked_endpoints_are_bounded(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setattr(dispatcher_module, "MAX_TRACKED_DNS_REFUSALS", 2)
        dispatcher = WebPushDispatcher("priv", vapid_subject="mailto:ops@x.com")
        for name in ("a", "b", "c"):
            dispatcher._count_dns_refusal(f"https://push.test/{name}")
        assert list(dispatcher._dns_refusals) == [
            "https://push.test/b",
            "https://push.test/c",
        ]

    def test_threshold_below_one_is_refused(self) -> None:
        with pytest.raises(ValueError, match="at least 1"):
            WebPushDispatcher(
                "priv", vapid_subject="mailto:ops@x.com", prune_after_dns_refusals=0
            )

    @pytest.mark.asyncio
    async def test_refusals_say_whether_they_are_permanent(self) -> None:
        """Literal and scheme refusals are permanent; DNS refusals are not."""
        dispatcher = WebPushDispatcher(
            "priv",
            vapid_subject="mailto:ops@x.com",
            resolver=_fixed_resolver("10.0.0.1"),
        )
        with pytest.raises(WebPushEndpointRefusedError) as literal:
            await dispatcher.check_endpoint("https://127.0.0.1/x")
        assert literal.value.permanent is True
        with pytest.raises(WebPushEndpointRefusedError) as answer:
            await dispatcher.check_endpoint("https://push.test/x")
        assert answer.value.permanent is False

    @pytest.mark.asyncio
    async def test_allow_private_networks_opts_out(self, recorder: _Recorder) -> None:
        dispatcher = WebPushDispatcher(
            "priv",
            vapid_subject="mailto:ops@x.com",
            allow_private_networks=True,
        )
        await dispatcher.send(_subscription("https://10.1.2.3/x"), "p")
        assert len(recorder.calls) == 1


class TestDispatchArguments:
    """Every dispatch carries a timeout and a redirect-refusing session."""

    @pytest.mark.asyncio
    async def test_default_timeout_is_forwarded(self, recorder: _Recorder) -> None:
        dispatcher = WebPushDispatcher(
            "priv",
            vapid_subject="mailto:ops@x.com",
            resolver=_fixed_resolver("93.184.216.34"),
        )
        await dispatcher.send(_subscription("https://push.example/x"), "p")
        assert recorder.calls[0]["timeout"] == DEFAULT_WEBPUSH_TIMEOUT
        assert recorder.calls[0]["requests_session"] is not None

    @pytest.mark.asyncio
    async def test_custom_timeout_is_forwarded(self, recorder: _Recorder) -> None:
        dispatcher = WebPushDispatcher(
            "priv",
            vapid_subject="mailto:ops@x.com",
            timeout=2.5,
            resolver=_fixed_resolver("93.184.216.34"),
        )
        await dispatcher.send(_subscription("https://push.example/x"), "p")
        assert recorder.calls[0]["timeout"] == 2.5


@dataclass(slots=True)
class TLSAuthority:
    """A throwaway CA, written to disk for ``REQUESTS_CA_BUNDLE``.

    Attributes:
        key (ec.EllipticCurvePrivateKey): The CA private key.
        cert (x509.Certificate): The self-signed CA certificate.
        bundle (Path): PEM file holding the CA certificate.
    """

    key: ec.EllipticCurvePrivateKey
    cert: x509.Certificate
    bundle: Path

    def server_context(self, name: str, directory: Path) -> ssl.SSLContext:
        """Issue a leaf for ``name`` and return a server context presenting it.

        Args:
            name (str): The DNS name the leaf certificate carries.
            directory (Path): Where the PEM files are written.

        Returns:
            ssl.SSLContext: A server context with the leaf loaded.
        """
        key = ec.generate_private_key(ec.SECP256R1())
        now = datetime.datetime.now(datetime.UTC)
        cert = (
            x509.CertificateBuilder()
            .subject_name(x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, name)]))
            .issuer_name(self.cert.subject)
            .public_key(key.public_key())
            .serial_number(x509.random_serial_number())
            .not_valid_before(now - datetime.timedelta(minutes=5))
            .not_valid_after(now + datetime.timedelta(hours=1))
            .add_extension(
                x509.SubjectAlternativeName([x509.DNSName(name)]), critical=False
            )
            .add_extension(
                x509.AuthorityKeyIdentifier.from_issuer_public_key(
                    self.key.public_key()
                ),
                critical=False,
            )
            .sign(self.key, hashes.SHA256())
        )
        cert_path = directory / f"{name}.pem"
        key_path = directory / f"{name}.key"
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


@pytest.fixture
def authority(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> TLSAuthority:
    """Create a CA that ``requests`` trusts for the duration of a test.

    Args:
        tmp_path (Path): Where the bundle is written.
        monkeypatch (pytest.MonkeyPatch): Sets ``REQUESTS_CA_BUNDLE``.

    Returns:
        TLSAuthority: The CA.
    """
    key = ec.generate_private_key(ec.SECP256R1())
    name = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, "webpush test CA")])
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
        .add_extension(
            x509.KeyUsage(
                digital_signature=True,
                content_commitment=False,
                key_encipherment=False,
                data_encipherment=False,
                key_agreement=False,
                key_cert_sign=True,
                crl_sign=True,
                encipher_only=False,
                decipher_only=False,
            ),
            critical=True,
        )
        .add_extension(
            x509.SubjectKeyIdentifier.from_public_key(key.public_key()),
            critical=False,
        )
        .sign(key, hashes.SHA256())
    )
    bundle = tmp_path / "ca.pem"
    bundle.write_bytes(cert.public_bytes(serialization.Encoding.PEM))
    monkeypatch.setenv("REQUESTS_CA_BUNDLE", str(bundle))
    for variable in ("HTTPS_PROXY", "https_proxy", "ALL_PROXY", "all_proxy"):
        monkeypatch.delenv(variable, raising=False)
    return TLSAuthority(key=key, cert=cert, bundle=bundle)


@dataclass(slots=True)
class LoopbackServer:
    """Loopback HTTP(S) server that records requests and replays one answer.

    Attributes:
        response (bytes): The raw HTTP response sent to every request.
        port (int): The port the server listens on.
        requests (list[tuple[str, str, str]]): ``(method, path, host)`` of
            every request served.
        sni (list[str | None]): SNI name of every TLS handshake.
    """

    response: bytes
    port: int = 0
    requests: list[tuple[str, str, str]] = field(default_factory=list)
    sni: list[str | None] = field(default_factory=list)

    async def handle(
        self, reader: asyncio.StreamReader, writer: asyncio.StreamWriter
    ) -> None:
        """Read one request, record it and send the canned response.

        Args:
            reader (asyncio.StreamReader): The request stream.
            writer (asyncio.StreamWriter): The response stream.
        """
        try:
            head = (await reader.readuntil(b"\r\n\r\n")).decode("latin-1")
            lines = head.split("\r\n")
            method, path, _version = lines[0].split(" ", 2)
            host = ""
            length = 0
            for line in lines[1:]:
                lowered = line.lower()
                if lowered.startswith("host:"):
                    host = line.split(":", 1)[1].strip()
                if lowered.startswith("content-length:"):
                    length = int(line.split(":", 1)[1])
            if length:
                await reader.readexactly(length)
            self.requests.append((method, path, host))
            writer.write(self.response)
            await writer.drain()
        except (asyncio.IncompleteReadError, ConnectionError, ssl.SSLError):
            pass
        finally:
            writer.close()


async def _serve(
    state: LoopbackServer, context: ssl.SSLContext | None
) -> asyncio.Server:
    """Start ``state`` on loopback.

    Args:
        state (LoopbackServer): The server state.
        context (ssl.SSLContext | None): TLS context, or ``None`` for HTTP.

    Returns:
        asyncio.Server: The running server.
    """
    if context is not None:

        def record_sni(
            _sock: ssl.SSLObject, name: str | None, _ctx: ssl.SSLContext
        ) -> None:
            """Record the SNI name the client sent.

            Args:
                _sock (ssl.SSLObject): The connection being handshaken.
                name (str | None): The SNI name.
                _ctx (ssl.SSLContext): The server context.
            """
            state.sni.append(name)

        context.sni_callback = record_sni
    server = await asyncio.start_server(state.handle, LOOPBACK, 0, ssl=context)
    state.port = server.sockets[0].getsockname()[1]
    return server


@pytest.fixture
def loopback_is_public(monkeypatch: pytest.MonkeyPatch) -> None:
    """Let loopback stand in for a public push host.

    The resolution, the pinned connection and the redirect handling are the
    production path; only the address predicate is relaxed.

    Args:
        monkeypatch (pytest.MonkeyPatch): Patches the predicate.
    """
    monkeypatch.setattr(dispatcher_module, "is_public_address", lambda _a: True)


@pytest.fixture
async def internal_server() -> AsyncIterator[LoopbackServer]:
    """Plain-HTTP loopback server standing in for an internal service.

    Yields:
        LoopbackServer: The running server.
    """
    state = LoopbackServer(
        response=b"HTTP/1.1 200 OK\r\nContent-Length: 6\r\n\r\nsecret"
    )
    server = await _serve(state, None)
    yield state
    server.close()
    await server.wait_closed()


@pytest.mark.usefixtures("loopback_is_public")
class TestRealDispatch:
    """The real pywebpush/requests stack against loopback servers."""

    @pytest.mark.asyncio
    @pytest.mark.parametrize("status", [b"302 Found", b"307 Temporary Redirect"])
    async def test_redirect_is_not_followed(
        self,
        status: bytes,
        authority: TLSAuthority,
        internal_server: LoopbackServer,
        tmp_path: Path,
    ) -> None:
        """A ``3xx`` is a failed delivery; ``Location`` is never requested.

        The exception chain keeps the ``requests`` response, whose socket
        stays open until it is collected, so the test drops it and runs the
        collector before closing the TLS server (which otherwise waits out
        its 30 s shutdown timeout).
        """
        location = f"http://{LOOPBACK}:{internal_server.port}/internal/secret"
        push = LoopbackServer(
            response=b"HTTP/1.1 "
            + status
            + b"\r\nLocation: "
            + location.encode("ascii")
            + b"\r\nContent-Length: 0\r\n\r\n"
        )
        server = await _serve(push, authority.server_context(PUSH_HOST, tmp_path))
        try:
            dispatcher = WebPushDispatcher(
                _vapid_private_key(),
                vapid_subject="mailto:ops@x.com",
                resolver=_fixed_resolver(LOOPBACK),
            )
            endpoint = f"https://{PUSH_HOST}:{push.port}/push/1"
            with pytest.raises(WebPushError) as info:
                await dispatcher.send(_subscription(endpoint), "p")
            status_code = info.value.status_code
            del info
            gc.collect()
        finally:
            server.close()
            await server.wait_closed()
        assert status_code == int(status.split(b" ")[0])
        assert internal_server.requests == []
        assert push.requests == [("POST", "/push/1", f"{PUSH_HOST}:{push.port}")]

    @pytest.mark.asyncio
    async def test_connection_goes_to_the_validated_address_with_sni(
        self, authority: TLSAuthority, tmp_path: Path
    ) -> None:
        push = LoopbackServer(
            response=b"HTTP/1.1 201 Created\r\nContent-Length: 0\r\n\r\n"
        )
        server = await _serve(push, authority.server_context(PUSH_HOST, tmp_path))
        lookups: list[str] = []

        async def resolver(host: str) -> list[str]:
            lookups.append(host)
            return [LOOPBACK]

        try:
            dispatcher = WebPushDispatcher(
                _vapid_private_key(),
                vapid_subject="mailto:ops@x.com",
                resolver=resolver,
            )
            endpoint = f"https://{PUSH_HOST}:{push.port}/push/1"
            await dispatcher.send(_subscription(endpoint), "p")
        finally:
            server.close()
            await server.wait_closed()
        assert lookups == [PUSH_HOST]
        assert push.sni == [PUSH_HOST]
        assert push.requests == [("POST", "/push/1", f"{PUSH_HOST}:{push.port}")]

    @pytest.mark.asyncio
    async def test_certificate_for_another_name_is_refused(
        self, authority: TLSAuthority, tmp_path: Path
    ) -> None:
        push = LoopbackServer(
            response=b"HTTP/1.1 201 Created\r\nContent-Length: 0\r\n\r\n"
        )
        server = await _serve(push, authority.server_context("other.test", tmp_path))
        try:
            dispatcher = WebPushDispatcher(
                _vapid_private_key(),
                vapid_subject="mailto:ops@x.com",
                resolver=_fixed_resolver(LOOPBACK),
            )
            endpoint = f"https://{PUSH_HOST}:{push.port}/push/1"
            with pytest.raises(WebPushError, match="SSLError"):
                await dispatcher.send(_subscription(endpoint), "p")
        finally:
            server.close()
            await server.wait_closed()
        assert push.requests == []

    @pytest.mark.asyncio
    async def test_stalled_endpoint_times_out(self) -> None:
        accepted = asyncio.Event()
        release = asyncio.Event()

        async def stall(
            reader: asyncio.StreamReader, writer: asyncio.StreamWriter
        ) -> None:
            accepted.set()
            await release.wait()
            writer.close()

        server = await asyncio.start_server(stall, LOOPBACK, 0)
        port = server.sockets[0].getsockname()[1]
        try:
            dispatcher = WebPushDispatcher(
                _vapid_private_key(),
                vapid_subject="mailto:ops@x.com",
                timeout=0.5,
                resolver=_fixed_resolver(LOOPBACK),
            )
            endpoint = f"https://{PUSH_HOST}:{port}/push/1"
            with pytest.raises(WebPushError):
                await asyncio.wait_for(
                    dispatcher.send(_subscription(endpoint), "p"), timeout=10
                )
        finally:
            release.set()
            server.close()
            await server.wait_closed()
        assert accepted.is_set()
