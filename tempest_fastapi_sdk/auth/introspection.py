"""Introspection-based bearer authentication (OAuth2 resource server).

Some services do not issue their own tokens: they receive an opaque
bearer minted by an upstream identity provider and must validate it by
asking that provider who the caller is. This is the OAuth2
*resource-server* pattern — the service is a consumer of tokens, never
an issuer.

:class:`IntrospectionAuth` generalizes that pattern:

* it validates a bearer by calling an upstream ``userinfo`` /
  introspection endpoint (``GET`` with ``Authorization: Bearer <token>``),
* it caches successful lookups in-process for a short TTL so a burst of
  requests carrying the same token does not hammer the provider,
* it can gate access on an application-membership claim
  (``access_apps`` by default), and
* it extracts the user id from the subject claim (``sub`` by default).

The instance owns a lazily-created shared :class:`httpx.AsyncClient`
(unless one is injected) and its own per-instance cache, so an
application may create several independent :class:`IntrospectionAuth`
objects — one per upstream — without them sharing state.

Both :meth:`IntrospectionAuth.get_claims` and
:meth:`IntrospectionAuth.get_user_id` are bound methods usable directly
as FastAPI dependencies::

    auth = IntrospectionAuth(userinfo_url="https://id.example.com/users/me")

    @router.get("/me")
    async def me(claims: dict[str, Any] = Depends(auth.get_claims)) -> dict[str, Any]:
        return claims

    @router.get("/things")
    async def things(user_id: UUID = Depends(auth.get_user_id)) -> list[str]:
        return await service.list_for(user_id)
"""

from __future__ import annotations

import time
from collections.abc import Callable
from typing import Annotated, Any
from uuid import UUID

import httpx
from fastapi import Depends
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer

from tempest_fastapi_sdk.exceptions import (
    ForbiddenException,
    UnauthorizedException,
)

_bearer_scheme = HTTPBearer(auto_error=False)


