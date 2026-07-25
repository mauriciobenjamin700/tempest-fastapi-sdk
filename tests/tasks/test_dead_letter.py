"""Tests for the persistent dead-letter store + admin visibility."""

from __future__ import annotations

from collections.abc import AsyncGenerator
from typing import Any

import pytest_asyncio
from sqlalchemy import select
from taskiq import InMemoryBroker

from tempest_fastapi_sdk import AdminActionContext
from tempest_fastapi_sdk.db import AsyncDatabaseManager, BaseRepository
from tempest_fastapi_sdk.tasks import (
    DbDeadLetterSink,
    DeadLetter,
    RetryPolicy,
    TaskInfo,
    TaskQueue,
    make_dead_letter_admin_model,
    make_dead_letter_model,
    make_requeue_action,
    task_inventory,
)

_DeadLetterModel = make_dead_letter_model(tablename="dl_test", class_name="DlTestModel")


class _DeadLetterRepository(BaseRepository[Any]):
    def __init__(self, session: Any) -> None:
        super().__init__(session, model=_DeadLetterModel)

    def map_to_schema(self, instance: Any) -> Any:
        return instance

    def map_to_model(self, data: dict[str, Any]) -> Any:
        return _DeadLetterModel(**data)

    def map_to_response(self, instance: Any) -> Any:
        return instance


@pytest_asyncio.fixture
async def db() -> AsyncGenerator[AsyncDatabaseManager]:
    manager = AsyncDatabaseManager("sqlite+aiosqlite:///:memory:")
    await manager.connect()
    await manager.create_tables()
    try:
        yield manager
    finally:
        await manager.drop_tables()
        await manager.disconnect()


class TestModel:
    def test_from_dead_letter_maps_fields(self) -> None:
        row = _DeadLetterModel.from_dead_letter(
            DeadLetter(
                task_name="jobs:x",
                task_id="abc",
                exception=ValueError("boom"),
                retries=3,
                args=[1, 2],
                kwargs={"k": "v"},
            )
        )
        assert row.task_name == "jobs:x"
        assert row.task_id == "abc"
        assert row.error == "boom"
        assert row.error_type == "ValueError"
        assert row.retries == 3
        assert row.args == [1, 2]
        assert row.kwargs == {"k": "v"}


class TestDbSink:
    async def test_persists_a_dead_letter(self, db: AsyncDatabaseManager) -> None:
        sink = DbDeadLetterSink(db, _DeadLetterModel)
        await sink(
            DeadLetter(
                task_name="jobs:y",
                task_id="id1",
                exception=RuntimeError("nope"),
                retries=1,
            )
        )
        async with db.get_session_context() as session:
            rows = (await session.execute(select(_DeadLetterModel))).scalars().all()
        assert len(rows) == 1
        assert rows[0].task_name == "jobs:y"
        assert rows[0].error_type == "RuntimeError"

    async def test_task_failure_lands_in_the_table(
        self, db: AsyncDatabaseManager
    ) -> None:
        tq = TaskQueue(InMemoryBroker())
        tq.dead_letter(DbDeadLetterSink(db, _DeadLetterModel))

        @tq.task(name="jobs:fail")
        async def fail(x: int) -> None:
            raise ValueError("kaput")

        await tq.connect()
        await (await fail.enqueue(9)).wait_result()
        await tq.disconnect()

        async with db.get_session_context() as session:
            rows = (await session.execute(select(_DeadLetterModel))).scalars().all()
        assert len(rows) == 1
        assert rows[0].task_name == "jobs:fail"
        assert rows[0].args == [9]


class TestTaskInventory:
    async def test_lists_registered_tasks(self) -> None:
        tq = TaskQueue(InMemoryBroker())

        @tq.task(name="jobs:plain")
        async def plain() -> None: ...

        @tq.cron("*/5 * * * *", name="jobs:cronny")
        async def cronny() -> None: ...

        @tq.task(name="jobs:retrier", retry=RetryPolicy(max_retries=4))
        async def retrier() -> None: ...

        inventory = task_inventory(tq)
        by_name = {info.name: info for info in inventory}
        assert isinstance(inventory[0], TaskInfo)
        assert by_name["jobs:cronny"].schedule == [{"cron": "*/5 * * * *"}]
        assert by_name["jobs:retrier"].retry_on_error is True
        assert by_name["jobs:retrier"].max_retries == 4
        assert by_name["jobs:plain"].retry_on_error is False


class TestRequeueAction:
    async def test_requeues_and_deletes(self, db: AsyncDatabaseManager) -> None:
        ran: list[int] = []
        tq = TaskQueue(InMemoryBroker())

        @tq.task(name="jobs:redo")
        async def redo(x: int) -> None:
            ran.append(x)

        await tq.connect()
        async with db.get_session_context() as session:
            session.add(
                _DeadLetterModel.from_dead_letter(
                    DeadLetter(
                        task_name="jobs:redo",
                        task_id="i1",
                        exception=ValueError("x"),
                        retries=1,
                        args=[42],
                    )
                )
            )

        async with db.get_session_context() as session:
            repo = _DeadLetterRepository(session)
            rows = (await session.execute(select(_DeadLetterModel))).scalars().all()
            action = make_requeue_action(tq)
            ctx = AdminActionContext(
                ids=[rows[0].id],
                repository=repo,
                db_session=session,
                request=None,
                session=None,
                principal=None,
            )
            result = await action(ctx)

        await tq.disconnect()

        assert result is not None
        assert result.category == "success"
        assert ran == [42]
        async with db.get_session_context() as session:
            remaining = (
                (await session.execute(select(_DeadLetterModel))).scalars().all()
            )
        assert remaining == []

    async def test_skips_unregistered_task(self, db: AsyncDatabaseManager) -> None:
        tq = TaskQueue(InMemoryBroker())
        await tq.connect()
        async with db.get_session_context() as session:
            session.add(
                _DeadLetterModel.from_dead_letter(
                    DeadLetter(
                        task_name="jobs:gone",
                        task_id="i2",
                        exception=ValueError("x"),
                        retries=1,
                    )
                )
            )
        async with db.get_session_context() as session:
            repo = _DeadLetterRepository(session)
            rows = (await session.execute(select(_DeadLetterModel))).scalars().all()
            action = make_requeue_action(tq)
            ctx = AdminActionContext(
                ids=[rows[0].id],
                repository=repo,
                db_session=session,
                request=None,
                session=None,
                principal=None,
            )
            result = await action(ctx)
        await tq.disconnect()
        assert result is not None
        assert result.category == "warning"


class TestAdminModel:
    def test_builds_read_mostly_model(self) -> None:
        admin = make_dead_letter_admin_model(_DeadLetterModel)
        assert admin.can_create is False
        assert admin.can_edit is False
        assert admin.can_delete is True

    def test_includes_requeue_action_when_tq_given(self) -> None:
        tq = TaskQueue(InMemoryBroker())
        admin = make_dead_letter_admin_model(_DeadLetterModel, tq=tq)
        assert len(admin.custom_actions()) == 1
