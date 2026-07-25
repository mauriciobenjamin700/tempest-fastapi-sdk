"""Tests for `tempest openapi-errors --fix`."""

from __future__ import annotations

import ast
import subprocess
from pathlib import Path

import pytest
from typer.testing import CliRunner

from tempest_fastapi_sdk.cli.main import app
from tempest_fastapi_sdk.cli.openapi_fix import (
    DirtyWorkingTreeError,
    ensure_clean_worktree,
)

EXCEPTIONS = '''"""Domain exceptions."""

from tempest_fastapi_sdk import ConflictException, NotFoundException


class ServiceNotFoundException(NotFoundException):
    """Service does not exist."""

    code: str = "SERVICE_NOT_FOUND"


class ServiceFullException(ConflictException):
    """Service reached its candidate limit."""

    code: str = "SERVICE_FULL"
'''

SERVICE = '''"""Candidate service."""

from src.core.exceptions import ServiceFullException, ServiceNotFoundException


class CandidateService:
    """Business logic for candidates."""

    async def apply(self, service_id: str) -> None:
        """Apply to a service.

        Raises:
            ServiceNotFoundException: If the service does not exist.
        """
        raise ServiceNotFoundException()

    async def validate_slots(self, service_id: str) -> None:
        """Check open slots.

        Raises:
            ServiceFullException: If the service is full.
        """
        raise ServiceFullException()
'''

BARE_ROUTER = '''"""Job routes."""

from fastapi import APIRouter

from src.services.candidate import CandidateService

router = APIRouter(prefix="/api/jobs")


@router.post("/{service_id}/candidates", status_code=201)
async def apply_to_service(service_id: str) -> dict[str, str]:
    """Apply the current user to a service."""
    service = CandidateService()
    await service.apply(service_id)
    await service.validate_slots(service_id)
    return {}
'''

PARTIAL_ROUTER = '''"""Job routes."""

from fastapi import APIRouter
from tempest_fastapi_sdk import error_responses

from src.core.exceptions import ServiceNotFoundException
from src.services.candidate import CandidateService

router = APIRouter(prefix="/api/jobs")


@router.get(
    "/{service_id}",
    responses=error_responses(ServiceNotFoundException),
)
async def read_service(service_id: str) -> dict[str, str]:
    """Read a service."""
    service = CandidateService()
    await service.validate_slots(service_id)
    return {}
'''


def _git(root: Path, *args: str) -> None:
    """Run a git command inside ``root``.

    Args:
        root (Path): Repository directory.
        *args (str): Command arguments after ``git``.
    """
    subprocess.run(
        ["git", "-c", "user.email=t@t", "-c", "user.name=t", *args],
        cwd=root,
        check=True,
        capture_output=True,
    )


@pytest.fixture
def project(tmp_path: Path) -> Path:
    """Build a committed miniature service with one undeclared route.

    Args:
        tmp_path (Path): pytest's per-test temporary directory.

    Returns:
        Path: The project root, a clean git repository.
    """
    (tmp_path / "src" / "core").mkdir(parents=True)
    (tmp_path / "src" / "services").mkdir(parents=True)
    (tmp_path / "src" / "api" / "routers").mkdir(parents=True)
    (tmp_path / "src" / "core" / "exceptions.py").write_text(EXCEPTIONS)
    (tmp_path / "src" / "services" / "candidate.py").write_text(SERVICE)
    (tmp_path / "src" / "api" / "routers" / "jobs.py").write_text(BARE_ROUTER)
    _git(tmp_path, "init", "-q", ".")
    _git(tmp_path, "add", "-A")
    _git(tmp_path, "commit", "-qm", "init")
    return tmp_path


def _run(root: Path, *args: str, monkeypatch: pytest.MonkeyPatch) -> str:
    """Invoke the CLI with ``root`` as the working directory.

    Args:
        root (Path): Project root.
        *args (str): Extra CLI arguments.
        monkeypatch (pytest.MonkeyPatch): Used to chdir.

    Returns:
        str: The command output.
    """
    monkeypatch.chdir(root)
    return CliRunner().invoke(app, ["openapi-errors", *args]).output


