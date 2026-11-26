"""Service-account access tokens for the Google Drive API.

A service account authenticates with the OAuth 2.0 *JWT bearer* grant
(RFC 7523), described by Google in "Using OAuth 2.0 for Server to Server
Applications" (https://developers.google.com/identity/protocols/oauth2/service-account):
the server signs a JWT with the account's private key (``RS256``), posts it
to the token endpoint and receives a short-lived access token.

No ``google-auth`` here: the whole exchange is one signed JWT and one form
post, built on ``pyjwt`` + ``cryptography`` (both already floors of the
``[auth]``/``[oidc]`` extras) and ``httpx``.

The token exchange sits behind :class:`AccessTokenProvider`, so a provider
for a user's OAuth token can replace :class:`ServiceAccountTokenProvider`
without touching
:class:`~tempest_fastapi_sdk.integrations.storage.google_drive.GoogleDriveClient`.
"""

from __future__ import annotations

import asyncio
import json
import logging
import time
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, field
from pathlib import Path
from typing import Protocol, runtime_checkable

import httpx

from tempest_fastapi_sdk.core.redaction import RedactionPolicy
from tempest_fastapi_sdk.exceptions.integration import GoogleDriveException

GOOGLE_TOKEN_URI: str = "https://oauth2.googleapis.com/token"
"""Google's OAuth 2.0 token endpoint, the ``token_uri`` of a service-account file."""

DRIVE_SCOPE: str = "https://www.googleapis.com/auth/drive"
"""Full Drive access — what a service account writing into a shared folder needs."""

DRIVE_FILE_SCOPE: str = "https://www.googleapis.com/auth/drive.file"
"""Per-file Drive access: only files the app created or opened."""

JWT_BEARER_GRANT_TYPE: str = "urn:ietf:params:oauth:grant-type:jwt-bearer"
"""The ``grant_type`` of the JWT bearer exchange (RFC 7523, section 2.1)."""

ASSERTION_LIFETIME_SECONDS: int = 3600
"""Lifetime of the signed assertion; Google refuses an ``exp`` over one hour."""

TOKEN_REFRESH_MARGIN_SECONDS: float = 300.0
"""How long before ``expires_in`` a cached access token is replaced."""

GOOGLE_DRIVE_EXTRA_HINT: str = (
    "Google Drive support requires the [google-drive] extra. "
    "Install with `pip install tempest-fastapi-sdk[google-drive]`."
)
"""Message of the ``ImportError`` raised without ``pyjwt``/``cryptography``."""

_REDACTION: RedactionPolicy = RedactionPolicy()
"""Redacts tokens, JWTs and e-mails out of upstream error text before it is raised."""

_logger: logging.Logger = logging.getLogger(__name__)


@runtime_checkable
class AccessTokenProvider(Protocol):
    """Anything that hands out a valid OAuth 2.0 access token for Drive.

    :class:`ServiceAccountTokenProvider` is the implementation shipped
    here. A provider for a user's OAuth token (refresh token exchange)
    implements the same single method, and
    :class:`~tempest_fastapi_sdk.integrations.storage.google_drive.GoogleDriveClient`
    works with it unchanged.

    Implementations must be safe to call concurrently and should cache
    the token until shortly before it expires.
    """

    async def access_token(self) -> str:
        """Return a bearer token valid for at least the next request.

        Returns:
            str: The access token, without the ``Bearer`` prefix.
        """
        ...


