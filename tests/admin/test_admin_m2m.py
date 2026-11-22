"""``AdminModel(m2m_fields=...)``: a multi-select of a ``secondary`` relationship."""

from __future__ import annotations

from collections.abc import AsyncIterator
from typing import Any

import pytest
from sqlalchemy import ForeignKey, String, func, select
from sqlalchemy.orm import Mapped, mapped_column, relationship, selectinload

from tempest_fastapi_sdk import AdminModel, AsyncDatabaseManager, BaseModel
from tests.admin.wave2_world import (
    ARTICLES,
    OWN,
    W2Article,
    W2Tag,
    Wave2World,
    article_tags,
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


def _tag_ids(article: W2Article | None) -> set[str]:
    """Return the ids of an article's tags.

    Args:
        article (W2Article | None): The reloaded article.

    Returns:
        set[str]: The tag ids.
    """
    assert article is not None
    return {str(tag.id) for tag in article.tags}


class _Thing(BaseModel):
    """A model with a one-to-many relationship, refused by ``m2m_fields``."""

    __tablename__ = "admin_w2_things"
    name: Mapped[str] = mapped_column(String(32), nullable=False)
    parts: Mapped[list[_Part]] = relationship(back_populates="thing")


class _Part(BaseModel):
    """Child of :class:`_Thing`."""

    __tablename__ = "admin_w2_parts"
    thing_id: Mapped[Any] = mapped_column(ForeignKey("admin_w2_things.id"))
    thing: Mapped[_Thing] = relationship(back_populates="parts")


def test_construction_refuses_a_name_that_is_not_a_secondary_relationship() -> None:
    with pytest.raises(ValueError, match="not a relationship with `secondary`"):
        AdminModel(_Thing, m2m_fields=[_Thing.parts])
    with pytest.raises(ValueError, match="not a relationship with `secondary`"):
        AdminModel(_Thing, m2m_fields=["name"])
    assert AdminModel(W2Article, m2m_fields=[W2Article.tags]).m2m_fields == ["tags"]


@pytest.mark.asyncio
async def test_form_offers_only_reachable_targets(world: Wave2World) -> None:
    page = await world.client.get(f"{ARTICLES}/{world.ids['article_in']}/edit")
    assert page.status_code == 200
    assert 'name="tags" multiple' in page.text
    assert "TagInside" in page.text
    assert "TagOutside" not in page.text
    assert str(world.ids["tag_out"]) not in page.text
    selected = f'value="{world.ids["tag_in"]}" selected'
    assert selected in page.text


@pytest.mark.asyncio
async def test_unscoped_form_offers_every_target(unscoped: Wave2World) -> None:
    page = await unscoped.client.get(f"{ARTICLES}/{unscoped.ids['article_in']}/edit")
    assert "TagOutside" in page.text


@pytest.mark.asyncio
async def test_edit_replaces_the_set_and_keeps_the_hidden_link(
    world: Wave2World,
) -> None:
    resp = await world.client.post(
        f"{ARTICLES}/{world.ids['article_in']}/edit",
        data={
            "csrf_token": world.csrf,
            "title": "ArticleInside",
            "slug": "inside",
            "company": OWN,
            "status": "draft",
        },
    )
    assert resp.status_code == 303
    assert _tag_ids(await world.article("article_in")) == {str(world.ids["tag_out"])}


@pytest.mark.asyncio
async def test_unscoped_edit_clears_every_link(unscoped: Wave2World) -> None:
    resp = await unscoped.client.post(
        f"{ARTICLES}/{unscoped.ids['article_in']}/edit",
        data={
            "csrf_token": unscoped.csrf,
            "title": "ArticleInside",
            "slug": "inside",
            "company": OWN,
            "status": "draft",
        },
    )
    assert resp.status_code == 303
    assert _tag_ids(await unscoped.article("article_in")) == set()


@pytest.mark.asyncio
async def test_forged_foreign_id_is_refused_as_missing(world: Wave2World) -> None:
    resp = await world.client.post(
        f"{ARTICLES}/{world.ids['article_out']}/edit",
        data={"csrf_token": world.csrf},
    )
    assert resp.status_code == 404
    created = await world.client.post(
        f"{ARTICLES}/new",
        data={
            "csrf_token": world.csrf,
            "title": "New",
            "slug": "new",
            "company": OWN,
            "status": "draft",
            "tags": [str(world.ids["tag_out"])],
        },
    )
    assert created.status_code == 400
    assert "that record does not exist" in created.text
    garbage = await world.client.post(
        f"{ARTICLES}/new",
        data={
            "csrf_token": world.csrf,
            "title": "New",
            "slug": "new",
            "company": OWN,
            "status": "draft",
            "tags": ["not-a-uuid"],
        },
    )
    assert garbage.status_code == 400
    assert "that record does not exist" in garbage.text


@pytest.mark.asyncio
async def test_posting_a_hidden_current_id_is_the_no_op_it_is(
    world: Wave2World,
) -> None:
    resp = await world.client.post(
        f"{ARTICLES}/{world.ids['article_in']}/edit",
        data={
            "csrf_token": world.csrf,
            "title": "ArticleInside",
            "slug": "inside",
            "company": OWN,
            "status": "draft",
            "tags": [str(world.ids["tag_in"]), str(world.ids["tag_out"])],
        },
    )
    assert resp.status_code == 303
    assert _tag_ids(await world.article("article_in")) == {
        str(world.ids["tag_in"]),
        str(world.ids["tag_out"]),
    }


@pytest.mark.asyncio
async def test_create_stores_the_links_in_the_same_transaction(
    world: Wave2World,
) -> None:
    resp = await world.client.post(
        f"{ARTICLES}/new",
        data={
            "csrf_token": world.csrf,
            "title": "Fresh",
            "slug": "fresh",
            "company": OWN,
            "status": "draft",
            "tags": [str(world.ids["tag_in"])],
        },
    )
    assert resp.status_code == 303
    new_id = resp.headers["location"].rsplit("/", 1)[1]
    detail = await world.client.get(f"{ARTICLES}/{new_id}")
    assert "TagInside" in detail.text


@pytest.mark.asyncio
async def test_refused_write_stores_no_link(world: Wave2World) -> None:
    resp = await world.client.post(
        f"{ARTICLES}/new",
        data={
            "csrf_token": world.csrf,
            "title": "Elsewhere",
            "slug": "elsewhere",
            "company": "globex",
            "status": "draft",
            "tags": [str(world.ids["tag_in"])],
        },
    )
    assert resp.status_code == 400
    assert "outside the rows you may manage" in resp.text
    async with world.db.get_session_context() as session:
        links = await session.scalar(select(func.count()).select_from(article_tags))
    assert links == 2


@pytest.mark.asyncio
async def test_detail_lists_only_reachable_links(world: Wave2World) -> None:
    page = await world.client.get(f"{ARTICLES}/{world.ids['article_in']}")
    assert page.status_code == 200
    assert f"/admin/m/{W2Tag.__tablename__}/{world.ids['tag_in']}" in page.text
    assert "TagOutside" not in page.text


@pytest.mark.asyncio
async def test_target_without_view_is_left_out_and_kept(
    database: AsyncDatabaseManager,
) -> None:
    def no_tags(user: Any, admin: AdminModel[Any], action: Any) -> bool:
        """Deny every action on the tags.

        Args:
            user (Any): The principal.
            admin (AdminModel[Any]): The admin asked about.
            action (Any): The permission.

        Returns:
            bool: ``False`` for the tags.
        """
        return admin.model is not W2Tag

    async for world in open_world(database, access_policy=no_tags):
        form = await world.client.get(f"{ARTICLES}/{world.ids['article_in']}/edit")
        assert 'name="tags"' not in form.text
        detail = await world.client.get(f"{ARTICLES}/{world.ids['article_in']}")
        assert "TagInside" not in detail.text
        resp = await world.client.post(
            f"{ARTICLES}/{world.ids['article_in']}/edit",
            data={
                "csrf_token": world.csrf,
                "title": "Renamed",
                "slug": "inside",
                "company": OWN,
                "status": "draft",
            },
        )
        assert resp.status_code == 303
        assert len(_tag_ids(await world.article("article_in"))) == 2


@pytest.mark.asyncio
async def test_link_beyond_the_option_cap_is_shown_and_kept(
    database: AsyncDatabaseManager, monkeypatch: pytest.MonkeyPatch
) -> None:
    from tempest_fastapi_sdk.admin import router as admin_router

    async for world in open_world(database):
        async with world.db.get_session_context() as session:
            session.add(W2Tag(name="AAAEarlier", company=OWN))
            await session.commit()
        async with world.db.get_session_context() as session:
            first = await session.get(W2Tag, world.ids["tag_in"])
            assert first is not None
            first.created_at = first.created_at.replace(year=2100)
            await session.commit()
        monkeypatch.setattr(admin_router, "FK_OPTION_CAP", 1)
        form = await world.client.get(f"{ARTICLES}/{world.ids['article_in']}/edit")
        assert "AAAEarlier" in form.text
        assert f'value="{world.ids["tag_in"]}" selected' in form.text
        resp = await world.client.post(
            f"{ARTICLES}/{world.ids['article_in']}/edit",
            data={
                "csrf_token": world.csrf,
                "title": "ArticleInside",
                "slug": "inside",
                "company": OWN,
                "status": "draft",
                "tags": [str(world.ids["tag_in"])],
            },
        )
        assert resp.status_code == 303
        assert _tag_ids(await world.article("article_in")) == {
            str(world.ids["tag_in"]),
            str(world.ids["tag_out"]),
        }


@pytest.mark.asyncio
async def test_field_locked_for_the_row_is_left_out_and_unchanged(
    database: AsyncDatabaseManager,
) -> None:
    def lock_tags(row: Any) -> list[str]:
        """Lock the tags of every row.

        Args:
            row (Any): The stored row.

        Returns:
            list[str]: The locked fields.
        """
        return ["tags"]

    async for world in open_world(
        database, article_options={"readonly_fields_for": lock_tags}
    ):
        form = await world.client.get(f"{ARTICLES}/{world.ids['article_in']}/edit")
        assert 'name="tags"' not in form.text
        resp = await world.client.post(
            f"{ARTICLES}/{world.ids['article_in']}/edit",
            data={
                "csrf_token": world.csrf,
                "title": "ArticleInside",
                "slug": "inside",
                "company": OWN,
                "status": "draft",
            },
        )
        assert resp.status_code == 303
        assert len(_tag_ids(await world.article("article_in"))) == 2


@pytest.mark.asyncio
async def test_rerender_looks_up_at_most_the_cap_beyond_the_options(
    database: AsyncDatabaseManager, monkeypatch: pytest.MonkeyPatch
) -> None:
    from tempest_fastapi_sdk.admin import router as admin_router

    async for world in open_world(database):
        async with world.db.get_session_context() as session:
            extra = [W2Tag(name=f"Extra{i}", company=OWN) for i in range(3)]
            session.add_all(extra)
            await session.commit()
            extra_ids = [str(tag.id) for tag in extra]
        monkeypatch.setattr(admin_router, "FK_OPTION_CAP", 1)
        resp = await world.client.post(
            f"{ARTICLES}/new",
            data={
                "csrf_token": world.csrf,
                "slug": "x",
                "company": OWN,
                "status": "draft",
                "tags": extra_ids,
            },
        )
        assert resp.status_code == 400
        select_html = resp.text.split('name="tags" multiple', 1)[1].split("</select>")[
            0
        ]
        assert select_html.count("<option") == 2


@pytest.mark.asyncio
async def test_reachable_link_never_rendered_is_kept_on_save(
    database: AsyncDatabaseManager, monkeypatch: pytest.MonkeyPatch
) -> None:
    from tempest_fastapi_sdk.admin import router as admin_router

    async for world in open_world(database):
        async with world.db.get_session_context() as session:
            session.add(W2Tag(name="AAAEarlier", company=OWN))
            later = W2Tag(name="TagInsideLater", company=OWN)
            session.add(later)
            await session.flush()
            later.created_at = later.created_at.replace(year=2101)
            first = await session.get(W2Tag, world.ids["tag_in"])
            assert first is not None
            first.created_at = first.created_at.replace(year=2100)
            article = (
                await session.execute(
                    select(W2Article)
                    .options(selectinload(W2Article.tags))
                    .where(W2Article.id == world.ids["article_in"])
                )
            ).scalar_one()
            article.tags.append(later)
            await session.commit()
            later_id = str(later.id)
        monkeypatch.setattr(admin_router, "FK_OPTION_CAP", 1)
        for _ in range(5):
            form = await world.client.get(f"{ARTICLES}/{world.ids['article_in']}/edit")
            assert f'value="{world.ids["tag_in"]}" selected' in form.text
            assert later_id not in form.text
            assert str(world.ids["tag_out"]) not in form.text
        resp = await world.client.post(
            f"{ARTICLES}/{world.ids['article_in']}/edit",
            data={
                "csrf_token": world.csrf,
                "title": "ArticleInside",
                "slug": "inside",
                "company": OWN,
                "status": "draft",
                "tags": [str(world.ids["tag_in"])],
            },
        )
        assert resp.status_code == 303
        assert _tag_ids(await world.article("article_in")) == {
            str(world.ids["tag_in"]),
            later_id,
            str(world.ids["tag_out"]),
        }


@pytest.mark.asyncio
async def test_restore_form_carries_the_current_links(world: Wave2World) -> None:
    from tempest_fastapi_sdk.db.audit import AuditActionEnum
    from tests.admin.wave2_world import W2Audit

    async with world.db.get_session_context() as session:
        entry = W2Audit.new_entry(
            entity=W2Article.__name__,
            entity_id=str(world.ids["article_in"]),
            action=AuditActionEnum.UPDATE,
            changes={"status": {"before": "published", "after": "draft"}},
            actor=str(world.ids["operator"]),
        )
        session.add(entry)
        await session.commit()
        entry_id = entry.id
    form = await world.client.get(
        f"{ARTICLES}/{world.ids['article_in']}/restore/{entry_id}"
    )
    assert form.status_code == 200
    assert f'value="{world.ids["tag_in"]}" selected' in form.text
    resp = await world.client.post(
        f"{ARTICLES}/{world.ids['article_in']}/edit",
        data={
            "csrf_token": world.csrf,
            "title": "ArticleInside",
            "slug": "inside",
            "company": OWN,
            "status": "published",
            "tags": [str(world.ids["tag_in"])],
        },
    )
    assert resp.status_code == 303
    assert len(_tag_ids(await world.article("article_in"))) == 2
