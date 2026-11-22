""" "Restore" from the audit history: pre-fill only, the save is a normal edit."""

from __future__ import annotations

import re
import uuid
from collections.abc import AsyncIterator
from dataclasses import dataclass
from pathlib import Path
from typing import Any, ClassVar

import pytest
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient
from sqlalchemy import String, select
from sqlalchemy.orm import Mapped, mapped_column

from tempest_fastapi_sdk import (
    AdminModel,
    AdminSite,
    AsyncDatabaseManager,
    BaseAuditLogModel,
    BaseModel,
    BaseRepository,
    BaseUserModel,
    UserModelAuthBackend,
    make_admin_router,
)
from tempest_fastapi_sdk.admin import AdminPermissionEnum
from tempest_fastapi_sdk.db.audit import AUDIT_REDACTED
from tempest_fastapi_sdk.testing import admin_csrf_token, admin_login
from tempest_fastapi_sdk.utils import LocalUploadStorage

SECRET = "r" * 48


class RestoreUser(BaseUserModel):
    """Operator of the restore tests."""

    __tablename__ = "admin_restore_users"


class RestoreItem(BaseModel):
    """Row whose history is restored."""

    __tablename__ = "admin_restore_items"
    __audit_redact__: ClassVar[tuple[str, ...]] = ("token",)
    name: Mapped[str] = mapped_column(String(64), nullable=False)
    note: Mapped[str | None] = mapped_column(String(64), nullable=True)
    status: Mapped[str] = mapped_column(String(16), nullable=False, default="open")
    code: Mapped[str] = mapped_column(String(16), nullable=False, default="C1")
    attachment: Mapped[str | None] = mapped_column(String(255), nullable=True)
    token: Mapped[str | None] = mapped_column(String(64), nullable=True)


class RestoreAudit(BaseAuditLogModel):
    """Audit table of :class:`RestoreItem`."""

    __tablename__ = "admin_restore_audit"


ITEMS = f"/admin/m/{RestoreItem.__tablename__}"
VALUE = re.compile(r'name="(\w+)" value="([^"]*)"')
SELECTED = re.compile(
    r'<select name="(\w+)"[^>]*>(?:(?!</select>).)*?'
    r'<option value="([^"]*)" selected',
    re.S,
)


@dataclass
class RestoreWorld:
    """A signed-in client, the database and the seeded ids."""

    client: AsyncClient
    db: AsyncDatabaseManager
    item: str
    entries: dict[str, str]

    async def row(self) -> RestoreItem:
        """Reload the item.

        Returns:
            RestoreItem: The stored row.
        """
        async with self.db.get_session_context() as session:
            repo: BaseRepository[RestoreItem] = BaseRepository(
                session, model=RestoreItem
            )
            return await repo.get_by_id(uuid.UUID(self.item))


def deny_edit(user: Any, admin: Any, action: AdminPermissionEnum) -> bool:
    """Grant everything but ``EDIT``.

    Args:
        user (Any): The principal.
        admin (Any): The admin asked about.
        action (AdminPermissionEnum): The permission.

    Returns:
        bool: ``False`` only for ``EDIT``.
    """
    return action is not AdminPermissionEnum.EDIT


async def _open(tmp_path: Path, *, policy: Any = None) -> AsyncIterator[RestoreWorld]:
    """Seed one item with four audit entries, mount the admin, sign in.

    Args:
        tmp_path (Path): Upload directory.
        policy (Any): The ``access_policy``.

    Yields:
        RestoreWorld: The signed-in world.
    """
    db = AsyncDatabaseManager("sqlite+aiosqlite:///:memory:")
    await db.connect()
    await db.create_tables()
    async with db.get_session_context() as session:
        user = RestoreUser(email="op@example.com", hashed_password="", is_admin=True)
        user.set_password("hunter2")
        item = RestoreItem(
            name="NewName",
            note="NewNote",
            status="open",
            code="C2",
            attachment="new.txt",
            token="tok-new",
        )
        session.add_all([user, item])
        await session.flush()
        entity_id = str(item.id)
        created = RestoreAudit(
            entity="RestoreItem",
            entity_id=entity_id,
            action="create",
            changes={"after": {"name": "First"}},
        )
        updated = RestoreAudit(
            entity="RestoreItem",
            entity_id=entity_id,
            action="update",
            changes={
                "name": {"before": "OldName", "after": "NewName"},
                "note": {"before": "OldNote", "after": "NewNote"},
                "code": {"before": "C1", "after": "C2"},
                "attachment": {"before": "old.txt", "after": "new.txt"},
                "token": {"before": AUDIT_REDACTED, "after": AUDIT_REDACTED},
            },
        )
        legacy = RestoreAudit(
            entity="RestoreItem",
            entity_id=entity_id,
            action="update",
            changes={"status": {"before": "legacy", "after": "open"}},
        )
        only_hidden = RestoreAudit(
            entity="RestoreItem",
            entity_id=entity_id,
            action="update",
            changes={"token": {"before": AUDIT_REDACTED, "after": AUDIT_REDACTED}},
        )
        session.add_all([created, updated, legacy, only_hidden])
        await session.commit()
        entries = {
            "create": str(created.id),
            "update": str(updated.id),
            "legacy": str(legacy.id),
            "hidden": str(only_hidden.id),
        }
    site = AdminSite(title="Restore")
    site.register(
        AdminModel(
            RestoreItem,
            readonly_fields=["code"],
            upload_fields=["attachment"],
            upload_storage=LocalUploadStorage(tmp_path),
            exclude_fields=["token"],
            choices={"status": ["open", "closed"]},
            audit_model=RestoreAudit,
        )
    )
    app = FastAPI()
    app.include_router(
        make_admin_router(
            site,
            db=db,
            auth_backend=UserModelAuthBackend(RestoreUser),
            secret_key=SECRET,
            cookie_secure=False,
            access_policy=policy,
        )
    )
    async with AsyncClient(
        transport=ASGITransport(app=app), base_url="http://test"
    ) as client:
        await admin_login(client, "op@example.com", "hunter2")
        yield RestoreWorld(client=client, db=db, item=entity_id, entries=entries)
    await db.drop_tables()
    await db.disconnect()


