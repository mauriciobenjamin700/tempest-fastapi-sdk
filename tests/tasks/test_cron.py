"""Tests for the human-friendly cron helpers."""

from __future__ import annotations

from datetime import timedelta

import pytest
from taskiq import InMemoryBroker

from tempest_fastapi_sdk.tasks import (
    Cron,
    CronOffset,
    TaskQueue,
    Weekday,
    daily,
    every_minute,
    every_n_minutes,
    hourly,
    monthly,
    weekdays,
    weekends,
    weekly,
)


class TestBuilders:
    def test_every_minute(self) -> None:
        assert every_minute() == "* * * * *"

    def test_every_n_minutes(self) -> None:
        assert every_n_minutes(5) == "*/5 * * * *"

    def test_hourly(self) -> None:
        assert hourly(30) == "30 * * * *"

    def test_daily(self) -> None:
        assert daily(9, 15) == "15 9 * * *"

    def test_weekly_with_enum(self) -> None:
        assert weekly(Weekday.MON, 9) == "0 9 * * MON"

    def test_weekly_with_raw_token(self) -> None:
        assert weekly("MON-FRI", 9) == "0 9 * * MON-FRI"

    def test_weekdays(self) -> None:
        assert weekdays(9) == "0 9 * * MON-FRI"

    def test_weekends(self) -> None:
        assert weekends(10) == "0 10 * * SAT,SUN"

    def test_monthly(self) -> None:
        assert monthly(1) == "0 0 1 * *"

    @pytest.mark.parametrize(
        "call",
        [
            lambda: daily(24),
            lambda: daily(0, 60),
            lambda: hourly(60),
            lambda: every_n_minutes(0),
            lambda: monthly(32),
        ],
    )
    def test_out_of_range_raises(self, call: object) -> None:
        with pytest.raises(ValueError):
            call()  # type: ignore[operator]


class TestEnums:
    def test_cron_presets_are_strings(self) -> None:
        assert Cron.EVERY_WEEKDAY_9AM == "0 9 * * MON-FRI"
        assert Cron.DAILY_9AM == "0 9 * * *"

    def test_offsets(self) -> None:
        assert CronOffset.BRASILIA == "-03:00"
        assert CronOffset.UTC == "+00:00"


class TestWiring:
    async def test_cron_enum_and_offset_coerced_to_plain_str(self) -> None:
        tq = TaskQueue(InMemoryBroker())

        @tq.cron(Cron.EVERY_WEEKDAY_9AM, cron_offset=CronOffset.BRASILIA)
        async def digest() -> None:
            return None

        sched = digest.taskiq_task.labels["schedule"][0]
        assert sched == {"cron": "0 9 * * MON-FRI", "cron_offset": "-03:00"}
        # coerced to plain str, not enum member
        assert type(sched["cron"]) is str
        assert type(sched["cron_offset"]) is str

    async def test_cron_accepts_builder_output(self) -> None:
        tq = TaskQueue(InMemoryBroker())

        @tq.cron(daily(9), cron_offset=CronOffset.BRASILIA)
        async def digest() -> None:
            return None

        assert digest.taskiq_task.labels["schedule"][0]["cron"] == "0 9 * * *"

    async def test_interval_still_works(self) -> None:
        tq = TaskQueue(InMemoryBroker())

        @tq.interval(timedelta(minutes=15))
        async def warm() -> None:
            return None

        assert warm.taskiq_task.labels["schedule"][0] == {
            "interval": timedelta(minutes=15),
        }
