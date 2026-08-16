"""Verify a Stripe webhook and hand the route a typed event.

Stripe signs the delivery with the endpoint's signing secret
(``whsec_...``) and ships the result in one header:

.. code-block:: text

    Stripe-Signature: t=1737052800,v1=5257a869e7ecebeda32affa62cdca3fa...

The signed payload is **not** the body — it is ``f"{t}.{body}"``. That
detail is the whole reason this module exists: computing the HMAC over the
body alone produces a signature that never matches, and every service
rediscovers it by staring at a 401.

The timestamp is also load-bearing. Without a tolerance window, a valid
delivery captured off the wire can be replayed forever, so a delivery
older than :data:`DEFAULT_TOLERANCE_SECONDS` is rejected even when the
signature is perfect.
"""

from __future__ import annotations

import hmac
import json
import time
from collections.abc import Callable, Coroutine
from dataclasses import dataclass, field
from typing import Any

from fastapi import Request

from tempest_fastapi_sdk.api.webhooks import WebhookSignatureVerifier
from tempest_fastapi_sdk.exceptions import UnauthorizedException
from tempest_fastapi_sdk.integrations.payment.stripe.events import StripeEvent

STRIPE_SIGNATURE_HEADER: str = "Stripe-Signature"
"""Header carrying the timestamp and one signature per active secret."""

DEFAULT_TOLERANCE_SECONDS: int = 300
"""How old a delivery may be and still be accepted.

Five minutes, the value Stripe's own libraries use. It bounds replay of a
captured delivery and absorbs ordinary clock drift between their servers
and yours.
"""


@dataclass(frozen=True, slots=True)
class StripeWebhookEvent:
    """A verified Stripe webhook delivery.

    A dataclass rather than a :class:`BaseSchema`: this is the value a
    route receives, not a wire DTO. ``BaseSchema`` sets
    ``use_enum_values=True``, which would store :attr:`event` as a bare
    ``str`` — and then ``delivery.event is StripeEvent.PAYMENT_INTENT_SUCCEEDED``
    is silently always false.

    Attributes:
        event_type (str): The ``type`` field exactly as delivered, kept
            even when this SDK release does not know it.
        event (StripeEvent | None): The parsed event, or ``None`` for a
            type added after this release.
        event_id (str): Stripe's ``evt_...`` id. Use it to deduplicate:
            Stripe retries a delivery until you answer 2xx, so the same
            event id arrives more than once by design.
        payload (dict[str, Any]): The whole decoded body.
        data_object (dict[str, Any]): ``data.object`` — the resource the
            event is about, which is what handlers actually read.
        body (bytes): The raw body, byte-for-byte, for logging or a second
            verification.
    """

    event_type: str
    event: StripeEvent | None = None
    event_id: str = ""
    payload: dict[str, Any] = field(default_factory=dict)
    data_object: dict[str, Any] = field(default_factory=dict)
    body: bytes = b""


def parse_signature_header(header: str) -> tuple[int | None, tuple[str, ...]]:
    """Split a ``Stripe-Signature`` header into timestamp and signatures.

    Args:
        header (str): The raw header value.

    Returns:
        tuple[int | None, tuple[str, ...]]: The ``t`` value as an integer
        (``None`` when absent or unparseable) and every ``v1`` signature.
        There can be more than one during a secret rotation, and accepting
        any of them is what makes rotation possible without dropping
        deliveries.
    """
    timestamp: int | None = None
    signatures: list[str] = []
    for part in header.split(","):
        key, _, value = part.strip().partition("=")
        if key == "t":
            try:
                timestamp = int(value)
            except ValueError:
                timestamp = None
        elif key == "v1":
            signatures.append(value)
    return timestamp, tuple(signatures)


def verify_signature(
    body: bytes,
    header: str,
    secret: str,
    *,
    tolerance_seconds: int = DEFAULT_TOLERANCE_SECONDS,
    now: float | None = None,
) -> bool:
    """Check a Stripe delivery against the endpoint's signing secret.

    Args:
        body (bytes): The raw request body, exactly as received. Any
            re-serialization changes the bytes and breaks the signature.
        header (str): The ``Stripe-Signature`` header value.
        secret (str): The endpoint signing secret (``whsec_...``).
        tolerance_seconds (int): Reject deliveries whose timestamp is
            further than this from ``now``. ``0`` disables the window,
            which also disables replay protection.
        now (float | None): Current epoch seconds. Injectable so tests
            pin time instead of sleeping.

    Returns:
        bool: ``True`` when the timestamp is inside the window and at
        least one ``v1`` signature matches, compared in constant time.

    The HMAC covers ``f"{t}.{body}"``, not the body — this is the
    documented Stripe scheme and the single most common mistake in a
    hand-rolled verification.
    """
    timestamp, signatures = parse_signature_header(header)
    if timestamp is None or not signatures:
        return False
    if tolerance_seconds > 0:
        current = time.time() if now is None else now
        if abs(current - timestamp) > tolerance_seconds:
            return False

    verifier = WebhookSignatureVerifier(
        secret,
        algorithm="sha256",
        header_name=STRIPE_SIGNATURE_HEADER,
    )
    expected = verifier.expected(f"{timestamp}.".encode() + body)
    return any(hmac.compare_digest(expected, candidate) for candidate in signatures)