class IntrospectionAuth:
    """Validate opaque bearer tokens against an upstream userinfo endpoint.

    The instance keeps an in-process cache keyed by the raw token, with a
    time-to-live measured on :func:`time.monotonic`. Successful lookups are
    cached; ``401`` / ``403`` responses evict the token immediately. The
    cache and the HTTP client are per-instance, so distinct upstreams get
    distinct :class:`IntrospectionAuth` objects with isolated state.

    Attributes:
        cache_ttl_seconds (int): How long a successful userinfo lookup stays
            cached, in seconds. ``0`` disables caching entirely.
        required_app (str | None): When set, the value that must be present
            in the application-membership claim for access to be granted.
        app_claim (str): Name of the claim holding the list of applications
            the subject may access.
        subject_claim (str): Name of the claim holding the subject
            identifier (parsed as a :class:`~uuid.UUID`).
    """

    def __init__(
        self,
        *,
        userinfo_url: str | Callable[[], str],
        cache_ttl_seconds: int = 30,
        required_app: str | None = None,
        app_claim: str = "access_apps",
        subject_claim: str = "sub",
        timeout: float = 10.0,
        http_client: httpx.AsyncClient | None = None,
    ) -> None:
        """Initialize the introspection authenticator.

        Args:
            userinfo_url (str | Callable[[], str]): The upstream userinfo /
                introspection URL, or a zero-argument callable returning it.
                A callable is resolved on every call, so a settings property
                may be passed and picked up lazily / per request.
            cache_ttl_seconds (int): Seconds to cache a successful lookup.
                ``0`` disables caching. Defaults to ``30``.
            required_app (str | None): When provided, the caller's
                ``app_claim`` list must contain this value, otherwise access
                is forbidden. Defaults to ``None`` (no app gate).
            app_claim (str): Claim name holding the list of accessible
                applications. Defaults to ``"access_apps"``.
            subject_claim (str): Claim name holding the subject id. Defaults
                to ``"sub"``.
            timeout (float): Total timeout, in seconds, applied to each
                upstream request via :class:`httpx.Timeout`. Ignored when
                ``http_client`` is injected. Defaults to ``10.0``.
            http_client (httpx.AsyncClient | None): An optional pre-built
                client to use instead of the lazily-created shared one. When
                provided, the instance does not own its lifecycle. Defaults
                to ``None``.
        """
        self._userinfo_url: str | Callable[[], str] = userinfo_url
        self.cache_ttl_seconds: int = cache_ttl_seconds
        self.required_app: str | None = required_app
        self.app_claim: str = app_claim
        self.subject_claim: str = subject_claim
        self._timeout: float = timeout
        self._http_client: httpx.AsyncClient | None = http_client
        self._owns_client: bool = http_client is None
        self._cache: dict[str, tuple[dict[str, Any], float]] = {}

    def _resolve_userinfo_url(self) -> str:
        """Resolve the configured userinfo URL for the current call.

        Returns:
            str: The upstream URL. When a callable was configured it is
            invoked on every call so late-bound settings are honored.
        """
        if callable(self._userinfo_url):
            return self._userinfo_url()
        return self._userinfo_url

    def _get_client(self) -> httpx.AsyncClient:
        """Return the HTTP client, creating the shared one on first use.

        Returns:
            httpx.AsyncClient: The injected client when one was provided,
            otherwise a lazily-created shared client bounded by ``timeout``.
        """
        if self._http_client is None:
            self._http_client = httpx.AsyncClient(timeout=httpx.Timeout(self._timeout))
        return self._http_client

    def _cache_get(self, token: str) -> dict[str, Any] | None:
        """Return cached claims for a token when still within its TTL.

        Args:
            token (str): The raw bearer token used as the cache key.

        Returns:
            dict[str, Any] | None: The cached claims when present and not
            expired, otherwise ``None``. Expired entries are evicted.
        """
        if self.cache_ttl_seconds <= 0:
            return None
        entry = self._cache.get(token)
        if entry is None:
            return None
        claims, expires_at = entry
        if time.monotonic() >= expires_at:
            self._cache.pop(token, None)
            return None
        return claims

    def _cache_set(self, token: str, claims: dict[str, Any]) -> None:
        """Store claims for a token when caching is enabled.

        Args:
            token (str): The raw bearer token used as the cache key.
            claims (dict[str, Any]): The claims returned by the upstream.
        """
        if self.cache_ttl_seconds <= 0:
            return
        self._cache[token] = (claims, time.monotonic() + self.cache_ttl_seconds)

    async def fetch_userinfo(self, token: str) -> dict[str, Any]:
        """Fetch and cache the userinfo claims for a bearer token.

        On a cache hit within the TTL the upstream is not contacted. On a
        cache miss the configured userinfo URL is called with the token as a
        bearer credential; a ``200`` response is cached and returned.

        Args:
            token (str): The raw opaque bearer token to validate.

        Returns:
            dict[str, Any]: The claims returned by the upstream userinfo
            endpoint.

        Raises:
            UnauthorizedException: When the upstream is unreachable
                (:class:`httpx.HTTPError`), returns ``401`` / ``403`` (the
                token is also evicted from the cache), or returns any other
                non-``200`` status.
        """
        cached = self._cache_get(token)
        if cached is not None:
            return cached

        url = self._resolve_userinfo_url()
        client = self._get_client()
        try:
            response = await client.get(
                url, headers={"Authorization": f"Bearer {token}"}
            )
        except httpx.HTTPError as error:
            raise UnauthorizedException(
                message="Could not reach the authentication provider"
            ) from error

        if response.status_code in (401, 403):
            self._cache.pop(token, None)
            raise UnauthorizedException(message="Invalid or expired token")

        if response.status_code != 200:
            raise UnauthorizedException(message="Invalid or expired token")

        claims: dict[str, Any] = response.json()
        self._cache_set(token, claims)
        return claims

    async def get_claims(
        self,
        credentials: Annotated[
            HTTPAuthorizationCredentials | None, Depends(_bearer_scheme)
        ] = None,
    ) -> dict[str, Any]:
        """FastAPI dependency returning the validated userinfo claims.

        Args:
            credentials (HTTPAuthorizationCredentials | None): The bearer
                credentials extracted by the ``HTTPBearer`` scheme, or
                ``None`` when no ``Authorization`` header was sent.

        Returns:
            dict[str, Any]: The claims returned by the upstream userinfo
            endpoint.

        Raises:
            UnauthorizedException: When no credentials were supplied or the
                token could not be validated upstream.
            ForbiddenException: When ``required_app`` is configured and is
                absent from the caller's ``app_claim`` list.
        """
        if credentials is None:
            raise UnauthorizedException(message="Authentication required")

        claims = await self.fetch_userinfo(credentials.credentials)

        if self.required_app is not None:
            allowed_apps = claims.get(self.app_claim) or []
            if self.required_app not in allowed_apps:
                raise ForbiddenException(
                    message="Application access is not granted for this user"
                )

        return claims

    async def get_user_id(
        self,
        credentials: Annotated[
            HTTPAuthorizationCredentials | None, Depends(_bearer_scheme)
        ] = None,
    ) -> UUID:
        """FastAPI dependency returning the caller's subject id as a UUID.

        This depends on the bearer credentials directly and internally calls
        :meth:`get_claims`, which avoids referencing ``self`` in a default
        argument (a default is evaluated at method-definition time, when the
        instance does not yet exist) while still wiring cleanly under
        ``Depends(auth.get_user_id)``.

        Args:
            credentials (HTTPAuthorizationCredentials | None): The bearer
                credentials extracted by the ``HTTPBearer`` scheme, or
                ``None`` when no ``Authorization`` header was sent.

        Returns:
            UUID: The subject claim parsed as a :class:`~uuid.UUID`.

        Raises:
            UnauthorizedException: When the token is missing / invalid, or
                the subject claim is absent or not a valid UUID.
            ForbiddenException: When ``required_app`` is configured and is
                absent from the caller's ``app_claim`` list.
        """
        claims = await self.get_claims(credentials)
        try:
            return UUID(str(claims[self.subject_claim]))
        except (KeyError, ValueError) as error:
            raise UnauthorizedException(
                message="Token is missing a valid subject claim"
            ) from error
