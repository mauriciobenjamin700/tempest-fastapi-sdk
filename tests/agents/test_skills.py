"""Tests for skills loaded on demand."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest

from tempest_fastapi_sdk.agents import (
    Agent,
    AgentContext,
    AgentTool,
    Skill,
    discover_skills,
    load_skill_tool,
    loaded_skills,
    skill_from_markdown,
    skills_prompt,
    text_tool,
)


def _call(tool_name: str, arguments: dict[str, Any] | None = None) -> dict[str, Any]:
    """Build one tool call; arguments are passed as a dict so a tool
    argument literally named ``name`` cannot collide with the tool's own."""
    return {"function": {"name": tool_name, "arguments": arguments or {}}}


class Scripted:
    def __init__(self, replies: list[dict[str, Any]]) -> None:
        self.replies = list(replies)
        self.specs_seen: list[list[str]] = []
        self.system_prompts: list[str] = []

    async def chat_with_tools(
        self,
        messages: list[dict[str, Any]],
        specs: list[dict[str, Any]],
    ) -> dict[str, Any]:
        self.specs_seen.append([s["function"]["name"] for s in specs])
        self.system_prompts.append(messages[0]["content"])
        if not self.replies:
            return {"content": "done", "tool_calls": []}
        return self.replies.pop(0)

    async def chat(self, messages: list[dict[str, Any]]) -> str:
        return "done"


async def _noop(arguments: dict[str, Any], _context: AgentContext) -> str:
    return f"ran:{arguments.get('text', '')}"


def _skill(name: str = "invoicing", *, tools: list[AgentTool] | None = None) -> Skill:
    return Skill(
        name=name,
        description=f"Handle {name} work.",
        instructions=f"Detailed {name} guidance, long enough to matter.",
        tools=tools or [],
    )


class TestSkillShape:
    def test_summary_is_the_one_line_form(self) -> None:
        assert _skill().summary() == "- invoicing: Handle invoicing work."

    def test_body_carries_the_instructions(self) -> None:
        body = _skill().body()
        assert "Detailed invoicing guidance" in body
        assert "# Skill: invoicing" in body

    def test_body_names_the_tools_it_unlocks(self) -> None:
        skill = _skill(tools=[text_tool("parse_nfe", "Parse an NF-e.", _noop)])
        assert "Tools now available: parse_nfe." in skill.body()

    def test_body_omits_the_tool_line_when_there_are_none(self) -> None:
        assert "Tools now available" not in _skill().body()


class TestSkillsPrompt:
    def test_lists_every_skill(self) -> None:
        prompt = skills_prompt([_skill("invoicing"), _skill("payroll")])
        assert "- invoicing:" in prompt
        assert "- payroll:" in prompt
        assert "load_skill" in prompt

    def test_no_skills_means_no_prompt_change(self) -> None:
        assert skills_prompt([]) == ""

    def test_the_full_instructions_are_absent(self) -> None:
        prompt = skills_prompt([_skill()])
        assert "Detailed invoicing guidance" not in prompt


class TestLoadSkillTool:
    @pytest.mark.asyncio
    async def test_loading_returns_the_instructions(self) -> None:
        loader = load_skill_tool([_skill()])
        context = AgentContext()
        result = await loader.invoke({"name": "invoicing"}, context)
        assert "Detailed invoicing guidance" in result.text
        assert loaded_skills(context) == {"invoicing"}

    @pytest.mark.asyncio
    async def test_loading_twice_says_so_instead_of_repeating(self) -> None:
        loader = load_skill_tool([_skill()])
        context = AgentContext()
        await loader.invoke({"name": "invoicing"}, context)
        second = await loader.invoke({"name": "invoicing"}, context)
        assert "already loaded" in second.text
        assert "Detailed invoicing guidance" not in second.text

    @pytest.mark.asyncio
    async def test_an_unknown_skill_lists_the_real_ones(self) -> None:
        from tempest_fastapi_sdk.agents import AgentToolError

        loader = load_skill_tool([_skill("invoicing"), _skill("payroll")])
        with pytest.raises(AgentToolError, match="invoicing, payroll"):
            await loader.invoke({"name": "ghost"}, AgentContext())

    def test_no_skills_loaded_is_an_empty_set(self) -> None:
        assert loaded_skills(AgentContext()) == set()


