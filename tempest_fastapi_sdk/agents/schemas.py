"""Typed shapes for an agent run: budget, trace, artifacts, verdict.

Everything here is plain Pydantic + :class:`~tempest_fastapi_sdk.core.BaseStrEnum`,
so this module imports with **no** optional extra installed. The models a
run actually drives are injected by the caller.

The design decision worth stating: an agent's value is not only its final
answer but the **record of how it got there**. A run that produced a good
answer through a tool that silently errored is a run you need to see. So
every step — the model's own turns and each tool call, with arguments,
output, elapsed time and error — is captured in :class:`AgentStep`, and the
run reports why it stopped rather than just stopping.
"""

from __future__ import annotations

from typing import Any

from pydantic import Field

from tempest_fastapi_sdk.core import BaseStrEnum
from tempest_fastapi_sdk.schemas.base import BaseSchema


class StepKind(BaseStrEnum):
    """What produced one step of the trace.

    * ``MODEL`` — the language model was asked to think or answer.
    * ``TOOL`` — a tool the model chose was invoked.
    """

    MODEL = "model"
    TOOL = "tool"


class StopReason(BaseStrEnum):
    """Why a run ended.

    Only ``COMPLETED`` means the model decided it was done. The rest are
    the agent cutting the run short, and a caller that treats them alike
    will ship truncated answers as finished ones.

    * ``COMPLETED`` — the model answered without asking for another tool.
    * ``MAX_STEPS`` — the step budget ran out first.
    * ``TIMEOUT`` — the wall-clock budget ran out first.
    * ``MAX_TOOL_CALLS`` — the tool-call budget ran out first.
    * ``ERROR`` — the model backend itself failed.
    * ``BLOCKED`` — moderation rejected the goal or the answer.
    """

    COMPLETED = "completed"
    MAX_STEPS = "max_steps"
    TIMEOUT = "timeout"
    MAX_TOOL_CALLS = "max_tool_calls"
    ERROR = "error"
    BLOCKED = "blocked"


class AgentArtifact(BaseSchema):
    """A binary result a tool produced — an image, an audio clip, a file.

    Artifacts are named because that is how they are referenced later: a
    tool that draws returns ``"chart.png"``, and the next step can ask the
    vision model to describe ``"chart.png"`` without anything being
    written to disk. The bytes travel in memory for the length of the run.

    Attributes:
        name (str): Unique name within the run.
        media_type (str): IANA type (``"image/png"``, ``"audio/wav"``).
        data (bytes): The raw content.
        description (str | None): What it is, for the model and for a human
            reading the trace.
    """

    name: str = Field(
        title="Name",
        description="Unique name within the run.",
        examples=["chart.png"],
    )
    media_type: str = Field(
        title="Media type",
        description="IANA media type of the content.",
        examples=["image/png"],
    )
    data: bytes = Field(
        title="Data",
        description="The raw content.",
        examples=[b"<binary>"],
    )
    description: str | None = Field(
        default=None,
        title="Description",
        description="What the artifact is.",
        examples=["A bar chart of monthly revenue"],
    )

    @property
    def size_bytes(self) -> int:
        """Return how many bytes the artifact holds.

        Returns:
            int: The content length.
        """
        return len(self.data)


class ToolResult(BaseSchema):
    """What a tool hands back: text for the model, artifacts for the caller.

    The split matters. A tool that generates an image cannot put PNG bytes
    into a prompt — the model gets ``text`` ("generated chart.png, 512x512")
    and the caller gets the bytes. A tool with nothing binary to return can
    be written as a plain ``str`` and is wrapped into one of these.

    Attributes:
        text (str): What the model reads as the tool's output.
        artifacts (list[AgentArtifact]): Binary results, if any.
    """

    text: str = Field(
        title="Text",
        description="What the model reads as this tool's output.",
        examples=["Generated chart.png (512x512)."],
    )
    artifacts: list[AgentArtifact] = Field(
        default_factory=list,
        title="Artifacts",
        description="Binary results produced by the tool.",
    )

    @classmethod
    def of(cls, value: ToolResult | str) -> ToolResult:
        """Normalize a handler's return value into a `ToolResult`.

        Args:
            value (ToolResult | str): What the handler returned.

        Returns:
            ToolResult: ``value`` itself, or a text-only result wrapping it.
        """
        if isinstance(value, ToolResult):
            return value
        return cls(text=str(value))


