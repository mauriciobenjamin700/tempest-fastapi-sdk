"""VAPID-authenticated Web Push dispatcher built on ``pywebpush``."""

from __future__ import annotations

import asyncio
import json
import logging
from typing import Any

from tempest_fastapi_sdk.webpush.schemas import (
    WebPushPayloadSchema,
    WebPushSubscriptionSchema,
)

logger = logging.getLogger(__name__)


class WebPushError(RuntimeError):
    """Raised when a push delivery attempt fails irrecoverably.

    Attributes:
        status_code (int | None): HTTP status returned by the push
            service, or ``None`` when the failure happened before the
            request was made.
        endpoint (str | None): The subscription endpoint, when known.
    """

    def __init__(
        self,
        message: str,
        *,
        status_code: int | None = None,
        endpoint: str | None = None,
    ) -> None:
        """Initialize the error.

        Args:
            message (str): Human-readable description.
            status_code (int | None): HTTP status, when known.
            endpoint (str | None): Subscription endpoint, when known.
        """
        super().__init__(message)
        self.status_code: int | None = status_code
        self.endpoint: str | None = endpoint


class WebPushGoneError(WebPushError):
    """Raised when the push service reports the subscription is gone.

    Maps to HTTP 404/410. Receivers should delete the subscription
    from their store and stop attempting delivery.
    """


def _require_pywebpush() -> Any:
    """Import :mod:`pywebpush` lazily.

    Returns:
        Any: The ``pywebpush`` module.

    Raises:
        ImportError: When the optional ``[webpush]`` extra is missing.
    """
    try:
        import pywebpush
    except ImportError as exc:
        raise ImportError(
            "Web Push support requires the optional [webpush] extra. "
            "Install with: pip install tempest-fastapi-sdk[webpush]",
        ) from exc
    return pywebpush


class WebPushDispatcher:
    """Send VAPID-signed Web Push notifications to browser subscribers.

    Wraps the synchronous ``pywebpush`` library in
    :func:`asyncio.to_thread` so dispatch fits the SDK's async-first
    convention. Subscriptions that respond with ``404``/``410`` raise
    :class:`WebPushGoneError` so the caller can prune their store;
    every other failure raises :class:`WebPushError`.

    Attributes:
        vapid_private_key (str): VAPID private key (PEM or base64url
            encoded). MUST match the public key advertised to clients.
        vapid_claims (dict[str, str]): Mandatory JWT claims attached
            to every push. ``sub`` is required (typically
            ``mailto:ops@example.com``).
        ttl_seconds (int): Default time-to-live applied to each push
            (the push service buffers the payload for at most this
            long when the device is offline).
    """

    def __init__(
        self,
        vapid_private_key: str,
        *,
        vapid_subject: str,
        ttl_seconds: int = 60,
        extra_vapid_claims: dict[str, str] | None = None,
    ) -> None:
        """Initialize the dispatcher.

        Args:
            vapid_private_key (str): VAPID private key.
            vapid_subject (str): The ``sub`` JWT claim. Browsers
                expect either a ``mailto:`` or ``https:`` URI.
            ttl_seconds (int): Default TTL for delivered messages.
            extra_vapid_claims (dict[str, str] | None): Additional
                claims merged into the JWT.
        """
        self.vapid_private_key: str = vapid_private_key
        self.vapid_claims: dict[str, str] = {"sub": vapid_subject}
        if extra_vapid_claims:
            self.vapid_claims.update(extra_vapid_claims)
        self.ttl_seconds: int = ttl_seconds

    async def send(
        self,
        subscription: WebPushSubscriptionSchema,
        payload: WebPushPayloadSchema | dict[str, Any] | str | bytes,
        *,
        ttl_seconds: int | None = None,
        headers: dict[str, str] | None = None,
    ) -> None:
        """Send a single push notification.

        Args:
            subscription (WebPushSubscriptionSchema): The recipient.
            payload (WebPushPayloadSchema | dict | str | bytes): The
                notification body. Pydantic models and dicts are
                JSON-encoded; strings/bytes are sent as-is.
            ttl_seconds (int | None): Override
                :attr:`ttl_seconds` for this dispatch.
            headers (dict[str, str] | None): Extra HTTP headers to
                attach to the push request (forwarded to pywebpush).

        Raises:
            WebPushGoneError: When the push service returns 404/410.
            WebPushError: For any other delivery failure.
        """
        pywebpush = _require_pywebpush()

        if isinstance(payload, WebPushPayloadSchema):
            data: str | bytes = payload.to_json()
        elif isinstance(payload, dict):
            data = json.dumps(payload, default=str)
        else:
            data = payload

        sub_info = {
            "endpoint": subscription.endpoint,
            "keys": {
                "p256dh": subscription.keys.p256dh,
                "auth": subscription.keys.auth,
            },
        }
        effective_ttl = ttl_seconds if ttl_seconds is not None else self.ttl_seconds

        def _dispatch() -> None:
            try:
                pywebpush.webpush(
                    subscription_info=sub_info,
                    data=data,
                    vapid_private_key=self.vapid_private_key,
                    vapid_claims=dict(self.vapid_claims),
                    ttl=effective_ttl,
                    headers=headers,
                )
            except pywebpush.WebPushException as exc:
                status = exc.response.status_code if exc.response is not None else None
                if status in {404, 410}:
                    raise WebPushGoneError(
                        f"Subscription gone (HTTP {status})",
                        status_code=status,
                        endpoint=subscription.endpoint,
                    ) from exc
                raise WebPushError(
                    f"Web Push delivery failed: {exc}",
                    status_code=status,
                    endpoint=subscription.endpoint,
                ) from exc

        await asyncio.to_thread(_dispatch)

    async def send_many(
        self,
        subscriptions: list[WebPushSubscriptionSchema],
        payload: WebPushPayloadSchema | dict[str, Any] | str | bytes,
        *,
        ttl_seconds: int | None = None,
        headers: dict[str, str] | None = None,
    ) -> list[str]:
        """Fan out a single payload to many subscriptions.

        Each dispatch runs concurrently via :func:`asyncio.gather`.
        Subscriptions that respond with 404/410 are returned so the
        caller can prune them; every other failure is logged and
        also returned in the gone list when the endpoint is known.

        Args:
            subscriptions (list[WebPushSubscriptionSchema]): Recipients.
            payload: The notification body (same shapes as :meth:`send`).
            ttl_seconds (int | None): Override TTL.
            headers (dict[str, str] | None): Extra HTTP headers.

        Returns:
            list[str]: Endpoints whose subscription is gone and should
            be removed from the application's store.
        """
        gone: list[str] = []

        async def _one(sub: WebPushSubscriptionSchema) -> None:
            try:
                await self.send(sub, payload, ttl_seconds=ttl_seconds, headers=headers)
            except WebPushGoneError:
                gone.append(sub.endpoint)
            except WebPushError as exc:
                logger.warning("Web Push send failed for %s: %s", sub.endpoint, exc)

        await asyncio.gather(*(_one(sub) for sub in subscriptions))
        return gone


__all__: list[str] = [
    "WebPushDispatcher",
    "WebPushError",
    "WebPushGoneError",
]
