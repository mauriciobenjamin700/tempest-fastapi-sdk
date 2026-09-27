"""Tests for token-bucket rules and per-plan quotas.

The Redis half runs the **real** Lua script through ``fakeredis`` (which
executes Lua via ``lupa``), not a Python re-implementation of its
contract: the atomicity this feature promises lives inside the script,
so a fake that emulates the contract would assert nothing about it.
"""

from __future__ import annotations

import time
from collections.abc import Callable
from types import SimpleNamespace
from typing import Any

import pytest
from fakeredis import aioredis as fake_aioredis
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient
from starlette.requests import Request

from tempest_fastapi_sdk import (
    MemoryQuotaStore,
    PlanRateLimitPolicy,
    QuotaStore,
    RateLimitMiddleware,
    RateLimitRule,
    RedisQuotaStore,
    RedisRateLimitStore,
    StaticRateLimitPolicy,
    key_by_ip,
    key_by_plan_principal,
    plan_by_header,
    plan_by_jwt_claim,
)


def _request(headers: dict[str, str] | None = None) -> Request:
    """Build a bare Starlette request with the given headers.

    Args:
        headers (dict[str, str] | None): Headers to attach.

    Returns:
        Request: A request usable by key/plan resolvers.
    """
    raw = [(k.lower().encode(), v.encode()) for k, v in (headers or {}).items()]
    return Request(
        {
            "type": "http",
            "method": "GET",
            "path": "/",
            "headers": raw,
            "client": ("1.2.3.4", 1234),
            "query_string": b"",
        },
    )


class _Clock:
    """A clock that only moves when the test moves it.

    Stores read the time on every call, so a real clock lets the
    machine's speed decide how much refill happens between two calls —
    the cause of #339, where the gap between four sequential calls
    outgrew the 10 ms a token took to come back on a loaded runner.

    Attributes:
        now (float): Current reading, in seconds.
    """

    def __init__(self, start: float) -> None:
        """Start the clock at ``start``.

        Args:
            start (float): The first reading, in seconds.
        """
        self.now: float = start

    def __call__(self) -> float:
        """Read the clock.

        Returns:
            float: The current reading, in seconds.
        """
        return self.now

    def advance(self, seconds: float) -> None:
        """Move the clock forward.

        Args:
            seconds (float): How far to move it.
        """
        self.now += seconds


EPOCH: float = 1_790_000_000.0
"""Where every fixed clock starts: a plausible Unix time, in seconds."""


def _pin_fakeredis_clock(monkeypatch: pytest.MonkeyPatch, clock: _Clock) -> None:
    """Make ``fakeredis`` read ``clock`` for ``TIME`` and for key expiry.

    The scripts read the time with ``redis.call('TIME')``, which
    ``fakeredis`` answers from ``time.time()`` in its server mixin; key
    expiry reads the same function in its socket module. Both are
    pinned, so moving ``clock`` ages scripts and TTLs together, as it
    would on a real server.

    Args:
        monkeypatch (pytest.MonkeyPatch): Restores both modules
            afterwards. ``raising`` stays on, so a ``fakeredis`` that
            reads its clock elsewhere fails here instead of silently
            going unpinned.
        clock (_Clock): The clock the server should read.
    """
    pinned = SimpleNamespace(time=clock)
    monkeypatch.setattr("fakeredis.commands_mixins.server_mixin.time", pinned)
    monkeypatch.setattr("fakeredis._basefakesocket.time", pinned)


def _memory_store(clock: _Clock, monkeypatch: pytest.MonkeyPatch) -> QuotaStore:
    """Build an in-process quota store reading ``clock``.

    Args:
        clock (_Clock): The store's clock.
        monkeypatch (pytest.MonkeyPatch): Unused; the factories share a
            signature.

    Returns:
        QuotaStore: A fresh :class:`MemoryQuotaStore`.
    """
    return MemoryQuotaStore(clock=clock)


