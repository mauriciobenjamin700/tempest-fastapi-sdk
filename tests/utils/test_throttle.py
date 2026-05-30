"""Tests for tempest_fastapi_sdk.utils.throttle."""

from typing import Any

import pytest

from tempest_fastapi_sdk import (
    AttemptThrottle,
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
