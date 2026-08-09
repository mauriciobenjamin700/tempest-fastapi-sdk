"""Tests for ``tempest pr-prompt``.

Every test runs against a real temporary git repository — the command's
whole job is reading git, so a mocked ``subprocess`` would assert the
mock rather than the behaviour.
"""

from __future__ import annotations

import shutil
import subprocess
from pathlib import Path

import pytest
from typer.testing import CliRunner

from tempest_fastapi_sdk.cli.main import app
from tempest_fastapi_sdk.cli.pr_prompt import (
    GitError,
    PromptLanguage,
    bundled_template,
    collect_context,
    current_branch,
    files_by_churn,
    generate_pr_prompt,
    repository_name,
    resolve_base,
    resolve_template,
)

pytestmark = pytest.mark.skipif(
    shutil.which("git") is None,
    reason="pr-prompt reads a real repository and needs git on PATH.",
)

runner = CliRunner()


def _git(repo: Path, *args: str) -> str:
    """Run a git command inside ``repo`` and return its stdout.

    Args:
        repo (Path): The repository directory.
        *args (str): Arguments after the ``git`` executable.

    Returns:
        str: The command's stdout.
    """
    return subprocess.run(
        ["git", *args],
        cwd=repo,
        capture_output=True,
        text=True,
        check=True,
    ).stdout.strip()


def _commit(repo: Path, message: str) -> None:
    """Stage everything in ``repo`` and commit it.

    Args:
        repo (Path): The repository directory.
        message (str): The commit subject.
    """
    _git(repo, "add", "-A")
    _git(repo, "commit", "-m", message)


@pytest.fixture
def repo(tmp_path: Path) -> Path:
    """Build a repository with a ``main`` base and a feature branch.

    The feature branch adds one commit touching two files: a small one
    and a much larger one, so churn ordering is observable.

    Args:
        tmp_path (Path): pytest's temporary directory.

    Returns:
        Path: The repository root.
    """
    root = tmp_path / "service"
    root.mkdir()
    _git(root, "-c", "init.defaultBranch=main", "init")
    _git(root, "config", "user.email", "dev@example.com")
    _git(root, "config", "user.name", "Dev")
    (root / "README.md").write_text("service\n", encoding="utf-8")
    _commit(root, "chore: bootstrap")

    _git(root, "checkout", "-b", "feat/orders")
    (root / "small.py").write_text("VALUE: int = 1\n", encoding="utf-8")
    (root / "big.py").write_text(
        "\n".join(f"LINE_{index}: int = {index}" for index in range(60)) + "\n",
        encoding="utf-8",
    )
    _commit(root, "feat: add the orders endpoint")
    return root


class TestPromptContent:
    def test_carries_rules_template_and_context(self, repo: Path) -> None:
        result = runner.invoke(app, ["pr-prompt", "main", "-p", str(repo)])

        assert result.exit_code == 0, result.output
        assert "REGRAS OBRIGATÓRIAS" in result.stdout
        assert "## Notas sobre deploy" in result.stdout
        assert "feat: add the orders endpoint" in result.stdout
        assert "big.py" in result.stdout
        assert "```diff" in result.stdout

    def test_english_switches_rules_and_bundled_template(self, repo: Path) -> None:
        result = runner.invoke(app, ["pr-prompt", "main", "-p", str(repo), "-l", "en"])

        assert result.exit_code == 0, result.output
        assert "MANDATORY RULES" in result.stdout
        assert "## Deploy notes" in result.stdout
        assert "REGRAS OBRIGATÓRIAS" not in result.stdout

    def test_head_option_describes_another_branch(self, repo: Path) -> None:
        _git(repo, "checkout", "main")

        result = runner.invoke(
            app,
            ["pr-prompt", "main", "-p", str(repo), "--head", "feat/orders"],
        )

        assert result.exit_code == 0, result.output
        assert "feat: add the orders endpoint" in result.stdout


