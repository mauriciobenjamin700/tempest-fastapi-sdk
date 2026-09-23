"""Offline verification of access tokens signed by an OIDC realm.

A realm (Keycloak, Auth0, Okta, Entra …) signs its access tokens with a
private key and publishes the public half as a JWK Set. Verifying a token
against that set answers "did this realm mint this token, for us, and is
it still valid" with no request per login — the key set is fetched once
and cached.

The cryptography is the easy part. What each service used to get wrong
is the **taxonomy**: which failure means "the caller presented a bad
credential" (401, never retry) and which means "the realm is down" (502,
retry later). :class:`OIDCTokenVerifier` fixes that mapping:

- Not a JWT at all (``"x"``) — ``OAuthTokenRejectedException``, 401.
- ``alg`` outside the configured list — ``OAuthTokenRejectedException``,
  401.
- ``kid`` absent from the key set — ``OAuthTokenRejectedException``, 401.
- Bad signature, ``iss`` or ``exp`` — ``OAuthTokenRejectedException``, 401.
- ``aud``/``azp`` naming another client —
  ``OAuthTokenAudienceMismatchException``, 401.
- Key set unreachable, non-2xx or invalid —
  ``OAuthProviderUnavailableException``, 502.

Requires the ``[oidc]`` extra (PyJWT + ``cryptography``, which PyJWT
needs for RS256 and EC keys) and the ``[http]`` extra for the
:class:`~tempest_fastapi_sdk.utils.http_client.HTTPClient` that fetches
the key set. Both are imported lazily; :class:`OIDCTokenVerifier` raises
:class:`ImportError` on instantiation when they are missing.
"""

from __future__ import annotations

import asyncio
import time
from collections.abc import Callable, Iterable, Sequence
from typing import Any

try:
    import jwt as _jwt
    from jwt.algorithms import has_crypto as _has_crypto
except ImportError:  # pragma: no cover - guarded by extras
    _jwt: Any = None  # type: ignore[no-redef]
    _has_crypto = False

try:
    import httpx as _httpx
except ImportError:  # pragma: no cover - guarded by [http] extra
    _httpx: Any = None  # type: ignore[no-redef]

from tempest_fastapi_sdk.api.oauth import (
    OAuthProviderUnavailableException,
    _assert_audience,
)
from tempest_fastapi_sdk.exceptions.oauth import OAuthTokenRejectedException
from tempest_fastapi_sdk.utils.http_client import CircuitOpenError, HTTPClient

ALWAYS_REQUIRED_CLAIMS: tuple[str, ...] = ("exp", "iss", "sub")
"""Claims every verified token must carry, whatever the caller configures.

``exp`` is the one that matters most: PyJWT checks it only **when
present**, so a token without it would otherwise verify forever.
"""