def _redis_store(clock: _Clock, monkeypatch: pytest.MonkeyPatch) -> QuotaStore:
    """Build a Redis quota store over an isolated fake server on ``clock``.

    Args:
        clock (_Clock): The server's clock.
        monkeypatch (pytest.MonkeyPatch): Pins the server's clock.

    Returns:
        QuotaStore: A fresh :class:`RedisQuotaStore`.
    """
    _pin_fakeredis_clock(monkeypatch, clock)
    return RedisQuotaStore(fake_aioredis.FakeRedis())


STORES: list[Callable[[_Clock, pytest.MonkeyPatch], QuotaStore]] = [
    _memory_store,
    _redis_store,
]
"""Store **factories** — a shared instance would leak state between tests."""


@pytest.fixture
def clock() -> _Clock:
    """Yield a clock that stands still until the test advances it.

    Returns:
        _Clock: A clock at :data:`EPOCH`.
    """
    return _Clock(EPOCH)


@pytest.fixture(params=STORES, ids=["memory", "redis"])
def store(
    request: pytest.FixtureRequest,
    clock: _Clock,
    monkeypatch: pytest.MonkeyPatch,
) -> QuotaStore:
    """Yield a fresh quota store on ``clock``, once per implementation.

    Args:
        request (pytest.FixtureRequest): Carries the store factory.
        clock (_Clock): The clock the store reads.
        monkeypatch (pytest.MonkeyPatch): Lets a factory pin a clock.

    Returns:
        QuotaStore: A store nobody else has written to.
    """
    factory: Callable[[_Clock, pytest.MonkeyPatch], QuotaStore] = request.param
    return factory(clock, monkeypatch)


# --------------------------------------------------------------------- #
# RateLimitRule                                                          #
# --------------------------------------------------------------------- #


def test_rule_rejects_invalid_numbers() -> None:
    """Every impossible rule is refused at construction."""
    with pytest.raises(ValueError, match="max_requests"):
        RateLimitRule(max_requests=0, window_seconds=1.0)
    with pytest.raises(ValueError, match="window_seconds"):
        RateLimitRule(max_requests=1, window_seconds=0.0)
    with pytest.raises(ValueError, match="burst"):
        RateLimitRule(max_requests=1, window_seconds=1.0, burst=0)


def test_rule_derives_algorithm_and_scope() -> None:
    """``burst`` selects the algorithm and feeds the derived scope."""
    window = RateLimitRule(max_requests=60, window_seconds=60.0)
    bucket = RateLimitRule(max_requests=600, window_seconds=60.0, burst=100)
    assert window.is_bucket is False
    assert window.capacity == 60
    assert window.effective_scope == "60/60s"
    assert bucket.is_bucket is True
    assert bucket.capacity == 100
    assert bucket.refill_per_second == 10.0
    assert bucket.effective_scope == "600/60s+b100"
    assert RateLimitRule(1, 1.0, scope="daily").effective_scope == "daily"


# --------------------------------------------------------------------- #
# Store behavior (both implementations)                                  #
# --------------------------------------------------------------------- #


@pytest.mark.asyncio
async def test_window_rule_allows_then_rejects(store: QuotaStore) -> None:
    """A sliding-window rule serves N requests, then rejects."""
    rules = [RateLimitRule(max_requests=3, window_seconds=30.0)]
    results = [await store.consume("k", rules) for _ in range(4)]
    assert [r.allowed for r in results] == [True, True, True, False]
    assert [r.remaining for r in results] == [2, 1, 0, 0]
    assert results[-1].retry_after >= 1
    assert results[-1].limit == 3


@pytest.mark.asyncio
async def test_bucket_absorbs_burst_then_rejects(store: QuotaStore) -> None:
    """A token bucket serves its whole capacity at once, then rejects.

    The clock stands still, so no token comes back between the calls
    however slow the machine is, and the retry hint is at least the full
    refill time of one token. It is rounded up, not exact: the Redis
    script reports 3601 s here, from ``ceil`` over a float rate.
    """
    rules = [RateLimitRule(max_requests=1, window_seconds=3600.0, burst=3)]
    results = [await store.consume("k", rules) for _ in range(4)]
    assert [r.allowed for r in results] == [True, True, True, False]
    assert [r.remaining for r in results] == [2, 1, 0, 0]
    assert results[-1].retry_after >= 3600


