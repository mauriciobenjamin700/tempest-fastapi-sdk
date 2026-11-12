"""Agent tools that look things up in a known list: names and municipalities.

* :func:`name_search_tool` — search any name index the service built: a
  :class:`~tempest_fastapi_sdk.text.FuzzyIndex` (spelling), a
  :class:`~tempest_fastapi_sdk.genai.rag.SemanticNameIndex` (meaning) or a
  :class:`~tempest_fastapi_sdk.genai.rag.HybridNameIndex` (both).
* :func:`municipality_search_tool` — find the Brazilian municipality a text
  mentions, with its state, IBGE code and coordinate, offline.

Both return plain text the model reads, one match per line, and tell the
model when nothing matched instead of returning an empty string.
"""

from __future__ import annotations

import inspect
from typing import Any

from tempest_fastapi_sdk.agents.tools import AgentContext, AgentTool, AgentToolError


def _score_note(match: Any) -> str:
    """Describe the scores a name match carries.

    Args:
        match (Any): A ``FuzzyMatch`` or ``NameMatch``.

    Returns:
        str: ``"spelling 0.93, meaning 0.79"``, or just the score.
    """
    parts: list[str] = []
    lexical = getattr(match, "lexical_score", None)
    semantic = getattr(match, "semantic_score", None)
    if lexical is not None:
        parts.append(f"spelling {lexical:.2f}")
    if semantic is not None:
        parts.append(f"meaning {semantic:.2f}")
    return ", ".join(parts) or f"score {match.score:.2f}"


def name_search_tool(
    index: Any,
    *,
    name: str = "search_names",
    description: str = (
        "Find entries of a known list whose name matches what the user "
        "wrote, tolerating typos (and synonyms when the list is indexed by "
        "meaning). Returns one entry per line: name, value and why it matched."
    ),
    limit: int = 5,
) -> AgentTool:
    """Build a tool that searches a name index.

    Args:
        index (Any): A ``FuzzyIndex``, ``SemanticNameIndex`` or
            ``HybridNameIndex`` (anything with ``search(query, limit=...)``,
            sync or ``async``, returning matches with ``key``, ``value`` and
            ``score``).
        name (str): The function name the model calls.
        description (str): What the tool does, written for the model. Say
            what the list holds (products, cities, categories): it is the
            only text steering the model to the tool.
        limit (int): Matches to return when the model does not say.

    Returns:
        AgentTool: The search tool.
    """

    async def handler(arguments: dict[str, Any], _context: AgentContext) -> str:
        """Search the index and render one match per line.

        Args:
            arguments (dict[str, Any]): ``query`` and optional ``limit``.
            _context (AgentContext): Unused.

        Returns:
            str: The matches, or a sentence saying there were none.

        Raises:
            AgentToolError: When ``query`` is missing.
        """
        query = str(arguments.get("query", "")).strip()
        if not query:
            raise AgentToolError("'query' is required")
        requested = arguments.get("limit")
        found = index.search(query, limit=int(requested) if requested else limit)
        if inspect.isawaitable(found):
            found = await found
        if not found:
            return f"No entry matches {query!r}."
        return "\n".join(
            f"{match.key} -> {match.value} ({_score_note(match)})" for match in found
        )

    return AgentTool(
        name=name,
        description=description,
        parameters={
            "type": "object",
            "properties": {
                "query": {
                    "type": "string",
                    "description": "The name as the user wrote it.",
                },
                "limit": {
                    "type": "integer",
                    "description": "How many matches to return.",
                },
            },
            "required": ["query"],
        },
        handler=handler,
    )


def municipality_search_tool(
    *,
    name: str = "find_brazilian_city",
    description: str = (
        "Find the Brazilian city (municipality) a text mentions, with its "
        "state, IBGE code and coordinates. Pass the user's sentence as it "
        "was written, or just the city name; pass 'uf' when the state is "
        "known. When several cities share the name and no state is given, "
        "all are returned and you should ask the user which one."
    ),
    limit: int = 5,
) -> AgentTool:
    """Build a tool that finds Brazilian municipalities in a text.

    Uses :func:`~tempest_fastapi_sdk.geo.find_municipalities_in_text` on the
    text; when it finds nothing (or a state is given), falls back to
    :func:`~tempest_fastapi_sdk.geo.search_municipalities` on the whole text
    as a name. Offline: no model and no network.

    Args:
        name (str): The function name the model calls.
        description (str): What the tool does, written for the model.
        limit (int): Matches to return.

    Returns:
        AgentTool: The municipality tool.
    """
    from tempest_fastapi_sdk.geo import (
        find_municipalities_in_text,
        search_municipalities,
    )

    async def handler(arguments: dict[str, Any], _context: AgentContext) -> str:
        """Find the municipalities and render one per line.

        Args:
            arguments (dict[str, Any]): ``text`` and optional ``uf``.
            _context (AgentContext): Unused.

        Returns:
            str: The municipalities, or a sentence saying there were none.

        Raises:
            AgentToolError: When ``text`` is missing or ``uf`` is not a
                Brazilian state.
        """
        text = str(arguments.get("text", "")).strip()
        if not text:
            raise AgentToolError("'text' is required")
        uf = str(arguments.get("uf") or "").strip() or None
        try:
            matches = (
                [] if uf is not None else find_municipalities_in_text(text, limit=limit)
            )
            if not matches:
                matches = search_municipalities(text, uf=uf, limit=limit)
        except ValueError as exc:
            raise AgentToolError(f"'uf' must be a Brazilian state: {uf!r}") from exc
        if not matches:
            return f"No Brazilian municipality found in {text!r}."
        lines = []
        for match in matches:
            city = match.municipality
            confirmed = "state confirmed" if match.uf_confirmed else "state not given"
            lines.append(
                f"{city.name}/{city.uf.value} — IBGE {city.ibge_code}, "
                f"lat {city.coordinate.latitude}, lon {city.coordinate.longitude}"
                f" (score {match.score:.2f}, {confirmed})"
            )
        return "\n".join(lines)

    return AgentTool(
        name=name,
        description=description,
        parameters={
            "type": "object",
            "properties": {
                "text": {
                    "type": "string",
                    "description": "The user's sentence, or the city name.",
                },
                "uf": {
                    "type": "string",
                    "description": "Two-letter state, when known (e.g. SP).",
                },
            },
            "required": ["text"],
        },
        handler=handler,
    )


__all__: list[str] = [
    "municipality_search_tool",
    "name_search_tool",
]
