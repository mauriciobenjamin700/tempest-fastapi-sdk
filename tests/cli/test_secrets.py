"""Tests for ``tempest secrets rotate``."""

from __future__ import annotations

from pathlib import Path

from typer.testing import CliRunner

from tempest_fastapi_sdk.cli.main import app

runner = CliRunner()


class TestPrint:
    def test_print_does_not_write_env(self, tmp_path: Path) -> None:
        env = tmp_path / ".env"
        result = runner.invoke(app, ["secrets", "rotate", "--print", "--env", str(env)])
        assert result.exit_code == 0, result.stdout
        assert "JWT_SECRET=" in result.stdout
        assert "TOKEN_SECRET=" in result.stdout
        assert not env.exists()


class TestWrite:
    def test_replaces_existing_and_appends_missing(self, tmp_path: Path) -> None:
        env = tmp_path / ".env"
        env.write_text("JWT_SECRET=old\nDEBUG=true\n", encoding="utf-8")

        result = runner.invoke(app, ["secrets", "rotate", "--env", str(env)])
        assert result.exit_code == 0, result.stdout

        content = env.read_text(encoding="utf-8")
        assert "JWT_SECRET=old" not in content
        assert "DEBUG=true" in content  # untouched
        assert "TOKEN_SECRET=" in content  # appended

    def test_backup_written(self, tmp_path: Path) -> None:
        env = tmp_path / ".env"
        env.write_text("JWT_SECRET=old\n", encoding="utf-8")
        runner.invoke(app, ["secrets", "rotate", "--env", str(env)])
        backup = tmp_path / ".env.bak"
        assert backup.exists()
        assert backup.read_text(encoding="utf-8") == "JWT_SECRET=old\n"

    def test_no_backup_flag(self, tmp_path: Path) -> None:
        env = tmp_path / ".env"
        env.write_text("JWT_SECRET=old\n", encoding="utf-8")
        runner.invoke(app, ["secrets", "rotate", "--env", str(env), "--no-backup"])
        assert not (tmp_path / ".env.bak").exists()

    def test_custom_keys(self, tmp_path: Path) -> None:
        env = tmp_path / ".env"
        result = runner.invoke(
            app,
            ["secrets", "rotate", "--env", str(env), "--keys", "FOO,BAR"],
        )
        assert result.exit_code == 0
        content = env.read_text(encoding="utf-8")
        assert "FOO=" in content
        assert "BAR=" in content
        assert "JWT_SECRET=" not in content

    def test_secrets_differ_each_run(self, tmp_path: Path) -> None:
        env = tmp_path / ".env"
        runner.invoke(app, ["secrets", "rotate", "--env", str(env)])
        first = env.read_text(encoding="utf-8")
        runner.invoke(app, ["secrets", "rotate", "--env", str(env)])
        second = env.read_text(encoding="utf-8")
        assert first != second


class TestValidation:
    def test_empty_keys_errors(self, tmp_path: Path) -> None:
        result = runner.invoke(
            app,
            ["secrets", "rotate", "--env", str(tmp_path / ".env"), "--keys", " "],
        )
        assert result.exit_code == 2
