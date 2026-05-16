"""Datetime helpers used by the SDK base schemas."""

from datetime import UTC, datetime


def to_utc(value: datetime) -> datetime:
    """Convert a datetime to UTC.

    Naive datetimes are assumed to be in UTC and tagged with the
    UTC tzinfo. Aware datetimes are converted to UTC.

    Args:
        value (datetime): The datetime to normalize.

    Returns:
        datetime: A timezone-aware datetime in UTC.
    """
    if value.tzinfo is None:
        return value.replace(tzinfo=UTC)
    return value.astimezone(UTC)


def utcnow() -> datetime:
    """Return the current time as a timezone-aware UTC datetime.

    Returns:
        datetime: The current UTC time.
    """
    return datetime.now(UTC)


__all__: list[str] = [
    "to_utc",
    "utcnow",
]
