"""The steering every fake shares.

A fake that only answers the happy path sends its user back to hand-written
mocks the moment they want to test the failure branch — which is the branch
worth testing, because it is the one production takes at 3 a.m. So every
fake here can be told to fail, and records what it was asked to do.
"""

from __future__ import annotations

from collections import deque


class _Steerable:
    """Queued failures plus a call log, shared by every fake.

    Not exported: nothing instantiates this on its own. Each fake inherits
    it and documents the two methods below as part of its own surface, so a
    reader of ``FakePixProvider`` never has to find this file.

    Attributes:
        calls (list[str]): Names of the methods that ran, in order.
    """

    def __init__(self) -> None:
        """Start with nothing queued and nothing recorded."""
        self._failures: deque[BaseException] = deque()
        self.calls: list[str] = []

    def fail_next(self, error: BaseException) -> None:
        """Make the next call raise ``error`` instead of answering.

        Args:
            error (BaseException): The exception to raise. Queue several to
                fail several calls in a row, in the order queued.

        Call it with the exception the real provider raises — say
        ``PushDeviceGoneError`` — so the branch under test is the branch
        production takes.
        """
        self._failures.append(error)

    def _record(self, method: str) -> None:
        """Log a call and raise the queued failure, if there is one.

        Args:
            method (str): The name of the method that is running.

        Raises:
            BaseException: Whatever was queued by :meth:`fail_next`.
        """
        self.calls.append(method)
        if self._failures:
            raise self._failures.popleft()
