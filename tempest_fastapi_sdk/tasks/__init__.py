"""TaskIQ-backed background task primitives.

Imports the optional ``taskiq`` package lazily so the rest of the SDK
remains importable when the ``[tasks]`` extra is not installed.

``TaskQueue`` is the recommended typed facade (tasks + scheduler folded
into one object); ``AsyncTaskBrokerManager`` / ``AsyncTaskScheduler`` are
the older lifecycle-only wrappers, kept for backward compatibility.
"""

from tempest_fastapi_sdk.tasks.manager import (
    AsyncTaskBrokerManager as AsyncTaskBrokerManager,
)
from tempest_fastapi_sdk.tasks.queue import Task as Task
from tempest_fastapi_sdk.tasks.queue import TaskQueue as TaskQueue
from tempest_fastapi_sdk.tasks.scheduler import AsyncTaskScheduler as AsyncTaskScheduler

__all__: list[str] = [
    "AsyncTaskBrokerManager",
    "AsyncTaskScheduler",
    "Task",
    "TaskQueue",
]
