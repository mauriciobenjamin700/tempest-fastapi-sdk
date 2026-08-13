"""Token-bucket rules and per-plan quotas for the rate-limit middleware.

The sliding window in :mod:`tempest_fastapi_sdk.api.middlewares.rate_limit`
answers one question: *did this key exceed N requests in the last W
seconds?* Two shapes it cannot express are the ones a paid API needs:

* **Burst tolerance.** A client that sends 20 requests at once and then
  idles for a minute is well-behaved on average, but a strict window
  rejects it. A **token bucket** decouples the sustained rate (tokens
  refilled per second) from the burst it absorbs (bucket capacity).
* **Tiered quotas.** ``free`` gets 60/min, ``pro`` gets 600/min plus a
  daily ceiling. That is *several* limits applied to the same request,
  resolved per principal, and they must be checked **together** — a
  request that fails the daily ceiling must not spend a token from the
  per-minute bucket.

Both live here. :class:`RateLimitRule` describes one limit (sliding
window when ``burst`` is unset, token bucket when it is set),
:class:`QuotaStore` evaluates a whole list of them atomically, and
:class:`PlanRateLimitPolicy` picks which list applies to the request.
Wire the result through
``RateLimitMiddleware(policy=..., quota_store=...)``.
"""

from __future__ import annotations

import asyncio
import math
import time
import uuid
from collections import deque
from collections.abc import Awaitable, Callable, Mapping, Sequence
from dataclasses import dataclass
from typing import Any, Protocol, runtime_checkable

from starlette.requests import Request

_GC_EVERY: int = 1000
"""Operations between sweeps of idle keys in :class:`MemoryQuotaStore`.

Sweeping is O(number of keys), so it cannot run on every request. A
sweep every 1000 consumes keeps the dictionary bounded by the *active*
key set without adding measurable per-request cost.
"""


@dataclass(frozen=True, slots=True)
class RateLimitRule:
    """One limit applied to a rate-limit key.

    With ``burst`` unset the rule is a **sliding window**: at most
    ``max_requests`` requests in any ``window_seconds`` interval. With
    ``burst`` set it is a **token bucket** whose capacity is ``burst``
    and which refills ``max_requests / window_seconds`` tokens per
    second — so ``RateLimitRule(600, 60.0, burst=100)`` sustains 10
    requests per second while absorbing a burst of 100.

    Attributes:
        max_requests (int): Requests allowed per window (sliding
            window), or tokens refilled per window (token bucket).
        window_seconds (float): Length of the window, in seconds.
        burst (int | None): Token-bucket capacity. ``None`` (default)
            selects the sliding-window algorithm.
        scope (str): Sub-key isolating this rule's counters from the
            other rules applied to the same principal. Empty (default)
            derives a deterministic scope from the rule's own numbers,
            which is unique as long as two rules in one list differ.
    """

    max_requests: int
    window_seconds: float
    burst: int | None = None
    scope: str = ""

    def __post_init__(self) -> None:
        """Validate the rule at construction time.

        Raises:
            ValueError: If ``max_requests`` < 1, ``window_seconds`` <= 0
                or ``burst`` is set below 1.
        """
        if self.max_requests < 1:
            raise ValueError("max_requests must be >= 1")
        if self.window_seconds <= 0:
            raise ValueError("window_seconds must be > 0")
        if self.burst is not None and self.burst < 1:
            raise ValueError("burst must be >= 1 when set")

    @property
    def is_bucket(self) -> bool:
        """Whether this rule uses the token-bucket algorithm.

        Returns:
            bool: ``True`` when ``burst`` is set.
        """
        return self.burst is not None

    @property
    def capacity(self) -> int:
        """Token-bucket capacity, or the window limit for a window rule.

        Returns:
            int: ``burst`` when set, else ``max_requests``.
        """
        return self.burst if self.burst is not None else self.max_requests

    @property
    def refill_per_second(self) -> float:
        """Tokens restored per second by the bucket.

        Returns:
            float: ``max_requests / window_seconds``.
        """
        return self.max_requests / self.window_seconds

    @property
    def effective_scope(self) -> str:
        """Sub-key used to isolate this rule's counters.

        Returns:
            str: ``scope`` when set, else a deterministic label built
            from the rule's numbers (e.g. ``"600/60s+b100"``).
        """
        if self.scope:
            return self.scope
        label = f"{self.max_requests}/{self.window_seconds:g}s"
        if self.burst is not None:
            label = f"{label}+b{self.burst}"
        return label


