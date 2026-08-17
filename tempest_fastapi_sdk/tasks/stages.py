"""Several independent stages of work, tracked on the record itself.

:class:`~tempest_fastapi_sdk.tasks.JobStore` gives long work its own row.
That is right when the work *is* the thing — an export, an import, a batch.
It is the wrong shape when the work **decorates a record the interface is
already showing**: a document that gets transcribed, then summarized, then
mined for suggestions. There the screen is already fetching the document,
and a second table means a second query and a join to render one page.

The alternative is status columns on the record: ``status_summary``,
``error_summary``, and so on, one triple per stage. That works, and it rots
in a specific way — each stage grows its own copy of "set running", "mark
failed", "is it still mine", and a fix has to be applied N times, with N
chances to miss one. Adding a stage means touching every one of those
places.

:class:`StageMap` is that table written once. You declare the stages and the
column-name convention; it resolves the names and performs the transitions.

    STAGES = StageMap(
        ["transcription", "summary", "suggestions"],
        prefix="doc_",
    )

    STAGES.mark(document, "summary", StageStatus.RUNNING)
    if STAGES.owns(document, "summary", StageStatus.RUNNING):
        document.doc_result_summary = text
        STAGES.mark(document, "summary", StageStatus.DONE)

**It declares no columns.** The project writes its own ``mapped_column``
declarations, so migrations, types and indexes stay where a reader expects
them; this only agrees with them on the naming. Nothing here touches a
database either — every function is a pure operation on an object, so the
transitions are testable without one.

Ownership, not cancellation, is the check that matters before writing a
result. "This stage is no longer mine" covers being cancelled *and* being
restarted by a newer run: in both cases the old run must not write, because
one would resurrect work the user stopped and the other would clobber the
newer result. That is :meth:`StageMap.owns`.
"""

from __future__ import annotations

from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from enum import StrEnum
from typing import Any


class StageStatus(StrEnum):
    """Where one stage of a pipeline is.

    * ``PENDING`` — queued, nobody has started it.
    * ``RUNNING`` — a worker is on it.
    * ``DONE`` — finished; the result column is filled.
    * ``FAILED`` — stopped; the error column says why.
    * ``CANCELLED`` — the user asked it to stop. Terminal like the two
      above, and like them it is not re-entered on its own — but it is not
      a failure, so an interface highlighting ``FAILED`` should leave it
      alone.
    """

    PENDING = "pending"
    RUNNING = "running"
    DONE = "done"
    FAILED = "failed"
    CANCELLED = "cancelled"


RUNNING_STAGE_STATUSES: frozenset[StageStatus] = frozenset(
    {StageStatus.PENDING, StageStatus.RUNNING},
)
"""Statuses with work left to stop — what :meth:`StageMap.cancel` accepts.

``PENDING`` counts: the message is on the queue and the worker will pick it
up, so there is something to stop even though nothing is running yet.
"""

TERMINAL_STAGE_STATUSES: frozenset[StageStatus] = frozenset(
    {StageStatus.DONE, StageStatus.FAILED, StageStatus.CANCELLED},
)
"""Statuses a stage does not leave on its own; only an explicit restart."""


@dataclass(frozen=True)
class StageColumns:
    """The resolved column names of one stage on one record.

    Attributes:
        name (str): The stage's name.
        status (str): Attribute holding the :class:`StageStatus`.
        error (str): Attribute holding the failure message.
        result (str): Attribute holding what the stage produced.
    """

    name: str
    status: str
    error: str
    result: str


