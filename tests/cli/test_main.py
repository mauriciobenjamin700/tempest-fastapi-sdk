"""Tests for the ``tempest`` CLI entry point."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import patch

from typer.testing import CliRunner

from tempest_fastapi_sdk import __version__
from tempest_fastapi_sdk.cli.main import app

runner = CliRunner()


class TestRoot:
    def test_no_args_prints_help(self) -> None:
        result = runner.invoke(app, [])
        assert result.exit_code != 0
        assert "Tempest FastAPI SDK CLI" in result.stdout

    def test_version_flag_prints_version(self) -> None:
        result = runner.invoke(app, ["--version"])
        assert result.exit_code == 0
        assert __version__ in result.stdout

    def test_version_command_matches_flag(self) -> None:
        result = runner.invoke(app, ["version"])
        assert result.exit_code == 0
        assert __version__ in result.stdout

    def test_help_lists_every_command(self) -> None:
        result = runner.invoke(app, ["--help"])
        assert result.exit_code == 0
        for cmd in ("new", "lint", "format", "fmt-check", "type", "test", "check"):
            assert cmd in result.stdout


class TestFullHelpOnError:
    # Force a wide terminal so Rich never wraps/truncates the option
    # names the assertions look for (CI runs at a narrow default width).
    _WIDE: dict[str, str] = {"COLUMNS": "200"}

    def test_unknown_command_prints_full_help(self) -> None:
        result = runner.invoke(app, ["frobnicate"], env=self._WIDE)
        out = result.stdout + (result.stderr or "")
        assert result.exit_code == 2
        # Full command listing (the group help) is shown, not just a hint.
        assert "Usage" in out
        assert "new" in out and "generate" in out
        assert "Error:" in out
        assert "No such command" in out

    def test_unknown_option_prints_full_help(self) -> None:
        result = runner.invoke(app, ["new", "demo", "--nope"], env=self._WIDE)
        out = result.stdout + (result.stderr or "")
        assert result.exit_code == 2
        # The offending command's own options are listed.
        assert "--extras" in out
        assert "--bind-host" in out
        assert "Error:" in out

    def test_missing_required_option_prints_full_help(self) -> None:
        result = runner.invoke(app, ["user", "create"], env=self._WIDE)
        out = result.stdout + (result.stderr or "")
        assert result.exit_code == 2
        assert "--email" in out
        assert "--password" in out
        assert "Error:" in out

    def test_unknown_subcommand_prints_subgroup_help(self) -> None:
        result = runner.invoke(app, ["user", "frobnicate"], env=self._WIDE)
        out = result.stdout + (result.stderr or "")
        assert result.exit_code == 2
        # The ``user`` group help (its subcommands) is rendered.
        assert "create" in out
        assert "promote" in out
        assert "Error:" in out

    def test_quality_gate_exit_code_still_propagates(self) -> None:
        with patch("tempest_fastapi_sdk.cli.main.lint_module") as fake:
            fake.run_ruff_check.return_value = 7
            result = runner.invoke(app, ["lint"])
        assert result.exit_code == 7


class TestNew:
    def test_rejects_invalid_slug(self, tmp_path: Path) -> None:
        result = runner.invoke(app, ["new", "Bad-Name", "--path", str(tmp_path)])
        assert result.exit_code == 2
        assert "project name must match" in (result.stdout + result.stderr)

    def test_rejects_python_keyword(self, tmp_path: Path) -> None:
        result = runner.invoke(app, ["new", "class", "--path", str(tmp_path)])
        assert result.exit_code == 2

    def test_scaffolds_full_project(self, tmp_path: Path) -> None:
        result = runner.invoke(app, ["new", "demo_svc", "--path", str(tmp_path)])
        assert result.exit_code == 0, result.stdout + result.stderr
        target = tmp_path / "demo_svc"
        assert (target / "main.py").is_file()
        assert (target / "pyproject.toml").is_file()
        assert (target / "src" / "server.py").is_file()
        assert (target / "src" / "api" / "app.py").is_file()
        assert (target / "src" / "core" / "settings.py").is_file()
        assert (target / "src" / "db" / "repositories" / "__init__.py").is_file()
        assert (target / "tests" / "test_smoke.py").is_file()
        assert (target / ".gitignore").is_file()
        assert (target / ".env.example").is_file()
        assert (target / "docker-compose.yaml").is_file()

    def test_compose_only_wires_postgres_by_default(self, tmp_path: Path) -> None:
        result = runner.invoke(app, ["new", "demo_svc", "--path", str(tmp_path)])
        assert result.exit_code == 0
        compose = (tmp_path / "demo_svc" / "docker-compose.yaml").read_text()
        assert "postgres:" in compose
        # Default --extras=auth — none of these services should appear:
        assert "redis:" not in compose
        assert "rabbitmq:" not in compose
        assert "minio:" not in compose
        assert "mailhog:" not in compose

    def test_compose_picks_extras(self, tmp_path: Path) -> None:
        result = runner.invoke(
            app,
            [
                "new",
                "demo_svc",
                "--path",
                str(tmp_path),
                "--extras",
                "auth,cache,minio,queue,email",
            ],
        )
        assert result.exit_code == 0, result.stdout + result.stderr
        compose = (tmp_path / "demo_svc" / "docker-compose.yaml").read_text()
        assert "redis:" in compose
        assert "rabbitmq:" in compose
        assert "minio:" in compose
        assert "mailhog:" in compose
        env = (tmp_path / "demo_svc" / ".env.example").read_text()
        assert "REDIS_URL" in env
        assert "RABBITMQ_URL" in env
        assert "MINIO_ENDPOINT" in env
        assert "SMTP_HOST=localhost" in env
        assert "SMTP_USE_TLS=false" in env

    def test_placeholders_are_rendered(self, tmp_path: Path) -> None:
        result = runner.invoke(app, ["new", "demo_svc", "--path", str(tmp_path)])
        assert result.exit_code == 0
        pyproject = (tmp_path / "demo_svc" / "pyproject.toml").read_text()
        assert 'name = "demo_svc"' in pyproject
        assert "tempest-fastapi-sdk[auth,admin]>=" in pyproject
        env = (tmp_path / "demo_svc" / ".env.example").read_text()
        assert "SERVER_HOST=127.0.0.1" in env
        assert "SERVER_PORT=8000" in env

    def test_custom_host_port_extras(self, tmp_path: Path) -> None:
        result = runner.invoke(
            app,
            [
                "new",
                "demo_svc",
                "--path",
                str(tmp_path),
                "--bind-host",
                "0.0.0.0",
                "--bind-port",
                "9090",
                "--extras",
                "auth,upload",
            ],
        )
        assert result.exit_code == 0
        pyproject = (tmp_path / "demo_svc" / "pyproject.toml").read_text()
        assert "tempest-fastapi-sdk[auth,upload]>=" in pyproject
        env = (tmp_path / "demo_svc" / ".env.example").read_text()
        assert "SERVER_HOST=0.0.0.0" in env
        assert "SERVER_PORT=9090" in env

    def test_scaffold_ships_sqlite_driver_and_commented_asyncpg(
        self, tmp_path: Path
    ) -> None:
        result = runner.invoke(
            app,
            ["new", "demo_svc", "--path", str(tmp_path), "--extras", ""],
        )
        assert result.exit_code == 0
        pyproject = (tmp_path / "demo_svc" / "pyproject.toml").read_text()
        # aiosqlite is a runtime dependency (default DATABASE_URL is SQLite),
        # not a dev-only one — so the service runs without --dev installs.
        runtime_block = pyproject.split("[dependency-groups]", 1)[0]
        assert '"aiosqlite>=0.20.0",' in runtime_block
        # asyncpg ships commented next to the SQLite driver, ready to enable
        # when switching DATABASE_URL to PostgreSQL.
        assert '# "asyncpg>=0.30.0",' in pyproject

    def test_empty_extras_drops_bracket_block(self, tmp_path: Path) -> None:
        result = runner.invoke(
            app,
            [
                "new",
                "demo_svc",
                "--path",
                str(tmp_path),
                "--extras",
                "",
            ],
        )
        assert result.exit_code == 0
        pyproject = (tmp_path / "demo_svc" / "pyproject.toml").read_text()
        assert "tempest-fastapi-sdk>=" in pyproject
        assert "tempest-fastapi-sdk[" not in pyproject

    def test_scaffolds_queue_layer_for_queue_extra(self, tmp_path: Path) -> None:
        result = runner.invoke(
            app,
            ["new", "demo_svc", "--path", str(tmp_path), "--extras", "auth,queue"],
        )
        assert result.exit_code == 0, result.stdout + result.stderr
        target = tmp_path / "demo_svc"
        assert (target / "src" / "queue" / "__init__.py").is_file()
        assert (target / "src" / "queue" / "handlers.py").is_file()
        # tasks layer must NOT appear without the [tasks] extra.
        assert not (target / "src" / "tasks").exists()

    def test_no_optional_layers_by_default(self, tmp_path: Path) -> None:
        result = runner.invoke(app, ["new", "demo_svc", "--path", str(tmp_path)])
        assert result.exit_code == 0
        target = tmp_path / "demo_svc"
        assert not (target / "src" / "queue").exists()
        assert not (target / "src" / "tasks").exists()

    def test_existing_target_requires_force(self, tmp_path: Path) -> None:
        (tmp_path / "demo_svc").mkdir()
        (tmp_path / "demo_svc" / "old.txt").write_text("keep me")
        result = runner.invoke(app, ["new", "demo_svc", "--path", str(tmp_path)])
        assert result.exit_code == 1
        assert (tmp_path / "demo_svc" / "old.txt").exists()

    def test_force_overwrites_target(self, tmp_path: Path) -> None:
        (tmp_path / "demo_svc").mkdir()
        result = runner.invoke(
            app, ["new", "demo_svc", "--path", str(tmp_path), "--force"]
        )
        assert result.exit_code == 0
        assert (tmp_path / "demo_svc" / "main.py").is_file()


class TestLintCommands:
    def test_lint_invokes_ruff_check(self) -> None:
        with patch("tempest_fastapi_sdk.cli.main.lint_module") as fake:
            fake.run_ruff_check.return_value = 0
            result = runner.invoke(app, ["lint", "src/"])
        fake.run_ruff_check.assert_called_once_with("src/")
        assert result.exit_code == 0

    def test_lint_propagates_exit_code(self) -> None:
        with patch("tempest_fastapi_sdk.cli.main.lint_module") as fake:
            fake.run_ruff_check.return_value = 7
            result = runner.invoke(app, ["lint"])
        assert result.exit_code == 7

    def test_format_invokes_ruff_format_write(self) -> None:
        with patch("tempest_fastapi_sdk.cli.main.lint_module") as fake:
            fake.run_ruff_format.return_value = 0
            runner.invoke(app, ["format"])
        fake.run_ruff_format.assert_called_once_with(".", check=False)

    def test_fmt_check_invokes_ruff_format_check(self) -> None:
        with patch("tempest_fastapi_sdk.cli.main.lint_module") as fake:
            fake.run_ruff_format.return_value = 0
            runner.invoke(app, ["fmt-check"])
        fake.run_ruff_format.assert_called_once_with(".", check=True)

    def test_type_invokes_mypy(self) -> None:
        with patch("tempest_fastapi_sdk.cli.main.lint_module") as fake:
            fake.run_mypy.return_value = 0
            runner.invoke(app, ["type", "src/"])
        fake.run_mypy.assert_called_once_with("src/")

    def test_test_invokes_pytest_with_target(self) -> None:
        with patch("tempest_fastapi_sdk.cli.main.lint_module") as fake:
            fake.run_pytest.return_value = 0
            runner.invoke(app, ["test", "tests/cli"])
        fake.run_pytest.assert_called_once_with("tests/cli")

    def test_test_invokes_pytest_without_target(self) -> None:
        with patch("tempest_fastapi_sdk.cli.main.lint_module") as fake:
            fake.run_pytest.return_value = 0
            runner.invoke(app, ["test"])
        fake.run_pytest.assert_called_once_with(None)

    def test_check_invokes_full_check(self) -> None:
        with patch("tempest_fastapi_sdk.cli.main.lint_module") as fake:
            fake.run_full_check.return_value = 0
            runner.invoke(app, ["check"])
        fake.run_full_check.assert_called_once_with(".")


class TestLintRunner:
    def test_execute_returns_127_when_executable_missing(self) -> None:
        from tempest_fastapi_sdk.cli import lint

        with patch.object(lint.shutil, "which", return_value=None):
            assert lint._execute("ruff", ["check"]) == 127

    def test_execute_prefers_direct_executable(self) -> None:
        from tempest_fastapi_sdk.cli import lint

        with (
            patch.object(lint.shutil, "which", side_effect=["/usr/bin/ruff", None]),
            patch.object(lint.subprocess, "call", return_value=0) as call,
        ):
            assert lint._execute("ruff", ["check", "."]) == 0
        call.assert_called_once_with(["/usr/bin/ruff", "check", "."])

    def test_execute_falls_back_to_uv(self) -> None:
        from tempest_fastapi_sdk.cli import lint

        def fake_which(name: str) -> str | None:
            return {"uv": "/usr/local/bin/uv"}.get(name)

        with (
            patch.object(lint.shutil, "which", side_effect=fake_which),
            patch.object(lint.subprocess, "call", return_value=0) as call,
        ):
            assert lint._execute("ruff", ["format"]) == 0
        call.assert_called_once_with(["/usr/local/bin/uv", "run", "ruff", "format"])

    def test_full_check_stops_at_first_failure(self) -> None:
        from tempest_fastapi_sdk.cli import lint

        calls: list[tuple[str, list[str]]] = []

        def fake_execute(executable: str, args: list[str]) -> int:
            calls.append((executable, args))
            return 5 if executable == "mypy" else 0

        with patch.object(lint, "_execute", side_effect=fake_execute):
            assert lint.run_full_check("src/") == 5
        executed = [c[0] for c in calls]
        assert executed == ["ruff", "ruff", "mypy"]

    def test_fix_runs_format_even_when_check_fix_fails(self) -> None:
        from tempest_fastapi_sdk.cli import lint

        calls: list[list[str]] = []

        def fake_execute(executable: str, args: list[str]) -> int:
            calls.append(args)
            # ``ruff check --fix`` exits non-zero on residual unfixable
            # violations; ``ruff format`` still must run after it.
            return 1 if args[0] == "check" else 0

        with patch.object(lint, "_execute", side_effect=fake_execute):
            code = lint.run_ruff_fix("src/")

        assert [a[0] for a in calls] == ["check", "format"]
        # Residual lint exit code is surfaced, but only after format ran.
        assert code == 1

    def test_fix_returns_format_code_when_check_clean(self) -> None:
        from tempest_fastapi_sdk.cli import lint

        def fake_execute(executable: str, args: list[str]) -> int:
            return 0 if args[0] == "check" else 3

        with patch.object(lint, "_execute", side_effect=fake_execute):
            assert lint.run_ruff_fix("src/") == 3


class TestScaffoldHelpers:
    def test_build_sdk_dep_pins_current_version(self) -> None:
        from tempest_fastapi_sdk.cli import new

        dep = new._build_sdk_dep("auth,upload")
        assert dep.startswith("tempest-fastapi-sdk[auth,upload]>=")
        assert __version__ in dep

    def test_build_sdk_dep_skips_empty_extras(self) -> None:
        from tempest_fastapi_sdk.cli import new

        assert new._build_sdk_dep("") == f"tempest-fastapi-sdk>={__version__}"
        assert new._build_sdk_dep(" , ") == f"tempest-fastapi-sdk>={__version__}"

    def test_render_replaces_placeholders(self) -> None:
        from tempest_fastapi_sdk.cli import new

        rendered = new._render(
            "name=__PROJECT_NAME__ host=__HOST__",
            {"PROJECT_NAME": "demo_svc", "HOST": "0.0.0.0"},
        )
        assert rendered == "name=demo_svc host=0.0.0.0"
