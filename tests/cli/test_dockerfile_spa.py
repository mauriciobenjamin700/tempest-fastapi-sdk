"""Tests for the fullstack (SPA) variant of the generated Dockerfile."""

from __future__ import annotations

from pathlib import Path

import pytest
from typer.testing import CliRunner

from tempest_fastapi_sdk.cli.generate import SPA_CANDIDATE_DIRS, detect_spa_dir
from tempest_fastapi_sdk.cli.main import app

PYPROJECT = '[project]\nname = "svc"\nversion = "0.1.0"\n'


@pytest.fixture
def project(tmp_path: Path) -> Path:
    """Create a backend-only project root.

    Args:
        tmp_path (Path): pytest's per-test temporary directory.

    Returns:
        Path: The project root, holding only a ``pyproject.toml``.
    """
    (tmp_path / "pyproject.toml").write_text(PYPROJECT)
    return tmp_path


def _add_spa(root: Path, name: str = "web") -> Path:
    """Add a frontend package to ``root``.

    Args:
        root (Path): The project root.
        name (str): The frontend directory name.

    Returns:
        Path: The frontend directory.
    """
    web = root / name
    web.mkdir(parents=True, exist_ok=True)
    (web / "package.json").write_text('{"name": "web"}')
    return web


def _generate(root: Path, *extra: str) -> str:
    """Run ``tempest generate --dockerfile`` against ``root``.

    Args:
        root (Path): The project root.
        *extra (str): Additional CLI arguments.

    Returns:
        str: The command output.
    """
    result = CliRunner().invoke(
        app, ["generate", "--dockerfile", "--path", str(root), "--force", *extra]
    )
    return result.output


class TestDetection:
    """A frontend is detected by its ``package.json``, not by the directory."""

    def test_no_frontend(self, project: Path) -> None:
        """A backend-only project detects nothing."""
        assert detect_spa_dir(project) is None

    @pytest.mark.parametrize("name", SPA_CANDIDATE_DIRS)
    def test_each_candidate_directory(self, project: Path, name: str) -> None:
        """Every conventional frontend directory name is recognized."""
        _add_spa(project, name)
        assert detect_spa_dir(project) == name

    def test_empty_directory_is_not_a_frontend(self, project: Path) -> None:
        """An empty ``web/`` must not produce a stage that fails at ``npm ci``.

        Detecting on the directory alone would emit a Node stage for a
        placeholder folder, and the image build would die inside the
        install step with a message pointing nowhere useful.
        """
        (project / "web").mkdir()
        assert detect_spa_dir(project) is None


class TestBackendOnly:
    """Without a frontend the output is exactly what it always was."""

    def test_no_node_stage(self, project: Path) -> None:
        """No Node stage, no SPA copy, no frontend ignore block."""
        _generate(project)
        dockerfile = (project / "Dockerfile").read_text()
        dockerignore = (project / ".dockerignore").read_text()
        assert "FROM node" not in dockerfile
        assert "COPY --from=spa" not in dockerfile
        assert "node_modules" not in dockerignore

    def test_no_blank_line_artifacts(self, project: Path) -> None:
        """Empty placeholders leave no stray blank lines behind.

        The SPA blocks are rendered by substituting empty strings, so a
        careless template would leave the backend-only Dockerfile subtly
        different from the file every existing project already has.
        """
        _generate(project)
        dockerfile = (project / "Dockerfile").read_text()
        assert "\n\n\n" not in dockerfile


class TestFullstack:
    """With a frontend the build compiles it in a Node stage."""

    @pytest.fixture
    def fullstack(self, project: Path) -> Path:
        """A project root that also holds a frontend.

        Args:
            project (Path): The backend-only root.

        Returns:
            Path: The same root, now with ``web/package.json``.
        """
        _add_spa(project)
        return project

    def test_node_stage_is_emitted(self, fullstack: Path) -> None:
        """A dedicated stage installs and builds the frontend."""
        _generate(fullstack)
        dockerfile = (fullstack / "Dockerfile").read_text()
        assert "FROM node:22-alpine AS spa" in dockerfile
        assert "RUN npm run build" in dockerfile

    def test_lockfile_is_optional(self, fullstack: Path) -> None:
        """``npm ci`` needs a lockfile, so the template falls back.

        A freshly scaffolded frontend has no ``package-lock.json`` yet;
        without the fallback the very first image build would fail.
        """
        _generate(fullstack)
        dockerfile = (fullstack / "Dockerfile").read_text()
        assert "package-lock.json*" in dockerfile
        assert "if [ -f package-lock.json ]; then npm ci; else npm install; fi" in (
            dockerfile
        )

    def test_only_dist_crosses_into_the_runtime(self, fullstack: Path) -> None:
        """Only the built ``dist/`` crosses from the Node stage.

        Asserted on the ``COPY --from=spa`` statements rather than on the
        word "node": the runtime stage legitimately mentions it in prose
        (the ``/app`` directory *node*), so a substring check would pass or
        fail for the wrong reason.
        """
        _generate(fullstack)
        dockerfile = (fullstack / "Dockerfile").read_text()
        final_stage = dockerfile.split("# ---- final")[1]
        copies = [
            line.strip()
            for line in final_stage.splitlines()
            if line.startswith("COPY --from=spa")
        ]
        assert copies == ["COPY --from=spa --chown=app:app /spa/dist /app/web/dist"]
        assert "FROM node" not in final_stage
        assert "npm" not in final_stage

    def test_dockerignore_excludes_the_frontend_build(self, fullstack: Path) -> None:
        """A local ``dist/`` must not be shipped instead of the built one."""
        _generate(fullstack)
        dockerignore = (fullstack / ".dockerignore").read_text()
        assert "web/node_modules/" in dockerignore
        assert "web/dist/" in dockerignore

    def test_header_points_at_the_router(self, fullstack: Path) -> None:
        """The generated comment names the helper that serves the build."""
        _generate(fullstack)
        assert 'make_spa_router("web/dist")' in (fullstack / "Dockerfile").read_text()

    def test_reports_the_stage(self, fullstack: Path) -> None:
        """The command says the image became fullstack."""
        assert "SPA stage: builds web/" in _generate(fullstack)


class TestFlags:
    """``--spa-dir`` and ``--no-spa`` override the detection."""

    def test_no_spa_forces_backend_only(self, project: Path) -> None:
        """A project with a frontend can still build a backend-only image."""
        _add_spa(project)
        _generate(project, "--no-spa")
        assert "FROM node" not in (project / "Dockerfile").read_text()

    def test_spa_dir_selects_a_custom_directory(self, project: Path) -> None:
        """An unconventional layout is supported explicitly."""
        _add_spa(project, "apps-web")
        _generate(project, "--spa-dir", "apps-web")
        dockerfile = (project / "Dockerfile").read_text()
        assert "COPY apps-web/package.json" in dockerfile
        assert "/app/apps-web/dist" in dockerfile

    def test_spa_dir_without_a_package_fails(self, project: Path) -> None:
        """A typo'd ``--spa-dir`` is reported, not silently ignored.

        Falling back to a backend-only image here would produce a build
        that runs and serves no frontend, which is discovered late.
        """
        result = CliRunner().invoke(
            app,
            [
                "generate",
                "--dockerfile",
                "--path",
                str(project),
                "--force",
                "--spa-dir",
                "nope",
            ],
        )
        assert result.exit_code == 1
        assert "must point at a frontend package" in result.output