@dataclass(frozen=True, slots=True)
class ServiceAccountCredentials:
    """The fields of a service-account JSON key the token exchange needs.

    ``private_key`` is kept out of ``repr`` so the object never prints its
    secret, in a traceback or a log line.

    Attributes:
        client_email (str): The account's address — the JWT ``iss`` and the
            principal a Drive folder is shared with.
        private_key (str): The PEM-encoded RSA private key.
        private_key_id (str | None): The key id, sent as the JWT ``kid``
            header when present.
        token_uri (str): Where the assertion is exchanged; also its ``aud``.
    """

    client_email: str
    private_key: str = field(repr=False)
    private_key_id: str | None = None
    token_uri: str = GOOGLE_TOKEN_URI

    @classmethod
    def from_info(cls, info: Mapping[str, object]) -> ServiceAccountCredentials:
        """Build credentials from the parsed service-account JSON.

        Args:
            info (Mapping[str, object]): The decoded key file.

        Returns:
            ServiceAccountCredentials: The credentials.

        Raises:
            ValueError: When ``type`` is not ``"service_account"`` or
                ``client_email`` / ``private_key`` is missing. The message
                names the field, never its value.
        """
        if info.get("type") != "service_account":
            raise ValueError(
                "Google Drive credentials must be a service-account key "
                '(the JSON "type" field must be "service_account").'
            )
        client_email = info.get("client_email")
        private_key = info.get("private_key")
        if not isinstance(client_email, str) or not client_email:
            raise ValueError("Service-account key has no client_email.")
        if not isinstance(private_key, str) or not private_key:
            raise ValueError("Service-account key has no private_key.")
        private_key_id = info.get("private_key_id")
        token_uri = info.get("token_uri")
        return cls(
            client_email=client_email,
            private_key=private_key,
            private_key_id=private_key_id if isinstance(private_key_id, str) else None,
            token_uri=token_uri
            if isinstance(token_uri, str) and token_uri
            else GOOGLE_TOKEN_URI,
        )

    @classmethod
    def from_json(cls, text: str) -> ServiceAccountCredentials:
        """Parse a service-account key given inline.

        Args:
            text (str): The JSON document.

        Returns:
            ServiceAccountCredentials: The credentials.

        Raises:
            ValueError: When the text is not a JSON object, or the object
                is not a service-account key. The text is not quoted.
        """
        try:
            info: object = json.loads(text)
        except json.JSONDecodeError:
            raise ValueError(
                "Google Drive service-account credentials are not valid JSON."
            ) from None
        if not isinstance(info, dict):
            raise ValueError(
                "Google Drive service-account credentials must be a JSON object."
            )
        return cls.from_info(info)

    @classmethod
    def from_file(cls, path: str | Path) -> ServiceAccountCredentials:
        """Read a service-account key file.

        Args:
            path (str | Path): Path to the JSON key downloaded from the
                Google Cloud console.

        Returns:
            ServiceAccountCredentials: The credentials.

        Raises:
            OSError: When the file cannot be read.
            ValueError: When it is not a service-account key.
        """
        return cls.from_json(Path(path).read_text(encoding="utf-8"))

    @classmethod
    def load(
        cls,
        *,
        credentials_path: str | Path | None = None,
        credentials_json: str | None = None,
    ) -> ServiceAccountCredentials:
        """Load credentials from inline JSON or a file — inline JSON wins.

        Same precedence as
        :class:`~tempest_fastapi_sdk.settings.FirebaseSettings`: the file is
        the usual local shape, the inline JSON is what a deployment without
        a mounted volume injects.

        Args:
            credentials_path (str | Path | None): Path to the key file.
            credentials_json (str | None): The key itself.

        Returns:
            ServiceAccountCredentials: The credentials.

        Raises:
            ValueError: When neither source is given, or the one used is
                not a service-account key.
            OSError: When the file cannot be read.
        """
        if credentials_json:
            return cls.from_json(credentials_json)
        if credentials_path:
            return cls.from_file(credentials_path)
        raise ValueError(
            "Google Drive needs a service account: pass credentials_json or "
            "credentials_path (GOOGLE_DRIVE_CREDENTIALS_JSON / "
            "GOOGLE_DRIVE_CREDENTIALS_PATH)."
        )


