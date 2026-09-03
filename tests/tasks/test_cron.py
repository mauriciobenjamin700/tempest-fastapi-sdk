"""Tests for the human-friendly cron helpers."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest
from taskiq import InMemoryBroker
from taskiq.cli.scheduler.run import is_cron_task_now

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
    normalize_cron_offset,
    normalize_schedule,
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
    async def test_cron_enum_and_offset_reach_the_schedule(self) -> None:
        tq = TaskQueue(InMemoryBroker())

        @tq.cron(Cron.EVERY_WEEKDAY_9AM, cron_offset=CronOffset.BRASILIA)
        async def digest() -> None:
            return None

        sched = digest.taskiq_task.labels["schedule"][0]
        assert sched == {
            "cron": "0 9 * * MON-FRI",
            "cron_offset": timedelta(hours=-3),
        }
        assert type(sched["cron"]) is str

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


def _utc(hour: int, minute: int = 0) -> datetime:
    """Return a UTC datetime on a fixed date, for cron predicates.

    Args:
        hour (int): Hour of day in UTC.
        minute (int): Minute past the hour.

    Returns:
        datetime: 2026-01-01 at ``hour:minute`` UTC, a date with no
        daylight-saving transition in any zone these tests name.
    """
    return datetime(2026, 1, 1, hour, minute, tzinfo=UTC)


class TestNormalizeCronOffset:
    """The two forms TaskIQ applies, and the refusal in between."""

    def test_cron_offset_member_becomes_a_timedelta(self) -> None:
        assert normalize_cron_offset(CronOffset.BRASILIA) == timedelta(hours=-3)
        assert normalize_cron_offset(CronOffset.UTC) == timedelta(0)
        assert normalize_cron_offset(CronOffset.ACRE) == timedelta(hours=-5)

    def test_numeric_string_becomes_a_timedelta(self) -> None:
        assert normalize_cron_offset("-03:30") == timedelta(hours=-3, minutes=-30)
        assert normalize_cron_offset("+05:45") == timedelta(hours=5, minutes=45)

    def test_timedelta_passes_through(self) -> None:
        delta = timedelta(hours=9)
        assert normalize_cron_offset(delta) is delta

    def test_resolvable_iana_key_passes_through(self) -> None:
        assert normalize_cron_offset("America/Sao_Paulo") == "America/Sao_Paulo"

    @pytest.mark.parametrize("value", ["-0300", "BRT", "", "Mars/Olympus"])
    def test_unresolvable_string_is_refused_with_both_forms_named(
        self,
        value: str,
    ) -> None:
        with pytest.raises(ValueError) as excinfo:
            normalize_cron_offset(value)
        message = str(excinfo.value)
        assert "±HH:MM" in message
        assert "America/Sao_Paulo" in message
        assert "tzdata" in message


class TestOffsetTakesEffect:
    """The declared offset changes when the task fires, not just its label.

    ``is_cron_task_now`` is the predicate ``SchedulerLoop`` evaluates on
    every tick, so asserting through it measures the firing hour rather
    than the spelling stored in the label.
    """

    def test_brasilia_shifts_the_firing_hour_by_three(self) -> None:
        offset = normalize_cron_offset(CronOffset.BRASILIA)
        assert is_cron_task_now("0 2 * * *", _utc(5), offset=offset) is True
        assert is_cron_task_now("0 2 * * *", _utc(2), offset=offset) is False

    def test_utc_member_leaves_the_hour_alone(self) -> None:
        offset = normalize_cron_offset(CronOffset.UTC)
        assert is_cron_task_now("0 2 * * *", _utc(2), offset=offset) is True
        assert is_cron_task_now("0 2 * * *", _utc(5), offset=offset) is False

    def test_iana_key_reaches_the_same_hour_as_the_fixed_offset(self) -> None:
        offset = normalize_cron_offset("America/Sao_Paulo")
        assert is_cron_task_now("0 2 * * *", _utc(5), offset=offset) is True

    def test_raw_member_value_would_have_raised(self) -> None:
        """The pre-normalization spelling is what killed the loop.

        ``CronOffset.BRASILIA`` is the string ``"-03:00"``, which TaskIQ
        passes to :class:`zoneinfo.ZoneInfo` as a key. This pins the
        reason normalization exists, so a future change back to the raw
        value fails here instead of in a consumer's scheduler.
        """
        with pytest.raises(Exception, match="No time zone found with key"):
            is_cron_task_now("0 2 * * *", _utc(5), offset=str(CronOffset.BRASILIA))


class TestNormalizeSchedule:
    """Raw specs get the same treatment, without losing their other keys."""

    def test_offset_normalized_and_siblings_preserved(self) -> None:
        when = datetime(2026, 3, 1, 12, 0, tzinfo=UTC)
        spec = [
            {"cron": "0 2 * * *", "cron_offset": CronOffset.BRASILIA},
            {"interval": timedelta(seconds=30)},
            {"time": when},
        ]
        assert normalize_schedule(spec) == [
            {"cron": "0 2 * * *", "cron_offset": timedelta(hours=-3)},
            {"interval": timedelta(seconds=30)},
            {"time": when},
        ]

    def test_caller_entries_are_not_mutated(self) -> None:
        spec = [{"cron": "0 2 * * *", "cron_offset": "-03:00"}]
        normalize_schedule(spec)
        assert spec == [{"cron": "0 2 * * *", "cron_offset": "-03:00"}]
