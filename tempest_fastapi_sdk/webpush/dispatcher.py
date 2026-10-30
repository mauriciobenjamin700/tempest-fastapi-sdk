"""VAPID-authenticated Web Push dispatcher built on ``pywebpush``.

The endpoint a dispatch targets was chosen by a browser subscriber, so the
request is treated as untrusted egress: the endpoint host must resolve only
to public addresses (the same policy as
:class:`~tempest_fastapi_sdk.genai.rag.ContentExtractor`), the connection
goes to the address that passed the check rather than to a second lookup,
redirects are never followed, and every request carries a timeout.
"""

from __future__ import annotations

import asyncio
import hashlib
import ipaddress
import json
import logging
from typing import Any, Final
from urllib.parse import urlsplit, urlunsplit

from tempest_fastapi_sdk.utils._egress import (
    Resolver,
    is_public_address,
    system_resolver,
)
from tempest_fastapi_sdk.webpush.schemas import (
    WebPushPayloadSchema,
    WebPushSubscriptionSchema,
)

logger = logging.getLogger(__name__)

_GONE_STATUSES: Final[frozenset[int]] = frozenset({404, 410})
"""Statuses every push service uses to say a subscription is dead."""

_WNS_HOST_SUFFIX: Final[str] = "notify.windows.com"
"""Host suffix of WNS, Microsoft's push service — the one Edge subscribes to."""

DEFAULT_WEBPUSH_TIMEOUT: Final[float] = 10.0
"""Seconds a dispatch waits to connect and, separately, for each read."""


def _is_gone(status: int | None, endpoint: str) -> bool:
    """Decide whether a failed dispatch means the subscription is dead.

    ``404`` and ``410`` are the answers the Web Push standard reserves for
    it, and every other bundled service uses them. WNS does not: when the
    browser was uninstalled, the user signed out of their Microsoft
    account, or the subscription was evicted internally, it answers
    ``400 Bad Request`` with an empty body. Reading that as a transient
    failure leaves the row in the store forever, failing on every send.

    The ``400`` rule is scoped to the WNS host on purpose. A bare
    ``status == 400`` would swallow a legitimate bad request — malformed
    payload, invalid header — from any other service and silently delete a
    live subscription, which is the worse mistake of the two.

    Args:
        status (int | None): HTTP status from the push service, or
            ``None`` when the request never got an answer.
        endpoint (str): The subscription endpoint the dispatch targeted.

    Returns:
        bool: Whether the caller should prune this subscription.
    """
    if status in _GONE_STATUSES:
        return True
    if status != 400:
        return False
    try:
        host = urlsplit(endpoint).netloc
    except ValueError:
        return False
    return host.endswith(_WNS_HOST_SUFFIX)


def _mask_endpoint(endpoint: str) -> str:
    """Return a stable but non-sensitive identifier for an endpoint.

    Keeps the host (useful for routing errors to the right push
    service) and replaces the full path + query with a short SHA-256
    prefix so subscription tokens never reach logs or API responses.

    Args:
        endpoint (str): The full push service URL.

    Returns:
        str: ``<host>/<sha256-prefix>`` or the original string when it
        cannot be parsed.
    """
    try:
        parsed = urlsplit(endpoint)
    except ValueError:
        return "<unparsable-endpoint>"
    host = parsed.netloc or "<unknown-host>"
    digest = hashlib.sha256(endpoint.encode("utf-8")).hexdigest()[:12]
    return f"{host}/{digest}"


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


class WebPushEndpointRefusedError(WebPushError):
    """Raised when the egress policy refuses a subscription endpoint.

    The endpoint is not an ``https`` URL, or (unless the dispatcher allows
    private networks) it names or resolves to a loopback, private,
    link-local or otherwise non-public address. No request was sent.

    Attributes:
        permanent (bool): ``True`` when the refusal follows from the URL
            itself — the scheme, or a host that is an IP literal — so no
            later attempt can succeed. ``False`` when it follows from a DNS
            answer, which can change: a resolver that maps a real push
            service to an internal proxy refuses every endpoint at once,
            and the store must not lose them all over a configuration slip.
    """

    def __init__(
        self,
        message: str,
        *,
        endpoint: str | None = None,
        permanent: bool,
    ) -> None:
        """Initialize the refusal.

        Args:
            message (str): Human-readable description.
            endpoint (str | None): Subscription endpoint, when known.
            permanent (bool): Whether the URL alone decided the refusal.
        """
        super().__init__(message, endpoint=endpoint)
        self.permanent: bool = permanent


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


