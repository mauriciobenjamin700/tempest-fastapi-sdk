"""``AdminModel(can_duplicate=True)``: the create form filled with a copy."""

from __future__ import annotations

import re
from collections.abc import AsyncIterator
from typing import Any

import pytest
from pydantic import BaseModel as Schema
from sqlalchemy import Index, String, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from tempest_fastapi_sdk import AdminModel, AsyncDatabaseManager, BaseModel
from tempest_fastapi_sdk.admin import AdminCreateHandler, AdminPermissionEnum
from tempest_fastapi_sdk.admin.forms import unique_field_names
from tests.admin.wave2_world import (
    ARTICLES,
    OWN,
    USERS,
    W2Article,
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


def _input_value(html: str, name: str) -> str | None:
    """Return the ``value`` of the text input called ``name``.

    Args:
        html (str): The form page.
        name (str): The input name.

    Returns:
        str | None: The value, or ``None`` when no such input exists.
    """
    match = re.search(rf'<input type="[a-z-]+" name="{name}" value="([^"]*)"', html)
    return None if match is None else match.group(1)


def test_construction_needs_the_column_create_form() -> None:
    with pytest.raises(ValueError, match="can_duplicate"):
        AdminModel(W2Article, can_duplicate=True, can_create=False)

    async def handler(data: Any, ctx: Any) -> None:
        """Stand in for a create handler.

        Args:
            data (Any): The schema instance.
            ctx (Any): The context.
        """

    class _Input(Schema):
        """Handler input."""

        title: str

    with pytest.raises(ValueError, match="can_duplicate"):
        AdminModel(
            W2Article,
            can_duplicate=True,
            create_handler=AdminCreateHandler(schema=_Input, handler=handler),
        )


@pytest.mark.asyncio
async def test_form_copies_values_but_not_identity_unique_or_audit(
    world: Wave2World,
) -> None:
    detail = await world.client.get(f"{ARTICLES}/{world.ids['article_in']}")
    duplicate_url = f"{ARTICLES}/{world.ids['article_in']}/duplicate"
    assert f'href="{duplicate_url}"' in detail.text
    page = await world.client.get(duplicate_url)
    assert page.status_code == 200
    assert "Duplicate W2 Article" in page.text
    assert _input_value(page.text, "title") == "ArticleInside"
    assert _input_value(page.text, "company") == OWN
    assert _input_value(page.text, "slug") == ""
    assert f'action="{ARTICLES}/new"' in page.text
    assert str(world.ids["operator"]) not in page.text
    assert f'value="{world.ids["tag_in"]}" selected' in page.text


@pytest.mark.asyncio
async def test_password_and_unique_email_stay_blank(world: Wave2World) -> None:
    page = await world.client.get(f"{USERS}/{world.ids['operator']}/duplicate")
    assert page.status_code == 200
    assert re.search(r'<textarea name="email" rows="4" required></textarea>', page.text)
    assert "op@example.com</textarea>" not in page.text
    assert 'name="hashed_password" value=""' in page.text
    assert _input_value(page.text, "company") == OWN


@pytest.mark.asyncio
async def test_source_outside_the_scope_is_not_found(world: Wave2World) -> None:
    out = await world.client.get(f"{ARTICLES}/{world.ids['article_out']}/duplicate")
    assert out.status_code == 404
    assert "ArticleOutside" not in out.text


@pytest.mark.asyncio
async def test_submission_is_an_ordinary_create(world: Wave2World) -> None:
    duplicated = await world.client.post(
        f"{ARTICLES}/new",
        data={
            "csrf_token": world.csrf,
            "title": "ArticleInside",
            "slug": "inside",
            "company": OWN,
            "status": "draft",
        },
    )
    assert duplicated.status_code == 400
    copied = await world.client.post(
        f"{ARTICLES}/new",
        data={
            "csrf_token": world.csrf,
            "title": "ArticleInside",
            "slug": "inside-copy",
            "company": "globex",
            "status": "draft",
        },
    )
    assert copied.status_code == 400
    assert "outside the rows you may manage" in copied.text


@pytest.mark.asyncio
async def test_needs_create_permission(database: AsyncDatabaseManager) -> None:
    def no_create(user: Any, admin: AdminModel[Any], action: Any) -> bool:
        """Deny ``CREATE`` everywhere.

        Args:
            user (Any): The principal.
            admin (AdminModel[Any]): The admin asked about.
            action (Any): The permission.

        Returns:
            bool: ``False`` for ``CREATE``.
        """
        return action is not AdminPermissionEnum.CREATE

    async for world in open_world(database, access_policy=no_create):
        detail = await world.client.get(f"{ARTICLES}/{world.ids['article_in']}")
        assert "/duplicate" not in detail.text
        page = await world.client.get(f"{ARTICLES}/{world.ids['article_in']}/duplicate")
        assert page.status_code == 403


@pytest.mark.asyncio
async def test_disabled_admin_answers_not_found(
    database: AsyncDatabaseManager,
) -> None:
    async for world in open_world(database, article_options={"can_duplicate": False}):
        detail = await world.client.get(f"{ARTICLES}/{world.ids['article_in']}")
        assert "/duplicate" not in detail.text
        page = await world.client.get(f"{ARTICLES}/{world.ids['article_in']}/duplicate")
        assert page.status_code == 404


class _Coded(BaseModel):
    """A model with every kind of uniqueness."""

    __tablename__ = "admin_w2_coded"
    __table_args__ = (
        UniqueConstraint("tenant", "code"),
        Index("ix_admin_w2_coded_ref", "ref", unique=True),
        Index("ix_admin_w2_coded_note", "note"),
    )
    tenant: Mapped[str] = mapped_column(String(8))
    code: Mapped[str] = mapped_column(String(8))
    ref: Mapped[str] = mapped_column(String(8))
    serial: Mapped[str] = mapped_column(String(8), unique=True)
    note: Mapped[str] = mapped_column(String(8))


def test_unique_columns_include_constraints_and_unique_indexes() -> None:
    names = unique_field_names(AdminModel(_Coded))
    assert {"id", "tenant", "code", "ref", "serial"} <= names
    assert "note" not in names
