"""``make publish`` checks everything before it uploads anything.

``scripts/publish.py`` replaced the release workflow on 2026-11-09. Its
safety is the order of :data:`STEPS`: the tag, the four versions, the lock
and the audit are checked, the distributions are built and inspected, and
only then do the two irreversible steps run -- the upload and the GitHub
Release. Moving a check after the upload, or dropping one, breaks nothing
visible on a good release; it only stops refusing the bad one. That is the
"violable in silence" shape, so the order is pinned here, and each pure
check is fed the shape that actually shipped.

Blind spot: the guard reads the step list and runs the pure checks. It does
not run ``uv publish`` or ``gh release`` -- the dry run of a real tag
(``make publish VERSION=X.Y.Z DRY_RUN=1``) is what exercises the rest.
"""

from __future__ import annotations

import importlib.util
import pathlib
import sys
from types import ModuleType

import pytest

ROOT: pathlib.Path = pathlib.Path(__file__).resolve().parent.parent

CHECKS_BEFORE_BUILD: tuple[str, ...] = (
    "preflight",
    "head-is-tag",
    "versions-match",
    "changelog-notes",
    "lock-check",
    "audit",
)
"""Steps that must all run before ``build``."""

INSPECTIONS_BEFORE_UPLOAD: tuple[str, ...] = (
    "build",
    "twine-check",
    "wheel-contents",
    "smoke",
)
"""Steps that must all run after ``build`` and before ``upload``."""


def load_publish() -> ModuleType:
    """Import ``scripts/publish.py``, which is not a package.

    Returns:
        ModuleType: The loaded module, registered in ``sys.modules``.
    """
    name: str = "tempest_publish_script"
    if name in sys.modules:
        return sys.modules[name]
    spec = importlib.util.spec_from_file_location(name, ROOT / "scripts" / "publish.py")
    assert spec is not None and spec.loader is not None
    module: ModuleType = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


def order_problems(names: list[str]) -> list[str]:
    """List the ordering rules a step sequence breaks.

    Args:
        names (list[str]): Step names, in run order.

    Returns:
        list[str]: One message per broken rule, empty when the order is safe.
    """
    required: tuple[str, ...] = (
        *CHECKS_BEFORE_BUILD,
        *INSPECTIONS_BEFORE_UPLOAD,
        "upload",
        "github-release",
    )
    missing: list[str] = [name for name in required if name not in names]
    if missing:
        return [f"missing step {name!r}" for name in missing]
    position: dict[str, int] = {name: names.index(name) for name in required}
    problems: list[str] = [
        f"{name!r} runs after 'build'"
        for name in CHECKS_BEFORE_BUILD
        if position[name] > position["build"]
    ]
    problems.extend(
        f"{name!r} runs after 'upload'"
        for name in INSPECTIONS_BEFORE_UPLOAD
        if position[name] > position["upload"]
    )
    if names[-2:] != ["upload", "github-release"]:
        problems.append("'upload' then 'github-release' must be the last two steps")
    return problems


@pytest.fixture(scope="module")
def publish() -> ModuleType:
    """Return the loaded publish script.

    Returns:
        ModuleType: ``scripts/publish.py``.
    """
    return load_publish()


class TestStepOrder:
    """Checks, then build and inspection, then upload, then release."""

    def test_the_script_order_is_safe(self, publish: ModuleType) -> None:
        """The committed ``STEPS`` passes every ordering rule."""
        names: list[str] = [name for name, _ in publish.STEPS]
        assert order_problems(names) == []

    def test_fires_when_the_audit_moves_after_the_build(self) -> None:
        """The pre-#436 release built before anything audited it."""
        names: list[str] = [
            "preflight",
            "head-is-tag",
            "versions-match",
            "changelog-notes",
            "lock-check",
            "build",
            "audit",
            "twine-check",
            "wheel-contents",
            "smoke",
            "upload",
            "github-release",
        ]
        assert order_problems(names) == ["'audit' runs after 'build'"]

    def test_fires_when_the_smoke_moves_after_the_upload(self) -> None:
        """A smoke after the upload only reports that PyPI already has the bad wheel."""
        names: list[str] = [
            *CHECKS_BEFORE_BUILD,
            "build",
            "twine-check",
            "wheel-contents",
            "upload",
            "smoke",
            "github-release",
        ]
        assert order_problems(names) == [
            "'smoke' runs after 'upload'",
            "'upload' then 'github-release' must be the last two steps",
        ]

    def test_fires_when_a_check_is_dropped(self) -> None:
        """Deleting the lock check is a missing step, not a passing order."""
        names: list[str] = [
            "preflight",
            "head-is-tag",
            "versions-match",
            "changelog-notes",
            "audit",
            *INSPECTIONS_BEFORE_UPLOAD,
            "upload",
            "github-release",
        ]
        assert order_problems(names) == ["missing step 'lock-check'"]