@dataclass(frozen=True, slots=True)
class QuotaResult:
    """Outcome of evaluating a whole rule list against one key.

    ``limit`` / ``remaining`` / ``reset_seconds`` describe the
    **binding** rule — the one with the least headroom, which is the
    number a client should pace itself against.

    Attributes:
        allowed (bool): ``True`` when every rule accepted the request.
        limit (int): ``max_requests`` of the binding rule.
        remaining (int): Requests still allowed under the binding rule.
        reset_seconds (int): Seconds until the binding rule's counters
            are fully restored (window elapsed, or bucket refilled to
            capacity).
        retry_after (int): Seconds to wait before retrying. ``0`` when
            allowed, always ``>= 1`` when rejected.
        scope (str): :attr:`RateLimitRule.effective_scope` of the
            binding rule — names *which* limit produced these numbers.
    """

    allowed: bool
    limit: int
    remaining: int
    reset_seconds: int
    retry_after: int
    scope: str


@runtime_checkable
class QuotaStore(Protocol):
    """Backend evaluating a list of rules against one key, atomically.

    Atomicity across the whole list is the contract: when any rule
    rejects, **no** rule may record the request. Otherwise a caller
    blocked by a daily ceiling would still burn per-minute tokens, and
    the per-minute limit would drain without a single request being
    served.
    """

    async def consume(
        self,
        key: str,
        rules: Sequence[RateLimitRule],
    ) -> QuotaResult:
        """Charge one request against every rule in ``rules``.

        Args:
            key (str): The principal's rate-limit key.
            rules (Sequence[RateLimitRule]): Limits to apply together.

        Returns:
            QuotaResult: The decision, described by the binding rule.
        """
        ...


def _window_state(
    hits: deque[float],
    now: float,
    rule: RateLimitRule,
) -> tuple[bool, int, int, int]:
    """Evaluate a sliding-window rule without recording the request.

    Args:
        hits (deque[float]): Monotonic timestamps of prior requests,
            pruned in place to the current window.
        now (float): Current monotonic time.
        rule (RateLimitRule): The rule being evaluated.

    Returns:
        tuple[bool, int, int, int]: ``(allowed, remaining, reset, retry)``
        where ``remaining`` already accounts for the pending request.
    """
    cutoff = now - rule.window_seconds
    while hits and hits[0] < cutoff:
        hits.popleft()
    oldest_reset = (
        max(1, math.ceil(hits[0] + rule.window_seconds - now))
        if hits
        else math.ceil(rule.window_seconds)
    )
    if len(hits) >= rule.max_requests:
        return False, 0, oldest_reset, oldest_reset
    return True, rule.max_requests - len(hits) - 1, oldest_reset, 0


def _bucket_state(
    tokens: float,
    updated_at: float,
    now: float,
    rule: RateLimitRule,
) -> tuple[bool, float, int, int, int]:
    """Evaluate a token-bucket rule without recording the request.

    Args:
        tokens (float): Tokens held at ``updated_at``.
        updated_at (float): Monotonic time the bucket was last touched.
        now (float): Current monotonic time.
        rule (RateLimitRule): The rule being evaluated.

    Returns:
        tuple[bool, float, int, int, int]: ``(allowed, refilled_tokens,
        remaining, reset, retry)``. ``refilled_tokens`` is the balance
        *before* spending, so the caller commits ``refilled - 1``.
    """
    refilled = min(
        float(rule.capacity),
        tokens + max(0.0, now - updated_at) * rule.refill_per_second,
    )
    reset = max(
        0,
        math.ceil((rule.capacity - refilled) / rule.refill_per_second),
    )
    if refilled < 1.0:
        retry = max(1, math.ceil((1.0 - refilled) / rule.refill_per_second))
        return False, refilled, 0, max(reset, retry), retry
    return True, refilled, int(refilled - 1.0), reset, 0


