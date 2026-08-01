"""Tests for the agent HTTP surface and the persistence sink."""

from __future__ import annotations

import json
from typing import Any

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from tempest_fastapi_sdk.agents import (
    Agent,
    AgentContext,
    AgentTool,
    InMemoryAgentRunSink,
    ToolResult,
    make_agent_router,
    make_agent_run_model,
)
from tempest_fastapi_sdk.agents.schemas import AgentArtifact, AgentRun, StopReason


def _call(name: str, **arguments: Any) -> dict[str, Any]:
    return {"function": {"name": name, "arguments": arguments}}


class ScriptedBackend:
    def __init__(self, replies: list[dict[str, Any]]) -> None:
        self.replies = list(replies)

    async def chat_with_tools(
        self,
        messages: list[dict[str, Any]],
        specs: list[dict[str, Any]],
    ) -> dict[str, Any]:
        if not self.replies:
            return {"content": "done", "tool_calls": []}
        return self.replies.pop(0)

    async def chat(self, messages: list[dict[str, Any]]) -> str:
        return "done"


def _drawing_tool() -> AgentTool:
    async def handler(
        arguments: dict[str, Any],
        _context: AgentContext,
    ) -> ToolResult:
        return ToolResult(
            text="drew it",
            artifacts=[
                AgentArtifact(
                    name=str(arguments.get("filename", "out.png")),
                    media_type="image/png",
                    data=b"\x89PNG-fake",
                    description="a drawing",
                )
            ],
        )

    return AgentTool(
        name="draw",
        description="Draw something.",
        parameters={"type": "object", "properties": {}},
        handler=handler,
    )


def _client(
    replies: list[dict[str, Any]],
    *,
    store: InMemoryAgentRunSink | None = None,
) -> TestClient:
    agent = Agent(
        ScriptedBackend(replies),
        tools=[_drawing_tool()],
        run_sink=store,
    )
    app = FastAPI()
    app.include_router(make_agent_router(agent, run_store=store))
    return TestClient(app)


class TestRunEndpoint:
    def test_returns_the_answer_and_the_trace(self) -> None:
        client = _client([{"content": "42", "tool_calls": []}])
        response = client.post("/api/agent/run", json={"goal": "what is 6*7?"})
        assert response.status_code == 200
        body = response.json()
        assert body["output"] == "42"
        assert body["stop_reason"] == "completed"
        assert body["succeeded"] is True
        assert len(body["steps"]) == 1

    def test_artifacts_are_metadata_not_bytes(self) -> None:
        client = _client(
            [
                {"content": "", "tool_calls": [_call("draw", filename="cat.png")]},
                {"content": "drew it", "tool_calls": []},
            ],
        )
        body = client.post("/api/agent/run", json={"goal": "draw"}).json()
        artifact = body["artifacts"][0]
        assert artifact == {
            "name": "cat.png",
            "media_type": "image/png",
            "size_bytes": len(b"\x89PNG-fake"),
            "description": "a drawing",
        }
        assert "data" not in artifact

    def test_a_truncated_run_reports_its_reason(self) -> None:
        from tempest_fastapi_sdk.agents import AgentBudget

        agent = Agent(
            ScriptedBackend([{"content": "", "tool_calls": [_call("draw")]}] * 10),
            tools=[_drawing_tool()],
            budget=AgentBudget(max_steps=2, max_seconds=None),
        )
        app = FastAPI()
        app.include_router(make_agent_router(agent))
        body = TestClient(app).post("/api/agent/run", json={"goal": "loop"}).json()
        assert body["stop_reason"] == "max_steps"
        assert body["succeeded"] is False


class TestStreamEndpoint:
    def test_streams_one_event_per_step(self) -> None:
        client = _client(
            [
                {"content": "", "tool_calls": [_call("draw")]},
                {"content": "done", "tool_calls": []},
            ],
        )
        with client.stream(
            "POST",
            "/api/agent/run/stream",
            json={"goal": "draw"},
        ) as response:
            assert response.status_code == 200
            payload = "".join(response.iter_text())
        kinds = [
            json.loads(line[len("data: ") :])["kind"]
            for line in payload.splitlines()
            if line.startswith("data: ") and line != "data: "
        ]
        assert kinds == ["model", "tool", "model"]
        assert "event: done" in payload


