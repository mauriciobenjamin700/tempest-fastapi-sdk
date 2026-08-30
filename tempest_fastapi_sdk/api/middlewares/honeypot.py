"""``HoneypotBanMiddleware`` — ban IPs that probe for vulnerabilities.

Every public API on the internet receives a constant stream of automated
scanners requesting ``/.env``, ``/.git/config``, ``/wp-admin/``,
``/phpmyadmin/``, ``/xmlrpc.php`` and a few hundred siblings. None of those
is a path an honest client ever requests, which makes a single hit an
unusually reliable signal — far more reliable than anything a rate limiter
can infer from volume alone.

:class:`~tempest_fastapi_sdk.api.middlewares.rate_limit.RateLimitMiddleware`
cannot express this. It answers *did this key exceed N requests in W
seconds*, and a scanner that probes twelve paths once each and moves on
never trips it. The two are complementary: the limiter bounds honest
clients, this bounds dishonest ones.

The interesting part is not the middleware — it is
:data:`DEFAULT_HONEYPOT_PATTERNS`, which is pure research, goes stale, and
is not specific to whichever service happens to host it. Shipping it here
means a consumer picks up new signatures with an SDK bump instead of
maintaining regexes by hand.
"""

from __future__ import annotations

import json
import logging
import re
import time
from collections.abc import Awaitable
from typing import Protocol, runtime_checkable

from starlette.types import ASGIApp, Receive, Scope, Send

from tempest_fastapi_sdk.utils.client_ip import get_client_ip_from_scope

logger = logging.getLogger(__name__)

_HONEYPOT_SOURCES: tuple[str, ...] = (
    # Dotfiles and secret files. `.well-known` is deliberately absent:
    # ACME and `security.txt` live there and are legitimate, so the
    # dotfile rules name the directories rather than matching `/.` .
    r"(?:^|/)\.env(\b|[./~])",
    r"(?:^|/)\.git(?:/|$)",
    r"(?:^|/)\.svn(?:/|$)",
    r"(?:^|/)\.hg(?:/|$)",
    r"(?:^|/)\.aws(?:/|$)",
    r"(?:^|/)\.ssh(?:/|$)",
    r"(?:^|/)\.htaccess\b",
    r"(?:^|/)\.htpasswd\b",
    r"(?:^|/)\.DS_Store\b",
    r"/\.(env|git|svn|aws|ssh|config)\b",
    # PHP reconnaissance. `php\?-` is the CGI argument-injection probe
    # (`/cgi-bin/php?-d+allow_url_include=1`), which is why the target
    # matched includes the query string and not the path alone.
    r"phpinfo",
    r"php-?info",
    r"php\?-",
    r"php_info",
    r"phpversion",
    r"_profiler/phpinfo",
    r"server-status",
    r"server-info",
    r"(?:^|/)info\.php\b",
    # CMS and admin-panel scanners.
    r"(?:^|/)wp-(?:admin|login|content|includes)(?:[/.]|$)",
    r"(?:^|/)xmlrpc\.php\b",
    r"(?:^|/)wlwmanifest\.xml\b",
    r"(?:^|/)phpmyadmin(?:/|$)",
    r"(?:^|/)pma(?:/|$)",
    r"(?:^|/)mysql(?:-?admin)?(?:/|$)",
    r"(?:^|/)administrator(?:/|$)",
    r"(?:^|/)joomla(?:/|$)",
    r"(?:^|/)drupal(?:/|$)",
    r"(?:^|/)magento(?:/|$)",
    r"(?:^|/)prestashop(?:/|$)",
    r"(?:^|/)cgi-bin(?:/|$)",
    # Backup and dump signatures.
    r"\.(?:bak|backup|old|orig|swp|swo|save|dist|sample"
    r"|sql|sql\.gz|tar(?:\.gz)?|zip|rar|7z)(?:\?|$|/)",
    r"~$",
    # Config and credential probes.
    r"(?:^|/)config(?:\.json|\.yaml|\.yml|\.ini|\.php|\.bak)",
    r"(?:^|/)credentials\b",
    r"(?:^|/)secret(?:s)?(?:\.|/)",
    # Endpoints only known scanners ask for.
    r"(?:^|/)enhancecp\b",
    r"(?:^|/)psnlink\b",
    r"(?:^|/)exapi\b",
)
"""Raw sources behind :data:`DEFAULT_HONEYPOT_PATTERNS`.

Ported from ``alofans-api``'s ``BadActorMiddleware._HONEYPOT_PATTERNS``,
which has been running in production against live scanner traffic. Kept as
strings so they can be recompiled under different flags under different
flags and so a test can assert the exact set rather than a compiled object
whose contents are invisible in a diff.
"""


