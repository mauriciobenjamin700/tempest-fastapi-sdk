"""The ``tempest`` script and the package without the ``[cli]`` extra.

``tempest-cli`` left the base dependencies in v0.308.0 because it requires
``ruff`` at runtime: every service importing the SDK carried a formatter to
production, and a project pinning ``ruff<0.8`` could not lock at all. The
CLI is optional now, and these tests pin the halves of that promise — the
package imports without it, and the script explains what to install instead
of printing a traceback.

Since v0.324.0 there are two installs: ``[cli-ops]`` (``typer`` + ``click``),
for a server, and ``[cli]`` (``[cli-ops]`` + ``tempest-cli``), for
development. Under ``[cli-ops]`` the operation commands and the project's own
commands run, and the quality-gate commands answer with the ``[cli]`` hint.

The suite runs with every extra installed, so the absence is reproduced in a
subprocess whose import system refuses the extra's modules. In-process
hiding would not do: the modules are already in ``sys.modules`` of the
interpreter that collected the CLI tests.
"""

from __future__ import annotations

import subprocess
import sys
import textwrap
from pathlib import Path

import pytest

from tempest_fastapi_sdk.cli.entrypoint import (
    CLI_EXTRA_EXIT_CODE,
    CLI_EXTRA_HINT,
    CLI_EXTRA_MODULES,
    CLI_GATE_HINT,
    CLI_GATE_MODULES,
    CLI_OPS_EXTRA_MODULES,
    missing_cli_modules,
    raise_for_missing_cli_extra,
)
from tempest_fastapi_sdk.cli.main import QUALITY_GATE_COMMANDS

OPS_ONLY_BLOCKED: frozenset[str] = frozenset({*CLI_GATE_MODULES, "ruff"})
"""What ``[cli-ops]`` lacks next to ``[cli]``: the gate and its formatter."""

_BLOCKER = textwrap.dedent(
    """
    import sys

    BLOCKED = {blocked!r}

    class _Blocker:
        def find_spec(self, name, path=None, target=None):
            if name.partition(".")[0] in BLOCKED:
                raise ModuleNotFoundError(f"No module named {{name!r}}", name=name)
            return None

    sys.meta_path.insert(0, _Blocker())
    """
)
"""Prelude that makes the ``[cli]`` modules unimportable in the child."""


def _run_without_cli_extra(
    body: str,
    *,
    blocked: frozenset[str] = frozenset(CLI_EXTRA_MODULES),
    cwd: Path | None = None,
) -> subprocess.CompletedProcess[str]:
    """Run ``body`` in a fresh interpreter that cannot import ``blocked``.

    Args:
        body (str): Python source executed after the blocker is installed.
        blocked (frozenset[str]): Top-level modules the child cannot
            import. Defaults to every module of ``[cli]``.
        cwd (Path | None): Working directory of the child.

    Returns:
        subprocess.CompletedProcess[str]: The finished child, with captured
        ``stdout`` and ``stderr``.
    """
    source = _BLOCKER.format(blocked=set(blocked)) + textwrap.dedent(body)
    return subprocess.run(
        [sys.executable, "-c", source],
        capture_output=True,
        text=True,
        timeout=120,
        check=False,
        cwd=cwd,
    )


def _run_tempest_with_ops_only(
    *args: str, cwd: Path | None = None
) -> subprocess.CompletedProcess[str]:
    """Run the ``tempest`` script as installed with ``[cli-ops]`` only.

    Args:
        *args (str): Arguments after ``tempest``.
        cwd (Path | None): Working directory of the child.

    Returns:
        subprocess.CompletedProcess[str]: The finished child.
    """
    return _run_without_cli_extra(
        f"""
        sys.argv = ["tempest", *{list(args)!r}]
        from tempest_fastapi_sdk.cli.entrypoint import main
        main()
        """,
        blocked=OPS_ONLY_BLOCKED,
        cwd=cwd,
    )


