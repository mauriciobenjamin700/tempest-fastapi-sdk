"""Tests for tempest_fastapi_sdk.utils.client_ip."""

from starlette.requests import Request

from tempest_fastapi_sdk import get_client_ip, get_client_ip_from_scope


def _scope(
    headers: dict[str, str] | None = None,
    client: tuple[str, int] | None = ("1.2.3.4", 5000),
) -> dict:
    """Build a minimal ASGI HTTP scope."""
    return {
        "type": "http",
        "headers": [
            (k.lower().encode("latin-1"), v.encode("latin-1"))
            for k, v in (headers or {}).items()
        ],
        "client": client,
    }


class TestGetClientIP:
    def test_uses_transport_peer_without_trusted_header(self) -> None:
        request = Request(_scope())
        assert get_client_ip(request) == "1.2.3.4"

    def test_prefers_trusted_header_when_set(self) -> None:
        request = Request(_scope(headers={"x-real-ip": "9.9.9.9"}))
        assert get_client_ip(request, trusted_header="x-real-ip") == "9.9.9.9"

    def test_ignores_untrusted_xff(self) -> None:
        """XFF is never consulted — spoof attempt falls back to peer."""
        request = Request(_scope(headers={"x-forwarded-for": "6.6.6.6"}))
        assert get_client_ip(request, trusted_header="x-real-ip") == "1.2.3.4"

    def test_falls_back_to_peer_when_trusted_header_absent(self) -> None:
        request = Request(_scope())
        assert get_client_ip(request, trusted_header="x-real-ip") == "1.2.3.4"

    def test_unknown_when_no_peer_and_no_header(self) -> None:
        request = Request(_scope(client=None))
        assert get_client_ip(request) == "unknown"

    def test_header_value_is_stripped(self) -> None:
        request = Request(_scope(headers={"x-real-ip": "  9.9.9.9  "}))
        assert get_client_ip(request, trusted_header="x-real-ip") == "9.9.9.9"


class TestGetClientIPFromScope:
    def test_uses_transport_peer(self) -> None:
        assert get_client_ip_from_scope(_scope()) == "1.2.3.4"

    def test_prefers_trusted_header(self) -> None:
        scope = _scope(headers={"x-real-ip": "9.9.9.9"})
        assert (
            get_client_ip_from_scope(scope, trusted_header="x-real-ip")
            == "9.9.9.9"
        )

    def test_ignores_untrusted_header(self) -> None:
        scope = _scope(headers={"x-forwarded-for": "6.6.6.6"})
        assert (
            get_client_ip_from_scope(scope, trusted_header="x-real-ip")
            == "1.2.3.4"
        )

    def test_unknown_when_unavailable(self) -> None:
        assert get_client_ip_from_scope(_scope(client=None)) == "unknown"
