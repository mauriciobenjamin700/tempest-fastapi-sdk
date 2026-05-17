"""HMAC signature verification for inbound webhooks."""

from __future__ import annotations

import base64
import hashlib
import hmac
from collections.abc import Callable, Coroutine
from typing import Any

from fastapi import Header, Request

from tempest_fastapi_sdk.exceptions.unauthorized import UnauthorizedException


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
            signature = signature[len(self.prefix):]
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


__all__: list[str] = [
    "WebhookSignatureVerifier",
]