def _compile_patterns(
    sources: tuple[str, ...] = _HONEYPOT_SOURCES,
) -> tuple[re.Pattern[str], ...]:
    """Compile scanner signatures, case-insensitively.

    Args:
        sources (tuple[str, ...]): Regex sources to compile. Defaults to
            the curated set behind :data:`DEFAULT_HONEYPOT_PATTERNS`.

    Returns:
        tuple[re.Pattern[str], ...]: One compiled pattern per source, in
        the order given.
    """
    return tuple(re.compile(source, re.IGNORECASE) for source in sources)


DEFAULT_HONEYPOT_PATTERNS: tuple[re.Pattern[str], ...] = _compile_patterns()
"""Curated scanner signatures, matched against path **and** query string.

A single hit is treated as proof of a scanner, so the set errs towards
paths no deployed application serves. It is overridable precisely because
the judgement is not universal — a service that legitimately serves
``/wp-admin`` exists somewhere, and it passes its own ``patterns``.
"""


@runtime_checkable
class BanStore(Protocol):
    """Where bans live between requests.

    Two methods, both of which the middleware is allowed to see fail: an
    outage must not take the API down with it. See
    :class:`HoneypotBanMiddleware` for how failures are absorbed.
    """

    async def is_banned(self, ip: str) -> bool:
        """Report whether ``ip`` currently has an active ban.

        Args:
            ip (str): The resolved client IP.

        Returns:
            bool: ``True`` while the ban is in force.
        """
        ...

    async def ban(self, ip: str, *, ttl_seconds: int, reason: str) -> None:
        """Record a ban for ``ip`` lasting ``ttl_seconds``.

        Args:
            ip (str): The resolved client IP.
            ttl_seconds (int): How long the ban stays in force.
            reason (str): The request target that triggered it, stored so
                an operator reviewing the ban can see what earned it.
        """
        ...


class MemoryBanStore:
    """In-process :class:`BanStore`, useful for a single worker and tests.

    Bans are held in a dict of expiry timestamps and are lost on restart,
    which is the trade a service accepts by not running Redis. Expired
    entries are dropped when read rather than swept, so the dict tracks the
    set of distinct offenders rather than growing with every hit.
    """

    def __init__(self) -> None:
        """Initialize an empty ban table."""
        self._bans: dict[str, float] = {}

    async def is_banned(self, ip: str) -> bool:
        """Report whether ``ip`` has an unexpired ban.

        Args:
            ip (str): The resolved client IP.

        Returns:
            bool: ``True`` while the ban is in force.
        """
        expiry = self._bans.get(ip)
        if expiry is None:
            return False
        if expiry <= time.time():
            del self._bans[ip]
            return False
        return True

    async def ban(self, ip: str, *, ttl_seconds: int, reason: str) -> None:
        """Record a ban for ``ip``.

        Args:
            ip (str): The resolved client IP.
            ttl_seconds (int): How long the ban stays in force.
            reason (str): The request target that triggered it. Unused
                here — an in-process store has no operator to review — and
                accepted so this class satisfies :class:`BanStore`.
        """
        del reason
        self._bans[ip] = time.time() + ttl_seconds


class _RedisLike(Protocol):
    """Minimal async-Redis surface used by :class:`RedisBanStore`.

    Declared with positional-only parameters and ``Awaitable`` returns
    rather than as ``async def``: ``redis.asyncio.Redis`` names the first
    parameter ``name`` and returns ``Awaitable``, not ``Coroutine``, so an
    ``async def`` member typed ``key`` rejects the very client this
    protocol exists to accept.
    """

    def get(self, key: str, /) -> Awaitable[str | bytes | None]:
        """Return the stored value, or ``None`` when the key is absent.

        Args:
            key (str): The ban key.

        Returns:
            Awaitable[str | bytes | None]: The stored reason, or ``None``
            when no ban is in force.
        """
        ...

    def set(self, key: str, value: str, /, *, ex: int) -> Awaitable[object]:
        """Store ``value`` under ``key`` with TTL ``ex`` (seconds).

        Args:
            key (str): The ban key.
            value (str): The request target that earned the ban.
            ex (int): Time-to-live in seconds.

        Returns:
            Awaitable[object]: Whatever the client returns; the caller
            ignores it.
        """
        ...


