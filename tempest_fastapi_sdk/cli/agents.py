"""``tempest agents`` — inspect and drive an agent without writing a script.

Two questions come up constantly while building an agent, and both used
to cost a scratch file: which tools does it actually have in this
configuration, and what does it do with one goal. ``tools`` answers the
first without running anything (no model call, no token spent);
``run`` answers the second and prints the trace, not just the answer,
because a run that stops on its budget still returns text.
"""

from __future__ import annotations

import asyncio
import importlib
import json
import sys
from pathlib import Path
from typing import Any

import typer

from tempest_fastapi_sdk.cli.project import CODE_ROOTS

agents_app: typer.Typer = typer.Typer(
    name="agents",
    help="Inspect an agent's tools and run it against one goal.",
    no_args_is_help=True,
)

_AGENT_OPTION: Any = typer.Option(
    "",
    "--agent",
    "-a",
    help="Import spec of the Agent ('module:attr'). Defaults to '<root>.agents:agent'.",
)


def _load_agent(spec: str) -> Any:
    """Import the project's agent.

    Args:
        spec (str): Explicit ``module:attr``, empty to probe
            ``<root>.agents:agent``.

    Returns:
        Any: The agent object.

    Raises:
        typer.Exit: Exit code 2 when nothing resolves, naming every
            attempt; the SDK scaffolds no agents layer, so the
            conventional path really is a guess here.
    """
    root = Path.cwd()
    if str(root) not in sys.path:
        sys.path.insert(0, str(root))

    candidates = [spec] if spec else [f"{name}.agents:agent" for name in CODE_ROOTS]
    notes: list[str] = []
    for candidate in candidates:
        module_name, _, attr = candidate.partition(":")
        try:
            module = importlib.import_module(module_name)
            return getattr(module, attr)
        except (ImportError, AttributeError) as exc:
            notes.append(f"  {candidate}: {exc}")
    for note in notes:
        typer.echo(note, err=True)
    typer.echo(
        "error: no agent found. Pass --agent 'module:attr' — the SDK does not "
        "scaffold an agents layer, so there is no convention to fall back on.",
        err=True,
    )
    raise typer.Exit(2)


@agents_app.command("tools")
def agents_tools(
    agent_spec: str = _AGENT_OPTION,
    as_json: bool = typer.Option(
        False,
        "--json",
        help="Print the tool specs as JSON instead of names and descriptions.",
    ),
) -> None:
    """List the tools this agent may call, without running it.

    Nothing is sent to a model, so this costs no token and works with a
    backend that has no credentials configured.
    """
    agent = _load_agent(agent_spec)
    tools = list(getattr(agent, "tools", ()))
    if not tools:
        typer.echo("(no tool)")
        return

    if as_json:
        typer.echo(
            json.dumps(
                [
                    {
                        "name": getattr(tool, "name", "?"),
                        "description": getattr(tool, "description", ""),
                    }
                    for tool in tools
                ],
                indent=2,
            )
        )
        return

    width = max(len(str(getattr(tool, "name", "?"))) for tool in tools)
    for tool in tools:
        name = str(getattr(tool, "name", "?"))
        description = str(getattr(tool, "description", "") or "").splitlines()
        typer.echo(f"{name.ljust(width)}  {description[0] if description else ''}")


@agents_app.command("run")
def agents_run(
    goal: str = typer.Argument(..., help="What the agent should accomplish."),
    agent_spec: str = _AGENT_OPTION,
    trace: bool = typer.Option(
        False,
        "--trace",
        help="Also print each step and the tool it called.",
    ),
) -> None:
    """Run the agent once against ``goal`` and print what came back.

    The exit code follows ``AgentRun.succeeded``, not "no exception":
    a run stopped by its step or time budget still carries text, and
    treating that text as an answer is the mistake this command refuses
    to make for you.

    Raises:
        typer.Exit: Exit code 1 when the run did not succeed, after
            printing the output and the stop reason.
    """
    agent = _load_agent(agent_spec)

    try:
        run = asyncio.run(agent.run(goal))
    except Exception as exc:
        typer.echo(f"error: the run raised — {type(exc).__name__}: {exc}", err=True)
        raise typer.Exit(1) from exc

    if trace:
        for index, step in enumerate(getattr(run, "steps", ()), start=1):
            tool = getattr(step, "tool", None) or "-"
            typer.echo(f"[{index}] {tool}", err=True)

    typer.echo(getattr(run, "output", ""))
    stop_reason = getattr(run, "stop_reason", None)
    reason = getattr(stop_reason, "value", stop_reason)
    if not getattr(run, "succeeded", False):
        typer.echo(f"error: the run did not succeed (stopped: {reason}).", err=True)
        raise typer.Exit(1)
    typer.echo(f"(stopped: {reason})", err=True)


__all__: list[str] = [
    "agents_app",
]
