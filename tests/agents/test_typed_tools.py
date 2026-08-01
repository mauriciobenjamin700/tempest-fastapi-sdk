"""Tests for Pydantic-typed tools and structured agent output."""

from __future__ import annotations

from typing import Any

import pytest
from pydantic import Field

from tempest_fastapi_sdk.agents import (
    Agent,
    AgentBudget,
    AgentContext,
    AgentToolError,
    StopReason,
    StructuredRun,
    final_answer_tool,
    run_structured,
    run_until,
    schema_of,
    structured_verdict,
    tool,
    typed_tool,
)
from tempest_fastapi_sdk.schemas import BaseSchema


class WeatherArgs(BaseSchema):
    """Arguments for the weather tool."""

    city: str = Field(description="City to look up.")
    days: int = Field(default=1, ge=1, le=7, description="Forecast horizon.")


class Coordinates(BaseSchema):
    """A latitude/longitude pair."""

    lat: float
    lon: float


class NestedArgs(BaseSchema):
    """Arguments carrying a nested model."""

    label: str
    at: Coordinates


class Summary(BaseSchema):
    """A structured answer."""

    headline: str = Field(description="One-line summary.")
    bullets: list[str] = Field(default_factory=list)


def _call(name: str, **arguments: Any) -> dict[str, Any]:
    return {"function": {"name": name, "arguments": arguments}}


class Scripted:
    def __init__(self, replies: list[dict[str, Any]], *, repeat: bool = False) -> None:
        self.replies = list(replies)
        self.repeat = repeat
        self.specs_seen: list[list[dict[str, Any]]] = []
        self.prompts: list[str] = []

    async def chat_with_tools(
        self,
        messages: list[dict[str, Any]],
        specs: list[dict[str, Any]],
    ) -> dict[str, Any]:
        self.specs_seen.append(specs)
        self.prompts.append(messages[0]["content"])
        if not self.replies:
            return {"content": "done", "tool_calls": []}
        if self.repeat and len(self.replies) == 1:
            return self.replies[0]
        return self.replies.pop(0)

    async def chat(self, messages: list[dict[str, Any]]) -> str:
        return "done"


class TestSchemaOf:
    def test_produces_a_plain_object_schema(self) -> None:
        schema = schema_of(WeatherArgs)
        assert schema["type"] == "object"
        assert "city" in schema["properties"]
        assert schema["required"] == ["city"]
        assert "title" not in schema

    def test_field_descriptions_survive(self) -> None:
        schema = schema_of(WeatherArgs)
        assert schema["properties"]["city"]["description"] == "City to look up."

    def test_constraints_survive(self) -> None:
        days = schema_of(WeatherArgs)["properties"]["days"]
        assert days["minimum"] == 1
        assert days["maximum"] == 7

    def test_nested_models_are_inlined_not_referenced(self) -> None:
        schema = schema_of(NestedArgs)
        assert "$defs" not in schema
        assert "$ref" not in str(schema)
        assert schema["properties"]["at"]["properties"]["lat"]["type"] == "number"


