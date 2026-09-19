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

    from tempest_fastapi_sdk import BaseUserModel, BaseUserRefreshTokenModel
    from tempest_fastapi_sdk.utils.password import PasswordPolicy

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


def _load_project_settings() -> Any | None:
    """Import the project's ``settings`` object, or ``None`` when absent.

    The scaffolded layout puts it at ``src/core/settings.py``; the file
    is probed before the import so a directory that simply is not a
    Tempest project reads as "nothing to load" rather than as a broken
    one. ``sys.path`` gains the working directory first, so the import
    resolves against the project the operator is standing in.

    Errors are deliberately **not** swallowed here: a settings module
    that exists and raises means something different to every caller —
    fatal when the database URL has no other source, ignorable when only
    the password policy was being read — so each one decides.

    Returns:
        Any | None: The project's ``settings`` object, or ``None`` when
        there is no ``src/core/settings.py`` to import.
    """
    cwd = Path.cwd()
    if not (cwd / "src" / "core" / "settings.py").is_file():
        return None
    sys.path.insert(0, str(cwd))
    from src.core.settings import settings  # type: ignore[import-not-found]

    return settings


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
    try:
        settings = _load_project_settings()
    except Exception as exc:
        typer.echo(
            f"error: could not load src.core.settings ({exc}). "
            f"Run inside the project root or set DATABASE_URL.",
            err=True,
        )
        raise typer.Exit(2) from exc
    if settings is not None:
        url = getattr(settings, "DATABASE_URL", None)
        if isinstance(url, str) and url:
            return url
    typer.echo(
        "error: DATABASE_URL not set and src/core/settings.py not found. "
        "Run inside the project root or export DATABASE_URL.",
        err=True,
    )
    raise typer.Exit(2)