def sign_payload(body: bytes, secret: str, *, timestamp: int) -> str:
    """Build the header Stripe would send for a body.

    Args:
        body (bytes): The raw body to sign.
        secret (str): The endpoint signing secret.
        timestamp (int): Epoch seconds to stamp the delivery with.

    Returns:
        str: A ``Stripe-Signature`` header value.

    Shipped rather than left to each test suite: writing this by hand is
    exactly where the ``f"{t}.{body}"`` detail gets re-derived wrong, and a
    test that signs the wrong thing passes against a verifier that checks
    the wrong thing.
    """
    verifier = WebhookSignatureVerifier(secret, algorithm="sha256")
    signature = verifier.expected(f"{timestamp}.".encode() + body)
    return f"t={timestamp},v1={signature}"


def parse_event(body: bytes) -> StripeWebhookEvent:
    """Decode a verified body into a typed event.

    Args:
        body (bytes): The raw, already-verified body.

    Returns:
        StripeWebhookEvent: The parsed delivery. A body that is not JSON
        yields an event with empty fields rather than raising — it
        verified, so it came from Stripe, and discarding a delivery the
        provider considers sent is worse than handling an empty one.
    """
    payload: dict[str, Any] = {}
    try:
        decoded = json.loads(body)
    except (ValueError, UnicodeDecodeError):
        decoded = None
    if isinstance(decoded, dict):
        payload = decoded

    data = payload.get("data")
    data_object: dict[str, Any] = {}
    if isinstance(data, dict) and isinstance(data.get("object"), dict):
        data_object = data["object"]

    event_type = str(payload.get("type", ""))
    known = StripeEvent.has_value(event_type)
    return StripeWebhookEvent(
        event_type=event_type,
        event=StripeEvent.from_value(event_type) if known else None,
        event_id=str(payload.get("id", "")),
        payload=payload,
        data_object=data_object,
        body=body,
    )


def make_stripe_webhook_dependency(
    secret: str,
    *,
    tolerance_seconds: int = DEFAULT_TOLERANCE_SECONDS,
    error_message: str = "Invalid Stripe webhook signature",
) -> Callable[..., Coroutine[Any, Any, StripeWebhookEvent]]:
    """Build a FastAPI dependency yielding a verified, parsed event.

    Args:
        secret (str): The endpoint signing secret (``whsec_...``). It is
            **per endpoint**, not per account: a service with a test and a
            live endpoint has two.
        tolerance_seconds (int): Replay window, in seconds.
        error_message (str): Message on the raised
            :class:`UnauthorizedException`.

    Returns:
        Callable[..., Coroutine[Any, Any, StripeWebhookEvent]]: An async
        dependency returning a :class:`StripeWebhookEvent`.

    Raises:
        UnauthorizedException: Raised by the returned dependency when the
            header is missing, malformed, outside the tolerance window, or
            signed with another secret.

    An **unknown event type** does not fail the request: Stripe adds event
    types continuously, and a service that 500s on one it has never seen
    turns their release into its own outage. The type is kept as
    ``event_type`` with ``event`` left ``None``.
    """

    async def dependency(request: Request) -> StripeWebhookEvent:
        """Verify and parse the inbound delivery.

        Args:
            request (Request): The inbound request.

        Returns:
            StripeWebhookEvent: The verified, decoded event.

        Raises:
            UnauthorizedException: If the signature is absent or invalid.
        """
        body = await request.body()
        header = request.headers.get(STRIPE_SIGNATURE_HEADER, "")
        if not header or not verify_signature(
            body, header, secret, tolerance_seconds=tolerance_seconds
        ):
            raise UnauthorizedException(error_message)
        return parse_event(body)

    return dependency


__all__: list[str] = [
    "DEFAULT_TOLERANCE_SECONDS",
    "STRIPE_SIGNATURE_HEADER",
    "StripeWebhookEvent",
    "make_stripe_webhook_dependency",
    "parse_event",
    "parse_signature_header",
    "sign_payload",
    "verify_signature",
]
