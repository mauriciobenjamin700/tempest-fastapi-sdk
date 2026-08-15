"""TaskIQ-backed background task primitives.

Imports the optional ``taskiq`` package lazily so the rest of the SDK
remains importable when the ``[tasks]`` extra is not installed.

``TaskQueue`` is the recommended typed facade (tasks + scheduler folded
into one object); ``AsyncTaskBrokerManager`` / ``AsyncTaskScheduler`` are
the older lifecycle-only wrappers, kept for backward compatibility. The
``cron`` helpers (``Cron`` / ``CronOffset`` / ``Weekday`` + builder
functions) have no third-party dependency and import without the extra.
"""

from tempest_fastapi_sdk.tasks.cron import (
    Cron as Cron,
)
from tempest_fastapi_sdk.tasks.cron import (
    CronOffset as CronOffset,
)
from tempest_fastapi_sdk.tasks.cron import (
    Weekday as Weekday,
)
from tempest_fastapi_sdk.tasks.cron import (
    daily as daily,
)
from tempest_fastapi_sdk.tasks.cron import (
    every_minute as every_minute,
)
from tempest_fastapi_sdk.tasks.cron import (
    every_n_minutes as every_n_minutes,
)
from tempest_fastapi_sdk.tasks.cron import (
    hourly as hourly,
)
from tempest_fastapi_sdk.tasks.cron import (
    monthly as monthly,
)
from tempest_fastapi_sdk.tasks.cron import (
    weekdays as weekdays,
)
from tempest_fastapi_sdk.tasks.cron import (
    weekends as weekends,
)
from tempest_fastapi_sdk.tasks.cron import (
    weekly as weekly,
)
from tempest_fastapi_sdk.tasks.dead_letter import (
    BaseDeadLetterModel as BaseDeadLetterModel,
)
from tempest_fastapi_sdk.tasks.dead_letter import (
    DbDeadLetterSink as DbDeadLetterSink,
)
from tempest_fastapi_sdk.tasks.dead_letter import (
    TaskInfo as TaskInfo,
)
from tempest_fastapi_sdk.tasks.dead_letter import (
    make_dead_letter_admin_model as make_dead_letter_admin_model,
)
from tempest_fastapi_sdk.tasks.dead_letter import (
    make_dead_letter_model as make_dead_letter_model,
)
from tempest_fastapi_sdk.tasks.dead_letter import (
    make_requeue_action as make_requeue_action,
)
from tempest_fastapi_sdk.tasks.dead_letter import (
    task_inventory as task_inventory,
)
from tempest_fastapi_sdk.tasks.manager import (
    AsyncTaskBrokerManager as AsyncTaskBrokerManager,
)
from tempest_fastapi_sdk.tasks.observability import (
    DeadLetter as DeadLetter,
)
from tempest_fastapi_sdk.tasks.observability import (
    DeadLetterSink as DeadLetterSink,
)
from tempest_fastapi_sdk.tasks.observability import (
    RetryPolicy as RetryPolicy,
)
from tempest_fastapi_sdk.tasks.observability import (
    TaskMetrics as TaskMetrics,
)
from tempest_fastapi_sdk.tasks.observability import (
    make_dead_letter_middleware as make_dead_letter_middleware,
)
from tempest_fastapi_sdk.tasks.oop import TaskBinding as TaskBinding
from tempest_fastapi_sdk.tasks.oop import TaskDef as TaskDef
from tempest_fastapi_sdk.tasks.oop import task_method as task_method
from tempest_fastapi_sdk.tasks.queue import Hook as Hook
from tempest_fastapi_sdk.tasks.queue import LifecycleResource as LifecycleResource
from tempest_fastapi_sdk.tasks.queue import LifecycleScope as LifecycleScope
from tempest_fastapi_sdk.tasks.queue import Task as Task
from tempest_fastapi_sdk.tasks.queue import TaskQueue as TaskQueue
from tempest_fastapi_sdk.tasks.scheduler import AsyncTaskScheduler as AsyncTaskScheduler

__all__: list[str] = [
    "AsyncTaskBrokerManager",
    "AsyncTaskScheduler",
    "BaseDeadLetterModel",
    "Cron",
    "CronOffset",
    "DbDeadLetterSink",
    "DeadLetter",
    "DeadLetterSink",
    "Hook",
    "LifecycleResource",
    "LifecycleScope",
    "RetryPolicy",
    "Task",
    "TaskBinding",
    "TaskDef",
    "TaskInfo",
    "TaskMetrics",
    "TaskQueue",
    "Weekday",
    "daily",
    "every_minute",
    "every_n_minutes",
    "hourly",
    "make_dead_letter_admin_model",
    "make_dead_letter_middleware",
    "make_dead_letter_model",
    "make_requeue_action",
    "monthly",
    "task_inventory",
    "task_method",
    "weekdays",
    "weekends",
    "weekly",
]
