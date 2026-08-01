"""The agent loop: a goal in, a traced run out.

`Agent` drives a text backend that supports tool calling. It asks the
model what to do, runs whatever tools the model picks, feeds the results
back, and repeats until the model answers without asking for another tool
— or until a budget stops it.

Three properties are deliberate, and each one is a bug this shape avoids:

* **A tool that raises does not end the run.** The failure is recorded on
  the step and handed to the model as an observation, because a model that
  hears "no artifact named 'chart.png'; available: plot.png" usually fixes
  it on the next turn, while an exception would throw away the work done
  so far.
* **Every ceiling is enforced, and the reason is reported.** Steps alone
  do not bound a run — one tool call can hang — so wall-clock is checked
  too, and :class:`~tempest_fastapi_sdk.agents.StopReason` says which one
  fired. A caller that ignores it will present truncated work as finished.
* **Binary results never enter the prompt.** Tools return text for the
  model and artifacts for the caller; artifacts are held by name so a
  later tool can consume one without base64 round-trips.

The backend is anything with ``chat_with_tools`` — the SDK's
``TextGenerator`` or ``OllamaGenerator``. A backend without it still works
for a toolless agent, which is just a single-shot answer.
"""

from __future__ import annotations

import contextlib
import time
from collections.abc import AsyncIterator, Sequence
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any

from tempest_fastapi_sdk.agents.schemas import (
    AgentBudget,
    AgentRun,
    AgentStep,
    StepKind,
    StopReason,
)
from tempest_fastapi_sdk.agents.skills import (
    Skill,
    load_skill_tool,
    loaded_skills,
    skills_prompt,
)
from tempest_fastapi_sdk.agents.tools import AgentContext, AgentTool

if TYPE_CHECKING:
    from tempest_fastapi_sdk.agents.storage import AgentRunSink
    from tempest_fastapi_sdk.genai.moderation import ModerationBackend

DEFAULT_SYSTEM_PROMPT: str = (
    "You are a capable assistant working towards the user's goal. "
    "Use the available tools when they help and answer directly when they "
    "do not. When a tool fails, read the error and try a different "
    "approach rather than repeating the same call. When you have the "
    "answer, reply with it and stop calling tools."
)
"""The default instruction given to the model.

Written to counter the two failure modes that dominate small local models:
calling a tool when a direct answer would do, and retrying an identical
failing call until the budget runs out.
"""


@dataclass
class _RunState:
    """One run's mutable state, owned by the call rather than the agent.

    Deliberately not stored on :class:`Agent`: a single agent instance is
    the normal way to serve many requests, and stashing the current run on
    ``self`` would let two concurrent calls overwrite each other's steps,
    artifacts and stop reason. Every entry point creates one of these and
    threads it through.

    Attributes:
        context (AgentContext): The run's artifacts and scratch state.
        steps (list[AgentStep]): The trace, appended as it happens.
        outcome (StopReason): Why the run ended.
        output (str): The final answer.
        started (float): ``time.monotonic()`` at the start.
        tool_calls (int): Tool invocations so far.
        deadline (float | None): The effective instant to stop at — the
            earlier of this agent's own budget and any deadline inherited
            from a delegating parent.
    """

    context: AgentContext
    steps: list[AgentStep] = field(default_factory=list)
    outcome: StopReason = StopReason.COMPLETED
    output: str = ""
    started: float = 0.0
    tool_calls: int = 0
    deadline: float | None = None