def _pick_binding(
    candidates: Sequence[tuple[RateLimitRule, int, int, int]],
) -> tuple[RateLimitRule, int, int, int]:
    """Return the rule a client should pace itself against.

    Args:
        candidates (Sequence[tuple[RateLimitRule, int, int, int]]): One
            ``(rule, remaining, reset, retry)`` tuple per evaluated rule.

    Returns:
        tuple[RateLimitRule, int, int, int]: The candidate with the least
        remaining headroom, breaking ties on the longer reset.
    """
    return min(candidates, key=lambda item: (item[1], -item[2]))


class MemoryQuotaStore:
    """In-process quota store covering both algorithms.

    Counters live in this worker's memory only — correct for a single
    process. Use :class:`RedisQuotaStore` when more than one replica
    must share the quota, otherwise each replica grants the full limit
    and the effective ceiling is ``limit x replicas``.

    Idle keys are swept every :data:`_GC_EVERY` consumes. Each key
    records the instant its state becomes indistinguishable from absent
    — the window's last hit falling out, or the bucket refilling to
    capacity — and only keys past that instant are dropped. Sweeping on
    a fixed age instead would hand free capacity to a slow bucket: one
    refilling at 10 tokens/minute with a burst of 1000 takes over an
    hour to fill, so evicting it early resets it to full.
    """

    def __init__(self) -> None:
        """Initialize an empty store."""
        self._windows: dict[str, deque[float]] = {}
        self._buckets: dict[str, tuple[float, float]] = {}
        self._expires_at: dict[str, float] = {}
        self._lock: asyncio.Lock = asyncio.Lock()
        self._ops: int = 0

    async def consume(
        self,
        key: str,
        rules: Sequence[RateLimitRule],
    ) -> QuotaResult:
        """Charge one request against every rule under a single lock.

        Evaluation is two-phase: every rule is inspected first and the
        request is recorded only when all of them accept, so a rejection
        by one rule never consumes another rule's budget.

        Args:
            key (str): The principal's rate-limit key.
            rules (Sequence[RateLimitRule]): Limits to apply together.

        Returns:
            QuotaResult: The decision, described by the binding rule.

        Raises:
            ValueError: If ``rules`` is empty.
        """
        if not rules:
            raise ValueError("rules must not be empty")
        now = time.monotonic()
        async with self._lock:
            self._ops += 1
            if self._ops % _GC_EVERY == 0:
                self._sweep(now)
            allowed = True
            candidates: list[tuple[RateLimitRule, int, int, int]] = []
            pending: list[tuple[RateLimitRule, str, float]] = []
            for rule in rules:
                rule_key = f"{key}|{rule.effective_scope}"
                if rule.is_bucket:
                    tokens, updated_at = self._buckets.get(
                        rule_key,
                        (float(rule.capacity), now),
                    )
                    ok, refilled, remaining, reset, retry = _bucket_state(
                        tokens,
                        updated_at,
                        now,
                        rule,
                    )
                    pending.append((rule, rule_key, refilled))
                else:
                    hits = self._windows.setdefault(rule_key, deque())
                    ok, remaining, reset, retry = _window_state(hits, now, rule)
                    pending.append((rule, rule_key, 0.0))
                candidates.append((rule, remaining, reset, retry))
                allowed = allowed and ok
            if allowed:
                for rule, rule_key, refilled in pending:
                    if rule.is_bucket:
                        left = refilled - 1.0
                        self._buckets[rule_key] = (left, now)
                        self._expires_at[rule_key] = now + (
                            (rule.capacity - left) / rule.refill_per_second
                        )
                    else:
                        self._windows[rule_key].append(now)
                        self._expires_at[rule_key] = now + rule.window_seconds
        binding_rule, remaining, reset, retry = _pick_binding(candidates)
        if allowed:
            return QuotaResult(
                allowed=True,
                limit=binding_rule.max_requests,
                remaining=remaining,
                reset_seconds=reset,
                retry_after=0,
                scope=binding_rule.effective_scope,
            )
        denied = max(
            (item for item in candidates if item[3] > 0),
            key=lambda item: item[3],
        )
        return QuotaResult(
            allowed=False,
            limit=denied[0].max_requests,
            remaining=0,
            reset_seconds=denied[2],
            retry_after=denied[3],
            scope=denied[0].effective_scope,
        )

    def _sweep(self, now: float) -> None:
        """Drop keys whose counters no longer hold information.

        A key is dropped only past the expiry recorded when it was last
        written — the moment its state stops differing from a key that
        was never seen.

        Args:
            now (float): Current monotonic time.
        """
        for rule_key, expires_at in list(self._expires_at.items()):
            if expires_at > now:
                continue
            del self._expires_at[rule_key]
            self._windows.pop(rule_key, None)
            self._buckets.pop(rule_key, None)


