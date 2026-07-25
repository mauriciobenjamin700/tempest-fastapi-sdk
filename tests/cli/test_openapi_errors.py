"""Tests for tempest_fastapi_sdk.cli.openapi_errors."""

from pathlib import Path

import pytest
from typer.testing import CliRunner

from tempest_fastapi_sdk.cli.main import app
from tempest_fastapi_sdk.cli.openapi_errors import (
    analyze_paths,
    default_source_paths,
)

EXCEPTIONS_MODULE: str = '''"""Domain exceptions."""

from tempest_fastapi_sdk import ConflictException, NotFoundException


class ServiceNotFoundException(NotFoundException):
    """Service does not exist."""

    code = "SERVICE_NOT_FOUND"


class ServiceFullException(ConflictException):
    """Service reached its candidate limit."""

    code = "SERVICE_FULL"


class CandidateAlreadyExistsException(ConflictException):
    """User already applied."""

    code = "CANDIDATE_ALREADY_EXISTS"
'''

SERVICE_MODULE: str = '''"""Candidate service."""

from src.core.exceptions import (
    CandidateAlreadyExistsException,
    ServiceFullException,
    ServiceNotFoundException,
)


class CandidateService:
    """Business logic for candidates."""

    async def apply(self, service_id: str) -> None:
        """Apply the current user to a service.

        Raises:
            ServiceNotFoundException: If the service does not exist.
        """
        raise ServiceNotFoundException()

    async def validate_slots(self, service_id: str) -> None:
        """Check the service still has open slots.

        Raises:
            ServiceFullException: If the service is full.
            CandidateAlreadyExistsException: If the user already applied.
        """
        raise ServiceFullException()
'''

ROUTER_MODULE: str = '''"""Job routes."""

from tempest_fastapi_sdk import TempestAPIRouter, error_responses, raises

from src.core.exceptions import ServiceFullException, ServiceNotFoundException
from src.services.candidate import CandidateService

router = TempestAPIRouter(prefix="/api/jobs")


@router.post(
    "/{service_id}/candidates",
    responses=error_responses(ServiceNotFoundException),
)
async def under_declared(service_id: str) -> dict[str, str]:
    """Apply to a service — declares one of three reachable exceptions."""
    service = CandidateService()
    await service.apply(service_id)
    await service.validate_slots(service_id)
    return {}


@router.get("/{service_id}")
@raises(ServiceNotFoundException, ServiceFullException)
async def over_declared(service_id: str) -> dict[str, str]:
    """Read a service — declares an exception the flow cannot raise."""
    raise ServiceNotFoundException()


@router.get("/ok/{service_id}")
@raises(ServiceNotFoundException)
async def exact(service_id: str) -> dict[str, str]:
    """Read a service — the declaration matches the flow."""
    raise ServiceNotFoundException()
'''


@pytest.fixture
def service_tree(tmp_path: Path) -> Path:
    """Write a miniature layered service and return its ``src`` directory.

    Args:
        tmp_path (Path): pytest's per-test temporary directory.

    Returns:
        Path: The ``src`` directory holding router, service and
        exceptions modules.
    """
    src = tmp_path / "src"
    (src / "core").mkdir(parents=True)
    (src / "services").mkdir(parents=True)
    (src / "api" / "routers").mkdir(parents=True)
    (src / "core" / "exceptions.py").write_text(EXCEPTIONS_MODULE)
    (src / "services" / "candidate.py").write_text(SERVICE_MODULE)
    (src / "api" / "routers" / "jobs.py").write_text(ROUTER_MODULE)
    return src