class TestTheChecks:
    """Each pure check refuses the shape that shipped."""

    def test_versions_agree(self, publish: ModuleType) -> None:
        """Four matching versions pass."""
        versions: dict[str, str] = {
            "pyproject.toml": "0.346.0",
            "__init__.py": "0.346.0",
            "uv.lock": "0.346.0",
        }
        assert publish.version_mismatches("0.346.0", versions) == []

    def test_fires_on_the_v0247_lock_drift(self, publish: ModuleType) -> None:
        """v0.247.0 tagged with ``uv.lock`` a version behind."""
        versions: dict[str, str] = {
            "pyproject.toml": "0.247.0",
            "__init__.py": "0.247.0",
            "uv.lock": "0.246.0",
        }
        assert publish.version_mismatches("0.247.0", versions) == [
            "uv.lock records '0.246.0', the tag is '0.247.0'"
        ]

    def test_reads_the_committed_versions(self, publish: ModuleType) -> None:
        """The reader finds all three in this checkout, and they agree."""
        versions: dict[str, str] = publish.read_versions(ROOT)
        assert set(versions) == {"pyproject.toml", "__init__.py", "uv.lock"}
        assert all(versions.values())
        assert len(set(versions.values())) == 1

    def test_changelog_section_is_extracted(self, publish: ModuleType) -> None:
        """Only the requested section, without its heading or the next one."""
        changelog: str = (
            "# Changelog\n\n## [0.2.0] — 2026-11-09\n\n### Added\n\n- b\n\n"
            "## [0.1.0] — 2026-11-01\n\n- a\n"
        )
        assert publish.changelog_notes(changelog, "0.2.0") == "### Added\n\n- b"
        assert publish.changelog_notes(changelog, "0.1.0") == "- a"

    def test_fires_on_a_missing_changelog_section(self, publish: ModuleType) -> None:
        """No section means no notes, not an empty release."""
        assert publish.changelog_notes("## [0.1.0] — x\n\n- a\n", "0.2.0") is None
        assert (
            publish.changelog_notes("## [0.2.0] — x\n\n## [0.1.0]\n", "0.2.0") is None
        )

    def test_the_real_changelog_has_the_current_version(
        self, publish: ModuleType
    ) -> None:
        """The version in ``pyproject.toml`` has notes to publish."""
        version: str = publish.read_versions(ROOT)["pyproject.toml"]
        changelog: str = (ROOT / "CHANGELOG.md").read_text(encoding="utf-8")
        assert publish.changelog_notes(changelog, version)

    def test_complete_wheel_passes(self, publish: ModuleType) -> None:
        """Enough source and exactly one Alembic template."""
        members: list[str] = [f"tempest_fastapi_sdk/m{i}.py" for i in range(25)]
        members.append("tempest_fastapi_sdk/db/alembic/env.py.template")
        assert publish.wheel_problems(members) == []

    def test_fires_on_an_empty_wheel(self, publish: ModuleType) -> None:
        """The empty-wheel class of bug: metadata only, no package."""
        members: list[str] = ["tempest_fastapi_sdk-0.1.0.dist-info/METADATA"]
        assert publish.wheel_problems(members) == [
            "wheel suspiciously empty (0 .py files)",
            "expected one env.py.template, found 0",
        ]


