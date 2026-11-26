"""A principal or impersonated account that is not a model (#837).

``AdminAuthBackend`` is generic in the principal, and its own docstring
names an LDAP entry as one. The router used to look the principal's type up
in the site behind ``cast("type[BaseModel]", ...)``, so a plain object raised
``AttributeError: ... get_table_name`` on every page that names the author
of an audited change, and on every row whose ``impersonate_account`` resolves
to a plain object.
"""

from __future__ import annotations

import uuid
from collections.abc import AsyncIterator
from dataclasses import dataclass
from types import SimpleNamespace
from typing import Any

import pytest
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient
from sqlalchemy import String
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import Mapped, mapped_column

from tempest_fastapi_sdk import (
    AdminModel,
    AdminSite,
    AsyncDatabaseManager,
    BaseAuditLogModel,
    BaseModel,
    BaseRepository,
    make_admin_router,
)
from tempest_fastapi_sdk.admin import (
    AdminAuthBackend,
    AdminAuthError,
    AdminPermissionEnum,
)
from tempest_fastapi_sdk.db.audit import snapshot_model
from tempest_fastapi_sdk.testing import admin_login

SECRET = "l" * 48
OPERATOR_ID = "uid=ops,dc=example,dc=com"


@dataclass(frozen=True)
class LdapEntry:
    """A directory entry: the principal, which is not a model."""

    dn: str
    common_name: str


class LdapThing(BaseModel):
    """Audited row edited by the directory operator."""

    __tablename__ = "admin_ldap_things"
    name: Mapped[str] = mapped_column(String(64), nullable=False)


class LdapThingAudit(BaseAuditLogModel):
    """Audit table of :class:`LdapThing`."""

    __tablename__ = "admin_ldap_things_audit"


class LdapBackend(AdminAuthBackend[LdapEntry]):
    """Backend whose principal is a plain dataclass."""

    async def authenticate(
        self, session: AsyncSession, *, identifier: str, password: str
    ) -> LdapEntry:
        """Accept the one operator.

        Args:
            session (AsyncSession): The session.
            identifier (str): The identifier.
            password (str): The password.

        Returns:
            LdapEntry: The operator.

        Raises:
            AdminAuthError: On any other credential.
        """
        if identifier != "ops@example.com" or password != "hunter2":
            raise AdminAuthError("Invalid credentials")
        return LdapEntry(dn=OPERATOR_ID, common_name="Directory Ops")

    async def load_principal(
        self, session: AsyncSession, principal_id: str
    ) -> LdapEntry | None:
        """Reload the operator by DN.

        Args:
            session (AsyncSession): The session.
            principal_id (str): The DN.

        Returns:
            LdapEntry | None: The operator, or ``None``.
        """
        if principal_id != OPERATOR_ID:
            return None
        return LdapEntry(dn=OPERATOR_ID, common_name="Directory Ops")

    def principal_id(self, principal: LdapEntry) -> str:
        """Return the DN.

        Args:
            principal (LdapEntry): The operator.

        Returns:
            str: The DN.
        """
        return principal.dn

    def display_name(self, principal: LdapEntry) -> str:
        """Return the common name.

        Args:
            principal (LdapEntry): The operator.

        Returns:
            str: The common name.
        """
        return principal.common_name


def grant(user: Any, admin: Any, action: AdminPermissionEnum) -> bool:
    """Grant every permission.

    Args:
        user (Any): The principal.
        admin (Any): The admin asked about.
        action (AdminPermissionEnum): The permission.

    Returns:
        bool: Always ``True``.
    """
    return True


def record(operator: Any, account: Any) -> str:
    """Answer a fixed URL for any impersonation.

    Args:
        operator (Any): The admin.
        account (Any): The account.

    Returns:
        str: The URL.
    """
    return "https://app.example/enter#token=x"


@dataclass
class LdapWorld:
    """Signed-in client and the audited row's id."""

    client: AsyncClient
    thing_id: str


@pytest.fixture
async def world() -> AsyncIterator[LdapWorld]:
    db = AsyncDatabaseManager("sqlite+aiosqlite:///:memory:")
    await db.connect()
    await db.create_tables()
    async with db.get_session_context() as session:
        repo: BaseRepository[LdapThing] = BaseRepository(
            session, model=LdapThing, audit_model=LdapThingAudit
        )
        thing = await repo.add_audited(LdapThing(name="original"), actor=OPERATOR_ID)
        thing_id = str(thing.id)
        before = snapshot_model(thing)
        thing.name = "renamed"
        await repo.update_audited(thing, before, actor=OPERATOR_ID)
    site = AdminSite(title="LDAP", recent_activity=10)
    site.register(
        AdminModel(
            LdapThing,
            audit_model=LdapThingAudit,
            impersonate=record,
            impersonate_account=lambda thing: SimpleNamespace(id=uuid.uuid4()),
        )
    )
    app = FastAPI()
    app.include_router(
        make_admin_router(
            site,
            db=db,
            auth_backend=LdapBackend(),
            secret_key=SECRET,
            cookie_secure=False,
            access_policy=grant,
        )
    )
    async with AsyncClient(
        transport=ASGITransport(app=app), base_url="http://test"
    ) as client:
        login = await admin_login(client, "ops@example.com", "hunter2")
        assert login.status_code == 303
        yield LdapWorld(client=client, thing_id=thing_id)
    await db.drop_tables()
    await db.disconnect()


@pytest.mark.asyncio
async def test_audit_panel_names_a_non_model_author(world: LdapWorld) -> None:
    response = await world.client.get(
        f"/admin/m/{LdapThing.__tablename__}/{world.thing_id}"
    )
    assert response.status_code == 200
    assert "tempest-admin-history__item--update" in response.text
    assert "· Directory Ops" in response.text


@pytest.mark.asyncio
async def test_dashboard_feed_names_a_non_model_author(world: LdapWorld) -> None:
    response = await world.client.get("/admin/")
    assert response.status_code == 200
    assert "tempest-admin-activity__meta" in response.text
    assert "· Directory Ops" in response.text


@pytest.mark.asyncio
async def test_non_model_impersonated_account_is_offered(world: LdapWorld) -> None:
    url = f"/admin/m/{LdapThing.__tablename__}/{world.thing_id}/impersonate"
    page = await world.client.get(url)
    assert page.status_code == 200
