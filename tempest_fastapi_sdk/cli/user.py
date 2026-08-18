"""``tempest user`` — seed and inspect users from the command line.

Imports the project's concrete ``UserModel`` (defaults to
``src.db.models:UserModel``) and writes through SQLAlchemy. Useful
for bootstrapping the first admin so the ``/admin`` login works
out of the box without manual SQL.

A concrete ``UserModel`` is expected to add columns of its own, so
``create`` does not assume the four columns the SDK knows about are the
whole row: ``--set name=value`` fills any other mapped column, and a
column the database requires and nothing defaults is prompted for on a
terminal (or reported as a hard error without one).
"""

from __future__ import annotations

import asyncio
import enum
import importlib
import json
import os
import sys
from datetime import date, datetime, time
from decimal import Decimal
from getpass import getpass
from pathlib import Path
from typing import TYPE_CHECKING, Any, cast
from uuid import UUID

import typer

if TYPE_CHECKING:
    from sqlalchemy import Column

    from tempest_fastapi_sdk import BaseUserModel

_OWN_FLAG_COLUMNS: dict[str, str] = {
    "email": "--email",
    "hashed_password": "--password",
    "is_admin": "--admin/--no-admin",
}
"""Columns ``create`` writes from its own options, so ``--set`` refuses them.

Accepting both spellings would let ``--set email=...`` silently lose to
``--email`` (or the other way round) depending on the order the values
are merged, and ``--set hashed_password=...`` would put a credential in
the shell history in a shape the CLI never verified.
"""

_TRUE_WORDS: frozenset[str] = frozenset({"1", "true", "t", "yes", "y", "on"})
_FALSE_WORDS: frozenset[str] = frozenset({"0", "false", "f", "no", "n", "off"})


def _stdin_is_interactive() -> bool:
    """Return whether stdin is attached to an interactive terminal.

    Isolated as a one-liner so the interactive admin prompt can be
    exercised in tests without faking the global ``sys.stdin``.

    Returns:
        bool: True when stdin is a TTY, False under pipes / CI / tests.
    """
    return sys.stdin.isatty()


def _resolve_database_url() -> str:
    """Pull the active DB URL from env / settings / fail loudly.

    Returns:
        str: The resolved URL.

    Raises:
        typer.Exit: When no URL can be found.
    """
    env = os.environ.get("DATABASE_URL")
    if env:
        return env
    cwd = Path.cwd()
    if (cwd / "src" / "core" / "settings.py").is_file():
        sys.path.insert(0, str(cwd))
        try:
            from src.core.settings import settings  # type: ignore[import-not-found]

            url = getattr(settings, "DATABASE_URL", None)
            if isinstance(url, str) and url:
                return url
        except Exception as exc:
            typer.echo(
                f"error: could not load src.core.settings ({exc}). "
                f"Run inside the project root or set DATABASE_URL.",
                err=True,
            )
            raise typer.Exit(2) from exc
    typer.echo(
        "error: DATABASE_URL not set and src/core/settings.py not found. "
        "Run inside the project root or export DATABASE_URL.",
        err=True,
    )
    raise typer.Exit(2)


def _load_user_model(dotted: str) -> type[BaseUserModel]:
    """Import the project's concrete ``UserModel`` via dotted spec.

    Args:
        dotted (str): ``"module.path:ClassName"`` (the default
            ``"src.db.models:UserModel"`` is what the scaffold ships).

    Returns:
        type[BaseUserModel]: The concrete user model class.

    Raises:
        typer.Exit: When the import fails or the class is not a
            :class:`BaseUserModel` subclass.
    """
    from tempest_fastapi_sdk import BaseUserModel as _BaseUserModel

    module_path, _, class_name = dotted.partition(":")
    if not module_path or not class_name:
        typer.echo(
            f"error: --model must be 'module.path:ClassName', got {dotted!r}",
            err=True,
        )
        raise typer.Exit(2)
    sys.path.insert(0, str(Path.cwd()))
    try:
        module = importlib.import_module(module_path)
    except ImportError as exc:
        typer.echo(f"error: cannot import {module_path!r}: {exc}", err=True)
        raise typer.Exit(2) from exc
    try:
        model = getattr(module, class_name)
    except AttributeError as exc:
        typer.echo(
            f"error: {module_path!r} has no attribute {class_name!r}",
            err=True,
        )
        raise typer.Exit(2) from exc
    if not isinstance(model, type) or not issubclass(model, _BaseUserModel):
        typer.echo(
            f"error: {dotted} is not a BaseUserModel subclass.",
            err=True,
        )
        raise typer.Exit(2)
    return model


