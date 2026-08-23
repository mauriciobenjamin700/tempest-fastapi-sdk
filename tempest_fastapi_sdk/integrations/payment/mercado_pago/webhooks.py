"""Verifying that a notification really came from Mercado Pago.

!!! danger "Read this before trusting the verification"
    **The signature scheme here has not been measured against a real
    Mercado Pago delivery.** The vendored specification does not describe
    it: it has no ``webhooks`` section and no security scheme covering
    notifications, which is checkable —

    ```bash
    grep -c "x-signature" vendor/mercadopago-openapi.yaml   # 0
    ```

    What *is* measured is the HMAC itself: :func:`sign_manifest` and
    :func:`verify_signature` are exercised against each other and against
    tampered inputs, so the cryptography and the parsing are exercised. What
    is **not** exercised is whether the manifest below is byte-for-byte the
    one Mercado Pago signs.

    Before this guards production money, run one real notification through
    :func:`verify_signature` and confirm it accepts. If it rejects, the
    manifest template is what to fix — pass your own through
    ``manifest_template=``, and please report it so the default can be
    corrected here.

The manifest Mercado Pago documents is three ``key:value;`` pairs, in a
fixed order: the ``data.id`` from the query string, the ``x-request-id``
header, and the timestamp from the signature header itself.
"""

from __future__ import annotations

import hashlib
import hmac
from dataclasses import dataclass, field
from typing import Any, Final

MERCADO_PAGO_SIGNATURE_HEADER: Final[str] = "x-signature"
"""Header carrying the timestamp and the HMAC, as ``ts=...,v1=...``."""

MERCADO_PAGO_REQUEST_ID_HEADER: Final[str] = "x-request-id"
"""Header whose value is part of the signed manifest."""

DEFAULT_MANIFEST_TEMPLATE: Final[str] = "id:{data_id};request-id:{request_id};ts:{ts};"
"""The manifest template this module signs and verifies by default.

Exposed as a constant, and overridable per call, precisely because it is
the part that has not been confirmed against a live delivery. A caller who
measures a different manifest can use it without waiting for a release.
"""


@dataclass(frozen=True, slots=True)
class MercadoPagoWebhookEvent:
    """A verified Mercado Pago notification.

    A dataclass rather than a ``BaseSchema``: this is the value handed to a
    route, not a wire DTO. ``BaseSchema`` sets ``use_enum_values=True``,
    which would store :attr:`event` as a bare ``str`` — and then
    ``event.event is MercadoPagoEvent.PAYMENT`` would be silently ``False``
    on every delivery.

    Attributes:
        topic (str): The notification type exactly as delivered.
        event (MercadoPagoEvent | None): The parsed topic, or ``None`` when
            Mercado Pago sent one this SDK version does not name.
        data_id (str): The resource id the notification points at — what
            you re-read from the API before acting.
        payload (dict[str, Any]): The decoded body.
        body (bytes): The raw body, byte-for-byte, for logging or a second
            verification.
    """

    topic: str
    event: Any | None = None
    data_id: str = ""
    payload: dict[str, Any] = field(default_factory=dict)
    body: bytes = b""


def parse_signature_header(header: str) -> tuple[str, str]:
    """Split ``ts=...,v1=...`` into its two parts.

    Args:
        header (str): The ``x-signature`` value.

    Returns:
        tuple[str, str]: The timestamp and the hex digest. Either is an
        empty string when the header does not carry it, which
        :func:`verify_signature` then treats as a failed verification
        rather than an exception — a malformed header is an untrusted
        caller, not a bug.
    """
    timestamp = ""
    digest = ""
    for part in header.split(","):
        key, _, value = part.strip().partition("=")
        if key == "ts":
            timestamp = value.strip()
        elif key == "v1":
            digest = value.strip()
    return timestamp, digest


def sign_manifest(
    *,
    secret: str,
    data_id: str,
    request_id: str,
    timestamp: str,
    manifest_template: str = DEFAULT_MANIFEST_TEMPLATE,
) -> str:
    """Compute the HMAC-SHA256 digest of one manifest.

    Args:
        secret (str): The webhook secret from the Mercado Pago dashboard.
        data_id (str): The ``data.id`` query parameter of the delivery.
        request_id (str): The ``x-request-id`` header.
        timestamp (str): The ``ts`` component of ``x-signature``.
        manifest_template (str): The manifest layout, with ``{data_id}``,
            ``{request_id}`` and ``{ts}`` placeholders.

    Returns:
        str: The lowercase hex digest.

    Exposed publicly because a test — yours or this package's — needs to
    produce a valid signature without duplicating the manifest layout.
    """
    manifest = manifest_template.format(
        data_id=data_id, request_id=request_id, ts=timestamp
    )
    return hmac.new(
        secret.encode("utf-8"), manifest.encode("utf-8"), hashlib.sha256
    ).hexdigest()


def verify_signature(
    *,
    secret: str,
    signature_header: str,
    data_id: str,
    request_id: str,
    manifest_template: str = DEFAULT_MANIFEST_TEMPLATE,
) -> bool:
    """Check a delivery's signature.

    Args:
        secret (str): The webhook secret from the dashboard.
        signature_header (str): The raw ``x-signature`` value.
        data_id (str): The ``data.id`` query parameter.
        request_id (str): The ``x-request-id`` header.
        manifest_template (str): The manifest layout — override it if you
            have measured a different one.

    Returns:
        bool: Whether the digest matches. Comparison goes through
        :func:`hmac.compare_digest`, so it does not leak the correct digest
        one byte at a time through timing.

    An empty ``secret`` always returns ``False``. Treating "no secret
    configured" as "everything is valid" is how an unauthenticated endpoint
    ships to production believing it is protected.
    """
    if not secret:
        return False
    timestamp, digest = parse_signature_header(signature_header)
    if not timestamp or not digest:
        return False
    expected = sign_manifest(
        secret=secret,
        data_id=data_id,
        request_id=request_id,
        timestamp=timestamp,
        manifest_template=manifest_template,
    )
    return hmac.compare_digest(expected, digest)


__all__: list[str] = [
    "DEFAULT_MANIFEST_TEMPLATE",
    "MERCADO_PAGO_REQUEST_ID_HEADER",
    "MERCADO_PAGO_SIGNATURE_HEADER",
    "MercadoPagoWebhookEvent",
    "parse_signature_header",
    "sign_manifest",
    "verify_signature",
]
