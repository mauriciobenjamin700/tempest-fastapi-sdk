"""Stage transitions on a record, and the mistakes the map refuses to make.

Written by hand N times, a per-stage status triple rots in a specific way:
each stage grows its own copy of "set running" and "mark failed", a fix has
to land N times, and a copy-pasted stage that kept a neighbour's column name
compiles, imports, and quietly reports the neighbour's state.

So most of what follows is about refusals — a duplicate stage, a template
that collides, an error message paired with a status that is not a failure —
plus the ownership check that decides whether a finished run is allowed to
write at all.

No database: every operation is a pure mutation of an object, which is why
these run without one.
"""

from __future__ import annotations

import pytest

from tempest_fastapi_sdk.tasks.stages import (
    RUNNING_STAGE_STATUSES,
    TERMINAL_STAGE_STATUSES,
    StageMap,
    StageStatus,
)


class _Record:
    """A stand-in for the ORM row a real project would declare.

    Attributes are created dynamically so the test does not have to spell
    fifteen of them out; a real model declares them as ``mapped_column``,
    which is exactly what the map does *not* do for you.
    """

    def __init__(self, stages: StageMap) -> None:
        """Start every declared column at ``None``.

        Args:
            stages (StageMap): The map whose columns to create.
        """
        for name in stages.names:
            columns = stages.columns(name)
            setattr(self, columns.status, None)
            setattr(self, columns.error, None)
            setattr(self, columns.result, None)


@pytest.fixture
def stages() -> StageMap:
    """A three-stage map with a prefix.

    Returns:
        StageMap: The map under test.
    """
    return StageMap(["transcription", "summary", "tasks"], prefix="doc_")


@pytest.fixture
def record(stages: StageMap) -> _Record:
    """A record with every column present and empty.

    Args:
        stages (StageMap): The map whose columns to create.

    Returns:
        _Record: The row under test.
    """
    return _Record(stages)


class TestDeclaration:
    """What the map refuses to be built from."""

    def test_columns_are_resolved_with_the_prefix(self, stages: StageMap) -> None:
        """The naming convention is the whole contract with the schema."""
        columns = stages.columns("summary")

        assert columns.status == "doc_status_summary"
        assert columns.error == "doc_error_summary"
        assert columns.result == "doc_result_summary"

    def test_templates_are_configurable(self) -> None:
        """A codebase spelling them the other way round should not rename."""
        stages = StageMap(
            ["summary"],
            status_template="{stage}_status",
            error_template="{stage}_error",
            result_template="{stage}_result",
        )

        assert stages.columns("summary").status == "summary_status"

    def test_no_stages_is_refused(self) -> None:
        """An empty map would silently do nothing forever."""
        with pytest.raises(ValueError, match="at least one stage"):
            StageMap([])

    def test_duplicate_stage_is_refused(self) -> None:
        """The second declaration would shadow the first, invisibly."""
        with pytest.raises(ValueError, match="unique"):
            StageMap(["summary", "summary"])

    def test_colliding_templates_are_refused(self) -> None:
        """Two stages sharing a column is the copy-paste bug, caught early.

        With a constant template every stage resolves to the same column,
        so both would read and write each other's state — and nothing else
        in the stack would notice.
        """
        with pytest.raises(ValueError, match="same column name"):
            StageMap(["a", "b"], status_template="status")

    def test_unknown_stage_names_the_declared_ones(self, stages: StageMap) -> None:
        """A typo should say what was available, not just fail."""
        with pytest.raises(KeyError, match="transcription"):
            stages.columns("sumary")


class TestTransitions:
    """Moving a stage."""

    def test_an_untouched_stage_has_no_status(
        self, stages: StageMap, record: _Record
    ) -> None:
        """``None`` means never requested — not the same as pending."""
        assert stages.status(record, "summary") is None
        assert stages.is_running(record, "summary") is False

    def test_mark_writes_the_status(self, stages: StageMap, record: _Record) -> None:
        """The value written is the enum's string, for the column."""
        stages.mark(record, "summary", StageStatus.RUNNING)

        assert record.doc_status_summary == "running"
        assert stages.status(record, "summary") is StageStatus.RUNNING

    def test_pending_counts_as_running(self, stages: StageMap, record: _Record) -> None:
        """The message is on the queue, so there is something to stop."""
        stages.mark(record, "summary", StageStatus.PENDING)

        assert stages.is_running(record, "summary") is True

    def test_failing_stores_the_reason(self, stages: StageMap, record: _Record) -> None:
        """The screen needs to say why."""
        stages.mark(record, "summary", StageStatus.FAILED, error="model refused")

        assert record.doc_error_summary == "model refused"

    def test_restarting_clears_the_previous_error(
        self, stages: StageMap, record: _Record
    ) -> None:
        """A retried stage must stop showing the last run's message."""
        stages.mark(record, "summary", StageStatus.FAILED, error="model refused")

        stages.mark(record, "summary", StageStatus.PENDING)

        assert record.doc_error_summary is None

    def test_error_with_a_non_failure_status_is_refused(
        self, stages: StageMap, record: _Record
    ) -> None:
        """That pairing means two transitions got mixed up.

        Dropping the message silently would hide the mistake.
        """
        with pytest.raises(ValueError, match="FAILED"):
            stages.mark(record, "summary", StageStatus.DONE, error="oops")

    def test_result_is_written_when_given(
        self, stages: StageMap, record: _Record
    ) -> None:
        """The produced value lands in the stage's own column."""
        stages.mark(record, "summary", StageStatus.DONE, result="the summary")

        assert record.doc_result_summary == "the summary"

    def test_marking_without_a_result_keeps_the_old_one(
        self, stages: StageMap, record: _Record
    ) -> None:
        """Cancelling a regeneration must not erase what is already there.

        The previous summary is still the best answer available; wiping it
        would make cancelling strictly worse than never asking.
        """
        stages.mark(record, "summary", StageStatus.DONE, result="the summary")

        stages.mark(record, "summary", StageStatus.PENDING)

        assert record.doc_result_summary == "the summary"

    def test_stages_do_not_touch_each_other(
        self, stages: StageMap, record: _Record
    ) -> None:
        """Independence is the property the whole map exists to keep."""
        stages.mark(record, "summary", StageStatus.FAILED, error="boom")

        assert stages.status(record, "transcription") is None
        assert record.doc_error_transcription is None


