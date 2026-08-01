"""Three kinds of agent memory, because they answer different questions.

"The agent should remember" hides three separate needs, and picking the
wrong one is why memory features disappoint. They are all here, all
opt-in, and the table below is the whole decision:

* **Scratchpad** — lives for one run. Park a finding so a later step can
  use it without re-deriving or re-reading it.
* **Facts** — durable and editable. Something is *true* and should stay
  true: a preference, an account id, a policy. You want to read and
  correct it outside the model.
* **Recall** — durable and fuzzy. Past conversations might be relevant
  and you cannot know in advance which; semantic search decides.

The distinction that matters most is **facts vs recall**. A fact is
asserted and exact: you can list facts, edit one, delete one, and show a
user what the system believes about them. Recall is retrieved and
approximate: it surfaces text that *looks* related, which is powerful and
unauditable. Storing "the user's plan is enterprise" in recall means
nobody can correct it; storing a whole conversation as a fact means
nothing useful comes back.

Each layer is a set of tools you add to an agent, so the model reaches
for memory deliberately rather than everything being injected always:

    >>> agent = Agent(
    ...     generator,
    ...     tools=[
    ...         *scratchpad_tools(),
    ...         *fact_tools(InMemoryFactStore()),
    ...     ],
    ... )

Recall additionally injects what it finds into the system prompt, because
by the time the model knows to ask, it has usually already answered.
"""

from __future__ import annotations

import time
from typing import Any, Protocol, runtime_checkable

from pydantic import Field

from tempest_fastapi_sdk.agents.tools import AgentContext, AgentTool, AgentToolError
from tempest_fastapi_sdk.schemas.base import BaseSchema

_SCRATCH_KEY: str = "__scratchpad__"
"""Where the scratchpad lives on the run context."""


class Fact(BaseSchema):
    """One durable, editable thing the agent knows.

    Attributes:
        key (str): Stable identifier — ``"plan"``, ``"timezone"``.
        value (str): What is believed.
        subject (str | None): Who or what it is about, when the store
            holds facts for more than one subject (a user id, a tenant).
        updated_at (float): Unix timestamp of the last write.
    """

    key: str = Field(
        title="Key",
        description="Stable identifier for the fact.",
        examples=["timezone"],
    )
    value: str = Field(
        title="Value",
        description="What is believed.",
        examples=["America/Recife"],
    )
    subject: str | None = Field(
        default=None,
        title="Subject",
        description="Who or what the fact is about.",
        examples=["user-42"],
    )
    updated_at: float = Field(
        default=0.0,
        title="Updated at",
        description="Unix timestamp of the last write.",
        examples=[1_754_000_000.0],
    )


@runtime_checkable
class FactStore(Protocol):
    """Where durable facts live.

    Deliberately tiny: four operations, all keyed. Anything that can do
    those is a valid store — a dict, a table, Redis, your own settings
    service. The point of facts is that they are *addressable*, so the
    interface stays addressable too.
    """

    async def get(self, key: str, *, subject: str | None = None) -> Fact | None:
        """Return one fact, or ``None``."""
        ...

    async def put(self, key: str, value: str, *, subject: str | None = None) -> Fact:
        """Write one fact and return it."""
        ...

    async def forget(self, key: str, *, subject: str | None = None) -> bool:
        """Delete one fact; return whether it existed."""
        ...

    async def all(self, *, subject: str | None = None) -> list[Fact]:
        """Return every fact for a subject, sorted by key."""
        ...


class InMemoryFactStore:
    """A :class:`FactStore` backed by a dict.

    For tests, single-process services and getting started. Facts vanish
    on restart, which is the one thing durable facts are supposed not to
    do — swap it for a database-backed store before it matters.
    """

    def __init__(self) -> None:
        """Create an empty store."""
        self._facts: dict[tuple[str | None, str], Fact] = {}

    async def get(self, key: str, *, subject: str | None = None) -> Fact | None:
        """Return one fact, or ``None`` when it was never written.

        Args:
            key (str): The fact's key.
            subject (str | None): Whose fact.

        Returns:
            Fact | None: The stored fact.
        """
        return self._facts.get((subject, key))

    async def put(self, key: str, value: str, *, subject: str | None = None) -> Fact:
        """Write one fact, replacing any previous value.

        Args:
            key (str): The fact's key.
            value (str): What to believe.
            subject (str | None): Whose fact.

        Returns:
            Fact: The stored fact.
        """
        fact = Fact(key=key, value=value, subject=subject, updated_at=time.time())
        self._facts[(subject, key)] = fact
        return fact

    async def forget(self, key: str, *, subject: str | None = None) -> bool:
        """Delete one fact.

        Args:
            key (str): The fact's key.
            subject (str | None): Whose fact.

        Returns:
            bool: Whether a fact was removed.
        """
        return self._facts.pop((subject, key), None) is not None

    async def all(self, *, subject: str | None = None) -> list[Fact]:
        """Return every fact for ``subject``, sorted by key.

        Args:
            subject (str | None): Whose facts.

        Returns:
            list[Fact]: The facts; empty when there are none.
        """
        found = [
            fact for (owner, _key), fact in self._facts.items() if owner == subject
        ]
        return sorted(found, key=lambda fact: fact.key)


