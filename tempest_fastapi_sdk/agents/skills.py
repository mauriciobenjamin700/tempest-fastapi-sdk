"""Capabilities the agent loads only when it decides to use them.

Every tool an agent can call sits in its prompt, and every line there
costs context and dilutes attention. Ten well-documented capabilities —
each with its conventions, its edge cases, its worked example — is more
instruction than a small local model can hold, and quality drops on all
ten.

A **skill** solves that by splitting what the model needs to *choose*
from what it needs to *do*:

* the **name and one-line description** are always in the prompt, which
  is all it takes to decide the skill is relevant;
* the **full instructions and the skill's own tools** arrive only after
  the model loads it.

So a hundred capabilities cost a hundred short lines, and the one being
used gets the whole page.

    >>> invoicing = Skill(
    ...     name="invoicing",
    ...     description="Read and validate Brazilian invoices (NF-e).",
    ...     instructions=INVOICE_GUIDE,      # as long as it needs to be
    ...     tools=[parse_nfe, validate_cnpj],
    ... )
    >>> agent = Agent(generator, skills=[invoicing])

This is the same progressive-disclosure shape Claude Code uses for its
own skills, for the same reason: the limit is attention, not storage.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from tempest_fastapi_sdk.agents.schemas import ToolResult
from tempest_fastapi_sdk.agents.tools import AgentContext, AgentTool, AgentToolError

LOAD_SKILL_TOOL: str = "load_skill"
"""Name of the tool an agent calls to open a skill."""

_LOADED_KEY: str = "__loaded_skills__"
"""Where the run context records which skills are already open."""


@dataclass
class Skill:
    """A named capability with instructions and tools of its own.

    Attributes:
        name (str): Short identifier the model uses to load it. Keep it
            lowercase and specific — it appears in every prompt.
        description (str): One line saying when this skill applies. This
            is the **only** text the model sees before loading, so it has
            to be enough to choose on: say what the skill is *for*, not
            how it works.
        instructions (str): The full guidance, revealed on load. Length is
            not a concern here — that is the point of the split.
        tools (list[AgentTool]): Tools that become callable once loaded.
            They are hidden until then, so their names and schemas cost
            nothing while unused.
    """

    name: str
    description: str
    instructions: str = ""
    tools: list[AgentTool] = field(default_factory=list)

    def summary(self) -> str:
        """Return the one-line entry shown in the system prompt.

        Returns:
            str: ``"- name: description"``.
        """
        return f"- {self.name}: {self.description}"

    def body(self) -> str:
        """Return what the model receives when it loads this skill.

        Returns:
            str: The instructions plus, when the skill carries tools, a
            note naming them — a model that just loaded a skill needs to
            be told the tools exist, since they were absent from the
            prompt a moment ago.
        """
        parts = [f"# Skill: {self.name}", self.description]
        if self.instructions:
            parts.append(self.instructions)
        if self.tools:
            names = ", ".join(tool.name for tool in self.tools)
            parts.append(f"Tools now available: {names}.")
        return "\n\n".join(parts)


def load_skill_tool(
    skills: Sequence[Skill],
    *,
    name: str = LOAD_SKILL_TOOL,
) -> AgentTool:
    """Build the tool an agent calls to open one of its skills.

    Args:
        skills (Sequence[Skill]): The available skills.
        name (str): The tool name.

    Returns:
        AgentTool: The loader. Loading twice is a no-op that says so, so a
        model that forgets it already read a skill does not waste a step
        re-reading it.
    """
    by_name = {skill.name: skill for skill in skills}

    async def handler(
        arguments: dict[str, Any],
        context: AgentContext,
    ) -> ToolResult:
        """Return the requested skill's full instructions."""
        wanted = str(arguments.get("name", "")).strip()
        skill = by_name.get(wanted)
        if skill is None:
            available = ", ".join(sorted(by_name)) or "none"
            raise AgentToolError(
                f"no skill named {wanted!r}; available: {available}",
            )
        loaded: set[str] = context.state.setdefault(_LOADED_KEY, set())
        if wanted in loaded:
            return ToolResult(text=f"Skill '{wanted}' is already loaded.")
        loaded.add(wanted)
        return ToolResult(text=skill.body())

    return AgentTool(
        name=name,
        description=(
            "Load a skill's full instructions before doing work that needs "
            "it. Load only what the task requires."
        ),
        parameters={
            "type": "object",
            "properties": {
                "name": {
                    "type": "string",
                    "description": "The skill to load.",
                },
            },
            "required": ["name"],
        },
        handler=handler,
    )


