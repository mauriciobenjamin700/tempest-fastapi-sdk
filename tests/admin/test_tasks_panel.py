"""The admin task panel: declared schedule and persisted runs.

Both halves already existed and had no screen — `task_inventory` returned a
list nothing rendered, and a `JobStore` row was reachable only by writing
your own page. What this panel deliberately cannot show is live queue depth:
TaskIQ exposes none, and a screen that implied otherwise would be worse than
no screen.
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from datetime import timedelta
from typing import Any

import pytest
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient

from tempest_fastapi_sdk import (
    AdminSite,
    AsyncDatabaseManager,
    BaseUserModel,
    UserModelAuthBackend,
    make_admin_router,
)
from tempest_fastapi_sdk.admin import ScheduledTask, TaskPanelService
from tempest_fastapi_sdk.tasks import JobStatus, JobStore, make_job_model

SECRET = "x" * 48


class PanelUser(BaseUserModel):
    __tablename__ = "admin_panel_users"


PanelJob = make_job_model(tablename="admin_panel_jobs", class_name="PanelJob")


class _FakeTask:
    """A registered task as the broker's registry exposes it.

    Attributes:
        labels (dict[str, Any]): The schedule and retry labels.
    """

    def __init__(self, **labels: Any) -> None:
        """Store the labels the inventory will read.

        Args:
            **labels (Any): Labels as ``task_inventory`` expects them.
        """
        self.labels: dict[str, Any] = labels


class _FakeBroker:
    """The subset of a TaskIQ broker the inventory touches."""

    def __init__(self, tasks: dict[str, _FakeTask]) -> None:
        """Store the registry.

        Args:
            tasks (dict[str, _FakeTask]): Registered tasks by name.
        """
        self._tasks = tasks

    def get_all_tasks(self) -> dict[str, _FakeTask]:
        """Return the registered tasks.

        Returns:
            dict[str, _FakeTask]: The registry.
        """
        return self._tasks


class _FakeQueue:
    """A ``TaskQueue`` stand-in exposing only ``broker``."""

    def __init__(self, tasks: dict[str, _FakeTask]) -> None:
        """Build the queue around a fake broker.

        Args:
            tasks (dict[str, _FakeTask]): Registered tasks by name.
        """
        self.broker = _FakeBroker(tasks)


def _queue() -> Any:
    """Build a queue whose registry has one cron, one interval, one plain.

    Returns:
        Any: The fake queue.
    """
    return _FakeQueue(
        {
            "reports.nightly": _FakeTask(
                schedule=[{"cron": "0 3 * * *"}],
                retry_on_error=True,
                max_retries=3,
            ),
            "sync.poll": _FakeTask(
                schedule=[{"interval": timedelta(seconds=30)}],
            ),
            "mail.send": _FakeTask(),
        }
    )


@pytest.fixture
async def db() -> AsyncIterator[AsyncDatabaseManager]:
    """Yield a connected in-memory database with the tables created.

    Yields:
        AsyncDatabaseManager: The manager the panel and admin share.
    """
    manager = AsyncDatabaseManager("sqlite+aiosqlite:///:memory:")
    await manager.connect()
    await manager.create_tables()
    yield manager
    await manager.drop_tables()
    await manager.disconnect()


async def _app(
    manager: AsyncDatabaseManager,
    panel: TaskPanelService[Any] | None,
) -> FastAPI:
    """Mount an admin router with an admin user already created.

    Args:
        manager (AsyncDatabaseManager): The database to use.
        panel (TaskPanelService[Any] | None): The panel to mount, if any.

    Returns:
        FastAPI: The app.
    """
    async with manager.get_session_context() as session:
        user = PanelUser(email="root@example.com", hashed_password="", is_admin=True)
        user.set_password("hunter2")
        session.add(user)
        await session.commit()

    app = FastAPI()
    app.include_router(
        make_admin_router(
            AdminSite(title="Panel Admin"),
            db=manager,
            auth_backend=UserModelAuthBackend(PanelUser),
            secret_key=SECRET,
            cookie_secure=False,
            tasks=panel,
        )
    )
    return app


def _client(app: FastAPI) -> AsyncClient:
    """Build an unopened client bound to the app.

    Args:
        app (FastAPI): The app to drive.

    Returns:
        AsyncClient: A client to use inside ``async with``.
    """
    return AsyncClient(transport=ASGITransport(app=app), base_url="http://test")


async def _login(client: AsyncClient) -> None:
    """Sign the admin user in on ``client``.

    Args:
        client (AsyncClient): The open client to authenticate.
    """
    await client.post(
        "/admin/login",
        data={"identifier": "root@example.com", "password": "hunter2"},
        follow_redirects=False,
    )


class TestServiceShape:
    def test_a_panel_with_no_source_is_refused(self) -> None:
        """Two empty sections would say nothing at all."""
        with pytest.raises(ValueError, match="nothing to show"):
            TaskPanelService()

    def test_the_schedule_puts_scheduled_tasks_first(self) -> None:
        panel: TaskPanelService[Any] = TaskPanelService(queue=_queue())
        rows: list[ScheduledTask] = panel.schedule()
        assert [row.name for row in rows] == [
            "reports.nightly",
            "sync.poll",
            "mail.send",
        ]

    def test_an_interval_is_read_as_seconds(self) -> None:
        panel: TaskPanelService[Any] = TaskPanelService(queue=_queue())
        poll = next(row for row in panel.schedule() if row.name == "sync.poll")
        assert poll.interval_seconds == 30.0
        assert poll.cron is None
        assert poll.is_scheduled is True

    def test_an_on_demand_task_is_not_scheduled(self) -> None:
        panel: TaskPanelService[Any] = TaskPanelService(queue=_queue())
        mail = next(row for row in panel.schedule() if row.name == "mail.send")
        assert mail.is_scheduled is False

    async def test_without_a_store_the_run_side_answers_empty(self) -> None:
        """No store is not an error — it is a section that does not render."""
        panel: TaskPanelService[Any] = TaskPanelService(queue=_queue())
        assert await panel.recent_runs() == []
        assert panel.shows_runs is False
        assert panel.shows_schedule is True


class TestPanelPage:
    async def test_both_sections_render(self, db: AsyncDatabaseManager) -> None:
        store: JobStore[Any] = JobStore(db, model=PanelJob)
        await store.enqueue("reports.nightly")
        app = await _app(db, TaskPanelService(queue=_queue(), job_store=store))
        async with _client(app) as client:
            await _login(client)
            response = await client.get("/admin/tasks")
        assert response.status_code == 200, response.text
        assert "reports.nightly" in response.text
        assert "0 3 * * *" in response.text
        assert "TaskIQ exposes none" in response.text

    async def test_only_the_schedule_renders_without_a_store(
        self, db: AsyncDatabaseManager
    ) -> None:
        app = await _app(db, TaskPanelService(queue=_queue()))
        async with _client(app) as client:
            await _login(client)
            response = await client.get("/admin/tasks")
        assert response.status_code == 200
        assert "Schedule" in response.text
        assert "No runs recorded yet" not in response.text

    async def test_only_the_runs_render_without_a_queue(
        self, db: AsyncDatabaseManager
    ) -> None:
        store: JobStore[Any] = JobStore(db, model=PanelJob)
        app = await _app(db, TaskPanelService(job_store=store))
        async with _client(app) as client:
            await _login(client)
            response = await client.get("/admin/tasks")
        assert response.status_code == 200
        assert "No runs recorded yet" in response.text
        assert "No tasks registered" not in response.text

    async def test_the_panel_is_absent_when_not_configured(
        self, db: AsyncDatabaseManager
    ) -> None:
        app = await _app(db, None)
        async with _client(app) as client:
            await _login(client)
            response = await client.get("/admin/tasks")
            dashboard = await client.get("/admin/")
        assert response.status_code == 404
        assert ">Tasks<" not in dashboard.text

    async def test_the_status_filter_narrows_the_list(
        self, db: AsyncDatabaseManager
    ) -> None:
        store: JobStore[Any] = JobStore(db, model=PanelJob)
        await store.enqueue("kept")
        done = await store.enqueue("finished")
        await store.claim(done.id)
        await store.succeed(done.id)
        app = await _app(db, TaskPanelService(job_store=store))
        async with _client(app) as client:
            await _login(client)
            response = await client.get("/admin/tasks?status=queued")
        assert response.status_code == 200
        assert "kept" in response.text
        assert "finished" not in response.text


class TestRunDetail:
    async def test_a_run_shows_its_stage_and_attempts(
        self, db: AsyncDatabaseManager
    ) -> None:
        store: JobStore[Any] = JobStore(db, model=PanelJob)
        job = await store.enqueue("extract")
        await store.claim(job.id)
        await store.report_progress(job.id, progress=0.4, stage="parsing")
        app = await _app(db, TaskPanelService(job_store=store))
        async with _client(app) as client:
            await _login(client)
            response = await client.get(f"/admin/tasks/{job.id}")
        assert response.status_code == 200, response.text
        assert "parsing" in response.text
        assert "Cancel this run" in response.text

    async def test_a_finished_run_offers_no_cancel(
        self, db: AsyncDatabaseManager
    ) -> None:
        store: JobStore[Any] = JobStore(db, model=PanelJob)
        job = await store.enqueue("extract")
        await store.claim(job.id)
        await store.succeed(job.id)
        app = await _app(db, TaskPanelService(job_store=store))
        async with _client(app) as client:
            await _login(client)
            response = await client.get(f"/admin/tasks/{job.id}")
        assert response.status_code == 200
        assert "Cancel this run" not in response.text

    async def test_a_missing_run_answers_404_with_a_page(
        self, db: AsyncDatabaseManager
    ) -> None:
        """A bookmarked link outlives the row a retention sweep deleted."""
        from uuid import uuid4

        store: JobStore[Any] = JobStore(db, model=PanelJob)
        app = await _app(db, TaskPanelService(job_store=store))
        async with _client(app) as client:
            await _login(client)
            response = await client.get(f"/admin/tasks/{uuid4()}")
        assert response.status_code == 404
        assert "no longer stored" in response.text

    async def test_cancelling_flips_the_row(self, db: AsyncDatabaseManager) -> None:
        store: JobStore[Any] = JobStore(db, model=PanelJob)
        job = await store.enqueue("extract")
        app = await _app(db, TaskPanelService(job_store=store))
        async with _client(app) as client:
            await _login(client)
            response = await client.post(
                f"/admin/tasks/{job.id}/cancel",
                follow_redirects=False,
            )
        assert response.status_code == 303
        assert response.headers["location"] == f"/admin/tasks/{job.id}"
        assert (await store.get(job.id)).status == JobStatus.CANCELLED.value


class TestAccess:
    async def test_the_panel_needs_a_session(self, db: AsyncDatabaseManager) -> None:
        app = await _app(db, TaskPanelService(queue=_queue()))
        async with _client(app) as client:
            response = await client.get("/admin/tasks", follow_redirects=False)
        assert response.status_code == 303
        assert response.headers["location"] == "/admin/login"
