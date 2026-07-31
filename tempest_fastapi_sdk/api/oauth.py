"""OAuth2 / OIDC client helpers for third-party social login.

Three concrete clients out of the box:

- ``GoogleOAuthClient`` — Google identity, uses the OIDC discovery
  document at ``https://accounts.google.com/.well-known/openid-configuration``.
- ``GitHubOAuthClient`` — GitHub OAuth (not full OIDC; user info is
  fetched from ``GET /user`` instead of an ``id_token``).
- ``OIDCProvider`` — generic discovery-driven OIDC client; works
  with any conformant IdP (Auth0, Keycloak, Okta, Microsoft Entra,
  Cognito).

The clients **only** cover the OAuth2 dance — generating an
authorize URL, exchanging the code for tokens, fetching the user.
Storing the user / minting your own session token / wiring an
``HttpOnly`` cookie are decisions left to the service.

Requires the ``[http]`` extra (uses ``HTTPClient`` under the hood).
"""

from __future__ import annotations

import secrets
from dataclasses import dataclass, field
from typing import Any
from urllib.parse import urlencode

from tempest_fastapi_sdk.exceptions.base import AppException
from tempest_fastapi_sdk.utils.http_client import HTTPClient


class OAuthError(AppException):
    """Raised when an OAuth exchange fails — wraps the IdP message."""

    code: str = "OAUTH_ERROR"
    status_code: int = 502


def _as_bool(value: Any) -> bool | None:
    """Normalize an OIDC boolean claim that may arrive as a string.

    Some IdPs serialize ``email_verified`` as ``"true"`` / ``"false"``
    rather than a JSON boolean. An unrecognized value returns ``None``
    (unknown) rather than ``False``, so a caller cannot mistake "the
    provider did not say" for "the provider said no".

    Args:
        value (Any): The raw claim value.

    Returns:
        bool | None: The parsed flag, or ``None`` when absent/unparseable.
    """
    if isinstance(value, bool):
        return value
    if isinstance(value, str):
        lowered = value.strip().lower()
        if lowered in {"true", "1", "yes"}:
            return True
        if lowered in {"false", "0", "no"}:
            return False
    return None


@dataclass(slots=True)
class OAuthUser:
    """Normalized user identity returned by every provider.

    Different IdPs use different field names (``sub`` vs ``id``,
    ``picture`` vs ``avatar_url``, ``name`` vs ``login``). This
    dataclass is the single shape the rest of the application sees.

    Attributes:
        provider (str): Provider key (``"google"``, ``"github"``,
            ``"oidc:auth0"`` …). Useful when multiple providers
            feed the same user table.
        subject (str): Stable per-provider user id. Combine with
            ``provider`` for a globally-unique key.
        email (str | None): The email the provider returned, if any.
            Some IdPs gate this behind extra scopes. **Not necessarily
            verified** — check ``email_verified`` before trusting it.
        email_verified (bool | None): Whether the provider states it has
            verified ``email``. ``None`` means the provider said nothing
            either way, which is not the same as ``True``.
        name (str | None): Human-readable display name.
        picture (str | None): Avatar / profile picture URL.
        raw (dict[str, Any]): Full provider payload for advanced
            cases (custom claims, role mappings).
    """

    provider: str
    subject: str
    email: str | None = None
    email_verified: bool | None = None
    name: str | None = None
    picture: str | None = None
    raw: dict[str, Any] = field(default_factory=dict)


@dataclass(slots=True)
class OAuthTokens:
    """Tokens returned by the IdP after the authorization-code exchange.

    Attributes:
        access_token (str): Bearer token to call provider APIs.
        token_type (str): Usually ``"Bearer"``.
        refresh_token (str | None): Refresh token when offline
            access was requested.
        id_token (str | None): OIDC id token (JWT). Present on
            OIDC flows, absent on plain OAuth2.
        expires_in (int | None): Lifetime of ``access_token`` in
            seconds.
        scope (str | None): Space-separated scopes granted.
        raw (dict[str, Any]): Full token-endpoint response.
    """

    access_token: str
    token_type: str
    refresh_token: str | None = None
    id_token: str | None = None
    expires_in: int | None = None
    scope: str | None = None
    raw: dict[str, Any] = field(default_factory=dict)


