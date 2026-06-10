"""Tests for tempest_fastapi_sdk.db.slow_query.SlowQueryLogger."""

import logging

import pytest
from sqlalchemy import text

from tempest_fastapi_sdk.db import AsyncDatabaseManager, SlowQueryLogger


class TestThresholdValidation:
    def test_negative_threshold_rejected(self) -> None:
        manager = AsyncDatabaseManager("sqlite+aiosqlite:///:memory:")
        with pytest.raises(ValueError):
            SlowQueryLogger(manager, threshold_ms=-1.0)  # type: ignore[arg-type]


class TestAttachDetach:
    async def test_attach_is_idempotent(self, db: AsyncDatabaseManager) -> None:
        slow = SlowQueryLogger(db.engine, threshold_ms=0.0)
        slow.attach()
        slow.attach()
        assert slow._attached is True
        slow.detach()
        assert slow._attached is False

    async def test_detach_before_attach_is_noop(self, db: AsyncDatabaseManager) -> None:
        slow = SlowQueryLogger(db.engine)
        slow.detach()
        assert slow._attached is False


class TestLogging:
    async def test_slow_query_is_logged(
        self,
        db: AsyncDatabaseManager,
        caplog: pytest.LogCaptureFixture,
    ) -> None:
        slow = SlowQueryLogger(db.engine, threshold_ms=0.0)
        slow.attach()
        with caplog.at_level(
            logging.WARNING, logger="tempest_fastapi_sdk.db.slow_query"
        ):
            async with db.get_session_context() as session:
                await session.execute(text("SELECT 1"))
        slow.detach()
        assert any("slow query" in r.message for r in caplog.records)

    async def test_fast_query_below_threshold_is_silent(
        self,
        db: AsyncDatabaseManager,
        caplog: pytest.LogCaptureFixture,
    ) -> None:
        slow = SlowQueryLogger(db.engine, threshold_ms=60_000.0)
        slow.attach()
        with caplog.at_level(
            logging.WARNING, logger="tempest_fastapi_sdk.db.slow_query"
        ):
            async with db.get_session_context() as session:
                await session.execute(text("SELECT 1"))
        slow.detach()
        assert not any("slow query" in r.message for r in caplog.records)

    async def test_parameters_omitted_by_default(
        self,
        db: AsyncDatabaseManager,
        caplog: pytest.LogCaptureFixture,
    ) -> None:
        slow = SlowQueryLogger(db.engine, threshold_ms=0.0)
        slow.attach()
        with caplog.at_level(
            logging.WARNING, logger="tempest_fastapi_sdk.db.slow_query"
        ):
            async with db.get_session_context() as session:
                await session.execute(text("SELECT :secret"), {"secret": "hunter2"})
        slow.detach()
        joined = " ".join(r.getMessage() for r in caplog.records)
        assert "hunter2" not in joined

    async def test_parameters_included_when_opted_in(
        self,
        db: AsyncDatabaseManager,
        caplog: pytest.LogCaptureFixture,
    ) -> None:
        slow = SlowQueryLogger(db.engine, threshold_ms=0.0, log_parameters=True)
        slow.attach()
        with caplog.at_level(
            logging.WARNING, logger="tempest_fastapi_sdk.db.slow_query"
        ):
            async with db.get_session_context() as session:
                await session.execute(text("SELECT :value"), {"value": "marker123"})
        slow.detach()
        joined = " ".join(r.getMessage() for r in caplog.records)
        assert "marker123" in joined


class TestEngineUnwrapping:
    async def test_accepts_async_engine_directly(
        self, db: AsyncDatabaseManager
    ) -> None:
        slow = SlowQueryLogger(db.engine)
        assert slow._sync_engine is db.engine.sync_engine
