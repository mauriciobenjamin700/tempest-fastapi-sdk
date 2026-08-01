"""Opt-in FastAPI router exposing one agent over HTTP.

`make_agent_router` mounts three things: run the agent and get the whole
record, stream the trace as it happens (SSE), and download an artifact the
run produced.

Artifacts are served from the run rather than embedded in the JSON. A
generated image is megabytes; base64 in a response body inflates it by a
third and makes the payload unreadable in a browser's network tab. The run
reports artifact **names**, and a second request fetches the bytes with a
real media type — which also means an ``<img src>`` works directly.

The router owns only the HTTP surface: model lifecycle, auth and rate
limiting stay with the caller. Mount it under an authenticated parent.
"""

from __future__ import annotations

import json
from collections.abc import AsyncIterator
from typing import TYPE_CHECKING, Any

from fastapi import APIRouter, HTTPException, Response, status

from tempest_fastapi_sdk.agents.schemas import AgentRun
from tempest_fastapi_sdk.schemas.base import BaseSchema
from tempest_fastapi_sdk.sse import ServerSentEvent, sse_response

if TYPE_CHECKING:
    from starlette.responses import StreamingResponse

    from tempest_fastapi_sdk.agents.agent import Agent
    from tempest_fastapi_sdk.agents.storage import InMemoryAgentRunSink


class AgentRunRequestSchema(BaseSchema):
    """Request body for ``POST /run`` and ``POST /run/stream``.

    Attributes:
        goal (str): What the agent should accomplish.
    """

    goal: str


class AgentArtifactSchema(BaseSchema):
    """One artifact's metadata, without the bytes.

    Attributes:
        name (str): The artifact name, used to fetch it.
        media_type (str): IANA media type.
        size_bytes (int): How large it is.
        description (str | None): What it is.
    """

    name: str
    media_type: str
    size_bytes: int
    description: str | None = None


class AgentRunResponseSchema(BaseSchema):
    """Response body for ``POST /run``.

    Mirrors :class:`~tempest_fastapi_sdk.agents.AgentRun` with the artifact
    **bytes** replaced by metadata, so the payload stays small and JSON-safe.

    Attributes:
        goal (str): What was asked.
        output (str): The final answer.
        stop_reason (str): Why the run ended.
        succeeded (bool): Whether the model finished on its own terms.
        seconds (float): Total duration.
        agent (str): The agent's name.
        steps (list[dict[str, Any]]): The trace.
        artifacts (list[AgentArtifactSchema]): What it produced.
    """

    goal: str
    output: str
    stop_reason: str
    succeeded: bool
    seconds: float
    agent: str
    steps: list[dict[str, Any]]
    artifacts: list[AgentArtifactSchema]

    @classmethod
    def from_run(cls, run: AgentRun) -> AgentRunResponseSchema:
        """Build the response from a finished run.

        Args:
            run (AgentRun): The completed run.

        Returns:
            AgentRunResponseSchema: The JSON-safe view.
        """
        return cls(
            goal=run.goal,
            output=run.output,
            stop_reason=str(run.stop_reason),
            succeeded=run.succeeded,
            seconds=run.seconds,
            agent=run.agent,
            steps=[step.model_dump(mode="json") for step in run.steps],
            artifacts=[
                AgentArtifactSchema(
                    name=artifact.name,
                    media_type=artifact.media_type,
                    size_bytes=artifact.size_bytes,
                    description=artifact.description,
                )
                for artifact in run.artifacts
            ],
        )