class TestHistoryEndpoints:
    def test_runs_are_listed_newest_first(self) -> None:
        store = InMemoryAgentRunSink(max_runs=5)
        client = _client([{"content": "a", "tool_calls": []}], store=store)
        client.post("/api/agent/run", json={"goal": "first"})
        client.post("/api/agent/run", json={"goal": "second"})
        body = client.get("/api/agent/runs").json()
        assert [run["goal"] for run in body] == ["second", "first"]

    def test_artifact_is_served_with_its_media_type(self) -> None:
        store = InMemoryAgentRunSink(max_runs=5)
        client = _client(
            [
                {"content": "", "tool_calls": [_call("draw", filename="cat.png")]},
                {"content": "done", "tool_calls": []},
            ],
            store=store,
        )
        client.post("/api/agent/run", json={"goal": "draw"})
        response = client.get("/api/agent/runs/0/artifacts/cat.png")
        assert response.status_code == 200
        assert response.headers["content-type"] == "image/png"
        assert response.content == b"\x89PNG-fake"

    def test_unknown_artifact_is_404(self) -> None:
        store = InMemoryAgentRunSink(max_runs=5)
        client = _client([{"content": "a", "tool_calls": []}], store=store)
        client.post("/api/agent/run", json={"goal": "x"})
        assert client.get("/api/agent/runs/0/artifacts/ghost.png").status_code == 404

    def test_unknown_run_is_404(self) -> None:
        store = InMemoryAgentRunSink(max_runs=5)
        client = _client([{"content": "a", "tool_calls": []}], store=store)
        assert client.get("/api/agent/runs/9/artifacts/x.png").status_code == 404

    def test_history_is_absent_without_a_store(self) -> None:
        client = _client([{"content": "a", "tool_calls": []}])
        assert client.get("/api/agent/runs").status_code == 404


class TestRunModel:
    def test_row_keeps_the_trace_but_not_the_bytes(self) -> None:
        model = make_agent_run_model(
            tablename="agent_runs_test", class_name="AgentRunRowA"
        )
        run = AgentRun(
            goal="draw a cat",
            output="done",
            stop_reason=StopReason.COMPLETED,
            seconds=1.5,
            agent="artist",
            artifacts=[
                AgentArtifact(
                    name="cat.png",
                    media_type="image/png",
                    data=b"\x89PNG" * 100,
                )
            ],
        )
        row = model.from_run(run)
        assert row.agent == "artist"
        assert row.goal == "draw a cat"
        assert row.stop_reason == "completed"
        assert row.artifact_names == ["cat.png"]
        assert row.step_count == 0
        assert not hasattr(row, "artifacts")

    def test_steps_serialize_to_json_safe_values(self) -> None:
        from tempest_fastapi_sdk.agents import AgentStep, StepKind

        model = make_agent_run_model(
            tablename="agent_runs_json_test", class_name="AgentRunRowB"
        )
        run = AgentRun(
            goal="g",
            steps=[
                AgentStep(
                    index=0,
                    kind=StepKind.TOOL,
                    name="draw",
                    arguments={"filename": "a.png"},
                    output="drew it",
                ),
            ],
        )
        row = model.from_run(run)
        assert row.step_count == 1
        assert json.dumps(row.steps)
        assert row.steps[0]["kind"] == "tool"


class TestPersistenceSink:
    @pytest.mark.asyncio
    async def test_db_sink_adds_a_row(self) -> None:
        from tempest_fastapi_sdk.agents import DbAgentRunSink

        added: list[Any] = []

        class FakeSession:
            def add(self, row: Any) -> None:
                added.append(row)

        class FakeContext:
            async def __aenter__(self) -> FakeSession:
                return FakeSession()

            async def __aexit__(self, *_exc: Any) -> None:
                return None

        class FakeDb:
            def get_session_context(self) -> FakeContext:
                return FakeContext()

        model = make_agent_run_model(
            tablename="agent_runs_sink_test", class_name="AgentRunRowC"
        )
        sink = DbAgentRunSink(FakeDb(), model)
        await sink(AgentRun(goal="g", output="o"))
        assert len(added) == 1
        assert added[0].goal == "g"