class ServiceAccountTokenProvider:
    """Access tokens from a service account, via the JWT bearer grant.

    Each exchange signs a fresh assertion with claims ``iss``
    (``client_email``), ``scope`` (space-separated), ``aud``
    (``token_uri``), ``iat`` and ``exp`` (``iat`` + one hour), ``RS256``,
    with ``kid`` set to ``private_key_id``. The token is cached until
    :data:`TOKEN_REFRESH_MARGIN_SECONDS` before its ``expires_in``, and an
    :class:`asyncio.Lock` makes concurrent callers share one exchange
    instead of each running their own.

    Neither the assertion, the private key nor the access token is ever
    logged or put in an exception message.
    """

    def __init__(
        self,
        credentials: ServiceAccountCredentials,
        *,
        http_client: httpx.AsyncClient,
        scopes: Sequence[str] = (DRIVE_SCOPE,),
        refresh_margin_seconds: float = TOKEN_REFRESH_MARGIN_SECONDS,
        clock: Callable[[], float] = time.time,
    ) -> None:
        """Initialize the provider.

        Args:
            credentials (ServiceAccountCredentials): The service account.
            http_client (httpx.AsyncClient): Client used for the token
                endpoint; the caller owns and closes it.
            scopes (Sequence[str]): OAuth scopes requested. Defaults to
                :data:`DRIVE_SCOPE`.
            refresh_margin_seconds (float): How long before expiry the
                cached token is replaced.
            clock (Callable[[], float]): Wall clock in epoch seconds; tests
                inject a fake.

        Raises:
            ValueError: When ``scopes`` is empty.
        """
        if not scopes:
            raise ValueError("ServiceAccountTokenProvider needs at least one scope.")
        self.credentials: ServiceAccountCredentials = credentials
        self.scopes: tuple[str, ...] = tuple(scopes)
        self._http: httpx.AsyncClient = http_client
        self._margin: float = refresh_margin_seconds
        self._clock: Callable[[], float] = clock
        self._token: str | None = None
        self._expires_at: float = 0.0
        self._lock: asyncio.Lock = asyncio.Lock()

    def _fresh(self) -> str | None:
        """Return the cached token when it is still outside the refresh margin."""
        if self._token is not None and self._clock() < self._expires_at:
            return self._token
        return None

    async def access_token(self) -> str:
        """Return a cached token, exchanging a new assertion when it is stale.

        Returns:
            str: The access token.

        Raises:
            GoogleDriveException: When the token endpoint refuses the
                assertion (``details["reason"]`` carries the OAuth
                ``error``, such as ``invalid_grant``) or cannot be reached.
            ImportError: When the ``[google-drive]`` extra is missing.
        """
        cached = self._fresh()
        if cached is not None:
            return cached
        async with self._lock:
            cached = self._fresh()
            if cached is not None:
                return cached
            issued_at = self._clock()
            token, expires_in = await self._exchange(int(issued_at))
            margin = min(self._margin, expires_in / 2)
            self._token = token
            self._expires_at = issued_at + expires_in - margin
            return token

    def _assertion(self, issued_at: int) -> str:
        """Sign the JWT bearer assertion.

        Args:
            issued_at (int): ``iat`` in epoch seconds.

        Returns:
            str: The compact ``RS256`` JWS.

        Raises:
            ImportError: When ``pyjwt``/``cryptography`` is not installed.
        """
        try:
            import jwt
        except ImportError as exc:
            raise ImportError(GOOGLE_DRIVE_EXTRA_HINT) from exc
        headers: dict[str, str] = {"typ": "JWT"}
        if self.credentials.private_key_id:
            headers["kid"] = self.credentials.private_key_id
        claims: dict[str, str | int] = {
            "iss": self.credentials.client_email,
            "scope": " ".join(self.scopes),
            "aud": self.credentials.token_uri,
            "iat": issued_at,
            "exp": issued_at + ASSERTION_LIFETIME_SECONDS,
        }
        try:
            return jwt.encode(
                claims,
                self.credentials.private_key,
                algorithm="RS256",
                headers=headers,
            )
        except NotImplementedError as exc:
            raise ImportError(GOOGLE_DRIVE_EXTRA_HINT) from exc

    async def _exchange(self, issued_at: int) -> tuple[str, float]:
        """Post the assertion to the token endpoint.

        Args:
            issued_at (int): ``iat`` of the assertion.

        Returns:
            tuple[str, float]: The access token and its ``expires_in``.

        Raises:
            GoogleDriveException: On a non-200 answer, an answer without a
                token, or a transport failure.
        """
        assertion = self._assertion(issued_at)
        try:
            response = await self._http.post(
                self.credentials.token_uri,
                data={"grant_type": JWT_BEARER_GRANT_TYPE, "assertion": assertion},
                headers={"Accept": "application/json"},
            )
        except httpx.TransportError as exc:
            _logger.warning("Google token endpoint unreachable: %s", type(exc).__name__)
            raise GoogleDriveException(
                upstream_status=None,
                reason="token_endpoint_unreachable",
                message="Could not reach Google's token endpoint.",
            ) from exc
        body: object
        try:
            body = response.json()
        except ValueError:
            body = None
        if response.status_code != 200:
            reason: str | None = None
            description = ""
            if isinstance(body, dict):
                error = body.get("error")
                reason = error if isinstance(error, str) else None
                detail = body.get("error_description")
                description = detail if isinstance(detail, str) else ""
            summary = (
                "Google refused the service-account assertion "
                f"({response.status_code} {reason or 'unknown'})"
            )
            raise GoogleDriveException(
                upstream_status=response.status_code,
                reason=reason,
                message=_REDACTION.redact_text(
                    f"{summary}: {description}" if description else summary
                ),
            )
        token = body.get("access_token") if isinstance(body, dict) else None
        expires_in = body.get("expires_in") if isinstance(body, dict) else None
        if not isinstance(token, str) or not token:
            raise GoogleDriveException(
                upstream_status=response.status_code,
                reason="no_access_token",
                message="Google's token endpoint answered without an access_token.",
            )
        lifetime = (
            float(expires_in)
            if isinstance(expires_in, int | float) and expires_in > 0
            else float(ASSERTION_LIFETIME_SECONDS)
        )
        return token, lifetime


__all__: list[str] = [
    "ASSERTION_LIFETIME_SECONDS",
    "DRIVE_FILE_SCOPE",
    "DRIVE_SCOPE",
    "GOOGLE_DRIVE_EXTRA_HINT",
    "GOOGLE_TOKEN_URI",
    "JWT_BEARER_GRANT_TYPE",
    "TOKEN_REFRESH_MARGIN_SECONDS",
    "AccessTokenProvider",
    "ServiceAccountCredentials",
    "ServiceAccountTokenProvider",
]
