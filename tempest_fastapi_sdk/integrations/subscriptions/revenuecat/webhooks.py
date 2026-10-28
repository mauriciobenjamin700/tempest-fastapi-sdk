"""Authenticate a RevenueCat webhook and record it in the inbox.

RevenueCat offers two ways for your server to know a delivery is theirs,
both described on https://www.revenuecat.com/docs/integrations/webhooks:

* **An ``Authorization`` header value** you type in the dashboard. RevenueCat
  "will send this header in every request", and your server "should verify
  the validity of the authorization header for every notification". It is a
  shared secret, not a signature: it proves who sent the request, not that
  the body is intact.
* **HMAC signing**, opt-in per integration: an
  ``X-RevenueCat-Webhook-Signature: t=<unix_timestamp>,v1=<hmac_sha256_hex>``
  header, the HMAC computed over ``"<timestamp>.<raw_json_body>"``. ``t`` is
  recomputed on every attempt, retries included, so the documentation's
  5-minute tolerance "only needs to cover clock skew and the latency of that
  POST".

:func:`make_revenuecat_webhook_dependency` checks whichever you configure —
both, when both are set. A secret counts as configured when it resolves to a
non-empty value for the request at hand, so an empty
``REVENUECAT_WEBHOOK_SECRET`` next to a filled signing secret means "HMAC
only", exactly as
:meth:`~tempest_fastapi_sdk.settings.RevenueCatSettings.production_violations`
reads it. The comparison is constant time (:func:`hmac.compare_digest`) and
**when neither secret resolves to a value, every delivery is refused**: an
unset environment variable must not turn the endpoint into one that accepts
anything.

The same page says RevenueCat retries a non-``200`` answer "up to 5 times"
(after 5, 10, 20, 40 and 80 minutes), disconnects after 60 seconds, and
recommends deferring processing until after the response. It also says the
same event can arrive more than once and to "keep track of the event ``id``".
:func:`make_revenuecat_webhook_router` does exactly that: it records the event
in a :class:`~tempest_fastapi_sdk.db.WebhookInbox` keyed by ``event.id`` and
answers ``200`` before any business logic runs.

None of the RevenueCat behaviour above was measured against a live account:
it is what the documentation states, at the URL cited.
"""

from __future__ import annotations

import hmac
import json
import logging
import time
from collections.abc import Awaitable, Callable, Coroutine
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any
from uuid import UUID

from fastapi import APIRouter, Depends, Request
from pydantic import BaseModel, ValidationError

from tempest_fastapi_sdk.api.route_docs import RouteDocs
from tempest_fastapi_sdk.api.webhooks import WebhookSignatureVerifier
from tempest_fastapi_sdk.exceptions import UnauthorizedException, ValidationException
from tempest_fastapi_sdk.integrations.subscriptions.revenuecat.events import (
    RevenueCatEventTypeEnum,
)
from tempest_fastapi_sdk.integrations.subscriptions.revenuecat.schemas import (
    RevenueCatWebhook,
)

if TYPE_CHECKING:
    from tempest_fastapi_sdk.db.webhook_inbox import WebhookInbox

logger = logging.getLogger("tempest_fastapi_sdk.integrations.subscriptions.revenuecat")

REVENUECAT_PROVIDER: str = "revenuecat"
"""``provider`` value the router records events under in the inbox."""

REVENUECAT_AUTHORIZATION_HEADER: str = "Authorization"
"""Header carrying the value configured in the RevenueCat dashboard."""

REVENUECAT_SIGNATURE_HEADER: str = "X-RevenueCat-Webhook-Signature"
"""Header carrying ``t=<timestamp>,v1=<hex>`` when HMAC signing is on."""

DEFAULT_SIGNATURE_TOLERANCE_SECONDS: int = 300
"""Accepted distance between ``t`` and now: the documentation's 5 minutes."""

SecretSource = str | Callable[[Request], str]
"""A secret, or a function reading it per request (rotation, multi-tenant)."""


def verify_authorization(header: str, secret: str) -> bool:
    """Check the ``Authorization`` header against the configured value.

    Accepts the value with or without a ``Bearer `` prefix, on either side:
    the dashboard field is free text, and services store both spellings.

    Args:
        header (str): The received header value.
        secret (str): The value configured in the dashboard.

    Returns:
        bool: ``True`` only when ``secret`` is non-empty and the header
        matches it, compared in constant time.
    """
    expected = _without_bearer(secret)
    if not expected:
        return False
    return hmac.compare_digest(_without_bearer(header).encode(), expected.encode())