class TestWithoutTheExtra:
    """The package and the script, in an environment without ``[cli]``."""

    def test_package_imports_without_the_extra(self) -> None:
        """The base package, ``cli`` and ``openapi`` import cleanly."""
        result = _run_without_cli_extra(
            """
            import tempest_fastapi_sdk
            import tempest_fastapi_sdk.cli
            import tempest_fastapi_sdk.openapi
            loaded = sorted(m for m in sys.modules if m.partition(".")[0] in BLOCKED)
            print("loaded:", loaded)
            """
        )
        assert result.returncode == 0, result.stderr
        assert "loaded: []" in result.stdout

    @pytest.mark.parametrize(
        ("statement", "hint"),
        [
            ("tempest_fastapi_sdk.cli.app", CLI_EXTRA_HINT),
            ("tempest_fastapi_sdk.cli.TempestConfig", CLI_GATE_HINT),
            ("importlib.import_module('tempest_fastapi_sdk.cli.main')", CLI_EXTRA_HINT),
            (
                "importlib.import_module('tempest_fastapi_sdk.cli.config')"
                ".load_tempest_config",
                CLI_GATE_HINT,
            ),
            ("importlib.import_module('tempest_fastapi_sdk.cli.lint')", CLI_GATE_HINT),
            (
                "importlib.import_module('tempest_fastapi_sdk.cli.pr_prompt')",
                CLI_GATE_HINT,
            ),
        ],
    )
    def test_reaching_the_cli_raises_the_install_hint(
        self, statement: str, hint: str
    ) -> None:
        """Every path into the CLI fails with the matching install line.

        What needs ``typer`` names both extras; what needs ``tempest_cli``
        names ``[cli]``.

        Args:
            statement (str): Expression that reaches the CLI.
            hint (str): The install line the ``ImportError`` must carry.
        """
        result = _run_without_cli_extra(
            f"""
            import importlib
            import tempest_fastapi_sdk.cli
            try:
                {statement}
            except ImportError as exc:
                print("ImportError:", exc)
            else:
                print("no error")
            """
        )
        assert result.returncode == 0, result.stderr
        assert f"ImportError: {hint}" in result.stdout

    def test_project_config_reads_without_the_extra(self, tmp_path: Path) -> None:
        """``[tool.tempest] commands`` is read with the standard library only.

        Args:
            tmp_path (Path): Project root holding the ``pyproject.toml``.
        """
        (tmp_path / "pyproject.toml").write_text(
            '[tool.tempest]\ncommands = ["ops.commands"]\n', encoding="utf-8"
        )
        result = _run_without_cli_extra(
            f"""
            from pathlib import Path
            from tempest_fastapi_sdk.cli.config import load_project_commands
            print("commands:", load_project_commands(Path({str(tmp_path)!r})))
            """
        )
        assert result.returncode == 0, result.stderr
        assert "commands: ('ops.commands',)" in result.stdout

    def test_script_prints_the_install_line_and_exits_non_zero(self) -> None:
        """``tempest`` without the extra exits 2 with the hint, no traceback."""
        result = _run_without_cli_extra(
            """
            from tempest_fastapi_sdk.cli.entrypoint import main
            main()
            """
        )
        assert result.returncode == CLI_EXTRA_EXIT_CODE == 2
        assert "Traceback" not in result.stderr
        assert "error: missing typer, click." in result.stderr
        assert 'uv add "tempest-fastapi-sdk[cli-ops]"' in result.stderr
        assert 'uv add --dev "tempest-fastapi-sdk[cli]"' in result.stderr
        assert 'uv tool install "tempest-fastapi-sdk[cli]"' in result.stderr

    def test_integration_generation_degrades_to_unformatted(self) -> None:
        """``_format_paths`` reports no formatter instead of raising.

        The ruff lookup ships with ``[cli]``; ``generate_integration`` is a
        public API of ``[openapi]`` whose contract is that a missing ruff
        degrades polish, never correctness.
        """
        result = _run_without_cli_extra(
            """
            from pathlib import Path
            from tempest_fastapi_sdk.openapi.generate import _format_paths
            print("formatted:", _format_paths([Path("schemas.py")]))
            """
        )
        assert result.returncode == 0, result.stderr
        assert "formatted: False" in result.stdout


