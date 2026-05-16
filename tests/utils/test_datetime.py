"""Tests for tempest_fastapi_sdk.utils.datetime."""

from datetime import UTC, datetime, timezone

from tempest_fastapi_sdk.utils import to_utc, utcnow


class TestToUtc:
    def test_naive_datetime_tags_as_utc(self) -> None:
        naive = datetime(2024, 1, 1, 12, 0, 0)
        result = to_utc(naive)
        assert result.tzinfo == UTC
        assert result.hour == 12

    def test_aware_datetime_converts_to_utc(self) -> None:
        aware = datetime(2024, 1, 1, 12, 0, 0, tzinfo=UTC)
        result = to_utc(aware)
        assert result == aware

    def test_other_timezone_shifts_correctly(self) -> None:
        from datetime import timedelta

        offset = timezone(timedelta(hours=-3))
        aware = datetime(2024, 1, 1, 12, 0, 0, tzinfo=offset)
        result = to_utc(aware)
        assert result.tzinfo == UTC
        assert result.hour == 15


class TestUtcNow:
    def test_returns_timezone_aware_utc(self) -> None:
        result = utcnow()
        assert result.tzinfo == UTC
