"""A ``TIMESTAMP`` column that always reads back timezone-aware."""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import TIMESTAMP
from sqlalchemy.engine.interfaces import Dialect
from sqlalchemy.types import TypeDecorator

from tempest_fastapi_sdk.utils.datetime import to_utc


class UtcDateTime(TypeDecorator[datetime]):
    """``TIMESTAMP(timezone=True)`` that hands back UTC-aware values.

    The DDL is unchanged — this emits exactly the same column type — but
    the value that comes back from a ``SELECT`` is normalized, because
    the engines disagree and the disagreement is invisible until it
    isn't.

    Measured on this suite, same row and same process: right after
    ``session.commit()`` the Python-side default makes ``created_at``
    aware, and after a ``SELECT`` on SQLite the very same column reads
    back **naive**::

        after commit (python default): datetime(..., tzinfo=timezone.utc)
        after SELECT (from sqlite)   : datetime(...)
        datetime.now(UTC) - row.created_at
        TypeError: can't subtract offset-naive and offset-aware datetimes

    PostgreSQL returns it aware. So code written against one engine
    raises ``TypeError`` on the other — in the suite or in production,
    depending on which side you wrote it against, and never in both.
    Every service was expected to remember ``to_utc`` at each read
    boundary; forgetting it is silent until an arithmetic or a
    comparison runs.

    Binding normalizes too, so a naive value handed in is stored as UTC
    rather than as whatever the driver's session zone makes of it. Naive
    input is read as UTC, which is the contract
    :func:`~tempest_fastapi_sdk.to_utc` already documents.

    Attributes:
        impl (TIMESTAMP): The wrapped type, declared as an **instance**
            with ``timezone=True`` rather than as the bare class. A
            decorator that writes ``impl = TIMESTAMP`` and adds the flag
            in ``load_dialect_impl`` emits the same DDL, but then
            ``impl_instance.timezone`` reads ``False`` — so anything
            asking the column whether it is aware (the cursor coercion,
            an admin widget) gets the wrong answer from a type that is
            in fact always aware.
        cache_ok (bool): Safe to cache in SQLAlchemy's compiled-statement
            cache — the type carries no per-instance state.
    """

    impl = TIMESTAMP(timezone=True)
    cache_ok = True

    @property
    def python_type(self) -> type[datetime]:
        """Return the Python type values of this column carry.

        ``TypeDecorator`` does not forward this to the type it wraps —
        it raises ``NotImplementedError`` — and callers that ask before
        deciding how to treat a column then degrade silently: the admin
        form falls back to a text input for what is a datetime, and the
        cursor coercion leaves an ISO string unparsed.

        Returns:
            type[datetime]: Always :class:`datetime.datetime`.
        """
        return datetime

    def process_bind_param(
        self,
        value: datetime | None,
        dialect: Dialect,
    ) -> datetime | None:
        """Normalize a value on its way into the database.

        Args:
            value (datetime | None): The value being bound.
            dialect (Dialect): The dialect in use.

        Returns:
            datetime | None: The value in UTC, or ``None``.
        """
        if value is None:
            return None
        return to_utc(value)

    def process_result_value(
        self,
        value: datetime | None,
        dialect: Dialect,
    ) -> datetime | None:
        """Normalize a value on its way out of the database.

        Args:
            value (datetime | None): The value the driver returned.
            dialect (Dialect): The dialect in use.

        Returns:
            datetime | None: An aware value in UTC, or ``None``.
        """
        if value is None:
            return None
        return to_utc(value)


__all__: list[str] = [
    "UtcDateTime",
]
