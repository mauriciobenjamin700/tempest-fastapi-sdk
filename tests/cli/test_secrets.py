"""Tests for ``tempest secrets generate|init|rotate|vapid``."""

from __future__ import annotations

import base64
import json
import stat
from pathlib import Path

import pytest
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


class TestPermissions:
    """The rewritten files hold live secrets, so only the owner may read them.

    The process umask on a shared host commonly yields ``0644``.
    """

    def test_env_is_owner_only(self, tmp_path: Path) -> None:
        env = tmp_path / ".env"
        env.write_text("JWT_SECRET=old\n", encoding="utf-8")
        env.chmod(0o644)

        result = runner.invoke(app, ["secrets", "rotate", "--env", str(env)])
        assert result.exit_code == 0, result.stdout
        assert stat.S_IMODE(env.stat().st_mode) == 0o600

    def test_backup_is_owner_only(self, tmp_path: Path) -> None:
        env = tmp_path / ".env"
        env.write_text("JWT_SECRET=old\n", encoding="utf-8")

        runner.invoke(app, ["secrets", "rotate", "--env", str(env)])

        backup = tmp_path / ".env.bak"
        assert backup.is_file()
        assert stat.S_IMODE(backup.stat().st_mode) == 0o600

    def test_a_created_env_is_owner_only(self, tmp_path: Path) -> None:
        env = tmp_path / ".env"
        result = runner.invoke(app, ["secrets", "rotate", "--env", str(env)])
        assert result.exit_code == 0, result.stdout
        assert stat.S_IMODE(env.stat().st_mode) == 0o600


class TestGenerate:
    def test_prints_one_bare_secret_and_writes_nothing(self, tmp_path: Path) -> None:
        result = runner.invoke(app, ["secrets", "generate"])
        assert result.exit_code == 0, result.stdout
        printed = result.stdout.strip().splitlines()
        assert len(printed) == 1
        assert "=" not in printed[0]
        assert not (tmp_path / ".env").exists()

    def test_count_prints_distinct_values(self) -> None:
        result = runner.invoke(app, ["secrets", "generate", "--count", "3"])
        assert result.exit_code == 0, result.stdout
        printed = result.stdout.strip().splitlines()
        assert len(printed) == 3
        assert len(set(printed)) == 3

    def test_keys_label_the_output(self) -> None:
        result = runner.invoke(
            app,
            ["secrets", "generate", "--keys", "JWT_SECRET,TOKEN_SECRET"],
        )
        assert result.exit_code == 0, result.stdout
        printed = result.stdout.strip().splitlines()
        assert [line.split("=")[0] for line in printed] == [
            "JWT_SECRET",
            "TOKEN_SECRET",
        ]

    def test_json_object_with_keys(self) -> None:
        result = runner.invoke(
            app,
            ["secrets", "generate", "--keys", "JWT_SECRET", "--json"],
        )
        assert result.exit_code == 0, result.stdout
        payload = json.loads(result.stdout)
        assert list(payload) == ["JWT_SECRET"]
        assert payload["JWT_SECRET"]

    def test_json_array_without_keys(self) -> None:
        result = runner.invoke(app, ["secrets", "generate", "--count", "2", "--json"])
        assert result.exit_code == 0, result.stdout
        payload = json.loads(result.stdout)
        assert isinstance(payload, list)
        assert len(payload) == 2

    def test_length_grows_the_value(self) -> None:
        short = runner.invoke(app, ["secrets", "generate", "--length", "16"])
        long = runner.invoke(app, ["secrets", "generate", "--length", "64"])
        assert len(long.stdout.strip()) > len(short.stdout.strip())

    def test_count_with_keys_is_refused(self) -> None:
        result = runner.invoke(
            app,
            ["secrets", "generate", "--keys", "A,B", "--count", "2"],
        )
        assert result.exit_code == 2

    def test_repeated_key_is_refused(self) -> None:
        result = runner.invoke(app, ["secrets", "generate", "--keys", "A,A"])
        assert result.exit_code == 2


