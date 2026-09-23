"""Tests for ``OIDCProvider(introspection_url=...)`` — RFC 7662 introspection.

The answers each endpoint gives here were measured against Keycloak 26.3:
``GET ?access_token=`` answers ``405``, a garbage token ``200
{"active": false}``, a wrong client secret ``401``, and a live token
``active: true`` with ``aud: "account"`` and ``azp`` naming the client.
"""

from __future__ import annotations

from collections.abc import Callable
from typing import Any
from urllib.parse import parse_qs

import httpx
import pytest

from tempest_fastapi_sdk import (
    HTTPClient,
    OAuthError,
    OAuthProviderUnavailableException,
    OAuthTokenAudienceMismatchException,
    OAuthTokenRejectedException,
    OAuthTokens,
    OIDCProvider,
    RetryPolicy,
)

CLIENT_ID: str = "api"
CLIENT_SECRET: str = "s3cret"
BASE: str = "https://id.example/realms/app/protocol/openid-connect"
INTROSPECT: str = f"{BASE}/token/introspect"
LIVE: dict[str, Any] = {
    "active": True,
    "aud": "account",
    "azp": CLIENT_ID,
    "sub": "user-1",
}

Handler = Callable[[httpx.Request], httpx.Response]


def _provider(handler: Handler, **kwargs: Any) -> OIDCProvider:
    """Build a provider whose HTTP calls land on ``handler``.

    Args:
        handler (Handler): Answers every request.
        **kwargs (Any): Forwarded to :class:`OIDCProvider`.

    Returns:
        OIDCProvider: The provider under test.
    """
    http = HTTPClient(
        failure_threshold=0,
        retry_policy=RetryPolicy(max_attempts=1),
        transport=httpx.MockTransport(handler),
    )
    options: dict[str, Any] = {"introspection_url": INTROSPECT, **kwargs}
    return OIDCProvider(
        client_id=CLIENT_ID,
        client_secret=CLIENT_SECRET,
        redirect_uri="https://app.example/cb",
        authorize_url=f"{BASE}/auth",
        token_url=f"{BASE}/token",
        http_client=http,
        **options,
    )


def _answer(status: int, body: Any) -> Handler:
    """Build a handler that answers every request the same way.

    Args:
        status (int): The status code.
        body (Any): The JSON body.

    Returns:
        Handler: The handler.
    """

    def handle(request: httpx.Request) -> httpx.Response:
        return httpx.Response(status, json=body)

    return handle


async def _verify(provider: OIDCProvider, token: str = "live-token") -> None:
    """Run the audience check on ``token``.

    Args:
        provider (OIDCProvider): The provider under test.
        token (str): The presented access token.
    """
    await provider.verify_token_audience(
        OAuthTokens(access_token=token, token_type="Bearer"),
    )


class TestTheRequestIsRfc7662:
    """The call is a ``POST`` that authenticates the client."""

    @pytest.mark.asyncio
    async def test_posts_the_token_with_client_credentials(self) -> None:
        seen: list[httpx.Request] = []

        def handle(request: httpx.Request) -> httpx.Response:
            seen.append(request)
            return httpx.Response(200, json=LIVE)

        await _verify(_provider(handle))

        assert len(seen) == 1
        request = seen[0]
        assert request.method == "POST"
        assert str(request.url) == INTROSPECT
        form = parse_qs(request.content.decode())
        assert form == {
            "token": ["live-token"],
            "token_type_hint": ["access_token"],
            "client_id": [CLIENT_ID],
            "client_secret": [CLIENT_SECRET],
        }


class TestALiveTokenIsAccepted:
    """Keycloak's ``aud: "account"`` passes on ``azp``."""

    @pytest.mark.asyncio
    async def test_live_token_for_our_client(self) -> None:
        await _verify(_provider(_answer(200, LIVE)))

    @pytest.mark.asyncio
    async def test_extra_audience_is_accepted(self) -> None:
        body = {**LIVE, "azp": "mobile-app"}
        await _verify(_provider(_answer(200, body), extra_audiences=["mobile-app"]))


