"""Where finished agent runs go — nowhere, memory, or a table.

An agent that leaves no record is fine for a one-off script and useless in
production: when someone asks why the agent answered what it did, the
trace is the answer. But persistence is a decision with a migration
attached, so it stays **opt-in**:

* no sink (the default) — the run is returned to the caller and forgotten;
* :class:`InMemoryAgentRunSink` — a bounded ring buffer, enough to serve a
  "last N runs" panel without a database;
* :class:`DbAgentRunSink` over :class:`BaseAgentRunModel` — one row per
  run, for keeping them.

Anything ``async``-callable taking an :class:`~tempest_fastapi_sdk.agents.AgentRun`
is a valid sink, so routing to a log, a queue or an object store is a
lambda away.

The SQLAlchemy pieces import with no extra: the ORM ships in the SDK's base
dependencies.
"""

from __future__ import annotations

from collections import deque
from typing import TYPE_CHECKING, Any, Protocol, runtime_checkable

from sqlalchemy import JSON, Float, Integer, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from tempest_fastapi_sdk.agents.schemas import AgentRun
from tempest_fastapi_sdk.db.model import BaseModel

if TYPE_CHECKING:
    from tempest_fastapi_sdk.db.connection import AsyncDatabaseManager


@runtime_checkable
class AgentRunSink(Protocol):
    """Where a finished run goes.

    Called once per run, after the answer is assembled. The agent
    swallows whatever this raises — a storage failure must not turn a
    completed run into a failed request.
    """

    async def __call__(self, run: AgentRun) -> None:
        """Handle one finished run.

        Args:
            run (AgentRun): The completed run.
        """
        ...


class InMemoryAgentRunSink:
    """Keep the last N runs in process memory.

    Bounded on purpose: runs carry their artifacts, so an unbounded list
    of image-generating runs is a memory leak with a slow fuse. When the
    buffer is full the oldest run is dropped.

    Example:

        >>> sink = InMemoryAgentRunSink(max_runs=50)
        >>> agent = Agent(generator, run_sink=sink)
        >>> await agent.run("...")
        >>> sink.recent(5)

    Attributes:
        max_runs (int): How many runs to keep.
    """

    def __init__(self, max_runs: int = 100) -> None:
        """Configure the buffer.

        Args:
            max_runs (int): How many runs to keep before dropping the
                oldest.

        Raises:
            ValueError: When ``max_runs`` is not positive.
        """
        if max_runs <= 0:
            raise ValueError("max_runs must be positive")
        self.max_runs = max_runs
        self._runs: deque[AgentRun] = deque(maxlen=max_runs)

    async def __call__(self, run: AgentRun) -> None:
        """Store one finished run.

        Args:
            run (AgentRun): The completed run.
        """
        self._runs.append(run)

    def recent(self, limit: int | None = None) -> list[AgentRun]:
        """Return the most recent runs, newest first.

        Args:
            limit (int | None): How many to return; ``None`` for all kept.

        Returns:
            list[AgentRun]: The runs, newest first.
        """
        runs = list(reversed(self._runs))
        return runs if limit is None else runs[:limit]

    def clear(self) -> None:
        """Drop every kept run."""
        self._runs.clear()

    def __len__(self) -> int:
        """Return how many runs are kept.

        Returns:
            int: The buffer size.
        """
        return len(self._runs)


class BaseAgentRunModel(BaseModel):
    """One agent run, persisted for audit and debugging.

    Abstract — subclass it (setting ``__tablename__``) in the project, or
    build a concrete class with :func:`make_agent_run_model`. Inherits the
    SDK base columns (``id`` / ``is_active`` / ``created_at`` /
    ``updated_at``); ``created_at`` is when the run was recorded.

    Artifacts are **not** stored: they are bytes, often megabytes of them,
    and a run table is not a blob store. What is kept is their names and
    media types, so a reader knows what was produced and can look for it
    wherever the caller put it.
    """

    __abstract__ = True

    agent: Mapped[str] = mapped_column(
        String(255),
        index=True,
        doc="The agent's name.",
    )
    goal: Mapped[str] = mapped_column(
        Text,
        doc="What the agent was asked to do.",
    )
    output: Mapped[str] = mapped_column(
        Text,
        default="",
        doc="The final answer.",
    )
    stop_reason: Mapped[str] = mapped_column(
        String(32),
        index=True,
        doc="Why the run ended.",
    )
    seconds: Mapped[float] = mapped_column(
        Float,
        default=0.0,
        doc="Total wall-clock duration.",
    )
    step_count: Mapped[int] = mapped_column(
        Integer,
        default=0,
        doc="How many steps the trace holds.",
    )
    steps: Mapped[list[dict[str, Any]]] = mapped_column(
        JSON,
        default=list,
        doc="The trace, as JSON.",
    )
    artifact_names: Mapped[list[str]] = mapped_column(
        JSON,
        default=list,
        doc="Names of the artifacts the run produced.",
    )

    @classmethod
    def from_run(cls, run: AgentRun) -> BaseAgentRunModel:
        """Build a row from a finished run.

        Args:
            run (AgentRun): The completed run.

        Returns:
            BaseAgentRunModel: The unsaved row.
        """
        return cls(
            agent=run.agent,
            goal=run.goal,
            output=run.output,
            stop_reason=str(run.stop_reason),
            seconds=run.seconds,
            step_count=len(run.steps),
            steps=[step.model_dump(mode="json") for step in run.steps],
            artifact_names=[artifact.name for artifact in run.artifacts],
        )


def make_agent_run_model(
    *,
    tablename: str = "agent_runs",
    class_name: str = "AgentRunModel",
) -> type[BaseAgentRunModel]:
    """Build a concrete agent-run model bound to ``tablename``.

    For tests and lightweight scripts; production code should subclass
    :class:`BaseAgentRunModel` by hand so migrations pick it up statically.

    Args:
        tablename (str): The table name.
        class_name (str): The generated class name.

    Returns:
        type[BaseAgentRunModel]: The concrete model class.
    """
    return type(
        class_name,
        (BaseAgentRunModel,),
        {"__tablename__": tablename},
    )


class DbAgentRunSink:
    """An :class:`AgentRunSink` that writes each run to the database.

    Example:

        >>> model = make_agent_run_model()
        >>> agent = Agent(generator, run_sink=DbAgentRunSink(db, model))

    Attributes:
        model (type[BaseAgentRunModel]): The concrete run table.
    """

    def __init__(
        self,
        db: AsyncDatabaseManager,
        model: type[BaseAgentRunModel],
    ) -> None:
        """Configure the sink.

        Args:
            db (AsyncDatabaseManager): The database manager; a short
                transaction is opened per run.
            model (type[BaseAgentRunModel]): The concrete run table.
        """
        self._db = db
        self.model = model

    async def __call__(self, run: AgentRun) -> None:
        """Insert one run as a row (commits on clean exit).

        Args:
            run (AgentRun): The completed run.
        """
        async with self._db.get_session_context() as session:
            session.add(self.model.from_run(run))


__all__: list[str] = [
    "AgentRunSink",
    "BaseAgentRunModel",
    "DbAgentRunSink",
    "InMemoryAgentRunSink",
    "make_agent_run_model",
]