class TestInit:
    def test_fills_a_missing_key(self, tmp_path: Path) -> None:
        env = tmp_path / ".env"
        env.write_text("DEBUG=true\n", encoding="utf-8")

        result = runner.invoke(app, ["secrets", "init", "--env", str(env)])
        assert result.exit_code == 0, result.stdout

        content = env.read_text(encoding="utf-8")
        assert "DEBUG=true" in content
        assert "JWT_SECRET=" in content
        assert "TOKEN_SECRET=" in content

    def test_replaces_the_scaffold_placeholder(self, tmp_path: Path) -> None:
        env = tmp_path / ".env"
        env.write_text(
            "JWT_SECRET=change-me-change-me-change-me-32\nTOKEN_SECRET=\n",
            encoding="utf-8",
        )

        result = runner.invoke(app, ["secrets", "init", "--env", str(env)])
        assert result.exit_code == 0, result.stdout

        content = env.read_text(encoding="utf-8")
        assert "change-me" not in content
        assert "TOKEN_SECRET=\n" not in content

    def test_keeps_a_configured_value(self, tmp_path: Path) -> None:
        env = tmp_path / ".env"
        env.write_text("JWT_SECRET=already-a-real-secret\n", encoding="utf-8")

        result = runner.invoke(app, ["secrets", "init", "--env", str(env)])
        assert result.exit_code == 0, result.stdout
        assert "JWT_SECRET=already-a-real-secret" in env.read_text(encoding="utf-8")
        assert "kept JWT_SECRET" in result.stdout

    def test_is_idempotent(self, tmp_path: Path) -> None:
        env = tmp_path / ".env"
        runner.invoke(app, ["secrets", "init", "--env", str(env)])
        first = env.read_text(encoding="utf-8")

        second_run = runner.invoke(app, ["secrets", "init", "--env", str(env)])
        assert second_run.exit_code == 0, second_run.stdout
        assert env.read_text(encoding="utf-8") == first
        assert "Nothing to do." in second_run.stdout

    def test_no_backup_when_nothing_is_lost(self, tmp_path: Path) -> None:
        env = tmp_path / ".env"
        env.write_text("JWT_SECRET=change-me\n", encoding="utf-8")
        runner.invoke(app, ["secrets", "init", "--env", str(env)])
        assert not (tmp_path / ".env.bak").exists()

    def test_force_replaces_and_backs_up(self, tmp_path: Path) -> None:
        env = tmp_path / ".env"
        env.write_text("JWT_SECRET=already-a-real-secret\n", encoding="utf-8")

        result = runner.invoke(app, ["secrets", "init", "--env", str(env), "--force"])
        assert result.exit_code == 0, result.stdout
        assert "already-a-real-secret" not in env.read_text(encoding="utf-8")

        backup = tmp_path / ".env.bak"
        assert (
            backup.read_text(encoding="utf-8") == "JWT_SECRET=already-a-real-secret\n"
        )

    def test_env_is_owner_only(self, tmp_path: Path) -> None:
        env = tmp_path / ".env"
        result = runner.invoke(app, ["secrets", "init", "--env", str(env)])
        assert result.exit_code == 0, result.stdout
        assert stat.S_IMODE(env.stat().st_mode) == 0o600


class TestVapid:
    def test_writes_the_pair(self, tmp_path: Path) -> None:
        env = tmp_path / ".env"
        result = runner.invoke(app, ["secrets", "vapid", "--env", str(env)])
        assert result.exit_code == 0, result.stdout

        content = env.read_text(encoding="utf-8")
        assert "VAPID_PUBLIC_KEY=" in content
        assert "VAPID_PRIVATE_KEY=" in content
        assert "VAPID_SUBJECT=" not in content

    def test_subject_is_written_when_given(self, tmp_path: Path) -> None:
        env = tmp_path / ".env"
        runner.invoke(
            app,
            [
                "secrets",
                "vapid",
                "--env",
                str(env),
                "--subject",
                "mailto:ops@example.com",
            ],
        )
        assert "VAPID_SUBJECT=mailto:ops@example.com" in env.read_text(encoding="utf-8")

    def test_key_sizes_are_the_web_push_shapes(self, tmp_path: Path) -> None:
        env = tmp_path / ".env"
        runner.invoke(app, ["secrets", "vapid", "--env", str(env)])
        values = dict(
            line.split("=", 1)
            for line in env.read_text(encoding="utf-8").splitlines()
            if "=" in line
        )

        public_raw = base64.urlsafe_b64decode(values["VAPID_PUBLIC_KEY"] + "==")
        private_raw = base64.urlsafe_b64decode(values["VAPID_PRIVATE_KEY"] + "==")
        assert len(public_raw) == 65
        assert public_raw[0] == 0x04
        assert len(private_raw) == 32

    def test_py_vapid_reads_the_private_key_back(self, tmp_path: Path) -> None:
        """The generated private key is what ``pywebpush`` signs with.

        Asserting the byte length only proves the encoding; this reads
        the value back through the library the dispatcher hands it to
        and signs a header with it.
        """
        vapid_module = pytest.importorskip("py_vapid")

        env = tmp_path / ".env"
        runner.invoke(app, ["secrets", "vapid", "--env", str(env)])
        values = dict(
            line.split("=", 1)
            for line in env.read_text(encoding="utf-8").splitlines()
            if "=" in line
        )

        vapid = vapid_module.Vapid02.from_string(values["VAPID_PRIVATE_KEY"])
        headers = vapid.sign(
            {"aud": "https://fcm.googleapis.com", "sub": "mailto:ops@example.com"}
        )
        assert headers["Authorization"].startswith("vapid ")

        from cryptography.hazmat.primitives import serialization

        derived = vapid.public_key.public_bytes(
            serialization.Encoding.X962,
            serialization.PublicFormat.UncompressedPoint,
        )
        assert (
            base64.urlsafe_b64encode(derived).decode().rstrip("=")
            == (values["VAPID_PUBLIC_KEY"])
        )

    def test_refuses_to_overwrite_without_force(self, tmp_path: Path) -> None:
        env = tmp_path / ".env"
        runner.invoke(app, ["secrets", "vapid", "--env", str(env)])
        before = env.read_text(encoding="utf-8")

        result = runner.invoke(app, ["secrets", "vapid", "--env", str(env)])
        assert result.exit_code == 1
        assert env.read_text(encoding="utf-8") == before

    def test_force_replaces_the_pair(self, tmp_path: Path) -> None:
        env = tmp_path / ".env"
        runner.invoke(app, ["secrets", "vapid", "--env", str(env)])
        before = env.read_text(encoding="utf-8")

        result = runner.invoke(app, ["secrets", "vapid", "--env", str(env), "--force"])
        assert result.exit_code == 0, result.stdout
        assert env.read_text(encoding="utf-8") != before

    def test_print_writes_nothing(self, tmp_path: Path) -> None:
        env = tmp_path / ".env"
        result = runner.invoke(app, ["secrets", "vapid", "--env", str(env), "--print"])
        assert result.exit_code == 0, result.stdout
        assert "VAPID_PUBLIC_KEY=" in result.stdout
        assert not env.exists()
