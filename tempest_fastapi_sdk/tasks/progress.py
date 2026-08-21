"""A percentage for work that has no percentage of its own.

:class:`~tempest_fastapi_sdk.tasks.JobStore` can carry a number
(:meth:`~tempest_fastapi_sdk.tasks.JobStore.report_progress`); what it
cannot do is invent one. Long work is usually a handful of steps of very
different weight — read a PDF, call a model, call it again, save — and
the step in the middle is both the longest and the one that reports
nothing while it runs.

The two dishonest ways out are well known. A bar that crawls on a timer
tells the user a story unrelated to the work. A bar that jumps 0 → 100
when the work ends is a spinner wearing a percentage.

This module is the third way: the caller **measures** its phases once,
declares them, and the tracker interpolates inside the phase that is
running while pinning the boundaries to real events.

    PLAN = PhasePlan(
        [
            Phase("pdf", weight=0.3, seconds=0.5),
            Phase("table", weight=30.0, seconds=18.0, seconds_per_kilochar=0.3),
            Phase("reading", weight=20.0, seconds=20.0, seconds_per_kilochar=0.2),
        ],
    )

    tracker = ProgressTracker(store, job.id, plan=PLAN)
    text = await tracker.run("pdf", read_pdf(payload))
    table = await tracker.run("table", model.read(text), size=len(text))

Three properties are deliberate:

* **Weights are relative, not percentages.** Pass the measured medians
  straight in and they normalise themselves, so re-measuring is editing
  numbers rather than recomputing shares that must total one.
* **A phase never fills.** The interpolation stops at
  ``ceiling_margin`` of its span, so "the table call is done" is
  something only the table call finishing can say.
* **One poll answers both questions.** The tick that writes progress is
  the same tick :func:`~tempest_fastapi_sdk.tasks.run_cancellable` uses
  to ask whether the user cancelled — they are the same question about
  the same row, at the same interval, so asking twice would double the
  traffic to say the same thing.

Where the numbers come from is the caller's problem, and it should be
measurement rather than intuition: run the real work over real inputs,
take the median per phase, and — when duration tracks input size, which
for a model call it does — fit the slope and pass
``seconds_per_kilochar`` too. A plan built from guesses produces a bar
that is precise about nothing.
"""

from __future__ import annotations

from dataclasses import dataclass
from time import monotonic
from typing import TYPE_CHECKING, Protocol, TypeVar
from uuid import UUID

from tempest_fastapi_sdk.tasks.cancellation import (
    DEFAULT_POLL_SECONDS,
    run_cancellable,
)

if TYPE_CHECKING:
    import threading
    from collections.abc import Awaitable, Callable, Mapping, Sequence

ResultT = TypeVar("ResultT")


@dataclass(frozen=True)
class Phase:
    """One measured step of a longer job.

    Attributes:
        name (str): What the step is called, as the interface will show
            it and as the row will record it.
        weight (float): How much of the whole job this step accounts
            for, relative to the other phases. Measured seconds are the
            natural value; they are normalised, so any scale works.
        seconds (float): How long the step usually takes — the median of
            real runs. ``0.0`` means unmeasured, and a phase with no
            expected duration does not interpolate: its bar sits at the
            phase floor until the phase ends.
        seconds_per_kilochar (float): How much each thousand characters
            of input adds, when the caller passes ``size``. Zero means
            duration does not track input size, or was not fitted.
    """

    name: str
    weight: float
    seconds: float = 0.0
    seconds_per_kilochar: float = 0.0


class ProgressSink(Protocol):
    """The two things a tracker needs from a job store.

    Narrow on purpose: a tracker asks the row how far along it is
    allowed to say the work is, and whether the user stopped it. Anything
    that answers those two questions can be tracked, which is what keeps
    the tests here free of a database.
    """

    async def report_progress(
        self,
        job_id: UUID,
        *,
        progress: float,
        stage: str | None = None,
    ) -> bool:
        """Move the recorded progress forward.

        Args:
            job_id (UUID): The job to advance.
            progress (float): Completed fraction.
            stage (str | None): Which part of the work is running.

        Returns:
            bool: Whether the row moved.
        """
        ...

    async def is_cancelled(self, job_id: UUID) -> bool:
        """Answer whether the user stopped this job.

        Args:
            job_id (UUID): The job to check.

        Returns:
            bool: ``True`` when the job is cancelled or gone.
        """
        ...


