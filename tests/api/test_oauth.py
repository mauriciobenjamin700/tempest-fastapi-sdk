"""Tests for the OAuth2/OIDC provider clients."""

from __future__ import annotations

import httpx
import pytest

from tempest_fastapi_sdk import (
    GitHubOAuthClient,
    GoogleOAuthClient,
    HTTPClient,
    OAuthError,
    OIDCProvider,
    generate_oauth_state,
)


def _client_with_handler(
    factory: type[GoogleOAuthClient | GitHubOAuthClient],
    handler: object,
    **kwargs: str,
) -> GoogleOAuthClient | GitHubOAuthClient:
    """Build a provider client backed by an httpx MockTransport."""
    http_client = HTTPClient(failure_threshold=0)
    http_client._client = httpx.AsyncClient(
        transport=httpx.MockTransport(handler),  # type: ignore[arg-type]
    )
    return factory(
        client_id=kwargs.get("client_id", "id"),
        client_secret=kwargs.get("client_secret", "secret"),
        redirect_uri=kwargs.get("redirect_uri", "https://app/cb"),
        http_client=http_client,
    )


class TestState:
    def test_state_unique(self) -> None:
        assert generate_oauth_state() != generate_oauth_state()


class TestAuthorizeUrl:
    def test_google_authorize_url_has_required_params(self) -> None:
        client = GoogleOAuthClient(
            client_id="cid",
            client_secret="sec",
            redirect_uri="https://app/cb",
        )
        url = client.build_authorize_url(state="st_xyz")
        assert "client_id=cid" in url
        assert "redirect_uri=https%3A%2F%2Fapp%2Fcb" in url
        assert "response_type=code" in url
        assert "scope=openid+email+profile" in url
        assert "state=st_xyz" in url

    def test_github_default_scopes(self) -> None:
        client = GitHubOAuthClient(
            client_id="cid",
            client_secret="sec",
            redirect_uri="https://app/cb",
        )
        url = client.build_authorize_url(state="st")
        assert "scope=read%3Auser+user%3Aemail" in url

    def test_authorize_url_passes_extra_params(self) -> None:
        client = GoogleOAuthClient(
            client_id="cid",
            client_secret="sec",
            redirect_uri="https://app/cb",
        )
        url = client.build_authorize_url(
            state="st",
            access_type="offline",
            prompt="consent",
        )
        assert "access_type=offline" in url
        assert "prompt=consent" in url


class TestExchangeCode:
    async def test_successful_exchange(self) -> None:
        def handler(request: httpx.Request) -> httpx.Response:
            assert "code=AUTHCODE" in request.content.decode()
            return httpx.Response(
                200,
                json={
                    "access_token": "ya29.token",
                    "token_type": "Bearer",
                    "expires_in": 3600,
                    "id_token": "eyJ...",
                    "scope": "openid email profile",
                },
            )

        client = _client_with_handler(GoogleOAuthClient, handler)
        try:
            tokens = await client.exchange_code("AUTHCODE")
            assert tokens.access_token == "ya29.token"
            assert tokens.token_type == "Bearer"
            assert tokens.expires_in == 3600
            assert tokens.id_token == "eyJ..."
        finally:
            await client.aclose()

    async def test_failed_exchange_raises(self) -> None:
        def handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(400, json={"error": "invalid_grant"})

        client = _client_with_handler(GoogleOAuthClient, handler)
        try:
            with pytest.raises(OAuthError):
                await client.exchange_code("bad")
        finally:
            await client.aclose()


class TestFetchUser:
    async def test_google_user_parsing(self) -> None:
        captured: dict[str, str] = {}

        def handler(request: httpx.Request) -> httpx.Response:
            captured["auth"] = request.headers.get("authorization", "")
            return httpx.Response(
                200,
                json={
                    "sub": "1234567890",
                    "email": "ana@example.com",
                    "name": "Ana Souza",
                    "picture": "https://lh3.googleusercontent.com/...",
                },
            )

        client = _client_with_handler(GoogleOAuthClient, handler)
        from tempest_fastapi_sdk import OAuthTokens

        try:
            user = await client.fetch_user(
                OAuthTokens(access_token="token", token_type="Bearer"),
            )
            assert user.provider == "google"
            assert user.subject == "1234567890"
            assert user.email == "ana@example.com"
            assert user.name == "Ana Souza"
            assert captured["auth"] == "Bearer token"
        finally:
            await client.aclose()

    async def test_github_user_parsing(self) -> None:
        def handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(
                200,
                json={
                    "id": 42,
                    "login": "anasouza",
                    "name": "Ana Souza",
                    "email": "ana@example.com",
                    "avatar_url": "https://avatars.githubusercontent.com/u/42",
                },
            )

        client = _client_with_handler(GitHubOAuthClient, handler)
        from tempest_fastapi_sdk import OAuthTokens

        try:
            user = await client.fetch_user(
                OAuthTokens(access_token="gho_x", token_type="Bearer"),
            )
            assert user.provider == "github"
            assert user.subject == "42"
            assert user.name == "Ana Souza"
            assert user.picture is not None
        finally:
            await client.aclose()


class TestOIDCProvider:
    async def test_oidc_provider_round_trip(self) -> None:
        def handler(request: httpx.Request) -> httpx.Response:
            if request.url.path.endswith("/token"):
                return httpx.Response(
                    200,
                    json={"access_token": "abc", "token_type": "Bearer"},
                )
            return httpx.Response(
                200,
                json={
                    "sub": "user-42",
                    "email": "ana@auth0",
                    "name": "Ana",
                },
            )

        http_client = HTTPClient(failure_threshold=0)
        http_client._client = httpx.AsyncClient(
            transport=httpx.MockTransport(handler),
        )
        provider = OIDCProvider(
            client_id="cid",
            client_secret="sec",
            redirect_uri="https://app/cb",
            authorize_url="https://idp.test/authorize",
            token_url="https://idp.test/token",
            userinfo_url="https://idp.test/userinfo",
            provider_name="oidc:auth0",
            http_client=http_client,
        )
        try:
            tokens = await provider.exchange_code("CODE")
            user = await provider.fetch_user(tokens)
            assert user.provider == "oidc:auth0"
            assert user.subject == "user-42"
        finally:
            await provider.aclose()
