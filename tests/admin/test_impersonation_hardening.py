"""Impersonation refuses the *identity* that goes into the credential.

A model whose rows share their primary key with the user table (a 1:1
profile) and a resolver that maps a row to its user both hand the handler an
account that is not the registered row; the admin-account refusal has to
look at that account. The audit entry is committed before the handler runs.
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
from sqlalchemy import ForeignKey, String
from sqlalchemy.exc import IntegrityError
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
from tempest_fastapi_sdk.admin import AdminPermissionEnum
from tempest_fastapi_sdk.testing import admin_csrf_token, admin_login

SECRET = "h" * 48
NONCE = re.compile(r'name="submission_nonce" value="([^"]+)"')


class HardUser(BaseUserModel):
    """Accounts: operators, another admin, a customer."""

    __tablename__ = "admin_hard_users"


class HardProfile(BaseModel):
    """1:1 profile whose primary key is the user's id."""

    __tablename__ = "admin_hard_profiles"
    id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("admin_hard_users.id"), primary_key=True
    )
    nickname: Mapped[str] = mapped_column(String(32), nullable=False)


class HardMembership(BaseModel):
    """Row that logs in as its ``user``."""

    __tablename__ = "admin_hard_memberships"
    label: Mapped[str] = mapped_column(String(32), nullable=False)
    user_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("admin_hard_users.id"), nullable=True
    )
    user: Mapped[HardUser | None] = relationship(lazy="selectin")


class HardBrokenAudit(BaseAuditLogModel):
    """Audit table whose insert always fails (``NOT NULL`` without default)."""

    __tablename__ = "admin_hard_broken_audit"
    ticket: Mapped[str] = mapped_column(String(8), nullable=False)


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


@dataclass
class HardWorld:
    """Signed-in client, ids and the handler's calls."""

    client: AsyncClient
    db: AsyncDatabaseManager
    ids: dict[str, str]
    calls: list[tuple[str, str]] = field(default_factory=list)


async def _seed(db: AsyncDatabaseManager) -> dict[str, str]:
    """Insert the accounts, their profiles and memberships.

    Args:
        db (AsyncDatabaseManager): The database.

    Returns:
        dict[str, str]: The ids by name.
    """
    async with db.get_session_context() as session:
        operator = HardUser(email="op@example.com", hashed_password="", is_admin=True)
        operator.set_password("hunter2")
        boss = HardUser(email="boss@example.com", hashed_password="", is_admin=True)
        customer = HardUser(email="cu@example.com", hashed_password="")
        session.add_all([operator, boss, customer])
        await session.flush()
        boss_profile = HardProfile(id=boss.id, nickname="boss")
        customer_profile = HardProfile(id=customer.id, nickname="cust")
        boss_member = HardMembership(label="m-boss", user_id=boss.id)
        customer_member = HardMembership(label="m-cust", user_id=customer.id)
        orphan = HardMembership(label="m-orphan", user_id=None)
        session.add_all(
            [boss_profile, customer_profile, boss_member, customer_member, orphan]
        )
        await session.commit()
        return {
            "operator": str(operator.id),
            "boss": str(boss.id),
            "customer": str(customer.id),
            "boss_member": str(boss_member.id),
            "customer_member": str(customer_member.id),
            "orphan": str(orphan.id),
        }


