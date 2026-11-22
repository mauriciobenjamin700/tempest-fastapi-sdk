"""The account ``impersonate_account`` resolves stays inside the operator's reach.

Covers the second security review of #804: the resolved account must be in
the ``row_scope`` of its own admin, must have an ``id``, a sync resolver may
read a lazy relationship, a custom backend that filters ``load_principal``
still recognises an inactive admin, and a failed handler leaves a second
audit entry.
"""

from __future__ import annotations

import re
import uuid
from collections.abc import AsyncIterator
from dataclasses import dataclass, field
from typing import Any

import pytest
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient
from sqlalchemy import ColumnElement, ForeignKey, String, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import Mapped, mapped_column, relationship

from tempest_fastapi_sdk import (
    AdminModel,
    AdminSite,
    AsyncDatabaseManager,
    BaseAuditLogModel,
    BaseModel,
    BaseUserModel,
    UserModelAuthBackend,
    make_admin_router,
)
from tempest_fastapi_sdk.admin import (
    ADMIN_IMPERSONATION_EVENT,
    ADMIN_IMPERSONATION_FAILED_EVENT,
    AdminAuthBackend,
    AdminAuthError,
    AdminPermissionEnum,
)
from tempest_fastapi_sdk.exceptions import ConflictException
from tempest_fastapi_sdk.testing import admin_csrf_token, admin_login

SECRET = "q" * 48
NONCE = re.compile(r'name="submission_nonce" value="([^"]+)"')


class ReachUser(BaseUserModel):
    """Accounts, each in one company."""

    __tablename__ = "admin_reach_users"
    company: Mapped[str] = mapped_column(String(16), nullable=False, default="acme")


class ReachMembership(BaseModel):
    """Row that logs in as its ``user``; the relationship is lazy (default)."""

    __tablename__ = "admin_reach_memberships"
    company: Mapped[str] = mapped_column(String(16), nullable=False)
    label: Mapped[str] = mapped_column(String(32), nullable=False)
    user_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("admin_reach_users.id"), nullable=True
    )
    user: Mapped[ReachUser | None] = relationship()


class ReachAudit(BaseAuditLogModel):
    """Audit table of the memberships and users."""

    __tablename__ = "admin_reach_audit"


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


def user_scope(user: Any, admin: Any) -> ColumnElement[bool]:
    """Keep the operator's company among users.

    Args:
        user (Any): The principal.
        admin (Any): The admin asked about.

    Returns:
        ColumnElement[bool]: ``company == user.company``.
    """
    return ReachUser.company == user.company


def membership_scope(user: Any, admin: Any) -> ColumnElement[bool]:
    """Keep the operator's company among memberships.

    Args:
        user (Any): The principal.
        admin (Any): The admin asked about.

    Returns:
        ColumnElement[bool]: ``company == user.company``.
    """
    return ReachMembership.company == user.company


@dataclass
class ReachWorld:
    """Signed-in client, ids and the handler's calls."""

    client: AsyncClient
    db: AsyncDatabaseManager
    ids: dict[str, str]
    calls: list[str] = field(default_factory=list)


async def _open(
    *, resolver: Any, user_handler_fails: bool = False
) -> AsyncIterator[ReachWorld]:
    """Seed, mount the scoped admins and sign the operator in.

    Args:
        resolver (Any): The membership admin's ``impersonate_account``.
        user_handler_fails (bool): Whether the user admin's handler raises.

    Yields:
        ReachWorld: The signed-in world.
    """
    db = AsyncDatabaseManager("sqlite+aiosqlite:///:memory:")
    await db.connect()
    await db.create_tables()
    async with db.get_session_context() as session:
        operator = ReachUser(
            email="op@example.com", hashed_password="", is_admin=True, company="acme"
        )
        operator.set_password("hunter2")
        mine = ReachUser(email="a@example.com", hashed_password="", company="acme")
        foreign = ReachUser(email="b@example.com", hashed_password="", company="globex")
        session.add_all([operator, mine, foreign])
        await session.flush()
        own = ReachMembership(company="acme", label="own", user_id=mine.id)
        cross = ReachMembership(company="acme", label="cross", user_id=foreign.id)
        session.add_all([own, cross])
        await session.commit()
        ids = {
            "mine": str(mine.id),
            "foreign": str(foreign.id),
            "own": str(own.id),
            "cross": str(cross.id),
        }
    calls: list[str] = []

    def record(operator: Any, account: Any) -> str:
        """Record the account and answer a fixed URL.

        Args:
            operator (Any): The admin.
            account (Any): The account.

        Returns:
            str: The URL.
        """
        calls.append(str(account.id))
        return "https://app.example/enter#token=x"

    def refuse(operator: Any, account: Any) -> str:
        """Refuse every impersonation.

        Args:
            operator (Any): The admin.
            account (Any): The account.

        Raises:
            ConflictException: Always.
        """
        calls.append(str(account.id))
        raise ConflictException(message="Account locked")

    site = AdminSite(title="Reach")
    site.register(
        AdminModel(
            ReachUser,
            row_scope=user_scope,
            audit_model=ReachAudit,
            impersonate=refuse if user_handler_fails else record,
        )
    )
    site.register(
        AdminModel(
            ReachMembership,
            row_scope=membership_scope,
            audit_model=ReachAudit,
            impersonate=record,
            impersonate_account=resolver,
        )
    )
    app = FastAPI()
    app.include_router(
        make_admin_router(
            site,
            db=db,
            auth_backend=UserModelAuthBackend(ReachUser),
            secret_key=SECRET,
            cookie_secure=False,
            access_policy=grant,
        )
    )
    async with AsyncClient(
        transport=ASGITransport(app=app), base_url="http://test"
    ) as client:
        await admin_login(client, "op@example.com", "hunter2")
        yield ReachWorld(client=client, db=db, ids=ids, calls=calls)
    await db.drop_tables()
    await db.disconnect()


