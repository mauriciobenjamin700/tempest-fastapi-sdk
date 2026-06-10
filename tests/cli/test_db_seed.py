"""Tests for ``tempest db seed``."""

from __future__ import annotations

import asyncio
import sys
import types
from collections.abc import Iterator
from pathlib import Path

import pytest
from sqlalchemy import String, select
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine
from sqlalchemy.orm import Mapped, mapped_column
from typer.testing import CliRunner

from tempest_fastapi_sdk import BaseModel
from tempest_fastapi_sdk.cli.main import app

runner = CliRunner()


class _SeedThingModel(BaseModel):
    """Row inserted by the seed callable under test."""

    __tablename__ = "seed_thing"

    name: Mapped[str] = mapped_column(String(50), nullable=False)


async def _seed(session: AsyncSession) -> int:
    """Async seed callable: insert two rows, return the count."""
    session.add_all([_SeedThingModel(name="a"), _SeedThingModel(name="b")])
    await session.flush()
    return 2


def _sync_seed(session: AsyncSession) -> None:
    """Sync seed callable: insert one row, return nothing."""
    session.add(_SeedThingModel(name="sync"))


# Expose the callables via an importable dotted module.
_module = types.ModuleType("cli_seed_module")
_module.seed = _seed  # type: ignore[attr-defined]
_module.sync_seed = _sync_seed  # type: ignore[attr-defined]
sys.modules["cli_seed_module"] = _module


@pytest.fixture
def project_db(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Iterator[str]:
    """Fresh on-disk SQLite with the seed table created."""
    monkeypatch.chdir(tmp_path)
    db_path = tmp_path / "app.db"
    url = f"sqlite+aiosqlite:///{db_path}"
    monkeypatch.setenv("DATABASE_URL", url)

    async def _create_schema() -> None:
        engine = create_async_engine(url)
        async with engine.begin() as conn:
            await conn.run_sync(BaseModel.metadata.create_all)
        await engine.dispose()

    asyncio.run(_create_schema())
    yield url


def _count_rows(url: str) -> int:
    async def _run() -> int:
        engine = create_async_engine(url)
        try:
            async with AsyncSession(engine) as session:
                result = await session.execute(select(_SeedThingModel))
                return len(list(result.scalars().all()))
        finally:
            await engine.dispose()

    return asyncio.run(_run())


class TestSeed:
    def test_async_seed_runs_and_commits(self, project_db: str) -> None:
        result = runner.invoke(app, ["db", "seed", "--seed", "cli_seed_module:seed"])
        assert result.exit_code == 0, result.stdout + result.stderr
        assert "(2 rows)" in result.stdout
        assert _count_rows(project_db) == 2

    def test_sync_seed_runs(self, project_db: str) -> None:
        result = runner.invoke(
            app, ["db", "seed", "--seed", "cli_seed_module:sync_seed"]
        )
        assert result.exit_code == 0, result.stdout + result.stderr
        assert _count_rows(project_db) == 1


class TestSeedErrors:
    def test_bad_spec_errors(self, project_db: str) -> None:
        result = runner.invoke(app, ["db", "seed", "--seed", "nocolon"])
        assert result.exit_code == 2

    def test_not_callable_errors(self, project_db: str) -> None:
        result = runner.invoke(app, ["db", "seed", "--seed", "cli_seed_module:__doc__"])
        assert result.exit_code == 2

    def test_missing_module_errors(self, project_db: str) -> None:
        result = runner.invoke(app, ["db", "seed", "--seed", "no.such.module:seed"])
        assert result.exit_code == 2
