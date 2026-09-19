"""Tests for ``tempest flags`` and ``tempest cache``.

Both groups talk to Redis through ``AsyncRedisManager``, so the suite
swaps the driver the manager imports for ``fakeredis`` — the client the
SDK's own Redis tests use. What is exercised is the command's behaviour
(exit codes, refusals, what it writes), not the server.
"""

from __future__ import annotations

from typing import Any

import pytest
from typer.testing import CliRunner

from tempest_fastapi_sdk.cli.main import app

runner = CliRunner()


@pytest.fixture
def fake_redis(monkeypatch: pytest.MonkeyPatch) -> Any:
    """Point ``AsyncRedisManager`` at one shared in-memory server.

    Every ``from_url`` in a test returns a client on the same fakeredis
    server, so a write made by one command is visible to the next — the
    property the CLI depends on and a per-call fake would hide.
    """
    aioredis = pytest.importorskip("fakeredis.aioredis")
    server = pytest.importorskip("fakeredis").FakeServer()

    class _Driver:
        """Stand-in for the ``redis.asyncio`` module."""

        class Redis:
            """Stand-in for ``redis.asyncio.Redis``."""

            @staticmethod
            def from_url(url: str, **kwargs: Any) -> Any:
                """Return a fakeredis client bound to the shared server."""
                kwargs.pop("url", None)
                return aioredis.FakeRedis(server=server, **kwargs)

    monkeypatch.setattr(
        "tempest_fastapi_sdk.cache.redis_manager._require_redis",
        lambda: _Driver,
    )
    monkeypatch.setenv("REDIS_URL", "redis://localhost:6379/0")
    return server


class TestFlags:
    def test_empty_backend_is_not_an_error(self, fake_redis: Any) -> None:
        result = runner.invoke(app, ["flags", "list"])
        assert result.exit_code == 0, result.stdout + result.stderr
        assert "(no flags)" in result.stdout

    def test_enable_then_list(self, fake_redis: Any) -> None:
        assert runner.invoke(app, ["flags", "enable", "new-checkout"]).exit_code == 0
        result = runner.invoke(app, ["flags", "list"])
        assert result.exit_code == 0, result.stdout + result.stderr
        assert "new-checkout  on" in result.stdout

    def test_disable_is_not_the_same_as_unset(self, fake_redis: Any) -> None:
        """The service resolves an unset flag to the caller's default."""
        runner.invoke(app, ["flags", "disable", "new-checkout"])

        stored = runner.invoke(app, ["flags", "get", "new-checkout"])
        assert stored.exit_code == 0
        assert "new-checkout off" in stored.stdout

        absent = runner.invoke(app, ["flags", "get", "never-set"])
        assert absent.exit_code == 1
        assert "never-set unset" in absent.stdout

    def test_custom_key_is_a_separate_namespace(self, fake_redis: Any) -> None:
        runner.invoke(app, ["flags", "enable", "x", "--key", "ff:a"])
        other = runner.invoke(app, ["flags", "list", "--key", "ff:b"])
        assert "(no flags)" in other.stdout

    def test_missing_url_exits_two(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.delenv("REDIS_URL", raising=False)
        result = runner.invoke(app, ["flags", "list"])
        assert result.exit_code == 2
        assert "no Redis URL" in result.stderr


class TestCache:
    def test_ping(self, fake_redis: Any) -> None:
        result = runner.invoke(app, ["cache", "ping"])
        assert result.exit_code == 0, result.stdout + result.stderr
        assert "PONG" in result.stdout

    def test_stats_formatting(self) -> None:
        """fakeredis has no INFO, so the formatting is exercised directly."""
        from tempest_fastapi_sdk.cli.cache import format_stats

        lines = format_stats(
            {
                "redis_version": "7.2.4",
                "used_memory_human": "1.10M",
                "connected_clients": 3,
                "keyspace_hits": 30,
                "keyspace_misses": 10,
            },
            dbsize=12,
        )
        assert "server      7.2.4" in lines
        assert "keys        12" in lines
        assert any("75.0%" in line for line in lines)

    def test_stats_ratio_has_no_answer_before_the_first_lookup(self) -> None:
        from tempest_fastapi_sdk.cli.cache import format_stats

        lines = format_stats({"keyspace_hits": 0, "keyspace_misses": 0}, dbsize=0)
        assert any("n/a (no lookup yet)" in line for line in lines)

    def test_flush_without_a_target_is_a_usage_error(self, fake_redis: Any) -> None:
        result = runner.invoke(app, ["cache", "flush"])
        assert result.exit_code == 2
        assert "nothing to invalidate" in result.stderr

    def test_flush_all_needs_confirmation(self, fake_redis: Any) -> None:
        runner.invoke(app, ["flags", "enable", "kept"])

        refused = runner.invoke(app, ["cache", "flush", "--all"])
        assert refused.exit_code == 2
        assert "FLUSHDB" in refused.stderr
        assert "kept  on" in runner.invoke(app, ["flags", "list"]).stdout

        confirmed = runner.invoke(app, ["cache", "flush", "--all", "--yes"])
        assert confirmed.exit_code == 0, confirmed.stdout + confirmed.stderr
        assert "(no flags)" in runner.invoke(app, ["flags", "list"]).stdout

    def test_flush_by_key_reports_the_count(self, fake_redis: Any) -> None:
        result = runner.invoke(app, ["cache", "flush", "--key", "absent-key"])
        assert result.exit_code == 0, result.stdout + result.stderr
        assert "Invalidated 0 key(s)." in result.stdout
