"""Verifying that a notification really came from Mercado Pago.

The vendored specification does not describe the signature at all — it has no
``webhooks`` section and no security scheme covering notifications, which is
checkable —

```bash
grep -c "x-signature" vendor/mercadopago-openapi.yaml   # 0
```

So the algorithm here is **ported from Mercado Pago's own validator**:
``mercadopago/sdk-nodejs``, ``src/utils/webhook/index.ts`` at commit
``99857f33`` (2026-08-03), the module their documentation points integrators
at. Every rule below comes from that file or from its test suite, and each is
pinned by a test here, so a change upstream shows up as a failure rather than
as deliveries that quietly stop verifying.

The rules, in the order they bite:

- **The manifest omits absent pairs.** It is not a fixed template. Present
  values are joined with ``;`` and the whole string ends with one:
  ``id:<data.id>;request-id:<x-request-id>;ts:<ts>;``. A delivery without
  ``data.id`` signs ``request-id:...;ts:...;`` — with no empty ``id:`` in it.
  Until v0.250.0 this module rendered a fixed template, so every such
  delivery failed verification.
- **A whitespace-only value is an absent value.** Upstream trims and treats
  the empty result as missing, which changes which pairs the manifest has.
- **Header keys are case-insensitive.** ``TS=`` and ``V1=`` parse.
- **Several hash versions can travel in one header.** ``ts=..,v1=..,v2=..``
  is valid; the verifier takes the first version it supports, so a provider
  migration to ``v2`` is a parameter change rather than a release.
- **The timestamp must be all digits.** Anything else is a malformed header,
  not a mismatch to investigate.
- **HMAC-SHA256, hex, compared in constant time.**

!!! danger "Still not measured against a live delivery"
    Ported from the provider's implementation is not the same as verified
    against a notification the provider actually sent. What is measured here:
    the manifest strings, byte for byte, against the rules upstream encodes;
    and the digests, against vectors computed by ``openssl dgst -sha256
    -hmac`` — a different implementation than Python's :mod:`hmac`.

    What is still unmeasured: whether Mercado Pago's live deliveries follow
    their own SDK. Run one real notification through :func:`verify_signature`
    before this guards production money, and report a rejection.

!!! warning "QR Code notifications are not signed"
    Upstream states it outright: those deliveries carry no signature and will
    always fail verification. Do not route them through this module — gate
    them some other way.
"""

from __future__ import annotations

import hashlib
import hmac
import time
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, field
from typing import Any, Final

MERCADO_PAGO_SIGNATURE_HEADER: Final[str] = "x-signature"
"""Header carrying the timestamp and the HMAC, as ``ts=...,v1=...``."""

MERCADO_PAGO_REQUEST_ID_HEADER: Final[str] = "x-request-id"
"""Header whose value is part of the signed manifest."""

DEFAULT_SIGNATURE_VERSIONS: Final[tuple[str, ...]] = ("v1",)
"""Hash versions accepted by default, in preference order.

Upstream defaults to ``['v1']`` and iterates in order, so a header carrying
both ``v1`` and ``v2`` verifies against whichever the caller lists first.
Passing ``("v2", "v1")`` is how an integrator adopts a new version before
this package ships one.
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


@dataclass(frozen=True, slots=True)
class SignatureHeader:
    """The parsed ``x-signature`` value.

    Attributes:
        timestamp (str): The ``ts`` component, or ``""`` when the header
            carried none.
        hashes (Mapping[str, str]): Every ``vN`` component, keyed by the
            lowercase version. A header may carry more than one.
    """

    timestamp: str = ""
    hashes: Mapping[str, str] = field(default_factory=dict)

    def digest(
        self,
        versions: Sequence[str] = DEFAULT_SIGNATURE_VERSIONS,
    ) -> str:
        """Pick the digest for the first version present.

        Args:
            versions (Sequence[str]): Versions to try, in preference order.

        Returns:
            str: The hex digest, or ``""`` when the header carries none of
            them — which is what a provider migration to an unsupported
            version looks like from here.
        """
        for version in versions:
            found = self.hashes.get(version.lower())
            if found:
                return found
        return ""


def _normalize(value: str | None) -> str:
    """Trim a header or query value, treating a blank one as absent.

    Args:
        value (str | None): The raw value, possibly None or whitespace.

    Returns:
        str: The trimmed value, or ``""`` when there is nothing in it.

    Upstream normalizes before building the manifest, and the difference is
    not cosmetic: a value of ``"   "`` decides whether its pair appears in
    the signed string at all.
    """
    return value.strip() if value else ""


_MILLISECOND_FLOOR: Final[int] = 10**12
"""Above this, a timestamp is milliseconds rather than seconds.