class PhasePlan:
    """Measured phases, turned into the fraction to write on the row.

    Attributes:
        ceiling_margin (float): How far into a phase's span the
            interpolation is allowed to climb before the phase actually
            ends.
    """

    def __init__(
        self,
        phases: Sequence[Phase],
        *,
        ceiling_margin: float = 0.95,
    ) -> None:
        """Build a plan from measured phases.

        Args:
            phases (Sequence[Phase]): The steps, in the order they run.
            ceiling_margin (float): Fraction of a phase's span the
                interpolation may reach. Defaults to ``0.95``, so a phase
                that is taking longer than usual stops just short of
                claiming to be finished.

        Raises:
            ValueError: When there are no phases, a name repeats, a
                weight is not positive, or ``ceiling_margin`` is outside
                ``(0, 1]``.
        """
        if not phases:
            raise ValueError("a plan needs at least one phase")
        names = [phase.name for phase in phases]
        if len(set(names)) != len(names):
            raise ValueError("phase names must be unique")
        if any(phase.weight <= 0 for phase in phases):
            raise ValueError("every phase weight must be positive")
        if not 0.0 < ceiling_margin <= 1.0:
            raise ValueError("ceiling_margin must be within (0, 1]")
        self.ceiling_margin: float = ceiling_margin
        self._phases: tuple[Phase, ...] = tuple(phases)
        total = sum(phase.weight for phase in phases)
        self._bounds: dict[str, tuple[float, float]] = {}
        floor = 0.0
        for phase in phases:
            ceiling = floor + phase.weight / total
            self._bounds[phase.name] = (floor, ceiling)
            floor = ceiling

    @classmethod
    def from_seconds(
        cls,
        medians: Mapping[str, float],
        *,
        per_kilochar: Mapping[str, float] | None = None,
        ceiling_margin: float = 0.95,
    ) -> PhasePlan:
        """Build a plan straight out of a measurement run.

        The median duration is both the weight and the expected duration,
        which is what a measurement gives you without further arithmetic.

        Args:
            medians (Mapping[str, float]): Median seconds per phase, in
                the order the phases run.
            per_kilochar (Mapping[str, float] | None): Fitted seconds per
                thousand characters, for the phases that have one.
            ceiling_margin (float): Passed through to the constructor.

        Returns:
            PhasePlan: The plan.

        Raises:
            ValueError: When a median is not positive — a phase that
                takes no time is not a phase to show.
        """
        slopes = dict(per_kilochar or {})
        return cls(
            [
                Phase(
                    name,
                    weight=seconds,
                    seconds=seconds,
                    seconds_per_kilochar=slopes.get(name, 0.0),
                )
                for name, seconds in medians.items()
            ],
            ceiling_margin=ceiling_margin,
        )

    @property
    def names(self) -> tuple[str, ...]:
        """The phase names, in order.

        Returns:
            tuple[str, ...]: The names this plan knows.
        """
        return tuple(phase.name for phase in self._phases)

    def bounds(self, phase: str) -> tuple[float, float]:
        """Where a phase starts and ends on the bar.

        Args:
            phase (str): The phase name.

        Returns:
            tuple[float, float]: Its floor and ceiling, both in
            ``[0, 1]``.

        Raises:
            KeyError: When the plan has no such phase.
        """
        return self._bounds[phase]

    def expected(self, phase: str, *, size: float | None = None) -> float:
        """How long this phase should take, given its input.

        Args:
            phase (str): The phase name.
            size (float | None): Input size in characters, when the phase
                was fitted against it.

        Returns:
            float: Expected seconds, ``0.0`` when the phase was never
            measured.

        Raises:
            KeyError: When the plan has no such phase.
        """
        measured = self._phase(phase)
        if size is None or measured.seconds_per_kilochar <= 0:
            return measured.seconds
        return measured.seconds + measured.seconds_per_kilochar * size / 1000

    def fraction(
        self,
        phase: str,
        *,
        elapsed: float = 0.0,
        size: float | None = None,
        done: float | None = None,
    ) -> float:
        """The number to write on the row right now.

        Args:
            phase (str): The phase that is running.
            elapsed (float): Seconds since the phase started.
            size (float | None): Input size in characters, when known.
            done (float | None): A real fraction of this phase, from a
                counter the work actually has — pages read out of pages
                total. Given, it wins: a measured count beats an
                interpolation, and it is allowed to reach the phase
                ceiling because it is not a guess.

        Returns:
            float: The completed fraction of the whole job, in
            ``[0, 1]``.

        Raises:
            KeyError: When the plan has no such phase.
        """
        floor, ceiling = self.bounds(phase)
        span = ceiling - floor
        if done is not None:
            return floor + span * min(max(done, 0.0), 1.0)
        expected = self.expected(phase, size=size)
        if expected <= 0 or elapsed <= 0:
            return floor
        share = min(elapsed / expected, 1.0) * self.ceiling_margin
        return floor + span * share

    def _phase(self, name: str) -> Phase:
        """Find a phase by name.

        Args:
            name (str): The phase name.

        Returns:
            Phase: The declared phase.

        Raises:
            KeyError: When the plan has no such phase.
        """
        for phase in self._phases:
            if phase.name == name:
                return phase
        raise KeyError(name)