class StageMap:
    """Declares a record's stages and performs their transitions.

    Attributes:
        prefix (str): Prepended to every resolved column name.
    """

    def __init__(
        self,
        stages: Sequence[str],
        *,
        prefix: str = "",
        status_template: str = "status_{stage}",
        error_template: str = "error_{stage}",
        result_template: str = "result_{stage}",
    ) -> None:
        """Declare the stages and how their columns are named.

        The templates exist because column naming is a house convention,
        not something a library gets to impose — a codebase spelling them
        ``summary_status`` should not have to rename its schema to use
        this.

        Args:
            stages (Sequence[str]): Stage names, in the order the
                interface should show them.
            prefix (str): Prepended to every column name, for schemas that
                namespace a record's columns (``doc_``, ``meeting_``).
            status_template (str): Column name for a stage's status, with
                ``{stage}`` substituted.
            error_template (str): Column name for a stage's error.
            result_template (str): Column name for a stage's result.

        Raises:
            ValueError: When ``stages`` is empty, or holds a duplicate, or
                when two stages would resolve to the same column name.
        """
        if not stages:
            raise ValueError("declare at least one stage")
        if len(set(stages)) != len(stages):
            raise ValueError("stage names must be unique")

        self.prefix = prefix
        self._order: tuple[str, ...] = tuple(stages)
        self._columns: dict[str, StageColumns] = {
            stage: StageColumns(
                name=stage,
                status=f"{prefix}{status_template.format(stage=stage)}",
                error=f"{prefix}{error_template.format(stage=stage)}",
                result=f"{prefix}{result_template.format(stage=stage)}",
            )
            for stage in stages
        }

        resolved = [
            name
            for columns in self._columns.values()
            for name in (columns.status, columns.error, columns.result)
        ]
        if len(set(resolved)) != len(resolved):
            raise ValueError(
                "two stages resolve to the same column name; check the templates",
            )

    @property
    def names(self) -> tuple[str, ...]:
        """The declared stage names, in declaration order.

        Returns:
            tuple[str, ...]: The names.
        """
        return self._order

    def columns(self, stage: str) -> StageColumns:
        """Resolve one stage's column names.

        Args:
            stage (str): The stage name.

        Returns:
            StageColumns: The resolved names.

        Raises:
            KeyError: When the stage was not declared.
        """
        try:
            return self._columns[stage]
        except KeyError:
            raise KeyError(
                f"unknown stage {stage!r}; declared: {', '.join(self._order)}",
            ) from None

    def status(self, record: Any, stage: str) -> StageStatus | None:
        """Read a stage's status off a record.

        Args:
            record (Any): The row.
            stage (str): The stage name.

        Returns:
            StageStatus | None: The status, or ``None`` when the column is
            empty — which means the stage was never requested, a state
            worth distinguishing from ``PENDING``.

        Raises:
            KeyError: When the stage was not declared.
        """
        raw = getattr(record, self.columns(stage).status, None)
        return StageStatus(raw) if raw is not None else None

    def is_running(self, record: Any, stage: str) -> bool:
        """Does this stage still have work to stop?

        Args:
            record (Any): The row.
            stage (str): The stage name.

        Returns:
            bool: ``True`` when the status is ``PENDING`` or ``RUNNING``.

        Raises:
            KeyError: When the stage was not declared.
        """
        return self.status(record, stage) in RUNNING_STAGE_STATUSES

    def owns(self, record: Any, stage: str, expected: StageStatus) -> bool:
        """Does the run that set ``expected`` still own this stage?

        The check to make **before writing a result**, and it is about
        ownership rather than cancellation on purpose: a status that is no
        longer ``expected`` covers both the user cancelling and a newer run
        having restarted the stage. Writing in either case is wrong — one
        resurrects stopped work, the other clobbers a fresher result.

        Args:
            record (Any): A **freshly read** row. Re-read it before calling
                this; an object loaded before the long work started still
                holds the old status and would answer ``True`` no matter
                what happened meanwhile.
            stage (str): The stage name.
            expected (StageStatus): What this run left the stage at.

        Returns:
            bool: ``True`` when the stage is still at ``expected``.

        Raises:
            KeyError: When the stage was not declared.
        """
        return self.status(record, stage) == expected

    def mark(
        self,
        record: Any,
        stage: str,
        status: StageStatus,
        *,
        error: str | None = None,
        result: Any = None,
    ) -> None:
        """Move a stage, writing the error and result columns with it.

        Mutates ``record`` in place and does not persist it — the caller
        owns the session and decides when to flush, which is what lets
        several stage writes share one transaction.

        The error column is cleared on any status other than ``FAILED``, so
        a restarted stage stops showing the message from the run before it.

        Args:
            record (Any): The row.
            stage (str): The stage name.
            status (StageStatus): The new status.
            error (str | None): The failure message; only meaningful with
                ``FAILED``.
            result (Any): What the stage produced. ``None`` leaves the
                result column untouched, so marking ``DONE`` without a
                value does not erase one written earlier.

        Raises:
            KeyError: When the stage was not declared.
            ValueError: When ``error`` is given with a status other than
                ``FAILED`` — that pairing means the caller mixed up two
                transitions, and silently dropping the message would hide
                it.
        """
        if error is not None and status is not StageStatus.FAILED:
            raise ValueError(
                f"error= is only meaningful with FAILED, got {status.value}",
            )
        columns = self.columns(stage)
        setattr(record, columns.status, status.value)
        setattr(record, columns.error, error if status is StageStatus.FAILED else None)
        if result is not None:
            setattr(record, columns.result, result)

    def cancel(
        self,
        record: Any,
        stages: Iterable[str] | None = None,
    ) -> tuple[list[str], list[str]]:
        """Cancel every requested stage that still has work to stop.

        Idempotent, and deliberately partial: a stage that already finished
        is reported as ignored rather than raising. A screen polling for
        status will routinely ask to cancel something that completed a
        moment ago, and that is not an error.

        There is no cascade, and none is needed when each stage only
        enqueues the next one on success: cancelling the first means the
        second is never created.

        Args:
            record (Any): The row.
            stages (Iterable[str] | None): Which to cancel; ``None``
                cancels every declared stage that is running.

        Returns:
            tuple[list[str], list[str]]: The stages cancelled, and those
            ignored, each in the order requested. Duplicates in the request
            are collapsed, so a caller cannot get the same stage twice.

        Raises:
            KeyError: When a requested stage was not declared.
        """
        requested = list(dict.fromkeys(stages if stages is not None else self._order))
        cancelled: list[str] = []
        ignored: list[str] = []
        for stage in requested:
            if self.is_running(record, stage):
                self.mark(record, stage, StageStatus.CANCELLED)
                cancelled.append(stage)
            else:
                ignored.append(stage)
        return cancelled, ignored

    def snapshot(self, record: Any) -> dict[str, StageStatus | None]:
        """Read every stage's status at once.

        What a status endpoint serializes, and what a test asserts on
        without naming a column.

        Args:
            record (Any): The row.

        Returns:
            dict[str, StageStatus | None]: Status per stage, in declaration
            order.
        """
        return {stage: self.status(record, stage) for stage in self._order}


__all__: list[str] = [
    "RUNNING_STAGE_STATUSES",
    "TERMINAL_STAGE_STATUSES",
    "StageColumns",
    "StageMap",
    "StageStatus",
]