class TestOwnership:
    """Whether a finished run is allowed to write."""

    def test_a_run_owns_the_stage_it_left_running(
        self, stages: StageMap, record: _Record
    ) -> None:
        """The ordinary path: nothing happened meanwhile."""
        stages.mark(record, "summary", StageStatus.RUNNING)

        assert stages.owns(record, "summary", StageStatus.RUNNING) is True

    def test_a_cancelled_stage_is_no_longer_owned(
        self, stages: StageMap, record: _Record
    ) -> None:
        """The user stopped it; the old run must not resurrect the work."""
        stages.mark(record, "summary", StageStatus.RUNNING)
        stages.cancel(record, ["summary"])

        assert stages.owns(record, "summary", StageStatus.RUNNING) is False

    def test_a_restarted_stage_is_no_longer_owned(
        self, stages: StageMap, record: _Record
    ) -> None:
        """A newer run took over; the old one must not clobber its result.

        This is why the check is about ownership rather than cancellation:
        both cases must stop the write, and only one of them is a cancel.
        """
        stages.mark(record, "summary", StageStatus.RUNNING)
        stages.mark(record, "summary", StageStatus.PENDING)

        assert stages.owns(record, "summary", StageStatus.RUNNING) is False


class TestCancel:
    """Stopping what still has work left."""

    def test_cancels_only_what_is_running(
        self, stages: StageMap, record: _Record
    ) -> None:
        """Finished stages are reported as ignored, not raised over."""
        stages.mark(record, "transcription", StageStatus.DONE)
        stages.mark(record, "summary", StageStatus.RUNNING)
        stages.mark(record, "tasks", StageStatus.PENDING)

        cancelled, ignored = stages.cancel(record)

        assert cancelled == ["summary", "tasks"]
        assert ignored == ["transcription"]

    def test_cancel_is_idempotent(self, stages: StageMap, record: _Record) -> None:
        """A double-click is not an error."""
        stages.mark(record, "summary", StageStatus.RUNNING)
        stages.cancel(record, ["summary"])

        cancelled, ignored = stages.cancel(record, ["summary"])

        assert cancelled == []
        assert ignored == ["summary"]

    def test_cancel_clears_the_previous_error(
        self, stages: StageMap, record: _Record
    ) -> None:
        """A cancelled stage is not showing why it failed last time."""
        stages.mark(record, "summary", StageStatus.FAILED, error="boom")
        stages.mark(record, "summary", StageStatus.PENDING)

        stages.cancel(record, ["summary"])

        assert record.doc_error_summary is None

    def test_the_request_order_is_preserved(
        self, stages: StageMap, record: _Record
    ) -> None:
        """The response should be predictable for the caller."""
        stages.mark(record, "summary", StageStatus.RUNNING)
        stages.mark(record, "tasks", StageStatus.RUNNING)

        cancelled, _ignored = stages.cancel(record, ["tasks", "summary"])

        assert cancelled == ["tasks", "summary"]

    def test_duplicates_are_collapsed(self, stages: StageMap, record: _Record) -> None:
        """A caller must not get the same stage reported twice."""
        stages.mark(record, "summary", StageStatus.RUNNING)

        cancelled, ignored = stages.cancel(record, ["summary", "summary"])

        assert cancelled == ["summary"]
        assert ignored == []

    def test_an_unknown_stage_raises(self, stages: StageMap, record: _Record) -> None:
        """Cancelling a stage that does not exist is a programming error."""
        with pytest.raises(KeyError):
            stages.cancel(record, ["nope"])


class TestSnapshot:
    """Reading everything at once."""

    def test_snapshot_covers_every_stage_in_order(
        self, stages: StageMap, record: _Record
    ) -> None:
        """What a status endpoint serializes."""
        stages.mark(record, "transcription", StageStatus.DONE)
        stages.mark(record, "summary", StageStatus.RUNNING)

        assert stages.snapshot(record) == {
            "transcription": StageStatus.DONE,
            "summary": StageStatus.RUNNING,
            "tasks": None,
        }


class TestStatusSets:
    """The two sets callers branch on."""

    def test_the_sets_do_not_overlap(self) -> None:
        """A status is either stoppable or terminal, never both."""
        assert not RUNNING_STAGE_STATUSES & TERMINAL_STAGE_STATUSES

    def test_every_status_is_in_one_of_them(self) -> None:
        """A new status must be classified, not silently unhandled."""
        assert set(StageStatus) == RUNNING_STAGE_STATUSES | TERMINAL_STAGE_STATUSES

    def test_cancelled_is_terminal_but_is_not_a_failure(self) -> None:
        """An alert that pages on failures must not fire on a cancel."""
        assert StageStatus.CANCELLED in TERMINAL_STAGE_STATUSES
        assert StageStatus.CANCELLED is not StageStatus.FAILED