def scratchpad_tools(*, prefix: str = "note") -> list[AgentTool]:
    """Build the within-run scratchpad tools.

    A long run derives things — a total, a file path, a decision — several
    steps before it needs them. Without somewhere to park them the model
    either re-derives (slow, and the second answer may differ) or carries
    them in the conversation, where they compete with everything else for
    attention.

    The scratchpad lives on :attr:`AgentContext.state`, so it disappears
    when the run ends. That is the feature: a note from an unrelated run
    turning up mid-task is worse than no notes at all.

    Args:
        prefix (str): Prefix for the tool names — ``note_write`` /
            ``note_read`` / ``note_list`` by default.

    Returns:
        list[AgentTool]: Three tools: write, read, list.
    """

    async def write(
        arguments: dict[str, Any],
        context: AgentContext,
    ) -> str:
        """Store one note under a key."""
        key = str(arguments.get("key", "")).strip()
        if not key:
            raise AgentToolError("'key' is required")
        notes: dict[str, str] = context.state.setdefault(_SCRATCH_KEY, {})
        notes[key] = str(arguments.get("value", ""))
        return f"Noted '{key}'."

    async def read(
        arguments: dict[str, Any],
        context: AgentContext,
    ) -> str:
        """Return one note."""
        key = str(arguments.get("key", "")).strip()
        notes: dict[str, str] = context.state.get(_SCRATCH_KEY, {})
        if key not in notes:
            known = ", ".join(sorted(notes)) or "none"
            raise AgentToolError(f"no note named {key!r}; notes so far: {known}")
        return notes[key]

    async def index(
        _arguments: dict[str, Any],
        context: AgentContext,
    ) -> str:
        """List the note keys taken so far."""
        notes: dict[str, str] = context.state.get(_SCRATCH_KEY, {})
        return ", ".join(sorted(notes)) if notes else "No notes yet."

    key_schema = {
        "type": "object",
        "properties": {"key": {"type": "string", "description": "The note's name."}},
        "required": ["key"],
    }
    return [
        AgentTool(
            name=f"{prefix}_write",
            description=(
                "Save a short note under a name so a later step can use it "
                "without working it out again."
            ),
            parameters={
                "type": "object",
                "properties": {
                    "key": {"type": "string", "description": "Name for the note."},
                    "value": {"type": "string", "description": "What to remember."},
                },
                "required": ["key", "value"],
            },
            handler=write,
        ),
        AgentTool(
            name=f"{prefix}_read",
            description="Read back a note saved earlier in this task.",
            parameters=key_schema,
            handler=read,
        ),
        AgentTool(
            name=f"{prefix}_list",
            description="List the names of the notes saved so far.",
            parameters={"type": "object", "properties": {}},
            handler=index,
        ),
    ]


def scratchpad(context: AgentContext) -> dict[str, str]:
    """Return the notes taken during a run.

    Args:
        context (AgentContext): The run context.

    Returns:
        dict[str, str]: The notes, empty when none were taken.
    """
    notes = context.state.get(_SCRATCH_KEY)
    return dict(notes) if notes else {}


