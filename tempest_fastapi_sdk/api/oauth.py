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

import base64
import secrets
from dataclasses import dataclass, field
from typing import Any, Protocol, runtime_checkable
from urllib.parse import urlencode

from tempest_fastapi_sdk.exceptions.base import AppException
from tempest_fastapi_sdk.exceptions.oauth import (
    OAuthAudienceUnverifiableException,
    OAuthTokenAudienceMismatchException,
    OAuthTokenRejectedException,
)
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


@runtime_checkable
class OAuthClient(Protocol):
    """The three calls the bundled auth router makes on a provider.

    Every client in this module satisfies it, and so does anything else
    that speaks the same three moves — which is the point: the router
    resolves ``/auth/oauth/{provider}/…`` against a mapping the
    application registered, so a provider the SDK does not ship, or a
    stub that never touches the network in tests, is wired the same way
    Google is. Naming the shape as a protocol keeps
    ``_BaseOAuthClient`` private without forcing consumers to subclass
    it.

    Attributes:
        provider_name (str): Key this client answers to. Stored on
            every linked-identity row, so changing it after accounts
            exist orphans them.
    """

    provider_name: str

    def build_authorize_url(self, *, state: str, **extra: str) -> str:
        """Render the URL the browser must be redirected to.

        Args:
            state (str): CSRF state to echo back on the callback.
            **extra (str): Extra query parameters.

        Returns:
            str: The provider's authorize URL.
        """
        ...

    async def exchange_code(self, code: str, /) -> OAuthTokens:
        """Swap an authorization code for tokens.

        Args:
            code (str): The ``code`` query parameter from the callback.

        Returns:
            OAuthTokens: The provider's token bundle.
        """
        ...

    async def fetch_user(self, tokens: OAuthTokens, /) -> OAuthUser:
        """Resolve tokens to a normalized identity.

        Args:
            tokens (OAuthTokens): The bundle from
                :meth:`exchange_code`.

        Returns:
            OAuthUser: The normalized identity.
        """
        ...


