"""Getting a typed object out of an agent, not a paragraph.

An agent that ends with prose is fine for a chat and useless for a
pipeline: something downstream has to turn "The invoice totals R$ 1.240,50
and is due on the 15th" back into fields, and that step fails on the day
the model phrases it differently.

The reliable way to avoid that is **not** to ask the model for JSON and
parse the reply. It is to give the model a tool shaped like the answer and
let it finish by calling that tool. The arguments *are* the structured
output, they arrive already validated against your schema, and the same
tool-calling machinery that makes the rest of the agent work carries them
— no second format for the model to get wrong.

    >>> class Invoice(BaseSchema):
    ...     total_cents: int
    ...     due_date: str
    ...
    >>> run = await agent.run_structured("Read invoice.pdf", output=Invoice)
    >>> run.data.total_cents
    124050

Free-text parsing survives only as a fallback, for backends whose tool
calling is weak; when it runs at all, it is reported rather than hidden.
"""

from __future__ import annotations

import json
from typing import TYPE_CHECKING, Any, Generic, TypeVar

from pydantic import BaseModel, Field, ValidationError

from tempest_fastapi_sdk.agents.schemas import AgentRun, StopReason
from tempest_fastapi_sdk.agents.tools import AgentContext, AgentTool
from tempest_fastapi_sdk.agents.typed import schema_of

if TYPE_CHECKING:
    from tempest_fastapi_sdk.agents.agent import Agent

OutputT = TypeVar("OutputT", bound=BaseModel)

FINAL_ANSWER_TOOL: str = "final_answer"
"""Name of the tool the model calls to deliver a structured answer."""


class StructuredRun(AgentRun, Generic[OutputT]):
    """An agent run whose answer is a validated model.

    Attributes:
        data (OutputT | None): The structured answer, or ``None`` when the
            run ended without producing one — a budget ran out, the model
            never called the answer tool, or what it passed did not
            validate. Always check it; a run can be
            :attr:`~tempest_fastapi_sdk.agents.AgentRun.succeeded` and
            still carry ``None`` here if the fallback parse also failed.
        parse_error (str | None): Why the structured answer is missing,
            when it is.
    """

    data: OutputT | None = Field(
        default=None,
        title="Data",
        description="The validated structured answer.",
    )
    parse_error: str | None = Field(
        default=None,
        title="Parse error",
        description="Why no structured answer was produced.",
    )

    @property
    def has_data(self) -> bool:
        """Return whether a structured answer was produced.

        Returns:
            bool: ``True`` when :attr:`data` is set.
        """
        return self.data is not None


def final_answer_tool(
    output: type[BaseModel],
    *,
    name: str = FINAL_ANSWER_TOOL,
    description: str | None = None,
) -> AgentTool:
    """Build the tool an agent calls to deliver its structured answer.

    Exposed on its own because it is occasionally useful directly — a
    multi-agent flow where only the last specialist reports structurally,
    for instance.

    Args:
        output (type[BaseModel]): The answer's shape.
        name (str): The tool name.
        description (str | None): Override the instruction shown to the
            model.

    Returns:
        AgentTool: The submission tool. Its handler records the validated
        object on the run context and returns a short acknowledgement; the
        agent notices the record and stops.
    """
    text = description or (
        "Deliver your final answer. Call this exactly once, when you have "
        "everything you need. Do not answer in prose — the values you pass "
        "here are the answer."
    )

    async def handler(
        arguments: dict[str, Any],
        context: AgentContext,
    ) -> str:
        """Validate the model's answer and stash it on the context."""
        parsed = output.model_validate(arguments)
        context.state[_STATE_KEY] = parsed
        return "Answer recorded."

    return AgentTool(
        name=name,
        description=text,
        parameters=schema_of(output),
        handler=handler,
    )


_STATE_KEY: str = "__structured_answer__"
"""Where the answer tool leaves its validated result on the context."""


def _instruction(output: type[BaseModel], tool_name: str) -> str:
    """Return the system-prompt line telling the model how to finish.

    Args:
        output (type[BaseModel]): The answer's shape.
        tool_name (str): The submission tool's name.

    Returns:
        str: The instruction to append to the agent's system prompt.
    """
    return (
        f"\n\nWhen you have the answer, call the '{tool_name}' tool with it. "
        "That call is how you finish — do not write the answer as prose."
    )


