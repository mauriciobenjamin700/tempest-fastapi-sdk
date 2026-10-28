"""The thread guard of ``tests/conftest.py`` fires on the shape that shipped (#650).

``tests/auth/test_auth_pages.py`` built its engine in a helper and never
disposed it; 56 of its tests left an ``aiosqlite`` worker thread running.
These cases rebuild that shape and assert :func:`tests._thread_guard.leaked_since`
reports it, then clean up so the autouse guard itself stays green.
"""

from __future__ import annotations

from sqlalchemy import text
from sqlalchemy.ext.asyncio import create_async_engine

from tests._thread_guard import aiosqlite_threads, leaked_since


async def test_an_engine_left_open_is_reported() -> None:
    before = aiosqlite_threads()
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.connect() as connection:
        await connection.execute(text("SELECT 1"))

    leaked = leaked_since(before, grace=0.2)

    await engine.dispose()
    assert len(leaked) == 1
    assert leaked_since(before) == []


async def test_a_disposed_engine_is_not_reported() -> None:
    before = aiosqlite_threads()
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.connect() as connection:
        await connection.execute(text("SELECT 1"))
    await engine.dispose()

    assert leaked_since(before) == []