class TestAnalyzePaths:
    """The static analyzer finds drift in both directions."""

    def test_finds_undocumented_and_unreachable(self, service_tree: Path) -> None:
        """Two routes drift, the matching one is silent."""
        findings = analyze_paths([service_tree])
        assert len(findings) == 2

    def test_undocumented_crosses_layers(self, service_tree: Path) -> None:
        """Exceptions from the service layer surface on the route.

        The route only declares ``ServiceNotFoundException``, but its
        flow reaches two more through ``CandidateService`` — one from a
        ``raise`` statement, one only from the ``Raises:`` docstring.
        """
        findings = analyze_paths([service_tree])
        under = next(f for f in findings if f.function.name == "under_declared")
        assert under.undocumented == [
            "CandidateAlreadyExistsException",
            "ServiceFullException",
        ]
        assert under.unreachable == []

    def test_over_declaration_is_reported(self, service_tree: Path) -> None:
        """A declared-but-unreachable exception is an inflated list."""
        findings = analyze_paths([service_tree])
        over = next(f for f in findings if f.function.name == "over_declared")
        assert over.unreachable == ["ServiceFullException"]
        assert over.undocumented == []

    def test_matching_route_produces_no_finding(self, service_tree: Path) -> None:
        """A correct declaration is not reported."""
        findings = analyze_paths([service_tree])
        assert all(f.function.name != "exact" for f in findings)

    def test_route_metadata_is_captured(self, service_tree: Path) -> None:
        """Findings carry method, path and a clickable location."""
        findings = analyze_paths([service_tree])
        under = next(f for f in findings if f.function.name == "under_declared")
        assert under.route.method == "POST"
        assert under.route.path == "/{service_id}/candidates"
        assert under.location.endswith("jobs.py:15")

    def test_builtin_exceptions_are_ignored(self, tmp_path: Path) -> None:
        """``raise ValueError`` is not an API error worth documenting."""
        src = tmp_path / "src"
        src.mkdir()
        (src / "routes.py").write_text(
            '"""Routes."""\n\n'
            "from fastapi import APIRouter\n\n"
            "router = APIRouter()\n\n\n"
            '@router.get("/x")\n'
            "async def read() -> None:\n"
            '    """Fail on bad input."""\n'
            '    raise ValueError("nope")\n'
        )
        assert analyze_paths([src]) == []

    def test_unparseable_file_is_skipped(self, service_tree: Path) -> None:
        """One broken file must not blind the whole report."""
        (service_tree / "broken.py").write_text("def (:\n")
        findings = analyze_paths([service_tree])
        assert len(findings) == 2

    def test_starred_declaration_reports_no_declaration(self, tmp_path: Path) -> None:
        """``error_responses(*ALL)`` cannot be resolved statically.

        Reporting the route as undeclared is the honest outcome: the
        analyzer must never claim a declaration it could not read.
        """
        src = tmp_path / "src"
        src.mkdir()
        (src / "routes.py").write_text(
            '"""Routes."""\n\n'
            "from fastapi import APIRouter\n\n"
            "from tempest_fastapi_sdk import NotFoundException, error_responses\n\n\n"
            "class ThingNotFoundException(NotFoundException):\n"
            '    """Thing does not exist."""\n\n'
            '    code = "THING_NOT_FOUND"\n\n\n'
            "ALL = (ThingNotFoundException,)\n"
            "router = APIRouter()\n\n\n"
            '@router.get("/x", responses=error_responses(*ALL))\n'
            "async def read() -> None:\n"
            '    """Read a thing."""\n'
            "    raise ThingNotFoundException()\n"
        )
        findings = analyze_paths([src])
        assert len(findings) == 1
        assert findings[0].undocumented == ["ThingNotFoundException"]

    def test_declaration_merged_from_a_dict_literal(self, tmp_path: Path) -> None:
        """``{**error_responses(A), 418: {...}}`` is read correctly."""
        src = tmp_path / "src"
        src.mkdir()
        (src / "routes.py").write_text(
            '"""Routes."""\n\n'
            "from fastapi import APIRouter\n\n"
            "from tempest_fastapi_sdk import NotFoundException, error_responses\n\n\n"
            "class ThingNotFoundException(NotFoundException):\n"
            '    """Thing does not exist."""\n\n'
            '    code = "THING_NOT_FOUND"\n\n\n'
            "router = APIRouter()\n\n\n"
            "@router.get(\n"
            '    "/x",\n'
            "    responses={\n"
            "        **error_responses(ThingNotFoundException),\n"
            '        418: {"description": "teapot"},\n'
            "    },\n"
            ")\n"
            "async def read() -> None:\n"
            '    """Read a thing."""\n'
            "    raise ThingNotFoundException()\n"
        )
        assert analyze_paths([src]) == []

    def test_missing_path_raises(self, tmp_path: Path) -> None:
        """A typo in ``--path`` fails loudly instead of scanning nothing."""
        with pytest.raises(FileNotFoundError):
            analyze_paths([tmp_path / "nope"])

    def test_single_file_narrows_the_call_graph(self, service_tree: Path) -> None:
        """Scanning one file limits reachability to that file.

        With the service layer out of scope the router's calls resolve to
        nothing, so exceptions raised downstream become invisible and
        every declaration reads as unreachable. ``--path`` decides how
        much of the flow the analyzer can see — point it at the whole
        source tree.
        """
        findings = analyze_paths([service_tree / "api" / "routers" / "jobs.py"])
        assert [f.function.name for f in findings] == [
            "under_declared",
            "over_declared",
        ]
        under = findings[0]
        assert under.unreachable == ["ServiceNotFoundException"]
        assert under.undocumented == []