@pytest.fixture
async def world(tmp_path: Path) -> AsyncIterator[RestoreWorld]:
    """The world with no ``access_policy``.

    Args:
        tmp_path (Path): Upload directory.

    Yields:
        RestoreWorld: The signed-in world.
    """
    async for opened in _open(tmp_path):
        yield opened


def _posted(page: str) -> dict[str, str]:
    """Collect what the browser would post from a rendered form.

    Args:
        page (str): The form page.

    Returns:
        dict[str, str]: Text inputs, hidden fields and selected options.
    """
    form = page.split('class="tempest-admin-form__form"', 1)[1].split("</form>", 1)[0]
    data = dict(VALUE.findall(form))
    data.update(dict(SELECTED.findall(form)))
    return data


def _restore_url(world: RestoreWorld, key: str) -> str:
    """Build the restore URL of a seeded entry.

    Args:
        world (RestoreWorld): The world.
        key (str): Which entry.

    Returns:
        str: The URL.
    """
    return f"{ITEMS}/{world.item}/restore/{world.entries[key]}"


@pytest.mark.asyncio
async def test_history_offers_restore_only_on_restorable_updates(
    world: RestoreWorld,
) -> None:
    detail = await world.client.get(f"{ITEMS}/{world.item}")
    assert _restore_url(world, "update") in detail.text
    assert _restore_url(world, "legacy") in detail.text
    assert _restore_url(world, "create") not in detail.text
    assert _restore_url(world, "hidden") not in detail.text
    assert (await world.client.get(_restore_url(world, "create"))).status_code == 404
    assert (await world.client.get(_restore_url(world, "hidden"))).status_code == 404


@pytest.mark.asyncio
async def test_restore_prefills_previous_values_and_writes_nothing(
    world: RestoreWorld,
) -> None:
    before = await world.row()
    page = await world.client.get(_restore_url(world, "update"))
    assert page.status_code == 200
    data = _posted(page.text)
    assert data["name"] == "OldName"
    assert data["note"] == "OldNote"
    assert "code" not in data
    assert "token" not in data
    assert "old.txt" not in page.text
    assert "Name, Note" in page.text
    assert f'action="{ITEMS}/{world.item}/edit"' in page.text
    assert data["_row_version"] == before.updated_at.isoformat()
    async with world.db.get_session_context() as session:
        audit_rows = (await session.execute(select(RestoreAudit))).scalars().all()
    assert len(audit_rows) == 4
    after = await world.row()
    assert (after.name, after.note, after.updated_at) == (
        before.name,
        before.note,
        before.updated_at,
    )


@pytest.mark.asyncio
async def test_saving_the_restored_form_is_a_normal_edit(world: RestoreWorld) -> None:
    page = await world.client.get(_restore_url(world, "update"))
    resp = await world.client.post(
        f"{ITEMS}/{world.item}/edit", data=_posted(page.text)
    )
    assert resp.status_code == 303
    row = await world.row()
    assert (row.name, row.note) == ("OldName", "OldNote")
    assert (row.code, row.attachment, row.token) == ("C2", "new.txt", "tok-new")


@pytest.mark.asyncio
async def test_restore_save_keeps_the_optimistic_lock(world: RestoreWorld) -> None:
    page = await world.client.get(_restore_url(world, "update"))
    csrf = admin_csrf_token(page.text)
    edit = await world.client.get(f"{ITEMS}/{world.item}/edit")
    version = _posted(edit.text)["_row_version"]
    first = await world.client.post(
        f"{ITEMS}/{world.item}/edit",
        data={
            "csrf_token": csrf,
            "name": "Concurrent",
            "status": "open",
            "_row_version": version,
        },
    )
    assert first.status_code == 303
    second = await world.client.post(
        f"{ITEMS}/{world.item}/edit", data=_posted(page.text)
    )
    assert second.status_code == 409
    assert (await world.row()).name == "Concurrent"


@pytest.mark.asyncio
async def test_a_value_no_longer_valid_fails_on_save(world: RestoreWorld) -> None:
    page = await world.client.get(_restore_url(world, "legacy"))
    data = _posted(page.text)
    assert data["status"] == "legacy"
    resp = await world.client.post(f"{ITEMS}/{world.item}/edit", data=data)
    assert resp.status_code == 400
    assert "tempest-admin-form__field-error" in resp.text
    assert (await world.row()).status == "open"


@pytest.mark.asyncio
async def test_restore_needs_edit(tmp_path: Path) -> None:
    async for world in _open(tmp_path, policy=deny_edit):
        detail = await world.client.get(f"{ITEMS}/{world.item}")
        assert detail.status_code == 200
        assert "/restore/" not in detail.text
        page = await world.client.get(_restore_url(world, "update"))
        assert page.status_code == 403


@pytest.mark.asyncio
async def test_unknown_or_malformed_entry_is_404(world: RestoreWorld) -> None:
    unknown = await world.client.get(f"{ITEMS}/{world.item}/restore/{uuid.uuid4()}")
    assert unknown.status_code == 404
    malformed = await world.client.get(f"{ITEMS}/{world.item}/restore/not-a-uuid")
    assert malformed.status_code == 404