class TestToolDecorator:
    def test_builds_a_tool_with_the_derived_schema(self) -> None:
        @tool("get_weather", "Get the weather.")
        async def get_weather(args: WeatherArgs, _ctx: AgentContext) -> str:
            return args.city

        assert get_weather.name == "get_weather"
        assert get_weather.description == "Get the weather."
        spec = get_weather.to_spec()["function"]["parameters"]
        assert "city" in spec["properties"]

    @pytest.mark.asyncio
    async def test_handler_receives_a_validated_instance(self) -> None:
        seen: list[WeatherArgs] = []

        @tool("get_weather", "Get the weather.")
        async def get_weather(args: WeatherArgs, _ctx: AgentContext) -> str:
            seen.append(args)
            return "ok"

        await get_weather.invoke({"city": "Recife"}, AgentContext())
        assert seen[0].city == "Recife"
        assert seen[0].days == 1

    @pytest.mark.asyncio
    async def test_defaults_are_applied(self) -> None:
        @tool("t", "T.")
        async def handler(args: WeatherArgs, _ctx: AgentContext) -> str:
            return str(args.days)

        result = await handler.invoke({"city": "Recife"}, AgentContext())
        assert result.text == "1"

    @pytest.mark.asyncio
    async def test_a_missing_field_names_the_field(self) -> None:
        @tool("t", "T.")
        async def handler(args: WeatherArgs, _ctx: AgentContext) -> str:
            return "unreachable"

        with pytest.raises(AgentToolError, match="city"):
            await handler.invoke({}, AgentContext())

    @pytest.mark.asyncio
    async def test_a_violated_constraint_is_rejected_before_the_handler(self) -> None:
        called = False

        @tool("t", "T.")
        async def handler(args: WeatherArgs, _ctx: AgentContext) -> str:
            nonlocal called
            called = True
            return "ran"

        with pytest.raises(AgentToolError, match="days"):
            await handler.invoke({"city": "x", "days": 500}, AgentContext())
        assert called is False

    @pytest.mark.asyncio
    async def test_the_error_is_one_readable_line(self) -> None:
        @tool("t", "T.")
        async def handler(args: WeatherArgs, _ctx: AgentContext) -> str:
            return "x"

        with pytest.raises(AgentToolError) as caught:
            await handler.invoke({"days": 99}, AgentContext())
        message = str(caught.value)
        assert "\n" not in message
        assert "https://" not in message

    def test_a_bad_signature_fails_at_decoration(self) -> None:
        with pytest.raises(TypeError, match="args, context"):

            @tool("t", "T.")
            async def only_one(args: WeatherArgs) -> str:  # type: ignore[arg-type]
                return "x"

    def test_an_unannotated_first_parameter_fails_at_decoration(self) -> None:
        with pytest.raises(TypeError, match="Pydantic model"):

            @tool("t", "T.")
            async def untyped(args, _ctx: AgentContext) -> str:  # type: ignore[no-untyped-def]  # noqa: ANN001
                return "x"

    @pytest.mark.asyncio
    async def test_typed_tool_without_the_decorator(self) -> None:
        async def handler(args: WeatherArgs, _ctx: AgentContext) -> str:
            return args.city

        built = typed_tool("t", "T.", WeatherArgs, handler)
        result = await built.invoke({"city": "Olinda"}, AgentContext())
        assert result.text == "Olinda"

    @pytest.mark.asyncio
    async def test_a_typed_tool_recovers_inside_a_run(self) -> None:
        @tool("get_weather", "Get the weather.")
        async def get_weather(args: WeatherArgs, _ctx: AgentContext) -> str:
            return f"{args.city}: 22"

        backend = Scripted(
            [
                {"content": "", "tool_calls": [_call("get_weather", town="Recife")]},
                {"content": "", "tool_calls": [_call("get_weather", city="Recife")]},
                {"content": "It is 22 degrees.", "tool_calls": []},
            ],
        )
        run = await Agent(backend, tools=[get_weather]).run("weather?")
        failed = next(s for s in run.steps if s.error)
        assert "city" in failed.error
        assert run.succeeded is True
        assert run.output == "It is 22 degrees."


class TestFinalAnswerTool:
    def test_schema_matches_the_output_model(self) -> None:
        built = final_answer_tool(Summary)
        properties = built.to_spec()["function"]["parameters"]["properties"]
        assert "headline" in properties
        assert "bullets" in properties

    @pytest.mark.asyncio
    async def test_records_the_validated_answer_on_the_context(self) -> None:
        built = final_answer_tool(Summary)
        context = AgentContext()
        await built.invoke({"headline": "All good", "bullets": ["a"]}, context)
        stored = next(iter(context.state.values()))
        assert isinstance(stored, Summary)
        assert stored.headline == "All good"


