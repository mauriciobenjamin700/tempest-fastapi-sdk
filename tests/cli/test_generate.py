"""Tests for ``tempest generate --docker``."""

from __future__ import annotations

from pathlib import Path

from typer.testing import CliRunner

from tempest_fastapi_sdk.cli.generate import (
    _discover_extras,
    _discover_project_name,
)
from tempest_fastapi_sdk.cli.main import app

runner = CliRunner()


def _seed_project(
    target: Path,
    *,
    name: str,
    extras: str,
) -> None:
    """Write a minimal pyproject.toml so the generator can read it."""
    extras_fragment = f"[{extras}]" if extras else ""
    target.mkdir(parents=True, exist_ok=True)
    (target / "pyproject.toml").write_text(
        f"""\
[project]
name = "{name}"
version = "0.1.0"
dependencies = [
    "tempest-fastapi-sdk{extras_fragment}>=0.25.0",
]
""",
        encoding="utf-8",
    )


class TestDiscovery:
    def test_extras_with_brackets(self) -> None:
        text = '"tempest-fastapi-sdk[auth,upload,minio]>=0.25.0",'
        assert _discover_extras(text) == "auth,upload,minio"

    def test_extras_without_brackets(self) -> None:
        text = '"tempest-fastapi-sdk>=0.25.0",'
        assert _discover_extras(text) == ""

    def test_extras_missing_returns_empty(self) -> None:
        assert _discover_extras("# nothing matches here") == ""

    def test_project_name(self) -> None:
        assert _discover_project_name('name = "my_api"', "fallback") == "my_api"

    def test_project_name_fallback(self) -> None:
        assert _discover_project_name("no name here", "fb") == "fb"


class TestGenerateDocker:
    def test_regenerates_from_pyproject(self, tmp_path: Path) -> None:
        _seed_project(tmp_path, name="my_svc", extras="auth,cache,minio")
        result = runner.invoke(
            app,
            ["generate", "--docker", "--path", str(tmp_path)],
        )
        assert result.exit_code == 0, result.stdout + result.stderr
        compose = (tmp_path / "docker-compose.yaml").read_text()
        assert "container_name: my_svc-postgres" in compose
        assert "redis:" in compose
        assert "minio:" in compose
        assert "rabbitmq:" not in compose  # only [cache] + [minio]
        assert "image: postgres:18-alpine" in compose
        assert "image: redis:8-alpine" in compose

    def test_minio_bumps_use_pinned_release_tags(self, tmp_path: Path) -> None:
        _seed_project(tmp_path, name="svc", extras="minio")
        runner.invoke(app, ["generate", "--docker", "--path", str(tmp_path)])
        compose = (tmp_path / "docker-compose.yaml").read_text()
        assert "minio/minio:RELEASE" in compose

    def test_refuses_overwrite_without_force(self, tmp_path: Path) -> None:
        _seed_project(tmp_path, name="svc", extras="auth")
        (tmp_path / "docker-compose.yaml").write_text("hand-edited", encoding="utf-8")
        result = runner.invoke(
            app,
            ["generate", "--docker", "--path", str(tmp_path)],
        )
        assert result.exit_code == 1
        assert (tmp_path / "docker-compose.yaml").read_text() == "hand-edited"

    def test_force_overwrites(self, tmp_path: Path) -> None:
        _seed_project(tmp_path, name="svc", extras="auth")
        (tmp_path / "docker-compose.yaml").write_text("hand-edited", encoding="utf-8")
        result = runner.invoke(
            app,
            ["generate", "--docker", "--path", str(tmp_path), "--force"],
        )
        assert result.exit_code == 0
        compose = (tmp_path / "docker-compose.yaml").read_text()
        assert "postgres:" in compose
        assert "hand-edited" not in compose

    def test_extras_override(self, tmp_path: Path) -> None:
        _seed_project(tmp_path, name="svc", extras="auth")
        result = runner.invoke(
            app,
            [
                "generate",
                "--docker",
                "--path",
                str(tmp_path),
                "--extras",
                "queue,email",
            ],
        )
        assert result.exit_code == 0
        compose = (tmp_path / "docker-compose.yaml").read_text()
        assert "rabbitmq:" in compose
        assert "mailhog:" in compose

    def test_name_override(self, tmp_path: Path) -> None:
        _seed_project(tmp_path, name="my_svc", extras="auth")
        runner.invoke(
            app,
            ["generate", "--docker", "--path", str(tmp_path), "--name", "alt"],
        )
        compose = (tmp_path / "docker-compose.yaml").read_text()
        assert "container_name: alt-postgres" in compose
        assert "container_name: my_svc-postgres" not in compose

    def test_missing_docker_flag_errors(self, tmp_path: Path) -> None:
        result = runner.invoke(app, ["generate", "--path", str(tmp_path)])
        assert result.exit_code == 2
        assert "pass --docker" in (result.stdout + result.stderr)

    def test_missing_pyproject_errors(self, tmp_path: Path) -> None:
        result = runner.invoke(
            app,
            ["generate", "--docker", "--path", str(tmp_path)],
        )
        assert result.exit_code == 2
        assert "pyproject.toml" in (result.stdout + result.stderr)

    def test_env_example_addendum_is_idempotent(self, tmp_path: Path) -> None:
        _seed_project(tmp_path, name="svc", extras="cache")
        (tmp_path / ".env.example").write_text(
            "# Server\nSERVER_HOST=127.0.0.1\n",
            encoding="utf-8",
        )
        runner.invoke(app, ["generate", "--docker", "--path", str(tmp_path)])
        first = (tmp_path / ".env.example").read_text()
        # Re-run shouldn't duplicate the Redis block.
        runner.invoke(app, ["generate", "--docker", "--path", str(tmp_path), "--force"])
        second = (tmp_path / ".env.example").read_text()
        assert second.count("REDIS_URL") == 1
        assert first == second