async def _world(register: Any) -> AsyncIterator[HardWorld]:
    """Mount the admin with the admins ``register`` adds, and sign in.

    Args:
        register (Any): ``(site, record) -> None`` registering the admins.

    Yields:
        HardWorld: The signed-in world.
    """
    db = AsyncDatabaseManager("sqlite+aiosqlite:///:memory:")
    await db.connect()
    await db.create_tables()
    ids = await _seed(db)
    calls: list[tuple[str, str]] = []

    def record(operator: Any, account: Any) -> str:
        """Record what the handler received.

        Args:
            operator (Any): The admin.
            account (Any): The account to enter as.

        Returns:
            str: A fixed URL.
        """
        calls.append((type(account).__name__, str(account.id)))
        return "https://app.example/enter#token=x"

    site = AdminSite(title="Hard")
    register(site, record)
    app = FastAPI()
    app.include_router(
        make_admin_router(
            site,
            db=db,
            auth_backend=UserModelAuthBackend(HardUser),
            secret_key=SECRET,
            cookie_secure=False,
            access_policy=grant,
        )
    )
    async with AsyncClient(
        transport=ASGITransport(app=app), base_url="http://test"
    ) as client:
        await admin_login(client, "op@example.com", "hunter2")
        yield HardWorld(client=client, db=db, ids=ids, calls=calls)
    await db.drop_tables()
    await db.disconnect()


async def _post(world: HardWorld, base: str, row: str) -> Any:
    """Open the confirmation of one row and confirm it.

    Args:
        world (HardWorld): The world.
        base (str): The model's admin URL.
        row (str): The row id.

    Returns:
        Any: The confirmation response.
    """
    any_page = await world.client.get(f"{base}/")
    csrf = admin_csrf_token(any_page.text)
    form = await world.client.get(f"{base}/{world.ids['customer']}/impersonate")
    match = NONCE.search(form.text)
    nonce = match.group(1) if match else "none"
    return await world.client.post(
        f"{base}/{row}/impersonate",
        data={"csrf_token": csrf, "submission_nonce": nonce},
    )


PROFILES = f"/admin/m/{HardProfile.__tablename__}"
MEMBERS = f"/admin/m/{HardMembership.__tablename__}"
USERS = f"/admin/m/{HardUser.__tablename__}"


@pytest.mark.asyncio
async def test_profile_sharing_an_admin_users_id_is_refused() -> None:
    def register(site: AdminSite, record: Any) -> None:
        """Register the profile admin.

        Args:
            site (AdminSite): The site.
            record (Any): The handler.
        """
        site.register(AdminModel(HardProfile, impersonate=record))

    async for world in _world(register):
        detail = await world.client.get(f"{PROFILES}/{world.ids['boss']}")
        assert "/impersonate" not in detail.text
        page = await world.client.get(f"{PROFILES}/{world.ids['boss']}/impersonate")
        assert page.status_code == 403
        refused = await _post(world, PROFILES, world.ids["boss"])
        assert refused.status_code == 403
        assert world.calls == []
        allowed = await _post(world, PROFILES, world.ids["customer"])
        assert allowed.status_code == 303
        assert world.calls == [("HardProfile", world.ids["customer"])]


@pytest.mark.asyncio
async def test_resolver_hands_the_user_to_the_handler_and_refuses_admins() -> None:
    def register(site: AdminSite, record: Any) -> None:
        """Register the membership admin with a resolver to its user.

        Args:
            site (AdminSite): The site.
            record (Any): The handler.
        """
        site.register(
            AdminModel(
                HardMembership,
                impersonate=record,
                impersonate_account=lambda membership: membership.user,
            )
        )

    async for world in _world(register):
        boss = await world.client.get(
            f"{MEMBERS}/{world.ids['boss_member']}/impersonate"
        )
        assert boss.status_code == 403
        orphan = await world.client.get(f"{MEMBERS}/{world.ids['orphan']}/impersonate")
        assert orphan.status_code == 404
        page = await world.client.get(
            f"{MEMBERS}/{world.ids['customer_member']}/impersonate"
        )
        assert page.status_code == 200
        match = NONCE.search(page.text)
        assert match is not None
        resp = await world.client.post(
            f"{MEMBERS}/{world.ids['customer_member']}/impersonate",
            data={
                "csrf_token": admin_csrf_token(page.text),
                "submission_nonce": match.group(1),
            },
        )
        assert resp.status_code == 303
        assert world.calls == [("HardUser", world.ids["customer"])]