def generate_oauth_state(n_bytes: int = 32) -> str:
    """Mint a CSRF-grade ``state`` for the authorize URL.

    The state ties the inbound callback to the originating
    session — store it server-side (or in a signed cookie) before
    redirecting, then compare on callback. Any mismatch means a
    forged redirect; reject with 400.

    Args:
        n_bytes (int): Entropy bytes. Default 32.

    Returns:
        str: URL-safe random token.
    """
    return secrets.token_urlsafe(n_bytes)


class _BaseOAuthClient:
    """Shared scaffolding for every provider client.

    Subclasses fill in the four endpoints and the user-info
    parsing. Connection pooling is reused via a shared
    ``HTTPClient`` so callers don't pay TCP/TLS handshake per
    login.
    """

    provider_name: str = "oauth"

    def __init__(
        self,
        *,
        client_id: str,
        client_secret: str,
        redirect_uri: str,
        scopes: list[str] | None = None,
        http_client: HTTPClient | None = None,
    ) -> None:
        """Initialize.

        Args:
            client_id (str): App client id issued by the provider.
            client_secret (str): App client secret.
            redirect_uri (str): Callback URL registered with the
                provider; must match exactly.
            scopes (list[str] | None): Scopes to request. Provider
                subclasses ship sensible defaults.
            http_client (HTTPClient | None): Shared client to
                reuse. ``None`` builds a dedicated one with sane
                defaults.
        """
        self.client_id: str = client_id
        self.client_secret: str = client_secret
        self.redirect_uri: str = redirect_uri
        self.scopes: list[str] = scopes or self._default_scopes()
        self._http: HTTPClient = http_client or HTTPClient(
            timeout=10.0,
            failure_threshold=0,
        )
        self._owns_http: bool = http_client is None

    def _default_scopes(self) -> list[str]:
        """Provider-specific default scope list."""
        return []

    async def aclose(self) -> None:
        """Close the underlying HTTP client when we own it."""
        if self._owns_http:
            await self._http.aclose()

    # ------------------------------------------------------------------
    # Provider-specific hooks (override).
    # ------------------------------------------------------------------

    @property
    def authorize_url(self) -> str:
        """Provider's authorize endpoint.

        Returns:
            str: The configured endpoint URL.
        """
        raise NotImplementedError

    @property
    def token_url(self) -> str:
        """Provider's token-exchange endpoint.

        Returns:
            str: The configured endpoint URL.
        """
        raise NotImplementedError

    @property
    def userinfo_url(self) -> str | None:
        """Provider's user-info endpoint (``None`` for ID-token-only flows).

        Returns:
            str | None: The endpoint URL, or ``None`` when the provider
                advertises none.
        """
        return None

    def _parse_user(self, payload: dict[str, Any]) -> OAuthUser:
        """Map the provider's user payload to :class:`OAuthUser`."""
        raise NotImplementedError

    # ------------------------------------------------------------------
    # Public API.
    # ------------------------------------------------------------------

    def build_authorize_url(self, *, state: str, **extra: str) -> str:
        """Render the URL the browser must redirect to.

        Args:
            state (str): CSRF state — produced by
                :func:`generate_oauth_state` and saved server-side
                before the redirect.
            **extra (str): Extra params merged into the query (e.g.
                ``access_type="offline"``, ``prompt="consent"``).

        Returns:
            str: Fully-formed authorize URL.
        """
        params: dict[str, str] = {
            "client_id": self.client_id,
            "redirect_uri": self.redirect_uri,
            "response_type": "code",
            "scope": " ".join(self.scopes),
            "state": state,
            **extra,
        }
        return f"{self.authorize_url}?{urlencode(params)}"

    async def exchange_code(self, code: str) -> OAuthTokens:
        """Swap an authorization code for an access token.

        Args:
            code (str): The ``code`` query param from the callback.

        Returns:
            OAuthTokens: Parsed token bundle.

        Raises:
            OAuthError: When the provider rejects the exchange.
        """
        response = await self._http.post(
            self.token_url,
            data={
                "grant_type": "authorization_code",
                "code": code,
                "redirect_uri": self.redirect_uri,
                "client_id": self.client_id,
                "client_secret": self.client_secret,
            },
            headers={"Accept": "application/json"},
        )
        if response.status_code >= 400:
            raise OAuthError(
                message=f"token exchange failed ({response.status_code})",
                details={"body": response.text},
            )
        payload: dict[str, Any] = response.json()
        return OAuthTokens(
            access_token=payload["access_token"],
            token_type=payload.get("token_type", "Bearer"),
            refresh_token=payload.get("refresh_token"),
            id_token=payload.get("id_token"),
            expires_in=payload.get("expires_in"),
            scope=payload.get("scope"),
            raw=payload,
        )

    async def fetch_user(self, tokens: OAuthTokens) -> OAuthUser:
        """Resolve the access token to a normalized :class:`OAuthUser`.

        Args:
            tokens (OAuthTokens): Bundle returned by
                :meth:`exchange_code`.

        Returns:
            OAuthUser: Normalized identity.

        Raises:
            OAuthError: When the userinfo endpoint rejects the
                token or returns malformed data.
        """
        url = self.userinfo_url
        if url is None:
            raise NotImplementedError(
                f"{self.provider_name}: userinfo endpoint not configured. "
                f"Override _parse_user to read claims from the id_token."
            )
        response = await self._http.get(
            url,
            headers={
                "Authorization": f"{tokens.token_type} {tokens.access_token}",
                "Accept": "application/json",
            },
        )
        if response.status_code >= 400:
            raise OAuthError(
                message=f"userinfo failed ({response.status_code})",
                details={"body": response.text},
            )
        return self._parse_user(response.json())


