"""Tests for tempest_fastapi_sdk.utils.throttle."""

from typing import Any

import pytest

from tempest_fastapi_sdk import (
    AttemptThrottle,
    InMemoryThrottleBackend,
    ThrottleBackend,
    ThrottleStatus,
    TooManyRequestsException,
)


class FakeRedis:
    """Minimal async fixed-window backend backed by a dict."""

    def __init__(self) -> None:
        self.values: dict[str, int] = {}
        self.ttls: dict[str, int] = {}

    async def incr(self, name: str) -> int:
        self.values[name] = self.values.get(name, 0) + 1
        return self.values[name]

    async def expire(self, name: str, seconds: int) -> None:
        self.ttls[name] = seconds

    async def ttl(self, name: str) -> int:
        return self.ttls.get(name, -2)

    async def get(self, name: str) -> Any:
        return self.values.get(name)

    async def delete(self, name: str) -> None:
        self.values.pop(name, None)
        self.ttls.pop(name, None)


class ExplodingRedis:
    """Backend whose every operation raises, to exercise fail-open."""

    async def incr(self, name: str) -> int:
        raise RuntimeError("down")

    async def expire(self, name: str, seconds: int) -> None:
        raise RuntimeError("down")

    async def ttl(self, name: str) -> int:
        raise RuntimeError("down")

    async def get(self, name: str) -> Any:
        raise RuntimeError("down")

    async def delete(self, name: str) -> None:
        raise RuntimeError("down")


def _throttle(backend: Any, **kw: Any) -> AttemptThrottle:
    return AttemptThrottle(
        backend,
        max_attempts=kw.pop("max_attempts", 3),
        window_seconds=kw.pop("window_seconds", 900),
        **kw,
    )


class TestConfig:
    def test_rejects_bad_max_attempts(self) -> None:
        with pytest.raises(ValueError):
            AttemptThrottle(FakeRedis(), max_attempts=0, window_seconds=60)

    def test_rejects_bad_window(self) -> None:
        with pytest.raises(ValueError):
            AttemptThrottle(FakeRedis(), max_attempts=1, window_seconds=0)


class TestHitAndStatus:
    async def test_hit_increments_and_sets_ttl_once(self) -> None:
        redis = FakeRedis()
        t = _throttle(redis, window_seconds=900)
        await t.hit("k")
        assert redis.ttls["throttle:k"] == 900
        s = await t.hit("k")
        assert s.attempts == 2 and s.blocked is False

    async def test_blocks_at_max_attempts(self) -> None:
        t = _throttle(FakeRedis(), max_attempts=3)
        await t.hit("k")
        await t.hit("k")
        s = await t.hit("k")
        assert s.attempts == 3
        assert s.blocked is True
        assert s.retry_after_seconds > 0

    async def test_status_is_read_only(self) -> None:
        redis = FakeRedis()
        t = _throttle(redis)
        await t.hit("k")
        before = redis.values["throttle:k"]
        await t.status("k")
        assert redis.values["throttle:k"] == before

    async def test_reset_clears_counter(self) -> None:
        redis = FakeRedis()
        t = _throttle(redis)
        await t.hit("k")
        await t.reset("k")
        assert "throttle:k" not in redis.values
        assert (await t.status("k")).attempts == 0

    async def test_namespace_isolates_keys(self) -> None:
        redis = FakeRedis()
        a = _throttle(redis, namespace="login")
        b = _throttle(redis, namespace="otp")
        await a.hit("k")
        assert (await b.status("k")).attempts == 0


class TestRaiseIfBlocked:
    async def test_raises_when_blocked(self) -> None:
        t = _throttle(FakeRedis(), max_attempts=1)
        await t.hit("k")
        with pytest.raises(TooManyRequestsException) as exc:
            await t.raise_if_blocked("k")
        assert exc.value.status_code == 429
        assert "Retry-After" in (exc.value.headers or {})
        assert "retry_after_seconds" in exc.value.details

    async def test_returns_status_when_within_budget(self) -> None:
        t = _throttle(FakeRedis(), max_attempts=3)
        await t.hit("k")
        status = await t.raise_if_blocked("k")
        assert isinstance(status, ThrottleStatus)
        assert status.blocked is False


class TestFailOpen:
    async def test_fail_open_allows_on_backend_error(self) -> None:
        t = _throttle(ExplodingRedis(), fail_open=True)
        assert (await t.hit("k")).blocked is False
        assert (await t.status("k")).blocked is False
        await t.reset("k")  # must not raise

    async def test_fail_closed_propagates(self) -> None:
        t = _throttle(ExplodingRedis(), fail_open=False)
        with pytest.raises(RuntimeError):
            await t.hit("k")