class TestTemplateResolution:
    def test_repository_template_wins_over_bundled(self, repo: Path) -> None:
        template = repo / ".github" / "pull_request_template.md"
        template.parent.mkdir()
        template.write_text("## Contexto do time\n", encoding="utf-8")

        result = runner.invoke(app, ["pr-prompt", "main", "-p", str(repo)])

        assert result.exit_code == 0, result.output
        assert "## Contexto do time" in result.stdout
        assert "## Notas sobre deploy" not in result.stdout

    def test_explicit_template_wins_over_repository(self, repo: Path) -> None:
        template = repo / ".github" / "pull_request_template.md"
        template.parent.mkdir()
        template.write_text("## Contexto do time\n", encoding="utf-8")
        explicit = repo.parent / "other.md"
        explicit.write_text("## Somente este\n", encoding="utf-8")

        result = runner.invoke(
            app,
            ["pr-prompt", "main", "-p", str(repo), "-t", str(explicit)],
        )

        assert result.exit_code == 0, result.output
        assert "## Somente este" in result.stdout
        assert "## Contexto do time" not in result.stdout

    def test_missing_explicit_template_is_an_error(self, repo: Path) -> None:
        result = runner.invoke(
            app,
            ["pr-prompt", "main", "-p", str(repo), "-t", str(repo / "nope.md")],
        )

        assert result.exit_code == 2

    def test_resolve_template_reports_its_source(self, repo: Path) -> None:
        bundled = resolve_template(repo, language=PromptLanguage.EN_US)
        assert bundled.bundled is True
        assert "en" in bundled.source

        (repo / ".pull_request_template.md").write_text("x\n", encoding="utf-8")
        found = resolve_template(repo)
        assert found.bundled is False
        assert found.source == ".pull_request_template.md"

    def test_bundled_templates_exist_in_both_languages(self) -> None:
        assert "## Problema" in bundled_template(PromptLanguage.PT_BR)
        assert "## Problem" in bundled_template(PromptLanguage.EN_US)


class TestBounds:
    def test_biggest_change_gets_the_only_excerpt(self, repo: Path) -> None:
        assert files_by_churn("main", "feat/orders", repo)[0] == "big.py"

        result = runner.invoke(
            app,
            ["pr-prompt", "main", "-p", str(repo), "--max-files", "1"],
        )

        assert result.exit_code == 0, result.output
        assert "#### big.py" in result.stdout
        assert "#### small.py" not in result.stdout

    def test_omitted_files_are_stated_in_the_prompt(self, repo: Path) -> None:
        result = runner.invoke(
            app,
            ["pr-prompt", "main", "-p", str(repo), "--max-files", "1"],
        )

        assert "Mais 1 arquivo(s) alterado(s)" in result.stdout
        assert "small.py" in result.stdout

    def test_zero_files_keeps_the_list_and_drops_the_patches(self, repo: Path) -> None:
        result = runner.invoke(
            app,
            ["pr-prompt", "main", "-p", str(repo), "--max-files", "0"],
        )

        assert result.exit_code == 0, result.output
        assert "```diff" not in result.stdout
        assert "big.py" in result.stdout

    def test_truncation_is_flagged_and_cuts_on_a_line_boundary(
        self, repo: Path
    ) -> None:
        result = runner.invoke(
            app,
            ["pr-prompt", "main", "-p", str(repo), "--max-chars", "200"],
        )

        assert "trecho cortado" in result.stdout
        excerpt = result.stdout.split("#### big.py\n```diff\n")[1].split("\n```")[0]
        full = _git(repo, "diff", "main...feat/orders", "--", "big.py")
        lines = excerpt.splitlines()
        assert lines == full.splitlines()[: len(lines)]
        assert len(excerpt) <= 200

    def test_full_excerpts_every_file_with_the_whole_patch(self, repo: Path) -> None:
        for index in range(12):
            (repo / f"extra_{index}.py").write_text(
                f"VALUE_{index}: int = {index}\n", encoding="utf-8"
            )
        _commit(repo, "feat: a dozen more files")

        result = runner.invoke(app, ["pr-prompt", "main", "-p", str(repo), "--full"])

        assert result.exit_code == 0, result.output
        assert result.stdout.count("```diff") == 14
        assert "trecho cortado" not in result.stdout
        assert "sem trecho de diff aqui" not in result.stdout
        excerpt = result.stdout.split("#### big.py\n```diff\n")[1].split("\n```")[0]
        assert excerpt == _git(repo, "diff", "main...feat/orders", "--", "big.py")

    def test_full_refuses_an_explicit_bound(self, repo: Path) -> None:
        for flag, value in (("--max-files", "3"), ("--max-chars", "500")):
            result = runner.invoke(
                app,
                ["pr-prompt", "main", "-p", str(repo), "--full", flag, value],
            )

            assert result.exit_code == 2, result.output
            assert flag in result.output

    def test_full_alone_is_accepted_with_the_defaults_untouched(
        self, repo: Path
    ) -> None:
        result = runner.invoke(app, ["pr-prompt", "main", "-p", str(repo), "--full"])

        assert result.exit_code == 0, result.output