_PROJECT_COMMANDS: str = """
import typer

commands: typer.Typer = typer.Typer()


@commands.command("staff-create")
def staff_create(email: str) -> None:
    typer.echo(f"staff created: {email}")
"""
"""A project command module, mounted through ``[tool.tempest] commands``."""


class TestWithTheOpsExtraOnly:
    """``tempest`` with ``[cli-ops]``: no ``tempest_cli``, no ``ruff``."""

    @pytest.fixture
    def project(self, tmp_path: Path) -> Path:
        """Write a project whose ``[tool.tempest] commands`` names a module.

        Args:
            tmp_path (Path): Directory to write into.

        Returns:
            Path: The project root.
        """
        (tmp_path / "pyproject.toml").write_text(
            '[project]\nname = "opsdemo"\nversion = "0.1.0"\n\n'
            '[tool.tempest]\ncommands = ["ops.commands"]\n',
            encoding="utf-8",
        )
        (tmp_path / "ops").mkdir()
        (tmp_path / "ops" / "__init__.py").write_text("", encoding="utf-8")
        (tmp_path / "ops" / "commands.py").write_text(
            _PROJECT_COMMANDS, encoding="utf-8"
        )
        return tmp_path

    def test_only_the_gate_is_missing(self) -> None:
        """The blocked set is the gate and ruff, never ``typer``/``click``."""
        assert not OPS_ONLY_BLOCKED & set(CLI_OPS_EXTRA_MODULES)

    def test_help_lists_the_operation_commands(self, project: Path) -> None:
        """``tempest --help`` lists every operation command and the project's.

        Args:
            project (Path): Project root used as the working directory.
        """
        result = _run_tempest_with_ops_only("--help", cwd=project)
        assert result.returncode == 0, result.stderr
        for name in ("doctor", "db", "shell", "user", "routes", "staff-create"):
            assert f" {name} " in result.stdout, name

    def test_gate_commands_stay_listed_with_the_hint(self, project: Path) -> None:
        """Each gate name is listed, its help naming the ``[cli]`` extra.

        Args:
            project (Path): Project root used as the working directory.
        """
        result = _run_tempest_with_ops_only("--help", cwd=project)
        assert result.returncode == 0, result.stderr
        assert result.stdout.count("needs the [cli] extra") == len(
            QUALITY_GATE_COMMANDS
        )

    @pytest.mark.parametrize(
        "args",
        [
            ("db", "--help"),
            ("shell", "--help"),
            ("user", "--help"),
            ("routes", "--help"),
        ],
    )
    def test_operation_command_starts(
        self, project: Path, args: tuple[str, ...]
    ) -> None:
        """Each operation command parses and prints its help.

        Args:
            project (Path): Project root used as the working directory.
            args (tuple[str, ...]): The command line after ``tempest``.
        """
        result = _run_tempest_with_ops_only(*args, cwd=project)
        assert result.returncode == 0, result.stderr
        assert f"Usage: tempest {args[0]}" in result.stdout

    def test_doctor_runs(self, project: Path) -> None:
        """``tempest doctor`` reports instead of failing on the missing gate.

        Args:
            project (Path): Project root used as the working directory.
        """
        result = _run_tempest_with_ops_only("doctor", cwd=project)
        assert result.returncode == 0, result.stderr
        assert "OK    sdk" in result.stdout

    def test_project_command_runs(self, project: Path) -> None:
        """A command from ``[tool.tempest] commands`` runs.

        Args:
            project (Path): Project root used as the working directory.
        """
        result = _run_tempest_with_ops_only(
            "staff-create", "ana@example.com", cwd=project
        )
        assert result.returncode == 0, result.stderr
        assert result.stdout == "staff created: ana@example.com\n"

    @pytest.mark.parametrize("name", QUALITY_GATE_COMMANDS)
    def test_gate_command_explains_the_cli_extra(
        self, project: Path, name: str
    ) -> None:
        """Every gate command exits 2 with the ``[cli]`` hint, no traceback.

        Extra arguments and unknown options are swallowed, so the user
        sees the hint rather than a usage error.

        Args:
            project (Path): Project root used as the working directory.
            name (str): The quality-gate command.
        """
        result = _run_tempest_with_ops_only(name, "src", "--fast", cwd=project)
        assert result.returncode == CLI_EXTRA_EXIT_CODE == 2
        assert "Traceback" not in result.stderr
        assert f"error: tempest {name} is not available." in result.stderr
        assert 'uv add --dev "tempest-fastapi-sdk[cli]"' in result.stderr

    def test_lint_layers_step_is_none(self) -> None:
        """Without the gate there is no ``lint-layers`` step to hand it."""
        result = _run_without_cli_extra(
            """
            from tempest_fastapi_sdk.cli.main import LINT_LAYERS_STEP
            print("step:", LINT_LAYERS_STEP)
            """,
            blocked=OPS_ONLY_BLOCKED,
        )
        assert result.returncode == 0, result.stderr
        assert "step: None" in result.stdout


