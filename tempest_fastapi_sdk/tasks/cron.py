"""Human-friendly cron helpers - schedule periodic tasks without cron syntax.

Cron expressions like ``"0 9 * * MON-FRI"`` are terse and error-prone.
This module lets you schedule the common cases by name instead:

* :class:`Cron` - ready-made expressions (``Cron.EVERY_WEEKDAY_9AM``).
* :class:`CronOffset` - timezone offsets by place (``CronOffset.BRASILIA``)
  so you never hand-write ``"-03:00"``.
* :class:`Weekday` - day-of-week tokens (``Weekday.MON``).
* Builder functions - :func:`daily`, :func:`weekdays`, :func:`hourly`,
  :func:`every_n_minutes`, … - for parameterized schedules.

Every builder returns a plain cron string, so it drops straight into
``@tq.cron(...)`` / ``AsyncTaskScheduler.cron(...)``::

    from tempest_fastapi_sdk.tasks import Cron, CronOffset, daily

    @tq.cron(Cron.EVERY_WEEKDAY_9AM, cron_offset=CronOffset.BRASILIA)
    async def daily_digest() -> None: ...

    @tq.cron(daily(hour=9), cron_offset=CronOffset.BRASILIA)   # same thing
    async def other_digest() -> None: ...

The module has **no** third-party dependency, so it imports without the
``[tasks]`` extra.
"""

from __future__ import annotations

from tempest_fastapi_sdk.core import BaseStrEnum


class Weekday(BaseStrEnum):
    """Day-of-week tokens for the cron day-of-week field.

    Members carry the three-letter cron token (``"MON"`` … ``"SUN"``) so
    they read clearly in a schedule and drop straight into the builder
    functions.
    """

    MON = "MON"
    TUE = "TUE"
    WED = "WED"
    THU = "THU"
    FRI = "FRI"
    SAT = "SAT"
    SUN = "SUN"


class CronOffset(BaseStrEnum):
    """Timezone offsets for ``cron_offset``, by place instead of digits.

    A cron expression is anchored to UTC unless you pass an offset. Pick
    the member for your region instead of hand-writing ``"-03:00"``:

    * ``UTC`` - ``+00:00``.
    * ``BRASILIA`` - ``-03:00`` (BRT; most of Brazil, incl. SP/RJ/DF).
    * ``FERNANDO_DE_NORONHA`` - ``-02:00``.
    * ``MANAUS`` - ``-04:00`` (Amazonas / Mato Grosso).
    * ``ACRE`` - ``-05:00`` (Acre / western Amazonas).
    """

    UTC = "+00:00"
    BRASILIA = "-03:00"
    FERNANDO_DE_NORONHA = "-02:00"
    MANAUS = "-04:00"
    ACRE = "-05:00"


class Cron(BaseStrEnum):
    """Ready-made cron expressions for the most common schedules.

    Pass a member straight to ``@tq.cron(...)`` - it *is* a cron string,
    so no conversion is needed. For anything parameterized (a specific
    hour, weekday, day of month) use the builder functions instead.
    """

    EVERY_MINUTE = "* * * * *"
    EVERY_5_MINUTES = "*/5 * * * *"
    EVERY_10_MINUTES = "*/10 * * * *"
    EVERY_15_MINUTES = "*/15 * * * *"
    EVERY_30_MINUTES = "*/30 * * * *"
    HOURLY = "0 * * * *"
    DAILY_MIDNIGHT = "0 0 * * *"
    DAILY_NOON = "0 12 * * *"
    DAILY_9AM = "0 9 * * *"
    EVERY_WEEKDAY_9AM = "0 9 * * MON-FRI"
    EVERY_MONDAY = "0 0 * * MON"
    FIRST_OF_MONTH = "0 0 1 * *"


def _check(value: int, low: int, high: int, name: str) -> int:
    """Return ``value`` if within ``[low, high]``, else raise.

    Args:
        value (int): The value to validate.
        low (int): Inclusive lower bound.
        high (int): Inclusive upper bound.
        name (str): Field name, used in the error message.

    Returns:
        int: The validated value.

    Raises:
        ValueError: When ``value`` is out of range.
    """
    if not low <= value <= high:
        raise ValueError(f"{name} must be between {low} and {high}, got {value}")
    return value


def _dow(day: Weekday | str) -> str:
    """Return the cron day-of-week token for ``day``.

    Args:
        day (Weekday | str): A :class:`Weekday` member or a raw token
            (``"MON"``, ``"MON-FRI"``, ``"1"`` …).

    Returns:
        str: The day-of-week token as a plain string.
    """
    return day.value if isinstance(day, Weekday) else str(day)