class TestRunStructured:
    @pytest.mark.asyncio
    async def test_the_tool_call_becomes_the_answer(self) -> None:
        backend = Scripted(
            [
                {
                    "content": "",
                    "tool_calls": [
                        _call(
                            "final_answer",
                            headline="Sales up 12%",
                            bullets=["North grew", "South flat"],
                        )
                    ],
                },
                {"content": "Answer recorded.", "tool_calls": []},
            ],
        )
        run = await run_structured(Agent(backend), "summarise", Summary)
        assert isinstance(run, StructuredRun)
        assert run.has_data is True
        assert run.data.headline == "Sales up 12%"
        assert run.data.bullets == ["North grew", "South flat"]
        assert run.parse_error is None

    @pytest.mark.asyncio
    async def test_the_answer_tool_is_offered_to_the_model(self) -> None:
        backend = Scripted([{"content": "{}", "tool_calls": []}])
        await run_structured(Agent(backend), "g", Summary)
        names = [s["function"]["name"] for s in backend.specs_seen[0]]
        assert "final_answer" in names

    @pytest.mark.asyncio
    async def test_the_system_prompt_explains_how_to_finish(self) -> None:
        backend = Scripted([{"content": "{}", "tool_calls": []}])
        await run_structured(Agent(backend), "g", Summary)
        assert "final_answer" in backend.prompts[0]

    @pytest.mark.asyncio
    async def test_the_caller_agent_is_not_mutated(self) -> None:
        backend = Scripted([{"content": "{}", "tool_calls": []}])
        agent = Agent(backend, name="worker")
        before = list(agent.tool_names)
        await run_structured(agent, "g", Summary)
        assert agent.tool_names == before
        assert "final_answer" not in agent.system_prompt

    @pytest.mark.asyncio
    async def test_json_in_prose_is_recovered_by_the_fallback(self) -> None:
        backend = Scripted(
            [
                {
                    "content": 'Here you go: {"headline": "Recovered", "bullets": []}',
                    "tool_calls": [],
                },
            ],
        )
        run = await run_structured(Agent(backend), "g", Summary)
        assert run.data.headline == "Recovered"

    @pytest.mark.asyncio
    async def test_the_fallback_can_be_refused(self) -> None:
        backend = Scripted(
            [{"content": '{"headline": "x", "bullets": []}', "tool_calls": []}],
        )
        run = await run_structured(
            Agent(backend),
            "g",
            Summary,
            allow_text_fallback=False,
        )
        assert run.data is None
        assert "fallback is disabled" in run.parse_error

    @pytest.mark.asyncio
    async def test_prose_without_json_reports_why(self) -> None:
        backend = Scripted([{"content": "I could not do it.", "tool_calls": []}])
        run = await run_structured(
            Agent(backend),
            "g",
            Summary,
            extraction_retry=False,
        )
        assert run.data is None
        assert "no JSON object" in run.parse_error

    @pytest.mark.asyncio
    async def test_json_that_does_not_match_the_schema_reports_why(self) -> None:
        backend = Scripted([{"content": '{"wrong": 1}', "tool_calls": []}])
        run = await run_structured(
            Agent(backend),
            "g",
            Summary,
            extraction_retry=False,
        )
        assert run.data is None
        assert "does not match the schema" in run.parse_error

    @pytest.mark.asyncio
    async def test_the_trace_and_stop_reason_survive(self) -> None:
        backend = Scripted(
            [
                {
                    "content": "",
                    "tool_calls": [_call("final_answer", headline="x", bullets=[])],
                },
                {"content": "done", "tool_calls": []},
            ],
        )
        run = await run_structured(Agent(backend), "the goal", Summary)
        assert run.goal == "the goal"
        assert run.stop_reason == StopReason.COMPLETED
        assert len(run.steps) >= 2

    @pytest.mark.asyncio
    async def test_a_truncated_run_carries_no_data(self) -> None:
        backend = Scripted(
            [{"content": "thinking", "tool_calls": [_call("final_answer")]}],
            repeat=True,
        )
        agent = Agent(backend, budget=AgentBudget(max_steps=2, max_seconds=None))
        run = await run_structured(agent, "g", Summary)
        assert run.stop_reason == StopReason.MAX_STEPS
        assert run.data is None

    @pytest.mark.asyncio
    async def test_the_agent_method_is_equivalent(self) -> None:
        backend = Scripted(
            [
                {
                    "content": "",
                    "tool_calls": [_call("final_answer", headline="via method")],
                },
                {"content": "ok", "tool_calls": []},
            ],
        )
        run = await Agent(backend).run_structured("g", Summary)
        assert run.data.headline == "via method"