@pytest.mark.asyncio
async def test_bucket_refills_over_time(store: QuotaStore, clock: _Clock) -> None:
    """A token comes back after one refill interval, and not before.

    The rule refills one token per hour, and only the test moves the
    clock. One second short of the interval the bucket is still empty;
    one second past it, exactly one token is back. The margin covers
    float rounding at the boundary: the Redis script holds the rate in
    tokens per millisecond, and ``3600000 * (1 / 3600000)`` is
    ``0.9999999999999999`` in double precision, so it still denies at
    exactly 3600 s where the memory store already allows.
    """
    rules = [RateLimitRule(max_requests=1, window_seconds=3600.0, burst=2)]
    assert (await store.consume("k", rules)).allowed
    assert (await store.consume("k", rules)).allowed
    assert not (await store.consume("k", rules)).allowed
    clock.advance(3599.0)
    early = await store.consume("k", rules)
    assert not early.allowed
    assert early.retry_after >= 1
    clock.advance(2.0)
    assert (await store.consume("k", rules)).allowed
    assert not (await store.consume("k", rules)).allowed


@pytest.mark.asyncio
async def test_rejection_by_one_rule_spends_nothing(store: QuotaStore) -> None:
    """A denied request must not consume the other rules' budget.

    The ceiling rule allows a single request; the per-minute rule allows
    many. After the ceiling is exhausted, the per-minute rule must still
    hold every token it never served.
    """
    rules = [
        RateLimitRule(max_requests=50, window_seconds=60.0, scope="minute"),
        RateLimitRule(max_requests=1, window_seconds=3600.0, scope="ceiling"),
    ]
    first = await store.consume("k", rules)
    assert first.allowed
    for _ in range(5):
        denied = await store.consume("k", rules)
        assert not denied.allowed
        assert denied.scope == "ceiling"
    minute_only = await store.consume("k", [rules[0]])
    assert minute_only.allowed
    assert minute_only.remaining == 48


@pytest.mark.asyncio
async def test_binding_rule_is_the_tightest(store: QuotaStore) -> None:
    """The reported headroom describes the rule closest to its limit."""
    rules = [
        RateLimitRule(max_requests=1000, window_seconds=60.0, scope="minute"),
        RateLimitRule(max_requests=5, window_seconds=3600.0, scope="hour"),
    ]
    result = await store.consume("k", rules)
    assert result.scope == "hour"
    assert result.limit == 5
    assert result.remaining == 4


@pytest.mark.asyncio
async def test_keys_are_independent(store: QuotaStore) -> None:
    """Two principals never share counters."""
    rules = [RateLimitRule(max_requests=1, window_seconds=30.0)]
    assert (await store.consume("a", rules)).allowed
    assert (await store.consume("b", rules)).allowed
    assert not (await store.consume("a", rules)).allowed


@pytest.mark.asyncio
async def test_empty_rule_list_is_refused(store: QuotaStore) -> None:
    """An empty rule list would silently disable the limit."""
    with pytest.raises(ValueError, match="rules must not be empty"):
        await store.consume("k", [])


# --------------------------------------------------------------------- #
# MemoryQuotaStore specifics                                             #
# --------------------------------------------------------------------- #


@pytest.mark.asyncio
async def test_memory_sweep_keeps_a_slow_bucket() -> None:
    """A bucket that has not refilled yet survives the sweep.

    Evicting it would reset it to capacity — free burst for a caller
    that just exhausted it.
    """
    store = MemoryQuotaStore()
    rules = [RateLimitRule(max_requests=1, window_seconds=3600.0, burst=2)]
    await store.consume("k", rules)
    await store.consume("k", rules)
    assert not (await store.consume("k", rules)).allowed
    store._sweep(store._expires_at["k|" + rules[0].effective_scope] - 1.0)
    assert not (await store.consume("k", rules)).allowed


