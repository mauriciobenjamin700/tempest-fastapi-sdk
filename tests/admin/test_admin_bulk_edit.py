"""``AdminModel(bulk_edit_fields=...)``: set one column on every selected row."""

from __future__ import annotations

import re
from collections.abc import AsyncIterator
from typing import Any

import pytest

from tempest_fastapi_sdk import AdminModel, AsyncDatabaseManager
from tempest_fastapi_sdk.admin import AdminPermissionEnum
from tests.admin.wave2_world import (
    ARTICLES,
    COMMENTS,
    OTHER,
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


def _nonce(html: str) -> str:
    """Return the single-use nonce of a form page.

    Args:
        html (str): The page.

    Returns:
        str: The nonce.
    """
    match = re.search(r'name="submission_nonce" value="([^"]+)"', html)
    assert match is not None
    return match.group(1)


async def _apply(
    world: Wave2World, field: str, value: str, ids: list[str]
) -> tuple[Any, Any]:
    """Walk the three steps of a bulk edit.

    Args:
        world (Wave2World): The signed-in world.
        field (str): The column to set.
        value (str): The value typed into its widget.
        ids (list[str]): The selected ids.

    Returns:
        tuple[Any, Any]: The value step page and the final response.
    """
    base = {"csrf_token": world.csrf, "action": "bulk_edit", "ids": ids}
    step = await world.client.post(
        f"{ARTICLES}/bulk", data={**base, "bulk_field": field}
    )
    final = await world.client.post(
        f"{ARTICLES}/bulk",
        data={
            **base,
            "bulk_field": field,
            "bulk_input": "1",
            field: value,
            "submission_nonce": _nonce(step.text),
        },
    )
    return step, final


def test_construction_refuses_a_field_the_edit_form_does_not_take() -> None:
    with pytest.raises(ValueError, match="bulk_edit_fields"):
        AdminModel(W2Article, bulk_edit_fields=["id"])
    with pytest.raises(ValueError, match="bulk_edit_fields"):
        AdminModel(W2Article, bulk_edit_fields=["slug"], readonly_fields=["slug"])
    with pytest.raises(ValueError, match="can_edit"):
        AdminModel(W2Article, bulk_edit_fields=["status"], can_edit=False)


@pytest.mark.asyncio
async def test_menu_offers_edit_field_and_asks_which_column(
    world: Wave2World,
) -> None:
    page = await world.client.get(f"{ARTICLES}/")
    assert 'value="bulk_edit"' in page.text
    step = await world.client.post(
        f"{ARTICLES}/bulk",
        data={
            "csrf_token": world.csrf,
            "action": "bulk_edit",
            "ids": [str(world.ids["article_in"])],
        },
    )
    assert step.status_code == 200
    assert 'name="bulk_field"' in step.text
    assert 'value="status"' in step.text and 'value="company"' in step.text


@pytest.mark.asyncio
async def test_value_step_renders_the_column_widget(world: Wave2World) -> None:
    step, _final = await _apply(
        world, "status", "published", [str(world.ids["article_in"])]
    )
    assert '<select name="status"' in step.text
    assert 'value="archived"' in step.text


@pytest.mark.asyncio
async def test_applies_to_selected_rows_in_scope_and_audits_each(
    world: Wave2World,
) -> None:
    _step, final = await _apply(
        world,
        "status",
        "published",
        [str(world.ids["article_in"]), str(world.ids["article_out"])],
    )
    assert final.status_code == 303
    assert "flash=Set+Status+on+1+row" in final.headers["location"]
    inside = await world.article("article_in")
    outside = await world.article("article_out")
    assert inside is not None and inside.status == "published"
    assert inside.updated_by == world.ids["operator"]
    assert outside is not None and outside.status == "draft"
    entries = await world.audit_entries()
    assert len(entries) == 1
    assert entries[0].entity_id == str(world.ids["article_in"])
    assert entries[0].changes["status"] == {"before": "draft", "after": "published"}
    assert entries[0].actor == str(world.ids["operator"])


@pytest.mark.asyncio
async def test_value_moving_rows_out_of_scope_is_rolled_back(
    world: Wave2World,
) -> None:
    _step, final = await _apply(world, "company", OTHER, [str(world.ids["article_in"])])
    assert final.status_code == 303
    assert "outside+the+rows+you+may+manage" in final.headers["location"]
    inside = await world.article("article_in")
    assert inside is not None and inside.company == OWN
    assert await world.audit_entries() == []


@pytest.mark.asyncio
async def test_unscoped_value_moves_the_row(database: AsyncDatabaseManager) -> None:
    async for world in open_world(database, scoped=False):
        _step, final = await _apply(
            world, "company", OTHER, [str(world.ids["article_in"])]
        )
        assert final.status_code == 303
        inside = await world.article("article_in")
        assert inside is not None and inside.company == OTHER


@pytest.mark.asyncio
async def test_invalid_value_is_refused_like_the_edit_form(
    world: Wave2World,
) -> None:
    _step, final = await _apply(
        world, "status", "bogus", [str(world.ids["article_in"])]
    )
    assert final.status_code == 400
    assert "Choose one of the listed values for Status." in final.text
    inside = await world.article("article_in")
    assert inside is not None and inside.status == "draft"


@pytest.mark.asyncio
async def test_nonce_is_single_use(world: Wave2World) -> None:
    base = {
        "csrf_token": world.csrf,
        "action": "bulk_edit",
        "ids": [str(world.ids["article_in"])],
        "bulk_field": "status",
    }
    step = await world.client.post(f"{ARTICLES}/bulk", data=base)
    payload = {
        **base,
        "bulk_input": "1",
        "status": "archived",
        "submission_nonce": _nonce(step.text),
    }
    first = await world.client.post(f"{ARTICLES}/bulk", data=payload)
    assert first.status_code == 303
    again = await world.client.post(f"{ARTICLES}/bulk", data=payload)
    assert again.status_code == 303
    assert "already" in again.headers["location"]
    assert len(await world.audit_entries()) == 1
    missing = await world.client.post(
        f"{ARTICLES}/bulk", data={**base, "bulk_input": "1", "status": "draft"}
    )
    assert missing.status_code == 400


@pytest.mark.asyncio
async def test_needs_edit_permission(database: AsyncDatabaseManager) -> None:
    def read_only(user: Any, admin: AdminModel[Any], action: Any) -> bool:
        """Allow only ``VIEW``.

        Args:
            user (Any): The principal.
            admin (AdminModel[Any]): The admin asked about.
            action (Any): The permission.

        Returns:
            bool: ``True`` for ``VIEW``.
        """
        return action is AdminPermissionEnum.VIEW

    async for world in open_world(database, access_policy=read_only):
        page = await world.client.get(f"{ARTICLES}/")
        assert 'value="bulk_edit"' not in page.text
        resp = await world.client.post(
            f"{ARTICLES}/bulk",
            data={
                "csrf_token": world.csrf,
                "action": "bulk_edit",
                "ids": [str(world.ids["article_in"])],
            },
        )
        assert resp.status_code == 403


@pytest.mark.asyncio
async def test_locked_row_refuses_the_whole_edit(
    database: AsyncDatabaseManager,
) -> None:
    def lock_published(row: Any) -> list[str]:
        """Lock ``status`` on the article inside the scope.

        Args:
            row (Any): The stored row.

        Returns:
            list[str]: The locked columns.
        """
        return ["status"] if row.title == "ArticleInside" else []

    async for world in open_world(
        database, article_options={"readonly_fields_for": lock_published}
    ):
        _step, final = await _apply(
            world, "status", "archived", [str(world.ids["article_in"])]
        )
        assert final.status_code == 400
        assert "1 selected row locks this field." in final.text


@pytest.mark.asyncio
async def test_foreign_key_outside_the_target_scope_is_refused(
    database: AsyncDatabaseManager,
) -> None:
    async for world in open_world(
        database, comment_options={"bulk_edit_fields": ["article_id"]}
    ):
        page = await world.client.get(f"{COMMENTS}/")
        own = re.findall(r'name="ids" value="([^"]+)"', page.text)
        assert len(own) == 2
        base = {"csrf_token": world.csrf, "action": "bulk_edit", "ids": own}
        step = await world.client.post(f"{COMMENTS}/bulk", data=base)
        assert "ArticleOutside" not in step.text
        final = await world.client.post(
            f"{COMMENTS}/bulk",
            data={
                **base,
                "bulk_input": "1",
                "article_id": str(world.ids["article_out"]),
                "submission_nonce": _nonce(step.text),
            },
        )
        assert final.status_code == 400
        assert "that record does not exist" in final.text


@pytest.mark.asyncio
async def test_validate_edit_is_asked_for_every_row(
    database: AsyncDatabaseManager,
) -> None:
    def no_archiving(row: Any, form: Any) -> dict[str, str]:
        """Refuse archiving the article inside the scope.

        Args:
            row (Any): The stored row.
            form (Any): The posted form.

        Returns:
            dict[str, str]: The refusal.
        """
        if form.get("status") == "archived" and row.title == "ArticleInside":
            return {"status": "Cannot archive this one."}
        return {}

    async for world in open_world(
        database, article_options={"validate_edit": no_archiving}
    ):
        before = await world.article("article_in")
        assert before is not None
        _step, final = await _apply(
            world, "status", "archived", [str(world.ids["article_in"])]
        )
        assert final.status_code == 400
        assert "Cannot archive this one." in final.text
        _step, done = await _apply(
            world, "status", "published", [str(world.ids["article_in"])]
        )
        assert done.status_code == 303
        after = await world.article("article_in")
        assert after is not None and after.updated_at > before.updated_at


@pytest.mark.asyncio
async def test_forged_bulk_field_changes_nothing(world: Wave2World) -> None:
    base = {
        "csrf_token": world.csrf,
        "action": "bulk_edit",
        "ids": [str(world.ids["article_in"])],
    }
    forged = await world.client.post(
        f"{ARTICLES}/bulk",
        data={**base, "bulk_field": "slug", "bulk_input": "1", "slug": "hijacked"},
    )
    assert forged.status_code == 200
    assert 'name="bulk_field"' in forged.text
    inside = await world.article("article_in")
    assert inside is not None and inside.slug == "inside"
    users = await world.client.post(
        f"{USERS}/bulk",
        data={
            **base,
            "ids": [str(world.ids["operator"])],
            "bulk_field": "is_admin",
            "bulk_input": "1",
        },
    )
    assert users.status_code == 400
    assert await world.audit_entries() == []
