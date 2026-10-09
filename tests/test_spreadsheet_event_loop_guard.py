"""Guard: no async spreadsheet reader parses on the event loop (#432).

Every coroutine of :mod:`tempest_fastapi_sdk.spreadsheet` that parses or
validates a sheet hands the work to a worker thread. Before #432 none of
them did: ``read_google_sheet_xlsx`` called ``read_xlsx_sheets`` inline and
``_read_csv_rows`` called ``_parse_csv`` inline, so a 99 999-row workbook
held the loop for 4.35 s with zero ticks of a 100 ms ticker.

The check is deterministic, not a stopwatch. The synchronous function each
coroutine delegates to is replaced by a :class:`_LoopProbe`, which blocks
its caller until a task **running on the event loop** releases it. If the
probe runs in a worker thread, the loop is free, the releaser task runs and
the probe returns at once. If the probe runs on the loop thread, the
releaser cannot run until the probe gives up after
:data:`RELEASE_TIMEOUT` seconds — and the probe records that it was never
released.

The guard proves it fires: :class:`TestGuardFires` runs the same harness
with :func:`asyncio.to_thread` swapped for a coroutine that calls the
function inline — the shape of removing the ``to_thread`` — and asserts
every case is caught, plus the exact pre-#432 shape of the ``.xlsx`` path.
"""

from __future__ import annotations

import asyncio
import threading
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import Any, Final

import httpx
import pytest
from pydantic import BaseModel

from tempest_fastapi_sdk.spreadsheet import (
    XLSX_MEDIA_TYPE,
    google,
    read_google_sheet,
    read_google_sheet_as,
    read_google_sheet_xlsx,
    read_xlsx_as_async,
    read_xlsx_async,
    read_xlsx_sheets_async,
    reader,
)

pytest.importorskip("openpyxl")

RELEASE_TIMEOUT: Final[float] = 1.0
"""Seconds a probe on the loop thread waits before giving up.

Only the failing direction pays it: a probe in a worker thread is released
within one poll of the releaser task (1 ms).
"""

SHARE_LINK: Final[str] = (
    "https://docs.google.com/spreadsheets/d/"
    "1h0ATstw2f6ryXvbwV-DW6zwsBRIF-2k5zHcm2uTEge8/edit?usp=sharing"
)
CSV_BODY: Final[bytes] = b"item,valor\r\nBota,500\r\nLuva,250"


class Item(BaseModel):
    """A row of :data:`CSV_BODY`."""

    item: str
    valor: int


class _LoopProbe:
    """Stand-in for a synchronous parser that only a loop task can release.

    Attributes:
        result (object): What the probe returns to its caller.
        started (threading.Event): Set when the probe is entered.
        released (threading.Event): Set by the releaser task on the loop.
        released_in_time (bool | None): Whether the release arrived while
            the probe was waiting; ``None`` until the probe is called.
    """

    def __init__(self, result: object) -> None:
        """Initialize the probe.

        Args:
            result (object): What the probe returns to its caller.
        """
        self.result: object = result
        self.started: threading.Event = threading.Event()
        self.released: threading.Event = threading.Event()
        self.released_in_time: bool | None = None

    def __call__(self, *args: Any, **kwargs: Any) -> object:
        """Block until released by the loop, or until the timeout.

        Args:
            *args (Any): Ignored positional arguments of the replaced function.
            **kwargs (Any): Ignored keyword arguments of the replaced function.

        Returns:
            object: :attr:`result`.
        """
        self.started.set()
        self.released_in_time = self.released.wait(RELEASE_TIMEOUT)
        return self.result


async def _loop_free_during(
    probe: _LoopProbe, call: Callable[[], Awaitable[object]]
) -> bool | None:
    """Run ``call`` next to a releaser task and report what the probe saw.

    Args:
        probe (_LoopProbe): The probe patched in for the parser.
        call (Callable[[], Awaitable[object]]): Builds the coroutine under
            test.

    Returns:
        bool | None: ``True`` when the loop ran while the probe waited,
        ``False`` when it did not, ``None`` when the probe was never called.
    """

    async def release() -> None:
        """Release the probe as soon as it is entered."""
        while not probe.started.is_set():
            await asyncio.sleep(0.001)
        probe.released.set()

    releaser: asyncio.Task[None] = asyncio.create_task(release())
    await call()
    if not probe.started.is_set():
        releaser.cancel()
    await asyncio.gather(releaser, return_exceptions=True)
    return probe.released_in_time


def _transport(media_type: str, body: bytes) -> httpx.MockTransport:
    """Answer every request with ``body`` under ``media_type``.

    Args:
        media_type (str): The ``content-type`` of the answer.
        body (bytes): The answer body.

    Returns:
        httpx.MockTransport: The fake transport.
    """

    def handler(request: httpx.Request) -> httpx.Response:
        """Return the fixed answer.

        Args:
            request (httpx.Request): The incoming request (unused).

        Returns:
            httpx.Response: ``200`` with ``body``.
        """
        return httpx.Response(200, headers={"content-type": media_type}, content=body)

    return httpx.MockTransport(handler)


async def _google_csv() -> object:
    """Call ``read_google_sheet`` against a fake CSV export.

    Returns:
        object: The rows.
    """
    transport = _transport("text/csv; charset=utf-8", CSV_BODY)
    async with httpx.AsyncClient(transport=transport) as client:
        return await read_google_sheet(SHARE_LINK, client=client)


