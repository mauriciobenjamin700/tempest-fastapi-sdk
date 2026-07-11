"""Webhook signatures — inbound verification + outbound signed delivery."""

from __future__ import annotations

import asyncio
import base64
import hashlib
import hmac
import json
import time
import uuid
from collections.abc import Callable, Coroutine, Mapping
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

from fastapi import Header, Request

from tempest_fastapi_sdk.exceptions.unauthorized import UnauthorizedException

if TYPE_CHECKING:
    import httpx

# The transient exceptions worth retrying — connection resets, timeouts.
# Guarded so importing this module never requires httpx (the sender takes
# an injected client; a project that uses it already has httpx installed).
try:
    import httpx as _httpx

    _TRANSIENT_ERRORS: tuple[type[Exception], ...] = (_httpx.TransportError,)
except ImportError:  # pragma: no cover - httpx is an optional dependency
    _TRANSIENT_ERRORS = ()


class WebhookSignatureVerifier:
    """Validate HMAC-signed webhook payloads (Stripe/GitHub-style).

    Providers usually compute ``hmac(secret, body)`` with a fixed
    algorithm and ship the digest in a request header (hex or base64).
    This helper centralizes the verification using
    :func:`hmac.compare_digest` (constant time) and exposes a FastAPI
    dependency that reads the raw body without consuming it for the
    route handler.

    Attributes:
        secret (bytes): The shared secret as bytes.
        algorithm (str): The hashlib algorithm name.
        header_name (str): The request header carrying the signature.
        encoding (str): How the signature is encoded — ``"hex"`` or
            ``"base64"``.
        prefix (str): Optional fixed prefix (e.g. ``"sha256="``).
    """

    def __init__(
        self,
        secret: str | bytes,
        *,
        algorithm: str = "sha256",
        header_name: str = "X-Signature",
        encoding: str = "hex",
        prefix: str = "",
    ) -> None:
        """Initialize the verifier.

        Args:
            secret (str | bytes): The shared secret. Strings are
                encoded as UTF-8.
            algorithm (str): hashlib algorithm name (``"sha256"``,
                ``"sha512"``, ...).
            header_name (str): Header carrying the signature.
            encoding (str): ``"hex"`` (default) or ``"base64"``.
            prefix (str): Optional fixed prefix on the header value
                (e.g. ``"sha256="``). Stripped before comparison.
        """
        if encoding not in {"hex", "base64"}:
            raise ValueError("encoding must be 'hex' or 'base64'")
        if algorithm not in hashlib.algorithms_guaranteed:
            raise ValueError(f"unsupported hashlib algorithm: {algorithm}")
        self.secret: bytes = secret.encode() if isinstance(secret, str) else secret
        self.algorithm: str = algorithm
        self.header_name: str = header_name
        self.encoding: str = encoding
        self.prefix: str = prefix

    def expected(self, body: bytes) -> str:
        """Return the expected signature for ``body``.

        Args:
            body (bytes): The raw request body to sign.

        Returns:
            str: The signature encoded per :attr:`encoding`.
        """
        mac = hmac.new(self.secret, body, getattr(hashlib, self.algorithm))
        if self.encoding == "hex":
            return mac.hexdigest()
        return base64.b64encode(mac.digest()).decode("ascii")

    def verify(self, body: bytes, signature: str) -> bool:
        """Check ``signature`` against ``body`` in constant time.

        Args:
            body (bytes): The raw request body.
            signature (str): The provider-supplied signature
                (including any configured :attr:`prefix`).

        Returns:
            bool: ``True`` when the signature matches.
        """
        if self.prefix and signature.startswith(self.prefix):
            signature = signature[len(self.prefix) :]
        return hmac.compare_digest(self.expected(body), signature)

    def dependency(
        self,
        *,
        error_message: str = "Invalid webhook signature",
    ) -> Callable[..., Coroutine[Any, Any, bytes]]:
        """Build a FastAPI dependency that validates the inbound webhook.

        The returned coroutine reads the raw body, verifies the header
        signature, and returns the body bytes so the route handler can
        re-parse it without re-reading the stream.

        Args:
            error_message (str): Message attached to the raised
                :class:`UnauthorizedException` when verification fails.

        Returns:
            Callable[..., Coroutine[Any, Any, bytes]]: An async
            FastAPI dependency that yields the raw body on success
            and raises :class:`UnauthorizedException` on mismatch.
        """
        header_alias = self.header_name
        verifier = self

        async def _verify(
            request: Request,
            signature: str = Header(default="", alias=header_alias),
        ) -> bytes:
            body = await request.body()
            if not signature or not verifier.verify(body, signature):
                raise UnauthorizedException(message=error_message)
            return body

        _verify.__doc__ = (
            f"Validate the {header_alias} HMAC signature and return the body."
        )
        return _verify


