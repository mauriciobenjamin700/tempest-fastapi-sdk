"""``tempest user`` — seed and inspect users from the command line.

Imports the project's concrete ``UserModel`` (defaults to
``src.db.models:UserModel``) and writes through SQLAlchemy. Useful
for bootstrapping the first admin so the ``/admin`` login works
out of the box without manual SQL.
"""

from __future__ import annotations

import asyncio
import importlib
import os
import sys
from getpass import getpass
from pathlib import Path
from typing import TYPE_CHECKING

import typer

if TYPE_CHECKING:
    from tempest_fastapi_sdk import BaseUserModel


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
) -> str:
    """Insert one user row, return its id as a string."""
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
    is_admin: bool = typer.Option(
        False,
        "--admin",
        help="Set ``is_admin=True`` so the user can log in to ``/admin``.",
    ),
    model: str = typer.Option(
        "src.db.models:UserModel",
        "--model",
        help=(
            "Dotted spec for the concrete UserModel. Override only when "
            "your model lives outside the scaffolded layout."
        ),
    ),
) -> None:
    """Create one user row + print its id."""
    if not password:
        password = getpass("Password: ")
        confirm = getpass("Confirm: ")
        if password != confirm:
            typer.echo("error: passwords do not match.", err=True)
            raise typer.Exit(2)
    if len(password) < 8:
        typer.echo("error: password must be at least 8 characters.", err=True)
        raise typer.Exit(2)

    database_url = _resolve_database_url()
    user_model = _load_user_model(model)
    user_id = asyncio.run(
        _create_user(
            database_url,
            user_model,
            email=email,
            password=password,
            is_admin=is_admin,
        )
    )
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


__all__: list[str] = [
    "user_app",
]