class AgentStep(BaseSchema):
    """One entry in the run's trace.

    Attributes:
        index (int): Position in the run, from ``0``.
        kind (StepKind): Whether the model or a tool produced it.
        name (str): The tool name, or ``"chat"`` for a model turn.
        arguments (dict[str, Any]): Arguments the model passed to the tool.
        output (str): What the step produced, as the model saw it.
        artifacts (list[str]): Names of artifacts this step created.
        error (str | None): The failure, when the step failed. A failed
            step is still a step: the error is fed back to the model as an
            observation, so it can correct course.
        seconds (float): Wall-clock duration.
    """

    index: int = Field(
        title="Index",
        description="Position in the run.",
        examples=[0],
    )
    kind: StepKind = Field(
        title="Kind",
        description="Whether the model or a tool produced this step.",
        examples=["tool"],
    )
    name: str = Field(
        title="Name",
        description="Tool name, or 'chat' for a model turn.",
        examples=["generate_image"],
    )
    arguments: dict[str, Any] = Field(
        default_factory=dict,
        title="Arguments",
        description="Arguments the model passed to the tool.",
    )
    output: str = Field(
        default="",
        title="Output",
        description="What the step produced, as the model saw it.",
        examples=["Generated chart.png (512x512)."],
    )
    artifacts: list[str] = Field(
        default_factory=list,
        title="Artifacts",
        description="Names of artifacts this step created.",
    )
    error: str | None = Field(
        default=None,
        title="Error",
        description="The failure, when the step failed.",
        examples=["unknown tool 'draw'"],
    )
    seconds: float = Field(
        default=0.0,
        title="Seconds",
        description="Wall-clock duration of the step.",
        examples=[1.42],
    )


class AgentBudget(BaseSchema):
    """The ceilings a run must not cross.

    An agent loop without a ceiling is an unbounded bill and an unbounded
    outage. Steps alone are not enough: one tool call can hang, so the
    wall-clock limit is what actually protects a request. Both default to
    something small enough to be safe and large enough to be useful.

    Attributes:
        max_steps (int): Total steps — model turns plus tool calls.
        max_seconds (float | None): Wall-clock ceiling, or ``None`` for no
            time limit (only sensible for offline batch work).
        max_tool_calls (int | None): Total tool invocations, or ``None``
            to bound them by ``max_steps`` alone.
    """

    max_steps: int = Field(
        default=12,
        gt=0,
        title="Max steps",
        description="Total steps: model turns plus tool calls.",
        examples=[12],
    )
    max_seconds: float | None = Field(
        default=120.0,
        gt=0,
        title="Max seconds",
        description="Wall-clock ceiling; None disables the time limit.",
        examples=[120.0],
    )
    max_tool_calls: int | None = Field(
        default=None,
        gt=0,
        title="Max tool calls",
        description="Total tool invocations; None bounds by steps alone.",
        examples=[8],
    )


class AgentRun(BaseSchema):
    """The complete record of one agent execution.

    Attributes:
        goal (str): What the agent was asked to do.
        output (str): The final answer.
        steps (list[AgentStep]): The trace, in order.
        artifacts (list[AgentArtifact]): Everything the run produced.
        stop_reason (StopReason): Why it ended — check this before
            trusting ``output``.
        seconds (float): Total wall-clock duration.
        agent (str): The agent's name, for traces holding more than one.
    """

    goal: str = Field(
        title="Goal",
        description="What the agent was asked to do.",
        examples=["Draw a bar chart and describe it"],
    )
    output: str = Field(
        default="",
        title="Output",
        description="The final answer.",
    )
    steps: list[AgentStep] = Field(
        default_factory=list,
        title="Steps",
        description="The trace, in order.",
    )
    artifacts: list[AgentArtifact] = Field(
        default_factory=list,
        title="Artifacts",
        description="Everything the run produced.",
    )
    stop_reason: StopReason = Field(
        default=StopReason.COMPLETED,
        title="Stop reason",
        description="Why the run ended.",
        examples=["completed"],
    )
    seconds: float = Field(
        default=0.0,
        title="Seconds",
        description="Total wall-clock duration.",
        examples=[8.31],
    )
    agent: str = Field(
        default="agent",
        title="Agent",
        description="The agent's name.",
        examples=["researcher"],
    )

    @property
    def succeeded(self) -> bool:
        """Return whether the model finished on its own terms.

        Returns:
            bool: ``True`` only for :attr:`StopReason.COMPLETED`. A run cut
            off by a budget may still carry useful text, but calling it a
            success would hide a truncation.
        """
        return self.stop_reason == StopReason.COMPLETED

    @property
    def tool_calls(self) -> list[str]:
        """Return the tool names invoked, in order.

        Returns:
            list[str]: One entry per tool step, including failed ones.
        """
        return [step.name for step in self.steps if step.kind == StepKind.TOOL]

    def artifact(self, name: str) -> AgentArtifact | None:
        """Return one artifact by name.

        Args:
            name (str): The artifact name.

        Returns:
            AgentArtifact | None: The artifact, or ``None`` when the run
            produced no artifact under that name.
        """
        for item in self.artifacts:
            if item.name == name:
                return item
        return None


__all__: list[str] = [
    "AgentArtifact",
    "AgentBudget",
    "AgentRun",
    "AgentStep",
    "StepKind",
    "StopReason",
    "ToolResult",
]
