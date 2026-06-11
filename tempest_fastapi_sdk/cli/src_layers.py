"""Add optional ``src`` layers to an existing project from its extras.

``tempest new`` scaffolds the always-present layers (api, controllers,
services, schemas, db, core, utils). The layers that only make sense
for a specific SDK extra — ``[queue]`` (FastStream) and ``[tasks]``
(TaskIQ) — are NOT part of the base skeleton: dropping empty
placeholder packages on every service contradicts the layout rules.

``tempest generate --src`` reads the extras pinned in the project's
``pyproject.toml`` and writes only the matching layers. The operation
is idempotent: existing files are left untouched unless ``--force`` is
passed, so a hand-edited handler is never clobbered silently.
"""

from __future__ import annotations

from pathlib import Path

# Each ``__ROOT__`` placeholder is replaced with the detected source
# root package (``src`` or ``app``) so intra-project imports resolve.
_ROOT_PLACEHOLDER = "__ROOT__"


_QUEUE_INIT = '''\
"""FastStream (RabbitMQ) message-queue wiring for this service.

A single :class:`AsyncBrokerManager` owns the broker for the whole
process. Routers and services reach it through :func:`get_broker`;
connect/disconnect run from the app lifespan.
"""

from __future__ import annotations

import os

from faststream.rabbit import RabbitBroker

from tempest_fastapi_sdk import AsyncBrokerManager

RABBITMQ_URL: str = os.environ.get(
    "RABBITMQ_URL",
    "amqp://guest:guest@localhost:5672/",
)
"""AMQP URL the broker connects to (override via the ``.env`` file)."""

broker: RabbitBroker = RabbitBroker(RABBITMQ_URL)
"""Process-wide FastStream broker; subscribers register against it."""

broker_manager: AsyncBrokerManager = AsyncBrokerManager(broker)
"""SDK manager wrapping :data:`broker` (connect/disconnect/publish)."""


def get_broker() -> AsyncBrokerManager:
    """Return the process-wide FastStream broker manager.

    Returns:
        AsyncBrokerManager: The shared manager (connected during the
        app lifespan).
    """
    return broker_manager


__all__: list[str] = ["broker", "broker_manager", "get_broker"]
'''


_QUEUE_HANDLERS = '''\
"""FastStream subscribers and publishers for this service.

Handlers are declared against the shared :data:`broker` so they
register automatically once the broker starts. Import this module from
the app lifespan (or ``src.queue``) to make sure the decorators run.
"""

from __future__ import annotations

from __ROOT__.queue import broker


@broker.subscriber("example")
async def handle_example(message: str) -> None:
    """Consume one message from the ``example`` queue.

    Args:
        message (str): The decoded message payload.

    Returns:
        None: Side-effecting consumer; nothing is returned.
    """
    print(f"received: {message}")


__all__: list[str] = ["handle_example"]
'''


_TASKS_INIT = '''\
"""TaskIQ (RabbitMQ) background-task wiring for this service.

A single :class:`AsyncTaskBrokerManager` owns the broker for the whole
process. Request handlers enqueue jobs via ``.kiq(...)``; a separate
worker process consumes them. connect/disconnect run from the app
lifespan.
"""

from __future__ import annotations

import os

from taskiq_aio_pika import AioPikaBroker

from tempest_fastapi_sdk import AsyncTaskBrokerManager

TASKIQ_BROKER_URL: str = os.environ.get(
    "TASKIQ_BROKER_URL",
    "amqp://guest:guest@localhost:5672/",
)
"""AMQP URL the TaskIQ broker connects to (override via ``.env``)."""

broker: AioPikaBroker = AioPikaBroker(TASKIQ_BROKER_URL)
"""Process-wide TaskIQ broker; tasks register against it."""

task_manager: AsyncTaskBrokerManager = AsyncTaskBrokerManager(broker)
"""SDK manager wrapping :data:`broker` (connect/disconnect/task)."""


def get_task_manager() -> AsyncTaskBrokerManager:
    """Return the process-wide TaskIQ broker manager.

    Returns:
        AsyncTaskBrokerManager: The shared manager (connected during
        the app lifespan).
    """
    return task_manager


__all__: list[str] = ["broker", "task_manager", "get_task_manager"]
'''


_TASKS_JOBS = '''\
"""TaskIQ background jobs for this service.

Jobs are declared against the shared :data:`broker` so they register
on startup. Enqueue them from a request handler with
``await example_job.kiq("payload")``.
"""

from __future__ import annotations

from __ROOT__.tasks import broker


@broker.task
async def example_job(payload: str) -> str:
    """Process one background job.

    Args:
        payload (str): Arbitrary job input.

    Returns:
        str: A short result string echoing the processed payload.
    """
    return f"processed: {payload}"


__all__: list[str] = ["example_job"]
'''


# extra -> {relative path under the source root: file content}.
LAYER_FILES: dict[str, dict[str, str]] = {
    "queue": {
        "queue/__init__.py": _QUEUE_INIT,
        "queue/handlers.py": _QUEUE_HANDLERS,
    },
    "tasks": {
        "tasks/__init__.py": _TASKS_INIT,
        "tasks/jobs.py": _TASKS_JOBS,
    },
}
"""Maps each layer-bearing extra to the files it contributes."""


def detect_source_root(target: Path) -> str:
    """Return the project's source-root package name (``src`` or ``app``).

    The layout rules allow either ``src/`` or ``app/`` as the root.
    When neither exists yet, ``"src"`` is assumed (the scaffold default).

    Args:
        target (Path): Project root directory.

    Returns:
        str: ``"src"`` or ``"app"``.
    """
    if (target / "app").is_dir() and not (target / "src").is_dir():
        return "app"
    return "src"


def layers_for_extras(extras: set[str]) -> list[str]:
    """Return the sorted layer keys triggered by the given extras.

    Args:
        extras (set[str]): The parsed SDK extras.

    Returns:
        list[str]: Extra names that contribute a source layer, sorted.
    """
    return sorted(extras & LAYER_FILES.keys())


def add_src_layers(
    target: Path,
    extras: set[str],
    *,
    force: bool,
) -> tuple[list[Path], list[Path]]:
    """Write the source layers triggered by ``extras`` into ``target``.

    Args:
        target (Path): Project root directory.
        extras (set[str]): Parsed SDK extras driving which layers land.
        force (bool): Overwrite files that already exist. When False,
            existing files are skipped (reported separately).

    Returns:
        tuple[list[Path], list[Path]]: ``(written, skipped)`` absolute
        paths — files written this run and files left untouched because
        they already existed and ``force`` was False.
    """
    root = detect_source_root(target)
    root_dir = target / root

    written: list[Path] = []
    skipped: list[Path] = []
    for extra in layers_for_extras(extras):
        for relative, content in LAYER_FILES[extra].items():
            destination = root_dir.joinpath(*relative.split("/"))
            if destination.exists() and not force:
                skipped.append(destination)
                continue
            destination.parent.mkdir(parents=True, exist_ok=True)
            destination.write_text(
                content.replace(_ROOT_PLACEHOLDER, root),
                encoding="utf-8",
            )
            written.append(destination)
    return written, skipped


__all__: list[str] = [
    "LAYER_FILES",
    "add_src_layers",
    "detect_source_root",
    "layers_for_extras",
]