class TestDefaultSourcePaths:
    """``default_source_paths`` honors both allowed layouts."""

    def test_picks_src(self, tmp_path: Path) -> None:
        """The ``src/`` layout is found."""
        (tmp_path / "src").mkdir()
        assert default_source_paths(tmp_path) == [tmp_path / "src"]

    def test_picks_app(self, tmp_path: Path) -> None:
        """The ``app/`` layout is found."""
        (tmp_path / "app").mkdir()
        assert default_source_paths(tmp_path) == [tmp_path / "app"]

    def test_empty_when_neither_exists(self, tmp_path: Path) -> None:
        """No convention match lets the caller report a clear error."""
        assert default_source_paths(tmp_path) == []


class TestOpenapiErrorsCommand:
    """``tempest openapi-errors`` wires the analyzer to an exit code."""

    def test_check_exits_nonzero_on_drift(self, service_tree: Path) -> None:
        """The command doubles as a CI gate."""
        result = CliRunner().invoke(
            app, ["openapi-errors", "--path", str(service_tree), "--check"]
        )
        assert result.exit_code == 1
        assert "undocumented: " in result.output
        assert "unreachable:  " in result.output

    def test_without_check_exits_zero(self, service_tree: Path) -> None:
        """The report is advisory unless ``--check`` is passed."""
        result = CliRunner().invoke(
            app, ["openapi-errors", "--path", str(service_tree)]
        )
        assert result.exit_code == 0
        assert "2 route(s) with drift" in result.output

    def test_allow_unreachable_ignores_over_declaration(self, tmp_path: Path) -> None:
        """Only the documentation hole fails the gate."""
        src = tmp_path / "src"
        src.mkdir()
        (src / "routes.py").write_text(
            '"""Routes."""\n\n'
            "from fastapi import APIRouter\n\n"
            "from tempest_fastapi_sdk import (\n"
            "    ConflictException,\n"
            "    NotFoundException,\n"
            "    error_responses,\n"
            ")\n\n\n"
            "class ThingNotFoundException(NotFoundException):\n"
            '    """Thing does not exist."""\n\n'
            '    code = "THING_NOT_FOUND"\n\n\n'
            "class ThingConflictException(ConflictException):\n"
            '    """Thing conflicts."""\n\n'
            '    code = "THING_CONFLICT"\n\n\n'
            "router = APIRouter()\n\n\n"
            "@router.get(\n"
            '    "/x",\n'
            "    responses=error_responses(\n"
            "        ThingNotFoundException, ThingConflictException\n"
            "    ),\n"
            ")\n"
            "async def read() -> None:\n"
            '    """Read a thing."""\n'
            "    raise ThingNotFoundException()\n"
        )
        result = CliRunner().invoke(
            app,
            [
                "openapi-errors",
                "--path",
                str(src),
                "--check",
                "--allow-unreachable",
            ],
        )
        assert "unreachable:  ThingConflictException" in result.output
        assert result.exit_code == 0

    def test_clean_project_reports_success(self, tmp_path: Path) -> None:
        """A project in sync prints a green line and exits zero."""
        src = tmp_path / "src"
        src.mkdir()
        (src / "routes.py").write_text('"""No routes here."""\n')
        result = CliRunner().invoke(
            app, ["openapi-errors", "--path", str(src), "--check"]
        )
        assert result.exit_code == 0
        assert "match its flow" in result.output

    def test_missing_source_directory_exits_two(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """No ``src``/``app`` is a usage error, never a passing check.

        Exiting zero here would make the CI step green on a project the
        analyzer never looked at.
        """
        monkeypatch.chdir(tmp_path)
        result = CliRunner().invoke(app, ["openapi-errors", "--check"])
        assert result.exit_code == 2

    def test_bad_path_is_a_parameter_error(self, tmp_path: Path) -> None:
        """A nonexistent ``--path`` is surfaced as a bad parameter."""
        result = CliRunner().invoke(
            app, ["openapi-errors", "--path", str(tmp_path / "nope")]
        )
        assert result.exit_code == 2