class ProgressTracker:
    """Runs a job's phases, writing where each one has got to.

    Attributes:
        plan (PhasePlan): The measured phases being followed.
        interval (float): Seconds between ticks — each tick writes the
            interpolated progress and asks whether the job was cancelled.
    """

    def __init__(
        self,
        sink: ProgressSink,
        job_id: UUID,
        *,
        plan: PhasePlan,
        interval: float = DEFAULT_POLL_SECONDS,
        clock: Callable[[], float] = monotonic,
    ) -> None:
        """Bind a tracker to one job.

        Args:
            sink (ProgressSink): Where progress is written and
                cancellation is read — normally a
                :class:`~tempest_fastapi_sdk.tasks.JobStore`.
            job_id (UUID): The job being tracked.
            plan (PhasePlan): The measured phases.
            interval (float): Seconds between ticks.
            clock (Callable[[], float]): Monotonic seconds source;
                injectable so a test does not have to wait.
        """
        self.plan: PhasePlan = plan
        self.interval: float = interval
        self._sink: ProgressSink = sink
        self._job_id: UUID = job_id
        self._clock: Callable[[], float] = clock

    async def enter(self, phase: str) -> None:
        """Announce a phase without running anything in it.

        Args:
            phase (str): The phase starting now.
        """
        floor, _ceiling = self.plan.bounds(phase)
        await self._sink.report_progress(
            self._job_id,
            progress=floor,
            stage=phase,
        )

    async def report(self, phase: str, *, done: float) -> None:
        """Write a phase's own count of how far it has got.

        For work that can actually count — pages extracted, rows written
        — this beats interpolation and is what the bar should show.

        Args:
            phase (str): The phase that is running.
            done (float): Its completed fraction, ``0.0`` to ``1.0``.
        """
        await self._sink.report_progress(
            self._job_id,
            progress=self.plan.fraction(phase, done=done),
            stage=phase,
        )

    async def run(
        self,
        phase: str,
        work: Awaitable[ResultT],
        *,
        size: float | None = None,
        stop_event: threading.Event | None = None,
    ) -> ResultT:
        """Run one phase, ticking the bar and honouring cancellation.

        Args:
            phase (str): The phase this work is.
            work (Awaitable[ResultT]): The work itself. It must be
                genuinely cancellable — async I/O, not a thread wrapper,
                for the reason
                :func:`~tempest_fastapi_sdk.tasks.run_cancellable`
                explains.
            size (float | None): Input size in characters, when the phase
                was fitted against it. With it, a call over 40.000
                characters is not paced like one over 4.000.
            stop_event (threading.Event | None): Forwarded to
                :func:`~tempest_fastapi_sdk.tasks.run_cancellable`, for
                work that runs in a thread and watches an event — a local
                model decoding, which no coroutine cancellation reaches.

        Returns:
            ResultT: Whatever ``work`` returned.

        Raises:
            StageInterruptedError: The job was cancelled while this phase
                was running; ``work`` is already cancelled when this
                propagates.
            KeyError: When the plan has no such phase.
            Exception: Anything ``work`` itself raises, unchanged.
        """
        await self.enter(phase)
        started = self._clock()

        async def tick() -> bool:
            """Write where the phase has got to, and ask if it may stop.

            Returns:
                bool: ``True`` when the job has been cancelled.
            """
            await self._sink.report_progress(
                self._job_id,
                progress=self.plan.fraction(
                    phase,
                    elapsed=self._clock() - started,
                    size=size,
                ),
                stage=phase,
            )
            return await self._sink.is_cancelled(self._job_id)

        return await run_cancellable(
            work,
            interrupted=tick,
            poll_seconds=self.interval,
            stop_event=stop_event,
        )


__all__: list[str] = [
    "Phase",
    "PhasePlan",
    "ProgressSink",
    "ProgressTracker",
]