class TestOutput:
    def test_out_writes_the_file_and_keeps_stdout_clean(self, repo: Path) -> None:
        destination = repo.parent / "pr_prompt.txt"

        result = runner.invoke(
            app,
            ["pr-prompt", "main", "-p", str(repo), "-o", str(destination)],
        )

        assert result.exit_code == 0, result.output
        assert "REGRAS OBRIGATÓRIAS" in destination.read_text(encoding="utf-8")
        assert "REGRAS OBRIGATÓRIAS" not in result.stdout


class TestRefResolution:
    def test_unknown_base_exits_two(self, repo: Path) -> None:
        result = runner.invoke(app, ["pr-prompt", "nope", "-p", str(repo)])

        assert result.exit_code == 2

    def test_empty_comparison_exits_one(self, repo: Path) -> None:
        result = runner.invoke(app, ["pr-prompt", "feat/orders", "-p", str(repo)])

        assert result.exit_code == 1

    def test_base_falls_back_to_the_remote_branch(self, repo: Path) -> None:
        clone = repo.parent / "clone"
        _git(repo.parent, "clone", str(repo), str(clone))
        _git(clone, "config", "user.email", "dev@example.com")
        _git(clone, "config", "user.name", "Dev")
        _git(clone, "checkout", "-b", "feat/local")
        (clone / "extra.py").write_text("X: int = 1\n", encoding="utf-8")
        _commit(clone, "feat: extra")

        assert "main" not in _git(clone, "branch", "--format=%(refname:short)")
        assert resolve_base("main", clone) == "origin/main"

        result = runner.invoke(app, ["pr-prompt", "main", "-p", str(clone)])
        assert result.exit_code == 0, result.output
        assert "feat: extra" in result.stdout

    def test_detached_head_reports_the_short_sha(self, repo: Path) -> None:
        sha = _git(repo, "rev-parse", "--short", "HEAD")
        _git(repo, "checkout", "--detach", "HEAD")

        assert current_branch(repo) == sha

    def test_outside_a_repository_raises(self, tmp_path: Path) -> None:
        with pytest.raises(GitError):
            collect_context(base="main", cwd=tmp_path)


class TestRepositoryName:
    def test_falls_back_to_the_directory_without_a_remote(self, repo: Path) -> None:
        assert repository_name(repo) == "service"

    def test_reads_the_origin_remote(self, repo: Path) -> None:
        _git(repo, "remote", "add", "origin", "git@github.com:acme/billing.git")

        assert repository_name(repo) == "billing"
        prompt, _, _ = generate_pr_prompt(base="main", cwd=repo)
        assert "Repositório: billing" in prompt


class TestBinaryFiles:
    def test_binary_change_never_becomes_an_excerpt(self, repo: Path) -> None:
        (repo / "logo.bin").write_bytes(bytes(range(256)) * 8)
        _commit(repo, "chore: add a binary asset")

        assert "logo.bin" not in files_by_churn("main", "feat/orders", repo)

        result = runner.invoke(app, ["pr-prompt", "main", "-p", str(repo)])
        assert result.exit_code == 0, result.output
        assert "#### logo.bin" not in result.stdout
