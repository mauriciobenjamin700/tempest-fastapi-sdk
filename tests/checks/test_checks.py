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
    MIN_SECRET_LENGTH,
    check_bind_host,
    check_cors,
    check_database,
    check_debug,
    check_secrets,
)
from tempest_fastapi_sdk.settings.mixins import (
    DatabaseSettings,
    JWTSettings,
    ServerSettings,
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


class TestBuiltinsAgainstTheSdkMixins:
    """The checks read the field names the SDK's own mixins declare.

    Every other test in this module feeds the checks a hand-rolled
    attribute bag, which is exactly how ``check_debug`` and
    ``check_database`` shipped broken until v0.272.0: the bag carried a
    ``DEBUG`` the mixins never define, so the suite exercised a shape no
    service composing ``ServerSettings`` ever produces. These tests
    compose the real mixins instead.
    """

    def _settings(self, **overrides: Any) -> Any:
        """Build a settings object out of the real SDK mixins.

        Args:
            **overrides (Any): Field values passed to the constructor,
                which outrank both the environment and the declared
                defaults.

        Returns:
            Any: The composed settings instance.
        """

        class _Settings(ServerSettings, DatabaseSettings, JWTSettings):
            """A service's settings, composed the way the docs prescribe."""

        return _Settings(_env_file=None, **overrides)

    def test_debug_fires_on_server_debug(self) -> None:
        messages = check_debug(self._settings(SERVER_DEBUG=True))

        assert [m.id for m in messages] == ["deployment.I001"]
        assert "SERVER_DEBUG" in messages[0].message

    def test_debug_is_quiet_when_off(self) -> None:
        assert check_debug(self._settings(SERVER_DEBUG=False)) == []

    def test_sqlite_under_debug_is_the_normal_dev_setup(self) -> None:
        """The warning used to fire precisely when debug was on.

        ``not getattr(context, "DEBUG", False)`` is always ``True`` when
        no ``DEBUG`` field exists, so the debug exemption never applied
        and the message contradicted itself.
        """
        settings = self._settings(
            SERVER_DEBUG=True, DATABASE_URL="sqlite+aiosqlite:///./x.db"
        )

        assert check_database(settings) == []

    def test_sqlite_outside_debug_is_flagged(self) -> None:
        settings = self._settings(
            SERVER_DEBUG=False, DATABASE_URL="sqlite+aiosqlite:///./x.db"
        )

        assert [m.id for m in check_database(settings)] == ["database.W001"]

    def test_a_hand_declared_debug_still_counts(self) -> None:
        """A project that declared a bare ``DEBUG`` keeps working."""
        assert check_debug(Settings(DEBUG=True)) != []
        assert check_database(Settings(DATABASE_URL="sqlite://", DEBUG=True)) == []

    def test_the_shipped_jwt_placeholder_is_flagged(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """The one value guaranteed to be wrong used to be the one that passed.

        ``JWTSettings.JWT_SECRET`` defaults to a 32-character string, so
        it cleared both the empty branch and the length branch.
        """
        monkeypatch.delenv("JWT_SECRET", raising=False)
        settings = self._settings()

        assert len(settings.JWT_SECRET) == MIN_SECRET_LENGTH
        assert [m.id for m in check_secrets(settings)] == ["security.W004"]

    def test_a_rotated_secret_is_quiet(self) -> None:
        assert check_secrets(self._settings(JWT_SECRET="x" * 40)) == []

    def test_the_comparison_follows_the_declared_default(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """A changed placeholder stays covered.

        The check reads ``model_fields[name].default`` rather than a copy
        of the literal, so replacing the placeholder does not silently
        retire the warning.
        """
        monkeypatch.delenv("JWT_SECRET", raising=False)

        class _Rotated(JWTSettings):
            JWT_SECRET: str = "some-other-placeholder-of-32-char"

        settings = _Rotated(_env_file=None)

        assert JWTSettings.model_fields["JWT_SECRET"].default != settings.JWT_SECRET
        assert [m.id for m in check_secrets(settings)] == ["security.W004"]

    def test_an_empty_secret_is_still_reported_as_empty(self) -> None:
        """``TOKEN_SECRET`` defaults to ``""`` — W001 outranks W004 there."""
        assert [m.id for m in check_secrets(Settings(TOKEN_SECRET=""))] == [
            "security.W001"
        ]