@pytest.mark.asyncio
async def test_audit_failure_runs_no_handler() -> None:
    def register(site: AdminSite, record: Any) -> None:
        """Register the user admin with the failing audit table.

        Args:
            site (AdminSite): The site.
            record (Any): The handler.
        """
        site.register(
            AdminModel(HardUser, impersonate=record, audit_model=HardBrokenAudit)
        )

    async for world in _world(register):
        with pytest.raises(IntegrityError):
            await _post(world, USERS, world.ids["customer"])
        assert world.calls == []


@pytest.mark.asyncio
async def test_inactive_admin_is_still_an_admin_identity() -> None:
    db = AsyncDatabaseManager("sqlite+aiosqlite:///:memory:")
    await db.connect()
    await db.create_tables()
    async with db.get_session_context() as session:
        ghost = HardUser(
            email="ghost@example.com",
            hashed_password="",
            is_admin=True,
            is_active=False,
        )
        session.add(ghost)
        await session.commit()
        backend = UserModelAuthBackend(HardUser)
        assert await backend.load_principal(session, str(ghost.id)) is None
        assert await backend.is_admin_identity(session, str(ghost.id)) is True
        assert await backend.is_admin_identity(session, str(uuid.uuid4())) is False
        assert await backend.is_admin_identity(session, "not-a-uuid") is False
    await db.drop_tables()
    await db.disconnect()


def test_token_actor_reads_the_act_claim() -> None:
    from tempest_fastapi_sdk import ACT_CLAIM, token_actor

    assert ACT_CLAIM == "act"
    assert token_actor({"sub": "u", "act": {"sub": "op"}}) == "op"
    assert token_actor({"sub": "u"}) is None
    assert token_actor({"sub": "u", "act": "op"}) == "unknown"
    assert token_actor({"sub": "u", "act": {"sub": ""}}) == "unknown"


@pytest.mark.asyncio
async def test_sensitive_route_refuses_the_impersonation_token() -> None:
    from fastapi import Depends

    from tempest_fastapi_sdk import (
        ForbiddenException,
        JWTUtils,
        make_bearer_token_dependency,
        token_actor,
    )
    from tempest_fastapi_sdk.admin import make_jwt_impersonation_handler

    jwt = JWTUtils("t" * 48)
    bearer = make_bearer_token_dependency(jwt)
    operator = HardUser(id=uuid.uuid4(), email="op@example.com", hashed_password="")
    target = HardUser(id=uuid.uuid4(), email="cu@example.com", hashed_password="")
    token = make_jwt_impersonation_handler(jwt, redirect_url="{token}")(
        operator, target
    )
    assert isinstance(token, str)
    own = jwt.encode({"sub": str(target.id), "typ": "access"})

    async def own_session_only(
        claims: dict[str, Any] | None = Depends(bearer),
    ) -> dict[str, Any]:
        """Refuse an impersonated session.

        Args:
            claims (dict[str, Any] | None): The decoded token.

        Returns:
            dict[str, Any]: The claims.

        Raises:
            ForbiddenException: When ``act`` names an actor.
        """
        assert claims is not None
        if token_actor(claims) is not None:
            raise ForbiddenException(message="Not while impersonating")
        return claims

    app = FastAPI()

    @app.post("/password")
    async def change_password(
        claims: dict[str, Any] = Depends(own_session_only),
    ) -> dict[str, str]:
        """Pretend to change the password.

        Args:
            claims (dict[str, Any]): The caller's claims.

        Returns:
            dict[str, str]: An acknowledgement.
        """
        return {"ok": str(claims["sub"])}

    from tempest_fastapi_sdk import register_exception_handlers

    register_exception_handlers(app)
    async with AsyncClient(
        transport=ASGITransport(app=app), base_url="http://test"
    ) as client:
        impersonated = await client.post(
            "/password", headers={"Authorization": f"Bearer {token}"}
        )
        mine = await client.post(
            "/password", headers={"Authorization": f"Bearer {own}"}
        )
    assert impersonated.status_code == 403
    assert mine.status_code == 200