class RedisBanStore:
    """:class:`BanStore` backed by an async ``redis`` client.

    This is the shape a real deployment wants: the ban is shared by every
    replica the moment it is written, and Redis owns the expiry, so a
    restart does not hand the scanner a clean slate. Requires the
    ``[cache]`` extra.
    """

    def __init__(self, client: _RedisLike, *, prefix: str = "honeypot:ban:") -> None:
        """Initialize.

        Args:
            client (_RedisLike): Async Redis-like client exposing
                ``get(key)`` / ``set(key, value, ex=...)`` (e.g.
                ``redis.asyncio.Redis`` or ``fakeredis.aioredis``).
            prefix (str): Key prefix so ban entries do not collide with
                other cached data.
        """
        self.client: _RedisLike = client
        self.prefix: str = prefix

    def _key(self, ip: str) -> str:
        """Namespace one IP's ban entry.

        Args:
            ip (str): The resolved client IP.

        Returns:
            str: The Redis key holding that IP's ban.
        """
        return f"{self.prefix}{ip}"

    async def is_banned(self, ip: str) -> bool:
        """Report whether ``ip`` currently has an active ban.

        Args:
            ip (str): The resolved client IP.

        Returns:
            bool: ``True`` while the key exists.
        """
        return await self.client.get(self._key(ip)) is not None

    async def ban(self, ip: str, *, ttl_seconds: int, reason: str) -> None:
        """Write the ban with a Redis-managed TTL.

        Args:
            ip (str): The resolved client IP.
            ttl_seconds (int): How long the ban stays in force.
            reason (str): The request target that triggered it.
        """
        await self.client.set(self._key(ip), reason, ex=ttl_seconds)


