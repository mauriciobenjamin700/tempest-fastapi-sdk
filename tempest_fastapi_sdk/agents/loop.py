"""Running an agent more than once, on purpose.

A single `Agent.run` stops when the model says it is done. That is the
right default and it is often not good enough: the model is a poor judge
of its own output, and "done" frequently means "out of ideas". These two
loops keep going.

* :func:`run_until` repeats the goal until a **predicate you wrote**
  accepts the answer. The predicate is ordinary Python — a regex, a schema
  parse, a compile step, a call to your own service — which makes it a far
  harder gate than asking the model whether it is satisfied.
* :func:`refine` runs the generate-critique-revise pattern with **two**
  agents. A second agent reading the first one's output catches what the
  author cannot, for the same reason a human reviewer does.

Both accumulate every round in a :class:`LoopResult`, so what you get back
is not just the last answer but the path to it — the earlier attempts and
each critique are usually where you learn that a prompt is wrong.

Costs add up multiplicatively here: `rounds` runs of an agent whose own
budget allows N steps is `rounds * N` model calls. That is the point of
these loops, and it is why every one of them takes a hard ceiling.
"""

from __future__ import annotations

import time
from collections.abc import Awaitable, Callable
from typing import TYPE_CHECKING

from pydantic import Field

from tempest_fastapi_sdk.agents.schemas import AgentArtifact, AgentRun, StopReason
from tempest_fastapi_sdk.agents.tools import AgentContext
from tempest_fastapi_sdk.schemas.base import BaseSchema

if TYPE_CHECKING:
    from tempest_fastapi_sdk.agents.agent import Agent

#: Decides whether an answer is good enough. Sync or async.
Verdict = Callable[[AgentRun], bool | Awaitable[bool]]


class LoopIteration(BaseSchema):
    """One round of a loop.

    Attributes:
        index (int): Round number, from ``0``.
        run (AgentRun): What the agent produced this round.
        accepted (bool): Whether the predicate (or critic) accepted it.
        critique (str | None): The critic's notes, for :func:`refine`.
    """

    index: int = Field(
        title="Index",
        description="Round number.",
        examples=[0],
    )
    run: AgentRun = Field(
        title="Run",
        description="What the agent produced this round.",
    )
    accepted: bool = Field(
        default=False,
        title="Accepted",
        description="Whether this round's answer was accepted.",
        examples=[True],
    )
    critique: str | None = Field(
        default=None,
        title="Critique",
        description="The critic's notes, when a critic ran.",
    )


class LoopResult(BaseSchema):
    """Everything a loop produced, not just its last answer.

    Attributes:
        goal (str): The original goal.
        output (str): The accepted answer, or the last one attempted.
        iterations (list[LoopIteration]): Every round, in order.
        accepted (bool): Whether any round was accepted. ``False`` means
            the loop ran out of rounds or time — the output is the best
            attempt, not an approved one.
        seconds (float): Total wall-clock across every round.
    """

    goal: str = Field(
        title="Goal",
        description="The original goal.",
    )
    output: str = Field(
        default="",
        title="Output",
        description="The accepted answer, or the last attempt.",
    )
    iterations: list[LoopIteration] = Field(
        default_factory=list,
        title="Iterations",
        description="Every round, in order.",
    )
    accepted: bool = Field(
        default=False,
        title="Accepted",
        description="Whether any round was accepted.",
        examples=[True],
    )
    seconds: float = Field(
        default=0.0,
        title="Seconds",
        description="Total wall-clock across every round.",
        examples=[42.5],
    )

    @property
    def rounds(self) -> int:
        """Return how many rounds ran.

        Returns:
            int: The iteration count.
        """
        return len(self.iterations)

    @property
    def final_run(self) -> AgentRun | None:
        """Return the last round's run.

        Returns:
            AgentRun | None: The final run, or ``None`` when no round ran
            (which happens only when the deadline was already past).
        """
        return self.iterations[-1].run if self.iterations else None

    @property
    def artifacts(self) -> list[AgentArtifact]:
        """Return the accepted round's artifacts, else the last round's.

        Returns:
            list[AgentArtifact]: The artifacts a caller should ship. Earlier rounds'
            artifacts are reachable through :attr:`iterations`, but
            returning all of them here would hand back rejected drafts
            alongside the approved one.
        """
        for iteration in reversed(self.iterations):
            if iteration.accepted:
                return list(iteration.run.artifacts)
        final = self.final_run
        return list(final.artifacts) if final is not None else []