class TestTheClientsTheRecipeNames:
    """The recipe promises two concrete clients; run against the real one.

    The dict double above never exercises what actually broke: it is written
    to match the protocol, so it cannot disagree with it. ``fakeredis``
    implements the ``redis-py`` surface — including calling ``expire``'s
    second parameter ``time`` — which is the shape the protocol got wrong
    through v0.262.0.
    """

    async def test_fakeredis_drives_a_full_window(self) -> None:
        """Count, block, expire and reset over the client the docs name."""
        fake_aioredis = pytest.importorskip("fakeredis.aioredis")
        client = fake_aioredis.FakeRedis()
        throttle = AttemptThrottle(
            client,
            max_attempts=2,
            window_seconds=60,
            fail_open=False,
        )

        assert (await throttle.hit("k")).attempts == 1
        second = await throttle.hit("k")
        assert second.attempts == 2
        assert second.blocked is True
        assert second.retry_after_seconds == 60

        with pytest.raises(TooManyRequestsException):
            await throttle.raise_if_blocked("k")

        await throttle.reset("k")
        assert (await throttle.status("k")) == ThrottleStatus(0, False, 0)

    async def test_the_ttl_the_first_failure_set_is_real(self) -> None:
        """``expire`` reached the client, whatever it calls its parameter."""
        fake_aioredis = pytest.importorskip("fakeredis.aioredis")
        client = fake_aioredis.FakeRedis()
        throttle = AttemptThrottle(
            client,
            max_attempts=5,
            window_seconds=900,
            fail_open=False,
        )

        await throttle.hit("k")

        assert await client.ttl("throttle:k") == 900


class _Clock:
    """Hand-driven clock so expiry is tested without sleeping."""

    def __init__(self) -> None:
        self.now: float = 1000.0

    def __call__(self) -> float:
        return self.now


class TestInMemoryThrottleBackend:
    """The bundled process-local backend honours the Redis contract."""

    def test_satisfies_the_protocol(self) -> None:
        """Assignable where the throttle expects a backend."""
        backend: ThrottleBackend = InMemoryThrottleBackend()
        assert backend is not None

    async def test_ttl_contract_absent_unset_and_counting(self) -> None:
        """``-2`` absent, ``-1`` without TTL, seconds rounded up after."""
        clock = _Clock()
        backend = InMemoryThrottleBackend(clock=clock)
        assert await backend.ttl("k") == -2
        assert await backend.get("k") is None
        assert await backend.incr("k") == 1
        assert await backend.ttl("k") == -1
        await backend.expire("k", 60)
        clock.now += 59.5
        assert await backend.ttl("k") == 1
        assert await backend.get("k") == "1"

    async def test_window_expires_on_its_own(self) -> None:
        """Once the TTL passes the key is gone and counting restarts."""
        clock = _Clock()
        throttle = AttemptThrottle(
            InMemoryThrottleBackend(clock=clock),
            max_attempts=2,
            window_seconds=900,
        )
        await throttle.hit("k")
        blocked = await throttle.hit("k")
        assert blocked.blocked is True
        assert blocked.retry_after_seconds == 900

        clock.now += 899
        assert (await throttle.status("k")).blocked is True

        clock.now += 1
        assert await throttle.status("k") == ThrottleStatus(0, False, 0)
        assert (await throttle.hit("k")).attempts == 1

    async def test_expire_on_absent_key_is_a_no_op(self) -> None:
        """Like ``EXPIRE``, a missing key is not created."""
        backend = InMemoryThrottleBackend()
        await backend.expire("missing", 60)
        assert await backend.ttl("missing") == -2

    async def test_delete_and_reset(self) -> None:
        """``reset`` clears the key whether or not it existed."""
        throttle = AttemptThrottle(
            InMemoryThrottleBackend(),
            max_attempts=1,
            window_seconds=60,
        )
        await throttle.reset("never-set")
        await throttle.hit("k")
        with pytest.raises(TooManyRequestsException):
            await throttle.raise_if_blocked("k")
        await throttle.reset("k")
        assert await throttle.status("k") == ThrottleStatus(0, False, 0)

    async def test_expired_entries_are_evicted_on_write(self) -> None:
        """A key nobody touches again does not stay in the dict."""
        clock = _Clock()
        backend = InMemoryThrottleBackend(clock=clock)
        await backend.incr("old")
        await backend.expire("old", 10)
        clock.now += 11
        await backend.incr("new")
        assert "old" not in backend._counters

    async def test_matches_fakeredis_step_for_step(self) -> None:
        """Same operations, same answers as the client the recipe names."""
        fake_aioredis = pytest.importorskip("fakeredis.aioredis")
        redis = fake_aioredis.FakeRedis(decode_responses=True)
        memory = InMemoryThrottleBackend()
        for backend in (redis, memory):
            assert await backend.ttl("k") == -2
            assert await backend.incr("k") == 1
            assert await backend.ttl("k") == -1
            await backend.expire("k", 900)
            assert await backend.incr("k") == 2
            assert await backend.ttl("k") == 900
            assert await backend.get("k") == "2"
            await backend.delete("k")
            assert await backend.get("k") is None