def fact_tools(
    store: FactStore,
    *,
    subject: str | None = None,
    prefix: str = "fact",
    allow_forget: bool = True,
) -> list[AgentTool]:
    """Build the durable-fact tools over ``store``.

    Facts are what you reach for when something is **true** and should
    stay true: a preference, an account id, a policy the agent must not
    re-ask about. Because they are keyed, you can list them, correct one,
    and show a user exactly what the system believes — none of which is
    possible with semantic recall.

    Args:
        store (FactStore): Where facts live.
        subject (str | None): Whose facts this agent reads and writes.
            Pass the user or tenant id in a multi-tenant service; leaving
            it ``None`` gives one shared namespace, which is right for a
            single-purpose agent and wrong for anything per-user.
        prefix (str): Prefix for the tool names.
        allow_forget (bool): Include the delete tool. Turn it off when
            facts are curated elsewhere and the model should only read —
            a model that can delete what it disagrees with will.

    Returns:
        list[AgentTool]: Remember, recall-by-key, list, and optionally
        forget.
    """

    async def remember(
        arguments: dict[str, Any],
        _context: AgentContext,
    ) -> str:
        """Write one durable fact."""
        key = str(arguments.get("key", "")).strip()
        value = str(arguments.get("value", "")).strip()
        if not key or not value:
            raise AgentToolError("'key' and 'value' are both required")
        await store.put(key, value, subject=subject)
        return f"Remembered {key!r}."

    async def lookup(
        arguments: dict[str, Any],
        _context: AgentContext,
    ) -> str:
        """Read one durable fact by key."""
        key = str(arguments.get("key", "")).strip()
        fact = await store.get(key, subject=subject)
        if fact is None:
            known = ", ".join(item.key for item in await store.all(subject=subject))
            raise AgentToolError(
                f"nothing remembered for {key!r}; known: {known or 'nothing'}",
            )
        return fact.value

    async def index(
        _arguments: dict[str, Any],
        _context: AgentContext,
    ) -> str:
        """List every remembered fact."""
        facts = await store.all(subject=subject)
        if not facts:
            return "Nothing remembered yet."
        return "\n".join(f"{fact.key}: {fact.value}" for fact in facts)

    async def forget(
        arguments: dict[str, Any],
        _context: AgentContext,
    ) -> str:
        """Delete one durable fact."""
        key = str(arguments.get("key", "")).strip()
        removed = await store.forget(key, subject=subject)
        return f"Forgot {key!r}." if removed else f"Nothing was stored for {key!r}."

    key_schema = {
        "type": "object",
        "properties": {"key": {"type": "string", "description": "The fact's name."}},
        "required": ["key"],
    }
    tools = [
        AgentTool(
            name=f"{prefix}_remember",
            description=(
                "Remember something durably, across conversations. Use it "
                "for stable truths like preferences or identifiers, not for "
                "passing notes within this task."
            ),
            parameters={
                "type": "object",
                "properties": {
                    "key": {"type": "string", "description": "Short name."},
                    "value": {"type": "string", "description": "What is true."},
                },
                "required": ["key", "value"],
            },
            handler=remember,
        ),
        AgentTool(
            name=f"{prefix}_recall",
            description="Look up something remembered earlier, by name.",
            parameters=key_schema,
            handler=lookup,
        ),
        AgentTool(
            name=f"{prefix}_list",
            description="List everything remembered durably.",
            parameters={"type": "object", "properties": {}},
            handler=index,
        ),
    ]
    if allow_forget:
        tools.append(
            AgentTool(
                name=f"{prefix}_forget",
                description="Delete something remembered, when it is wrong.",
                parameters=key_schema,
                handler=forget,
            ),
        )
    return tools


async def facts_prompt(
    store: FactStore,
    *,
    subject: str | None = None,
    heading: str = "What you already know",
) -> str:
    """Render the stored facts as a system-prompt block.

    Injecting facts beats making the model look them up: by the time it
    knows to ask about a timezone it has usually already answered in the
    wrong one. Facts are short and few, so the prompt cost is small and
    the payoff is that the model cannot fail to consult them.

    Args:
        store (FactStore): Where facts live.
        subject (str | None): Whose facts.
        heading (str): Heading for the block.

    Returns:
        str: The block, or an empty string when nothing is stored — so an
        agent with an empty store keeps exactly the prompt it had.
    """
    facts = await store.all(subject=subject)
    if not facts:
        return ""
    lines = "\n".join(f"- {fact.key}: {fact.value}" for fact in facts)
    return f"\n\n{heading}:\n{lines}"


async def recall_prompt(
    memory: Any,
    query: str,
    *,
    user_id: str,
    top_k: int = 4,
    heading: str = "Possibly relevant from earlier conversations",
) -> str:
    """Render semantic recall as a system-prompt block.

    Wraps a :class:`~tempest_fastapi_sdk.genai.rag.ChatMemory` (or
    anything with ``recall``) so the agent starts a run already holding
    what past conversations might contribute. Recall is **fuzzy**: it
    surfaces text that looks related, which is why it belongs behind a
    heading that says "possibly" rather than being presented as fact.

    Args:
        memory (Any): A ``ChatMemory``-shaped object.
        query (str): The goal, used as the search query.
        user_id (str): Whose history to search.
        top_k (int): How many hits to include.
        heading (str): Heading for the block.

    Returns:
        str: The block, or an empty string when nothing is found or the
        memory backend fails — recall is an enhancement, and a search
        outage must not stop the agent from working.
    """
    try:
        hits = await memory.recall(query, user_id=user_id, top_k=top_k)
    except Exception:
        return ""
    if not hits:
        return ""
    lines = "\n".join(f"- {getattr(hit, 'content', hit)}" for hit in hits)
    return f"\n\n{heading}:\n{lines}"


__all__: list[str] = [
    "Fact",
    "FactStore",
    "InMemoryFactStore",
    "fact_tools",
    "facts_prompt",
    "recall_prompt",
    "scratchpad",
    "scratchpad_tools",
]