def skills_prompt(skills: Sequence[Skill], *, tool_name: str = LOAD_SKILL_TOOL) -> str:
    """Return the system-prompt block advertising the available skills.

    Args:
        skills (Sequence[Skill]): The available skills.
        tool_name (str): Name of the loader tool.

    Returns:
        str: The block to append to the agent's system prompt, or an empty
        string when there are no skills (so an agent without skills keeps
        exactly the prompt it had).
    """
    if not skills:
        return ""
    lines = "\n".join(skill.summary() for skill in skills)
    return (
        "\n\nYou have skills available. Each is a set of instructions and "
        f"tools you can load with the '{tool_name}' tool when the task "
        "needs it:\n"
        f"{lines}\n"
        "Load a skill before doing work that falls under it. Do not load "
        "skills you do not need."
    )


def loaded_skills(context: AgentContext) -> set[str]:
    """Return the names of the skills loaded during this run.

    Useful in a trace or a test: it says which capabilities the agent
    actually reached for, which is usually the first thing you want to
    know when it took a wrong turn.

    Args:
        context (AgentContext): The run context.

    Returns:
        set[str]: The loaded skill names; empty when none were.
    """
    loaded = context.state.get(_LOADED_KEY)
    return set(loaded) if loaded else set()


def skill_from_markdown(path: str | Path) -> Skill:
    """Read a skill from a Markdown file with YAML-ish frontmatter.

    The format is the one Claude Code uses, so the same file works in both
    places:

    ```markdown
    ---
    name: invoicing
    description: Read and validate Brazilian invoices (NF-e).
    ---

    Full instructions go here, as long as they need to be.
    ```

    Only ``name`` and ``description`` are read from the frontmatter; the
    body becomes the instructions. Tools cannot come from a file — they
    are Python — so attach them afterwards with
    ``skill.tools.append(...)``.

    Args:
        path (str | Path): The Markdown file.

    Returns:
        Skill: The parsed skill.

    Raises:
        FileNotFoundError: When the file does not exist.
        ValueError: When the frontmatter is missing or has no ``name``.
    """
    file = Path(path)
    text = file.read_text(encoding="utf-8")
    if not text.startswith("---"):
        raise ValueError(f"{file}: expected YAML frontmatter starting with '---'")
    _, _, rest = text.partition("---")
    front, separator, body = rest.partition("---")
    if not separator:
        raise ValueError(f"{file}: frontmatter is not terminated with '---'")

    meta: dict[str, str] = {}
    for line in front.splitlines():
        key, colon, value = line.partition(":")
        if colon:
            meta[key.strip()] = value.strip().strip("'\"")

    name = meta.get("name") or file.parent.name
    if not name:
        raise ValueError(f"{file}: frontmatter has no 'name'")
    return Skill(
        name=name,
        description=meta.get("description", ""),
        instructions=body.strip(),
    )


def discover_skills(
    directory: str | Path, *, pattern: str = "*/SKILL.md"
) -> list[Skill]:
    """Load every skill file under ``directory``, sorted by name.

    Lets a deployment add a capability by dropping in a file, without a
    code change — the counterpart to defining skills in Python when they
    need tools.

    Args:
        directory (str | Path): The directory to scan.
        pattern (str): Glob for the skill files, relative to ``directory``.

    Returns:
        list[Skill]: The skills found, sorted by name. Empty when the
        directory does not exist — a missing skills directory is a valid
        state, not an error, so a service starts fine without one.
    """
    root = Path(directory)
    if not root.is_dir():
        return []
    found = [skill_from_markdown(path) for path in sorted(root.glob(pattern))]
    return sorted(found, key=lambda skill: skill.name)


__all__: list[str] = [
    "LOAD_SKILL_TOOL",
    "Skill",
    "discover_skills",
    "load_skill_tool",
    "loaded_skills",
    "skill_from_markdown",
    "skills_prompt",
]