async def run_structured(
    agent: Agent,
    goal: str,
    output: type[OutputT],
    *,
    context: AgentContext | None = None,
    allow_text_fallback: bool = True,
    extraction_retry: bool = True,
) -> StructuredRun[OutputT]:
    """Run an agent and get a validated object back.

    Example:

        >>> class Summary(BaseSchema):
        ...     headline: str
        ...     bullets: list[str]
        ...
        >>> run = await run_structured(agent, "Summarise the report.", Summary)
        >>> run.data.headline if run.has_data else run.parse_error

    The agent is given a temporary ``final_answer`` tool shaped like
    ``output``; calling it *is* how the model finishes. That keeps the
    answer inside the tool-calling path the backend already handles well,
    instead of asking it to emit JSON as text and hoping.

    Args:
        agent (Agent): The agent to run. It is not mutated — the answer
            tool is added to a copy, so the same agent stays usable for
            unstructured runs.
        goal (str): What to accomplish.
        output (type[OutputT]): The answer's shape.
        context (AgentContext | None): A pre-seeded context.
        allow_text_fallback (bool): When the model answers in prose
            instead of calling the tool, try to parse JSON out of that
            text. Set ``False`` to treat prose as a failure — stricter,
            and worth it when a wrong-but-parseable answer is more
            dangerous than no answer.
        extraction_retry (bool): When prose carries no JSON either, make
            one more call whose **only** tool is the answer tool, asking
            the model to restate what it just said in that shape. Small
            local models routinely work the task out correctly and then
            answer in prose regardless of instructions; this recovers the
            answer they already have instead of discarding the whole run.
            It costs one extra model call, which is the right trade
            whenever the alternative is losing the work.

    Returns:
        StructuredRun[OutputT]: The usual run record plus ``data``. When
        ``data`` is ``None``, ``parse_error`` says why.
    """
    from tempest_fastapi_sdk.agents.agent import Agent as AgentClass

    answer_tool = final_answer_tool(output)
    scoped = AgentClass(
        agent.generator,
        tools=[*agent.tools, answer_tool],
        system_prompt=agent.system_prompt + _instruction(output, answer_tool.name),
        budget=agent.budget,
        moderator=agent.moderator,
        run_sink=agent.run_sink,
        metrics=agent.metrics,
        name=agent.name,
    )

    ctx = context or AgentContext()
    run = await scoped.run(goal, context=ctx)

    data: OutputT | None = ctx.state.pop(_STATE_KEY, None)
    parse_error: str | None = None

    if data is None:
        if not allow_text_fallback:
            parse_error = (
                f"the model did not call '{answer_tool.name}' and the text "
                "fallback is disabled"
            )
        else:
            data, parse_error = _from_text(run.output, output)
            if data is None and extraction_retry and run.output.strip():
                data, parse_error = await _extract(
                    agent,
                    run.output,
                    output,
                    answer_tool,
                )

    return StructuredRun[output](  # type: ignore[valid-type]
        goal=run.goal,
        output=run.output,
        steps=run.steps,
        artifacts=run.artifacts,
        stop_reason=run.stop_reason,
        seconds=run.seconds,
        agent=run.agent,
        data=data,
        parse_error=parse_error,
    )


async def _extract(
    agent: Agent,
    text: str,
    output: type[OutputT],
    answer_tool: AgentTool,
) -> tuple[OutputT | None, str | None]:
    """Ask the model to restate a prose answer in the required shape.

    The extractor is given **only** the answer tool. With nothing else to
    call and nothing left to work out, even a small model reliably fills
    the fields — the reasoning already happened in the run that produced
    ``text``, and all that remains is transcription.

    Args:
        agent (Agent): The agent whose backend to reuse.
        text (str): The prose answer to convert.
        output (type[OutputT]): The answer's shape.
        answer_tool (AgentTool): The submission tool.

    Returns:
        tuple[OutputT | None, str | None]: The extracted object, or
        ``None`` plus why the extraction failed too.
    """
    from tempest_fastapi_sdk.agents.agent import Agent as AgentClass
    from tempest_fastapi_sdk.agents.schemas import AgentBudget

    extractor = AgentClass(
        agent.generator,
        tools=[answer_tool],
        system_prompt=(
            "You convert an answer into structured fields. Call the "
            f"'{answer_tool.name}' tool with the values found in the text "
            "below. Do not add information that is not there; if a field "
            "is genuinely absent, use the most reasonable empty value."
        ),
        budget=AgentBudget(max_steps=3, max_seconds=agent.budget.max_seconds),
        name=f"{agent.name}-extractor",
    )
    context = AgentContext()
    try:
        await extractor.run(f"TEXT TO CONVERT:\n\n{text}", context=context)
    except Exception as exc:  # pragma: no cover - backend-specific failures
        return None, f"extraction pass failed: {exc}"
    extracted: OutputT | None = context.state.pop(_STATE_KEY, None)
    if extracted is not None:
        return extracted, None
    return None, "the answer was prose and could not be converted to the schema"


def _from_text(
    text: str,
    output: type[OutputT],
) -> tuple[OutputT | None, str | None]:
    """Try to recover a structured answer from free text.

    Looks for the outermost JSON object in the reply, because models that
    ignore the tool tend to wrap the JSON in prose or a fenced block.

    Args:
        text (str): The model's final message.
        output (type[OutputT]): The answer's shape.

    Returns:
        tuple[OutputT | None, str | None]: The parsed object, or ``None``
        plus the reason it could not be produced.
    """
    if not text.strip():
        return None, "the run produced no answer"
    start = text.find("{")
    end = text.rfind("}")
    if start == -1 or end <= start:
        return None, "no JSON object found in the answer"
    try:
        payload = json.loads(text[start : end + 1])
    except ValueError as exc:
        return None, f"the answer is not valid JSON: {exc}"
    try:
        return output.model_validate(payload), None
    except ValidationError as exc:
        return None, (
            f"the answer does not match the schema: {exc.error_count()} error(s)"
        )


def structured_verdict(
    run: StructuredRun[Any],
) -> bool:
    """Accept a structured run only when it carries validated data.

    A :data:`~tempest_fastapi_sdk.agents.Verdict` for use with
    :func:`~tempest_fastapi_sdk.agents.run_until`, so a loop can retry
    until the model actually produces the shape you asked for.

    Args:
        run (StructuredRun[Any]): The run to judge.

    Returns:
        bool: ``True`` when the run completed *and* produced data.
    """
    return run.stop_reason == StopReason.COMPLETED and run.data is not None


__all__: list[str] = [
    "FINAL_ANSWER_TOOL",
    "StructuredRun",
    "final_answer_tool",
    "run_structured",
    "structured_verdict",
]