class HoneypotBanMiddleware:
    """Ban an IP the first time it requests a path only a scanner wants.

    A banned IP is answered before routing, before authentication and
    before the rate limiter — the cheapest possible rejection, which is the
    point of banning rather than limiting.

    Two decisions are baked in rather than left to the consumer:

    **It fails open.** Every call into the store is guarded: a store that
    raises is logged at ``WARNING`` and the request proceeds. A blocklist
    that cannot reach Redis must let traffic through, because getting this
    backwards turns a cache outage into a full outage. The failure stays
    visible in the logs rather than being swallowed.

    **The client IP comes from a single trusted header.** Behind nginx,
    ``X-Forwarded-For`` is *appended* to whatever the client sent, so its
    leftmost entry is attacker-controlled — a hand-rolled blocklist that
    reads it directly bans the wrong IP, and can be made to ban a real
    user. ``trusted_ip_header`` is passed to
    :func:`~tempest_fastapi_sdk.utils.client_ip.get_client_ip_from_scope`,
    which reads one edge-set header and otherwise falls back to the
    transport peer.

    The response deliberately says nothing about the ban: a scanner that
    learns it is banned learns to rotate.

    Attributes:
        app (ASGIApp): The wrapped ASGI application.
        store (BanStore): Where bans are recorded and read.
        patterns (tuple[re.Pattern[str], ...]): The signatures in force.
        ban_seconds (int): How long a ban lasts.
    """

    def __init__(
        self,
        app: ASGIApp,
        *,
        store: BanStore,
        patterns: tuple[re.Pattern[str], ...] = DEFAULT_HONEYPOT_PATTERNS,
        ban_seconds: int = 86_400,
        trusted_ip_header: str | None = None,
        exempt_paths: tuple[str, ...] = (),
        status_code: int = 403,
    ) -> None:
        """Initialize.

        Args:
            app (ASGIApp): The wrapped ASGI application.
            store (BanStore): Where bans live. :class:`MemoryBanStore` for
                a single worker or a test; :class:`RedisBanStore` for
                anything with replicas.
            patterns (tuple[re.Pattern[str], ...]): Signatures matched
                against the request target. Defaults to
                :data:`DEFAULT_HONEYPOT_PATTERNS`.
            ban_seconds (int): Ban duration. Defaults to 24 hours, long
                enough to outlast a scan pass and short enough that a
                recycled address is not punished for its predecessor.
            trusted_ip_header (str | None): Single edge-set header naming
                the client IP (e.g. ``"x-real-ip"``). ``None`` uses the
                transport peer. Never point this at a bare
                ``X-Forwarded-For``.
            exempt_paths (tuple[str, ...]): Path prefixes checked before
                the patterns, for the service that legitimately serves
                something the curated list flags.
            status_code (int): Status returned to a banned or offending
                caller.
        """
        self.app: ASGIApp = app
        self.store: BanStore = store
        self.patterns: tuple[re.Pattern[str], ...] = patterns
        self.ban_seconds: int = ban_seconds
        self._trusted_ip_header: str | None = trusted_ip_header
        self._exempt: tuple[str, ...] = exempt_paths
        self._status_code: int = status_code

    def matches(self, target: str) -> bool:
        """Report whether ``target`` carries a scanner signature.

        Args:
            target (str): The request path, with ``?query`` appended when
                the request carried one.

        Returns:
            bool: ``True`` when any configured pattern matches.
        """
        return any(pattern.search(target) for pattern in self.patterns)

    async def _is_banned(self, ip: str) -> bool:
        """Ask the store whether ``ip`` is banned, failing open.

        Args:
            ip (str): The resolved client IP.

        Returns:
            bool: ``True`` when the store reports an active ban;
            ``False`` when it reports none **or** when it failed.
        """
        try:
            return await self.store.is_banned(ip)
        except Exception as exc:
            logger.warning(
                "honeypot: ban lookup failed for ip=%s, letting request through: %s",
                ip,
                exc,
            )
            return False

    async def _ban(self, ip: str, target: str) -> None:
        """Record a ban, absorbing a store failure.

        The offending request is refused either way — the ban is what is
        lost when the store is unwell, not the rejection.

        Args:
            ip (str): The resolved client IP.
            target (str): The request target that earned the ban.
        """
        try:
            await self.store.ban(
                ip,
                ttl_seconds=self.ban_seconds,
                reason=target,
            )
        except Exception as exc:
            logger.warning(
                "honeypot: ban write failed for ip=%s: %s",
                ip,
                exc,
            )

    async def _refuse(self, send: Send) -> None:
        """Answer without disclosing that a ban exists.

        Args:
            send (Send): The ASGI send callable.
        """
        body: bytes = json.dumps({"detail": "Forbidden"}).encode("utf-8")
        await send(
            {
                "type": "http.response.start",
                "status": self._status_code,
                "headers": [
                    (b"content-type", b"application/json"),
                    (b"content-length", str(len(body)).encode("latin-1")),
                ],
            }
        )
        await send({"type": "http.response.body", "body": body})

    async def __call__(
        self,
        scope: Scope,
        receive: Receive,
        send: Send,
    ) -> None:
        """Refuse banned callers, and ban new ones on their first probe.

        The banned check runs before the pattern check so a scanner that
        keeps going costs one store read per request rather than a read
        and a write.

        Args:
            scope (Scope): The ASGI scope.
            receive (Receive): The upstream receive callable.
            send (Send): The upstream send callable.
        """
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        path: str = scope.get("path", "")
        if any(path.startswith(prefix) for prefix in self._exempt):
            await self.app(scope, receive, send)
            return

        query: str = scope.get("query_string", b"").decode("latin-1")
        target: str = f"{path}?{query}" if query else path
        ip: str = get_client_ip_from_scope(
            scope,
            trusted_header=self._trusted_ip_header,
        )

        if await self._is_banned(ip):
            await self._refuse(send)
            return

        if self.matches(target):
            await self._ban(ip, target)
            logger.warning(
                "honeypot: banned ip=%s target=%s method=%s",
                ip,
                target,
                scope.get("method", ""),
                extra={
                    "client_ip": ip,
                    "http_target": target,
                    "ban_seconds": self.ban_seconds,
                },
            )
            await self._refuse(send)
            return

        await self.app(scope, receive, send)


__all__: list[str] = [
    "DEFAULT_HONEYPOT_PATTERNS",
    "BanStore",
    "HoneypotBanMiddleware",
    "MemoryBanStore",
    "RedisBanStore",
]