class TestAnInactiveTokenIsTheCallersFault:
    """``active`` other than ``true`` answers 401."""

    @pytest.mark.asyncio
    @pytest.mark.parametrize(
        "body",
        [{"active": False}, {}, {"active": "true"}, {"active": 1}],
    )
    async def test_refused(self, body: dict[str, Any]) -> None:
        with pytest.raises(OAuthTokenRejectedException) as caught:
            await _verify(_provider(_answer(200, body)))
        assert caught.value.status_code == 401
        assert caught.value.details["reason"] == "inactive"


class TestAnotherClientsTokenIsAnAudienceMismatch:
    """A live token minted for another client is refused."""

    @pytest.mark.asyncio
    async def test_foreign_azp(self) -> None:
        body = {**LIVE, "azp": "other", "client_id": "other"}
        with pytest.raises(OAuthTokenAudienceMismatchException):
            await _verify(_provider(_answer(200, body)))


class TestAnUnreachableEndpointIsARetryable502:
    """Failures that retrying later can fix answer 502 unavailable."""

    @pytest.mark.asyncio
    async def test_transport_error(self) -> None:
        def handle(request: httpx.Request) -> httpx.Response:
            raise httpx.ConnectError("refused", request=request)

        with pytest.raises(OAuthProviderUnavailableException) as caught:
            await _verify(_provider(handle))
        assert caught.value.status_code == 502
        assert caught.value.details["reason"] == "unreachable"

    @pytest.mark.asyncio
    async def test_server_error(self) -> None:
        with pytest.raises(OAuthProviderUnavailableException) as caught:
            await _verify(_provider(_answer(503, {"error": "down"})))
        assert caught.value.details["reason"] == "status 503"

    @pytest.mark.asyncio
    async def test_body_that_is_not_json(self) -> None:
        def handle(request: httpx.Request) -> httpx.Response:
            return httpx.Response(200, text="<html>login</html>")

        with pytest.raises(OAuthProviderUnavailableException) as caught:
            await _verify(_provider(handle))
        assert caught.value.details["reason"] == "not json"

    @pytest.mark.asyncio
    async def test_json_that_is_not_an_object(self) -> None:
        with pytest.raises(OAuthProviderUnavailableException):
            await _verify(_provider(_answer(200, [LIVE])))


class TestRefusedClientCredentialsAreOurFault:
    """A 4xx from introspection is our configuration, not the caller's token."""

    @pytest.mark.asyncio
    @pytest.mark.parametrize("status", [400, 401, 403])
    async def test_answers_oauth_error_not_401(self, status: int) -> None:
        body = {
            "error": "invalid_request",
            "error_description": "Authentication failed.",
        }
        with pytest.raises(OAuthError) as caught:
            await _verify(_provider(_answer(status, body)))
        assert type(caught.value) is OAuthError
        assert caught.value.status_code == 502
        assert caught.value.details["status"] == status


class TestConfiguration:
    """How the two audience endpoints combine."""

    def test_both_urls_are_refused(self) -> None:
        with pytest.raises(ValueError, match="not both"):
            _provider(_answer(200, LIVE), tokeninfo_url=f"{BASE}/tokeninfo")

    @pytest.mark.asyncio
    async def test_tokeninfo_url_keeps_the_get_shape(self) -> None:
        seen: list[httpx.Request] = []

        def handle(request: httpx.Request) -> httpx.Response:
            seen.append(request)
            return httpx.Response(200, json=LIVE)

        provider = _provider(
            handle,
            introspection_url=None,
            tokeninfo_url=f"{BASE}/tokeninfo",
        )
        await _verify(provider)
        assert seen[0].method == "GET"
        assert seen[0].url.params["access_token"] == "live-token"

    def test_the_url_is_exposed(self) -> None:
        assert _provider(_answer(200, LIVE)).introspection_url == INTROSPECT
