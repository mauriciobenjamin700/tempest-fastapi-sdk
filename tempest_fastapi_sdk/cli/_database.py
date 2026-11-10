"""Database plumbing shared by the CLI commands.

Every :class:`~tempest_fastapi_sdk.db.connection.AsyncDatabaseManager` the
CLI opens goes through :func:`cli_database_manager`, so the one policy that
matters in a terminal -- never echo bound parameters -- lives in a single
place. ``tests/test_cli_hide_parameters_guard.py`` fails on a manager built
anywhere else under ``tempest_fastapi_sdk/cli/``.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from sqlalchemy import Table

    from tempest_fastapi_sdk.db.connection import AsyncDatabaseManager


def cli_database_manager(database_url: str) -> AsyncDatabaseManager:
    """Build the manager a CLI command talks to the database through.

    The engine is created with ``hide_parameters=True``. A command such as
    ``tempest user create`` binds a bcrypt hash; without the flag a failed
    statement renders ``[parameters: (..., '$2b$12$...', ...)]`` into the
    ``DBAPIError`` message, and that message reaches the terminal and any
    CI log capturing it. With it, SQLAlchemy prints
    ``[SQL parameters hidden due to hide_parameters=True]`` instead.

    Args:
        database_url (str): The resolved database URL.

    Returns:
        AsyncDatabaseManager: A manager that is not connected yet.
    """
    from tempest_fastapi_sdk.db.connection import AsyncDatabaseManager

    return AsyncDatabaseManager(database_url, hide_parameters=True)


async def missing_tables(
    manager: AsyncDatabaseManager,
    tables: list[Table],
) -> list[str]:
    """Return the names of ``tables`` the connected database does not have.

    Used before a write so a database that was never migrated is reported
    as such, instead of surfacing the driver's ``no such table`` /
    ``relation ... does not exist`` error.

    Args:
        manager (AsyncDatabaseManager): A connected manager.
        tables (list[Table]): The tables the command is about to touch.

    Returns:
        list[str]: Qualified names (``schema.name`` when the table has a
        schema) of the tables that are absent, in the order given. Empty
        when every table exists.
    """
    from sqlalchemy import inspect
    from sqlalchemy.engine import Connection

    def _absent(connection: Connection) -> list[str]:
        """Inspect ``connection`` for each table.

        Args:
            connection (Connection): The sync connection ``run_sync`` hands
                over.

        Returns:
            list[str]: The qualified names of the missing tables.
        """
        inspector = inspect(connection)
        return [
            table.fullname
            for table in tables
            if not inspector.has_table(table.name, schema=table.schema)
        ]

    async with manager.engine.connect() as connection:
        return await connection.run_sync(_absent)