class OIDCTokenVerifier:
    """Verify realm-signed JWT access tokens against the realm's JWK Set.

    The key set is fetched with the injected async
    :class:`~tempest_fastapi_sdk.utils.http_client.HTTPClient` and parsed
    with ``jwt.PyJWKSet.from_dict``. ``jwt.PyJWKClient`` is deliberately
    not used: its fetch is blocking I/O (it would need a thread per
    refresh), and it refetches the key set once per **unknown** ``kid``,
    so a stream of tokens carrying forged ``kid`` values turns into the
    same stream of requests to the IdP. Here a miss triggers at most one
    refresh per ``min_refresh_interval``, and concurrent misses share it.

    The ``alg`` list is closed and never read from the token: the header
    is attacker-controlled, so ``none`` or ``HS256`` (verifying an RSA
    public key as an HMAC secret) are refused before any key is looked
    up. ``aud`` is checked by the SDK rather than by PyJWT, over ``aud``,
    ``azp`` and ``client_id`` together — Keycloak puts the resource the
    token addresses in ``aud`` (usually ``"account"``) and names the
    client in ``azp``, so PyJWT's ``verify_aud`` would refuse every
    legitimate token.

    Attributes:
        issuer (str): The ``iss`` every token must carry.
        jwks_url (str): Where the realm publishes its JWK Set.
        algorithms (tuple[str, ...]): Signing algorithms accepted.
        required_claims (tuple[str, ...]): Claims a token must carry;
            always includes :data:`ALWAYS_REQUIRED_CLAIMS`.
        leeway (float): Clock skew tolerated on ``exp`` / ``nbf`` /
            ``iat``, in seconds.
        lifespan (float): Seconds a fetched key set is trusted before
            the next verification refreshes it.
        min_refresh_interval (float): Minimum seconds between two
            fetches of the key set, whatever triggers them.
    """

    def __init__(
        self,
        issuer: str,
        jwks_url: str,
        *,
        algorithms: Sequence[str] = ("RS256",),
        required_claims: Sequence[str] = ALWAYS_REQUIRED_CLAIMS,
        leeway: float = 0,
        lifespan: float = 300,
        min_refresh_interval: float = 60,
        http_client: HTTPClient | None = None,
    ) -> None:
        """Initialize.

        Args:
            issuer (str): The realm's issuer, exactly as it appears in
                the ``iss`` claim (for Keycloak,
                ``https://<host>/realms/<realm>``).
            jwks_url (str): The realm's JWK Set endpoint — the
                ``jwks_uri`` of its discovery document (for Keycloak,
                ``<issuer>/protocol/openid-connect/certs``).
            algorithms (Sequence[str]): Signing algorithms accepted.
                Closed on purpose; ``none`` is refused even if listed.
            required_claims (Sequence[str]): Claims a token must carry.
                ``exp``, ``iss`` and ``sub`` are added when omitted.
            leeway (float): Clock skew tolerated, in seconds.
            lifespan (float): Seconds a fetched key set is trusted.
            min_refresh_interval (float): Minimum seconds between two
                key-set fetches. Bounds what forged ``kid`` values can
                cost the IdP: one request per interval, not one per
                token.
            http_client (HTTPClient | None): Shared client for the
                key-set fetch. ``None`` builds a dedicated one (timeout
                10s, breaker off); close it with :meth:`aclose`.

        Raises:
            ImportError: When the ``[oidc]`` extra (PyJWT with
                ``cryptography``) is missing.
            ValueError: When ``algorithms`` is empty or only ``none``,
                or when a duration is negative.
        """
        if _jwt is None or not _has_crypto:
            raise ImportError(
                "OIDCTokenVerifier requires the [oidc] extra "
                "(PyJWT with cryptography). Install with "
                '`pip install "tempest-fastapi-sdk[oidc]"`.'
            )
        accepted = tuple(alg for alg in algorithms if alg.lower() != "none")
        if not accepted:
            raise ValueError("algorithms must name at least one signing algorithm")
        if min(leeway, lifespan, min_refresh_interval) < 0:
            raise ValueError("leeway, lifespan and min_refresh_interval must be >= 0")
        self.issuer: str = issuer
        self.jwks_url: str = jwks_url
        self.algorithms: tuple[str, ...] = accepted
        self.required_claims: tuple[str, ...] = tuple(
            dict.fromkeys((*ALWAYS_REQUIRED_CLAIMS, *required_claims))
        )
        self.leeway: float = leeway
        self.lifespan: float = lifespan
        self.min_refresh_interval: float = min_refresh_interval
        self._http: HTTPClient = http_client or HTTPClient(
            timeout=10.0,
            failure_threshold=0,
        )
        self._owns_http: bool = http_client is None
        self._keys: dict[str, Any] = {}
        self._fetched_at: float | None = None
        self._attempted_at: float | None = None
        self._last_failure: OAuthProviderUnavailableException | None = None
        self._lock: asyncio.Lock = asyncio.Lock()
        self._clock: Callable[[], float] = time.monotonic

    async def aclose(self) -> None:
        """Close the underlying HTTP client when this verifier owns it."""
        if self._owns_http:
            await self._http.aclose()

    def _details(self, reason: str) -> dict[str, Any]:
        """Build the ``details`` payload of a refusal.

        Args:
            reason (str): Short machine-readable cause.

        Returns:
            dict[str, Any]: The issuer and the reason.
        """
        return {"issuer": self.issuer, "reason": reason}

    def _unavailable(self, reason: str) -> OAuthProviderUnavailableException:
        """Build the 502 raised when the key set cannot be used.

        Args:
            reason (str): Short machine-readable cause.

        Returns:
            OAuthProviderUnavailableException: The exception to raise.
        """
        return OAuthProviderUnavailableException(details=self._details(reason))

    async def _fetch_keys(self) -> dict[str, Any]:
        """Download and parse the realm's JWK Set.

        Only signing keys with a ``kid`` are kept — ``use: "enc"`` keys
        are for encryption and must never verify a signature.

        Returns:
            dict[str, Any]: ``PyJWK`` objects keyed by ``kid``.

        Raises:
            OAuthProviderUnavailableException: When the endpoint is
                unreachable, answers non-2xx, returns something that is
                not JSON, or returns no usable signing key.
        """
        assert _httpx is not None, "guarded by HTTPClient"
        try:
            response = await self._http.get(
                self.jwks_url,
                headers={"Accept": "application/json"},
            )
        except (_httpx.HTTPError, CircuitOpenError) as error:
            raise self._unavailable("unreachable") from error
        if not 200 <= response.status_code < 300:
            raise self._unavailable(f"status {response.status_code}")
        try:
            payload: Any = response.json()
        except ValueError as error:
            raise self._unavailable("not json") from error
        entries = payload.get("keys") if isinstance(payload, dict) else None
        if not isinstance(entries, list) or not all(
            isinstance(entry, dict) for entry in entries
        ):
            raise self._unavailable("not a jwk set")
        try:
            key_set = _jwt.PyJWKSet.from_dict(payload)
        except _jwt.PyJWTError as error:
            raise self._unavailable("no usable key") from error
        keys = {
            key.key_id: key
            for key in key_set.keys
            if key.key_id and key.public_key_use in ("sig", None)
        }
        if not keys:
            raise self._unavailable("no signing key")
        return keys

    async def _signing_key(self, kid: str) -> Any:
        """Return the key for ``kid``, refreshing the key set when allowed.

        A cached key is used while the set is younger than
        :attr:`lifespan`. A miss (or a stale set) refreshes it, but at
        most once per :attr:`min_refresh_interval`: inside that window a
        missing ``kid`` is refused straight from the cache, and a failed
        refresh keeps answering 502 instead of pretending the key set
        was read. The lock makes concurrent misses wait for one fetch
        instead of each starting their own.

        Args:
            kid (str): The ``kid`` from the token header.

        Returns:
            Any: The ``PyJWK`` that verifies the token.

        Raises:
            OAuthTokenRejectedException: When the key set was read and
                has no key for ``kid``.
            OAuthProviderUnavailableException: When the key set cannot
                be read.
        """
        async with self._lock:
            now = self._clock()
            fresh = (
                self._fetched_at is not None and now - self._fetched_at < self.lifespan
            )
            if fresh and kid in self._keys:
                return self._keys[kid]
            cooling = (
                self._attempted_at is not None
                and now - self._attempted_at < self.min_refresh_interval
            )
            if not cooling:
                self._attempted_at = now
                try:
                    self._keys = await self._fetch_keys()
                except OAuthProviderUnavailableException as error:
                    self._last_failure = error
                    raise
                self._fetched_at = now
                self._last_failure = None
            elif self._last_failure is not None:
                raise OAuthProviderUnavailableException(
                    details=self._last_failure.details,
                ) from self._last_failure
            key = self._keys.get(kid)
            if key is None:
                raise OAuthTokenRejectedException(details=self._details("unknown kid"))
            return key

    async def verify(
        self,
        token: str,
        *,
        accepted_audiences: Iterable[str],
    ) -> dict[str, Any]:
        """Verify ``token`` and return its claims.

        Order matters and is part of the contract: the header is parsed
        and its ``alg`` checked **before** any key-set request, so a
        malformed or ``alg: none`` token costs the IdP nothing.

        Args:
            token (str): The compact JWT the caller presented.
            accepted_audiences (Iterable[str]): Client ids this
                application answers to, compared against ``aud``,
                ``azp`` and ``client_id``.

        Returns:
            dict[str, Any]: The verified claims.

        Raises:
            OAuthTokenRejectedException: When the token is not a JWT,
                its ``alg`` is not accepted, its ``kid`` is unknown, or
                its signature, ``iss``, ``exp`` or a required claim is
                invalid.
            OAuthTokenAudienceMismatchException: When the token was
                issued to another client.
            OAuthAudienceUnverifiableException: When
                ``accepted_audiences`` holds no client id.
            OAuthProviderUnavailableException: When the key set cannot
                be read.
        """
        try:
            header = _jwt.get_unverified_header(token)
        except _jwt.PyJWTError as error:
            raise OAuthTokenRejectedException(
                details=self._details("malformed"),
            ) from error
        if header.get("alg") not in self.algorithms:
            raise OAuthTokenRejectedException(details=self._details("algorithm"))
        kid = header.get("kid")
        if not isinstance(kid, str) or not kid:
            raise OAuthTokenRejectedException(details=self._details("no kid"))
        key = await self._signing_key(kid)
        try:
            claims: dict[str, Any] = _jwt.decode(
                token,
                key=key,
                algorithms=list(self.algorithms),
                issuer=self.issuer,
                leeway=self.leeway,
                options={
                    "require": list(self.required_claims),
                    "verify_aud": False,
                },
            )
        except _jwt.PyJWTError as error:
            raise OAuthTokenRejectedException(
                details=self._details(type(error).__name__),
            ) from error
        _assert_audience(
            claims,
            accepted=accepted_audiences,
            details=self._details("audience"),
        )
        return claims


__all__: list[str] = [
    "ALWAYS_REQUIRED_CLAIMS",
    "OIDCTokenVerifier",
]
