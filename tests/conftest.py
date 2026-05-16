"""Shared pytest fixtures."""

from collections.abc import AsyncGenerator

import pytest_asyncio
from sqlalchemy.ext.asyncio import AsyncSession

from tempest_fastapi_sdk.db.connection import AsyncDatabaseManager


@pytest_asyncio.fixture
async def db() -> AsyncGenerator[AsyncDatabaseManager]:
    """Yield a fresh in-memory SQLite database for each test."""
    manager = AsyncDatabaseManager("sqlite+aiosqlite:///:memory:")
    await manager.connect()
    await manager.create_tables()
    try:
        yield manager
    finally:
        await manager.drop_tables()
        await manager.disconnect()


@pytest_asyncio.fixture
async def session(
    db: AsyncDatabaseManager,
) -> AsyncGenerator[AsyncSession]:
    """Yield a managed AsyncSession bound to the in-memory database."""
    async with db.get_session_context() as session:
        yield session
