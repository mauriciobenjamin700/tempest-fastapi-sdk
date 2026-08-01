"""Agents that delegate to other agents.

There is no separate "team" object here, and that is the design. An agent
already knows how to pick a tool by name and read what it returns, so the
cheapest way to let it hand work to a specialist is to make the specialist
**a tool**. `agent_tool` does exactly that: one `Agent` in, one
:class:`~tempest_fastapi_sdk.agents.AgentTool` out. Composition falls out
of what already exists rather than needing a second mechanism.

What delegation *does* need is three guards, because the failure modes are
not the same as a plain tool's:

* **The clock is inherited.** A sub-agent may finish sooner than its own
  budget allows but never later than its parent's, since the parent is the
  one holding a request open. `AgentContext.deadline` carries the earlier
  of the two.
* **Depth is bounded.** Nothing stops a model from having A delegate to B
  which delegates back to A. `max_depth` turns that from an infinite loop
  into a refusal the model can read.
* **The child's work comes back.** Its artifacts merge into the parent's
  namespace under a prefix, and its trace hangs off the parent's step
  instead of vanishing.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from tempest_fastapi_sdk.agents.schemas import AgentArtifact, ToolResult
from tempest_fastapi_sdk.agents.tools import AgentContext, AgentTool, AgentToolError

if TYPE_CHECKING:
    from tempest_fastapi_sdk.agents.agent import Agent

DEFAULT_MAX_DEPTH: int = 3
"""How many delegations deep a chain may go before it is refused.

Three is enough for a coordinator to reach a specialist that consults one
helper, and shallow enough that a cycle is caught long before it costs
real time.
"""


def agent_tool(
    agent: Agent,
    *,
    name: str | None = None,
    description: str | None = None,
    max_depth: int = DEFAULT_MAX_DEPTH,
    share_artifacts: bool = True,
) -> AgentTool:
    """Expose an agent as a tool another agent can call.

    Example:

        >>> researcher = Agent(generator, tools=[web_search_tool(search)],
        ...                    name="researcher")
        >>> writer = Agent(
        ...     generator,
        ...     tools=[agent_tool(researcher,
        ...                       description="Research a topic on the web.")],
        ...     name="writer",
        ... )
        >>> run = await writer.run("Write a short brief about PIX.")
        >>> run.steps[1].kind, run.steps[1].children[0].name
        ('agent', 'chat')

    The delegated run's outcome is reported back to the caller as text —
    including *why* it stopped. A parent told only the partial answer of a
    timed-out child would present it as a complete one.

    Args:
        agent (Agent): The specialist to delegate to.
        name (str | None): The function name the model calls; defaults to
            ``"ask_<agent name>"``.
        description (str | None): What this specialist is for, written for
            the calling model. Defaults to a generic line — worth
            overriding, since this text is the only basis the caller has
            for choosing between specialists.
        max_depth (int): Refuse to delegate beyond this nesting depth.
        share_artifacts (bool): Merge the child's artifacts into the
            parent's run, prefixed with the child's name so two specialists
            writing ``report.md`` cannot clobber each other.

    Returns:
        AgentTool: The delegation tool.
    """
    tool_name = name or f"ask_{agent.name}"
    tool_description = description or (
        f"Delegate a self-contained sub-task to the '{agent.name}' agent "
        "and get its answer back. Give it one clear goal."
    )

    async def handler(
        arguments: dict[str, Any],
        context: AgentContext,
    ) -> ToolResult:
        """Run the sub-agent under the caller's clock and depth budget."""
        goal = str(arguments.get("goal", "")).strip()
        if not goal:
            raise AgentToolError("'goal' is required")
        if context.depth >= max_depth:
            raise AgentToolError(
                f"delegation refused: already {context.depth} levels deep "
                f"(max {max_depth}). Answer with what you have.",
            )

        child_context = context.child(goal=goal, parent=context.parent or "agent")
        run = await agent.run(goal, context=child_context)

        artifacts: list[AgentArtifact] = []
        if share_artifacts:
            for artifact in run.artifacts:
                artifacts.append(
                    artifact.model_copy(
                        update={"name": f"{agent.name}/{artifact.name}"},
                    ),
                )

        summary = run.output or "(the sub-agent produced no answer)"
        if not run.succeeded:
            summary = f"[stopped: {run.stop_reason}] {summary}"
        if artifacts:
            names = ", ".join(item.name for item in artifacts)
            summary = f"{summary}\n\nProduced: {names}"
        return ToolResult(text=summary, artifacts=artifacts, run=run)

    return AgentTool(
        name=tool_name,
        description=tool_description,
        parameters={
            "type": "object",
            "properties": {
                "goal": {
                    "type": "string",
                    "description": (
                        "The self-contained sub-task to delegate. The "
                        "specialist cannot see this conversation, so state "
                        "everything it needs."
                    ),
                },
            },
            "required": ["goal"],
        },
        handler=handler,
    )


def team_tools(
    agents: dict[Agent, str] | list[Agent],
    *,
    max_depth: int = DEFAULT_MAX_DEPTH,
) -> list[AgentTool]:
    """Turn several specialists into a tool set for a coordinator.

    Example:

        >>> coordinator = Agent(
        ...     generator,
        ...     tools=team_tools({
        ...         researcher: "Research facts on the web.",
        ...         illustrator: "Draw images from a description.",
        ...     }),
        ... )

    A thin convenience over :func:`agent_tool` — it exists because passing
    the description alongside each agent is the shape that keeps the
    descriptions readable next to each other, and those descriptions are
    what the coordinator actually chooses on.

    Args:
        agents (dict[Agent, str] | list[Agent]): Either a mapping of agent
            to description, or a plain list using each agent's default
            description.
        max_depth (int): Passed through to every delegation tool.

    Returns:
        list[AgentTool]: One delegation tool per specialist.
    """
    if isinstance(agents, dict):
        return [
            agent_tool(agent, description=text, max_depth=max_depth)
            for agent, text in agents.items()
        ]
    return [agent_tool(agent, max_depth=max_depth) for agent in agents]


__all__: list[str] = ["DEFAULT_MAX_DEPTH", "agent_tool", "team_tools"]