class GoogleOAuthClient(_BaseOAuthClient):
    """Google identity client (OIDC-compatible).

    Default scopes: ``openid email profile``.
    """

    provider_name: str = "google"

    @property
    def authorize_url(self) -> str:
        """Google's authorize endpoint.

        Returns:
            str: The configured endpoint URL.
        """
        return "https://accounts.google.com/o/oauth2/v2/auth"

    @property
    def token_url(self) -> str:
        """Google's token endpoint.

        Returns:
            str: The configured endpoint URL.
        """
        return "https://oauth2.googleapis.com/token"

    @property
    def userinfo_url(self) -> str | None:
        """OIDC-flavored userinfo endpoint.

        Returns:
            str | None: The endpoint URL, or ``None`` when the provider
                advertises none.
        """
        return "https://openidconnect.googleapis.com/v1/userinfo"

    def _default_scopes(self) -> list[str]:
        return ["openid", "email", "profile"]

    def _parse_user(self, payload: dict[str, Any]) -> OAuthUser:
        return OAuthUser(
            provider=self.provider_name,
            subject=str(payload["sub"]),
            email=payload.get("email"),
            email_verified=_as_bool(payload.get("email_verified")),
            name=payload.get("name"),
            picture=payload.get("picture"),
            raw=payload,
        )


