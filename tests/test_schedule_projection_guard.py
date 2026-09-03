"""Guard: the panel row models every schedule key TaskIQ reads.

``TaskPanelService.schedule()`` used to read a chosen pair of keys off the
broker registry, and that projection dropped a declaration three times:
an interval shown as ``on demand`` (fixed in v0.268.0), a one-shot
``time`` doing the same, and a ``cron_offset`` that left the panel stating
an hour three hours off what actually fires.

The keys are derived from ``taskiq.scheduler.scheduled_task.ScheduledTask``
rather than written down here, so a key TaskIQ adds later fails this file
instead of evaporating on the screen. A key that genuinely does not belong
on a schedule row is exempted **with a reason**, never by omission.

Blind spot: this reads the dataclass, not the template. That the cell
renders legibly is checked in ``tests/admin/test_tasks_panel.py`` and by
the browser pass.
"""

from __future__ import annotations

import dataclasses
from collections.abc import Mapping
from datetime import datetime, timedelta, timezone
from typing import Any, Final

import taskiq
from taskiq.scheduler.scheduled_task import ScheduledTask as TaskiqScheduledTask

from tempest_fastapi_sdk.admin import TaskPanelService, TaskTrigger
from tempest_fastapi_sdk.tasks import TaskQueue

MODELLED: Final[Mapping[str, str]] = {
    "cron": "cron",
    "cron_offset": "cron_offset",
    "interval": "interval_seconds",
    "time": "run_at",
}
"""TaskIQ schedule key to the :class:`TaskTrigger` field that carries it."""

EXEMPT: Final[Mapping[str, str]] = {
    "task_name": "identity of the task, carried by ScheduledTask.name",
    "task_id": "assigned per dispatch, not part of the declaration",
    "schedule_id": "assigned per entry by the schedule source",
    "labels": "describes what is sent, not when; reachable via extra",
    "args": "describes what is sent, not when; reachable via extra",
    "kwargs": "describes what is sent, not when; reachable via extra",
}
"""Keys that deliberately get no field, each with the reason why."""


def taskiq_schedule_keys() -> frozenset[str]:
    """Return every field TaskIQ's own scheduled task declares.

    Returns:
        frozenset[str]: The field names, read off the pydantic model so
        the set follows the installed TaskIQ rather than a copy here.
    """
    return frozenset(TaskiqScheduledTask.model_fields)


def uncovered_keys(modelled: Mapping[str, str]) -> set[str]:
    """Return schedule keys that ``modelled`` neither maps nor exempts.

    Args:
        modelled (Mapping[str, str]): Candidate key-to-field mapping.

    Returns:
        set[str]: The keys left unaccounted for.
    """
    return {
        key
        for key in taskiq_schedule_keys()
        if key not in modelled and key not in EXEMPT
    }


class TestEveryKeyIsAccountedFor:
    def test_no_schedule_key_is_unaccounted_for(self) -> None:
        assert uncovered_keys(MODELLED) == set()

    def test_every_modelled_key_names_a_real_field(self) -> None:
        fields = {field.name for field in dataclasses.fields(TaskTrigger)}
        assert set(MODELLED.values()) <= fields

    def test_modelled_and_exempt_stay_disjoint(self) -> None:
        assert set(MODELLED) & set(EXEMPT) == set()

    def test_every_exemption_carries_a_reason(self) -> None:
        assert all(reason.strip() for reason in EXEMPT.values())


class TestTheGuardFires:
    """Fed the projection that shipped, the check has to fail.

    0.283.1 modelled ``cron`` and ``interval`` only. Both keys it dropped
    were real declarations a consumer had written.
    """

    def test_the_0_283_1_projection_is_reported(self) -> None:
        shipped: Mapping[str, str] = {
            "cron": "cron",
            "interval": "interval_seconds",
        }
        assert uncovered_keys(shipped) == {"cron_offset", "time"}


class TestAnEntryWithEveryKeyReachesTheRow:
    """A single entry carrying all of them loses nothing."""

    def test_when_keys_land_on_fields_and_the_rest_lands_in_extra(self) -> None:
        when = datetime(2026, 1, 1, 12, tzinfo=timezone.utc)
        entry: dict[str, Any] = {
            "cron": "0 2 * * *",
            "cron_offset": timedelta(hours=-3),
            "interval": timedelta(seconds=30),
            "time": when,
            "labels": {"queue": "slow"},
            "args": [1],
            "kwargs": {"a": 2},
            "schedule_id": "abc",
        }
        tq: TaskQueue = TaskQueue(taskiq.InMemoryBroker())

        @tq.task(name="everything", schedule=[entry])
        async def everything() -> None:
            return None

        panel: TaskPanelService[Any] = TaskPanelService(queue=tq)
        row = next(item for item in panel.schedule() if item.name == "everything")
        trigger = row.triggers[0]

        assert trigger.cron == "0 2 * * *"
        assert trigger.cron_offset == timedelta(hours=-3)
        assert trigger.interval_seconds == 30.0
        assert trigger.run_at == when
        assert set(trigger.extra) == {"labels", "args", "kwargs", "schedule_id"}