async def _accepts(verdict: Verdict, run: AgentRun) -> bool:
    """Call a predicate that may be sync or async.

    Args:
        verdict (Verdict): The caller's predicate.
        run (AgentRun): The run to judge.

    Returns:
        bool: Whether the run is acceptable.
    """
    result = verdict(run)
    if isinstance(result, bool):
        return result
    return bool(await result)


async def run_until(
    agent: Agent,
    goal: str,
    *,
    until: Verdict,
    max_rounds: int = 3,
    max_seconds: float | None = None,
    feedback: Callable[[AgentRun, int], str] | None = None,
    context: AgentContext | None = None,
) -> LoopResult:
    """Run an agent repeatedly until an answer passes ``until``.

    Example:

        >>> def compiles(run: AgentRun) -> bool:
        ...     return "def " in run.output and run.succeeded
        >>> result = await run_until(agent, "Write a factorial function.",
        ...                          until=compiles, max_rounds=4)
        >>> result.accepted, result.rounds

    The predicate is where the value is: a check that actually runs the
    output — parses the JSON, imports the module, hits the endpoint — is a
    far harder gate than asking the model whether it is happy, and it is
    the reason this loop can improve on a single run at all.

    Args:
        agent (Agent): The agent to run.
        goal (str): What to accomplish.
        until (Verdict): Returns ``True`` when an answer is good enough.
            Sync or async.
        max_rounds (int): Hard ceiling on attempts. Reached without
            acceptance, the loop returns the last attempt with
            ``accepted=False``.
        max_seconds (float | None): Wall-clock ceiling across **all**
            rounds. Each round inherits what is left, so the loop cannot
            overrun by one agent's budget.
        feedback (Callable[[AgentRun, int], str] | None): Builds the next
            round's goal from the rejected run. Defaults to restating the
            goal with the failed attempt attached — a model that cannot see
            its previous attempt tends to reproduce it.
        context (AgentContext | None): A pre-seeded context for round one.

    Returns:
        LoopResult: Every round, and whether any was accepted.

    Raises:
        ValueError: When ``max_rounds`` is not positive.
    """
    if max_rounds <= 0:
        raise ValueError("max_rounds must be positive")

    started = time.monotonic()
    deadline = started + max_seconds if max_seconds is not None else None
    iterations: list[LoopIteration] = []
    output = ""

    for index in range(max_rounds):
        if deadline is not None and time.monotonic() >= deadline:
            break

        round_context = (
            context if index == 0 and context is not None else AgentContext()
        )
        round_context.deadline = deadline
        prompt = (
            goal
            if index == 0
            else _feedback_prompt(
                goal,
                iterations[-1].run,
                index,
                feedback,
            )
        )

        run = await agent.run(prompt, context=round_context)
        accepted = await _accepts(until, run)
        iterations.append(
            LoopIteration(index=index, run=run, accepted=accepted),
        )
        output = run.output
        if accepted:
            break

    return LoopResult(
        goal=goal,
        output=output,
        iterations=iterations,
        accepted=any(item.accepted for item in iterations),
        seconds=time.monotonic() - started,
    )


def _feedback_prompt(
    goal: str,
    previous: AgentRun,
    index: int,
    feedback: Callable[[AgentRun, int], str] | None,
) -> str:
    """Build the next round's goal from the rejected attempt.

    Args:
        goal (str): The original goal.
        previous (AgentRun): The rejected run.
        index (int): The upcoming round number.
        feedback (Callable[[AgentRun, int], str] | None): Caller override.

    Returns:
        str: The prompt for the next round.
    """
    if feedback is not None:
        return feedback(previous, index)
    return (
        f"{goal}\n\n"
        f"Your previous attempt was rejected. It was:\n\n{previous.output}\n\n"
        "Produce a different and better answer."
    )


