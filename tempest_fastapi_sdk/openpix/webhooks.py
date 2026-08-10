"""Verify an OpenPix webhook and hand the route a typed event.

The SDK already ships :class:`RSAWebhookSignatureVerifier`, and the
generated integration already ships the event enum. Nobody ties the two
together, so every service that receives an OpenPix webhook re-derives the
same three facts: which header carries the signature, which public key
verifies it, and what the ``event`` string means. This module supplies all
three and returns a parsed event.
"""

from __future__ import annotations

import base64
import json
from collections.abc import Callable, Coroutine
from dataclasses import dataclass, field
from typing import Any

from fastapi import Request

from tempest_fastapi_sdk.api.webhooks import RSAWebhookSignatureVerifier
from tempest_fastapi_sdk.exceptions import UnauthorizedException
from tempest_fastapi_sdk.openpix.events import OpenPixEvent

OPENPIX_WEBHOOK_SIGNATURE_HEADER: str = "x-webhook-signature"
"""Header carrying the base64 RSA signature over the raw request body."""

OPENPIX_WEBHOOK_PUBLIC_KEY: str = """\
-----BEGIN PUBLIC KEY-----
MIGfMA0GCSqGSIb3DQEBAQUAA4GNADCBiQKBgQC/+NtIkjzevvqD+I3MMv3bLXDt
pvxBjY4BsRrSdca3rtAwMcRYYvxSnd7jagVLpctMiOxQO8ieUCKLSWHpsMAjO/zZ
WMKbqoG8MNpi/u3fp6zz0mcHCOSqYsPUUG19buW8bis5ZZ2IZgBObWSpTvJ0cnj6
HKBAA82Jln+lGwS1MwIDAQAB
-----END PUBLIC KEY-----
"""
"""OpenPix's published webhook public key, PEM-decoded.

The provider publishes it base64-encoded on the webhook-signature page; it
is stored decoded here so the PEM is readable and reviewable in the diff.
It is global to OpenPix, not per-account.

!!! warning "This key is RSA-1024"
    Verified on import into ``cryptography``: 1024 bits, exponent 65537.
    That is below the 2048-bit floor NIST has recommended since 2013, and
    it caps how much the signature can prove. **Treat a valid signature as
    evidence the delivery came from OpenPix, not as authorization to move
    money.** Before acting on a charge-completed event, re-read the charge
    from the API — the signature is a filter on inbound noise, the API read
    is the fact. Nothing here can raise the key's strength; the mitigation
    is not trusting it beyond what it is.
"""


@dataclass(frozen=True, slots=True)
class OpenPixWebhookEvent:
    """A verified OpenPix webhook delivery.

    A dataclass rather than a :class:`BaseSchema`: this is the value handed
    to a route, not a wire DTO. ``BaseSchema`` sets ``use_enum_values=True``,
    which would store :attr:`event` as a bare ``str`` — and then the obvious
    ``event.event is OpenPixEvent.CHARGE_COMPLETED`` is **always false**,
    silently, on every delivery. It also skips re-validating a payload that
    was just parsed from JSON.

    Attributes:
        event_name (str): The ``event`` field exactly as delivered, kept
            even when it is not a known value.
        event (OpenPixEvent | None): The parsed event, or ``None`` when
            OpenPix sent something this SDK version does not know.
        payload (dict[str, Any]): The whole decoded body. Deliberately not
            modelled here — the 358 generated schemas live in the consuming
            service, so the caller validates the branch they care about
            (``ChargeSchema.model_validate(event.payload["charge"])``).
        body (bytes): The raw body, byte-for-byte as received, for logging
            or a second verification.
    """

    event_name: str
    event: OpenPixEvent | None = None
    payload: dict[str, Any] = field(default_factory=dict)
    body: bytes = b""