class GitHubOAuthClient(_BaseOAuthClient):
    """GitHub OAuth client.

    GitHub doesn't issue an ``id_token`` — the user identity comes
    from ``GET /user``. Default scopes: ``read:user user:email``.
    """

    provider_name: str = "github"

    @property
    def authorize_url(self) -> str:
        """GitHub's authorize endpoint.

        Returns:
            str: The configured endpoint URL.
        """
        return "https://github.com/login/oauth/authorize"

    @property
    def token_url(self) -> str:
        """GitHub's token endpoint.

        Returns:
            str: The configured endpoint URL.
        """
        return "https://github.com/login/oauth/access_token"

    @property
    def userinfo_url(self) -> str | None:
        """GitHub's user-info endpoint.

        Returns:
            str | None: The endpoint URL, or ``None`` when the provider
                advertises none.
        """
        return "https://api.github.com/user"

    def _default_scopes(self) -> list[str]:
        return ["read:user", "user:email"]

    def _parse_user(self, payload: dict[str, Any]) -> OAuthUser:
        """Map ``GET /user`` onto :class:`OAuthUser`.

        ``email_verified`` is left ``None``: the ``GET /user`` payload the
        client reads carries no verification flag, and the address it
        returns is the account's *public* profile email, which GitHub does
        not require to be verified. Call ``GET /user/emails`` (scope
        ``user:email``) and read the ``verified`` field there when you need
        the answer.
        """
        return OAuthUser(
            provider=self.provider_name,
            subject=str(payload["id"]),
            email=payload.get("email"),
            name=payload.get("name") or payload.get("login"),
            picture=payload.get("avatar_url"),
            raw=payload,
        )


class OIDCProvider(_BaseOAuthClient):
    """Generic OIDC provider — works with any conformant IdP.

    Pass the authorize / token / userinfo endpoints explicitly,
    or fetch them once at boot from the IdP's discovery document
    at ``${issuer}/.well-known/openid-configuration`` and pass the
    URLs in. Default scopes: ``openid email profile``.
    """

    def __init__(
        self,
        *,
        client_id: str,
        client_secret: str,
        redirect_uri: str,
        authorize_url: str,
        token_url: str,
        userinfo_url: str | None = None,
        provider_name: str = "oidc",
        scopes: list[str] | None = None,
        http_client: HTTPClient | None = None,
    ) -> None:
        """Initialize.

        Args:
            client_id (str): App client id at the IdP.
            client_secret (str): App client secret.
            redirect_uri (str): Registered callback URL.
            authorize_url (str): IdP's authorize endpoint.
            token_url (str): IdP's token endpoint.
            userinfo_url (str | None): IdP's userinfo endpoint.
                ``None`` requires you to override
                :meth:`_parse_user` to read claims from the
                ``id_token``.
            provider_name (str): Key embedded in
                :attr:`OAuthUser.provider` (e.g. ``"oidc:auth0"``).
            scopes (list[str] | None): Scopes to request.
            http_client (HTTPClient | None): Shared client.
        """
        self._authorize_url: str = authorize_url
        self._token_url: str = token_url
        self._userinfo_url: str | None = userinfo_url
        self.provider_name = provider_name
        super().__init__(
            client_id=client_id,
            client_secret=client_secret,
            redirect_uri=redirect_uri,
            scopes=scopes,
            http_client=http_client,
        )

    def _default_scopes(self) -> list[str]:
        return ["openid", "email", "profile"]

    @property
    def authorize_url(self) -> str:
        """The provider's authorization endpoint, from discovery or config.

        Returns:
            str: The configured endpoint URL.
        """
        return self._authorize_url

    @property
    def token_url(self) -> str:
        """The provider's token endpoint, from discovery or config.

        Returns:
            str: The configured endpoint URL.
        """
        return self._token_url

    @property
    def userinfo_url(self) -> str | None:
        """The provider's userinfo endpoint, when it advertises one.

        Returns:
            str | None: The endpoint URL, or ``None`` when the provider
                advertises none.
        """
        return self._userinfo_url

    def _parse_user(self, payload: dict[str, Any]) -> OAuthUser:
        return OAuthUser(
            provider=self.provider_name,
            subject=str(payload.get("sub") or payload["id"]),
            email=payload.get("email"),
            email_verified=_as_bool(payload.get("email_verified")),
            name=payload.get("name") or payload.get("preferred_username"),
            picture=payload.get("picture"),
            raw=payload,
        )


__all__: list[str] = [
    "GitHubOAuthClient",
    "GoogleOAuthClient",
    "OAuthError",
    "OAuthTokens",
    "OAuthUser",
    "OIDCProvider",
    "generate_oauth_state",
]