async def _create_user(
    database_url: str,
    user_model: type[BaseUserModel],
    *,
    email: str,
    password: str,
    is_admin: bool,
    extra: dict[str, Any] | None = None,
) -> str:
    """Insert one user row, return its id as a string.

    Args:
        database_url (str): The resolved database URL.
        user_model (type[BaseUserModel]): The concrete user model.
        email (str): Login identifier, stored lowercased.
        password (str): Plaintext password, hashed before the insert.
        is_admin (bool): Whether the user may log in to ``/admin``.
        extra (dict[str, Any] | None): Values for the model's own
            columns, already validated and converted.

    Returns:
        str: The new row's id.

    Raises:
        ConflictException: When the insert is rejected by the database.
    """
    from tempest_fastapi_sdk import AsyncDatabaseManager
    from tempest_fastapi_sdk.exceptions import ConflictException

    db = AsyncDatabaseManager(database_url)
    await db.connect()
    try:
        async with db.get_session_context() as session:
            user = user_model(
                email=email.lower(),
                is_admin=is_admin,
                is_active=True,
                **(extra or {}),
            )
            user.set_password(password)
            session.add(user)
            try:
                await session.commit()
            except Exception as exc:
                await session.rollback()
                raise ConflictException(
                    message=f"could not insert user: {exc}",
                ) from exc
            await session.refresh(user)
            return str(user.id)
    finally:
        await db.disconnect()


async def _set_user_admin(
    database_url: str,
    user_model: type[BaseUserModel],
    *,
    email: str,
    is_admin: bool,
) -> str | None:
    """Flip ``is_admin`` for one user, found by email.

    Args:
        database_url (str): The resolved database URL.
        user_model (type[BaseUserModel]): The concrete user model.
        email (str): Email of the user to update (normalized to lower).
        is_admin (bool): The new ``is_admin`` value.

    Returns:
        str | None: The user's id as a string, or ``None`` when no user
        matches the email.
    """
    from sqlalchemy import select

    from tempest_fastapi_sdk import AsyncDatabaseManager

    db = AsyncDatabaseManager(database_url)
    await db.connect()
    try:
        async with db.get_session_context() as session:
            result = await session.execute(
                select(user_model).where(user_model.email == email.lower()),
            )
            user = result.scalar_one_or_none()
            if user is None:
                return None
            user.is_admin = is_admin
            await session.commit()
            await session.refresh(user)
            return str(user.id)
    finally:
        await db.disconnect()


async def _list_users(
    database_url: str,
    user_model: type[BaseUserModel],
    *,
    admin_only: bool,
) -> list[tuple[str, str, bool, bool]]:
    """Return ``(id, email, is_admin, is_active)`` rows."""
    from sqlalchemy import select

    from tempest_fastapi_sdk import AsyncDatabaseManager

    db = AsyncDatabaseManager(database_url)
    await db.connect()
    try:
        async with db.get_session_context() as session:
            query = select(user_model)
            if admin_only:
                query = query.where(user_model.is_admin.is_(True))
            result = await session.execute(query)
            return [
                (str(u.id), u.email, bool(u.is_admin), bool(u.is_active))
                for u in result.scalars().all()
            ]
    finally:
        await db.disconnect()


def _mapped_columns(user_model: type[BaseUserModel]) -> dict[str, Column[Any]]:
    """Map every mapped column name of ``user_model`` to its column.

    Args:
        user_model (type[BaseUserModel]): The concrete user model.

    Returns:
        dict[str, Column[Any]]: Attribute name -> column, in mapper
        order, including the ones ``create`` fills from its own options.
    """
    from sqlalchemy import inspect as sa_inspect

    columns: dict[str, Column[Any]] = {}
    for attr in sa_inspect(user_model).column_attrs:
        columns[attr.key] = cast("Column[Any]", attr.columns[0])
    return columns


