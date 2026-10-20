"""Heartbeat files for TaskIQ workers and schedulers, and their checker.

A container healthcheck for a worker can only ask the broker whether it
is reachable (:meth:`~tempest_fastapi_sdk.tasks.TaskQueue.health_check`),
and a worker stuck on a hung task, or a scheduler whose loop stopped,
still has a perfectly reachable broker. A heartbeat file closes that gap:
the process proves it is doing its job by rewriting a timestamp, and the
healthcheck asks how old the timestamp is.

:meth:`~tempest_fastapi_sdk.tasks.TaskQueue.enable_heartbeat` wires the
writers. This package holds what the healthcheck needs and imports
nothing outside the standard library, so the checker runs with only the
base package installed::

    python -m tempest_fastapi_sdk.tasks.heartbeat /tmp/worker.heartbeat --max-age 90

The command exits ``0`` when the file exists and its timestamp is younger
than ``--max-age`` seconds, ``1`` otherwise, and prints one line saying
why. It is a package with a ``__main__`` rather than a module so that
``python -m`` does not re-execute a module the ``tasks`` package already
imported (which makes ``runpy`` print a ``RuntimeWarning`` on every
healthcheck).
"""

from tempest_fastapi_sdk.tasks.heartbeat.files import (
    DEFAULT_HEARTBEAT_EVERY as DEFAULT_HEARTBEAT_EVERY,
)
from tempest_fastapi_sdk.tasks.heartbeat.files import (
    HEARTBEAT_SCHEDULE_ID as HEARTBEAT_SCHEDULE_ID,
)
from tempest_fastapi_sdk.tasks.heartbeat.files import (
    HEARTBEAT_TASK_NAME as HEARTBEAT_TASK_NAME,
)
from tempest_fastapi_sdk.tasks.heartbeat.files import (
    HeartbeatWriter as HeartbeatWriter,
)
from tempest_fastapi_sdk.tasks.heartbeat.files import (
    heartbeat_age as heartbeat_age,
)
from tempest_fastapi_sdk.tasks.heartbeat.files import (
    is_heartbeat_fresh as is_heartbeat_fresh,
)
from tempest_fastapi_sdk.tasks.heartbeat.files import (
    main as main,
)
from tempest_fastapi_sdk.tasks.heartbeat.files import (
    read_heartbeat as read_heartbeat,
)
from tempest_fastapi_sdk.tasks.heartbeat.files import (
    write_heartbeat as write_heartbeat,
)

__all__: list[str] = [
    "DEFAULT_HEARTBEAT_EVERY",
    "HEARTBEAT_SCHEDULE_ID",
    "HEARTBEAT_TASK_NAME",
    "HeartbeatWriter",
    "heartbeat_age",
    "is_heartbeat_fresh",
    "main",
    "read_heartbeat",
    "write_heartbeat",
]