@runtime_checkable
class OAuthAudienceVerifier(Protocol):
    """The extra call the token-in-hand endpoint makes on a provider.

    Deliberately **not** part of :class:`OAuthClient`: the redirect flow
    exchanges a code this service asked for, so it already knows the
    token is its own and needs none of this. Only
    ``POST /auth/oauth/{provider}/token`` — where the token is handed to
    us by whoever is calling — has to ask who the token was minted for.

    Keeping it separate also means a client written against an older
    SDK still satisfies :class:`OAuthClient`; it just cannot be used
    with the token-in-hand route, which refuses with
    :class:`~tempest_fastapi_sdk.exceptions.oauth.OAuthAudienceUnverifiableException`
    instead of trusting it.
    """

    async def verify_token_audience(self, tokens: OAuthTokens, /) -> None:
        """Assert the token was issued to *this* application.

        Args:
            tokens (OAuthTokens): The bundle the caller presented.

        Raises:
            OAuthTokenAudienceMismatchException: When the provider says
                the token belongs to another ``client_id``.
            OAuthTokenRejectedException: When the provider rejects the
                token outright.
        """
        ...


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

    @property
    def tokeninfo_url(self) -> str | None:
        """Endpoint that reports which application a token belongs to.

        ``None`` — the default — means this client cannot answer the
        question, and the token-in-hand route refuses rather than
        guessing. Point it at the provider's tokeninfo / RFC 7662
        introspection endpoint to enable that route.

        Returns:
            str | None: The configured endpoint, or ``None``.
        """
        return None

    async def verify_token_audience(self, tokens: OAuthTokens) -> None:
        """Refuse a token that was minted for a different application.

        The check the userinfo endpoint cannot make: userinfo answers
        *whose* token this is, which is exactly what an attacker's own
        app can obtain about a victim. Comparing the audience the
        provider reports against :attr:`client_id` is what separates
        "this user consented to us" from "this user consented to
        somebody, and somebody is replaying it here".

        Reads ``aud``, ``azp`` and ``client_id`` — the three spellings
        Google's tokeninfo and RFC 7662 introspection use between them —
        and accepts when any of them is ours. RFC 7662's
        ``active: false`` is treated as a rejection, since an inactive
        token has no audience to compare.

        Args:
            tokens (OAuthTokens): The bundle the caller presented.

        Raises:
            OAuthAudienceUnverifiableException: When this client has no
                :attr:`tokeninfo_url` to ask.
            OAuthTokenRejectedException: When the provider refuses the
                token, or answers with something that is not JSON.
            OAuthTokenAudienceMismatchException: When the audience the
                provider reports is not :attr:`client_id`.
        """
        url = self.tokeninfo_url
        if url is None:
            raise OAuthAudienceUnverifiableException(
                details={"provider": self.provider_name},
            )
        response = await self._http.get(
            url,
            params={"access_token": tokens.access_token},
            headers={"Accept": "application/json"},
        )
        if response.status_code >= 400:
            raise OAuthTokenRejectedException(
                details={
                    "provider": self.provider_name,
                    "status": response.status_code,
                },
            )
        try:
            payload: dict[str, Any] = response.json()
        except ValueError as error:
            raise OAuthTokenRejectedException(
                details={"provider": self.provider_name},
            ) from error
        if payload.get("active") is False:
            raise OAuthTokenRejectedException(
                details={"provider": self.provider_name},
            )
        self._assert_audience(payload)

    def _assert_audience(self, payload: dict[str, Any]) -> None:
        """Compare the audience claims in ``payload`` with our client id.

        Args:
            payload (dict[str, Any]): The introspection response.

        Raises:
            OAuthTokenAudienceMismatchException: When none of the
                audience claims is :attr:`client_id`.
        """
        audiences: set[str] = set()
        for key in ("aud", "azp", "client_id"):
            value = payload.get(key)
            if isinstance(value, list):
                audiences.update(str(item) for item in value)
            elif value:
                audiences.add(str(value))
        if self.client_id not in audiences:
            raise OAuthTokenAudienceMismatchException(
                details={"provider": self.provider_name},
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

    @property
    def tokeninfo_url(self) -> str | None:
        """Google's tokeninfo endpoint, which reports ``aud`` and ``azp``.

        Returns:
            str | None: The endpoint the audience check queries.
        """
        return "https://oauth2.googleapis.com/tokeninfo"

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

    async def verify_token_audience(self, tokens: OAuthTokens) -> None:
        """Ask GitHub whether the token belongs to *this* OAuth app.

        GitHub publishes no tokeninfo endpoint. What it does publish is
        ``POST /applications/{client_id}/token``, authenticated with the
        app's own ``client_id:client_secret`` — it answers 200 only for
        a token that app issued, and 404 for anybody else's. That is the
        same question :meth:`_BaseOAuthClient.verify_token_audience`
        asks, so the refusals it raises are the same ones.

        Args:
            tokens (OAuthTokens): The bundle the caller presented.

        Raises:
            OAuthTokenAudienceMismatchException: When GitHub does not
                recognize the token as this application's (404).
            OAuthTokenRejectedException: When GitHub refuses the check
                for any other reason.
        """
        credentials = base64.b64encode(
            f"{self.client_id}:{self.client_secret}".encode(),
        ).decode()
        response = await self._http.post(
            f"https://api.github.com/applications/{self.client_id}/token",
            json={"access_token": tokens.access_token},
            headers={
                "Accept": "application/vnd.github+json",
                "Authorization": f"Basic {credentials}",
            },
        )
        if response.status_code == 404:
            raise OAuthTokenAudienceMismatchException(
                details={"provider": self.provider_name},
            )
        if response.status_code >= 400:
            raise OAuthTokenRejectedException(
                details={
                    "provider": self.provider_name,
                    "status": response.status_code,
                },
            )

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
        tokeninfo_url: str | None = None,
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
            tokeninfo_url (str | None): IdP's token-introspection
                endpoint (RFC 7662), used to check which application a
                presented token was issued to. ``None`` leaves
                ``POST /auth/oauth/{provider}/token`` refusing for this
                provider — the redirect flow is unaffected.
            provider_name (str): Key embedded in
                :attr:`OAuthUser.provider` (e.g. ``"oidc:auth0"``).
            scopes (list[str] | None): Scopes to request.
            http_client (HTTPClient | None): Shared client.
        """
        self._authorize_url: str = authorize_url
        self._token_url: str = token_url
        self._userinfo_url: str | None = userinfo_url
        self._tokeninfo_url: str | None = tokeninfo_url
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

    @property
    def tokeninfo_url(self) -> str | None:
        """The IdP's token-introspection endpoint, when one was wired.

        Returns:
            str | None: The endpoint URL, or ``None`` — which keeps
            ``POST /auth/oauth/{provider}/token`` refusing for this
            provider, since its audience cannot be checked.
        """
        return self._tokeninfo_url

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
    "OAuthClient",
    "OAuthError",
    "OAuthTokens",
    "OAuthUser",
    "OIDCProvider",
    "generate_oauth_state",
]