def _egress_session(host: str, address: str | None) -> Any:
    """Build the ``requests`` session a single dispatch goes through.

    The session never follows redirects: a push service answers the
    ``POST`` itself, so a ``3xx`` is reported as a failed delivery instead
    of being chased to wherever ``Location`` points. When ``address`` is
    given the request URL carries that validated IP, while the ``Host``
    header, the TLS SNI name and the certificate check keep ``host`` — so a
    DNS answer that changes after the check never reaches the connection.

    Args:
        host (str): The endpoint hostname, used for ``Host``, SNI and the
            certificate match.
        address (str | None): The validated IP to connect to, or ``None``
            to connect to the URL as given.

    Returns:
        Any: A ``requests.Session`` configured as described.
    """
    import requests
    from requests.adapters import HTTPAdapter

    class _PinnedAdapter(HTTPAdapter):
        """Adapter that presents ``host`` for SNI and the certificate match."""

        def init_poolmanager(
            self,
            connections: int,
            maxsize: int,
            block: bool = False,
            **pool_kwargs: Any,
        ) -> None:
            """Create the pool manager with the original hostname pinned.

            Args:
                connections (int): Number of pools to cache.
                maxsize (int): Connections kept per pool.
                block (bool): Whether the pool blocks when exhausted.
                **pool_kwargs (Any): Forwarded to the ``urllib3`` pool manager.
            """
            super().init_poolmanager(
                connections,
                maxsize,
                block,
                server_hostname=host,
                assert_hostname=host,
                **pool_kwargs,
            )

        def proxy_manager_for(self, proxy: str, **proxy_kwargs: Any) -> Any:
            """Create a proxy manager with the original hostname pinned.

            Args:
                proxy (str): The proxy URL.
                **proxy_kwargs (Any): Forwarded to ``requests``.

            Returns:
                Any: The ``urllib3`` proxy manager.
            """
            return super().proxy_manager_for(
                proxy,
                server_hostname=host,
                assert_hostname=host,
                **proxy_kwargs,
            )

    class _EgressSession(requests.Session):
        """Session that refuses redirects and connects to the pinned IP."""

        def request(  # type: ignore[override]
            self,
            method: str,
            url: str,
            *,
            headers: dict[str, str] | None = None,
            allow_redirects: bool = False,
            **kwargs: Any,
        ) -> Any:
            """Send the request with redirects off and the IP pinned.

            Args:
                method (str): HTTP method.
                url (str): The endpoint URL.
                headers (dict[str, str] | None): Request headers; ``Host``
                    is set to the original hostname when pinning.
                allow_redirects (bool): Ignored — redirects are never
                    followed.
                **kwargs (Any): Forwarded to ``requests.Session.request``.

            Returns:
                Any: The ``requests.Response``.
            """
            merged: dict[str, str] = dict(headers or {})
            if address is not None:
                parts = urlsplit(url)
                literal = f"[{address}]" if ":" in address else address
                netloc = f"{literal}:{parts.port}" if parts.port else literal
                merged["Host"] = parts.netloc.rpartition("@")[2]
                url = urlunsplit(parts._replace(netloc=netloc))
            return super().request(
                method,
                url,
                headers=merged,
                allow_redirects=False,
                **kwargs,
            )

    session = _EgressSession()
    if address is not None:
        session.mount("https://", _PinnedAdapter())
    return session


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
        timeout (float): Seconds each dispatch waits to connect and for
            each read before it fails with :class:`WebPushError`.
        allow_private_networks (bool): Whether endpoints resolving to
            loopback, private, link-local or otherwise non-public
            addresses are dispatched to.
    """

    def __init__(
        self,
        vapid_private_key: str,
        *,
        vapid_subject: str,
        ttl_seconds: int = 60,
        extra_vapid_claims: dict[str, str] | None = None,
        timeout: float = DEFAULT_WEBPUSH_TIMEOUT,
        allow_private_networks: bool = False,
        resolver: Resolver | None = None,
    ) -> None:
        """Initialize the dispatcher.

        Args:
            vapid_private_key (str): VAPID private key.
            vapid_subject (str): The ``sub`` JWT claim. Browsers
                expect either a ``mailto:`` or ``https:`` URI.
            ttl_seconds (int): Default TTL for delivered messages.
            extra_vapid_claims (dict[str, str] | None): Additional
                claims merged into the JWT.
            timeout (float): Connect and read timeout, in seconds, of
                every dispatch.
            allow_private_networks (bool): Skip the public-address check,
                for a self-hosted push service on the internal network.
                Redirects stay disabled and the timeout still applies.
                Only turn this on when subscribers are trusted.
            resolver (Resolver | None): Async hostname → addresses lookup
                used by the public-address check; the first address it
                returns is the one the dispatch connects to. ``None`` uses
                the event loop's ``getaddrinfo``.
        """
        self.vapid_private_key: str = vapid_private_key
        self.vapid_claims: dict[str, str] = {"sub": vapid_subject}
        if extra_vapid_claims:
            self.vapid_claims.update(extra_vapid_claims)
        self.ttl_seconds: int = ttl_seconds
        self.timeout: float = timeout
        self.allow_private_networks: bool = allow_private_networks
        self._resolver: Resolver = resolver or system_resolver

    async def _pin_destination(self, endpoint: str) -> tuple[str, str | None]:
        """Refuse a non-public endpoint and return where to connect.

        Args:
            endpoint (str): The subscription endpoint.

        Returns:
            tuple[str, str | None]: The endpoint hostname and the validated
            IP the dispatch must connect to — ``None`` when the URL is sent
            unchanged (private networks are allowed, or the host is already
            an IP literal).

        Raises:
            WebPushEndpointRefusedError: When the endpoint is not ``https``
                with a host, or (unless private networks are allowed) its
                IP literal or any resolved address is not public.
            WebPushError: When the endpoint host cannot be resolved.
        """
        masked = _mask_endpoint(endpoint)
        try:
            parts = urlsplit(endpoint)
            scheme: str = parts.scheme
            host: str | None = parts.hostname
        except ValueError:
            scheme, host = "", None
        if scheme != "https" or not host:
            raise WebPushEndpointRefusedError(
                f"Refused Web Push endpoint {masked}: not an https URL",
                endpoint=endpoint,
                permanent=True,
            )
        if self.allow_private_networks:
            return host, None
        try:
            literal: str | None = str(ipaddress.ip_address(host))
        except ValueError:
            literal = None
        if literal is not None:
            if not is_public_address(literal):
                raise WebPushEndpointRefusedError(
                    f"Refused Web Push endpoint {masked}: non-public address",
                    endpoint=endpoint,
                    permanent=True,
                )
            return host, None
        try:
            addresses = list(await self._resolver(host))
        except OSError as exc:
            raise WebPushError(
                f"Could not resolve Web Push endpoint {masked}",
                endpoint=endpoint,
            ) from exc
        if not addresses or not all(is_public_address(a) for a in addresses):
            raise WebPushEndpointRefusedError(
                f"Refused Web Push endpoint {masked}: non-public address",
                endpoint=endpoint,
                permanent=False,
            )
        return host, str(addresses[0]).split("%", 1)[0]

    async def check_endpoint(self, endpoint: str) -> None:
        """Apply the egress check a dispatch applies, without sending.

        The same scheme, public-address and ``allow_private_networks``
        rules as :meth:`send`, through the same ``resolver``, so a
        subscription refused here is exactly one :meth:`send` would refuse.
        :func:`~tempest_fastapi_sdk.webpush.make_web_push_router` calls it
        before storing an endpoint; the switch that lets a self-hosted push
        service through stays one switch, on the dispatcher.

        Args:
            endpoint (str): The subscription endpoint to judge.

        Raises:
            WebPushEndpointRefusedError: When the endpoint is refused.
            WebPushError: When the endpoint host cannot be resolved.
        """
        await self._pin_destination(endpoint)

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
            WebPushGoneError: When the push service reports the
                subscription as dead — ``404``/``410`` anywhere, plus
                ``400`` from WNS, which is how Edge's push service spells
                it. See :func:`_is_gone`.
            WebPushEndpointRefusedError: When the egress check refuses the
                endpoint (non-public address, not ``https``); no request is
                sent.
            WebPushError: For any other delivery failure, including an
                unresolvable host, a redirect answer, a timeout and a
                connection error.
        """
        pywebpush = _require_pywebpush()
        import requests

        host, address = await self._pin_destination(subscription.endpoint)

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
            session = _egress_session(host, address)
            try:
                pywebpush.webpush(
                    subscription_info=sub_info,
                    data=data,
                    vapid_private_key=self.vapid_private_key,
                    vapid_claims=dict(self.vapid_claims),
                    ttl=effective_ttl,
                    headers=headers,
                    timeout=self.timeout,
                    requests_session=session,
                )
            except requests.RequestException as exc:
                raise WebPushError(
                    f"Web Push delivery failed for "
                    f"{_mask_endpoint(subscription.endpoint)} "
                    f"({type(exc).__name__})",
                    endpoint=subscription.endpoint,
                ) from exc
            except pywebpush.WebPushException as exc:
                status = exc.response.status_code if exc.response is not None else None
                masked = _mask_endpoint(subscription.endpoint)
                if _is_gone(status, subscription.endpoint):
                    raise WebPushGoneError(
                        f"Subscription gone (HTTP {status}) for {masked}",
                        status_code=status,
                        endpoint=subscription.endpoint,
                    ) from exc
                raise WebPushError(
                    f"Web Push delivery failed for {masked} (HTTP {status})",
                    status_code=status,
                    endpoint=subscription.endpoint,
                ) from exc
            finally:
                session.close()

        await asyncio.to_thread(_dispatch)

    async def send_many(
        self,
        subscriptions: list[WebPushSubscriptionSchema],
        payload: WebPushPayloadSchema | dict[str, Any] | str | bytes,
        *,
        ttl_seconds: int | None = None,
        headers: dict[str, str] | None = None,
        max_concurrency: int | None = None,
    ) -> list[str]:
        """Fan out a single payload to many subscriptions.

        Dispatches run concurrently via :func:`asyncio.gather`. Only
        subscriptions that can never be delivered are returned — the ones
        the push service reports as dead, and the ones the egress check
        refuses permanently (see
        :attr:`WebPushEndpointRefusedError.permanent`: not ``https``, or an
        IP-literal host that is not public). The list is what
        :meth:`prune` consumes, so an endpoint that failed for a transient
        reason must not be in it: a refusal decided by a DNS answer is
        logged and kept, like every other failure, because a delivery that
        failed once is not a device that went away.

        Args:
            subscriptions (list[WebPushSubscriptionSchema]): Recipients.
            payload: The notification body (same shapes as :meth:`send`).
            ttl_seconds (int | None): Override TTL.
            headers (dict[str, str] | None): Extra HTTP headers.
            max_concurrency (int | None): Cap on dispatches in flight at
                once. ``None`` (the default) keeps the historical
                behaviour of starting every dispatch immediately, which is
                right for one user's handful of devices. A global fan-out
                should pass a bound — every dispatch is a TLS request to a
                push service, and thousands at once earn a rate limit
                rather than a faster send. Values below ``1`` are treated
                as unbounded.

        Returns:
            list[str]: Endpoints whose subscription is gone and should
            be removed from the application's store.
        """
        gone: list[str] = []
        limiter: asyncio.Semaphore | None = (
            asyncio.Semaphore(max_concurrency)
            if max_concurrency is not None and max_concurrency >= 1
            else None
        )

        async def _dispatch(sub: WebPushSubscriptionSchema) -> None:
            try:
                await self.send(sub, payload, ttl_seconds=ttl_seconds, headers=headers)
            except WebPushGoneError:
                gone.append(sub.endpoint)
            except WebPushEndpointRefusedError as exc:
                if exc.permanent:
                    gone.append(sub.endpoint)
                    return
                logger.warning(
                    "Web Push send failed for %s: %s",
                    _mask_endpoint(sub.endpoint),
                    exc,
                )
            except WebPushError as exc:
                logger.warning(
                    "Web Push send failed for %s: %s",
                    _mask_endpoint(sub.endpoint),
                    exc,
                )

        async def _one(sub: WebPushSubscriptionSchema) -> None:
            if limiter is None:
                await _dispatch(sub)
                return
            async with limiter:
                await _dispatch(sub)

        await asyncio.gather(*(_one(sub) for sub in subscriptions))
        return gone


__all__: list[str] = [
    "WebPushDispatcher",
    "WebPushEndpointRefusedError",
    "WebPushError",
    "WebPushGoneError",
]
