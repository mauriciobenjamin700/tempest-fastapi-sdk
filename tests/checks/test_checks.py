"""Tests for the system-check framework and built-in checks."""

from typing import Any

import pytest

from tempest_fastapi_sdk.checks import (
    CheckLevel,
    CheckMessage,
    CheckRegistry,
    SystemCheckError,
    error,
    info,
    run_system_checks,
    warning,
)
from tempest_fastapi_sdk.checks.builtins import (
    check_bind_host,
    check_cors,
    check_database,
    check_debug,
    check_secrets,
)


class Settings:
    """Attribute bag standing in for a settings object."""

    def __init__(self, **kwargs: Any) -> None:
        self.__dict__.update(kwargs)


class TestCheckMessage:
    def test_is_serious(self) -> None:
        assert error("x").is_serious() is True
        assert warning("x").is_serious() is False
        assert warning("x").is_serious(CheckLevel.WARNING) is True

    def test_str_includes_hint_and_id(self) -> None:
        rendered = str(warning("bad", hint="fix it", id="X001"))
        assert "WARNING" in rendered
        assert "(X001)" in rendered
        assert "HINT: fix it" in rendered


class TestRegistry:
    def test_register_and_run(self) -> None:
        registry = CheckRegistry()

        @registry.check("security")
        def _c(_ctx: Any) -> list[CheckMessage]:
            return [warning("w")]

        assert len(registry.run()) == 1

    def test_tag_filter(self) -> None:
        registry = CheckRegistry()
        registry.register(lambda _c: [info("a")], "security")
        registry.register(lambda _c: [info("b")], "database")

        assert len(registry.run(tags=["security"])) == 1
        assert len(registry.run(tags=["security", "database"])) == 2
        assert len(registry.run(tags=["nope"])) == 0

    def test_context_is_passed(self) -> None:
        registry = CheckRegistry()
        seen: list[Any] = []
        registry.register(lambda ctx: seen.append(ctx) or [])
        sentinel = object()
        registry.run(sentinel)
        assert seen == [sentinel]

    def test_clear(self) -> None:
        registry = CheckRegistry()
        registry.register(lambda _c: [info("a")])
        registry.clear()
        assert registry.run() == []


class TestRunSystemChecks:
    def test_raises_on_error(self) -> None:
        registry = CheckRegistry()
        registry.register(lambda _c: [error("boom")])
        with pytest.raises(SystemCheckError) as exc:
            run_system_checks(registry=registry)
        assert len(exc.value.messages) == 1

    def test_passes_with_only_warnings(self) -> None:
        registry = CheckRegistry()
        registry.register(lambda _c: [warning("meh")])
        messages = run_system_checks(registry=registry)
        assert len(messages) == 1

    def test_custom_fail_level(self) -> None:
        registry = CheckRegistry()
        registry.register(lambda _c: [warning("meh")])
        with pytest.raises(SystemCheckError):
            run_system_checks(registry=registry, fail_level=CheckLevel.WARNING)


class TestBuiltins:
    def test_secrets_empty_and_short_and_ok(self) -> None:
        assert check_secrets(Settings(TOKEN_SECRET="")) != []
        assert check_secrets(Settings(JWT_SECRET="short")) != []
        assert check_secrets(Settings(JWT_SECRET="x" * 40)) == []

    def test_secrets_absent_is_quiet(self) -> None:
        assert check_secrets(Settings()) == []
        assert check_secrets(None) == []

    def test_debug(self) -> None:
        assert check_debug(Settings(DEBUG=True)) != []
        assert check_debug(Settings(DEBUG=False)) == []

    def test_cors_wildcard_with_credentials(self) -> None:
        flagged = Settings(CORS_ORIGINS=["*"], CORS_ALLOW_CREDENTIALS=True)
        assert check_cors(flagged) != []
        # Wildcard without credentials is fine.
        assert check_cors(Settings(CORS_ORIGINS=["*"])) == []
        # Explicit origins with credentials is fine.
        ok = Settings(CORS_ORIGINS=["https://x.com"], CORS_ALLOW_CREDENTIALS=True)
        assert check_cors(ok) == []

    def test_database_sqlite_in_prod(self) -> None:
        prod = Settings(DATABASE_URL="sqlite+aiosqlite:///./db.sqlite3", DEBUG=False)
        assert check_database(prod) != []
        dev = Settings(DATABASE_URL="sqlite+aiosqlite:///./db.sqlite3", DEBUG=True)
        assert check_database(dev) == []
        pg = Settings(DATABASE_URL="postgresql+asyncpg://x", DEBUG=False)
        assert check_database(pg) == []

    def test_bind_host(self) -> None:
        assert check_bind_host(Settings(SERVER_HOST="0.0.0.0")) != []
        assert check_bind_host(Settings(SERVER_HOST="127.0.0.1")) == []
