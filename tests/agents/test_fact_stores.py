"""Tests for the durable fact stores and the agent test helpers.

The database store runs against a real in-memory SQLite, because the whole
point of it is surviving what the in-memory store does not.
"""

from __future__ import annotations

import json
from collections.abc import AsyncIterator
from typing import Any

import pytest

from tempest_fastapi_sdk.agents import (
    Agent,
    AgentContext,
    DbFactStore,
    Fact,
    FactStore,
    RedisFactStore,
    fact_tools,
    facts_prompt,
    make_fact_model,
)
from tempest_fastapi_sdk.agents.testing import (
    FailingBackend,
    ScriptedBackend,
    assert_artifact,
    assert_completed,
    assert_used_tools,
    failed_steps,
    replies,
    replies_with_tool,
    replies_with_tools,
    tool_call,
    tool_steps,
)
from tempest_fastapi_sdk.db.connection import AsyncDatabaseManager

FactRow = make_fact_model(tablename="test_agent_facts", class_name="TestFactRow")


@pytest.fixture
async def db() -> AsyncIterator[AsyncDatabaseManager]:
    """Return a database manager over a fresh in-memory SQLite."""
    manager = AsyncDatabaseManager("sqlite+aiosqlite:///:memory:")
    await manager.connect()
    await manager.create_tables()
    yield manager
    await manager.disconnect()


class FakeRedis:
    """The four hash commands the Redis store uses."""

    def __init__(self) -> None:
        self.hashes: dict[str, dict[str, str]] = {}

    async def hget(self, key: str, field: str) -> str | None:
        return self.hashes.get(key, {}).get(field)

    async def hset(self, key: str, field: str, value: str) -> int:
        self.hashes.setdefault(key, {})[field] = value
        return 1

    async def hdel(self, key: str, field: str) -> int:
        return 1 if self.hashes.get(key, {}).pop(field, None) is not None else 0

    async def hgetall(self, key: str) -> dict[str, str]:
        return dict(self.hashes.get(key, {}))


class TestDbFactStore:
    @pytest.mark.asyncio
    async def test_put_then_get(self, db: AsyncDatabaseManager) -> None:
        store = DbFactStore(db, FactRow)
        written = await store.put("timezone", "America/Recife", subject="u1")
        assert isinstance(written, Fact)
        found = await store.get("timezone", subject="u1")
        assert found is not None
        assert found.value == "America/Recife"
        assert found.subject == "u1"
        assert found.updated_at > 0

    @pytest.mark.asyncio
    async def test_a_fact_survives_a_new_store_instance(
        self, db: AsyncDatabaseManager
    ) -> None:
        await DbFactStore(db, FactRow).put("plan", "pro", subject="u1")
        fresh = DbFactStore(db, FactRow)
        found = await fresh.get("plan", subject="u1")
        assert found is not None
        assert found.value == "pro"

    @pytest.mark.asyncio
    async def test_writing_again_replaces_rather_than_duplicating(
        self,
        db: AsyncDatabaseManager,
    ) -> None:
        store = DbFactStore(db, FactRow)
        await store.put("plan", "free", subject="u1")
        await store.put("plan", "pro", subject="u1")
        assert (await store.get("plan", subject="u1")).value == "pro"
        assert len(await store.all(subject="u1")) == 1

    @pytest.mark.asyncio
    async def test_subjects_are_isolated(self, db: AsyncDatabaseManager) -> None:
        store = DbFactStore(db, FactRow)
        await store.put("plan", "pro", subject="u1")
        await store.put("plan", "free", subject="u2")
        assert (await store.get("plan", subject="u1")).value == "pro"
        assert (await store.get("plan", subject="u2")).value == "free"

    @pytest.mark.asyncio
    async def test_a_shared_namespace_is_its_own_bucket(
        self, db: AsyncDatabaseManager
    ) -> None:
        store = DbFactStore(db, FactRow)
        await store.put("plan", "shared")
        await store.put("plan", "mine", subject="u1")
        assert (await store.get("plan")).value == "shared"
        assert (await store.get("plan", subject="u1")).value == "mine"

    @pytest.mark.asyncio
    async def test_missing_facts_are_none_not_errors(
        self, db: AsyncDatabaseManager
    ) -> None:
        assert await DbFactStore(db, FactRow).get("ghost", subject="u1") is None

    @pytest.mark.asyncio
    async def test_forget_reports_whether_it_existed(
        self, db: AsyncDatabaseManager
    ) -> None:
        store = DbFactStore(db, FactRow)
        await store.put("plan", "pro", subject="u1")
        assert await store.forget("plan", subject="u1") is True
        assert await store.forget("plan", subject="u1") is False

    @pytest.mark.asyncio
    async def test_all_is_sorted_and_scoped(self, db: AsyncDatabaseManager) -> None:
        store = DbFactStore(db, FactRow)
        await store.put("zebra", "z", subject="u1")
        await store.put("alpha", "a", subject="u1")
        await store.put("other", "o", subject="u2")
        assert [f.key for f in await store.all(subject="u1")] == ["alpha", "zebra"]
        assert await store.all(subject="nobody") == []

    @pytest.mark.asyncio
    async def test_it_satisfies_the_protocol(self, db: AsyncDatabaseManager) -> None:
        assert isinstance(DbFactStore(db, FactRow), FactStore)

    @pytest.mark.asyncio
    async def test_it_drives_the_agent_tools(self, db: AsyncDatabaseManager) -> None:
        store = DbFactStore(db, FactRow)
        agent = Agent(
            ScriptedBackend(
                [
                    replies_with_tool(
                        "fact_remember",
                        {"key": "plan", "value": "pro"},
                    ),
                    replies("Remembered."),
                ],
            ),
            tools=fact_tools(store, subject="u1"),
        )
        run = await agent.run("remember my plan")
        assert_completed(run)
        assert (await store.get("plan", subject="u1")).value == "pro"

    @pytest.mark.asyncio
    async def test_it_renders_into_a_prompt(self, db: AsyncDatabaseManager) -> None:
        store = DbFactStore(db, FactRow)
        await store.put("timezone", "America/Recife", subject="u1")
        block = await facts_prompt(store, subject="u1")
        assert "- timezone: America/Recife" in block


