"""``tempest secrets`` — generate, seed and rotate application secrets.

Four commands, ordered by how much they touch:

- ``generate`` prints fresh random values and writes nothing. It is the
  one to pipe into a secret manager.
- ``init`` fills the keys a freshly scaffolded ``.env`` leaves unset:
  missing, empty, or still carrying the template's ``change-me``
  placeholder. A key that already holds a real value is kept, so the
  command is safe to re-run.
- ``rotate`` replaces the keys unconditionally, backing the old file up
  first.
- ``vapid`` generates the Web Push key pair, which is a P-256 key pair
  rather than a random string and therefore cannot come from the other
  three.

!!! warning
    Rotating ``JWT_SECRET`` invalidates every token signed with the
    old one: users are logged out and pending password-reset /
    activation links stop working. Rotate during a maintenance window
    (or run two secrets in parallel if you need zero-downtime
    rotation).
"""

from __future__ import annotations

import base64
import json
import secrets
from contextlib import suppress
from pathlib import Path

import typer

# Secret env vars a Tempest service signs/authenticates with. MinIO and
# database credentials are intentionally excluded — those are external
# credentials, not values the service is free to regenerate.
_DEFAULT_KEYS: tuple[str, ...] = ("JWT_SECRET", "TOKEN_SECRET")

_PLACEHOLDER_MARKERS: tuple[str, ...] = (
    "change-me",
    "changeme",
    "change_me",
    "your-secret",
    "placeholder",
    "todo",
)
"""Substrings that mark a value as a template stand-in, not a secret.

``tempest new`` writes ``JWT_SECRET=change-me-change-me-change-me-32``,
which is long enough to pass a length check and is exactly what
``check_secrets`` reports as ``security.W004``. ``init`` treats any
value containing one of these (case-insensitively) as unset.
"""

_VAPID_KEYS: tuple[str, str] = ("VAPID_PUBLIC_KEY", "VAPID_PRIVATE_KEY")

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


def _read_env_values(path: Path) -> dict[str, str]:
    """Parse ``KEY=value`` pairs out of a ``.env`` file.

    Comments, blank lines and lines without ``=`` are skipped. Only the
    shape this module writes is parsed — no quote stripping, no
    interpolation — because the caller compares against a placeholder,
    not against a value it will hand to a client.

    Args:
        path (Path): The ``.env`` file (may not exist).

    Returns:
        dict[str, str]: ``KEY -> value`` for every parsed line.
    """
    if not path.is_file():
        return {}
    values: dict[str, str] = {}
    for line in path.read_text(encoding="utf-8").splitlines():
        stripped = line.strip()
        if not stripped or stripped.startswith("#") or "=" not in stripped:
            continue
        key, _, value = stripped.partition("=")
        values[key.strip()] = value.strip()
    return values


def _looks_unset(value: str | None) -> bool:
    """Return whether ``value`` still needs a real secret.

    Args:
        value (str | None): The current value, or ``None`` when the key
            is absent from the file.

    Returns:
        bool: True when the key is missing, empty, or carries one of the
        template placeholders.
    """
    if value is None or value == "":
        return True
    lowered = value.lower()
    return any(marker in lowered for marker in _PLACEHOLDER_MARKERS)