class TestWithTheExtra:
    """The same entry points when ``[cli]`` is installed, as in this suite."""

    def test_no_module_is_missing(self) -> None:
        """The suite installs every extra, so nothing is reported missing."""
        assert missing_cli_modules() == []

    def test_gate_names_match_tempest_cli(self) -> None:
        """:data:`QUALITY_GATE_COMMANDS` is what ``register_commands`` adds.

        The tuple is ported so the placeholders exist without
        ``tempest-cli``; a command added or renamed upstream fails here.
        """
        import typer
        from tempest_cli.main import register_commands

        probe: typer.Typer = typer.Typer()
        register_commands(probe)
        names = tuple(command.name for command in probe.registered_commands)
        assert names == QUALITY_GATE_COMMANDS

    def test_gate_is_registered(self) -> None:
        """With ``[cli]`` the real gate runs, not the placeholder."""
        from tempest_fastapi_sdk.cli.main import LINT_LAYERS_STEP, app

        assert LINT_LAYERS_STEP is not None
        lint = next(c for c in app.registered_commands if c.name == "lint")
        assert lint.callback is not None
        assert lint.callback.__module__ == "tempest_cli.main"

    def test_script_runs_the_cli(self) -> None:
        """``tempest --version`` goes through the entry point to the CLI."""
        result = subprocess.run(
            [
                sys.executable,
                "-c",
                "import sys; sys.argv = ['tempest', '--version'];"
                "from tempest_fastapi_sdk.cli.entrypoint import main; main()",
            ],
            capture_output=True,
            text=True,
            timeout=120,
            check=False,
        )
        assert result.returncode == 0, result.stderr
        assert result.stdout.startswith("tempest-fastapi-sdk ")


class TestRaiseForMissingCliExtra:
    """Only a missing ``[cli]`` module becomes the install hint."""

    @pytest.mark.parametrize(
        ("name", "hint"),
        [
            ("tempest_cli", CLI_GATE_HINT),
            ("tempest_cli.main", CLI_GATE_HINT),
            ("typer.core", CLI_EXTRA_HINT),
            ("click", CLI_EXTRA_HINT),
        ],
    )
    def test_cli_module_becomes_the_hint(self, name: str, hint: str) -> None:
        """A missing extra module, or a submodule of one, raises its hint.

        Args:
            name (str): The module the failed import names.
            hint (str): The install line expected for it.
        """
        error = ModuleNotFoundError(f"No module named {name!r}", name=name)
        with pytest.raises(ImportError) as caught:
            raise_for_missing_cli_extra(error)
        assert str(caught.value) == hint
        assert caught.value.__cause__ is error

    @pytest.mark.parametrize("name", ["yaml", "tempest_clix", None])
    def test_other_module_is_left_alone(self, name: str | None) -> None:
        """Any other missing module returns, so the caller re-raises it.

        Args:
            name (str | None): The module the failed import names.
        """
        raise_for_missing_cli_extra(ModuleNotFoundError("No module", name=name))