def _coerce_column_value(column: Column[Any], raw: str) -> Any:
    """Convert a command-line string to the column's Python type.

    Covers the types a seeded row realistically carries: strings,
    booleans, numbers, UUIDs, ISO-8601 date/time values, enums (by value
    first, then by member name, matching how
    :class:`~tempest_fastapi_sdk.db.enums.TempestEnum` stores them) and
    JSON columns. Anything else is passed through as the raw string, so
    a custom type that accepts one keeps working.

    Args:
        column (Column[Any]): The target column.
        raw (str): The value as typed on the command line.

    Returns:
        Any: The converted value.

    Raises:
        ValueError: When ``raw`` cannot be converted to the column type.
    """
    try:
        python_type: Any = column.type.python_type
    except NotImplementedError:
        return raw
    if python_type is str:
        return raw
    if python_type is bool:
        lowered = raw.strip().lower()
        if lowered in _TRUE_WORDS:
            return True
        if lowered in _FALSE_WORDS:
            return False
        raise ValueError(
            f"expected a boolean (one of {', '.join(sorted(_TRUE_WORDS))} / "
            f"{', '.join(sorted(_FALSE_WORDS))}), got {raw!r}"
        )
    if python_type is int:
        return int(raw)
    if python_type is float:
        return float(raw)
    if python_type is Decimal:
        return Decimal(raw)
    if python_type is UUID:
        return UUID(raw)
    if python_type is datetime:
        return datetime.fromisoformat(raw)
    if python_type is date:
        return date.fromisoformat(raw)
    if python_type is time:
        return time.fromisoformat(raw)
    if python_type is bytes:
        return raw.encode()
    if isinstance(python_type, type) and issubclass(python_type, enum.Enum):
        try:
            return python_type(raw)
        except ValueError:
            try:
                return python_type[raw]
            except KeyError as exc:
                accepted = ", ".join(str(member.value) for member in python_type)
                raise ValueError(f"expected one of {accepted}, got {raw!r}") from exc
    if python_type in (dict, list):
        return json.loads(raw)
    return raw


def _parse_set_options(
    user_model: type[BaseUserModel],
    pairs: list[str],
) -> dict[str, Any]:
    """Turn ``--set name=value`` pairs into validated model kwargs.

    Args:
        user_model (type[BaseUserModel]): The concrete user model.
        pairs (list[str]): The raw ``name=value`` strings.

    Returns:
        dict[str, Any]: Column name -> converted value.

    Raises:
        typer.Exit: With code 2 when a pair is malformed, names a column
            the model does not map, names a column ``create`` owns, or
            carries a value the column type rejects.
    """
    columns = _mapped_columns(user_model)
    data: dict[str, Any] = {}
    for pair in pairs:
        name, separator, raw = pair.partition("=")
        name = name.strip()
        if not separator or not name:
            typer.echo(
                f"error: --set expects 'column=value', got {pair!r}",
                err=True,
            )
            raise typer.Exit(2)
        if name in _OWN_FLAG_COLUMNS:
            typer.echo(
                f"error: --set {name}=... is not allowed; use "
                f"{_OWN_FLAG_COLUMNS[name]} instead.",
                err=True,
            )
            raise typer.Exit(2)
        if name not in columns:
            accepted = ", ".join(
                column for column in columns if column not in _OWN_FLAG_COLUMNS
            )
            typer.echo(
                f"error: {user_model.__name__} has no column {name!r}. "
                f"Accepted columns: {accepted}.",
                err=True,
            )
            raise typer.Exit(2)
        try:
            data[name] = _coerce_column_value(columns[name], raw)
        except (ValueError, TypeError) as exc:
            typer.echo(f"error: --set {name}={raw!r}: {exc}", err=True)
            raise typer.Exit(2) from exc
    return data


