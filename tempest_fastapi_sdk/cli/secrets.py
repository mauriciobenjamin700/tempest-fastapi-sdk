"""``tempest secrets`` — generate and rotate application secrets.

Rotating a leaked (or simply stale) signing secret should be one
command, not a hunt through ``.env`` with a hand-rolled
``openssl rand``. ``tempest secrets rotate`` generates fresh,
URL-safe random values for the secret keys a Tempest service uses
(``JWT_SECRET`` / ``TOKEN_SECRET`` by default), rewrites the matching
lines in ``.env`` in place — backing the old file up first — and
leaves every other line untouched.

!!! warning
    Rotating ``JWT_SECRET`` invalidates every token signed with the
    old one: users are logged out and pending password-reset /
    activation links stop working. Rotate during a maintenance window
    (or run two secrets in parallel if you need zero-downtime
    rotation).
"""

from __future__ import annotations

import secrets
from pathlib import Path

import typer

# Secret env vars a Tempest service signs/authenticates with. MinIO and
# database credentials are intentionally excluded — those are external
# credentials, not values the service is free to regenerate.
_DEFAULT_KEYS: tuple[str, ...] = ("JWT_SECRET", "TOKEN_SECRET")

secrets_app: typer.Typer = typer.Typer(
    name="secrets",
    help="Generate and rotate application secrets.",
    no_args_is_help=True,
)


def _generate(length: int) -> str:
    """Return a URL-safe random secret with at least ``length`` bytes.

    Args:
        length (int): Number of random bytes of entropy.

    Returns:
        str: A URL-safe token (longer than ``length`` chars due to the
        base64 encoding).
    """
    return secrets.token_urlsafe(length)


def _rewrite_env(path: Path, new_values: dict[str, str]) -> tuple[list[str], list[str]]:
    """Apply ``new_values`` to a ``.env`` file's content.

    Existing ``KEY=...`` lines are replaced in place (preserving order
    and surrounding lines); keys not present are appended at the end.

    Args:
        path (Path): The ``.env`` file (may not exist yet).
        new_values (dict[str, str]): ``KEY -> new secret`` mapping.

    Returns:
        tuple[list[str], list[str]]: ``(updated_keys, appended_keys)``.
    """
    lines = path.read_text(encoding="utf-8").splitlines() if path.is_file() else []
    remaining = dict(new_values)
    updated: list[str] = []
    out: list[str] = []
    for line in lines:
        stripped = line.lstrip()
        replaced = False
        for key in list(remaining):
            if stripped.startswith(f"{key}="):
                out.append(f"{key}={remaining.pop(key)}")
                updated.append(key)
                replaced = True
                break
        if not replaced:
            out.append(line)
    appended = list(remaining)
    for key in appended:
        out.append(f"{key}={remaining[key]}")
    path.write_text("\n".join(out) + "\n", encoding="utf-8")
    return updated, appended


@secrets_app.command("rotate")
def secrets_rotate(
    keys: str = typer.Option(
        ",".join(_DEFAULT_KEYS),
        "--keys",
        "-k",
        help=(
            "Comma-separated env var names to rotate. Defaults to "
            "'JWT_SECRET,TOKEN_SECRET'."
        ),
    ),
    env_file: str = typer.Option(
        ".env",
        "--env",
        "-e",
        help="Path to the .env file to rewrite.",
    ),
    length: int = typer.Option(
        48,
        "--length",
        "-l",
        min=16,
        help="Bytes of entropy per secret (URL-safe encoded longer).",
    ),
    show: bool = typer.Option(
        False,
        "--print",
        help="Print the generated secrets to stdout instead of writing .env.",
    ),
    no_backup: bool = typer.Option(
        False,
        "--no-backup",
        help="Skip writing the .env.bak backup before rewriting.",
    ),
) -> None:
    """Generate fresh secrets and write them to ``.env`` (or print them).

    With ``--print`` nothing is written — the new values go to stdout so
    you can pipe them into a secret manager. Otherwise the ``.env`` file
    is rewritten in place (existing keys replaced, missing keys
    appended) after a ``.env.bak`` backup.
    """
    names = [k.strip() for k in keys.split(",") if k.strip()]
    if not names:
        typer.echo("error: --keys produced no names.", err=True)
        raise typer.Exit(2)

    new_values = {name: _generate(length) for name in names}

    if show:
        for name, value in new_values.items():
            typer.echo(f"{name}={value}")
        return

    path = Path(env_file).expanduser()
    if path.is_file() and not no_backup:
        backup = path.with_suffix(path.suffix + ".bak")
        backup.write_text(path.read_text(encoding="utf-8"), encoding="utf-8")
        typer.echo(f"Backed up {path} -> {backup}")

    updated, appended = _rewrite_env(path, new_values)
    for key in updated:
        typer.echo(f"rotated {key}")
    for key in appended:
        typer.echo(f"added {key}")
    typer.echo(
        "Done. Rotating JWT_SECRET invalidates existing tokens — "
        "restart the service to load the new values."
    )


__all__: list[str] = [
    "secrets_app",
]