class TestTheToken:
    """The upload needs ``UV_PUBLISH_TOKEN`` and never shows it."""

    def test_refuses_without_the_token(self, publish: ModuleType) -> None:
        """A real publish with no token stops with a message naming the variable."""
        message: str | None = publish.missing_token({}, dry_run=False)
        assert message is not None
        assert "UV_PUBLISH_TOKEN" in message

    def test_blank_token_is_no_token(self, publish: ModuleType) -> None:
        """``export UV_PUBLISH_TOKEN=`` is refused like an unset one."""
        assert (
            publish.missing_token({"UV_PUBLISH_TOKEN": "  "}, dry_run=False) is not None
        )

    def test_accepts_a_token_without_echoing_it(self, publish: ModuleType) -> None:
        """With a token there is nothing to say, so nothing can leak it."""
        assert (
            publish.missing_token({"UV_PUBLISH_TOKEN": "pypi-secret"}, dry_run=False)
            is None
        )

    def test_dry_run_needs_no_token(self, publish: ModuleType) -> None:
        """``DRY_RUN=1`` uploads nothing."""
        assert publish.missing_token({}, dry_run=True) is None

    def test_main_stops_at_preflight(
        self,
        publish: ModuleType,
        tmp_path: pathlib.Path,
        monkeypatch: pytest.MonkeyPatch,
        capsys: pytest.CaptureFixture[str],
    ) -> None:
        """No token: exit 1 at the first step, before git, the audit or a build."""
        monkeypatch.delenv("UV_PUBLISH_TOKEN", raising=False)
        code: int = publish.main(["--version", "0.346.0", "--root", str(tmp_path)])
        captured = capsys.readouterr()
        assert code == 1
        assert "REFUSED at preflight" in captured.err
        assert "$ " not in captured.out
        assert not (tmp_path / "dist").exists()

    def test_the_token_never_reaches_a_command_line(self, publish: ModuleType) -> None:
        """``uv publish`` reads the variable itself; no step passes ``--token``."""
        source: str = (ROOT / "scripts" / "publish.py").read_text(encoding="utf-8")
        assert "--token" not in source
        assert "env[TOKEN_ENV]" not in source


def load_deploy_docs() -> ModuleType:
    """Import ``scripts/deploy_docs.py``, which is not a package.

    Returns:
        ModuleType: The loaded module, registered in ``sys.modules``.
    """
    name: str = "tempest_deploy_docs_script"
    if name in sys.modules:
        return sys.modules[name]
    spec = importlib.util.spec_from_file_location(
        name, ROOT / "scripts" / "deploy_docs.py"
    )
    assert spec is not None and spec.loader is not None
    module: ModuleType = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


class TestDocsDeploy:
    """The site that reaches ``gh-pages`` is the strict, bilingual build."""

    def test_builds_like_docs_build(self) -> None:
        """Same ``mkdocs build --strict`` as ``make docs-build``, on the lock."""
        deploy = load_deploy_docs()
        command: tuple[str, ...] = deploy.BUILD_COMMAND
        assert command[-3:] == ("mkdocs", "build", "--strict")
        assert "--locked" in command
        makefile: str = (ROOT / "Makefile").read_text(encoding="utf-8")
        assert "uv run --group docs mkdocs build --strict" in makefile

    def test_both_locales_pass(self, tmp_path: pathlib.Path) -> None:
        """PT-BR at the root and EN under ``/en/``."""
        deploy = load_deploy_docs()
        (tmp_path / "en").mkdir()
        (tmp_path / "index.html").write_text("<html></html>", encoding="utf-8")
        (tmp_path / "en" / "index.html").write_text("<html></html>", encoding="utf-8")
        assert deploy.missing_locales(tmp_path) == []

    def test_fires_when_the_english_locale_is_missing(
        self, tmp_path: pathlib.Path
    ) -> None:
        """A build where ``mkdocs-static-i18n`` produced only the default locale."""
        deploy = load_deploy_docs()
        (tmp_path / "index.html").write_text("<html></html>", encoding="utf-8")
        assert deploy.missing_locales(tmp_path) == ["en/index.html"]