class Agent:
    """A goal-driven loop over a tool-calling text backend.

    Example:

        >>> agent = Agent(
        ...     OllamaGenerator("llama3.2"),
        ...     tools=[generate_image_tool(image_generator)],
        ...     budget=AgentBudget(max_steps=8, max_seconds=90),
        ... )
        >>> run = await agent.run("Draw a red bicycle and tell me the seed.")
        >>> run.succeeded, run.tool_calls
        (True, ['generate_image'])

    Attributes:
        generator (Any): The text backend driving the loop.
        tools (list[AgentTool]): What the model may call.
        system_prompt (str): The instruction prepended to every run.
        budget (AgentBudget): The ceilings a run must not cross.
        name (str): This agent's name, recorded on each run.
    """

    def __init__(
        self,
        generator: Any,
        *,
        tools: Sequence[AgentTool] = (),
        skills: Sequence[Skill] = (),
        system_prompt: str = DEFAULT_SYSTEM_PROMPT,
        budget: AgentBudget | None = None,
        moderator: ModerationBackend | None = None,
        run_sink: AgentRunSink | None = None,
        metrics: Any = None,
        name: str = "agent",
    ) -> None:
        """Configure the agent.

        Args:
            generator (Any): A text backend — ``TextGenerator``,
                ``OllamaGenerator``, or anything with ``chat_with_tools``
                (and ``chat`` as the toolless fallback).
            tools (Sequence[AgentTool]): What the model may call. An empty
                sequence makes this a single-shot answerer.
            skills (Sequence[Skill]): Capabilities loaded on demand. Only
                each one's name and one-line description sit in the prompt;
                the full instructions and the skill's own tools arrive when
                the model loads it. This is how an agent can have many
                capabilities without the prompt growing to match.
            system_prompt (str): The instruction prepended to every run.
            budget (AgentBudget | None): Ceilings; a default
                :class:`~tempest_fastapi_sdk.agents.AgentBudget` when
                ``None``.
            moderator (ModerationBackend | None): When set, the goal is
                checked before the run starts and the answer before it is
                returned. A rejection stops the run with
                :attr:`StopReason.BLOCKED` rather than raising.
            run_sink (AgentRunSink | None): Where finished runs go —
                in-memory, a database table, your own callable. Sink
                failures never fail the run.
            metrics (Any): Optional
                :class:`~tempest_fastapi_sdk.genai.GenAIMetrics`; each run
                records duration under the op ``"agent"``.
            name (str): This agent's name, recorded on each run.
        """
        self.generator = generator
        self.tools = list(tools)
        self.skills = list(skills)
        if self.skills:
            self.tools.append(load_skill_tool(self.skills))
            system_prompt = system_prompt + skills_prompt(self.skills)
        self.system_prompt = system_prompt
        self.budget = budget or AgentBudget()
        self.moderator = moderator
        self.run_sink = run_sink
        self.metrics = metrics
        self.name = name

    @property
    def tool_names(self) -> list[str]:
        """Return the names the model can call, in order.

        Returns:
            list[str]: The configured tool names.
        """
        return [tool.name for tool in self.tools]

    def _available(self, context: AgentContext) -> list[AgentTool]:
        """Return the tools callable right now, given what is loaded.

        A skill's tools are hidden until the model loads the skill — that
        is the whole point of the split — so this is recomputed each turn
        rather than fixed at construction.

        Args:
            context (AgentContext): The run context, holding the set of
                loaded skills.

        Returns:
            list[AgentTool]: Base tools plus the tools of every loaded
            skill.
        """
        if not self.skills:
            return self.tools
        opened = loaded_skills(context)
        extra = [
            tool
            for skill in self.skills
            if skill.name in opened
            for tool in skill.tools
        ]
        return [*self.tools, *extra]

    def _specs(self, context: AgentContext) -> list[dict[str, Any]]:
        """Return the tool specifications passed to the backend.

        Args:
            context (AgentContext): The run context.

        Returns:
            list[dict[str, Any]]: One spec per currently-callable tool.
        """
        return [tool.to_spec() for tool in self._available(context)]

    async def _blocked(self, text: str) -> str | None:
        """Return the moderation reason when ``text`` is rejected.

        Args:
            text (str): The goal or the answer.

        Returns:
            str | None: A human-readable reason, or ``None`` when the text
            is allowed or no moderator is configured.
        """
        if self.moderator is None or not text:
            return None
        verdict = await self.moderator.check(text)
        if not getattr(verdict, "flagged", False):
            return None
        labels = ", ".join(getattr(verdict, "labels", []) or []) or "policy"
        return f"blocked by moderation ({labels})"

    async def run(
        self,
        goal: str,
        *,
        context: AgentContext | None = None,
    ) -> AgentRun:
        """Work towards ``goal`` and return the full record.

        Example:

            >>> run = await agent.run("Summarise the latest sales report.")
            >>> run.stop_reason
            <StopReason.COMPLETED: 'completed'>

        Args:
            goal (str): What the agent should accomplish.
            context (AgentContext | None): A pre-seeded context — use it to
                hand the agent artifacts it should start from (an audio
                clip to transcribe, an image to look at).

        Returns:
            AgentRun: The answer, the trace, the artifacts and the reason
            the run ended. Check
            :attr:`~tempest_fastapi_sdk.agents.AgentRun.succeeded` before
            trusting the output — a budget-truncated run still carries text.
        """
        state = _RunState(context=context or AgentContext())
        async for step in self._iterate(goal, state):
            del step
        return await self._finish(goal, state)

    async def run_structured(
        self,
        goal: str,
        output: type[Any],
        *,
        context: AgentContext | None = None,
        allow_text_fallback: bool = True,
    ) -> Any:
        """Work towards ``goal`` and return a validated object.

        Convenience wrapper over
        :func:`~tempest_fastapi_sdk.agents.run_structured`. The agent gains
        a temporary answer tool shaped like ``output``; calling it is how
        the model finishes, which keeps the answer inside the tool-calling
        path instead of asking for JSON as prose.

        Example:

            >>> run = await agent.run_structured("Read the invoice.", Invoice)
            >>> run.data.total_cents if run.has_data else run.parse_error

        Args:
            goal (str): What the agent should accomplish.
            output (type[Any]): A Pydantic model describing the answer.
            context (AgentContext | None): A pre-seeded context.
            allow_text_fallback (bool): Try to parse JSON out of a prose
                reply when the model ignores the tool.

        Returns:
            StructuredRun: The run record with ``data`` set, or
            ``parse_error`` explaining why it is not.
        """
        from tempest_fastapi_sdk.agents.structured import run_structured

        return await run_structured(
            self,
            goal,
            output,
            context=context,
            allow_text_fallback=allow_text_fallback,
        )

    async def stream(
        self,
        goal: str,
        *,
        context: AgentContext | None = None,
    ) -> AsyncIterator[AgentStep]:
        """Work towards ``goal``, yielding each step as it completes.

        The run is finalized (and sent to the sink) once the iterator is
        exhausted; abandoning it early leaves no record, which is the
        correct behaviour for a cancelled request.

        Example:

            >>> async for step in agent.stream("Draw a cat"):
            ...     print(step.kind, step.name)

        Args:
            goal (str): What the agent should accomplish.
            context (AgentContext | None): A pre-seeded context.

        Yields:
            AgentStep: Each model turn and tool call, in order.
        """
        state = _RunState(context=context or AgentContext())
        async for step in self._iterate(goal, state):
            yield step
        await self._finish(goal, state)

    def _deadline(self, started: float, inherited: float | None) -> float | None:
        """Return the instant this run must stop at.

        The **earlier** of the two clocks wins. A sub-agent configured with
        a generous budget cannot extend the request its parent is holding
        open, and a parent with a long budget cannot make a child ignore
        its own shorter one.

        Args:
            started (float): ``time.monotonic()`` at the run's start.
            inherited (float | None): A deadline from a delegating parent.

        Returns:
            float | None: The effective deadline, or ``None`` when neither
            side sets a time limit.
        """
        own = (
            started + self.budget.max_seconds
            if self.budget.max_seconds is not None
            else None
        )
        candidates = [value for value in (own, inherited) if value is not None]
        return min(candidates) if candidates else None

    def _stop_for_budget(self, state: _RunState) -> StopReason | None:
        """Return the ceiling that has been crossed, if any.

        Args:
            state (_RunState): The run in progress.

        Returns:
            StopReason | None: The reason to stop, or ``None`` to continue.
        """
        budget = self.budget
        if len(state.steps) >= budget.max_steps:
            return StopReason.MAX_STEPS
        if (
            budget.max_tool_calls is not None
            and state.tool_calls >= budget.max_tool_calls
        ):
            return StopReason.MAX_TOOL_CALLS
        if state.deadline is not None and time.monotonic() >= state.deadline:
            return StopReason.TIMEOUT
        return None

    async def _iterate(
        self,
        goal: str,
        state: _RunState,
    ) -> AsyncIterator[AgentStep]:
        """Drive the loop, recording into ``state`` and yielding each step.

        The shared mutable ``state`` is how :meth:`run` and :meth:`stream`
        use one implementation: the caller owns it, so two concurrent runs
        on the same agent never see each other.

        Args:
            goal (str): The goal.
            state (_RunState): This run's state, mutated in place.

        Yields:
            AgentStep: Each completed step.
        """
        state.started = time.monotonic()
        state.context.goal = goal
        state.deadline = self._deadline(state.started, state.context.deadline)
        state.context.deadline = state.deadline

        reason = await self._blocked(goal)
        if reason is not None:
            state.outcome = StopReason.BLOCKED
            state.output = reason
            return

        messages: list[dict[str, Any]] = [
            {"role": "system", "content": self.system_prompt},
            {"role": "user", "content": goal},
        ]
        while True:
            stop = self._stop_for_budget(state)
            if stop is not None:
                state.outcome = stop
                return

            available = self._available(state.context)
            tool_by_name = {tool.name: tool for tool in available}
            specs = [tool.to_spec() for tool in available]

            step_started = time.monotonic()
            try:
                message = await self._ask(messages, specs)
            except Exception as exc:
                step = AgentStep(
                    index=len(state.steps),
                    kind=StepKind.MODEL,
                    name="chat",
                    error=f"{type(exc).__name__}: {exc}",
                    seconds=time.monotonic() - step_started,
                )
                state.steps.append(step)
                state.outcome = StopReason.ERROR
                state.output = step.error or ""
                yield step
                return

            content = str(message.get("content") or "")
            calls: list[dict[str, Any]] = message.get("tool_calls") or []
            model_step = AgentStep(
                index=len(state.steps),
                kind=StepKind.MODEL,
                name="chat",
                output=content,
                seconds=time.monotonic() - step_started,
            )
            state.steps.append(model_step)
            yield model_step

            if not calls:
                state.output = content
                state.outcome = StopReason.COMPLETED
                return

            messages.append(
                {
                    "role": "assistant",
                    "content": content,
                    "tool_calls": calls,
                },
            )
            for call in calls:
                state.tool_calls += 1
                step = await self._run_tool(
                    call,
                    tool_by_name,
                    state.context,
                    len(state.steps),
                )
                state.steps.append(step)
                messages.append(
                    {
                        "role": "tool",
                        "content": step.error or step.output,
                    },
                )
                yield step

    async def _ask(
        self,
        messages: list[dict[str, Any]],
        specs: list[dict[str, Any]],
    ) -> dict[str, Any]:
        """Ask the backend for the next move.

        Falls back to plain ``chat`` when there are no tools, or when the
        backend does not implement ``chat_with_tools`` — a toolless agent
        is still a useful single-shot answerer, and refusing to run would
        be worse than answering without tools.

        Args:
            messages (list[dict[str, Any]]): The conversation so far.
            specs (list[dict[str, Any]]): Tool specifications.

        Returns:
            dict[str, Any]: A message dict with ``content`` and, possibly,
            ``tool_calls``.
        """
        hook = getattr(self.generator, "chat_with_tools", None)
        if specs and callable(hook):
            result = await hook(messages, specs)
            return dict(result)
        reply = await self.generator.chat(messages)
        return {"content": str(reply), "tool_calls": []}

    async def _run_tool(
        self,
        call: dict[str, Any],
        tool_by_name: dict[str, AgentTool],
        ctx: AgentContext,
        index: int,
    ) -> AgentStep:
        """Invoke one tool call and turn it into a step.

        Never raises: an unknown tool, bad arguments or a handler blowing
        up all become a step carrying ``error``, which the caller feeds
        back to the model as an observation.

        Args:
            call (dict[str, Any]): One entry of the model's ``tool_calls``.
            tool_by_name (dict[str, AgentTool]): The available tools.
            ctx (AgentContext): The run context (artifacts land here).
            index (int): The step index to assign.

        Returns:
            AgentStep: The completed step, successful or failed.
        """
        started = time.monotonic()
        function: dict[str, Any] = call.get("function") or {}
        name = str(function.get("name", ""))
        arguments = function.get("arguments")
        if not isinstance(arguments, dict):
            arguments = {}

        tool = tool_by_name.get(name)
        if tool is None:
            known = ", ".join(sorted(tool_by_name)) or "none"
            return AgentStep(
                index=index,
                kind=StepKind.TOOL,
                name=name or "unknown",
                arguments=arguments,
                error=f"unknown tool {name!r}; available: {known}",
                seconds=time.monotonic() - started,
            )

        try:
            result = await tool.invoke(arguments, ctx)
        except Exception as exc:
            return AgentStep(
                index=index,
                kind=StepKind.TOOL,
                name=name,
                arguments=arguments,
                error=f"{type(exc).__name__}: {exc}",
                seconds=time.monotonic() - started,
            )

        for artifact in result.artifacts:
            ctx.artifacts[artifact.name] = artifact
        delegated = result.run
        return AgentStep(
            index=index,
            kind=StepKind.AGENT if delegated is not None else StepKind.TOOL,
            name=name,
            arguments=arguments,
            output=result.text,
            artifacts=[artifact.name for artifact in result.artifacts],
            seconds=time.monotonic() - started,
            agent=delegated.agent if delegated is not None else None,
            children=delegated.steps if delegated is not None else [],
        )

    async def _finish(self, goal: str, state: _RunState) -> AgentRun:
        """Assemble the run, moderate the answer and hand it to the sink.

        A run cut short by a budget has no final message, so the last thing
        the model said is used as the output. It is partial work, and
        :attr:`~tempest_fastapi_sdk.agents.AgentRun.stop_reason` is what
        says so — returning an empty string instead would throw away the
        only useful thing a truncated run produced.

        Args:
            goal (str): The goal.
            state (_RunState): The finished run's state.

        Returns:
            AgentRun: The finished record.
        """
        outcome = state.outcome
        output = state.output

        if outcome == StopReason.COMPLETED:
            reason = await self._blocked(output)
            if reason is not None:
                outcome = StopReason.BLOCKED
                output = reason

        if not output:
            for step in reversed(state.steps):
                if step.kind == StepKind.MODEL and step.output:
                    output = step.output
                    break

        run = AgentRun(
            goal=goal,
            output=output,
            steps=state.steps,
            artifacts=list(state.context.artifacts.values()),
            stop_reason=outcome,
            seconds=time.monotonic() - state.started,
            agent=self.name,
        )
        await self._record(run)
        return run

    async def _record(self, run: AgentRun) -> None:
        """Send the run to the sink and the metrics, swallowing failures.

        A storage or metrics hiccup must not turn a completed run into a
        failed request — the work is already done and the caller is holding
        the answer.

        Args:
            run (AgentRun): The finished run.
        """
        if self.metrics is not None:
            with contextlib.suppress(Exception):
                self.metrics.record(self.name, "agent", run.seconds)
        if self.run_sink is not None:
            with contextlib.suppress(Exception):
                await self.run_sink(run)


__all__: list[str] = ["DEFAULT_SYSTEM_PROMPT", "Agent"]