def make_agent_router(
    agent: Agent,
    *,
    run_store: InMemoryAgentRunSink | None = None,
    prefix: str = "/api/agent",
    tags: list[str] | None = None,
) -> APIRouter:
    """Build a router exposing one agent.

    Endpoints:

    * ``POST {prefix}/run`` — run to completion, return the record.
    * ``POST {prefix}/run/stream`` — stream each step as an SSE event.
    * ``GET {prefix}/runs`` — recent runs (only with a ``run_store``).
    * ``GET {prefix}/runs/{index}/artifacts/{name}`` — download an
      artifact from a kept run (only with a ``run_store``).

    Example:

        >>> store = InMemoryAgentRunSink(max_runs=50)
        >>> agent = Agent(generator, tools=tools, run_sink=store)
        >>> app.include_router(make_agent_router(agent, run_store=store))

    Args:
        agent (Agent): The agent to expose.
        run_store (InMemoryAgentRunSink | None): The same sink the agent
            writes to. Without it the history endpoints are not mounted,
            because there would be nothing to read — a run's artifacts
            live only as long as something holds the run.
        prefix (str): URL prefix.
        tags (list[str] | None): OpenAPI tags. Defaults to ``["agent"]``.

    Returns:
        APIRouter: Ready to mount with ``app.include_router``.
    """
    router = APIRouter(prefix=prefix, tags=list(tags or ["agent"]))

    @router.post("/run", response_model=AgentRunResponseSchema)
    async def run_agent(body: AgentRunRequestSchema) -> AgentRunResponseSchema:
        """Run the agent to completion and return the whole record.

        Args:
            body (AgentRunRequestSchema): The goal.

        Returns:
            AgentRunResponseSchema: Answer, trace, artifact metadata and
            the stop reason.
        """
        run = await agent.run(body.goal)
        return AgentRunResponseSchema.from_run(run)

    @router.post("/run/stream")
    async def stream_agent(body: AgentRunRequestSchema) -> StreamingResponse:
        """Stream the agent's steps as they complete.

        Each event carries one step, so a UI can show the agent working
        instead of a spinner. The stream ends when the run does.

        Args:
            body (AgentRunRequestSchema): The goal.

        Returns:
            StreamingResponse: An SSE stream of ``step`` events.
        """

        async def events() -> AsyncIterator[bytes]:
            """Yield one SSE frame per completed step, then a ``done`` marker.

            The trailing marker matters for a client: an agent's last step
            looks exactly like its earlier ones, so without it a UI cannot
            tell "finished" from "still thinking".
            """
            async for step in agent.stream(body.goal):
                payload = json.dumps(step.model_dump(mode="json"))
                yield (
                    ServerSentEvent(data=payload, event="step")
                    .encode()
                    .encode(
                        "utf-8",
                    )
                )
            yield ServerSentEvent(data="", event="done").encode().encode("utf-8")

        return sse_response(events())

    if run_store is not None:

        @router.get("/runs", response_model=list[AgentRunResponseSchema])
        async def list_runs(limit: int = 20) -> list[AgentRunResponseSchema]:
            """List the most recent runs, newest first.

            Args:
                limit (int): How many to return.

            Returns:
                list[AgentRunResponseSchema]: The kept runs.
            """
            return [
                AgentRunResponseSchema.from_run(run) for run in run_store.recent(limit)
            ]

        @router.get("/runs/{index}/artifacts/{name}", response_class=Response)
        async def get_artifact(index: int, name: str) -> Response:
            """Download one artifact from a kept run.

            Args:
                index (int): Position in the recent list (``0`` is newest).
                name (str): The artifact name.

            Returns:
                Response: The bytes, with the artifact's media type.

            Raises:
                HTTPException: 404 when the run or the artifact is not
                    kept — an artifact is only reachable while its run is
                    still in the buffer.
            """
            runs = run_store.recent()
            if index < 0 or index >= len(runs):
                raise HTTPException(
                    status_code=status.HTTP_404_NOT_FOUND,
                    detail=f"no kept run at index {index}",
                )
            artifact = runs[index].artifact(name)
            if artifact is None:
                raise HTTPException(
                    status_code=status.HTTP_404_NOT_FOUND,
                    detail=f"run {index} has no artifact named {name!r}",
                )
            return Response(
                content=artifact.data,
                media_type=artifact.media_type,
            )

    return router


__all__: list[str] = [
    "AgentArtifactSchema",
    "AgentRunRequestSchema",
    "AgentRunResponseSchema",
    "make_agent_router",
]