def _missing_required_columns(
    user_model: type[BaseUserModel],
    provided: dict[str, Any],
) -> list[str]:
    """List columns the insert would send as ``NULL`` against ``NOT NULL``.

    A column counts as missing when the database requires a value, no
    Python-side or server-side default supplies one, it is not the
    primary key, ``create`` does not write it from its own options, and
    ``--set`` did not provide it.

    Args:
        user_model (type[BaseUserModel]): The concrete user model.
        provided (dict[str, Any]): Values already collected via ``--set``.

    Returns:
        list[str]: The column names still missing, in mapper order.
    """
    missing: list[str] = []
    for name, column in _mapped_columns(user_model).items():
        if name in provided or name in _OWN_FLAG_COLUMNS:
            continue
        if column.primary_key or column.nullable:
            continue
        if column.default is not None or column.server_default is not None:
            continue
        missing.append(name)
    return missing


def _fill_required_columns(
    user_model: type[BaseUserModel],
    provided: dict[str, Any],
) -> dict[str, Any]:
    """Complete ``provided`` with the required columns still missing.

    On a terminal each missing column is prompted for, mirroring what
    the ``--admin``/``--no-admin`` prompt already does. Without a TTY
    there is nobody to ask, so the run fails naming every column the
    insert would have sent as ``NULL`` — which is the error the database
    would have raised anyway, minus the stack trace.

    Args:
        user_model (type[BaseUserModel]): The concrete user model.
        provided (dict[str, Any]): Values collected via ``--set``.

    Returns:
        dict[str, Any]: ``provided`` plus the prompted values.

    Raises:
        typer.Exit: With code 2 when a required column is missing in a
            non-interactive run, is answered with an empty string, or is
            answered with a value the column type rejects.
    """
    missing = _missing_required_columns(user_model, provided)
    if not missing:
        return provided
    if not _stdin_is_interactive():
        listed = ", ".join(missing)
        typer.echo(
            f"error: {user_model.__name__} requires a value for: {listed}. "
            f"Pass each one as --set <column>=<value>.",
            err=True,
        )
        raise typer.Exit(2)
    columns = _mapped_columns(user_model)
    filled = dict(provided)
    for name in missing:
        answer = typer.prompt(name).strip()
        if not answer:
            typer.echo(f"error: {name} is required.", err=True)
            raise typer.Exit(2)
        try:
            filled[name] = _coerce_column_value(columns[name], answer)
        except (ValueError, TypeError) as exc:
            typer.echo(f"error: {name}={answer!r}: {exc}", err=True)
            raise typer.Exit(2) from exc
    return filled


user_app: typer.Typer = typer.Typer(
    name="user",
    help="Seed and inspect users (writes through the project's UserModel).",
    no_args_is_help=True,
)


@user_app.command("create")
def user_create(
    email: str = typer.Option(
        ...,
        "--email",
        "-e",
        help="Email — normalized to lowercase, must be unique.",
    ),
    password: str | None = typer.Option(
        None,
        "--password",
        "-p",
        help=(
            "Password. Omit to read it interactively (avoids leaving the "
            "secret in shell history)."
        ),
    ),
    is_admin: bool | None = typer.Option(
        None,
        "--admin/--no-admin",
        help=(
            "Set ``is_admin=True`` so the user can log in to ``/admin``. "
            "Omit both flags in an interactive terminal to be prompted; "
            "non-interactive runs default to a regular (non-admin) user."
        ),
    ),
    model: str = typer.Option(
        "src.db.models:UserModel",
        "--model",
        help=(
            "Dotted spec for the concrete UserModel. Override only when "
            "your model lives outside the scaffolded layout."
        ),
    ),
    set_values: list[str] = typer.Option(
        [],
        "--set",
        metavar="COLUMN=VALUE",
        help=(
            "Value for a column your UserModel adds, repeatable "
            "(--set display_name=Ana --set locale=pt-BR). Validated "
            "against the model's mapped columns and converted to the "
            "column type. Use --email / --password / --admin for the "
            "columns those flags own."
        ),
    ),
) -> None:
    """Create one user row + print its id.

    Notes:
        With neither ``--admin`` nor ``--no-admin`` given, the choice is
        prompted when attached to a terminal and defaults to non-admin
        otherwise, so a scripted or CI run never blocks waiting on input.

        A database that rejects the insert (a duplicate email, most
        often) exits with code 1 and the database's own message, instead
        of a traceback.

        A ``UserModel`` that adds a ``NOT NULL`` column with no default
        cannot be seeded from ``--email``/``--password``/``--admin``
        alone. Pass each one as ``--set <column>=<value>``; on a terminal
        the ones still missing are prompted for, and a non-interactive
        run exits with code 2 naming them instead of letting the database
        reject the insert.
    """
    if not password:
        password = getpass("Password: ")
        confirm = getpass("Confirm: ")
        if password != confirm:
            typer.echo("error: passwords do not match.", err=True)
            raise typer.Exit(2)
    if len(password) < 8:
        typer.echo("error: password must be at least 8 characters.", err=True)
        raise typer.Exit(2)

    if is_admin is None:
        if _stdin_is_interactive():
            is_admin = typer.confirm(
                "Should this user be an administrator?",
                default=False,
            )
        else:
            is_admin = False

    database_url = _resolve_database_url()
    user_model = _load_user_model(model)
    extra = _fill_required_columns(
        user_model,
        _parse_set_options(user_model, set_values),
    )
    from tempest_fastapi_sdk.exceptions import ConflictException

    try:
        user_id = asyncio.run(
            _create_user(
                database_url,
                user_model,
                email=email,
                password=password,
                is_admin=is_admin,
                extra=extra,
            )
        )
    except ConflictException as exc:
        typer.echo(f"error: {exc.message}", err=True)
        raise typer.Exit(1) from exc
    role = "admin" if is_admin else "user"
    typer.echo(f"Created {role}: {email} (id={user_id})")