async def _google_csv_as() -> object:
    """Call ``read_google_sheet_as`` against a fake CSV export.

    Returns:
        object: The validated rows.
    """
    transport = _transport("text/csv; charset=utf-8", CSV_BODY)
    async with httpx.AsyncClient(transport=transport) as client:
        return await read_google_sheet_as(SHARE_LINK, Item, client=client)


async def _google_xlsx() -> object:
    """Call ``read_google_sheet_xlsx`` against a fake ``.xlsx`` export.

    Returns:
        object: The tabs.
    """
    transport = _transport(XLSX_MEDIA_TYPE, b"PK-not-parsed")
    async with httpx.AsyncClient(transport=transport) as client:
        return await read_google_sheet_xlsx(SHARE_LINK, client=client)


@dataclass(frozen=True)
class Case:
    """One coroutine and the synchronous functions it must not run inline.

    Attributes:
        name (str): The test id.
        targets (tuple[tuple[object, str], ...]): ``(module, attribute)``
            pairs replaced by the probe. Every module that could hold a
            reference to the function is listed, so moving the call to a
            directly imported name does not slip past the probe.
        result (object): What the probe returns.
        call (Callable[[], Awaitable[object]]): Builds the coroutine.
    """

    name: str
    targets: tuple[tuple[object, str], ...]
    result: object
    call: Callable[[], Awaitable[object]]


CASES: Final[tuple[Case, ...]] = (
    Case(
        "read_xlsx_async",
        ((reader, "read_xlsx"),),
        [],
        lambda: read_xlsx_async(b""),
    ),
    Case(
        "read_xlsx_sheets_async",
        ((reader, "read_xlsx_sheets"),),
        {},
        lambda: read_xlsx_sheets_async(b""),
    ),
    Case(
        "read_xlsx_as_async",
        ((reader, "read_xlsx_as"),),
        [],
        lambda: read_xlsx_as_async(b"", Item),
    ),
    Case(
        "read_google_sheet-csv-parse",
        ((google, "_parse_csv"),),
        [],
        _google_csv,
    ),
    Case(
        "read_google_sheet_as-validation",
        ((google, "_validate_rows"),),
        [],
        _google_csv_as,
    ),
    Case(
        "read_google_sheet_xlsx-parse",
        ((reader, "read_xlsx_sheets"), (google, "read_xlsx_sheets")),
        {},
        _google_xlsx,
    ),
)


def _patch(monkeypatch: pytest.MonkeyPatch, case: Case) -> _LoopProbe:
    """Replace every target of ``case`` with one probe.

    Args:
        monkeypatch (pytest.MonkeyPatch): The fixture.
        case (Case): The case under test.

    Returns:
        _LoopProbe: The probe now standing in for the parser.
    """
    probe = _LoopProbe(case.result)
    for module, attribute in case.targets:
        monkeypatch.setattr(module, attribute, probe, raising=False)
    return probe


async def _inline_to_thread(
    func: Callable[..., object], /, *args: Any, **kwargs: Any
) -> object:
    """Run ``func`` on the loop thread — the shape of a removed ``to_thread``.

    Args:
        func (Callable[..., object]): The function ``to_thread`` was given.
        *args (Any): Its positional arguments.
        **kwargs (Any): Its keyword arguments.

    Returns:
        object: What ``func`` returned.
    """
    return func(*args, **kwargs)


@pytest.mark.parametrize("case", CASES, ids=[case.name for case in CASES])
async def test_parser_runs_off_the_loop(
    monkeypatch: pytest.MonkeyPatch, case: Case
) -> None:
    """The loop keeps running while the parser works."""
    probe = _patch(monkeypatch, case)
    assert await _loop_free_during(probe, case.call) is True


class TestGuardFires:
    """The harness catches a parser that runs on the loop thread."""

    @pytest.mark.parametrize("case", CASES, ids=[case.name for case in CASES])
    async def test_removing_to_thread_is_caught(
        self, monkeypatch: pytest.MonkeyPatch, case: Case
    ) -> None:
        """With ``to_thread`` calling inline, every case reports a blocked loop."""
        probe = _patch(monkeypatch, case)
        monkeypatch.setattr(asyncio, "to_thread", _inline_to_thread)
        assert await _loop_free_during(probe, case.call) is False

    async def test_pre_fix_xlsx_shape_is_caught(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """The pre-#432 body of ``read_google_sheet_xlsx`` blocks the loop."""
        probe = _LoopProbe({})
        monkeypatch.setattr(reader, "read_xlsx_sheets", probe)

        async def pre_fix() -> object:
            """Parse inline, as ``read_google_sheet_xlsx`` did before #432.

            Returns:
                object: The tabs.
            """
            return reader.read_xlsx_sheets(b"")

        assert await _loop_free_during(probe, pre_fix) is False

    async def test_uncalled_probe_is_not_a_pass(self) -> None:
        """A coroutine that never reaches the probe reports ``None``, not ``True``."""
        probe = _LoopProbe([])

        async def skip() -> object:
            """Return without calling the probe.

            Returns:
                object: An empty list.
            """
            return []

        assert await _loop_free_during(probe, skip) is None