@runtime_checkable
class RedisLike(Protocol):
    """Minimal async Redis surface used by :class:`RedisQuotaStore`.

    Matches the relevant subset of ``redis.asyncio.Redis``.
    """

    def eval(
        self,
        script: str,
        numkeys: int,
        *keys_and_args: Any,
    ) -> Awaitable[Any]:
        """Evaluate a Lua ``script`` server-side."""
        ...


# Evaluates every rule before writing any of them, so a rejection never
# spends another rule's budget. Rule i is encoded as four ARGV slots
# (kind, max_requests, window_ms, capacity) starting at index 4.
# Returns {allowed, limit, remaining, reset_ms, retry_ms} for the
# binding rule.
_QUOTA_LUA: str = """
local now = tonumber(ARGV[1])
local count = tonumber(ARGV[2])
local member = ARGV[3]
local allowed = 1
local state = {}
for i = 1, count do
  local base = 3 + (i - 1) * 4
  local kind = tonumber(ARGV[base + 1])
  local limit = tonumber(ARGV[base + 2])
  local window = tonumber(ARGV[base + 3])
  local capacity = tonumber(ARGV[base + 4])
  local key = KEYS[i]
  local ok, remaining, reset, retry, tokens
  if kind == 0 then
    redis.call('ZREMRANGEBYSCORE', key, 0, now - window)
    local used = redis.call('ZCARD', key)
    local oldest = redis.call('ZRANGE', key, 0, 0, 'WITHSCORES')
    if oldest[2] then
      reset = (tonumber(oldest[2]) + window) - now
      if reset < 1 then reset = 1 end
    else
      reset = window
    end
    if used < limit then
      ok = 1
      remaining = limit - used - 1
      retry = 0
    else
      ok = 0
      remaining = 0
      retry = reset
    end
    tokens = 0
  else
    local rate = limit / window
    local data = redis.call('HMGET', key, 'tokens', 'ts')
    local held = tonumber(data[1])
    local ts = tonumber(data[2])
    if held == nil or ts == nil then
      held = capacity
      ts = now
    end
    local elapsed = now - ts
    if elapsed < 0 then elapsed = 0 end
    tokens = held + elapsed * rate
    if tokens > capacity then tokens = capacity end
    reset = math.ceil((capacity - tokens) / rate)
    if reset < 0 then reset = 0 end
    if tokens >= 1 then
      ok = 1
      remaining = math.floor(tokens - 1)
      retry = 0
    else
      ok = 0
      remaining = 0
      retry = math.ceil((1 - tokens) / rate)
      if retry < 1 then retry = 1 end
      if reset < retry then reset = retry end
    end
  end
  local ttl
  if kind == 0 then
    ttl = window
  else
    -- Time for the bucket to refill from empty, never the window: a
    -- burst far above the per-window rate takes much longer to fill,
    -- and an early expiry would silently hand back a full bucket.
    ttl = math.ceil(capacity * window / limit)
  end
  if ok == 0 then allowed = 0 end
  state[i] = {kind, limit, remaining, reset, retry, tokens, ttl}
end
if allowed == 1 then
  for i = 1, count do
    local key = KEYS[i]
    local entry = state[i]
    if entry[1] == 0 then
      redis.call('ZADD', key, now, member .. ':' .. i)
      redis.call('PEXPIRE', key, entry[7])
    else
      redis.call('HSET', key, 'tokens', entry[6] - 1, 'ts', now)
      redis.call('PEXPIRE', key, entry[7])
    end
  end
end
local pick = 1
for i = 2, count do
  local a = state[i]
  local b = state[pick]
  if a[3] < b[3] or (a[3] == b[3] and a[4] > b[4]) then pick = i end
end
if allowed == 0 then
  pick = 1
  for i = 1, count do
    if state[i][5] > state[pick][5] then pick = i end
  end
end
local chosen = state[pick]
return {allowed, chosen[2], chosen[3], chosen[4], chosen[5], pick}
"""