``10**12`` seconds is the year 33658; ``10**12`` milliseconds is 2001. No
epoch value in between is ambiguous, which is what makes the magnitude a
safe discriminator.
"""


def _timestamp_seconds(timestamp: str) -> float:
    """Read the ``ts`` component as seconds since the epoch.

    Args:
        timestamp (str): The all-digit ``ts`` value.

    Returns:
        float: Seconds since the epoch.

    Upstream treats ``ts`` as seconds, and their own issue #458 was this
    exact confusion: comparing a seconds timestamp against a milliseconds
    clock rejected every valid delivery. Their test suite then pins a
    13-digit constant into the HMAC while using a 10-digit one for the
    tolerance check, so the provider's own artifacts disagree about the
    unit. Rather than pick one and reject the other, the unit is read off
    the magnitude.
    """
    value = int(timestamp)
    return value / 1000 if value >= _MILLISECOND_FLOOR else float(value)


def parse_signature_header(header: str) -> SignatureHeader:
    """Split ``ts=...,v1=...`` into its components.

    Args:
        header (str): The ``x-signature`` value.

    Returns:
        SignatureHeader: The timestamp and every version hash present. A
        malformed header yields empty components rather than an exception:
        it means an untrusted caller, not a bug.

    Keys are lowercased and values trimmed, matching upstream — ``TS=`` and
    ``V1=`` parse. Unknown keys are ignored, so a component the provider
    adds later does not break parsing.
    """
    timestamp = ""
    hashes: dict[str, str] = {}
    for part in header.split(","):
        key, separator, value = part.strip().partition("=")
        if not separator:
            continue
        name = key.strip().lower()
        found = value.strip()
        if not name or not found:
            continue
        if name == "ts":
            timestamp = found
        elif len(name) > 1 and name[0] == "v" and name[1:].isdigit():
            hashes[name] = found
    return SignatureHeader(timestamp=timestamp, hashes=hashes)


def build_manifest(
    *,
    data_id: str = "",
    request_id: str = "",
    timestamp: str,
) -> str:
    """Build the string Mercado Pago signs, omitting the pairs it omits.

    Args:
        data_id (str): The ``data.id`` query parameter of the delivery.
        request_id (str): The ``x-request-id`` header.
        timestamp (str): The ``ts`` component of ``x-signature``.

    Returns:
        str: The manifest — present pairs joined by ``;``, always ending in
        ``;``. With everything present that is
        ``id:<data_id>;request-id:<request_id>;ts:<ts>;``; with no
        ``data.id`` it is ``request-id:<request_id>;ts:<ts>;``, and the
        empty ``id:`` is **not** there.

    The omission rule is the whole reason this function exists instead of a
    format string: a fixed template turns an absent ``data.id`` into
    ``id:;``, which hashes to something the provider never signed. Ported
    from ``mercadopago/sdk-nodejs``, ``src/utils/webhook/index.ts``
    (``buildManifest``) at commit ``99857f33``.
    """
    parts: list[str] = []
    if _normalize(data_id):
        parts.append(f"id:{_normalize(data_id)}")
    if _normalize(request_id):
        parts.append(f"request-id:{_normalize(request_id)}")
    parts.append(f"ts:{timestamp}")
    return ";".join(parts) + ";"


def sign_manifest(
    *,
    secret: str,
    data_id: str = "",
    request_id: str = "",
    timestamp: str,
) -> str:
    """Compute the HMAC-SHA256 digest of one delivery's manifest.

    Args:
        secret (str): The webhook secret from the Mercado Pago dashboard.
        data_id (str): The ``data.id`` query parameter of the delivery.
        request_id (str): The ``x-request-id`` header.
        timestamp (str): The ``ts`` component of ``x-signature``.

    Returns:
        str: The lowercase hex digest.

    Exposed publicly because a test — yours or this package's — needs to
    produce a valid signature without duplicating the manifest rules.
    """
    manifest = build_manifest(
        data_id=data_id,
        request_id=request_id,
        timestamp=timestamp,
    )
    return hmac.new(
        secret.encode("utf-8"), manifest.encode("utf-8"), hashlib.sha256
    ).hexdigest()


def verify_signature(
    *,
    secret: str,
    signature_header: str,
    data_id: str = "",
    request_id: str = "",
    versions: Sequence[str] = DEFAULT_SIGNATURE_VERSIONS,
    tolerance_seconds: float | None = None,
    now: Callable[[], float] | None = None,
) -> bool:
    """Check a delivery's signature.

    Args:
        secret (str): The webhook secret from the dashboard.
        signature_header (str): The raw ``x-signature`` value.
        data_id (str): The ``data.id`` query parameter. Absent for the
            topics that carry none, and then it is left out of the manifest.
        request_id (str): The ``x-request-id`` header, same rule.
        versions (Sequence[str]): Hash versions to accept, in preference
            order. Defaults to :data:`DEFAULT_SIGNATURE_VERSIONS`.
        tolerance_seconds (float | None): Maximum drift between ``ts`` and
            the clock. ``None`` — the default — skips the check.
        now (Callable[[], float] | None): Clock for the tolerance check, in
            **seconds** since the epoch. Defaults to :func:`time.time`;
            override it in tests, never in production. The ``ts`` it is
            compared against is read as seconds or milliseconds by
            magnitude, because the provider's own artifacts disagree about
            the unit.

    Returns:
        bool: Whether the delivery verifies. Comparison goes through
        :func:`hmac.compare_digest`, so it does not leak the correct digest
        one byte at a time through timing.

    A ``False`` here covers every rejection: no secret, a malformed header,
    a timestamp that is not all digits, no hash for a supported version, a
    digest that does not match, or drift past ``tolerance_seconds``. The
    reason is deliberately not returned — a route that branches on *why* a
    signature failed tends to grow a path that accepts one of them.

    An empty ``secret`` always returns ``False``. Treating "no secret
    configured" as "everything is valid" is how an unauthenticated endpoint
    ships to production believing it is protected.

    !!! tip "Turn the tolerance on"
        Without it, a delivery captured off the wire verifies forever: the
        signature covers a timestamp nobody is checking. Upstream leaves the
        window opt-in and so does this, but a value like ``300.0`` is what
        makes the ``ts`` in the manifest do any work.
    """
    if not secret:
        return False
    parsed = parse_signature_header(signature_header)
    if not parsed.timestamp or not parsed.timestamp.isdigit():
        return False
    digest = parsed.digest(versions)
    if not digest:
        return False
    expected = sign_manifest(
        secret=secret,
        data_id=data_id,
        request_id=request_id,
        timestamp=parsed.timestamp,
    )
    if not hmac.compare_digest(expected, digest):
        return False
    if tolerance_seconds is None:
        return True
    clock = now or time.time
    return abs(clock() - _timestamp_seconds(parsed.timestamp)) <= tolerance_seconds


__all__: list[str] = [
    "DEFAULT_SIGNATURE_VERSIONS",
    "MERCADO_PAGO_REQUEST_ID_HEADER",
    "MERCADO_PAGO_SIGNATURE_HEADER",
    "MercadoPagoWebhookEvent",
    "SignatureHeader",
    "build_manifest",
    "parse_signature_header",
    "sign_manifest",
    "verify_signature",
]