@pytest.mark.asyncio
async def test_memory_sweep_drops_expired_keys() -> None:
    """Past its expiry a key carries no information and is dropped."""
    store = MemoryQuotaStore()
    rules = [RateLimitRule(max_requests=1, window_seconds=30.0)]
    await store.consume("k", rules)
    assert store._windows
    store._sweep(store._expires_at["k|" + rules[0].effective_scope] + 1.0)
    assert not store._windows
    assert not store._expires_at


# --------------------------------------------------------------------- #
# RedisQuotaStore specifics                                              #
# --------------------------------------------------------------------- #


class _BrokenRedis:
    """Fake Redis whose ``eval`` always raises."""

    async def eval(self, script: str, numkeys: int, *args: Any) -> Any:
        """Fail every call.

        Args:
            script (str): Ignored.
            numkeys (int): Ignored.
            *args (Any): Ignored.

        Raises:
            RuntimeError: Always.
        """
        raise RuntimeError("redis down")


@pytest.mark.asyncio
async def test_redis_namespace_prefixes_keys() -> None:
    """The configured namespace scopes every key it writes."""
    redis = fake_aioredis.FakeRedis()
    store = RedisQuotaStore(redis, namespace="q")
    rules = [RateLimitRule(max_requests=2, window_seconds=30.0, scope="minute")]
    await store.consume("user:7", rules)
    assert await redis.keys("q:user:7|minute")


@pytest.mark.asyncio
async def test_redis_fails_open_by_default() -> None:
    """A backend outage must not lock every caller out."""
    store = RedisQuotaStore(_BrokenRedis())  # type: ignore[arg-type]
    result = await store.consume("k", [RateLimitRule(10, 60.0)])
    assert result.allowed
    assert result.remaining == 9


@pytest.mark.asyncio
async def test_redis_fail_closed_propagates() -> None:
    """``fail_open=False`` surfaces the backend error."""
    store = RedisQuotaStore(_BrokenRedis(), fail_open=False)  # type: ignore[arg-type]
    with pytest.raises(RuntimeError, match="redis down"):
        await store.consume("k", [RateLimitRule(10, 60.0)])


@pytest.mark.asyncio
async def test_redis_bucket_ttl_covers_a_full_refill() -> None:
    """The bucket key outlives the time it needs to refill.

    A TTL derived from the window instead of the refill time expires a
    slow, high-burst bucket long before it is full, and the next request
    finds a fresh (full) bucket.
    """
    redis = fake_aioredis.FakeRedis()
    store = RedisQuotaStore(redis)
    rule = RateLimitRule(max_requests=10, window_seconds=60.0, burst=1000)
    await store.consume("k", [rule])
    ttl_ms = await redis.pttl(f"quota:k|{rule.effective_scope}")
    assert ttl_ms > 60_000 * 2


# --------------------------------------------------------------------- #
# Server clock (both Redis stores)                                       #
# --------------------------------------------------------------------- #


SKEWS: list[tuple[float, float]] = [(0.0, 3600.0), (-3600.0, 0.0)]
"""``(drain_offset, probe_offset)`` pairs, in seconds of client wall clock.

The first drains the limit on time and probes from a client one hour
ahead; the second drains from a client one hour behind and probes on
time. Both handed out a free request when the scripts took the time
from the caller.
"""


_WALL_CLOCK: Callable[[], float] = time.time
"""The unpatched ``time.time``, captured before any test shifts it."""


def _skew_client_clock(monkeypatch: pytest.MonkeyPatch, offset: float) -> None:
    """Shift ``time.time()`` for the calling process by ``offset`` seconds.

    ``fakeredis`` is pinned to its own clock first
    (:func:`_pin_fakeredis_clock`), so this moves only what a client
    would read — the time a store would pass to Redis if it took it
    from the process.

    Args:
        monkeypatch (pytest.MonkeyPatch): Restores ``time.time``.
        offset (float): Seconds to add to the real wall clock.
    """
    monkeypatch.setattr(time, "time", lambda: _WALL_CLOCK() + offset)


