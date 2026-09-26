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
        lines = payload.splitlines()
        kinds = [
            json.loads(lines[index + 1][len("data: ") :])["kind"]
            for index, line in enumerate(lines)
            if line == "event: step"
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
        run_id = client.post("/api/agent/run", json={"goal": "draw"}).json()["run_id"]
        response = client.get(f"/api/agent/runs/{run_id}/artifacts/cat.png")
        assert response.status_code == 200
        assert response.headers["content-type"] == "image/png"
        assert response.content == b"\x89PNG-fake"

    def test_unknown_artifact_is_404(self) -> None:
        store = InMemoryAgentRunSink(max_runs=5)
        client = _client([{"content": "a", "tool_calls": []}], store=store)
        run_id = client.post("/api/agent/run", json={"goal": "x"}).json()["run_id"]
        url = f"/api/agent/runs/{run_id}/artifacts/ghost.png"
        assert client.get(url).status_code == 404

    def test_unknown_run_is_404(self) -> None:
        store = InMemoryAgentRunSink(max_runs=5)
        client = _client([{"content": "a", "tool_calls": []}], store=store)
        assert client.get("/api/agent/runs/nope/artifacts/x.png").status_code == 404

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


def _owned_client(
    replies: list[dict[str, Any]],
    store: InMemoryAgentRunSink,
) -> TestClient:
    """Build a client whose runs are scoped to the ``X-User`` header."""
    from fastapi import Header

    def principal(x_user: str = Header(default="anonymous")) -> str:
        return x_user

    agent = Agent(ScriptedBackend(replies), tools=[_drawing_tool()], run_sink=store)
    app = FastAPI()
    app.include_router(make_agent_router(agent, run_store=store, owner=principal))
    return TestClient(app)


class TestStableRunIds:
    def test_artifact_url_survives_a_newer_run(self) -> None:
        store = InMemoryAgentRunSink(max_runs=5)
        client = _client(
            [
                {"content": "", "tool_calls": [_call("draw", filename="cat.png")]},
                {"content": "done", "tool_calls": []},
            ],
            store=store,
        )
        first = client.post("/api/agent/run", json={"goal": "draw"}).json()
        client.post("/api/agent/run", json={"goal": "something else"})
        run_id = first["run_id"]
        response = client.get(f"/api/agent/runs/{run_id}/artifacts/cat.png")
        assert response.status_code == 200
        assert response.content == b"\x89PNG-fake"

    def test_a_list_index_is_not_a_run_id(self) -> None:
        store = InMemoryAgentRunSink(max_runs=5)
        client = _client(
            [
                {"content": "", "tool_calls": [_call("draw", filename="cat.png")]},
                {"content": "done", "tool_calls": []},
            ],
            store=store,
        )
        client.post("/api/agent/run", json={"goal": "draw"})
        assert client.get("/api/agent/runs/0/artifacts/cat.png").status_code == 404

    def test_the_stream_reports_the_run_id(self) -> None:
        store = InMemoryAgentRunSink(max_runs=5)
        client = _client([{"content": "hi", "tool_calls": []}], store=store)
        with client.stream(
            "POST",
            "/api/agent/run/stream",
            json={"goal": "x"},
        ) as response:
            payload = "".join(response.iter_text())
        lines = payload.splitlines()
        done_at = lines.index("event: done")
        done = json.loads(lines[done_at + 1][len("data: ") :])
        assert done["run_id"] == store.recent()[0].run_id


class TestNestedArtifactNames:
    def test_a_sub_agent_artifact_path_is_served(self) -> None:
        from tempest_fastapi_sdk.agents import agent_tool

        store = InMemoryAgentRunSink(max_runs=5)
        illustrator = Agent(
            ScriptedBackend(
                [
                    {"content": "", "tool_calls": [_call("draw", filename="bike.png")]},
                    {"content": "drew", "tool_calls": []},
                ],
            ),
            tools=[_drawing_tool()],
            name="illustrator",
        )
        coordinator = Agent(
            ScriptedBackend(
                [
                    {
                        "content": "",
                        "tool_calls": [_call("ask_illustrator", goal="bike")],
                    },
                    {"content": "done", "tool_calls": []},
                ],
            ),
            tools=[agent_tool(illustrator)],
            run_sink=store,
        )
        app = FastAPI()
        app.include_router(make_agent_router(coordinator, run_store=store))
        client = TestClient(app)
        body = client.post("/api/agent/run", json={"goal": "draw"}).json()
        names = [artifact["name"] for artifact in body["artifacts"]]
        assert names == ["illustrator/bike.png"]
        response = client.get(
            f"/api/agent/runs/{body['run_id']}/artifacts/illustrator/bike.png",
        )
        assert response.status_code == 200
        assert response.content == b"\x89PNG-fake"


class TestOwnerScoping:
    def test_runs_are_listed_only_to_their_owner(self) -> None:
        store = InMemoryAgentRunSink(max_runs=5)
        client = _owned_client([{"content": "a", "tool_calls": []}], store)
        client.post("/api/agent/run", json={"goal": "alice"}, headers={"X-User": "a"})
        client.post("/api/agent/run", json={"goal": "bob"}, headers={"X-User": "b"})
        mine = client.get("/api/agent/runs", headers={"X-User": "a"}).json()
        assert [run["goal"] for run in mine] == ["alice"]

    def test_another_owners_artifact_is_404(self) -> None:
        store = InMemoryAgentRunSink(max_runs=5)
        client = _owned_client(
            [
                {"content": "", "tool_calls": [_call("draw", filename="cat.png")]},
                {"content": "done", "tool_calls": []},
            ],
            store,
        )
        body = client.post(
            "/api/agent/run",
            json={"goal": "draw"},
            headers={"X-User": "a"},
        ).json()
        url = f"/api/agent/runs/{body['run_id']}/artifacts/cat.png"
        assert client.get(url, headers={"X-User": "b"}).status_code == 404
        assert client.get(url, headers={"X-User": "a"}).status_code == 200

    def test_a_tool_reads_the_owner_from_the_context(self) -> None:
        from fastapi import Header

        seen: list[str | None] = []

        async def whoami(_arguments: dict[str, Any], context: AgentContext) -> str:
            seen.append(context.owner)
            return "ok"

        def principal(x_user: str = Header(default="anonymous")) -> str:
            return x_user

        agent = Agent(
            ScriptedBackend(
                [
                    {"content": "", "tool_calls": [_call("whoami")]},
                    {"content": "done", "tool_calls": []},
                ],
            ),
            tools=[
                AgentTool(
                    name="whoami",
                    description="Who.",
                    parameters={"type": "object", "properties": {}},
                    handler=whoami,
                ),
            ],
        )
        app = FastAPI()
        app.include_router(make_agent_router(agent, owner=principal))
        TestClient(app).post(
            "/api/agent/run",
            json={"goal": "g"},
            headers={"X-User": "alice"},
        )
        assert seen == ["alice"]

    def test_the_owner_is_recorded_on_the_run(self) -> None:
        store = InMemoryAgentRunSink(max_runs=5)
        client = _owned_client([{"content": "a", "tool_calls": []}], store)
        client.post("/api/agent/run", json={"goal": "g"}, headers={"X-User": "a"})
        assert store.recent()[0].owner == "a"


class TestToolFailuresOverHttp:
    def test_raw_exception_text_is_not_served(self) -> None:
        async def leaky(_arguments: dict[str, Any], _ctx: AgentContext) -> str:
            raise RuntimeError("postgresql://admin:hunter2@db/app")

        agent = Agent(
            ScriptedBackend(
                [
                    {"content": "", "tool_calls": [_call("db")]},
                    {"content": "sorry", "tool_calls": []},
                ],
            ),
            tools=[
                AgentTool(
                    name="db",
                    description="DB.",
                    parameters={"type": "object", "properties": {}},
                    handler=leaky,
                ),
            ],
        )
        app = FastAPI()
        app.include_router(make_agent_router(agent))
        response = TestClient(app).post("/api/agent/run", json={"goal": "g"})
        assert "hunter2" not in response.text