class TestRedisFactStore:
    @pytest.mark.asyncio
    async def test_put_then_get(self) -> None:
        store = RedisFactStore(FakeRedis())
        await store.put("plan", "pro", subject="u1")
        found = await store.get("plan", subject="u1")
        assert found is not None
        assert found.value == "pro"
        assert found.updated_at > 0

    @pytest.mark.asyncio
    async def test_one_hash_per_subject(self) -> None:
        redis = FakeRedis()
        store = RedisFactStore(redis, prefix="p")
        await store.put("plan", "pro", subject="u1")
        await store.put("plan", "free", subject="u2")
        assert set(redis.hashes) == {"p:u1", "p:u2"}

    @pytest.mark.asyncio
    async def test_a_shared_namespace_gets_its_own_bucket(self) -> None:
        redis = FakeRedis()
        await RedisFactStore(redis, prefix="p").put("plan", "shared")
        assert "p:_" in redis.hashes

    @pytest.mark.asyncio
    async def test_missing_is_none(self) -> None:
        assert await RedisFactStore(FakeRedis()).get("ghost") is None

    @pytest.mark.asyncio
    async def test_forget_reports_whether_it_existed(self) -> None:
        store = RedisFactStore(FakeRedis())
        await store.put("plan", "pro")
        assert await store.forget("plan") is True
        assert await store.forget("plan") is False

    @pytest.mark.asyncio
    async def test_all_is_sorted(self) -> None:
        store = RedisFactStore(FakeRedis())
        await store.put("zebra", "z")
        await store.put("alpha", "a")
        assert [f.key for f in await store.all()] == ["alpha", "zebra"]

    @pytest.mark.asyncio
    async def test_bytes_from_a_raw_client_are_decoded(self) -> None:
        class BytesRedis(FakeRedis):
            async def hgetall(self, key: str) -> dict[bytes, bytes]:
                return {
                    field.encode(): value.encode()
                    for field, value in self.hashes.get(key, {}).items()
                }

        redis = BytesRedis()
        redis.hashes["agent:facts:_"] = {
            "plan": json.dumps({"value": "pro", "updated_at": 1.0}),
        }
        facts = await RedisFactStore(redis).all()
        assert facts[0].key == "plan"
        assert facts[0].value == "pro"

    def test_it_satisfies_the_protocol(self) -> None:
        assert isinstance(RedisFactStore(FakeRedis()), FactStore)


