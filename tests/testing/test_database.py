"""Tests for tempest_fastapi_sdk.testing.database helpers."""

from sqlalchemy import String, select
from sqlalchemy.ext.asyncio import async_sessionmaker
from sqlalchemy.orm import Mapped, mapped_column

from tempest_fastapi_sdk import BaseModel
from tempest_fastapi_sdk.testing import (
    create_test_engine,
    drop_test_metadata,
    init_test_metadata,
    test_database,
    test_session,
)


class Tinker(BaseModel):
    __tablename__ = "tinker_for_testing_helpers"
    label: Mapped[str] = mapped_column(String(32), nullable=False)


async def test_create_test_engine_yields_in_memory_sqlite() -> None:
    engine = create_test_engine()
    try:
        assert "sqlite" in str(engine.url)
    finally:
        await engine.dispose()


async def test_init_and_drop_metadata() -> None:
    engine = create_test_engine()
    try:
        await init_test_metadata(engine)
        await drop_test_metadata(engine)
    finally:
        await engine.dispose()


async def test_session_yields_working_session() -> None:
    async with test_session() as session:
        session.add(Tinker(label="hello"))
        await session.commit()
        loaded = (await session.execute(select(Tinker))).scalars().all()
        assert [t.label for t in loaded] == ["hello"]


async def test_database_yields_session_factory() -> None:
    async with test_database() as factory:
        assert isinstance(factory, async_sessionmaker)
        async with factory() as session:
            session.add(Tinker(label="one"))
            await session.commit()
        async with factory() as session:
            session.add(Tinker(label="two"))
            await session.commit()
            loaded = (await session.execute(select(Tinker))).scalars().all()
            assert {t.label for t in loaded} == {"one", "two"}
