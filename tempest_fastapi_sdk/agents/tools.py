"""What an agent can do, and what it knows while doing it.

An :class:`AgentTool` is a name, a description, a JSON-schema of its
arguments, and an async handler. The model reads the first three to decide
what to call; the agent runs the fourth.

The handler takes **two** positional arguments — ``(arguments, context)``.
The context is what makes multimodal chaining work: a tool that draws
registers ``chart.png`` on the run, and the next tool can read those bytes
back by name instead of the agent having to write them to disk or the model
having to carry base64 through a prompt.

Nothing here imports a model library; the tools are closures over objects
the caller already built.
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from pathlib import PurePosixPath
from typing import TYPE_CHECKING, Any

from tempest_fastapi_sdk.agents.schemas import AgentArtifact, ToolResult

if TYPE_CHECKING:
    from tempest_fastapi_sdk.genai.pipeline import Tool


class AgentToolError(Exception):
    """A tool failed in a way the model should hear about.

    Raising this (rather than returning text) marks the step as failed in
    the trace while still feeding the message back to the model as an
    observation, so it can try something else.

    Its message is treated as **written for an audience**: it is fed to
    the model and recorded verbatim on the step, which is what the HTTP
    router, the SSE stream and every run sink expose. Any other exception
    also becomes an observation the model reads in full, but the trace
    keeps only its type — an arbitrary exception can carry a DSN, a token
    or a file path, and the trace is served to clients. Raise this one when
    the text is safe to show.
    """


@dataclass
class AgentContext:
    """What a tool can see about the run it is part of.

    Attributes:
        goal (str): The goal the agent was given.
        artifacts (dict[str, AgentArtifact]): Everything produced so far,
            by name. A tool reads from it to consume an earlier result and
            never has to know which step produced it.
        state (dict[str, Any]): Free-form scratch space shared across the
            run's tools, for callers wiring their own coordination.
        depth (int): How many delegations deep this run is. ``0`` is the
            top-level run; a sub-agent sees ``1``, its own sub-agent ``2``.
            It is what stops A delegating to B delegating back to A
            forever.
        deadline (float | None): A ``time.monotonic()`` instant this run
            must not run past, inherited from the caller. A sub-agent may
            finish sooner than its own budget allows but **never** later
            than its parent's clock, because the parent is the one holding
            a request open.
        parent (str | None): Name of the agent that delegated here, for
            reading a nested trace.
        agent (str | None): Name of the agent running under this context.
            Set by the agent at the start of each run, so a delegation tool
            can tell its child who the parent is.
        run_id (str | None): Identifier of the run using this context.
            Assigned by the agent at the start of **each** run (a context
            reused for a second run gets a new one) and copied onto
            :attr:`~tempest_fastapi_sdk.agents.AgentRun.run_id`.
        owner (str | None): Who the run belongs to — a user or tenant id.
            Copied onto :attr:`~tempest_fastapi_sdk.agents.AgentRun.owner`
            and inherited by delegated runs; the HTTP router sets it from
            its ``owner`` dependency and filters history by it.
        opened_skills (set[str]): Skills loaded by the agent running under
            **this** context. Deliberately not kept in :attr:`state`, which
            a delegated child shares: a skill one agent opened must not
            expose its tools to the other.
        answer (Any): The structured answer a final-answer tool recorded,
            per context for the same reason.
    """

    goal: str = ""
    artifacts: dict[str, AgentArtifact] = field(default_factory=dict)
    state: dict[str, Any] = field(default_factory=dict)
    depth: int = 0
    deadline: float | None = None
    parent: str | None = None
    agent: str | None = None
    run_id: str | None = None
    owner: str | None = None
    opened_skills: set[str] = field(default_factory=set)
    answer: Any = None

    def child(self, *, goal: str, parent: str) -> AgentContext:
        """Derive the context a delegated agent should run under.

        The child gets its **own** artifact namespace — a sub-agent that
        writes ``report.md`` must not silently overwrite the parent's
        ``report.md`` — and its own loaded skills and answer slot, while
        inheriting the deadline, the owner, the shared :attr:`state` and one
        more level of depth. Whatever it produces is merged back explicitly
        by the caller, so the parent decides what to keep.

        Args:
            goal (str): The sub-goal being delegated.
            parent (str): The delegating agent's name.

        Returns:
            AgentContext: The child context.
        """
        return AgentContext(
            goal=goal,
            depth=self.depth + 1,
            deadline=self.deadline,
            parent=parent,
            state=self.state,
            owner=self.owner,
        )

    def unique_artifact_name(self, name: str) -> str:
        """Return ``name``, or the first free variant of it on this run.

        ``chart.png`` becomes ``chart-1.png``, then ``chart-2.png``: the
        extension survives so the media type still reads right, and a name
        already taken — an input the caller seeded, an earlier step's
        output — is never reused.

        Args:
            name (str): The desired artifact name.

        Returns:
            str: A name no artifact on this context holds.
        """
        if name not in self.artifacts:
            return name
        suffix = PurePosixPath(name).suffix
        stem = name[: len(name) - len(suffix)] if suffix else name
        counter = 1
        while f"{stem}-{counter}{suffix}" in self.artifacts:
            counter += 1
        return f"{stem}-{counter}{suffix}"

    def claim_artifact_name(self, requested: str | None, default: str) -> str:
        """Return the name a tool should store a new artifact under.

        A name the **model** chose is honoured only when it is free: saving
        over an existing artifact would silently destroy an input, so it is
        refused with a message the model can act on. With no chosen name the
        ``default`` is made unique instead, because a generated default
        colliding is the tool's problem, not the model's.

        Args:
            requested (str | None): The filename the model passed, if any.
            default (str): The tool's own default name.

        Returns:
            str: A free artifact name.

        Raises:
            AgentToolError: When ``requested`` names an existing artifact.
        """
        if requested:
            if requested in self.artifacts:
                raise AgentToolError(
                    f"an artifact named {requested!r} already exists; "
                    "choose another filename",
                )
            return requested
        return self.unique_artifact_name(default)

    def require_artifact(self, name: str) -> AgentArtifact:
        """Return an artifact by name, or fail with a message for the model.

        Args:
            name (str): The artifact the tool needs.

        Returns:
            AgentArtifact: The stored artifact.

        Raises:
            AgentToolError: When nothing is registered under ``name``. The
                message lists what *is* available, because the usual cause
                is the model inventing a filename, and a bare "not found"
                gives it nothing to correct with.
        """
        found = self.artifacts.get(name)
        if found is not None:
            return found
        available = ", ".join(sorted(self.artifacts)) or "none"
        raise AgentToolError(
            f"no artifact named {name!r}; available: {available}",
        )


#: What a tool handler returns — text, or text plus artifacts.
ToolReturn = ToolResult | str

#: An agent tool's implementation.
ToolHandler = Callable[[dict[str, Any], AgentContext], Awaitable[ToolReturn]]


@dataclass
class AgentTool:
    """One capability the model can invoke by name.

    Example:

        >>> async def add(arguments: dict[str, Any], _ctx: AgentContext) -> str:
        ...     return str(arguments["a"] + arguments["b"])
        >>> tool = AgentTool(
        ...     name="add",
        ...     description="Add two numbers.",
        ...     parameters={
        ...         "type": "object",
        ...         "properties": {
        ...             "a": {"type": "number"},
        ...             "b": {"type": "number"},
        ...         },
        ...         "required": ["a", "b"],
        ...     },
        ...     handler=add,
        ... )

    Attributes:
        name (str): The function name the model calls.
        description (str): What it does, written for the model. This is
            the only thing steering tool choice, so it is worth more care
            than the implementation.
        parameters (dict[str, Any]): JSON-schema of the arguments.
        handler (ToolHandler): Async ``(arguments, context)`` implementation.
    """

    name: str
    description: str
    parameters: dict[str, Any]
    handler: ToolHandler

    def to_spec(self) -> dict[str, Any]:
        """Render the tool as an OpenAI / Ollama function specification.

        Returns:
            dict[str, Any]: The ``{"type": "function", "function": {...}}``
            spec passed to a backend's ``chat_with_tools``.
        """
        return {
            "type": "function",
            "function": {
                "name": self.name,
                "description": self.description,
                "parameters": self.parameters,
            },
        }

    async def invoke(
        self,
        arguments: dict[str, Any],
        context: AgentContext,
    ) -> ToolResult:
        """Run the handler and normalize what it returns.

        Args:
            arguments (dict[str, Any]): Parsed arguments from the model.
            context (AgentContext): The run context.

        Returns:
            ToolResult: The handler's result, wrapped when it returned a
            plain string.

        Raises:
            Exception: Whatever the handler raised. The agent catches it,
                records the step as failed and passes the message back to
                the model; it is not swallowed here, so a caller invoking a
                tool directly still sees the real failure.
        """
        return ToolResult.of(await self.handler(arguments, context))

    @classmethod
    def from_tool(cls, tool: Tool) -> AgentTool:
        """Adapt a chat-pipeline :class:`~tempest_fastapi_sdk.genai.Tool`.

        The pipeline's tools take one argument and return a string, so the
        adapter drops the context and wraps the result. Use it to reuse
        tools already written for ``AIChatPipeline`` without touching them.

        Args:
            tool (Tool): The chat-pipeline tool.

        Returns:
            AgentTool: The same capability, agent-shaped.
        """

        async def handler(
            arguments: dict[str, Any],
            _context: AgentContext,
        ) -> ToolReturn:
            """Call the wrapped single-argument handler."""
            return await tool.handler(arguments)

        return cls(
            name=tool.name,
            description=tool.description,
            parameters=tool.parameters,
            handler=handler,
        )


def text_tool(
    name: str,
    description: str,
    handler: Callable[..., Awaitable[ToolReturn]],
    *,
    parameters: dict[str, Any] | None = None,
) -> AgentTool:
    """Build a tool that takes a single ``text`` argument.

    The shape covers most hand-written tools (look something up, transform
    a string) and saves restating the same JSON-schema each time.

    Example:

        >>> tool = text_tool(
        ...     "shout",
        ...     "Return the text in upper case.",
        ...     lambda arguments, _ctx: _upper(arguments["text"]),
        ... )

    Args:
        name (str): The function name the model calls.
        description (str): What the tool does, written for the model.
        handler (Callable[..., Awaitable[ToolReturn]]): Async
            ``(arguments, context)`` implementation.
        parameters (dict[str, Any] | None): Override the generated schema
            when the tool takes more than ``text``.

    Returns:
        AgentTool: The tool, ready to hand to an agent.
    """
    schema: dict[str, Any] = parameters or {
        "type": "object",
        "properties": {
            "text": {"type": "string", "description": "The input text."},
        },
        "required": ["text"],
    }
    return AgentTool(
        name=name,
        description=description,
        parameters=schema,
        handler=handler,
    )


__all__: list[str] = [
    "AgentContext",
    "AgentTool",
    "AgentToolError",
    "ToolHandler",
    "ToolReturn",
    "text_tool",
]