class RedisQuotaStore:
    """Distributed quota store — every rule evaluated in one Lua call.

    A sliding-window rule maps to a sorted set of request timestamps; a
    token-bucket rule maps to a hash holding ``tokens`` and the
    monotonic-free wall clock ``ts`` of the last refill. The script
    decides every rule first and only writes when all of them accept, so
    the whole list is atomic — which a client-side loop over several
    single-rule calls cannot be.

    When the backend raises and ``fail_open`` is ``True`` (the default),
    the request is allowed rather than locking every caller out on a
    transient Redis outage.
    """

    def __init__(
        self,
        redis: RedisLike,
        *,
        namespace: str = "quota",
        fail_open: bool = True,
    ) -> None:
        """Initialize the store.

        Args:
            redis (RedisLike): Async Redis client (e.g.
                ``redis.asyncio.Redis``).
            namespace (str): Prefix for every Redis key.
            fail_open (bool): Allow the request when the backend errors.
        """
        self._redis: RedisLike = redis
        self._namespace: str = namespace
        self._fail_open: bool = fail_open

    async def consume(
        self,
        key: str,
        rules: Sequence[RateLimitRule],
    ) -> QuotaResult:
        """Charge one request against every rule via the Lua script.

        Args:
            key (str): The principal's rate-limit key.
            rules (Sequence[RateLimitRule]): Limits to apply together.

        Returns:
            QuotaResult: The decision, described by the binding rule. On
            a backend error this is ``allowed`` when ``fail_open`` is
            set.

        Raises:
            ValueError: If ``rules`` is empty.
            Exception: Propagates the backend error when ``fail_open``
                is ``False``.
        """
        if not rules:
            raise ValueError("rules must not be empty")
        now_ms = int(time.time() * 1000)
        keys: list[str] = [
            f"{self._namespace}:{key}|{rule.effective_scope}" for rule in rules
        ]
        args: list[Any] = [now_ms, len(rules), uuid.uuid4().hex]
        for rule in rules:
            args.extend(
                [
                    1 if rule.is_bucket else 0,
                    rule.max_requests,
                    int(rule.window_seconds * 1000),
                    rule.capacity,
                ],
            )
        try:
            raw: list[int] = await self._redis.eval(
                _QUOTA_LUA,
                len(keys),
                *keys,
                *args,
            )
        except Exception:
            if self._fail_open:
                first = rules[0]
                return QuotaResult(
                    allowed=True,
                    limit=first.max_requests,
                    remaining=first.max_requests - 1,
                    reset_seconds=math.ceil(first.window_seconds),
                    retry_after=0,
                    scope=first.effective_scope,
                )
            raise
        allowed = bool(raw[0])
        retry_after = 0 if allowed else max(1, math.ceil(int(raw[4]) / 1000))
        return QuotaResult(
            allowed=allowed,
            limit=int(raw[1]),
            remaining=int(raw[2]),
            reset_seconds=max(0, math.ceil(int(raw[3]) / 1000)),
            retry_after=retry_after,
            scope=rules[int(raw[5]) - 1].effective_scope,
        )