def webhook_verifier(
    *,
    public_key_pem: str | bytes = OPENPIX_WEBHOOK_PUBLIC_KEY,
    header_name: str = OPENPIX_WEBHOOK_SIGNATURE_HEADER,
) -> RSAWebhookSignatureVerifier:
    """Build a verifier configured for OpenPix.

    Args:
        public_key_pem (str | bytes): PEM-encoded public key. Overridable
            so a caller can pin a rotated key without waiting for an SDK
            release — the provider can change it at any time and a hard
            constant would strand every consumer.
        header_name (str): Header carrying the signature.

    Returns:
        RSAWebhookSignatureVerifier: A verifier over ``sha256`` — the
        algorithm OpenPix documents (``sha256WithRSAEncryption``).

    This supplies constants rather than forwarding arguments: the key and
    the header name are the whole point, and they are what every consumer
    would otherwise copy out of a docs page by hand.
    """
    return RSAWebhookSignatureVerifier(
        public_key_pem,
        algorithm="sha256",
        header_name=header_name,
    )


def make_openpix_webhook_dependency(
    *,
    verifier: RSAWebhookSignatureVerifier | None = None,
    error_message: str = "Invalid OpenPix webhook signature",
) -> Callable[..., Coroutine[Any, Any, OpenPixWebhookEvent]]:
    """Build a FastAPI dependency yielding a verified, parsed event.

    Args:
        verifier (RSAWebhookSignatureVerifier | None): Verifier to use.
            Defaults to :func:`webhook_verifier`. Inject one to pin a
            rotated key, or a verifier over a test key pair in tests.
        error_message (str): Message on the raised
            :class:`UnauthorizedException`.

    Returns:
        Callable[..., Coroutine[Any, Any, OpenPixWebhookEvent]]: An async
        dependency that verifies the signature, decodes the body and
        returns an :class:`OpenPixWebhookEvent`.

    Raises:
        UnauthorizedException: Raised by the returned dependency when the
            signature is missing or does not verify.

    Two behaviours are deliberate. An **unrecognized event name** does not
    fail the request: OpenPix adds events, and a service that 500s on one
    it has never seen turns a provider's release into its own outage — the
    name is kept as ``event_name`` with ``event`` left ``None``. A body
    that is **not JSON** is also not an error here; it verified, so it came
    from OpenPix, and rejecting it would discard a delivery the provider
    considers sent. ``payload`` stays empty and ``body`` carries the bytes.
    """
    active = verifier if verifier is not None else webhook_verifier()

    async def dependency(request: Request) -> OpenPixWebhookEvent:
        """Verify and parse the inbound delivery.

        Args:
            request (Request): The inbound request.

        Returns:
            OpenPixWebhookEvent: The verified, decoded event.

        Raises:
            UnauthorizedException: If the signature is absent or invalid.
        """
        body = await request.body()
        signature = request.headers.get(active.header_name)
        if not signature or not active.verify(body, signature):
            raise UnauthorizedException(error_message)

        payload: dict[str, Any] = {}
        try:
            decoded = json.loads(body)
        except (ValueError, UnicodeDecodeError):
            decoded = None
        if isinstance(decoded, dict):
            payload = decoded

        event_name = str(payload.get("event", ""))
        known = OpenPixEvent.has_value(event_name)
        return OpenPixWebhookEvent(
            event_name=event_name,
            event=OpenPixEvent.from_value(event_name) if known else None,
            payload=payload,
            body=body,
        )

    return dependency


def decode_public_key(encoded: str) -> str:
    """Decode the base64 public key exactly as OpenPix publishes it.

    Args:
        encoded (str): The base64 blob copied from the provider's docs.

    Returns:
        str: The PEM text, ready for :func:`webhook_verifier`.

    Raises:
        ValueError: If the blob is not valid base64, or does not decode to
            a PEM public key — a truncated copy-paste otherwise fails much
            later, as a signature mismatch on a real delivery.

    The provider prints the key base64-encoded rather than as PEM, so
    rotating it means decoding a blob. Doing that at the call site is one
    line of ``base64`` that everyone writes slightly differently.
    """
    try:
        pem = base64.b64decode(encoded, validate=True).decode("utf-8")
    except (ValueError, UnicodeDecodeError) as error:
        raise ValueError(f"not a base64-encoded PEM key: {error}") from error
    if "-----BEGIN PUBLIC KEY-----" not in pem:
        raise ValueError("decoded value is not a PEM public key")
    return pem


__all__: list[str] = [
    "OPENPIX_WEBHOOK_PUBLIC_KEY",
    "OPENPIX_WEBHOOK_SIGNATURE_HEADER",
    "OpenPixWebhookEvent",
    "decode_public_key",
    "make_openpix_webhook_dependency",
    "webhook_verifier",
]
