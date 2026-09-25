"""Keep the database's own words out of the 5xx log.

A database that refuses a write explains itself, and the explanation
quotes the row. Postgres answers a duplicate with::

    duplicate key value violates unique constraint "users_cpf_key"
    DETAIL:  Key (cpf)=(123.456.789-00) already exists.

``asyncpg`` hands that sentence to SQLAlchemy verbatim, SQLAlchemy puts
it in ``str(IntegrityError)``, and a log record that carries the
exception in ``exc_info`` prints it on the last line of the traceback —
into ``error.log``, into ``500.log``, into the logs router and the
``/admin/logs`` panel. The value is whatever the client sent: a CPF, an
e-mail, a Pix key, a phone number.

``create_async_engine(..., hide_parameters=True)`` does not help, and
that was measured against Postgres 16 rather than assumed: it replaces
the ``[parameters: ...]`` block SQLAlchemy appends, but the ``DETAIL``
line is the **server's** text, relayed by the driver, and stays.

:func:`redact_database_errors` is what the SDK's 5xx handlers apply
before they log. It never changes the exception the application raised,
the response, or what ``on_server_error`` receives — only the object the
log record points at.
"""

from __future__ import annotations

from collections.abc import Callable

from sqlalchemy.exc import DBAPIError

from tempest_fastapi_sdk.db.integrity import (
    describe_database_error,
    dotted_type_name,
)

ExceptionRedactor = Callable[[BaseException], BaseException]
"""Map the exception a handler caught to the one its log record carries.

Receives the original exception and returns what goes into ``exc_info``:
the same object when there is nothing to hide, or a stand-in whose text
is safe to write to disk. Must not raise and must not mutate its
argument — the original is still the one ``on_server_error`` receives.
"""


class RedactedError(Exception):
    """Stand-in for one link of an exception chain, safe to log.

    The traceback module formats an exception from its type, its text,
    its frames and its ``__cause__``/``__context__`` links. A stand-in
    keeps the frames (``__traceback__`` is the original's, so every
    ``File ..., line ...`` entry is unchanged) and the chain shape, and
    replaces only the text. The original type is not lost: it opens the
    text, as ``sqlalchemy.exc.IntegrityError: ...``.

    Attributes:
        original_type (str): Dotted name of the type this link stands
            in for.
    """

    def __init__(self, original_type: str, text: str) -> None:
        """Build the stand-in.

        Args:
            original_type (str): Dotted name of the replaced type.
            text (str): What the log line says after the type.
        """
        super().__init__(f"{original_type}: {text}" if text else original_type)
        self.original_type: str = original_type


def _links(error: BaseException) -> list[BaseException]:
    """Return the exceptions directly reachable from ``error``.

    Args:
        error (BaseException): The link to expand.

    Returns:
        list[BaseException]: The cause, the context and, for an
        exception group, its members.
    """
    reachable: list[BaseException] = []
    if error.__cause__ is not None:
        reachable.append(error.__cause__)
    if error.__context__ is not None:
        reachable.append(error.__context__)
    if isinstance(error, BaseExceptionGroup):
        reachable.extend(error.exceptions)
    return reachable


def _holds_database_error(error: BaseException) -> bool:
    """Tell whether a ``DBAPIError`` is anywhere in ``error``'s graph.

    Args:
        error (BaseException): Where to start.

    Returns:
        bool: ``True`` when the chain or a group member holds one.
    """
    seen: set[int] = set()
    pending: list[BaseException] = [error]
    while pending:
        current = pending.pop()
        if id(current) in seen:
            continue
        seen.add(id(current))
        if isinstance(current, DBAPIError):
            return True
        pending.extend(_links(current))
    return False


def _rebuild(error: BaseException, memo: dict[int, BaseException]) -> BaseException:
    """Build the safe-to-log copy of one link and everything it reaches.

    A ``DBAPIError`` becomes a :class:`RedactedError` carrying the
    summary, and its own chain is dropped: what lies below it is the
    driver's exception, whose text is the same server sentence. Any
    other link keeps its text and is rebuilt only so its ``__cause__``
    can point at the redacted copy — the original is never mutated.
    An exception group is rebuilt as a group, member by member.

    Args:
        error (BaseException): The link to copy.
        memo (dict[int, BaseException]): Copies already built, by the
            original's ``id``, which is what keeps a cyclic chain from
            recursing forever.

    Returns:
        BaseException: The copy, with the original's frames.
    """
    known = memo.get(id(error))
    if known is not None:
        return known
    copy: BaseException
    if isinstance(error, DBAPIError):
        copy = RedactedError(dotted_type_name(error), describe_database_error(error))
        memo[id(error)] = copy
        copy.__suppress_context__ = True
        return copy.with_traceback(error.__traceback__)
    if isinstance(error, BaseExceptionGroup):
        members: list[Exception] = []
        for member in error.exceptions:
            rebuilt = _rebuild(member, memo)
            members.append(
                rebuilt
                if isinstance(rebuilt, Exception)
                else RedactedError(dotted_type_name(member), str(member))
            )
        copy = ExceptionGroup(f"{dotted_type_name(error)}: {error.message}", members)
    else:
        copy = RedactedError(dotted_type_name(error), str(error))
    memo[id(error)] = copy
    copy.__suppress_context__ = error.__suppress_context__
    if error.__cause__ is not None:
        copy.__cause__ = _rebuild(error.__cause__, memo)
    if error.__context__ is not None:
        copy.__context__ = _rebuild(error.__context__, memo)
    return copy.with_traceback(error.__traceback__)


def redact_database_errors(error: BaseException) -> BaseException:
    """Return ``error``, or a stand-in when a database error is in it.

    The default ``redact_exception`` of every SDK 5xx handler. Walks the
    ``__cause__``/``__context__`` chain and exception-group members; when
    none is a ``sqlalchemy.exc.DBAPIError`` the original comes back
    untouched, so a non-database 500 logs exactly as before. Otherwise
    the chain is rebuilt from :class:`RedactedError` links that keep
    every frame and replace the database error's text with a summary —
    for an ``IntegrityError``, ``unique violation; constraint=...;
    columns=...`` — ending in :data:`~tempest_fastapi_sdk.WITHHELD_NOTICE`.

    What is lost, on purpose: the driver's exception (asyncpg's
    ``UniqueViolationError`` and SQLAlchemy's adapter around it), whose
    text repeats the server sentence. Its type survives in the summary
    as ``driver=...``.

    Example:

        >>> from sqlalchemy.exc import IntegrityError
        >>> from tempest_fastapi_sdk import redact_database_errors
        >>>
        >>> def logged(error: IntegrityError) -> str:
        ...     return str(redact_database_errors(error))

    Args:
        error (BaseException): The exception a handler caught.

    Returns:
        BaseException: ``error`` itself, or its redacted copy.
    """
    if not _holds_database_error(error):
        return error
    return _rebuild(error, {})


__all__: list[str] = [
    "ExceptionRedactor",
    "RedactedError",
    "redact_database_errors",
]