class TestDryRun:
    """``--dry-run`` previews without touching anything."""

    def test_prints_a_diff(
        self, project: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """The preview is a unified diff of the rewrite."""
        output = _run(project, "--fix", "--dry-run", monkeypatch=monkeypatch)
        assert "--- a/src/api/routers/jobs.py" in output
        assert "+    responses=error_responses(" in output

    def test_writes_nothing(
        self, project: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """The file on disk is untouched."""
        before = (project / "src" / "api" / "routers" / "jobs.py").read_text()
        _run(project, "--fix", "--dry-run", monkeypatch=monkeypatch)
        assert (project / "src" / "api" / "routers" / "jobs.py").read_text() == before

    def test_preview_matches_the_write(
        self, project: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """The diff shows the *formatted* result, not the raw splice.

        A preview that differs from what the write produces is worse than
        no preview, so both go through the same normalization.
        """
        preview = _run(project, "--fix", "--dry-run", monkeypatch=monkeypatch)
        added = [
            line[1:]
            for line in preview.splitlines()
            if line.startswith("+") and not line.startswith("+++")
        ]
        _run(project, "--fix", monkeypatch=monkeypatch)
        written = (project / "src" / "api" / "routers" / "jobs.py").read_text()
        for line in added:
            assert line in written


class TestWrite:
    """``--fix`` produces code that imports and documents the route."""

    def test_injects_the_declaration(
        self, project: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """The route decorator gains ``responses=error_responses(...)``."""
        _run(project, "--fix", monkeypatch=monkeypatch)
        source = (project / "src" / "api" / "routers" / "jobs.py").read_text()
        assert "error_responses(" in source
        assert "ServiceNotFoundException" in source
        assert "ServiceFullException" in source

    def test_adds_the_imports(
        self, project: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """The helper and the exception classes are imported.

        Without this the rewrite would produce a ``NameError`` at import
        time — a fix that breaks the app is worse than the gap it closed.
        """
        _run(project, "--fix", monkeypatch=monkeypatch)
        source = (project / "src" / "api" / "routers" / "jobs.py").read_text()
        assert "from tempest_fastapi_sdk import error_responses" in source
        assert "from src.core.exceptions import" in source

    def test_result_is_valid_python(
        self, project: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """The rewritten file parses."""
        _run(project, "--fix", monkeypatch=monkeypatch)
        ast.parse((project / "src" / "api" / "routers" / "jobs.py").read_text())

    def test_is_idempotent(
        self, project: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """A second pass finds nothing left to declare."""
        _run(project, "--fix", monkeypatch=monkeypatch)
        _git(project, "add", "-A")
        _git(project, "commit", "-qm", "fixed")
        assert "match its flow" in _run(project, monkeypatch=monkeypatch)

    def test_reports_what_it_wrote(
        self, project: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """The summary names the undo command."""
        output = _run(project, "--fix", monkeypatch=monkeypatch)
        assert "Wrote 1 file(s)" in output
        assert "git checkout" in output


class TestMerge:
    """An existing declaration is extended, never replaced."""

    def test_appends_to_an_existing_call(
        self, project: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """The already-declared name keeps its place; the missing one joins."""
        path = project / "src" / "api" / "routers" / "jobs.py"
        path.write_text(PARTIAL_ROUTER)
        _git(project, "add", "-A")
        _git(project, "commit", "-qm", "partial")
        _run(project, "--fix", monkeypatch=monkeypatch)
        source = path.read_text()
        assert source.count("error_responses(") == 1
        call = source.split("error_responses(")[1]
        assert call.index("ServiceNotFoundException") < call.index(
            "ServiceFullException"
        )

    def test_does_not_duplicate_the_import(
        self, project: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """A class already imported is not imported twice."""
        path = project / "src" / "api" / "routers" / "jobs.py"
        path.write_text(PARTIAL_ROUTER)
        _git(project, "add", "-A")
        _git(project, "commit", "-qm", "partial")
        _run(project, "--fix", monkeypatch=monkeypatch)
        assert path.read_text().count("ServiceNotFoundException,") <= 1

    def test_never_removes_unreachable(
        self, project: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """A declared-but-unreachable name survives the rewrite.

        Reachability is resolved by call name and cannot see a dynamic
        raise, so acting on ``unreachable`` could delete a correct
        declaration. The fixer only ever adds.
        """
        path = project / "src" / "api" / "routers" / "jobs.py"
        path.write_text(PARTIAL_ROUTER)
        _git(project, "add", "-A")
        _git(project, "commit", "-qm", "partial")
        output = _run(project, "--fix", monkeypatch=monkeypatch)
        assert "unreachable:  ServiceNotFoundException" in output
        assert "ServiceNotFoundException" in path.read_text()


class TestSafety:
    """A dirty tree is refused, so the diff stays the review."""

    def test_dirty_tree_is_refused(
        self, project: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Exits 1 without writing when there are uncommitted changes."""
        (project / "src" / "stray.py").write_text("x = 1\n")
        monkeypatch.chdir(project)
        result = CliRunner().invoke(app, ["openapi-errors", "--fix"])
        assert result.exit_code == 1
        assert "uncommitted changes" in result.output
        assert (
            "error_responses"
            not in (project / "src" / "api" / "routers" / "jobs.py").read_text()
        )

    def test_dry_run_works_on_a_dirty_tree(
        self, project: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Previewing is read-only, so it needs no clean tree."""
        (project / "src" / "stray.py").write_text("x = 1\n")
        output = _run(project, "--fix", "--dry-run", monkeypatch=monkeypatch)
        assert "Dry run" in output

    def test_ensure_clean_worktree_raises(self, project: Path) -> None:
        """The guard itself reports the reason and the fix."""
        (project / "dirty.txt").write_text("x")
        with pytest.raises(DirtyWorkingTreeError, match="Commit or stash"):
            ensure_clean_worktree(project)

    def test_outside_a_repository_is_allowed(self, tmp_path: Path) -> None:
        """A non-repository directory is not blocked.

        Requiring git would make the tool unusable outside version control;
        the guard exists to protect a review workflow that only exists when
        there is a repository to review in.
        """
        ensure_clean_worktree(tmp_path)


class TestNothingToDo:
    """A project already in sync is left alone."""

    def test_reports_and_writes_nothing(
        self, project: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """No routes, no rewrite."""
        (project / "src" / "api" / "routers" / "jobs.py").write_text(
            '"""No routes."""\n'
        )
        _git(project, "add", "-A")
        _git(project, "commit", "-qm", "empty")
        output = _run(project, "--fix", monkeypatch=monkeypatch)
        assert "match its flow" in output