class TestExtractionRetry:
    """A prose answer is converted rather than discarded.

    This is the case real small models actually produce: they work the
    task out correctly, then answer in prose no matter what the prompt
    said.
    """

    @pytest.mark.asyncio
    async def test_prose_is_converted_by_a_second_pass(self) -> None:
        backend = Scripted(
            [
                {"content": "The weather in Recife is 22 degrees.", "tool_calls": []},
                {
                    "content": "",
                    "tool_calls": [
                        _call("final_answer", headline="Recife: 22 degrees")
                    ],
                },
                {"content": "Answer recorded.", "tool_calls": []},
            ],
        )
        run = await run_structured(Agent(backend), "weather?", Summary)
        assert run.has_data is True
        assert run.data.headline == "Recife: 22 degrees"
        assert run.parse_error is None

    @pytest.mark.asyncio
    async def test_the_extractor_only_gets_the_answer_tool(self) -> None:
        @tool("noisy", "Should not be offered to the extractor.")
        async def noisy(args: WeatherArgs, _ctx: AgentContext) -> str:
            return "x"

        backend = Scripted(
            [
                {"content": "prose answer", "tool_calls": []},
                {
                    "content": "",
                    "tool_calls": [_call("final_answer", headline="converted")],
                },
                {"content": "ok", "tool_calls": []},
            ],
        )
        await run_structured(Agent(backend, tools=[noisy]), "g", Summary)
        extractor_specs = backend.specs_seen[-1]
        assert [s["function"]["name"] for s in extractor_specs] == ["final_answer"]

    @pytest.mark.asyncio
    async def test_the_extractor_is_shown_the_prose(self) -> None:
        backend = Scripted(
            [
                {"content": "the prose answer", "tool_calls": []},
                {
                    "content": "",
                    "tool_calls": [_call("final_answer", headline="x")],
                },
                {"content": "ok", "tool_calls": []},
            ],
        )
        agent = Agent(backend)
        await run_structured(agent, "g", Summary)
        assert "convert" in backend.prompts[-1].lower()

    @pytest.mark.asyncio
    async def test_a_failed_extraction_reports_plainly(self) -> None:
        backend = Scripted([{"content": "prose only", "tool_calls": []}], repeat=True)
        run = await run_structured(Agent(backend), "g", Summary)
        assert run.data is None
        assert "could not be converted" in run.parse_error

    @pytest.mark.asyncio
    async def test_an_empty_answer_skips_the_extra_call(self) -> None:
        backend = Scripted([{"content": "", "tool_calls": []}])
        run = await run_structured(Agent(backend), "g", Summary)
        assert run.data is None
        assert run.parse_error == "the run produced no answer"
        assert len(backend.specs_seen) == 1

    @pytest.mark.asyncio
    async def test_it_can_be_switched_off(self) -> None:
        backend = Scripted([{"content": "prose only", "tool_calls": []}], repeat=True)
        run = await run_structured(
            Agent(backend),
            "g",
            Summary,
            extraction_retry=False,
        )
        assert "no JSON object" in run.parse_error
        assert len(backend.specs_seen) == 1


class TestStructuredVerdict:
    @pytest.mark.asyncio
    async def test_a_loop_retries_until_the_shape_arrives(self) -> None:
        backend = Scripted(
            [
                {"content": "no json here", "tool_calls": []},
                {
                    "content": "",
                    "tool_calls": [_call("final_answer", headline="second try")],
                },
                {"content": "ok", "tool_calls": []},
            ],
        )
        agent = Agent(backend)

        async def attempt(goal: str) -> StructuredRun[Summary]:
            return await run_structured(agent, goal, Summary, extraction_retry=False)

        first = await attempt("g")
        assert first.data is None
        assert structured_verdict(first) is False

        second = await attempt("g")
        assert structured_verdict(second) is True

    @pytest.mark.asyncio
    async def test_verdict_composes_with_run_until(self) -> None:
        backend = Scripted([{"content": "nope", "tool_calls": []}], repeat=True)
        result = await run_until(
            Agent(backend),
            "g",
            until=lambda run: False,
            max_rounds=2,
        )
        assert result.accepted is False