@user_app.command("list")
def user_list(
    admin_only: bool = typer.Option(
        False,
        "--admin",
        help="List only users with ``is_admin=True``.",
    ),
    model: str = typer.Option(
        "src.db.models:UserModel",
        "--model",
        help="Dotted spec for the concrete UserModel.",
    ),
) -> None:
    """Print one row per user — ``id  email  admin  active``."""
    database_url = _resolve_database_url()
    user_model = _load_user_model(model)
    rows = asyncio.run(
        _list_users(database_url, user_model, admin_only=admin_only),
    )
    if not rows:
        typer.echo("(no users)")
        return
    for uid, email, admin, active in rows:
        flags = "+admin" if admin else "      "
        status = "active" if active else "inactive"
        typer.echo(f"{uid}  {email}  {flags}  {status}")


def _run_set_admin(email: str, model: str, *, is_admin: bool) -> None:
    """Resolve resources, flip ``is_admin`` and report the outcome.

    Args:
        email (str): Email of the user to update.
        model (str): Dotted spec for the concrete UserModel.
        is_admin (bool): The new ``is_admin`` value (True promotes,
            False revokes).

    Raises:
        typer.Exit: With code 1 when no user matches the email.
    """
    database_url = _resolve_database_url()
    user_model = _load_user_model(model)
    user_id = asyncio.run(
        _set_user_admin(
            database_url,
            user_model,
            email=email,
            is_admin=is_admin,
        )
    )
    if user_id is None:
        typer.echo(f"error: no user found with email {email!r}.", err=True)
        raise typer.Exit(1)
    verb = "Promoted" if is_admin else "Revoked admin from"
    typer.echo(f"{verb} {email.lower()} (id={user_id})")


@user_app.command("promote")
def user_promote(
    email: str = typer.Option(
        ...,
        "--email",
        "-e",
        help="Email of the existing user to promote to administrator.",
    ),
    model: str = typer.Option(
        "src.db.models:UserModel",
        "--model",
        help="Dotted spec for the concrete UserModel.",
    ),
) -> None:
    """Set ``is_admin=True`` for an existing user (grant ``/admin`` access)."""
    _run_set_admin(email, model, is_admin=True)


@user_app.command("revoke")
def user_revoke(
    email: str = typer.Option(
        ...,
        "--email",
        "-e",
        help="Email of the existing user to demote to a regular account.",
    ),
    model: str = typer.Option(
        "src.db.models:UserModel",
        "--model",
        help="Dotted spec for the concrete UserModel.",
    ),
) -> None:
    """Set ``is_admin=False`` for an existing user (revoke ``/admin`` access)."""
    _run_set_admin(email, model, is_admin=False)


__all__: list[str] = [
    "user_app",
]
