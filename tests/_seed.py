"""Seed parent rows that the SDK's foreign keys require.

The SQLite engines the SDK builds enforce foreign keys (#395), so a test
that stores a ``user_id`` must point it at a user that exists — a bare
``uuid4()`` is an orphan the database refuses, as PostgreSQL would.
"""

from itertools import count
from uuid import UUID

from sqlalchemy.ext.asyncio import AsyncSession

from tempest_fastapi_sdk import BaseUserModel

_EMAILS = count()
"""Process-wide counter keeping seeded e-mails unique across calls."""


async def seed_users(
    session: AsyncSession,
    model: type[BaseUserModel],
    how_many: int,
) -> list[UUID]:
    """Persist users and return their ids.

    Args:
        session (AsyncSession): The test's session; the rows are flushed,
            not committed, so they share the test's transaction.
        model (type[BaseUserModel]): The concrete user model the foreign
            keys under test reference.
        how_many (int): How many users to create.

    Returns:
        list[UUID]: The new users' ids, in creation order.
    """
    users = [
        model(email=f"seed{next(_EMAILS)}@example.com", hashed_password="x")
        for _ in range(how_many)
    ]
    session.add_all(users)
    await session.flush()
    return [user.id for user in users]