DEFAULT_CRITIC_PROMPT: str = (
    "You are reviewing another agent's work against the goal below. "
    "If it fully satisfies the goal, reply with exactly APPROVED and "
    "nothing else. Otherwise list what is wrong and what to change, in "
    "specific terms the author can act on. Do not rewrite it yourself."
)
"""Instruction given to the critic in :func:`refine`.

The exact-token approval matters: a critic asked for free-form judgement
hedges, and "looks good, though you might consider..." is impossible to
branch on. One reserved word makes the decision machine-readable while the
rejection stays free-form, which is the half that needs to be expressive.
"""

APPROVAL_TOKEN: str = "APPROVED"
"""What the critic says when it accepts. Compared case-insensitively."""


async def refine(
    worker: Agent,
    critic: Agent,
    goal: str,
    *,
    max_rounds: int = 3,
    max_seconds: float | None = None,
    critic_prompt: str = DEFAULT_CRITIC_PROMPT,
) -> LoopResult:
    """Generate, critique and revise until the critic approves.

    Example:

        >>> result = await refine(writer, reviewer, "Write release notes.")
        >>> result.accepted
        True
        >>> result.iterations[0].critique
        'Too vague about the breaking change...'

    A second agent reading the first one's output catches what the author
    cannot — the same reason code review works on people. The critic never
    rewrites: forcing it to describe the fix keeps the work (and the
    accountability for it) with the worker, and a critic that rewrites
    tends to smuggle in its own errors unreviewed.

    Args:
        worker (Agent): Produces the answer.
        critic (Agent): Judges it. Give it no tools unless it needs them to
            verify — a critic with a search tool will research instead of
            reviewing.
        goal (str): What to accomplish.
        max_rounds (int): Hard ceiling on worker attempts.
        max_seconds (float | None): Wall-clock ceiling across all rounds,
            inherited by both agents.
        critic_prompt (str): The critic's instruction. It must ask for the
            exact token ``APPROVED``; see :data:`DEFAULT_CRITIC_PROMPT`.

    Returns:
        LoopResult: Every round with its critique, and whether the critic
        ever approved.

    Raises:
        ValueError: When ``max_rounds`` is not positive.
    """
    if max_rounds <= 0:
        raise ValueError("max_rounds must be positive")

    started = time.monotonic()
    deadline = started + max_seconds if max_seconds is not None else None
    iterations: list[LoopIteration] = []
    output = ""
    last_critique: str | None = None

    for index in range(max_rounds):
        if deadline is not None and time.monotonic() >= deadline:
            break

        worker_context = AgentContext()
        worker_context.deadline = deadline
        prompt = goal
        if last_critique is not None:
            prompt = (
                f"{goal}\n\n"
                f"A reviewer rejected your previous attempt:\n\n{output}\n\n"
                f"Their feedback:\n\n{last_critique}\n\n"
                "Produce a corrected version."
            )

        run = await worker.run(prompt, context=worker_context)
        output = run.output

        critic_context = AgentContext()
        critic_context.deadline = deadline
        review = await critic.run(
            f"{critic_prompt}\n\nGOAL:\n{goal}\n\nWORK TO REVIEW:\n{output}",
            context=critic_context,
        )
        verdict = review.output.strip()
        approved = verdict.upper().startswith(APPROVAL_TOKEN)
        last_critique = None if approved else verdict

        iterations.append(
            LoopIteration(
                index=index,
                run=run,
                accepted=approved,
                critique=None if approved else verdict,
            ),
        )
        if approved:
            break

    return LoopResult(
        goal=goal,
        output=output,
        iterations=iterations,
        accepted=any(item.accepted for item in iterations),
        seconds=time.monotonic() - started,
    )


def succeeded(run: AgentRun) -> bool:
    """Accept a run only when the model finished on its own terms.

    The simplest useful :data:`Verdict`, and a reminder that a run stopped
    by a budget still carries text: without a check like this a loop would
    happily accept truncated work.

    Args:
        run (AgentRun): The run to judge.

    Returns:
        bool: ``True`` when the stop reason is
        :attr:`~tempest_fastapi_sdk.agents.StopReason.COMPLETED`.
    """
    return run.stop_reason == StopReason.COMPLETED


__all__: list[str] = [
    "APPROVAL_TOKEN",
    "DEFAULT_CRITIC_PROMPT",
    "LoopIteration",
    "LoopResult",
    "Verdict",
    "refine",
    "run_until",
    "succeeded",
]
