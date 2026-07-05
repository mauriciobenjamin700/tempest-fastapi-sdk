"""Class-based background tasks — the symmetric counterpart to ``@tq.task``.

Mirrors :mod:`tempest_fastapi_sdk.queue.consumer`: group background tasks
in a class (shared setup, inheritance) instead of free functions. Two
explicit styles, no magic:

**1. Constructor form** — one task per class; name it in the constructor,
override :meth:`TaskDef.run`::

    class NightlyReport(TaskDef):
        def __init__(self) -> None:
            super().__init__(name="reports:nightly")

        async def run(self, day: str) -> None:
            ...

    nightly = tq.register(NightlyReport())   # -> a Task
    await nightly.enqueue(day="2026-07-05")

**2. Grouped form** — many tasks per class, each method marked with
:func:`task_method`::

    class ReportTasks(TaskDef):
        @task_method(name="reports:nightly")
        async def nightly(self, day: str) -> None: ...

        @task_method()
        async def weekly(self) -> None: ...

    tasks = tq.register(ReportTasks())        # -> {"nightly": Task, "weekly": Task}
    await tasks["nightly"].enqueue(day="2026-07-05")

:meth:`~tempest_fastapi_sdk.tasks.TaskQueue.register` reads
:meth:`TaskDef.task_bindings` and registers each one.
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from typing import Any

_TASK_ATTR = "__tempest_task__"


def _marked_method_names(cls: type) -> list[str]:
    """Return the names of methods marked with :func:`task_method`.

    Scans the class MRO's ``__dict__`` directly (never the instance) so
    properties are not evaluated — avoids recursion when a property itself
    inspects its class.

    Args:
        cls (type): The class to scan.

    Returns:
        list[str]: Marked method names, in MRO then definition order,
        de-duplicated.
    """
    names: list[str] = []
    for klass in cls.__mro__:
        for name, value in vars(klass).items():
            if callable(value) and hasattr(value, _TASK_ATTR) and name not in names:
                names.append(name)
    return names


def task_method(
    name: str | None = None,
    **options: Any,
) -> Callable[[Callable[..., Awaitable[Any]]], Callable[..., Awaitable[Any]]]:
    """Mark a :class:`TaskDef` method as a background task (grouped form).

    Args:
        name (str | None): Override the auto-generated ``module:function``
            task name.
        **options (Any): Extra TaskIQ labels / options forwarded to the
            task registration.

    Returns:
        Callable: The same method, tagged so ``register`` can find it.
    """

    def mark(
        method: Callable[..., Awaitable[Any]],
    ) -> Callable[..., Awaitable[Any]]:
        setattr(method, _TASK_ATTR, {"name": name, "options": options})
        return method

    return mark


@dataclass(slots=True)
class TaskBinding:
    """One task registration produced by a :class:`TaskDef`.

    Attributes:
        key (str): A stable key (the method name, or ``"run"`` for the
            constructor form) used to look the task up after registration.
        func (Callable[..., Awaitable[Any]]): The async callable to run.
        name (str | None): Explicit task name override, or ``None``.
        options (dict[str, Any]): Extra TaskIQ labels / options.
    """

    key: str
    func: Callable[..., Awaitable[Any]]
    name: str | None
    options: dict[str, Any] = field(default_factory=dict)


class TaskDef:
    """Base class for class-based background tasks.

    Subclass it in either style from the module docstring. Register an
    instance with :meth:`~tempest_fastapi_sdk.tasks.TaskQueue.register`,
    which returns a single ``Task`` (constructor form) or a
    ``dict[str, Task]`` keyed by method name (grouped form).

    Attributes:
        name (str | None): Task name for the constructor form; ``None``
            lets TaskIQ derive ``module:function``.
    """

    name: str | None = None

    def __init__(self, *, name: str | None = None) -> None:
        """Configure the constructor form.

        Args:
            name (str | None): Override the task name. Omit in the grouped
                (``@task_method``) form.
        """
        if name is not None:
            self.name = name

    async def run(self, *args: Any, **kwargs: Any) -> Any:
        """Run the task — override in the constructor form.

        Raises:
            NotImplementedError: When neither ``run`` is overridden nor
                any :func:`task_method` is defined.
        """
        raise NotImplementedError(
            "Override run() (constructor form) or mark methods with "
            "@task_method (grouped form).",
        )

    def task_bindings(self) -> list[TaskBinding]:
        """Return every task this definition declares.

        Grouped ``@task_method`` methods take precedence; if none are
        present, the constructor form (``run`` + ``name``) is used.

        Returns:
            list[TaskBinding]: One entry per task.
        """
        grouped: list[TaskBinding] = []
        for attr_name in _marked_method_names(type(self)):
            method = getattr(self, attr_name)
            meta = getattr(method, _TASK_ATTR)
            grouped.append(
                TaskBinding(
                    key=attr_name,
                    func=method,
                    name=meta["name"],
                    options=meta["options"],
                ),
            )
        if grouped:
            return grouped
        return [TaskBinding(key="run", func=self.run, name=self.name)]

    @property
    def is_grouped(self) -> bool:
        """Return ``True`` when this uses the grouped ``@task_method`` form.

        Returns:
            bool: ``True`` if any method is marked with :func:`task_method`.
        """
        return bool(_marked_method_names(type(self)))


__all__: list[str] = [
    "TaskBinding",
    "TaskDef",
    "task_method",
]
