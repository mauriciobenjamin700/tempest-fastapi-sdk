"""Tests for tempest_fastapi_sdk.schemas.response.BaseResponseSchema."""

from datetime import UTC, datetime, timedelta, timezone
from uuid import uuid4

from tempest_fastapi_sdk.schemas import BaseResponseSchema


def _build(**overrides: object) -> BaseResponseSchema:
    payload: dict[str, object] = {
        "id": uuid4(),
        "is_active": True,
        "created_at": datetime(2024, 1, 1, 12, 0, 0, tzinfo=UTC),
        "updated_at": datetime(2024, 1, 2, 12, 0, 0, tzinfo=UTC),
    }
    payload.update(overrides)
    return BaseResponseSchema(**payload)  # type: ignore[arg-type]


class TestBaseResponseSchema:
    def test_holds_four_columns(self) -> None:
        result = _build()
        assert result.is_active is True
        assert result.created_at.tzinfo == UTC

    def test_naive_timestamp_normalized_to_utc(self) -> None:
        result = _build(created_at=datetime(2024, 1, 1, 12, 0, 0))
        assert result.created_at.tzinfo == UTC
        assert result.created_at.hour == 12

    def test_other_tz_converts_to_utc(self) -> None:
        offset = timezone(timedelta(hours=-3))
        result = _build(created_at=datetime(2024, 1, 1, 12, 0, 0, tzinfo=offset))
        assert result.created_at.tzinfo == UTC
        assert result.created_at.hour == 15