def every_minute() -> str:
    """Return a cron expression firing every minute (``"* * * * *"``).

    Returns:
        str: The cron expression.
    """
    return "* * * * *"


def every_n_minutes(n: int) -> str:
    """Return a cron expression firing every ``n`` minutes.

    Args:
        n (int): The minute step, ``1``-``59``.

    Returns:
        str: e.g. ``"*/5 * * * *"`` for ``n=5``.

    Raises:
        ValueError: When ``n`` is outside ``1``-``59``.
    """
    return f"*/{_check(n, 1, 59, 'n')} * * * *"


def hourly(minute: int = 0) -> str:
    """Return a cron expression firing once an hour at ``minute``.

    Args:
        minute (int): Minute past the hour, ``0``-``59``.

    Returns:
        str: e.g. ``"30 * * * *"`` for ``minute=30``.

    Raises:
        ValueError: When ``minute`` is out of range.
    """
    return f"{_check(minute, 0, 59, 'minute')} * * * *"


def daily(hour: int = 0, minute: int = 0) -> str:
    """Return a cron expression firing once a day at ``hour:minute``.

    Args:
        hour (int): Hour of day, ``0``-``23``.
        minute (int): Minute past the hour, ``0``-``59``.

    Returns:
        str: e.g. ``"0 9 * * *"`` for ``hour=9``.

    Raises:
        ValueError: When ``hour`` / ``minute`` is out of range.
    """
    return f"{_check(minute, 0, 59, 'minute')} {_check(hour, 0, 23, 'hour')} * * *"


def weekly(day: Weekday | str, hour: int = 0, minute: int = 0) -> str:
    """Return a cron expression firing once a week on ``day`` at ``hour:minute``.

    Args:
        day (Weekday | str): The weekday (``Weekday.MON`` or a raw token).
        hour (int): Hour of day, ``0``-``23``.
        minute (int): Minute past the hour, ``0``-``59``.

    Returns:
        str: e.g. ``"0 9 * * MON"``.

    Raises:
        ValueError: When ``hour`` / ``minute`` is out of range.
    """
    m = _check(minute, 0, 59, "minute")
    h = _check(hour, 0, 23, "hour")
    return f"{m} {h} * * {_dow(day)}"


def weekdays(hour: int = 0, minute: int = 0) -> str:
    """Return a cron expression firing Mon-Fri at ``hour:minute``.

    Args:
        hour (int): Hour of day, ``0``-``23``.
        minute (int): Minute past the hour, ``0``-``59``.

    Returns:
        str: e.g. ``"0 9 * * MON-FRI"``.

    Raises:
        ValueError: When ``hour`` / ``minute`` is out of range.
    """
    m = _check(minute, 0, 59, "minute")
    h = _check(hour, 0, 23, "hour")
    return f"{m} {h} * * MON-FRI"


def weekends(hour: int = 0, minute: int = 0) -> str:
    """Return a cron expression firing Sat-Sun at ``hour:minute``.

    Args:
        hour (int): Hour of day, ``0``-``23``.
        minute (int): Minute past the hour, ``0``-``59``.

    Returns:
        str: e.g. ``"0 9 * * SAT,SUN"``.

    Raises:
        ValueError: When ``hour`` / ``minute`` is out of range.
    """
    m = _check(minute, 0, 59, "minute")
    h = _check(hour, 0, 23, "hour")
    return f"{m} {h} * * SAT,SUN"


def monthly(day: int = 1, hour: int = 0, minute: int = 0) -> str:
    """Return a cron expression firing once a month on ``day`` at ``hour:minute``.

    Args:
        day (int): Day of month, ``1``-``31``.
        hour (int): Hour of day, ``0``-``23``.
        minute (int): Minute past the hour, ``0``-``59``.

    Returns:
        str: e.g. ``"0 0 1 * *"`` for the first of the month at midnight.

    Raises:
        ValueError: When ``day`` / ``hour`` / ``minute`` is out of range.
    """
    m = _check(minute, 0, 59, "minute")
    h = _check(hour, 0, 23, "hour")
    d = _check(day, 1, 31, "day")
    return f"{m} {h} {d} * *"


__all__: list[str] = [
    "Cron",
    "CronOffset",
    "Weekday",
    "daily",
    "every_minute",
    "every_n_minutes",
    "hourly",
    "monthly",
    "weekdays",
    "weekends",
    "weekly",
]
