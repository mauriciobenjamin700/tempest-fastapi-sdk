"""Tests for ``async_retry`` and the ``RetryPolicy`` it applies.

The policy's arithmetic was already covered where it lived; what is new
is that something can now *apply* it to a coroutine. The cases below are
the three decisions a hand-written retry loop tends to get wrong: which
exceptions consume an attempt, what surfaces when the budget runs out,
and whether a cancellation is fought or honored.
"""

import asyncio
import logging

import pytest

from tempest_fastapi_sdk import (
    LogUtils,
    RetryLogger,
    RetryPolicy,
    async_retry,
    reinitialize_logging,
)

FAST = RetryPolicy(max_attempts=3, backoff_initial_seconds=0.001)


class TestSuccess:
    """A call that eventually works."""

    async def test_returns_without_retrying_when_it_works(self) -> None:
        calls = {"n": 0}

        @async_retry(FAST)
        async def ok() -> str:
            calls["n"] += 1
            return "done"

        assert await ok() == "done"
        assert calls["n"] == 1

    async def test_retries_until_it_succeeds(self) -> None:
        calls = {"n": 0}

        @async_retry(FAST, (ConnectionError,))
        async def flaky() -> str:
            calls["n"] += 1
            if calls["n"] < 3:
                raise ConnectionError("transient")
            return "done"

        assert await flaky() == "done"
        assert calls["n"] == 3

    async def test_forwards_arguments_and_keeps_identity(self) -> None:
        @async_retry(FAST)
        async def add(left: int, *, right: int) -> int:
            """Add two numbers."""
            return left + right

        assert await add(2, right=3) == 5
        assert add.__name__ == "add"
        assert add.__doc__ == "Add two numbers."


class TestExhaustion:
    """What a caller sees when every attempt failed."""

    async def test_the_last_exception_propagates(self) -> None:
        """Not a wrapper: a caller reacting to ConnectionError must see one."""
        attempts: list[str] = []

        @async_retry(FAST, (ConnectionError,))
        async def always() -> None:
            attempts.append("try")
            raise ConnectionError(f"failure {len(attempts)}")

        with pytest.raises(ConnectionError, match="failure 3"):
            await always()

        assert len(attempts) == 3

    async def test_a_single_attempt_policy_does_not_retry(self) -> None:
        calls = {"n": 0}

        @async_retry(RetryPolicy(max_attempts=1), (ValueError,))
        async def once() -> None:
            calls["n"] += 1
            raise ValueError("no budget")

        with pytest.raises(ValueError):
            await once()

        assert calls["n"] == 1


class TestWhatCountsAsTransient:
    """Retrying the wrong exception turns one bug into the same bug, later."""

    async def test_an_unlisted_exception_short_circuits(self) -> None:
        calls = {"n": 0}

        @async_retry(FAST, (ConnectionError,))
        async def wrong() -> None:
            calls["n"] += 1
            raise TypeError("a bug, not a blip")

        with pytest.raises(TypeError):
            await wrong()

        assert calls["n"] == 1

    async def test_cancellation_is_honored_not_retried(self) -> None:
        """``CancelledError`` derives from ``BaseException`` since 3.8.

        The default ``(Exception,)`` therefore lets it through. Retrying
        a cancelled task fights the cancellation instead of honoring it.
        """
        calls = {"n": 0}

        @async_retry(FAST)
        async def cancelled() -> None:
            calls["n"] += 1
            raise asyncio.CancelledError

        with pytest.raises(asyncio.CancelledError):
            await cancelled()

        assert calls["n"] == 1


class TestBackoff:
    """The waiting comes from the policy, not from the decorator."""

    async def test_sleeps_follow_the_policy_curve(
        self,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        slept: list[float] = []

        async def fake_sleep(delay: float) -> None:
            slept.append(delay)

        monkeypatch.setattr(asyncio, "sleep", fake_sleep)

        policy = RetryPolicy(
            max_attempts=4,
            backoff_initial_seconds=0.5,
            backoff_max_seconds=1.5,
        )

        @async_retry(policy, (ValueError,))
        async def always() -> None:
            raise ValueError("nope")

        with pytest.raises(ValueError):
            await always()

        assert slept == [
            policy.sleep_for(1),
            policy.sleep_for(2),
            policy.sleep_for(3),
        ]
        assert slept == [0.5, 1.0, 1.5]

    async def test_no_sleep_after_the_final_attempt(
        self,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """Sleeping after the last try only delays the failure."""
        slept: list[float] = []

        async def fake_sleep(delay: float) -> None:
            slept.append(delay)

        monkeypatch.setattr(asyncio, "sleep", fake_sleep)

        @async_retry(RetryPolicy(max_attempts=2), (ValueError,))
        async def always() -> None:
            raise ValueError("nope")

        with pytest.raises(ValueError):
            await always()

        assert len(slept) == 1


class TestPolicyValidation:
    """A budget below one would return ``None`` from something typed ``T``."""

    def test_zero_attempts_is_refused_at_decoration_time(self) -> None:
        with pytest.raises(ValueError, match="max_attempts"):
            async_retry(RetryPolicy(max_attempts=0))

    async def test_the_default_policy_is_not_shared_state(self) -> None:
        """``RetryPolicy`` is a mutable dataclass, so a module-level
        default instance would be one object every decorated function
        points at.
        """

        @async_retry()
        async def first() -> None:
            return None

        @async_retry()
        async def second() -> None:
            return None

        await first()
        await second()


class TestLoggerAcceptsBothFacades:
    """``logger=`` takes whatever writes a WARNING and an ERROR.

    Typing the parameter ``logging.Logger`` excluded ``LogUtils`` —
    the logger this same SDK hands the service. The call worked at
    runtime, because the shapes match, and mypy reported ``Argument
    "logger" to "async_retry" has incompatible type "LogUtils";
    expected "Logger | None"``. A service without a type-checker never
    saw it; one with a type-checker wrote ``logger=logger.logger``,
    reaching past the facade to the object it wraps.
    """

    def test_logging_logger_satisfies_the_protocol(self) -> None:
        assert isinstance(logging.getLogger("t"), RetryLogger)

    def test_logutils_satisfies_the_protocol(self) -> None:
        assert isinstance(LogUtils.__new__(LogUtils), RetryLogger)

    @pytest.mark.asyncio
    async def test_logutils_receives_the_retry_and_give_up_records(
        self,
    ) -> None:
        captured: list[logging.LogRecord] = []

        class Grab(logging.Handler):
            def emit(self, record: logging.LogRecord) -> None:
                captured.append(record)

        reinitialize_logging()
        facade = LogUtils(name="tempest.retry.facade", file_output=False)
        facade.logger.handlers = [Grab()]
        facade.logger.propagate = False

        @async_retry(FAST, (ConnectionError,), logger=facade)
        async def always_fails() -> None:
            """Fail every time so both record kinds are written."""
            raise ConnectionError("down")

        with pytest.raises(ConnectionError):
            await always_fails()

        levels = [record.levelname for record in captured]
        assert levels == ["WARNING", "WARNING", "ERROR"]
        assert "gave up after 3 attempts" in captured[-1].getMessage()