MEMBERS = f"/admin/m/{ReachMembership.__tablename__}"
USERS = f"/admin/m/{ReachUser.__tablename__}"


async def _confirm(world: ReachWorld, url: str) -> Any:
    """Open a confirmation page and submit it.

    Args:
        world (ReachWorld): The world.
        url (str): The impersonate URL.

    Returns:
        Any: The ``POST`` response.
    """
    page = await world.client.get(url)
    assert page.status_code == 200, page.text
    match = NONCE.search(page.text)
    assert match is not None
    return await world.client.post(
        url,
        data={
            "csrf_token": admin_csrf_token(page.text),
            "submission_nonce": match.group(1),
        },
    )


async def _audit_events(world: ReachWorld) -> list[str | None]:
    """Return the event names of every audit row, oldest first.

    Args:
        world (ReachWorld): The world.

    Returns:
        list[str | None]: ``context["event"]`` per row.
    """
    async with world.db.get_session_context() as session:
        rows = (
            (await session.execute(select(ReachAudit).order_by(ReachAudit.created_at)))
            .scalars()
            .all()
        )
    return [(row.context or {}).get("event") for row in rows]


@pytest.mark.asyncio
async def test_resolved_account_outside_its_admins_scope_is_404() -> None:
    async for world in _open(resolver=lambda membership: membership.user):
        cross = await world.client.get(f"{MEMBERS}/{world.ids['cross']}/impersonate")
        assert cross.status_code == 404
        detail = await world.client.get(f"{MEMBERS}/{world.ids['cross']}")
        assert detail.status_code == 200
        assert "/impersonate" not in detail.text
        resp = await _confirm(world, f"{MEMBERS}/{world.ids['own']}/impersonate")
        assert resp.status_code == 303
        assert world.calls == [world.ids["mine"]]


@pytest.mark.asyncio
async def test_sync_resolver_reads_a_lazy_relationship_on_the_detail() -> None:
    async for world in _open(resolver=lambda membership: membership.user):
        detail = await world.client.get(f"{MEMBERS}/{world.ids['own']}")
        assert detail.status_code == 200
        assert f"{MEMBERS}/{world.ids['own']}/impersonate" in detail.text


@pytest.mark.asyncio
async def test_account_without_id_is_refused_before_the_audit() -> None:
    async for world in _open(resolver=lambda membership: "a@example.com"):
        page = await world.client.get(f"{MEMBERS}/{world.ids['own']}/impersonate")
        assert page.status_code == 404
        detail = await world.client.get(f"{MEMBERS}/{world.ids['own']}")
        assert "/impersonate" not in detail.text
        assert await _audit_events(world) == []
        assert world.calls == []


@pytest.mark.asyncio
async def test_failed_handler_writes_a_second_audit_entry() -> None:
    async for world in _open(
        resolver=lambda membership: membership.user, user_handler_fails=True
    ):
        resp = await _confirm(world, f"{USERS}/{world.ids['mine']}/impersonate")
        assert resp.status_code == 400
        assert await _audit_events(world) == [
            ADMIN_IMPERSONATION_EVENT,
            ADMIN_IMPERSONATION_FAILED_EVENT,
        ]
        detail = await world.client.get(f"{USERS}/{world.ids['mine']}")
        assert "log in as failed" in detail.text


class FilteringBackend(AdminAuthBackend[ReachUser]):
    """Custom backend whose ``load_principal`` drops inactive accounts."""

    async def authenticate(
        self, session: AsyncSession, *, identifier: str, password: str
    ) -> ReachUser:
        """Refuse every login (unused here).

        Args:
            session (AsyncSession): The session.
            identifier (str): The identifier.
            password (str): The password.

        Raises:
            AdminAuthError: Always.
        """
        raise AdminAuthError("unused")

    async def load_principal(
        self, session: AsyncSession, principal_id: str
    ) -> ReachUser | None:
        """Load an active admin only.

        Args:
            session (AsyncSession): The session.
            principal_id (str): The id.

        Returns:
            ReachUser | None: The admin, or ``None``.
        """
        user = await self.load_account(session, principal_id)
        if user is None or not user.is_active or not user.is_admin:
            return None
        return user

    async def load_account(
        self, session: AsyncSession, account_id: str
    ) -> ReachUser | None:
        """Load the user row with no sign-in filter.

        Args:
            session (AsyncSession): The session.
            account_id (str): The id.

        Returns:
            ReachUser | None: The row.
        """
        return await session.get(ReachUser, uuid.UUID(account_id))

    def principal_id(self, principal: ReachUser) -> str:
        """Return the id as text.

        Args:
            principal (ReachUser): The principal.

        Returns:
            str: The id.
        """
        return str(principal.id)


@pytest.mark.asyncio
async def test_custom_backend_that_filters_still_sees_an_inactive_admin() -> None:
    db = AsyncDatabaseManager("sqlite+aiosqlite:///:memory:")
    await db.connect()
    await db.create_tables()
    async with db.get_session_context() as session:
        ghost = ReachUser(
            email="ghost@example.com",
            hashed_password="",
            is_admin=True,
            is_active=False,
        )
        plain = ReachUser(email="plain@example.com", hashed_password="")
        session.add_all([ghost, plain])
        await session.commit()
        backend = FilteringBackend()
        assert await backend.load_principal(session, str(ghost.id)) is None
        assert await backend.is_admin_identity(session, str(ghost.id)) is True
        assert await backend.is_admin_identity(session, str(plain.id)) is False
    await db.drop_tables()
    await db.disconnect()
