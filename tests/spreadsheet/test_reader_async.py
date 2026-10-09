"""Tests for the async ``.xlsx`` readers (#432).

The guard that every async reader parses off the loop, with a probe in
place of the parser, is ``tests/test_spreadsheet_event_loop_guard.py``.
This module checks the rest: the async variants return what the sync ones
return, a real workbook parse leaves the loop ticking, and the semaphore
bounds concurrent reads — per event loop, so a second ``asyncio.run`` does
not trip over a semaphore bound to the first.
"""

from __future__ import annotations

import asyncio
import threading
import time
from datetime import datetime
from decimal import Decimal
from typing import Any, Final

import pytest
from pydantic import BaseModel

from tempest_fastapi_sdk.spreadsheet import (
    DEFAULT_MAX_CONCURRENT_XLSX_READS,
    SheetNotFoundError,
    new_workbook,
    read_xlsx,
    read_xlsx_as,
    read_xlsx_as_async,
    read_xlsx_async,
    read_xlsx_sheets,
    read_xlsx_sheets_async,
    reader,
    workbook_to_bytes,
)

pytest.importorskip("openpyxl")

TICK: Final[float] = 0.01
"""Seconds between ticks of the loop ticker."""

LARGE_ROWS: Final[int] = 5_000
"""Rows of the generated tab: measured at ~0.25 s to read here."""


class Row(BaseModel):
    """A row of :func:`_workbook`."""

    id: int
    nome: str
    valor: Decimal
    data: datetime


def _workbook(rows: int) -> bytes:
    """Build a two-tab workbook with ``rows`` data rows in the first tab.

    Args:
        rows (int): Data rows of the ``Dados`` tab.

    Returns:
        bytes: The ``.xlsx`` file.
    """
    workbook = new_workbook("Dados", "Notas")
    sheet = workbook["Dados"]
    sheet.append(["id", "nome", "valor", "data"])
    for index in range(rows):
        sheet.append([index, f"nome {index}", index * 1.5, datetime(2026, 1, 1)])
    workbook["Notas"].append(["nota"])
    workbook["Notas"].append(["conferir"])
    return workbook_to_bytes(workbook)


class TestSameResultAsSync:
    """The async variants are the sync readers, moved to a thread."""

    async def test_read_xlsx_async(self) -> None:
        """Same rows as ``read_xlsx``."""
        data = _workbook(3)
        assert await read_xlsx_async(data, sheet="Notas") == read_xlsx(
            data, sheet="Notas"
        )

    async def test_read_xlsx_sheets_async(self) -> None:
        """Same tabs as ``read_xlsx_sheets``."""
        data = _workbook(3)
        assert await read_xlsx_sheets_async(data) == read_xlsx_sheets(data)

    async def test_read_xlsx_as_async(self) -> None:
        """Same models as ``read_xlsx_as``."""
        data = _workbook(3)
        assert await read_xlsx_as_async(data, Row) == read_xlsx_as(data, Row)

    async def test_errors_cross_the_thread(self) -> None:
        """An error raised in the worker reaches the caller unchanged."""
        with pytest.raises(SheetNotFoundError):
            await read_xlsx_async(_workbook(1), sheet="Inexistente")

    async def test_limits_are_checked(self) -> None:
        """A non-positive limit raises ``ValueError``, as in the sync reader."""
        with pytest.raises(ValueError, match="max_rows"):
            await read_xlsx_sheets_async(_workbook(1), max_rows=0)


async def _ticks_during_read(read: Any) -> int:
    """Count loop ticks that happen while ``read`` is in progress.

    The count is taken right before and right after the read, on the loop
    thread, so a read that holds the loop leaves it at exactly zero.

    Args:
        read (Any): Builds the awaitable that reads the workbook.

    Returns:
        int: Ticks the loop ran during the read.
    """
    ticks: list[int] = [0]
    stop: asyncio.Event = asyncio.Event()

    async def ticker() -> None:
        """Count until stopped."""
        while not stop.is_set():
            await asyncio.sleep(TICK)
            ticks[0] += 1

    task: asyncio.Task[None] = asyncio.create_task(ticker())
    await asyncio.sleep(0)
    before: int = ticks[0]
    await read()
    during: int = ticks[0] - before
    stop.set()
    await task
    return during