class TestAgentIntegration:
    def test_the_loader_is_added_and_the_prompt_advertises(self) -> None:
        agent = Agent(Scripted([]), skills=[_skill()])
        assert "load_skill" in agent.tool_names
        assert "- invoicing:" in agent.system_prompt

    def test_an_agent_without_skills_is_untouched(self) -> None:
        agent = Agent(Scripted([]), tools=[text_tool("t", "T.", _noop)])
        assert agent.tool_names == ["t"]
        assert "load_skill" not in agent.system_prompt

    @pytest.mark.asyncio
    async def test_skill_tools_are_hidden_until_loaded(self) -> None:
        skill = _skill(tools=[text_tool("parse_nfe", "Parse an NF-e.", _noop)])
        backend = Scripted(
            [
                {
                    "content": "",
                    "tool_calls": [_call("load_skill", {"name": "invoicing"})],
                },
                {"content": "", "tool_calls": [_call("parse_nfe", {"text": "x"})]},
                {"content": "done", "tool_calls": []},
            ],
        )
        run = await Agent(backend, skills=[skill]).run("read the invoice")
        assert "parse_nfe" not in backend.specs_seen[0]
        assert "parse_nfe" in backend.specs_seen[1]
        assert run.tool_calls == ["load_skill", "parse_nfe"]
        assert run.succeeded is True

    @pytest.mark.asyncio
    async def test_calling_a_skill_tool_before_loading_fails_readably(self) -> None:
        skill = _skill(tools=[text_tool("parse_nfe", "Parse an NF-e.", _noop)])
        backend = Scripted(
            [
                {"content": "", "tool_calls": [_call("parse_nfe", {"text": "x"})]},
                {
                    "content": "",
                    "tool_calls": [_call("load_skill", {"name": "invoicing"})],
                },
                {"content": "", "tool_calls": [_call("parse_nfe", {"text": "x"})]},
                {"content": "recovered", "tool_calls": []},
            ],
        )
        run = await Agent(backend, skills=[skill]).run("go")
        failed = next(s for s in run.steps if s.error)
        assert "unknown tool" in failed.error
        assert run.output == "recovered"

    @pytest.mark.asyncio
    async def test_two_skills_only_the_loaded_one_unlocks(self) -> None:
        alpha = Skill(
            name="alpha",
            description="Alpha work.",
            tools=[text_tool("alpha_tool", "Alpha.", _noop)],
        )
        beta = Skill(
            name="beta",
            description="Beta work.",
            tools=[text_tool("beta_tool", "Beta.", _noop)],
        )
        backend = Scripted(
            [
                {"content": "", "tool_calls": [_call("load_skill", {"name": "alpha"})]},
                {"content": "done", "tool_calls": []},
            ],
        )
        await Agent(backend, skills=[alpha, beta]).run("go")
        assert "alpha_tool" in backend.specs_seen[1]
        assert "beta_tool" not in backend.specs_seen[1]

    @pytest.mark.asyncio
    async def test_loaded_skills_do_not_leak_between_runs(self) -> None:
        skill = _skill(tools=[text_tool("parse_nfe", "Parse.", _noop)])
        agent = Agent(
            Scripted(
                [
                    {
                        "content": "",
                        "tool_calls": [_call("load_skill", {"name": "invoicing"})],
                    },
                    {"content": "done", "tool_calls": []},
                ],
            ),
            skills=[skill],
        )
        await agent.run("first")
        second_backend = Scripted([{"content": "done", "tool_calls": []}])
        agent.generator = second_backend
        await agent.run("second")
        assert "parse_nfe" not in second_backend.specs_seen[0]


class TestSkillFiles:
    def test_reads_frontmatter_and_body(self, tmp_path: Path) -> None:
        path = tmp_path / "SKILL.md"
        path.write_text(
            "---\nname: invoicing\ndescription: Read NF-e.\n---\n\nThe guide.\n",
            encoding="utf-8",
        )
        skill = skill_from_markdown(path)
        assert skill.name == "invoicing"
        assert skill.description == "Read NF-e."
        assert skill.instructions == "The guide."

    def test_quotes_are_stripped(self, tmp_path: Path) -> None:
        path = tmp_path / "SKILL.md"
        path.write_text(
            "---\nname: \"quoted\"\ndescription: 'also quoted'\n---\n\nBody.\n",
            encoding="utf-8",
        )
        skill = skill_from_markdown(path)
        assert skill.name == "quoted"
        assert skill.description == "also quoted"

    def test_missing_frontmatter_is_an_error(self, tmp_path: Path) -> None:
        path = tmp_path / "SKILL.md"
        path.write_text("Just a body.\n", encoding="utf-8")
        with pytest.raises(ValueError, match="frontmatter"):
            skill_from_markdown(path)

    def test_unterminated_frontmatter_is_an_error(self, tmp_path: Path) -> None:
        path = tmp_path / "SKILL.md"
        path.write_text("---\nname: x\n", encoding="utf-8")
        with pytest.raises(ValueError, match="terminated"):
            skill_from_markdown(path)

    def test_discovery_sorts_by_name(self, tmp_path: Path) -> None:
        for name in ("zeta", "alpha"):
            folder = tmp_path / name
            folder.mkdir()
            (folder / "SKILL.md").write_text(
                f"---\nname: {name}\ndescription: {name} work.\n---\n\nBody.\n",
                encoding="utf-8",
            )
        found = discover_skills(tmp_path)
        assert [skill.name for skill in found] == ["alpha", "zeta"]

    def test_a_missing_directory_is_not_an_error(self, tmp_path: Path) -> None:
        assert discover_skills(tmp_path / "nope") == []

    def test_discovered_skills_can_be_given_tools(self, tmp_path: Path) -> None:
        folder = tmp_path / "invoicing"
        folder.mkdir()
        (folder / "SKILL.md").write_text(
            "---\nname: invoicing\ndescription: Read NF-e.\n---\n\nGuide.\n",
            encoding="utf-8",
        )
        skill = discover_skills(tmp_path)[0]
        skill.tools.append(text_tool("parse_nfe", "Parse.", _noop))
        assert "Tools now available: parse_nfe." in skill.body()