@pytest.mark.asyncio
@pytest.mark.parametrize(("drain", "probe"), SKEWS, ids=["ahead", "behind"])
async def test_quota_clients_with_skewed_clocks_share_one_bucket(
    clock: _Clock,
    monkeypatch: pytest.MonkeyPatch,
    drain: float,
    probe: float,
) -> None:
    """Two clients an hour apart on the wall clock drain the same bucket.

    The bucket refills one token per hour. Taking the time from the
    caller, the client an hour ahead saw a full hour of refill, and the
    client an hour behind wrote a ``ts`` that gave the on-time client
    the same hour — each probe was allowed. With the time read from the
    server, the clients' clocks never enter the decision.
    """
    _pin_fakeredis_clock(monkeypatch, clock)
    server = fake_aioredis.FakeServer()
    draining = RedisQuotaStore(fake_aioredis.FakeRedis(server=server))
    probing = RedisQuotaStore(fake_aioredis.FakeRedis(server=server))
    rules = [RateLimitRule(max_requests=1, window_seconds=3600.0, burst=3)]
    _skew_client_clock(monkeypatch, drain)
    drained = [await draining.consume("k", rules) for _ in range(3)]
    assert all(result.allowed for result in drained)
    _skew_client_clock(monkeypatch, probe)
    denied = await probing.consume("k", rules)
    assert not denied.allowed
    assert denied.retry_after >= 3600


@pytest.mark.asyncio
@pytest.mark.parametrize(("drain", "probe"), SKEWS, ids=["ahead", "behind"])
async def test_rate_limit_clients_with_skewed_clocks_share_one_window(
    clock: _Clock,
    monkeypatch: pytest.MonkeyPatch,
    drain: float,
    probe: float,
) -> None:
    """The sliding-window store keeps one window for skewed clients too.

    Taking the time from the caller, the client an hour ahead pruned
    every hit as older than the window, and hits written by the client
    an hour behind were pruned by the next on-time call; the fourth hit
    was allowed either way.
    """
    _pin_fakeredis_clock(monkeypatch, clock)
    server = fake_aioredis.FakeServer()
    draining = RedisRateLimitStore(fake_aioredis.FakeRedis(server=server))
    probing = RedisRateLimitStore(fake_aioredis.FakeRedis(server=server))
    _skew_client_clock(monkeypatch, drain)
    hits = [await draining.hit("k", 3, 3600.0) for _ in range(3)]
    assert all(hit.allowed for hit in hits)
    _skew_client_clock(monkeypatch, probe)
    denied = await probing.hit("k", 3, 3600.0)
    assert not denied.allowed
    assert denied.retry_after == 3600