@runtime_checkable
class RateLimitPolicy(Protocol):
    """Resolves which limits apply to a given request."""

    def rules_for(self, request: Request) -> Sequence[RateLimitRule]:
        """Return the rules to charge this request against.

        Args:
            request (Request): The inbound request.

        Returns:
            Sequence[RateLimitRule]: Limits applied together. Must not
            be empty — an empty list would silently disable the limit.
        """
        ...


class PlanRateLimitPolicy:
    """Per-plan quotas — one rule list per tier, resolved per request.

    The resolver runs on the raw request (the middleware executes before
    FastAPI resolves dependencies), so it reads the plan from a token
    claim or a header. A plan name the mapping does not know falls back
    to ``default_plan`` instead of raising: an unknown tier must be
    limited, not served unlimited, and a request is the wrong place to
    discover a configuration typo.
    """

    def __init__(
        self,
        plans: Mapping[str, Sequence[RateLimitRule]],
        *,
        resolve: Callable[[Request], str | None],
        default_plan: str,
    ) -> None:
        """Initialize the policy.

        Args:
            plans (Mapping[str, Sequence[RateLimitRule]]): Rule list per
                plan name (e.g. ``{"free": [...], "pro": [...]}``).
            resolve (Callable[[Request], str | None]): Extracts the plan
                name from the request. ``None`` selects ``default_plan``.
            default_plan (str): Plan used for anonymous traffic and for
                names missing from ``plans``.

        Raises:
            ValueError: If ``plans`` is empty, if ``default_plan`` is not
                one of its keys, or if any plan carries no rule. Each
                would only surface as unlimited traffic at runtime.
        """
        if not plans:
            raise ValueError("plans must not be empty")
        if default_plan not in plans:
            raise ValueError(f"default_plan {default_plan!r} is not a plan name")
        for name, rules in plans.items():
            if not rules:
                raise ValueError(f"plan {name!r} must declare at least one rule")
        self._plans: dict[str, tuple[RateLimitRule, ...]] = {
            name: tuple(rules) for name, rules in plans.items()
        }
        self._resolve: Callable[[Request], str | None] = resolve
        self._default_plan: str = default_plan

    def plan_for(self, request: Request) -> str:
        """Return the plan name applied to ``request``.

        Args:
            request (Request): The inbound request.

        Returns:
            str: The resolved plan name, or ``default_plan`` when the
            resolver yields ``None`` or an unknown name.
        """
        name = self._resolve(request)
        if name is None or name not in self._plans:
            return self._default_plan
        return name

    def rules_for(self, request: Request) -> Sequence[RateLimitRule]:
        """Return the rule list of the request's plan.

        Args:
            request (Request): The inbound request.

        Returns:
            Sequence[RateLimitRule]: The plan's limits.
        """
        return self._plans[self.plan_for(request)]


class StaticRateLimitPolicy:
    """One rule list for every request — quotas without tiers.

    The simplest way to use a token bucket or stack a per-minute limit
    under a daily ceiling when the service has a single tier.
    """

    def __init__(self, rules: Sequence[RateLimitRule]) -> None:
        """Initialize the policy.

        Args:
            rules (Sequence[RateLimitRule]): Limits applied to every
                request.

        Raises:
            ValueError: If ``rules`` is empty.
        """
        if not rules:
            raise ValueError("rules must not be empty")
        self._rules: tuple[RateLimitRule, ...] = tuple(rules)

    def rules_for(self, request: Request) -> Sequence[RateLimitRule]:
        """Return the configured rules.

        Args:
            request (Request): The inbound request (unused).

        Returns:
            Sequence[RateLimitRule]: The configured limits.
        """
        return self._rules