def _without_bearer(value: str) -> str:
    """Strip whitespace and one leading ``Bearer `` (any case).

    Applied to both sides, so a secret stored with or without the prefix
    matches a header sent with or without it.

    Args:
        value (str): A header value or a configured secret.

    Returns:
        str: The bare credential.
    """
    parts = value.split(maxsplit=1)
    if parts and parts[0].lower() == "bearer":
        return parts[1].strip() if len(parts) == 2 else ""
    return value.strip()


def parse_signature_header(header: str) -> tuple[int | None, tuple[str, ...]]:
    """Split ``X-RevenueCat-Webhook-Signature`` into timestamp and signatures.

    Args:
        header (str): The raw header value.

    Returns:
        tuple[int | None, tuple[str, ...]]: ``t`` as an integer (``None``
        when absent or not a number) and every ``v1`` value.
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
        elif key == "v1" and value:
            signatures.append(value)
    return timestamp, tuple(signatures)


def sign_payload(body: bytes, secret: str, *, timestamp: int) -> str:
    """Build the signature header RevenueCat would send for ``body``.

    For tests: a suite that signs by hand re-derives the
    ``"<timestamp>.<body>"`` detail, and a test that signs the wrong thing
    passes against a verifier that checks the wrong thing.

    Args:
        body (bytes): The raw body.
        secret (str): The integration's signing secret.
        timestamp (int): Epoch seconds.

    Returns:
        str: A ``t=...,v1=...`` header value.
    """
    verifier = WebhookSignatureVerifier(secret, algorithm="sha256")
    return f"t={timestamp},v1={verifier.expected(f'{timestamp}.'.encode() + body)}"


def verify_signature(
    body: bytes,
    header: str,
    secret: str,
    *,
    tolerance_seconds: int = DEFAULT_SIGNATURE_TOLERANCE_SECONDS,
    now: float | None = None,
) -> bool:
    """Check an HMAC-signed RevenueCat delivery.

    Args:
        body (bytes): The raw body, exactly as received. Re-serialized JSON
            changes the bytes and fails, as the documentation warns.
        header (str): The ``X-RevenueCat-Webhook-Signature`` value.
        secret (str): The integration's signing secret.
        tolerance_seconds (int): Maximum ``abs(now - t)``. ``0`` disables the
            window, and with it the replay protection.
        now (float | None): Current epoch seconds; injectable for tests.

    Returns:
        bool: ``True`` when ``secret`` is non-empty, ``t`` is inside the
        window and a ``v1`` matches in constant time.
    """
    if not secret:
        return False
    timestamp, signatures = parse_signature_header(header)
    if timestamp is None or not signatures:
        return False
    if tolerance_seconds > 0:
        current = time.time() if now is None else now
        if abs(current - timestamp) > tolerance_seconds:
            return False
    verifier = WebhookSignatureVerifier(secret, algorithm="sha256")
    expected = verifier.expected(f"{timestamp}.".encode() + body)
    return any(hmac.compare_digest(expected, candidate) for candidate in signatures)


def parse_webhook(body: bytes) -> RevenueCatWebhook:
    """Decode an authenticated body into :class:`RevenueCatWebhook`.

    Args:
        body (bytes): The raw body.

    Returns:
        RevenueCatWebhook: The parsed body.

    Raises:
        ValidationException: (422) When the body is not JSON or has no
            ``event.id`` / ``event.type`` — without an id there is nothing to
            deduplicate on, and RevenueCat will retry the delivery.
    """
    try:
        return RevenueCatWebhook.model_validate(json.loads(body))
    except (ValueError, UnicodeDecodeError, ValidationError) as exc:
        raise ValidationException(
            "RevenueCat webhook body is not a valid event"
        ) from exc


@dataclass(frozen=True, slots=True)
class RevenueCatWebhookDelivery:
    """An authenticated, parsed RevenueCat delivery.

    Attributes:
        webhook (RevenueCatWebhook): The parsed body.
        event_type (RevenueCatEventTypeEnum | None): The type as an enum
            member, or ``None`` for a type this release does not know.
        body (bytes): The raw body, byte for byte.
    """

    webhook: RevenueCatWebhook
    event_type: RevenueCatEventTypeEnum | None
    body: bytes


def _resolve(source: SecretSource, request: Request) -> str:
    """Read a secret that may be a function of the request.

    Args:
        source (SecretSource): The configured secret or reader.
        request (Request): The inbound request.

    Returns:
        str: The secret for this request.
    """
    return source(request) if callable(source) else source


def _is_configured(secret: str) -> bool:
    """Tell whether a resolved secret holds a value.

    The single rule both checks share: a secret is configured when, with
    whitespace and an optional ``Bearer `` prefix removed, something is
    left. ``""``, ``"  "`` and ``"Bearer "`` are all unconfigured. The prefix
    never appears in a signing secret, so stripping it there changes nothing.

    Args:
        secret (str): The secret resolved for this request.

    Returns:
        bool: ``True`` when the secret holds a value.
    """
    return bool(_without_bearer(secret))


def make_revenuecat_webhook_dependency(
    secret: SecretSource | None,
    *,
    signing_secret: SecretSource | None = None,
    tolerance_seconds: int = DEFAULT_SIGNATURE_TOLERANCE_SECONDS,
    error_message: str = "Invalid RevenueCat webhook credentials",
) -> Callable[..., Coroutine[Any, Any, RevenueCatWebhookDelivery]]:
    """Build a FastAPI dependency that authenticates and parses a delivery.

    Each secret is resolved per request, and every one that resolves to a
    value must pass. So ``secret=""`` with a filled ``signing_secret`` is
    HMAC only, and the reverse is ``Authorization`` only — the reading
    :class:`~tempest_fastapi_sdk.settings.RevenueCatSettings` applies at boot.
    When neither resolves to a value the request is refused: emptiness fails
    closed, it never opens the endpoint.

    Args:
        secret (SecretSource | None): The ``Authorization`` value configured
            in the dashboard, or a function returning it per request.
            ``None`` or a value that resolves to empty skips this check.
        signing_secret (SecretSource | None): The HMAC signing secret, when
            signing is enabled on the integration. ``None`` or a value that
            resolves to empty skips this check.
        tolerance_seconds (int): Signature timestamp window, in seconds.
        error_message (str): Message of the 401.

    Returns:
        Callable[..., Coroutine[Any, Any, RevenueCatWebhookDelivery]]: The
        async dependency.

    Raises:
        ValueError: When both ``secret`` and ``signing_secret`` are
            ``None`` — that endpoint would accept anyone.
        UnauthorizedException: (401, from the dependency) When a configured
            check fails (header missing or wrong), or when neither secret
            resolves to a value for this request.
        ValidationException: (422, from the dependency) When an
            authenticated body is not a valid event.
    """
    if secret is None and signing_secret is None:
        raise ValueError(
            "make_revenuecat_webhook_dependency needs secret, signing_secret "
            "or both; with neither, the endpoint would accept any request"
        )

    async def dependency(request: Request) -> RevenueCatWebhookDelivery:
        """Authenticate and parse the inbound delivery.

        Args:
            request (Request): The inbound request.

        Returns:
            RevenueCatWebhookDelivery: The parsed delivery.

        Raises:
            UnauthorizedException: If a configured check fails.
            ValidationException: If the body is not a valid event.
        """
        body = await request.body()
        authorization = "" if secret is None else _resolve(secret, request)
        signing = "" if signing_secret is None else _resolve(signing_secret, request)
        checks_authorization = _is_configured(authorization)
        checks_signature = _is_configured(signing)
        if not checks_authorization and not checks_signature:
            raise UnauthorizedException(error_message)
        if checks_authorization and not verify_authorization(
            request.headers.get(REVENUECAT_AUTHORIZATION_HEADER, ""),
            authorization,
        ):
            raise UnauthorizedException(error_message)
        if checks_signature and not verify_signature(
            body,
            request.headers.get(REVENUECAT_SIGNATURE_HEADER, ""),
            signing,
            tolerance_seconds=tolerance_seconds,
        ):
            raise UnauthorizedException(error_message)
        webhook = parse_webhook(body)
        kind = webhook.event.type
        return RevenueCatWebhookDelivery(
            webhook=webhook,
            event_type=(
                RevenueCatEventTypeEnum.from_value(kind)
                if RevenueCatEventTypeEnum.has_value(kind)
                else None
            ),
            body=body,
        )

    return dependency


class RevenueCatWebhookAck(BaseModel):
    """What the router answers RevenueCat.

    Attributes:
        recorded (bool): ``True`` for the first delivery of this event,
            ``False`` for a repeat (still a ``200``: RevenueCat only needs to
            stop retrying).
        event_id (str): RevenueCat's event id.
    """

    recorded: bool
    event_id: str


def make_revenuecat_webhook_router(
    *,
    inbox: WebhookInbox[Any],
    secret: SecretSource | None,
    signing_secret: SecretSource | None = None,
    on_recorded: Callable[[UUID], Awaitable[object]] | None = None,
    path: str = "/webhooks/revenuecat",
    tags: list[str] | None = None,
    include_in_schema: bool = True,
    tolerance_seconds: int = DEFAULT_SIGNATURE_TOLERANCE_SECONDS,
    docs_locale: str | None = None,
) -> APIRouter:
    """Mount the whole RevenueCat webhook: authenticate, record, answer 200.

    The route does no business logic. It records the event in ``inbox``
    under ``(provider="revenuecat", external_id=event.id)`` with the body's
    ``model_dump`` — which leaves ``subscriber_attributes`` out — and answers
    ``200``. A repeated ``event.id`` records nothing and still answers
    ``200``. Processing happens later, through
    :meth:`~tempest_fastapi_sdk.db.WebhookInbox.process` or the scheduled
    :meth:`~tempest_fastapi_sdk.db.WebhookInbox.drain_once`.

    Args:
        inbox (WebhookInbox[Any]): Where events are recorded.
        secret (SecretSource | None): The ``Authorization`` value; see
            :func:`make_revenuecat_webhook_dependency`.
        signing_secret (SecretSource | None): The HMAC signing secret.
        on_recorded (Callable[[UUID], Awaitable[object]] | None): Awaited
            with the inbox id of a **newly** recorded event — enqueue the
            processing task here. Not called for a repeat. If it raises, the
            event is already recorded and the sweep picks it up, so the route
            still answers ``200``.
        path (str): Route path.
        tags (list[str] | None): OpenAPI tags.
        include_in_schema (bool): Whether the route appears in OpenAPI.
        tolerance_seconds (int): Signature timestamp window.
        docs_locale (str | None): Language of the OpenAPI ``summary``
            and ``description`` text — ``"en-US"`` or ``"pt-BR"``.
            ``None`` (default) reads ``API_DOCS_LOCALE`` through
            :class:`~tempest_fastapi_sdk.settings.ServerSettings`,
            whose default is ``"en-US"``.

    Returns:
        APIRouter: Ready for ``app.include_router``.

    Raises:
        ValueError: When both ``secret`` and ``signing_secret`` are ``None``.
    """
    verify = make_revenuecat_webhook_dependency(
        secret,
        signing_secret=signing_secret,
        tolerance_seconds=tolerance_seconds,
    )
    route_docs: RouteDocs = RouteDocs(docs_locale)
    router = APIRouter(tags=list(tags) if tags is not None else [])

    @router.post(
        path,
        response_model=RevenueCatWebhookAck,
        include_in_schema=include_in_schema,
        summary=route_docs.summary("revenuecat.receive_revenuecat_webhook"),
        description=route_docs.description("revenuecat.receive_revenuecat_webhook"),
    )
    async def receive_revenuecat_webhook(
        delivery: RevenueCatWebhookDelivery = Depends(verify),
    ) -> RevenueCatWebhookAck:
        """Record the delivery and acknowledge it.

        Args:
            delivery (RevenueCatWebhookDelivery): The authenticated delivery.

        Returns:
            RevenueCatWebhookAck: Whether this delivery was new.
        """
        event = delivery.webhook.event
        inbox_id = await inbox.record(
            REVENUECAT_PROVIDER,
            event.id,
            event.type,
            delivery.webhook.model_dump(mode="json", exclude_unset=True),
        )
        if inbox_id is not None and on_recorded is not None:
            try:
                await on_recorded(inbox_id)
            except Exception:
                logger.exception(
                    "on_recorded failed for RevenueCat event %s; "
                    "the inbox sweep will process it",
                    event.id,
                )
        return RevenueCatWebhookAck(recorded=inbox_id is not None, event_id=event.id)

    return router


__all__: list[str] = [
    "DEFAULT_SIGNATURE_TOLERANCE_SECONDS",
    "REVENUECAT_AUTHORIZATION_HEADER",
    "REVENUECAT_PROVIDER",
    "REVENUECAT_SIGNATURE_HEADER",
    "RevenueCatWebhookAck",
    "RevenueCatWebhookDelivery",
    "SecretSource",
    "make_revenuecat_webhook_dependency",
    "make_revenuecat_webhook_router",
    "parse_signature_header",
    "parse_webhook",
    "sign_payload",
    "verify_authorization",
    "verify_signature",
]