def _rewrite_env(path: Path, new_values: dict[str, str]) -> tuple[list[str], list[str]]:
    """Apply ``new_values`` to a ``.env`` file's content.

    Existing ``KEY=...`` lines are replaced in place (preserving order
    and surrounding lines); keys not present are appended at the end.

    Args:
        path (Path): The ``.env`` file (may not exist yet).
        new_values (dict[str, str]): ``KEY -> new secret`` mapping.

    The file is left readable by its owner only (``0600``). It now holds
    freshly minted secrets, and the process umask on a shared host commonly
    yields ``0644`` — world-readable, which for this content is a leak on
    its own.

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
    _restrict(path)
    return updated, appended


def _restrict(path: Path) -> None:
    """Make ``path`` readable and writable by its owner only.

    Args:
        path (Path): The file to lock down.
    """
    with suppress(OSError):
        path.chmod(0o600)


def _backup(path: Path) -> Path | None:
    """Copy ``path`` next to itself as ``.bak``, owner-readable only.

    Args:
        path (Path): The file to back up.

    Returns:
        Path | None: The backup path, or ``None`` when there was no file
        to copy.
    """
    if not path.is_file():
        return None
    backup = path.with_suffix(path.suffix + ".bak")
    backup.write_text(path.read_text(encoding="utf-8"), encoding="utf-8")
    _restrict(backup)
    return backup


def _split_keys(keys: str) -> list[str]:
    """Split a comma-separated ``--keys`` value into names.

    Args:
        keys (str): The raw option value.

    Returns:
        list[str]: The non-empty names, in the order given.

    Raises:
        typer.Exit: Exit code 2 when the value holds no name at all.
    """
    names = [k.strip() for k in keys.split(",") if k.strip()]
    if not names:
        typer.echo("error: --keys produced no names.", err=True)
        raise typer.Exit(2)
    return names


def _vapid_keypair() -> tuple[str, str]:
    """Generate a VAPID (P-256) key pair as URL-safe base64 strings.

    The private key is the raw 32-byte scalar and the public key is the
    65-byte uncompressed point, both base64url-encoded without padding —
    the pair of shapes ``py_vapid.Vapid02.from_string`` reads back and
    the browser accepts as ``applicationServerKey``.

    Returns:
        tuple[str, str]: ``(public_key, private_key)``.

    Raises:
        typer.Exit: Exit code 2 when ``cryptography`` is not installed.
    """
    try:
        from cryptography.hazmat.primitives import serialization
        from cryptography.hazmat.primitives.asymmetric import ec
    except ImportError as exc:
        typer.echo(
            "error: generating VAPID keys needs 'cryptography'. Install the "
            'extra: uv add "tempest-fastapi-sdk[webpush]".',
            err=True,
        )
        raise typer.Exit(2) from exc

    key = ec.generate_private_key(ec.SECP256R1())
    private_raw = key.private_numbers().private_value.to_bytes(32, "big")
    public_raw = key.public_key().public_bytes(
        serialization.Encoding.X962,
        serialization.PublicFormat.UncompressedPoint,
    )
    return (_b64url(public_raw), _b64url(private_raw))


def _b64url(raw: bytes) -> str:
    """Encode ``raw`` as unpadded URL-safe base64.

    Args:
        raw (bytes): The bytes to encode.

    Returns:
        str: The encoded value, with ``=`` padding stripped.
    """
    return base64.urlsafe_b64encode(raw).decode("ascii").rstrip("=")


@secrets_app.command("generate")
def secrets_generate(
    keys: str = typer.Option(
        "",
        "--keys",
        "-k",
        help=(
            "Comma-separated env var names to label the output with "
            "(prints 'KEY=value' lines). Omitted, bare values are printed."
        ),
    ),
    count: int = typer.Option(
        1,
        "--count",
        "-c",
        min=1,
        help="How many secrets to print. Refused together with --keys.",
    ),
    length: int = typer.Option(
        48,
        "--length",
        "-l",
        min=16,
        help="Bytes of entropy per secret (URL-safe encoded longer).",
    ),
    as_json: bool = typer.Option(
        False,
        "--json",
        help="Print a JSON object (with --keys) or array (without).",
    ),
) -> None:
    """Print fresh secrets to stdout, writing nothing.

    This is the command to pipe into a secret manager or paste into a
    deployment form. Use ``tempest secrets init`` to fill a local
    ``.env`` and ``tempest secrets rotate`` to replace values already in
    one.

    Raises:
        typer.Exit: Exit code 2 when ``--count`` is combined with
            ``--keys`` (the names already fix the count), or when
            ``--keys`` repeats a name.
    """
    names = _split_keys(keys) if keys else []
    if names and count != 1:
        typer.echo(
            "error: --count and --keys set the same thing. Pass one name "
            "per secret, or drop --keys.",
            err=True,
        )
        raise typer.Exit(2)
    if len(set(names)) != len(names):
        typer.echo("error: --keys repeats a name.", err=True)
        raise typer.Exit(2)

    if names:
        values = {name: _generate(length) for name in names}
        if as_json:
            typer.echo(json.dumps(values, indent=2))
            return
        for name, value in values.items():
            typer.echo(f"{name}={value}")
        return

    generated = [_generate(length) for _ in range(count)]
    if as_json:
        typer.echo(json.dumps(generated, indent=2))
        return
    for value in generated:
        typer.echo(value)


@secrets_app.command("init")
def secrets_init(
    keys: str = typer.Option(
        ",".join(_DEFAULT_KEYS),
        "--keys",
        "-k",
        help=(
            "Comma-separated env var names to fill. Defaults to "
            "'JWT_SECRET,TOKEN_SECRET'."
        ),
    ),
    env_file: str = typer.Option(
        ".env",
        "--env",
        "-e",
        help="Path to the .env file to fill.",
    ),
    length: int = typer.Option(
        48,
        "--length",
        "-l",
        min=16,
        help="Bytes of entropy per secret (URL-safe encoded longer).",
    ),
    force: bool = typer.Option(
        False,
        "--force",
        "-f",
        help="Also replace keys that already hold a real value (backs the file up).",
    ),
) -> None:
    """Fill the secret keys a ``.env`` leaves unset, keeping the rest.

    A key counts as unset when it is missing from the file, empty, or
    still carries the ``change-me`` placeholder ``tempest new`` writes —
    the same value ``tempest check-config`` reports as
    ``security.W004``. Every other key is kept, so running this after
    ``tempest new`` is safe and repeatable.

    With ``--force`` the command replaces real values too, and backs the
    file up to ``.env.bak`` first because there is then something to
    lose.
    """
    names = _split_keys(keys)
    path = Path(env_file).expanduser()
    current = _read_env_values(path)

    targets = [name for name in names if force or _looks_unset(current.get(name))]
    kept = [name for name in names if name not in targets]

    if not targets:
        for name in kept:
            typer.echo(f"kept {name} (already set)")
        typer.echo("Nothing to do.")
        return

    if force and any(not _looks_unset(current.get(name)) for name in targets):
        backup = _backup(path)
        if backup is not None:
            typer.echo(f"Backed up {path} -> {backup}")

    updated, appended = _rewrite_env(
        path,
        {name: _generate(length) for name in targets},
    )
    for name in updated:
        reason = "was placeholder" if current.get(name) else "was empty"
        typer.echo(f"set {name} ({'replaced' if force else reason})")
    for name in appended:
        typer.echo(f"set {name} (was missing)")
    for name in kept:
        typer.echo(f"kept {name} (already set)")


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
    names = _split_keys(keys)
    new_values = {name: _generate(length) for name in names}

    if show:
        for name, value in new_values.items():
            typer.echo(f"{name}={value}")
        return

    path = Path(env_file).expanduser()
    if not no_backup:
        backup = _backup(path)
        if backup is not None:
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


@secrets_app.command("vapid")
def secrets_vapid(
    env_file: str = typer.Option(
        ".env",
        "--env",
        "-e",
        help="Path to the .env file to write the pair into.",
    ),
    subject: str = typer.Option(
        "",
        "--subject",
        "-s",
        help=(
            "Contact advertised in the VAPID JWT ('mailto:you@example.com' "
            "or 'https://example.com'). Written as VAPID_SUBJECT when given."
        ),
    ),
    show: bool = typer.Option(
        False,
        "--print",
        help="Print the pair to stdout instead of writing .env.",
    ),
    force: bool = typer.Option(
        False,
        "--force",
        "-f",
        help="Replace a VAPID key pair the .env already holds.",
    ),
) -> None:
    """Generate the Web Push (VAPID) key pair for this service.

    Web Push needs a P-256 key pair, not a random string: the browser
    subscribes with the public key and the server signs every push with
    the private one. The values land as ``VAPID_PUBLIC_KEY`` /
    ``VAPID_PRIVATE_KEY``, which is what ``WebPushSettings`` reads.

    Replacing a pair the file already holds **invalidates every existing
    subscription** — browsers subscribed with the old public key stop
    accepting the pushes signed by the new private one, and each client
    has to subscribe again. The command therefore refuses to overwrite
    without ``--force``.

    Raises:
        typer.Exit: Exit code 1 when the ``.env`` already holds a key
            pair and ``--force`` was not passed.
    """
    path = Path(env_file).expanduser()
    if not show and not force:
        current = _read_env_values(path)
        existing = [key for key in _VAPID_KEYS if not _looks_unset(current.get(key))]
        if existing:
            typer.echo(
                f"error: {path} already holds {', '.join(existing)}. Replacing "
                "the pair invalidates every existing browser subscription — "
                "pass --force if that is what you want.",
                err=True,
            )
            raise typer.Exit(1)

    public_key, private_key = _vapid_keypair()
    new_values = {"VAPID_PUBLIC_KEY": public_key, "VAPID_PRIVATE_KEY": private_key}
    if subject:
        new_values["VAPID_SUBJECT"] = subject

    if show:
        for name, value in new_values.items():
            typer.echo(f"{name}={value}")
        return

    backup = _backup(path)
    if backup is not None:
        typer.echo(f"Backed up {path} -> {backup}")
    updated, appended = _rewrite_env(path, new_values)
    for name in [*updated, *appended]:
        typer.echo(f"set {name}")
    typer.echo("Done. Serve VAPID_PUBLIC_KEY to the browser as applicationServerKey.")


__all__: list[str] = [
    "secrets_app",
]
