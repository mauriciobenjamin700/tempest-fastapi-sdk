"""The ``tempest`` script and the package without the ``[cli]`` extra.

``tempest-cli`` left the base dependencies in v0.308.0 because it requires
``ruff`` at runtime: every service importing the SDK carried a formatter to
production, and a project pinning ``ruff<0.8`` could not lock at all. The
CLI is the ``[cli]`` extra now, and these tests pin the two halves of that
promise — the package imports without it, and the script explains what to
install instead of printing a traceback.

The suite runs with every extra installed, so the absence is reproduced in a
subprocess whose import system refuses the extra's modules. In-process
hiding would not do: the modules are already in ``sys.modules`` of the
interpreter that collected the CLI tests.
"""

from __future__ import annotations

import subprocess
import sys
import textwrap

import pytest

from tempest_fastapi_sdk.cli.entrypoint import (
    CLI_EXTRA_EXIT_CODE,
    CLI_EXTRA_HINT,
    CLI_EXTRA_MODULES,
    missing_cli_modules,
    raise_for_missing_cli_extra,
)

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


def _run_without_cli_extra(body: str) -> subprocess.CompletedProcess[str]:
    """Run ``body`` in a fresh interpreter that cannot import the extra.

    Args:
        body (str): Python source executed after the blocker is installed.

    Returns:
        subprocess.CompletedProcess[str]: The finished child, with captured
        ``stdout`` and ``stderr``.
    """
    source = _BLOCKER.format(blocked=set(CLI_EXTRA_MODULES)) + textwrap.dedent(body)
    return subprocess.run(
        [sys.executable, "-c", source],
        capture_output=True,
        text=True,
        timeout=120,
        check=False,
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
        "statement",
        [
            "tempest_fastapi_sdk.cli.app",
            "tempest_fastapi_sdk.cli.TempestConfig",
            "importlib.import_module('tempest_fastapi_sdk.cli.main')",
            "importlib.import_module('tempest_fastapi_sdk.cli.config')",
            "importlib.import_module('tempest_fastapi_sdk.cli.lint')",
            "importlib.import_module('tempest_fastapi_sdk.cli.pr_prompt')",
        ],
    )
    def test_reaching_the_cli_raises_the_install_hint(self, statement: str) -> None:
        """Every path into the CLI fails with the ``[cli]`` install line.

        Args:
            statement (str): Expression that reaches the CLI.
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
        assert f"ImportError: {CLI_EXTRA_HINT}" in result.stdout

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
        assert "error: missing tempest_cli, typer, click." in result.stderr
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


class TestWithTheExtra:
    """The same entry points when ``[cli]`` is installed, as in this suite."""

    def test_no_module_is_missing(self) -> None:
        """The suite installs every extra, so nothing is reported missing."""
        assert missing_cli_modules() == []

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

    @pytest.mark.parametrize("name", ["tempest_cli", "typer.core", "click"])
    def test_cli_module_becomes_the_hint(self, name: str) -> None:
        """A missing extra module, or a submodule of one, raises the hint.

        Args:
            name (str): The module the failed import names.
        """
        error = ModuleNotFoundError(f"No module named {name!r}", name=name)
        with pytest.raises(ImportError, match=r"\[cli\] extra") as caught:
            raise_for_missing_cli_extra(error)
        assert caught.value.__cause__ is error

    @pytest.mark.parametrize("name", ["yaml", "tempest_clix", None])
    def test_other_module_is_left_alone(self, name: str | None) -> None:
        """Any other missing module returns, so the caller re-raises it.

        Args:
            name (str | None): The module the failed import names.
        """
        raise_for_missing_cli_extra(ModuleNotFoundError("No module", name=name))