def _import_symbol(dotted: str, option: str) -> Any:
    """Import ``"module.path:ClassName"`` from the project root.

    The parse / import / ``getattr`` half of every ``--*-model`` option,
    shared so each loader below only owns the type check that is
    actually its own. ``sys.path`` gains the working directory first, so
    a spec like ``src.db.models:UserModel`` resolves against the project
    the operator is standing in rather than the installed SDK.

    Args:
        dotted (str): The ``"module.path:ClassName"`` spec.
        option (str): The CLI flag the spec came from, named back to the
            operator in every error message.

    Returns:
        Any: Whatever the module binds under that name — the caller
        checks the type.

    Raises:
        typer.Exit: With code 2 when the spec is malformed, the module
            cannot be imported, or the attribute is missing.
    """
    module_path, _, class_name = dotted.partition(":")
    if not module_path or not class_name:
        typer.echo(
            f"error: {option} must be 'module.path:ClassName', got {dotted!r}",
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
        return getattr(module, class_name)
    except AttributeError as exc:
        typer.echo(
            f"error: {module_path!r} has no attribute {class_name!r}",
            err=True,
        )
        raise typer.Exit(2) from exc


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

    model = _import_symbol(dotted, "--model")
    if not isinstance(model, type) or not issubclass(model, _BaseUserModel):
        typer.echo(
            f"error: {dotted} is not a BaseUserModel subclass.",
            err=True,
        )
        raise typer.Exit(2)
    return model


_DEFAULT_REFRESH_TOKEN_MODEL: str = "src.db.models:UserRefreshTokenModel"
"""Dotted spec ``set-password`` tries when ``--refresh-token-model`` is omitted.

The name ``tempest new`` scaffolds. Revoking the user's sessions is the
behaviour a password reset is supposed to have, so it has to be what
happens when the operator types nothing — a flag that must be
remembered to be secure is one that will be forgotten. Projects that
never wired the opt-in refresh table simply have nothing at this path,
which is why the lookup is allowed to come back empty instead of
failing.
"""


def _load_refresh_token_model(dotted: str) -> type[BaseUserRefreshTokenModel]:
    """Import the project's concrete refresh-token model via dotted spec.

    Args:
        dotted (str): ``"module.path:ClassName"`` for the concrete
            :class:`BaseUserRefreshTokenModel` subclass — the table the
            project passes as ``refresh_token_model=`` to
            :class:`~tempest_fastapi_sdk.UserAuthService`.

    Returns:
        type[BaseUserRefreshTokenModel]: The concrete model class.

    Raises:
        typer.Exit: When the import fails or the class is not a
            :class:`BaseUserRefreshTokenModel` subclass.
    """
    from tempest_fastapi_sdk import (
        BaseUserRefreshTokenModel as _BaseUserRefreshTokenModel,
    )

    model = _import_symbol(dotted, "--refresh-token-model")
    if not isinstance(model, type) or not issubclass(
        model,
        _BaseUserRefreshTokenModel,
    ):
        typer.echo(
            f"error: {dotted} is not a BaseUserRefreshTokenModel subclass.",
            err=True,
        )
        raise typer.Exit(2)
    return model


def _find_default_refresh_token_model() -> type[BaseUserRefreshTokenModel] | None:
    """Import :data:`_DEFAULT_REFRESH_TOKEN_MODEL`, or return ``None``.

    The tolerant sibling of :func:`_load_refresh_token_model`: a spec the
    operator typed is a promise the CLI must keep or refuse, but the
    conventional path is a guess, and a project that never opted into
    DB-backed refresh tokens has no such module. Every failure — absent
    module, absent attribute, wrong base — reads the same here, and the
    caller says out loud that no session was revoked.

    Returns:
        type[BaseUserRefreshTokenModel] | None: The scaffolded
        refresh-token model, or ``None`` when the project has none.
    """
    from tempest_fastapi_sdk import (
        BaseUserRefreshTokenModel as _BaseUserRefreshTokenModel,
    )

    module_path, _, class_name = _DEFAULT_REFRESH_TOKEN_MODEL.partition(":")
    sys.path.insert(0, str(Path.cwd()))
    try:
        module = importlib.import_module(module_path)
    except ImportError:
        return None
    model = getattr(module, class_name, None)
    if not isinstance(model, type) or not issubclass(
        model,
        _BaseUserRefreshTokenModel,
    ):
        return None
    return model


_POLICY_SETTINGS_FIELDS: tuple[str, ...] = (
    "AUTH_PASSWORD_MIN_LENGTH",
    "AUTH_PASSWORD_MAX_BYTES",
    "AUTH_PASSWORD_REQUIRE_COMPLEXITY",
)
"""The three ``AuthSettings`` fields :class:`PasswordPolicy` is built from.

Checked one by one before calling ``PasswordPolicy.from_settings``: a
project composes only the mixins it uses, so a ``Settings`` without
``AuthSettings`` is ordinary, not broken, and must fall back to the
defaults rather than raise ``AttributeError`` at the operator.
"""


def _resolve_password_policy() -> PasswordPolicy:
    """Read the project's password policy, or fall back to the defaults.

    The CLI writes to the same column ``signup`` and the admin panel
    write to, so it has to accept exactly the same passwords they do.
    Hard-coding a floor here is how the three drift: a value the CLI
    seeds is then rejected the first time its owner tries to change it.

    Reads ``src.core.settings:settings`` when the scaffolded layout is
    present and it carries the ``AUTH_PASSWORD_*`` fields; anything else
    — no project on disk, a ``Settings`` that never composed
    ``AuthSettings``, an import that blows up — yields the
    :class:`PasswordPolicy` defaults, which mirror the ``AuthSettings``
    field defaults.

    Returns:
        PasswordPolicy: The rules a plaintext password must satisfy.
    """
    from tempest_fastapi_sdk.utils.password import PasswordPolicy as _PasswordPolicy

    try:
        settings = _load_project_settings()
    except Exception:
        return _PasswordPolicy()
    if settings is None:
        return _PasswordPolicy()
    if not all(hasattr(settings, name) for name in _POLICY_SETTINGS_FIELDS):
        return _PasswordPolicy()
    return _PasswordPolicy.from_settings(settings)


def _validate_password(password: str) -> None:
    """Reject a plaintext the project's password policy refuses.

    Runs before anything touches the database, so a password bcrypt
    cannot hash (over 72 UTF-8 bytes) exits with a message naming the
    bound instead of surfacing the ``ValueError`` from
    ``PasswordUtils.hash`` as a traceback.

    Args:
        password (str): The plaintext to check.

    Raises:
        typer.Exit: With code 2 when the policy rejects it.
    """
    from tempest_fastapi_sdk.utils.password import check_password_policy

    violation = check_password_policy(password, _resolve_password_policy())
    if violation is not None:
        typer.echo(f"error: {violation.message}.", err=True)
        raise typer.Exit(2)


def _read_password(password: str | None) -> str:
    """Return a validated plaintext, prompting twice when none was given.

    Omitting ``--password`` keeps the secret out of shell history and
    out of the process list, so the prompt is the recommended path and
    the confirmation catches the typo that would otherwise lock the
    account out.

    Args:
        password (str | None): The value of ``--password``, or ``None``
            to read it from the terminal.

    Returns:
        str: The plaintext, already checked against the policy.

    Raises:
        typer.Exit: With code 2 when the two prompts disagree or the
            policy rejects the password.
    """
    if not password:
        password = getpass("Password: ")
        confirm = getpass("Confirm: ")
        if password != confirm:
            typer.echo("error: passwords do not match.", err=True)
            raise typer.Exit(2)
    _validate_password(password)
    return password


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


async def _set_user_password(
    database_url: str,
    user_model: type[BaseUserModel],
    *,
    email: str,
    password: str,
    refresh_token_model: type[BaseUserRefreshTokenModel] | None,
) -> tuple[str, int] | None:
    """Re-hash one user's password, found by email, and kill its sessions.

    The revocation runs inside the same transaction as the new hash, so
    the two land together: a commit that wrote the password but not the
    revocation would leave every stolen refresh token exchangeable
    against an account whose owner believes it was just secured.

    Args:
        database_url (str): The resolved database URL.
        user_model (type[BaseUserModel]): The concrete user model.
        email (str): Email of the user to update (normalized to lower).
        password (str): The new plaintext, hashed before the update.
        refresh_token_model (type[BaseUserRefreshTokenModel] | None):
            The project's concrete refresh-token table, or ``None`` to
            leave existing sessions alone.

    Returns:
        tuple[str, int] | None: The user's id and how many refresh
        tokens were revoked, or ``None`` when no user matches the email.
    """
    from sqlalchemy import select

    from tempest_fastapi_sdk import AsyncDatabaseManager
    from tempest_fastapi_sdk.auth.service import revoke_user_refresh_tokens

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
            user.set_password(password)
            revoked = 0
            if refresh_token_model is not None:
                revoked = await revoke_user_refresh_tokens(
                    session,
                    refresh_token_model,
                    user_id=user.id,
                )
            await session.commit()
            await session.refresh(user)
            return str(user.id), revoked
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


async def _fetch_user(
    database_url: str,
    user_model: type[BaseUserModel],
    *,
    email: str,
) -> dict[str, Any] | None:
    """Read one user's whole row, found by email.

    The row is materialized into a plain dict inside the session: the
    ORM instance expires at commit, and reading a column off it after
    the session closes raises ``MissingGreenlet`` in async context.

    Args:
        database_url (str): The resolved database URL.
        user_model (type[BaseUserModel]): The concrete user model.
        email (str): Email to look up (normalized to lower).

    Returns:
        dict[str, Any] | None: ``column -> value`` for every mapped
        column, or ``None`` when no user matches.
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
            return {name: getattr(user, name) for name in _mapped_columns(user_model)}
    finally:
        await db.disconnect()


async def _set_user_active(
    database_url: str,
    user_model: type[BaseUserModel],
    *,
    email: str,
    is_active: bool,
    refresh_token_model: type[BaseUserRefreshTokenModel] | None,
) -> tuple[str, int] | None:
    """Flip ``is_active`` for one user, found by email.

    Deactivating revokes the user's refresh tokens in the same
    transaction. Leaving them alive would let a stolen token keep
    minting access tokens for an account the operator just turned off —
    the same window ``set-password`` closes.

    Args:
        database_url (str): The resolved database URL.
        user_model (type[BaseUserModel]): The concrete user model.
        email (str): Email of the user to update (normalized to lower).
        is_active (bool): The new ``is_active`` value.
        refresh_token_model (type[BaseUserRefreshTokenModel] | None):
            The project's refresh-token table, or ``None`` when it has
            none.

    Returns:
        tuple[str, int] | None: The user's id and how many refresh
        tokens were revoked, or ``None`` when no user matches.
    """
    from sqlalchemy import select

    from tempest_fastapi_sdk import AsyncDatabaseManager
    from tempest_fastapi_sdk.auth.service import revoke_user_refresh_tokens

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
            user.is_active = is_active
            revoked = 0
            if not is_active and refresh_token_model is not None:
                revoked = await revoke_user_refresh_tokens(
                    session,
                    refresh_token_model,
                    user_id=user.id,
                )
            await session.commit()
            await session.refresh(user)
            return str(user.id), revoked
    finally:
        await db.disconnect()


async def _delete_user(
    database_url: str,
    user_model: type[BaseUserModel],
    *,
    email: str,
    refresh_token_model: type[BaseUserRefreshTokenModel] | None,
) -> str | None:
    """Delete one user row, found by email.

    The user's refresh tokens are deleted first, in the same
    transaction: the scaffolded token table carries a plain
    ``ForeignKey`` with no ``ON DELETE CASCADE``, so deleting the user
    while a token still points at it fails with an integrity error that
    names a constraint rather than the cause.

    Args:
        database_url (str): The resolved database URL.
        user_model (type[BaseUserModel]): The concrete user model.
        email (str): Email of the user to delete (normalized to lower).
        refresh_token_model (type[BaseUserRefreshTokenModel] | None):
            The project's refresh-token table, or ``None`` when it has
            none.

    Returns:
        str | None: The deleted user's id, or ``None`` when no user
        matches.
    """
    from sqlalchemy import delete, select

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
            user_id = str(user.id)
            if refresh_token_model is not None:
                await session.execute(
                    delete(refresh_token_model).where(
                        refresh_token_model.user_id == user.id,
                    ),
                )
            await session.delete(user)
            await session.commit()
            return user_id
    finally:
        await db.disconnect()


async def _revoke_sessions(
    database_url: str,
    user_model: type[BaseUserModel],
    refresh_token_model: type[BaseUserRefreshTokenModel],
    *,
    email: str,
) -> int | None:
    """Revoke every live refresh token of one user, found by email.

    Args:
        database_url (str): The resolved database URL.
        user_model (type[BaseUserModel]): The concrete user model.
        refresh_token_model (type[BaseUserRefreshTokenModel]): The
            project's refresh-token table.
        email (str): Email of the user to log out.

    Returns:
        int | None: How many tokens this call revoked — a row already
        revoked is not counted again — or ``None`` when no user matches.
    """
    from sqlalchemy import select

    from tempest_fastapi_sdk import AsyncDatabaseManager
    from tempest_fastapi_sdk.auth.service import revoke_user_refresh_tokens

    db = AsyncDatabaseManager(database_url)
    await db.connect()
    try:
        async with db.get_session_context() as session:
            found = await session.execute(
                select(user_model).where(user_model.email == email.lower()),
            )
            user = found.scalar_one_or_none()
            if user is None:
                return None
            revoked = await revoke_user_refresh_tokens(
                session,
                refresh_token_model,
                user_id=user.id,
            )
            await session.commit()
            return revoked
    finally:
        await db.disconnect()


async def _list_user_sessions(
    database_url: str,
    user_model: type[BaseUserModel],
    refresh_token_model: type[BaseUserRefreshTokenModel],
    *,
    email: str,
) -> list[dict[str, Any]] | None:
    """List the refresh tokens issued to one user.

    Args:
        database_url (str): The resolved database URL.
        user_model (type[BaseUserModel]): The concrete user model.
        refresh_token_model (type[BaseUserRefreshTokenModel]): The
            project's refresh-token table.
        email (str): Email of the user to inspect.

    Returns:
        list[dict[str, Any]] | None: One entry per token, newest first,
        or ``None`` when no user matches the email. An empty list is a
        user with no session, which is a result, not an error.
    """
    from sqlalchemy import select

    from tempest_fastapi_sdk import AsyncDatabaseManager

    db = AsyncDatabaseManager(database_url)
    await db.connect()
    try:
        async with db.get_session_context() as session:
            found = await session.execute(
                select(user_model).where(user_model.email == email.lower()),
            )
            user = found.scalar_one_or_none()
            if user is None:
                return None
            rows = await session.execute(
                select(refresh_token_model)
                .where(refresh_token_model.user_id == user.id)
                .order_by(refresh_token_model.created_at.desc()),
            )
            return [
                {
                    "id": str(token.id),
                    "family_id": str(token.family_id),
                    "created_at": token.created_at,
                    "expires_at": token.expires_at,
                    "revoked_at": token.revoked_at,
                    "used_at": token.used_at,
                }
                for token in rows.scalars().all()
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

        The password is checked against the project's own policy — the
        three ``AUTH_PASSWORD_*`` fields of ``src.core.settings``, or the
        ``PasswordPolicy`` defaults when the project does not compose
        ``AuthSettings`` — so a seeded account is never one its owner
        cannot log in to change.

        A ``UserModel`` that adds a ``NOT NULL`` column with no default
        cannot be seeded from ``--email``/``--password``/``--admin``
        alone. Pass each one as ``--set <column>=<value>``; on a terminal
        the ones still missing are prompted for, and a non-interactive
        run exits with code 2 naming them instead of letting the database
        reject the insert.
    """
    password = _read_password(password)

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


@user_app.command("set-password")
def user_set_password(
    email: str = typer.Option(
        ...,
        "--email",
        "-e",
        help="Email of the existing user whose password is being replaced.",
    ),
    password: str | None = typer.Option(
        None,
        "--password",
        "-p",
        help=(
            "New password. Omit to read it interactively (avoids leaving "
            "the secret in shell history)."
        ),
    ),
    model: str = typer.Option(
        "src.db.models:UserModel",
        "--model",
        help="Dotted spec for the concrete UserModel.",
    ),
    refresh_token_model: str | None = typer.Option(
        None,
        "--refresh-token-model",
        help=(
            "Dotted spec for the concrete refresh-token model. Omitted, "
            f"{_DEFAULT_REFRESH_TOKEN_MODEL!r} is tried, so the user's "
            "sessions are revoked along with the password whenever the "
            "project has that table."
        ),
    ),
    keep_sessions: bool = typer.Option(
        False,
        "--keep-sessions",
        help=(
            "Do not revoke anything: every refresh token the user holds "
            "stays exchangeable after the password changes."
        ),
    ),
) -> None:
    """Replace an existing user's password, found by email.

    The operator-side half of the reset flow: the account nobody can
    mail a link to, the first admin locked out of a fresh environment,
    the credential rotated after a report. The new plaintext is hashed
    with the model's own ``set_password``, so it is the same hash the
    login endpoint verifies.

    Notes:
        The password is checked against the project's own policy before
        anything is written, exactly like ``create``. Every dotted spec
        is resolved before the prompt, so a typo in ``--model`` costs one
        error message rather than a password typed twice into a command
        that was never going to run.

        Changing the hash does not, by itself, end a session — an access
        token already issued stays valid until it expires, and a
        DB-backed refresh token can still be exchanged for a fresh one.
        So the user's refresh tokens are revoked **by default**, in the
        same transaction that writes the hash: the command resolves
        ``--refresh-token-model``, falling back to
        ``src.db.models:UserRefreshTokenModel``. Every path says which
        of the two happened, so a run that left sessions alive is never
        silent about it.

    Raises:
        typer.Exit: With code 1 when no user matches the email, or code
            2 when the password is rejected, the two flags contradict
            each other, or a dotted spec cannot be resolved.
    """
    if keep_sessions and refresh_token_model:
        typer.echo(
            "error: --keep-sessions and --refresh-token-model contradict "
            "each other. Pass one or neither.",
            err=True,
        )
        raise typer.Exit(2)
    database_url = _resolve_database_url()
    user_model = _load_user_model(model)
    token_model: type[BaseUserRefreshTokenModel] | None = None
    if not keep_sessions:
        token_model = (
            _load_refresh_token_model(refresh_token_model)
            if refresh_token_model
            else _find_default_refresh_token_model()
        )
    password = _read_password(password)
    outcome = asyncio.run(
        _set_user_password(
            database_url,
            user_model,
            email=email,
            password=password,
            refresh_token_model=token_model,
        )
    )
    if outcome is None:
        typer.echo(f"error: no user found with email {email!r}.", err=True)
        raise typer.Exit(1)
    user_id, revoked = outcome
    typer.echo(f"Password updated for {email.lower()} (id={user_id})")
    if token_model is not None:
        typer.echo(f"Revoked {revoked} active refresh token(s).")
        return
    if keep_sessions:
        typer.echo(
            "note: --keep-sessions given, so every refresh token the user "
            "holds is still exchangeable.",
            err=True,
        )
        return
    typer.echo(
        f"note: no refresh-token model at {_DEFAULT_REFRESH_TOKEN_MODEL!r}, "
        "so no session was revoked. Pass --refresh-token-model if yours "
        "lives elsewhere.",
        err=True,
    )


def _resolve_refresh_token_model(
    spec: str,
) -> type[BaseUserRefreshTokenModel] | None:
    """Resolve the refresh-token model from a spec or the convention.

    Args:
        spec (str): The ``--refresh-token-model`` value, empty when the
            operator did not type one.

    Returns:
        type[BaseUserRefreshTokenModel] | None: The model, or ``None``
        when the project has none at the conventional path.

    Raises:
        typer.Exit: Code 2 when a typed spec does not resolve — a spec
            the operator wrote is a promise, not a guess.
    """
    if spec:
        return _load_refresh_token_model(spec)
    return _find_default_refresh_token_model()


@user_app.command("show")
def user_show(
    email: str = typer.Option(
        ...,
        "--email",
        "-e",
        help="Email of the user to print.",
    ),
    model: str = typer.Option(
        "src.db.models:UserModel",
        "--model",
        help="Dotted spec for the concrete UserModel.",
    ),
    as_json: bool = typer.Option(
        False,
        "--json",
        help="Print the row as a JSON object instead of aligned pairs.",
    ),
) -> None:
    """Print every mapped column of one user, found by email.

    ``list`` answers "who exists"; this answers "what does this row
    actually hold", including the columns a concrete ``UserModel``
    added. ``hashed_password`` is redacted — printing it puts a
    credential in the terminal scrollback and in CI logs, and no
    question this command answers needs it.

    Raises:
        typer.Exit: Code 1 when no user matches the email.
    """
    database_url = _resolve_database_url()
    user_model = _load_user_model(model)
    row = asyncio.run(_fetch_user(database_url, user_model, email=email))
    if row is None:
        typer.echo(f"error: no user found with email {email!r}.", err=True)
        raise typer.Exit(1)

    printable = {
        name: "(redacted)" if name == "hashed_password" else value
        for name, value in row.items()
    }
    if as_json:
        typer.echo(json.dumps(printable, indent=2, default=str))
        return
    width = max(len(name) for name in printable)
    for name, value in printable.items():
        typer.echo(f"{name.ljust(width)}  {value}")


def _run_set_active(
    email: str,
    model: str,
    refresh_token_model_spec: str,
    *,
    is_active: bool,
) -> None:
    """Resolve resources, flip ``is_active`` and report the outcome.

    Args:
        email (str): Email of the user to update.
        model (str): Dotted spec for the concrete UserModel.
        refresh_token_model_spec (str): Dotted spec for the
            refresh-token model, empty to use the convention.
        is_active (bool): The new ``is_active`` value.

    Raises:
        typer.Exit: Code 1 when no user matches the email.
    """
    database_url = _resolve_database_url()
    user_model = _load_user_model(model)
    token_model = (
        None
        if is_active
        else _resolve_refresh_token_model(
            refresh_token_model_spec,
        )
    )
    outcome = asyncio.run(
        _set_user_active(
            database_url,
            user_model,
            email=email,
            is_active=is_active,
            refresh_token_model=token_model,
        )
    )
    if outcome is None:
        typer.echo(f"error: no user found with email {email!r}.", err=True)
        raise typer.Exit(1)
    user_id, revoked = outcome
    verb = "Activated" if is_active else "Deactivated"
    typer.echo(f"{verb} {email.lower()} (id={user_id})")
    if is_active:
        return
    if token_model is None:
        typer.echo(
            f"note: no refresh-token model at '{_DEFAULT_REFRESH_TOKEN_MODEL}', "
            "so no session was revoked. Pass --refresh-token-model if yours "
            "lives elsewhere.",
            err=True,
        )
        return
    typer.echo(f"Revoked {revoked} active refresh token(s).")


@user_app.command("activate")
def user_activate(
    email: str = typer.Option(
        ...,
        "--email",
        "-e",
        help="Email of the user to re-enable.",
    ),
    model: str = typer.Option(
        "src.db.models:UserModel",
        "--model",
        help="Dotted spec for the concrete UserModel.",
    ),
) -> None:
    """Set ``is_active=True`` for an existing user."""
    _run_set_active(email, model, "", is_active=True)


@user_app.command("deactivate")
def user_deactivate(
    email: str = typer.Option(
        ...,
        "--email",
        "-e",
        help="Email of the user to disable.",
    ),
    model: str = typer.Option(
        "src.db.models:UserModel",
        "--model",
        help="Dotted spec for the concrete UserModel.",
    ),
    refresh_token_model: str = typer.Option(
        "",
        "--refresh-token-model",
        help=(
            "Dotted spec for the refresh-token table. Defaults to "
            "'src.db.models:UserRefreshTokenModel' when it exists."
        ),
    ),
) -> None:
    """Set ``is_active=False`` and revoke the user's sessions.

    Deactivating without revoking leaves a stolen refresh token
    exchangeable against an account the operator just turned off, so the
    revocation runs in the same transaction rather than behind a flag.
    """
    _run_set_active(email, model, refresh_token_model, is_active=False)


@user_app.command("delete")
def user_delete(
    email: str = typer.Option(
        ...,
        "--email",
        "-e",
        help="Email of the user to delete.",
    ),
    yes: bool = typer.Option(
        False,
        "--yes",
        "-y",
        help="Confirm the deletion. Required outside an interactive terminal.",
    ),
    model: str = typer.Option(
        "src.db.models:UserModel",
        "--model",
        help="Dotted spec for the concrete UserModel.",
    ),
    refresh_token_model: str = typer.Option(
        "",
        "--refresh-token-model",
        help=(
            "Dotted spec for the refresh-token table whose rows are removed "
            "with the user. Defaults to the scaffolded one when it exists."
        ),
    ),
) -> None:
    """Delete one user row, found by email.

    This is irreversible and takes the user's refresh tokens with it.
    ``tempest user deactivate`` is the reversible answer to "this person
    should not be able to log in", and is usually the one you want.

    Raises:
        typer.Exit: Code 1 when no user matches the email or the
            deletion is not confirmed; code 2 when a typed
            ``--refresh-token-model`` does not resolve.
    """
    if not yes:
        if not _stdin_is_interactive():
            typer.echo(
                "error: deleting a user is irreversible. Pass --yes to confirm.",
                err=True,
            )
            raise typer.Exit(1)
        if not typer.confirm(f"Delete {email.lower()} permanently?"):
            typer.echo("Aborted.", err=True)
            raise typer.Exit(1)

    database_url = _resolve_database_url()
    user_model = _load_user_model(model)
    token_model = _resolve_refresh_token_model(refresh_token_model)
    user_id = asyncio.run(
        _delete_user(
            database_url,
            user_model,
            email=email,
            refresh_token_model=token_model,
        )
    )
    if user_id is None:
        typer.echo(f"error: no user found with email {email!r}.", err=True)
        raise typer.Exit(1)
    typer.echo(f"Deleted {email.lower()} (id={user_id})")


@user_app.command("sessions")
def user_sessions(
    email: str = typer.Option(
        ...,
        "--email",
        "-e",
        help="Email of the user whose sessions to inspect.",
    ),
    revoke: bool = typer.Option(
        False,
        "--revoke",
        help="Revoke every active refresh token instead of listing them.",
    ),
    model: str = typer.Option(
        "src.db.models:UserModel",
        "--model",
        help="Dotted spec for the concrete UserModel.",
    ),
    refresh_token_model: str = typer.Option(
        "",
        "--refresh-token-model",
        help=(
            "Dotted spec for the refresh-token table. Defaults to "
            "'src.db.models:UserRefreshTokenModel'."
        ),
    ),
) -> None:
    """List (or revoke) the DB-backed sessions of one user.

    A user with no session prints an empty table and exits 0 — "this
    account is not logged in anywhere" is an answer, not a failure.

    Raises:
        typer.Exit: Code 1 when no user matches the email; code 2 when
            the project exposes no refresh-token table, since there is
            then nothing this command could be reporting on.
    """
    database_url = _resolve_database_url()
    user_model = _load_user_model(model)
    token_model = _resolve_refresh_token_model(refresh_token_model)
    if token_model is None:
        typer.echo(
            f"error: no refresh-token model at '{_DEFAULT_REFRESH_TOKEN_MODEL}'. "
            "Pass --refresh-token-model if yours lives elsewhere.",
            err=True,
        )
        raise typer.Exit(2)

    if revoke:
        revoked = asyncio.run(
            _revoke_sessions(database_url, user_model, token_model, email=email)
        )
        if revoked is None:
            typer.echo(f"error: no user found with email {email!r}.", err=True)
            raise typer.Exit(1)
        typer.echo(f"Revoked {revoked} active refresh token(s).")
        return

    rows = asyncio.run(
        _list_user_sessions(database_url, user_model, token_model, email=email)
    )
    if rows is None:
        typer.echo(f"error: no user found with email {email!r}.", err=True)
        raise typer.Exit(1)
    typer.echo("ID  FAMILY  CREATED  EXPIRES  STATE")
    for row in rows:
        state = "revoked" if row["revoked_at"] else "active"
        typer.echo(
            f"{row['id']}  {row['family_id']}  {row['created_at']}  "
            f"{row['expires_at']}  {state}"
        )


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