class TestRealWorkbook:
    """A real parse, not a probe: the loop ticks while the workbook is read."""

    async def test_async_read_leaves_the_loop_ticking(self) -> None:
        """Measured ~23 ticks of 10 ms during a ~0.25 s read; the floor is 5."""
        data = _workbook(LARGE_ROWS)
        assert await _ticks_during_read(lambda: read_xlsx_async(data)) >= 5

    async def test_sync_read_in_a_coroutine_holds_the_loop(self) -> None:
        """The defect of #432, reproduced: zero ticks during the read."""
        data = _workbook(LARGE_ROWS)

        async def blocking() -> list[dict[str, Any]]:
            """Call the sync reader inline, as the old recipe did.

            Returns:
                list[dict[str, Any]]: The rows.
            """
            return read_xlsx(data)

        assert await _ticks_during_read(blocking) == 0


class _Concurrency:
    """Stand-in reader that records how many calls overlap.

    Attributes:
        active (int): Calls in progress.
        peak (int): Most calls seen in progress at once.
    """

    def __init__(self) -> None:
        """Initialize the counters."""
        self.active: int = 0
        self.peak: int = 0
        self._lock: threading.Lock = threading.Lock()

    def __call__(self, *args: Any, **kwargs: Any) -> list[dict[str, Any]]:
        """Hold the call open for 50 ms while counting overlaps.

        Args:
            *args (Any): Ignored.
            **kwargs (Any): Ignored.

        Returns:
            list[dict[str, Any]]: An empty tab.
        """
        with self._lock:
            self.active += 1
            self.peak = max(self.peak, self.active)
        time.sleep(0.05)
        with self._lock:
            self.active -= 1
        return []


class TestSemaphore:
    """Concurrent reads are bounded, per event loop."""

    async def test_default_bound(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """Without a semaphore, at most the default number run at once."""
        fake = _Concurrency()
        monkeypatch.setattr(reader, "read_xlsx", fake)
        await asyncio.gather(
            *(
                read_xlsx_async(b"")
                for _ in range(DEFAULT_MAX_CONCURRENT_XLSX_READS + 3)
            )
        )
        assert fake.peak == DEFAULT_MAX_CONCURRENT_XLSX_READS

    async def test_caller_semaphore(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """A caller's semaphore replaces the default, across all three readers."""
        fake = _Concurrency()
        monkeypatch.setattr(reader, "read_xlsx", fake)
        monkeypatch.setattr(reader, "read_xlsx_sheets", fake)
        monkeypatch.setattr(reader, "read_xlsx_as", fake)
        one: asyncio.Semaphore = asyncio.Semaphore(1)
        await asyncio.gather(
            read_xlsx_async(b"", semaphore=one),
            read_xlsx_sheets_async(b"", semaphore=one),
            read_xlsx_as_async(b"", Row, semaphore=one),
        )
        assert fake.peak == 1

    def test_default_semaphore_survives_a_new_loop(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Two ``asyncio.run`` calls with contention: no loop-binding error.

        A module-level ``asyncio.Semaphore`` contended in one loop raises
        ``RuntimeError: ... is bound to a different event loop`` when
        contended in the next; the default semaphore is per loop.
        """
        fake = _Concurrency()
        monkeypatch.setattr(reader, "read_xlsx", fake)

        async def contend() -> None:
            """Run more reads than the default bound at once."""
            await asyncio.gather(
                *(
                    read_xlsx_async(b"")
                    for _ in range(DEFAULT_MAX_CONCURRENT_XLSX_READS + 2)
                )
            )

        asyncio.run(contend())
        asyncio.run(contend())
        assert fake.peak == DEFAULT_MAX_CONCURRENT_XLSX_READS
