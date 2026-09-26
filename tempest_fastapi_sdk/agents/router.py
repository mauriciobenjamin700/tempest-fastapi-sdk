"""Opt-in FastAPI router exposing one agent over HTTP.

`make_agent_router` mounts three things: run the agent and get the whole
record, stream the trace as it happens (SSE), and download an artifact the
run produced.

Artifacts are served from the run rather than embedded in the JSON. A
generated image is megabytes; base64 in a response body inflates it by a
third and makes the payload unreadable in a browser's network tab. The run
reports artifact **names**, and a second request fetches the bytes with a
real media type — which also means an ``<img src>`` works directly.

Kept runs are addressed by their stable ``run_id``, never by a position in
the history: a position shifts every time a newer run arrives, so a link
handed out a second ago would start serving someone else's run.

The router owns only the HTTP surface: model lifecycle, authentication and
rate limiting stay with the caller. What it does own is **who sees which
run** — pass ``owner`` and every run is tagged with the caller's principal
and the history endpoints show each caller only their own.
"""

from __future__ import annotations

import json
from collections.abc import AsyncIterator, Awaitable, Callable
from typing import TYPE_CHECKING, Any

from fastapi import APIRouter, Depends, HTTPException, Response, status

from tempest_fastapi_sdk.agents.schemas import AgentRun
from tempest_fastapi_sdk.agents.tools import AgentContext
from tempest_fastapi_sdk.schemas.base import BaseSchema
from tempest_fastapi_sdk.sse import ServerSentEvent, sse_response

if TYPE_CHECKING:
    from starlette.responses import StreamingResponse

    from tempest_fastapi_sdk.agents.agent import Agent
    from tempest_fastapi_sdk.agents.storage import InMemoryAgentRunSink

