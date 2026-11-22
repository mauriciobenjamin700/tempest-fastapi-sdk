"""``AdminSite(recent_activity=N)``: the dashboard feed of audited changes."""

from __future__ import annotations

import re
from collections.abc import AsyncIterator
from datetime import UTC, datetime, timedelta
from typing import Any
from uuid import uuid4

import pytest
from sqlalchemy import select

from tempest_fastapi_sdk import AdminModel, AdminSite, AsyncDatabaseManager
from tempest_fastapi_sdk.db.audit import AuditActionEnum
from tests.admin.wave2_world import (
    ARTICLES,
    W2Article,
    W2Audit,
    W2Comment,
    Wave2World,
    new_database,
    open_world,
)


@pytest.fixture
async def database() -> AsyncIterator[AsyncDatabaseManager]:
    """Yield a fresh in-memory database with the tables created.

    Yields:
        AsyncDatabaseManager: The connected manager.
    """
    db = await new_database()
    yield db
    await db.drop_tables()
    await db.disconnect()


async def _record(world: Wave2World) -> None:
    """Write one audit entry per case the feed has to tell apart.

    Newest first: an update of the foreign article by the foreign user, an
    update of the article in scope by the foreign user, a comment created in
    scope, a delete of a row that no longer exists, and the oldest, the
    creation of the article in scope by the operator.

    Args:
        world (Wave2World): The seeded world.
    """
    now = datetime.now(UTC)
    async with world.db.get_session_context() as session:
        comment = (
            await session.execute(
                select(W2Comment).where(W2Comment.body == "CommentInA")
            )
        ).scalar_one()
        rows = [
            (W2Article.__name__, world.ids["article_out"], "stranger", "update", 0),
            (W2Article.__name__, world.ids["article_in"], "stranger", "update", 1),
            (W2Comment.__name__, comment.id, "operator", "create", 2),
            (W2Article.__name__, uuid4(), "operator", "delete", 3),
            (W2Article.__name__, world.ids["article_in"], "operator", "create", 4),
        ]
        for entity, entity_id, actor, action, age in rows:
            entry = W2Audit.new_entry(
                entity=entity,
                entity_id=str(entity_id),
                action=AuditActionEnum(action),
                changes={},
                actor=str(world.ids[actor]),
            )
            entry.created_at = now - timedelta(minutes=age)
            session.add(entry)
        await session.commit()


def _feed(html: str) -> list[str]:
    """Return the text of each feed item, tags stripped.

    Args:
        html (str): The dashboard.

    Returns:
        list[str]: One whitespace-collapsed string per entry.
    """
    items = re.findall(
        r'<li class="tempest-admin-activity__item">(.*?)</li>', html, re.S
    )
    return [" ".join(re.sub(r"<[^>]+>", " ", item).split()) for item in items]


def test_site_refuses_a_non_positive_size() -> None:
    with pytest.raises(ValueError, match="recent_activity"):
        AdminSite(recent_activity=0)
    assert AdminSite().recent_activity is None


@pytest.mark.asyncio
async def test_feed_is_off_by_default(database: AsyncDatabaseManager) -> None:
    async for world in open_world(database):
        await _record(world)
        page = await world.client.get("/admin/")
        assert page.status_code == 200
        assert "Recent activity" not in page.text


@pytest.mark.asyncio
async def test_feed_merges_models_and_hides_rows_outside_the_scope(
    database: AsyncDatabaseManager,
) -> None:
    async for world in open_world(database, recent_activity=10):
        await _record(world)
        page = await world.client.get("/admin/")
        assert "Recent activity" in page.text
        feed = _feed(page.text)
        assert len(feed) == 3
        assert feed[0].startswith("update W2 Article · ArticleInside")
        assert feed[1].startswith("create W2 Comment · CommentInA")
        assert feed[2].startswith("create W2 Article · ArticleInside")
        assert "ArticleOutside" not in page.text
        assert f'href="{ARTICLES}/{world.ids["article_in"]}"' in page.text


@pytest.mark.asyncio
async def test_author_outside_the_user_scope_is_shown_by_id(
    database: AsyncDatabaseManager,
) -> None:
    async for world in open_world(database, recent_activity=10):
        await _record(world)
        feed = _feed((await world.client.get("/admin/")).text)
        assert feed[0].endswith(str(world.ids["stranger"]))
        assert "other@example.com" not in " ".join(feed)
        assert feed[2].endswith("op@example.com")


@pytest.mark.asyncio
async def test_unscoped_feed_shows_every_entry(
    database: AsyncDatabaseManager,
) -> None:
    async for world in open_world(database, scoped=False, recent_activity=3):
        await _record(world)
        feed = _feed((await world.client.get("/admin/")).text)
        assert len(feed) == 3
        assert feed[0].startswith("update W2 Article · ArticleOutside")
        assert "other@example.com" in feed[0]


@pytest.mark.asyncio
async def test_unscoped_entry_of_a_deleted_row_has_no_link(
    database: AsyncDatabaseManager,
) -> None:
    async for world in open_world(database, scoped=False, recent_activity=10):
        await _record(world)
        feed = _feed((await world.client.get("/admin/")).text)
        assert any(
            item.startswith("delete W2 Article · record no longer exists")
            for item in feed
        )


@pytest.mark.asyncio
async def test_model_without_view_is_left_out(
    database: AsyncDatabaseManager,
) -> None:
    def no_comments(user: Any, admin: AdminModel[Any], action: Any) -> bool:
        """Deny every action on the comments.

        Args:
            user (Any): The principal.
            admin (AdminModel[Any]): The admin asked about.
            action (Any): The permission.

        Returns:
            bool: ``False`` for the comments.
        """
        return admin.model is not W2Comment

    async for world in open_world(
        database, recent_activity=10, access_policy=no_comments
    ):
        await _record(world)
        feed = _feed((await world.client.get("/admin/")).text)
        assert [item.split(" · ")[0] for item in feed] == [
            "update W2 Article",
            "create W2 Article",
        ]