@runtime_checkable
class _JWTDecoder(Protocol):
    """Minimal JWT decoder surface used by :func:`plan_by_jwt_claim`.

    Matches :meth:`tempest_fastapi_sdk.utils.JWTUtils.decode_or_none`.
    """

    def decode_or_none(self, token: str) -> dict[str, Any] | None:
        """Decode a token, returning ``None`` when it is missing/invalid.

        Args:
            token (str): The bearer token to decode.

        Returns:
            dict[str, Any] | None: The decoded claims, or ``None``.
        """
        ...


def _bearer_token(request: Request) -> str | None:
    """Extract the bearer token from the ``Authorization`` header.

    Args:
        request (Request): The inbound request.

    Returns:
        str | None: The token, or ``None`` when no bearer is present.
    """
    header = request.headers.get("authorization")
    if not header:
        return None
    parts = header.split(maxsplit=1)
    if len(parts) == 2 and parts[0].lower() == "bearer":
        return parts[1].strip()
    return None


def plan_by_jwt_claim(
    jwt: _JWTDecoder,
    claim: str = "plan",
) -> Callable[[Request], str | None]:
    """Build a plan resolver reading a claim from the bearer token.

    The token is decoded opportunistically — a missing or invalid token
    yields ``None``, which the policy maps to its default plan, so
    anonymous traffic is still limited.

    Args:
        jwt (_JWTDecoder): A decoder exposing ``decode_or_none`` (e.g.
            :class:`tempest_fastapi_sdk.utils.JWTUtils`).
        claim (str): Claim holding the plan name. Defaults to ``"plan"``.

    Returns:
        Callable[[Request], str | None]: The resolver.
    """

    def _resolve(request: Request) -> str | None:
        token = _bearer_token(request)
        if not token:
            return None
        claims = jwt.decode_or_none(token)
        if claims is None:
            return None
        value = claims.get(claim)
        return str(value) if value is not None else None

    return _resolve


def plan_by_header(header_name: str) -> Callable[[Request], str | None]:
    """Build a plan resolver reading the plan name from a header.

    Only safe when the header is set by your own edge (an API gateway
    that already authenticated the caller). A client-supplied header
    lets anyone name their own tier.

    Args:
        header_name (str): Header carrying the plan name.

    Returns:
        Callable[[Request], str | None]: The resolver.
    """

    def _resolve(request: Request) -> str | None:
        value = request.headers.get(header_name)
        return value.strip() if value else None

    return _resolve


def key_by_plan_principal(
    policy: PlanRateLimitPolicy,
    key_func: Callable[[Request], str],
) -> Callable[[Request], str]:
    """Prefix a principal key with the plan that resolved for it.

    Useful when a principal can move between tiers and you want the
    upgrade to take effect immediately: the new plan writes to fresh
    counters instead of inheriting the exhausted ones. The trade-off is
    that a caller able to *choose* their plan (a client-supplied header)
    could reset their own counters by switching — pair it with a
    resolver your edge controls.

    Args:
        policy (PlanRateLimitPolicy): Policy resolving the plan name.
        key_func (Callable[[Request], str]): Principal key function
            (e.g. :func:`~tempest_fastapi_sdk.key_by_jwt_subject`).

    Returns:
        Callable[[Request], str]: A key function yielding
        ``"<plan>:<principal key>"``.
    """

    def _key(request: Request) -> str:
        return f"{policy.plan_for(request)}:{key_func(request)}"

    return _key


__all__: list[str] = [
    "MemoryQuotaStore",
    "PlanRateLimitPolicy",
    "QuotaResult",
    "QuotaStore",
    "RateLimitPolicy",
    "RateLimitRule",
    "RedisQuotaStore",
    "StaticRateLimitPolicy",
    "key_by_plan_principal",
    "plan_by_header",
    "plan_by_jwt_claim",
]
