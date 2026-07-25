"""Persistent dead-letter store + admin visibility for background tasks.

The in-memory :class:`~tempest_fastapi_sdk.tasks.DeadLetterSink` protocol
(v0.157.0) says *what* to do with a terminally-failed task; this module ships
the batteries so you can **see and re-run** those failures:

* :class:`BaseDeadLetterModel` + :func:`make_dead_letter_model` — a SQLAlchemy
  table (one row per terminal failure) over the SDK's ``BaseModel``.
* :class:`DbDeadLetterSink` — a ready :class:`DeadLetterSink` that writes each
  dead letter to that table. Pass it to
  :meth:`~tempest_fastapi_sdk.tasks.TaskQueue.dead_letter`.
* :func:`make_dead_letter_admin_model` — a preconfigured
  :class:`~tempest_fastapi_sdk.admin.AdminModel` so the failures show up in the
  admin (filter by task, search the error, export), with an optional
  **requeue** bulk action that re-enqueues the selected calls.
* :func:`task_inventory` — the registered-task inventory (name / schedule /
  retry policy) read straight off the broker, for a "what tasks exist" view.

TaskIQ exposes no universal live queue introspection (Flower is Celery-specific
and leans on the broker's own API), so this deliberately does **not** try to
show pending/in-flight jobs. What it shows is real: persisted failures and the
declared task set.

Everything imports without the ``[tasks]`` extra; TaskIQ and the admin are only
touched inside the functions that need them.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

from sqlalchemy import JSON, Integer, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from tempest_fastapi_sdk.db.model import BaseModel
from tempest_fastapi_sdk.tasks.observability import DeadLetter

if TYPE_CHECKING:
    from tempest_fastapi_sdk.admin import AdminModel
    from tempest_fastapi_sdk.db.connection import AsyncDatabaseManager
    from tempest_fastapi_sdk.tasks.queue import TaskQueue


class BaseDeadLetterModel(BaseModel):
    """A terminally-failed task call, persisted for inspection + requeue.

    Abstract — subclass it (setting ``__tablename__``) in the project, or build
    a concrete class with :func:`make_dead_letter_model`. Inherits the SDK base
    columns (``id`` / ``is_active`` / ``created_at`` / ``updated_at``);
    ``created_at`` is when the failure was recorded.
    """

    __abstract__ = True

    task_name: Mapped[str] = mapped_column(
        String(255),
        nullable=False,
        index=True,
        doc="The registered task name that failed.",
    )
    task_id: Mapped[str] = mapped_column(
        String(255),
        nullable=False,
        doc="The failed invocation's id.",
    )
    error: Mapped[str] = mapped_column(
        Text,
        nullable=False,
        doc="String form of the exception that ended the last attempt.",
    )
    error_type: Mapped[str] = mapped_column(
        String(255),
        nullable=False,
        index=True,
        doc="The exception class name (e.g. 'ValueError').",
    )
    retries: Mapped[int] = mapped_column(
        Integer,
        nullable=False,
        default=0,
        doc="Attempts made before the task was given up on.",
    )
    args: Mapped[list[Any]] = mapped_column(
        JSON,
        nullable=False,
        default=list,
        doc="Positional arguments of the failed call (JSON).",
    )
    kwargs: Mapped[dict[str, Any]] = mapped_column(
        JSON,
        nullable=False,
        default=dict,
        doc="Keyword arguments of the failed call (JSON).",
    )

    @classmethod
    def from_dead_letter(cls, dead_letter: DeadLetter) -> BaseDeadLetterModel:
        """Build a row from a :class:`DeadLetter`.

        Args:
            dead_letter (DeadLetter): The terminal failure to persist.

        Returns:
            BaseDeadLetterModel: An unsaved row instance.
        """
        return cls(
            task_name=dead_letter.task_name,
            task_id=dead_letter.task_id,
            error=str(dead_letter.exception),
            error_type=type(dead_letter.exception).__name__,
            retries=dead_letter.retries,
            args=list(dead_letter.args),
            kwargs=dict(dead_letter.kwargs),
        )


def make_dead_letter_model(
    *,
    tablename: str = "dead_letters",
    class_name: str = "DeadLetterModel",
) -> type[BaseDeadLetterModel]:
    """Build a concrete dead-letter model bound to ``tablename``.

    For tests and lightweight scripts; production code should subclass
    :class:`BaseDeadLetterModel` by hand so migrations pick it up statically.

    Args:
        tablename (str): The table name.
        class_name (str): The generated class name.

    Returns:
        type[BaseDeadLetterModel]: The concrete model class.
    """
    return type(
        class_name,
        (BaseDeadLetterModel,),
        {
            "__tablename__": tablename,
            "__module__": __name__,
            "__qualname__": class_name,
        },
    )


class DbDeadLetterSink:
    """A :class:`DeadLetterSink` that persists dead letters to the database.

    Example:

        >>> model = make_dead_letter_model()
        >>> tq.dead_letter(DbDeadLetterSink(db, model))

    Runs inside TaskIQ's ``on_error`` middleware, which swallows and logs any
    sink exception — so a DB hiccup never crashes the worker.
    """

    def __init__(
        self,
        db: AsyncDatabaseManager,
        model: type[BaseDeadLetterModel],
    ) -> None:
        """Configure the sink.

        Args:
            db (AsyncDatabaseManager): The database manager (opens its own
                short transaction per dead letter).
            model (type[BaseDeadLetterModel]): The concrete dead-letter table.
        """
        self._db = db
        self._model = model

    async def __call__(self, dead_letter: DeadLetter) -> None:
        """Insert one dead letter as a row (commits on clean exit).

        Args:
            dead_letter (DeadLetter): The terminal failure to persist.
        """
        async with self._db.get_session_context() as session:
            session.add(self._model.from_dead_letter(dead_letter))


@dataclass(frozen=True)
class TaskInfo:
    """One registered task's declared shape (for the inventory view).

    Attributes:
        name (str): The registered task name.
        schedule (list[dict[str, Any]]): The cron/interval schedule labels, or
            an empty list for an on-demand task.
        retry_on_error (bool): Whether the task opts into retries.
        max_retries (int | None): The attempt cap, when the task set one.
    """

    name: str
    schedule: list[dict[str, Any]]
    retry_on_error: bool
    max_retries: int | None


def task_inventory(tq: TaskQueue) -> list[TaskInfo]:
    """List every task registered on ``tq``'s broker, sorted by name.

    Reads the declared task set (name + schedule + retry labels) off the
    broker — no live queue state is involved.

    Args:
        tq (TaskQueue): The task queue whose broker to inspect.

    Returns:
        list[TaskInfo]: One entry per registered task, sorted by name.
    """
    infos: list[TaskInfo] = []
    for name, task in tq.broker.get_all_tasks().items():
        labels: dict[str, Any] = dict(getattr(task, "labels", {}) or {})
        max_retries = labels.get("max_retries")
        infos.append(
            TaskInfo(
                name=name,
                schedule=list(labels.get("schedule", []) or []),
                retry_on_error=bool(labels.get("retry_on_error", False)),
                max_retries=int(max_retries) if max_retries is not None else None,
            )
        )
    return sorted(infos, key=lambda info: info.name)


def make_requeue_action(
    tq: TaskQueue,
    *,
    label: str = "Requeue task",
    delete_after: bool = True,
) -> Any:
    """Build an admin bulk action that re-enqueues selected dead letters.

    Each selected row is re-enqueued on ``tq``'s broker with its stored
    ``args`` / ``kwargs``; rows whose task is no longer registered are skipped.
    Successfully requeued rows are deleted when ``delete_after`` is set.

    Args:
        tq (TaskQueue): The queue to re-enqueue onto.
        label (str): The action label shown in the admin dropdown.
        delete_after (bool): Delete a row once its call is re-enqueued.

    Returns:
        Any: An ``@admin_action``-decorated handler (needs the admin module).
    """
    from tempest_fastapi_sdk.admin import (
        AdminActionContext,
        AdminActionResult,
        admin_action,
    )

    @admin_action(label=label)
    async def requeue(ctx: AdminActionContext) -> AdminActionResult:
        rows = await ctx.repository.list(filters={"id": ctx.ids})
        requeued_ids: list[Any] = []
        for row in rows:
            task = tq.broker.find_task(row.task_name)
            if task is None:
                continue
            await task.kiq(*(row.args or []), **(row.kwargs or {}))
            requeued_ids.append(row.id)
        if delete_after and requeued_ids:
            await ctx.repository.delete_batch(requeued_ids)
        skipped = len(rows) - len(requeued_ids)
        message = f"Requeued {len(requeued_ids)} task(s)."
        if skipped:
            message += f" Skipped {skipped} (task no longer registered)."
        category = "success" if requeued_ids else "warning"
        return AdminActionResult(message, category=category)

    return requeue


def make_dead_letter_admin_model(
    model: type[BaseDeadLetterModel],
    *,
    tq: TaskQueue | None = None,
    requeue_label: str = "Requeue task",
    delete_after: bool = True,
) -> AdminModel[Any]:
    """Build a read-mostly :class:`AdminModel` over a dead-letter table.

    Register the result on your :class:`~tempest_fastapi_sdk.admin.AdminSite` to
    get the dead-letter panel: a filterable, searchable list of terminal
    failures. Pass ``tq`` to add the requeue bulk action.

    Args:
        model (type[BaseDeadLetterModel]): The concrete dead-letter table.
        tq (TaskQueue | None): When given, adds a requeue bulk action wired to
            this queue.
        requeue_label (str): Label for the requeue action.
        delete_after (bool): Delete a row once requeued.

    Returns:
        AdminModel[Any]: A preconfigured admin registration (no create/edit).
    """
    from sqlalchemy import desc

    from tempest_fastapi_sdk.admin import AdminModel

    actions = []
    if tq is not None:
        actions.append(
            make_requeue_action(tq, label=requeue_label, delete_after=delete_after)
        )
    return AdminModel(
        model=model,
        list_display=[
            model.task_name,
            model.error_type,
            model.retries,
            model.created_at,
        ],
        list_filter=[model.task_name, model.error_type],
        search_fields=[model.task_name, model.task_id, model.error],
        ordering=desc(model.created_at),
        can_create=False,
        can_edit=False,
        can_delete=True,
        actions=actions,
    )


__all__: list[str] = [
    "BaseDeadLetterModel",
    "DbDeadLetterSink",
    "TaskInfo",
    "make_dead_letter_admin_model",
    "make_dead_letter_model",
    "make_requeue_action",
    "task_inventory",
]