@pytest.mark.asyncio
async def test_redis_scripts_read_the_server_clock(
    clock: _Clock,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Moving only the server's clock refills the bucket.

    The converse of the skew cases: the client clock stays put, and the
    hour of refill comes entirely from ``TIME``. The burst of two keeps
    the key's TTL (two hours) past the advance, so the token that comes
    back is a refill and not a key that expired into a fresh bucket.
    """
    _pin_fakeredis_clock(monkeypatch, clock)
    store = RedisQuotaStore(fake_aioredis.FakeRedis())
    rules = [RateLimitRule(max_requests=1, window_seconds=3600.0, burst=2)]
    assert (await store.consume("k", rules)).allowed
    assert (await store.consume("k", rules)).allowed
    assert not (await store.consume("k", rules)).allowed
    clock.advance(3601.0)
    assert (await store.consume("k", rules)).allowed
    assert not (await store.consume("k", rules)).allowed


# --------------------------------------------------------------------- #
# Policies and resolvers                                                 #
# --------------------------------------------------------------------- #


def test_plan_policy_validates_its_configuration() -> None:
    """A misconfigured policy fails at construction, not per request."""
    rules = [RateLimitRule(10, 60.0)]
    with pytest.raises(ValueError, match="plans must not be empty"):
        PlanRateLimitPolicy({}, resolve=lambda r: None, default_plan="free")
    with pytest.raises(ValueError, match="not a plan name"):
        PlanRateLimitPolicy(
            {"free": rules},
            resolve=lambda r: None,
            default_plan="pro",
        )
    with pytest.raises(ValueError, match="at least one rule"):
        PlanRateLimitPolicy(
            {"free": []},
            resolve=lambda r: None,
            default_plan="free",
        )


def test_plan_policy_falls_back_to_the_default_plan() -> None:
    """Anonymous traffic and unknown tiers are limited, never unlimited."""
    free = [RateLimitRule(10, 60.0, scope="free")]
    pro = [RateLimitRule(1000, 60.0, scope="pro")]
    policy = PlanRateLimitPolicy(
        {"free": free, "pro": pro},
        resolve=plan_by_header("x-plan"),
        default_plan="free",
    )
    assert policy.plan_for(_request()) == "free"
    assert policy.plan_for(_request({"x-plan": "enterprise"})) == "free"
    assert policy.plan_for(_request({"x-plan": "pro"})) == "pro"
    assert list(policy.rules_for(_request({"x-plan": "pro"}))) == pro


def test_static_policy_applies_one_list() -> None:
    """The single-tier policy returns exactly what it was given."""
    rules = [RateLimitRule(10, 60.0, burst=20)]
    policy = StaticRateLimitPolicy(rules)
    assert list(policy.rules_for(_request())) == rules
    with pytest.raises(ValueError, match="rules must not be empty"):
        StaticRateLimitPolicy([])


class _Decoder:
    """JWT decoder stub returning fixed claims for one token."""

    def __init__(self, claims: dict[str, Any] | None) -> None:
        """Store the claims to answer with.

        Args:
            claims (dict[str, Any] | None): Claims, or ``None`` for an
                undecodable token.
        """
        self._claims = claims

    def decode_or_none(self, token: str) -> dict[str, Any] | None:
        """Return the configured claims.

        Args:
            token (str): Ignored.

        Returns:
            dict[str, Any] | None: The configured claims.
        """
        return self._claims


def test_plan_by_jwt_claim_reads_the_token() -> None:
    """The plan comes from a claim; a bad token yields no plan."""
    resolve = plan_by_jwt_claim(_Decoder({"plan": "pro"}))
    assert resolve(_request({"authorization": "Bearer x"})) == "pro"
    assert resolve(_request()) is None
    assert (
        plan_by_jwt_claim(_Decoder(None))(
            _request({"authorization": "Bearer x"}),
        )
        is None
    )
    assert (
        plan_by_jwt_claim(_Decoder({}))(
            _request({"authorization": "Bearer x"}),
        )
        is None
    )


def test_key_by_plan_principal_prefixes_the_plan() -> None:
    """Moving tier writes to fresh counters instead of inheriting them."""
    policy = PlanRateLimitPolicy(
        {"free": [RateLimitRule(1, 60.0)], "pro": [RateLimitRule(10, 60.0)]},
        resolve=plan_by_header("x-plan"),
        default_plan="free",
    )
    key = key_by_plan_principal(policy, key_by_ip())
    assert key(_request()) == "free:ip:1.2.3.4"
    assert key(_request({"x-plan": "pro"})) == "pro:ip:1.2.3.4"


# --------------------------------------------------------------------- #
# Middleware integration                                                 #
# --------------------------------------------------------------------- #


def _app(**kwargs: object) -> FastAPI:
    """Build a one-route app behind the rate-limit middleware.

    Args:
        **kwargs (object): Middleware keyword arguments.

    Returns:
        FastAPI: The configured application.
    """
    app = FastAPI()
    app.add_middleware(RateLimitMiddleware, **kwargs)

    @app.get("/ping")
    async def ping() -> dict[str, bool]:
        return {"ok": True}

    return app


def test_policy_and_store_together_are_refused() -> None:
    """The two modes select different backends — picking silently is worse."""
    with pytest.raises(ValueError, match="not both"):
        RateLimitMiddleware(
            _app(),
            policy=StaticRateLimitPolicy([RateLimitRule(1, 60.0)]),
            store=MemoryQuotaStore(),  # type: ignore[arg-type]
        )


@pytest.mark.asyncio
async def test_middleware_enforces_the_policy() -> None:
    """Requests past the burst get a 429 with Retry-After."""
    app = _app(
        policy=StaticRateLimitPolicy(
            [RateLimitRule(max_requests=1, window_seconds=3600.0, burst=2)],
        ),
    )
    async with AsyncClient(
        transport=ASGITransport(app=app),
        base_url="http://test",
    ) as client:
        responses = [await client.get("/ping") for _ in range(3)]
    assert [r.status_code for r in responses] == [200, 200, 429]
    assert int(responses[-1].headers["Retry-After"]) >= 1


@pytest.mark.asyncio
async def test_policy_mode_advertises_the_limit() -> None:
    """Allowed and rejected responses both carry the RateLimit headers."""
    app = _app(
        policy=StaticRateLimitPolicy(
            [RateLimitRule(max_requests=2, window_seconds=60.0)],
        ),
    )
    async with AsyncClient(
        transport=ASGITransport(app=app),
        base_url="http://test",
    ) as client:
        first = await client.get("/ping")
        await client.get("/ping")
        denied = await client.get("/ping")
    assert first.headers["RateLimit-Limit"] == "2"
    assert first.headers["RateLimit-Remaining"] == "1"
    assert int(first.headers["RateLimit-Reset"]) > 0
    assert denied.headers["RateLimit-Remaining"] == "0"


@pytest.mark.asyncio
async def test_window_mode_omits_an_unknown_reset() -> None:
    """The sliding-window store reports no reset for an allowed request."""
    app = _app(max_requests=1, window_seconds=60.0)
    async with AsyncClient(
        transport=ASGITransport(app=app),
        base_url="http://test",
    ) as client:
        allowed = await client.get("/ping")
        denied = await client.get("/ping")
    assert allowed.headers["RateLimit-Limit"] == "1"
    assert "RateLimit-Reset" not in allowed.headers
    assert denied.headers["RateLimit-Reset"] == denied.headers["Retry-After"]


@pytest.mark.asyncio
async def test_limit_headers_can_be_turned_off() -> None:
    """``limit_headers=False`` keeps the responses byte-identical to before."""
    app = _app(max_requests=1, window_seconds=60.0, limit_headers=False)
    async with AsyncClient(
        transport=ASGITransport(app=app),
        base_url="http://test",
    ) as client:
        allowed = await client.get("/ping")
        denied = await client.get("/ping")
    assert "RateLimit-Limit" not in allowed.headers
    assert "RateLimit-Limit" not in denied.headers
    assert denied.headers["Retry-After"]


@pytest.mark.asyncio
async def test_per_plan_quotas_differ_by_tier() -> None:
    """Each tier gets its own ceiling, resolved from the request."""
    policy = PlanRateLimitPolicy(
        {
            "free": [RateLimitRule(max_requests=1, window_seconds=3600.0)],
            "pro": [RateLimitRule(max_requests=5, window_seconds=3600.0)],
        },
        resolve=plan_by_header("x-plan"),
        default_plan="free",
    )
    app = _app(policy=policy, key_func=key_by_plan_principal(policy, key_by_ip()))
    async with AsyncClient(
        transport=ASGITransport(app=app),
        base_url="http://test",
    ) as client:
        free = [(await client.get("/ping")).status_code for _ in range(2)]
        pro = [
            (await client.get("/ping", headers={"x-plan": "pro"})).status_code
            for _ in range(5)
        ]
    assert free == [200, 429]
    assert pro == [200] * 5
