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
from typing import TYPE_CHECKING, Any

from tempest_fastapi_sdk.agents.schemas import AgentArtifact, ToolResult

if TYPE_CHECKING:
    from tempest_fastapi_sdk.genai.pipeline import Tool


class AgentToolError(Exception):
    """A tool failed in a way the model should hear about.

    Raising this (rather than returning text) marks the step as failed in
    the trace while still feeding the message back to the model as an
    observation, so it can try something else. Any other exception is
    treated the same way — the difference is only that this one is
    deliberate.
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
    """

    goal: str = ""
    artifacts: dict[str, AgentArtifact] = field(default_factory=dict)
    state: dict[str, Any] = field(default_factory=dict)

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