class RSAWebhookSignatureVerifier:
    """Validate RSA-signed webhook payloads (OpenPix/Woovi-style).

    Some providers sign each webhook with their PRIVATE key and publish
    a well-known PUBLIC key; the receiver verifies the asymmetric
    signature over the raw body. This complements
    :class:`WebhookSignatureVerifier` (symmetric HMAC) for those
    gateways. Uses ``RSASSA-PKCS1-v1_5`` over a configurable hash.

    Requires ``cryptography`` (ships with the ``[webpush]`` extra, or
    ``pip install cryptography``); the import is deferred to first use.

    Attributes:
        public_key_pem (bytes): The PEM-encoded provider public key.
        algorithm (str): Hash algorithm name (``"sha256"`` default).
        header_name (str): Header carrying the base64 signature.
    """

    def __init__(
        self,
        public_key_pem: str | bytes,
        *,
        algorithm: str = "sha256",
        header_name: str = "x-webhook-signature",
    ) -> None:
        """Initialize the verifier.

        Args:
            public_key_pem (str | bytes): PEM-encoded RSA public key.
                Strings are encoded as UTF-8.
            algorithm (str): Hash algorithm — ``"sha256"`` (default),
                ``"sha384"`` or ``"sha512"``.
            header_name (str): Header carrying the base64 signature.

        Raises:
            ValueError: If ``algorithm`` is not a supported SHA-2 hash.
        """
        if algorithm not in {"sha256", "sha384", "sha512"}:
            raise ValueError(
                "algorithm must be one of sha256/sha384/sha512",
            )
        self.public_key_pem: bytes = (
            public_key_pem.encode("utf-8")
            if isinstance(public_key_pem, str)
            else public_key_pem
        )
        self.algorithm: str = algorithm
        self.header_name: str = header_name

    def verify(self, body: bytes, signature: str) -> bool:
        """Verify a base64 RSA signature over ``body`` in one shot.

        Args:
            body (bytes): The raw request body, exactly as received
                (never re-serialized — re-encoding changes the bytes
                and breaks the signature).
            signature (str): The base64-encoded signature header value.

        Returns:
            bool: ``True`` when the signature verifies against the
                configured public key, ``False`` on any failure
                (bad signature, malformed base64, unparseable key).

        Raises:
            ImportError: If ``cryptography`` is not installed.
        """
        try:
            from cryptography.exceptions import InvalidSignature
            from cryptography.hazmat.primitives import hashes, serialization
            from cryptography.hazmat.primitives.asymmetric import padding
            from cryptography.hazmat.primitives.asymmetric.rsa import (
                RSAPublicKey,
            )
        except ImportError as exc:  # pragma: no cover - guarded by extras
            raise ImportError(
                "RSAWebhookSignatureVerifier requires `cryptography`. "
                "Install with `pip install tempest-fastapi-sdk[webpush]` "
                "or `pip install cryptography`."
            ) from exc

        if not signature:
            return False

        hash_algorithm: hashes.HashAlgorithm = {
            "sha256": hashes.SHA256(),
            "sha384": hashes.SHA384(),
            "sha512": hashes.SHA512(),
        }[self.algorithm]

        try:
            public_key = serialization.load_pem_public_key(
                self.public_key_pem,
            )
            if not isinstance(public_key, RSAPublicKey):
                return False
            public_key.verify(
                base64.b64decode(signature),
                body,
                padding.PKCS1v15(),
                hash_algorithm,
            )
        except (InvalidSignature, ValueError, TypeError):
            return False
        return True

    def dependency(
        self,
        *,
        error_message: str = "Invalid webhook signature",
    ) -> Callable[..., Coroutine[Any, Any, bytes]]:
        """Build a FastAPI dependency that validates the inbound webhook.

        Reads the raw body, verifies the RSA signature header, and
        returns the body bytes so the route handler can re-parse it
        without re-reading the stream.

        Args:
            error_message (str): Message attached to the raised
                :class:`UnauthorizedException` when verification fails.

        Returns:
            Callable[..., Coroutine[Any, Any, bytes]]: An async FastAPI
            dependency yielding the raw body on success and raising
            :class:`UnauthorizedException` on mismatch.
        """
        header_alias = self.header_name
        verifier = self

        async def _verify(
            request: Request,
            signature: str = Header(default="", alias=header_alias),
        ) -> bytes:
            body = await request.body()
            if not verifier.verify(body, signature):
                raise UnauthorizedException(message=error_message)
            return body

        _verify.__doc__ = (
            f"Validate the {header_alias} RSA signature and return the body."
        )
        return _verify


