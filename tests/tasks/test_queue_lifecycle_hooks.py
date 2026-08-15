"""Tests for ``TaskQueue`` startup/shutdown hooks and managed resources.

A ``taskiq worker`` process has no FastAPI ``lifespan``, so without these
hooks there is nowhere to open the database or the message broker and
nowhere to close them — the worker works by accident, on lazy connects,
and never disposes a pool.
"""

from __future__ import annotations

from typing import Any

import pytest
from taskiq import TaskiqEvents

from tempest_fastapi_sdk.tasks import LifecycleResource, TaskQueue


class FakeResource:
    """A resource that records when it was opened and closed."""

    def __init__(self, name: str, log: list[str]) -> None:
        """Initialize the resource.

        Args:
            name (str): Label written to the shared log.
            log (list[str]): Shared list recording the call order.
        """
        self.name: str = name
        self.log: list[str] = log

    async def connect(self) -> None:
        """Record that the resource was opened."""
        self.log.append(f"connect:{self.name}")

    async def disconnect(self) -> None:
        """Record that the resource was closed."""
        self.log.append(f"disconnect:{self.name}")


class TestHooksFire:
    """The in-memory broker runs both sides, so worker hooks are testable."""

    async def test_startup_hook_runs_on_connect(self) -> None:
        """The bare decorator form registers and fires."""
        queue = TaskQueue.memory()
        calls: list[str] = []

        @queue.on_startup
        async def _open() -> None:
            calls.append("open")

        await queue.connect()
        await queue.disconnect()

        assert calls == ["open"]

    async def test_shutdown_hook_runs_on_disconnect(self) -> None:
        """The mirror half, and only at the end."""
        queue = TaskQueue.memory()
        calls: list[str] = []

        @queue.on_shutdown
        async def _close() -> None:
            calls.append("close")

        await queue.connect()
        assert calls == []

        await queue.disconnect()
        assert calls == ["close"]

    async def test_a_sync_hook_is_accepted(self) -> None:
        """TaskIQ awaits only what is awaitable, so both shapes work."""
        queue = TaskQueue.memory()
        calls: list[str] = []

        @queue.on_startup
        def _open() -> None:
            calls.append("open")

        await queue.connect()
        await queue.disconnect()

        assert calls == ["open"]

    async def test_the_hook_takes_no_arguments(self) -> None:
        """TaskIQ passes its state; the SDK's hooks must not have to name it."""
        queue = TaskQueue.memory()
        seen: list[Any] = []

        @queue.on_startup
        async def _open() -> None:
            seen.append(queue.broker.state)

        await queue.connect()
        await queue.disconnect()

        assert len(seen) == 1

    async def test_the_decorator_returns_the_function(self) -> None:
        """A decorated handler stays callable under its own name."""
        queue = TaskQueue.memory()

        @queue.on_startup
        async def _open() -> None:
            return None

        assert callable(_open)


class TestScope:
    """Which process a hook is registered for."""

    def test_worker_is_the_default(self) -> None:
        """The web process has its own lifespan and must be left alone."""
        queue = TaskQueue.memory()

        @queue.on_startup
        async def _open() -> None: ...

        assert len(queue.broker.event_handlers[TaskiqEvents.WORKER_STARTUP]) == 1
        assert not queue.broker.event_handlers[TaskiqEvents.CLIENT_STARTUP]

    def test_client_scope_registers_on_the_client_event(self) -> None:
        """For a hook that belongs to whoever enqueues, not who runs."""
        queue = TaskQueue.memory()

        @queue.on_startup(scope="client")
        async def _open() -> None: ...

        assert not queue.broker.event_handlers[TaskiqEvents.WORKER_STARTUP]
        assert len(queue.broker.event_handlers[TaskiqEvents.CLIENT_STARTUP]) == 1

    def test_both_registers_twice(self) -> None:
        """One handler, two events."""
        queue = TaskQueue.memory()

        @queue.on_shutdown(scope="both")
        async def _close() -> None: ...

        assert len(queue.broker.event_handlers[TaskiqEvents.WORKER_SHUTDOWN]) == 1
        assert len(queue.broker.event_handlers[TaskiqEvents.CLIENT_SHUTDOWN]) == 1


class TestResources:
    """``resources=[db, broker]`` — the one-line form."""

    async def test_resources_are_opened_and_closed(self) -> None:
        """Passed to the constructor, managed for the process's lifetime."""
        log: list[str] = []
        queue = TaskQueue.memory(resources=[FakeResource("db", log)])

        await queue.connect()
        assert log == ["connect:db"]

        await queue.disconnect()
        assert log == ["connect:db", "disconnect:db"]

    async def test_they_close_in_reverse_order(self) -> None:
        """A resource that depends on an earlier one still has it."""
        log: list[str] = []
        queue = TaskQueue.memory(
            resources=[FakeResource("db", log), FakeResource("broker", log)],
        )

        await queue.connect()
        await queue.disconnect()

        assert log == [
            "connect:db",
            "connect:broker",
            "disconnect:broker",
            "disconnect:db",
        ]

    async def test_use_is_the_same_thing_after_construction(self) -> None:
        """``queue.use(db)`` for a resource built later."""
        log: list[str] = []
        queue = TaskQueue.memory()
        queue.use(FakeResource("db", log))

        await queue.connect()
        await queue.disconnect()

        assert log == ["connect:db", "disconnect:db"]

    def test_the_database_manager_satisfies_the_protocol(self) -> None:
        """The protocol exists so the SDK never names its own classes."""
        from tempest_fastapi_sdk.db import AsyncDatabaseManager

        manager = AsyncDatabaseManager("sqlite+aiosqlite:///:memory:")

        assert isinstance(manager, LifecycleResource)

    def test_something_without_connect_does_not(self) -> None:
        """The guard fires: an arbitrary object is not a resource."""
        assert not isinstance(object(), LifecycleResource)


class TestLifespanIntegration:
    """The hooks also run under the facade's own context manager."""

    async def test_lifespan_runs_both_ends(self) -> None:
        """``async with queue.lifespan()`` opens and closes the resources."""
        log: list[str] = []
        queue = TaskQueue.memory(resources=[FakeResource("db", log)])

        async with queue.lifespan():
            assert log == ["connect:db"]

        assert log == ["connect:db", "disconnect:db"]


class TestFailureIsVisible:
    """A hook that raises must not be swallowed."""

    async def test_a_raising_startup_hook_propagates(self) -> None:
        """Silent resource failure is how a worker dies mysteriously."""
        queue = TaskQueue.memory()

        @queue.on_startup
        async def _open() -> None:
            raise RuntimeError("database is unreachable")

        with pytest.raises(RuntimeError, match="database is unreachable"):
            await queue.connect()