OwnerDependency = Callable[..., str] | Callable[..., Awaitable[str]]
"""A FastAPI dependency returning the calling principal's id."""


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
        run_id (str): The run's stable id — what the artifact URL uses.
        goal (str): What was asked.
        output (str): The final answer.
        stop_reason (str): Why the run ended.
        succeeded (bool): Whether the model finished on its own terms.
        seconds (float): Total duration.
        agent (str): The agent's name.
        steps (list[dict[str, Any]]): The trace.
        artifacts (list[AgentArtifactSchema]): What it produced.
    """

    run_id: str
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
            run_id=run.run_id,
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


async def _anonymous() -> str | None:
    """Return no principal: the router runs without owner scoping.

    Returns:
        str | None: Always ``None``.
    """
    return None


def make_agent_router(
    agent: Agent,
    *,
    run_store: InMemoryAgentRunSink | None = None,
    prefix: str = "/api/agent",
    tags: list[str] | None = None,
    owner: OwnerDependency | None = None,
) -> APIRouter:
    """Build a router exposing one agent.

    Endpoints:

    * ``POST {prefix}/run`` — run to completion, return the record.
    * ``POST {prefix}/run/stream`` — stream each step as an SSE event; the
      closing ``done`` event carries ``{"run_id": ...}``.
    * ``GET {prefix}/runs`` — recent runs (only with a ``run_store``).
    * ``GET {prefix}/runs/{run_id}/artifacts/{name}`` — download an
      artifact from a kept run (only with a ``run_store``). ``name`` may
      contain ``/``, which is how a delegated agent's artifacts are named
      (``illustrator/bike.png``).

    Example:

        >>> store = InMemoryAgentRunSink(max_runs=50)
        >>> agent = Agent(generator, tools=tools, run_sink=store)
        >>> app.include_router(
        ...     make_agent_router(agent, run_store=store, owner=current_user_id),
        ... )

    Args:
        agent (Agent): The agent to expose.
        run_store (InMemoryAgentRunSink | None): The same sink the agent
            writes to. Without it the history endpoints are not mounted,
            because there would be nothing to read — a run's artifacts
            live only as long as something holds the run.
        prefix (str): URL prefix.
        tags (list[str] | None): OpenAPI tags. Defaults to ``["agent"]``.
        owner (OwnerDependency | None): A FastAPI dependency returning the
            caller's principal id (a user or tenant id), resolved on every
            request like any ``Depends``. Each run is tagged with it
            (``AgentRun.owner``), ``GET /runs`` lists only the caller's
            runs, and an artifact of another caller's run answers ``404``
            exactly like a run that does not exist. Without it every caller
            sees every kept run — only acceptable when a single principal
            can reach the router at all.

    Returns:
        APIRouter: Ready to mount with ``app.include_router``.
    """
    router = APIRouter(prefix=prefix, tags=list(tags or ["agent"]))
    principal_of: Callable[..., Any] = owner if owner is not None else _anonymous

    @router.post("/run", response_model=AgentRunResponseSchema)
    async def run_agent(
        body: AgentRunRequestSchema,
        principal: str | None = Depends(principal_of),
    ) -> AgentRunResponseSchema:
        """Run the agent to completion and return the whole record.

        Args:
            body (AgentRunRequestSchema): The goal.
            principal (str | None): The caller, from the ``owner``
                dependency.

        Returns:
            AgentRunResponseSchema: Answer, trace, artifact metadata and
            the stop reason.
        """
        run = await agent.run(body.goal, context=AgentContext(owner=principal))
        return AgentRunResponseSchema.from_run(run)

    @router.post("/run/stream")
    async def stream_agent(
        body: AgentRunRequestSchema,
        principal: str | None = Depends(principal_of),
    ) -> StreamingResponse:
        """Stream the agent's steps as they complete.

        Each event carries one step, so a UI can show the agent working
        instead of a spinner. The stream ends when the run does.

        Args:
            body (AgentRunRequestSchema): The goal.
            principal (str | None): The caller, from the ``owner``
                dependency.

        Returns:
            StreamingResponse: An SSE stream of ``step`` events and a
            closing ``done`` event.
        """
        context = AgentContext(owner=principal)

        async def events() -> AsyncIterator[bytes]:
            """Yield one SSE frame per completed step, then a ``done`` marker.

            The trailing marker matters for a client: an agent's last step
            looks exactly like its earlier ones, so without it a UI cannot
            tell "finished" from "still thinking". It carries the run id,
            which is what the artifact URL needs.
            """
            async for step in agent.stream(body.goal, context=context):
                payload = json.dumps(step.model_dump(mode="json"))
                yield (
                    ServerSentEvent(data=payload, event="step")
                    .encode()
                    .encode(
                        "utf-8",
                    )
                )
            done = json.dumps({"run_id": context.run_id})
            yield ServerSentEvent(data=done, event="done").encode().encode("utf-8")

        return sse_response(events())

    if run_store is not None:
        store = run_store

        def visible(run: AgentRun, principal: str | None) -> bool:
            """Return whether ``principal`` may see ``run``.

            Args:
                run (AgentRun): A kept run.
                principal (str | None): The caller.

            Returns:
                bool: Always ``True`` without an ``owner`` dependency;
                otherwise only for the run's own owner.
            """
            return owner is None or run.owner == principal

        @router.get("/runs", response_model=list[AgentRunResponseSchema])
        async def list_runs(
            principal: str | None = Depends(principal_of),
            limit: int = 20,
        ) -> list[AgentRunResponseSchema]:
            """List the caller's most recent runs, newest first.

            Args:
                principal (str | None): The caller, from the ``owner``
                    dependency.
                limit (int): How many to return.

            Returns:
                list[AgentRunResponseSchema]: The kept runs this caller may
                see; empty when there are none.
            """
            mine = [run for run in store.recent() if visible(run, principal)]
            return [AgentRunResponseSchema.from_run(run) for run in mine[:limit]]

        @router.get(
            "/runs/{run_id}/artifacts/{name:path}",
            response_class=Response,
        )
        async def get_artifact(
            run_id: str,
            name: str,
            principal: str | None = Depends(principal_of),
        ) -> Response:
            """Download one artifact from a kept run.

            Args:
                run_id (str): The run's stable id.
                name (str): The artifact name; may contain ``/``.
                principal (str | None): The caller, from the ``owner``
                    dependency.

            Returns:
                Response: The bytes, with the artifact's media type.

            Raises:
                HTTPException: 404 when the run is not kept, belongs to
                    another caller, or has no such artifact — an artifact
                    is only reachable while its run is still in the buffer.
            """
            run = store.get(run_id)
            if run is None or not visible(run, principal):
                raise HTTPException(
                    status_code=status.HTTP_404_NOT_FOUND,
                    detail=f"no kept run with id {run_id!r}",
                )
            artifact = run.artifact(name)
            if artifact is None:
                raise HTTPException(
                    status_code=status.HTTP_404_NOT_FOUND,
                    detail=f"run {run_id!r} has no artifact named {name!r}",
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
    "OwnerDependency",
    "make_agent_router",
]