@dataclass(frozen=True, slots=True)
class WebhookDelivery:
    """The outcome of one outbound webhook delivery attempt sequence.

    Attributes:
        url (str): The destination URL.
        event (str): The event type sent.
        delivered (bool): Whether a 2xx was received.
        status_code (int | None): The last HTTP status, or ``None`` when
            every attempt failed before a response (network error).
        attempts (int): How many POSTs were made.
        error (str | None): The last error message when not delivered.
        delivery_id (str): The unique id sent in the id header.
    """

    url: str
    event: str
    delivered: bool
    status_code: int | None
    attempts: int
    delivery_id: str
    error: str | None = None


class WebhookSender:
    """Sign and deliver outbound webhooks with bounded retries.

    The counterpart to :class:`WebhookSignatureVerifier`: it POSTs a JSON
    event to a subscriber URL, signs the exact body with the **same**
    verifier instance (so the receiver validates it with that verifier),
    and retries transient failures (connection errors, 5xx, 429) with
    exponential backoff. Client errors (other 4xx) are not retried — they
    won't succeed on repeat.

    The HTTP client is injected (an ``httpx.AsyncClient``), matching the
    SDK's other outbound helpers — the caller owns its lifecycle, pooling
    and base config.

    Example:
        ```python
        verifier = WebhookSignatureVerifier(secret, prefix="sha256=")
        sender = WebhookSender(http_client, signer=verifier)
        result = await sender.send(
            "https://sub.example.com/hooks",
            event="order.paid",
            payload={"id": str(order.id), "total": 4200},
        )
        if not result.delivered:
            ...  # enqueue for later / alert
        ```
    """

    def __init__(
        self,
        http_client: httpx.AsyncClient,
        *,
        signer: WebhookSignatureVerifier | None = None,
        max_attempts: int = 3,
        backoff_base: float = 0.5,
        timeout: float = 10.0,
        event_header: str = "X-Webhook-Event",
        id_header: str = "X-Webhook-Id",
        timestamp_header: str = "X-Webhook-Timestamp",
    ) -> None:
        """Initialize the sender.

        Args:
            http_client (httpx.AsyncClient): The injected async client
                used for every POST.
            signer (WebhookSignatureVerifier | None): When given, its
                ``expected(body)`` HMAC (with its ``prefix``) is written
                to its ``header_name`` so the receiver can verify with
                the same instance. ``None`` sends unsigned.
            max_attempts (int): Total POST attempts before giving up
                (``>= 1``). Defaults to 3.
            backoff_base (float): Base seconds for exponential backoff
                (``backoff_base * 2**(attempt-1)``). Defaults to 0.5.
            timeout (float): Per-request timeout in seconds.
            event_header (str): Header carrying the event type.
            id_header (str): Header carrying the unique delivery id.
            timestamp_header (str): Header carrying the unix timestamp.
        """
        self._client: httpx.AsyncClient = http_client
        self._signer: WebhookSignatureVerifier | None = signer
        self._max_attempts: int = max(1, max_attempts)
        self._backoff_base: float = backoff_base
        self._timeout: float = timeout
        self._event_header: str = event_header
        self._id_header: str = id_header
        self._timestamp_header: str = timestamp_header

    def _headers(self, event: str, body: bytes, delivery_id: str) -> dict[str, str]:
        """Build the request headers (content type, metadata, signature).

        Args:
            event (str): The event type.
            body (bytes): The exact serialized body being signed.
            delivery_id (str): The unique delivery id.

        Returns:
            dict[str, str]: The headers to send.
        """
        headers = {
            "content-type": "application/json",
            self._event_header: event,
            self._id_header: delivery_id,
            self._timestamp_header: str(int(time.time())),
        }
        if self._signer is not None:
            headers[self._signer.header_name] = (
                self._signer.prefix + self._signer.expected(body)
            )
        return headers

    async def send(
        self,
        url: str,
        *,
        event: str,
        payload: Any,
        headers: Mapping[str, str] | None = None,
    ) -> WebhookDelivery:
        """Deliver one signed webhook, retrying transient failures.

        Args:
            url (str): The subscriber URL.
            event (str): The event type (sent in the event header).
            payload (Any): JSON-serializable body (``default=str`` covers
                UUID/Decimal/datetime).
            headers (Mapping[str, str] | None): Extra headers merged in
                (they do not override the signature/metadata headers).

        Returns:
            WebhookDelivery: The outcome — ``delivered`` plus the last
            status, attempt count and error.
        """
        body = json.dumps(payload, default=str, separators=(",", ":")).encode()
        delivery_id = uuid.uuid4().hex
        request_headers = {**(headers or {}), **self._headers(event, body, delivery_id)}

        status_code: int | None = None
        error: str | None = None
        attempts = 0
        for attempt in range(1, self._max_attempts + 1):
            attempts = attempt
            try:
                response = await self._client.post(
                    url,
                    content=body,
                    headers=request_headers,
                    timeout=self._timeout,
                )
            except _TRANSIENT_ERRORS as exc:  # network-level failure
                status_code = None
                error = f"{type(exc).__name__}: {exc}"
            else:
                status_code = response.status_code
                if 200 <= status_code < 300:
                    return WebhookDelivery(
                        url=url,
                        event=event,
                        delivered=True,
                        status_code=status_code,
                        attempts=attempt,
                        delivery_id=delivery_id,
                    )
                error = f"HTTP {status_code}"
                # Only 5xx / 429 are worth retrying; other 4xx won't recover.
                if not (status_code >= 500 or status_code == 429):
                    break
            if attempt < self._max_attempts:
                await asyncio.sleep(self._backoff_base * 2 ** (attempt - 1))

        return WebhookDelivery(
            url=url,
            event=event,
            delivered=False,
            status_code=status_code,
            attempts=attempts,
            delivery_id=delivery_id,
            error=error,
        )

    async def send_many(
        self,
        deliveries: list[tuple[str, Any]],
        *,
        event: str,
    ) -> list[WebhookDelivery]:
        """Deliver the same event to many URLs concurrently.

        Args:
            deliveries (list[tuple[str, Any]]): ``(url, payload)`` pairs.
            event (str): The event type applied to every delivery.

        Returns:
            list[WebhookDelivery]: One result per input, in order.
        """
        return await asyncio.gather(
            *(
                self.send(url, event=event, payload=payload)
                for url, payload in deliveries
            )
        )


__all__: list[str] = [
    "RSAWebhookSignatureVerifier",
    "WebhookDelivery",
    "WebhookSender",
    "WebhookSignatureVerifier",
]