class TestScriptedBackend:
    @pytest.mark.asyncio
    async def test_a_scripted_run_end_to_end(self) -> None:
        from tempest_fastapi_sdk.agents import text_tool

        async def weather(arguments: dict[str, Any], _ctx: AgentContext) -> str:
            return f"{arguments['city']}: 22"

        tool = text_tool(
            "get_weather",
            "Get the weather.",
            weather,
            parameters={
                "type": "object",
                "properties": {"city": {"type": "string"}},
                "required": ["city"],
            },
        )
        backend = ScriptedBackend(
            [
                replies_with_tool("get_weather", {"city": "Recife"}),
                replies("It is 22 degrees."),
            ],
        )
        run = await Agent(backend, tools=[tool]).run("weather?")
        assert_completed(run)
        assert_used_tools(run, "get_weather")
        assert run.output == "It is 22 degrees."
        assert backend.exhausted is True

    @pytest.mark.asyncio
    async def test_it_records_what_the_agent_sent(self) -> None:
        backend = ScriptedBackend([replies("ok")])
        await Agent(backend, system_prompt="BE BRIEF").run("the goal")
        assert backend.system_prompts[0] == "BE BRIEF"
        assert backend.prompts[0] == "the goal"
        assert backend.calls == 1

    @pytest.mark.asyncio
    async def test_specs_seen_exposes_the_offered_tools(self) -> None:
        from tempest_fastapi_sdk.agents import Skill

        skill = Skill(name="s", description="S.", instructions="Guide.")
        backend = ScriptedBackend([replies("ok")])
        await Agent(backend, skills=[skill]).run("go")
        assert backend.specs_seen[0] == ["load_skill"]

    @pytest.mark.asyncio
    async def test_repeat_last_drives_a_budget_to_fire(self) -> None:
        from tempest_fastapi_sdk.agents import AgentBudget, StopReason, text_tool

        async def noop(_arguments: dict[str, Any], _ctx: AgentContext) -> str:
            return "x"

        backend = ScriptedBackend(
            [replies_with_tool("t", {"text": "x"})],
            repeat_last=True,
        )
        run = await Agent(
            backend,
            tools=[text_tool("t", "T.", noop)],
            budget=AgentBudget(max_steps=4, max_seconds=None),
        ).run("loop")
        assert run.stop_reason == StopReason.MAX_STEPS

    @pytest.mark.asyncio
    async def test_several_tools_in_one_turn(self) -> None:
        from tempest_fastapi_sdk.agents import text_tool

        async def noop(arguments: dict[str, Any], _ctx: AgentContext) -> str:
            return arguments.get("text", "")

        backend = ScriptedBackend(
            [
                replies_with_tools(
                    tool_call("t", {"text": "a"}),
                    tool_call("t", {"text": "b"}),
                ),
                replies("done"),
            ],
        )
        run = await Agent(backend, tools=[text_tool("t", "T.", noop)]).run("go")
        assert_used_tools(run, "t", "t")

    @pytest.mark.asyncio
    async def test_exhausted_is_false_when_the_script_is_unused(self) -> None:
        backend = ScriptedBackend([replies("first"), replies("never reached")])
        await Agent(backend).run("go")
        assert backend.exhausted is False


class TestFailingBackend:
    @pytest.mark.asyncio
    async def test_it_becomes_an_error_stop_not_an_exception(self) -> None:
        from tempest_fastapi_sdk.agents import StopReason

        run = await Agent(FailingBackend("db is gone")).run("go")
        assert run.stop_reason == StopReason.ERROR
        assert "db is gone" in run.output


class TestAssertions:
    @pytest.mark.asyncio
    async def test_assert_used_tools_names_the_difference(self) -> None:
        run = await Agent(ScriptedBackend([replies("ok")])).run("go")
        with pytest.raises(AssertionError, match="expected tools"):
            assert_used_tools(run, "search")

    @pytest.mark.asyncio
    async def test_assert_completed_names_the_stop_reason(self) -> None:
        from tempest_fastapi_sdk.agents import AgentBudget, text_tool

        async def noop(_arguments: dict[str, Any], _ctx: AgentContext) -> str:
            return "x"

        run = await Agent(
            ScriptedBackend([replies_with_tool("t", {"text": "x"})], repeat_last=True),
            tools=[text_tool("t", "T.", noop)],
            budget=AgentBudget(max_steps=2, max_seconds=None),
        ).run("go")
        with pytest.raises(AssertionError, match="max_steps"):
            assert_completed(run)

    @pytest.mark.asyncio
    async def test_assert_artifact_lists_what_was_produced(self) -> None:
        run = await Agent(ScriptedBackend([replies("ok")])).run("go")
        with pytest.raises(AssertionError, match="produced"):
            assert_artifact(run, "chart.png")

    @pytest.mark.asyncio
    async def test_tool_and_failed_step_filters(self) -> None:
        from tempest_fastapi_sdk.agents import text_tool

        async def boom(_arguments: dict[str, Any], _ctx: AgentContext) -> str:
            raise RuntimeError("nope")

        backend = ScriptedBackend(
            [
                replies_with_tool("t", {"text": "x"}),
                replies("recovered"),
            ],
        )
        run = await Agent(backend, tools=[text_tool("t", "T.", boom)]).run("go")
        assert len(tool_steps(run)) == 1
        assert len(failed_steps(run)) == 1
        assert "nope" in failed_steps(run)[0].error
