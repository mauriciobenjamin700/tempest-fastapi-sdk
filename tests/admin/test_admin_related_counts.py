"""``AdminModel(related_counts=[RelatedCount(...)])``: child counts on the list."""

from __future__ import annotations

import re
from collections.abc import AsyncIterator
from typing import Any

import pytest
from sqlalchemy import event

from tempest_fastapi_sdk import AdminModel, AsyncDatabaseManager, RelatedCount
from tests.admin.wave2_world import (
    ARTICLES,
    COMMENTS,
    OWN,
    W2Article,
    W2Comment,
    W2Tag,
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


@pytest.fixture
async def world(database: AsyncDatabaseManager) -> AsyncIterator[Wave2World]:
    """Signed-in world with every model scoped by company.

    Yields:
        Wave2World: The world.
    """
    async for opened in open_world(database):
        yield opened


@pytest.fixture
async def unscoped(database: AsyncDatabaseManager) -> AsyncIterator[Wave2World]:
    """The same world with no ``row_scope``.

    Yields:
        Wave2World: The world.
    """
    async for opened in open_world(database, scoped=False):
        yield opened


def _count_cells(html: str) -> list[str]:
    """Return the text of every related-count cell, in page order.

    Args:
        html (str): The list page.

    Returns:
        list[str]: The cells' visible text.
    """
    cells = re.findall(r'<td class="tempest-admin-list__count">(.*?)</td>', html)
    return [re.sub(r"<[^>]+>", "", cell) for cell in cells]


def test_construction_validates_the_column() -> None:
    with pytest.raises(TypeError, match="mapped attribute"):
        RelatedCount("article_id")  # type: ignore[arg-type]
    with pytest.raises(ValueError, match="has no ForeignKey"):
        RelatedCount(W2Comment.body)
    with pytest.raises(ValueError, match="references 'admin_w2_articles'"):
        AdminModel(W2Tag, related_counts=[RelatedCount(W2Comment.article_id)])
    with pytest.raises(ValueError, match="twice"):
        AdminModel(
            W2Article,
            related_counts=[
                RelatedCount(W2Comment.article_id),
                RelatedCount(W2Comment.article_id, label="Again"),
            ],
        )


@pytest.mark.asyncio
async def test_count_covers_only_children_in_the_child_scope(
    world: Wave2World,
) -> None:
    page = await world.client.get(f"{ARTICLES}/")
    assert page.status_code == 200
    assert '<th class="tempest-admin-list__count">Comments</th>' in page.text
    assert _count_cells(page.text) == ["2"]


@pytest.mark.asyncio
async def test_unscoped_count_covers_every_child(
    database: AsyncDatabaseManager,
) -> None:
    async for world in open_world(database, scoped=False):
        page = await world.client.get(f"{ARTICLES}/?sort=title&dir=asc")
        assert _count_cells(page.text) == ["3", "1"]


@pytest.mark.asyncio
async def test_count_links_to_the_child_list_filtered_by_the_parent(
    world: Wave2World,
) -> None:
    page = await world.client.get(f"{ARTICLES}/")
    link = f"{COMMENTS}/?filter_article_id={world.ids['article_in']}"
    assert f'href="{link}"' in page.text
    child = await world.client.get(link)
    assert child.status_code == 200
    assert "CommentInA" in child.text and "CommentInB" in child.text
    assert "CommentOutA" not in child.text and "CommentOutB" not in child.text
    assert "Filtered by Article: ArticleInside" in child.text
    export = await world.client.get(
        f"{COMMENTS}/export.csv?filter_article_id={world.ids['article_in']}"
    )
    assert "CommentInA" in export.text and "CommentInB" in export.text
    assert "CommentOutA" not in export.text and "CommentOutB" not in export.text


@pytest.mark.asyncio
async def test_one_aggregate_query_per_page_whatever_its_size(
    unscoped: Wave2World,
) -> None:
    world = unscoped
    async with world.db.get_session_context() as session:
        for index in range(30):
            article = W2Article(
                title=f"Bulk{index:02d}", slug=f"bulk-{index}", company=OWN
            )
            session.add(article)
            await session.flush()
            session.add(W2Comment(body="x", company=OWN, article_id=article.id))
        await session.commit()
    statements: list[str] = []

    def record(
        conn: Any,
        cursor: Any,
        statement: str,
        parameters: Any,
        context: Any,
        executemany: bool,
    ) -> None:
        """Collect every statement sent to the database.

        Args:
            conn (Any): The connection.
            cursor (Any): The DBAPI cursor.
            statement (str): The SQL.
            parameters (Any): The bound parameters.
            context (Any): The execution context.
            executemany (bool): Whether it is an executemany.
        """
        statements.append(statement)

    engine = world.db.engine.sync_engine
    event.listen(engine, "before_cursor_execute", record)
    try:
        page = await world.client.get(f"{ARTICLES}/")
    finally:
        event.remove(engine, "before_cursor_execute", record)
    assert page.status_code == 200
    assert len(_count_cells(page.text)) == 25
    grouped = [sql for sql in statements if "GROUP BY" in sql.upper()]
    comment_reads = [sql for sql in statements if "admin_w2_comments" in sql]
    assert len(grouped) == 1
    assert comment_reads == grouped


@pytest.mark.asyncio
async def test_child_without_view_drops_the_column(
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

    async for world in open_world(database, access_policy=no_comments):
        page = await world.client.get(f"{ARTICLES}/")
        assert page.status_code == 200
        assert "Comments" not in page.text
        assert _count_cells(page.text) == []


@pytest.mark.asyncio
async def test_unregistered_child_counts_without_a_link(
    database: AsyncDatabaseManager,
) -> None:
    async for world in open_world(database, scoped=False, drop=(W2Comment,)):
        page = await world.client.get(f"{ARTICLES}/?sort=title&dir=asc")
        assert page.status_code == 200
        assert _count_cells(page.text) == ["3", "1"]
        assert "filter_article_id" not in page.text


@pytest.mark.asyncio
async def test_chip_with_a_foreign_parent_shows_only_the_id(
    world: Wave2World,
) -> None:
    child = await world.client.get(
        f"{COMMENTS}/?filter_article_id={world.ids['article_out']}"
    )
    assert child.status_code == 200
    assert f"Filtered by Article: {world.ids['article_out']}" in child.text
    assert "ArticleOutside" not in child.text
    assert "CommentOutB" not in child.text


@pytest.mark.asyncio
async def test_filter_on_a_column_the_child_hides_is_ignored(
    database: AsyncDatabaseManager,
) -> None:
    options = {
        "exclude_fields": ["article_id"],
        "can_create": False,
        "audit_model": None,
    }
    async for world in open_world(database, comment_options=options):
        articles = await world.client.get(f"{ARTICLES}/")
        assert _count_cells(articles.text) == ["2"]
        assert "filter_article_id" not in articles.text
        bogus = "00000000-0000-0000-0000-000000000000"
        child = await world.client.get(f"{COMMENTS}/?filter_article_id={bogus}")
        assert child.status_code == 200
        assert "CommentInA" in child.text and "CommentInB" in child.text
        assert "Filtered by" not in child.text


@pytest.mark.asyncio
async def test_counts_render_without_an_exact_row_count(
    database: AsyncDatabaseManager,
) -> None:
    async for world in open_world(database, article_options={"count_strategy": "none"}):
        page = await world.client.get(f"{ARTICLES}/")
        assert page.status_code == 200
        assert _count_cells(page.text) == ["2"]
