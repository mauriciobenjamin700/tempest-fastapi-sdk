"""``AdminModel(impersonate=...)``: permission, confirmation, refusal, audit."""

from __future__ import annotations

import logging
import re
import uuid
from collections.abc import AsyncIterator, Callable
from dataclasses import dataclass, field
from datetime import timedelta
from typing import Any

import pytest
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient
from sqlalchemy import select

from tempest_fastapi_sdk import (
    AdminModel,
    AdminSite,
    AsyncDatabaseManager,
    BaseAuditLogModel,
    BaseUserModel,
    JWTUtils,
    UserModelAuthBackend,
    make_admin_router,
)
from tempest_fastapi_sdk.admin import (
    ADMIN_IMPERSONATION_EVENT,
    ADMIN_IMPERSONATION_TTL,
    AdminImpersonateHandler,
    AdminPermissionEnum,
    make_jwt_impersonation_handler,
)
from tempest_fastapi_sdk.db.audit import AuditRequestMixin
from tempest_fastapi_sdk.exceptions import ConflictException
from tempest_fastapi_sdk.testing import admin_csrf_token, admin_login
from tempest_fastapi_sdk.utils import ACCESS_TOKEN_TYPE, token_type_allowed

SECRET = "i" * 48
JWT_SECRET = "j" * 48


class ImpUser(BaseUserModel):
    """Accounts of the impersonation tests: operators and customers."""

    __tablename__ = "admin_imp_users"


class ImpAudit(AuditRequestMixin, BaseAuditLogModel):
    """Audit table with the event / IP / user-agent columns."""

    __tablename__ = "admin_imp_audit"


USERS = f"/admin/m/{ImpUser.__tablename__}"
NONCE = re.compile(r'name="submission_nonce" value="([^"]+)"')


@dataclass
class ImpWorld:
    """A signed-in operator, the seeded accounts and the handler's calls."""

    client: AsyncClient
    db: AsyncDatabaseManager
    ids: dict[str, str]
    calls: list[tuple[str, str]] = field(default_factory=list)


def grant_impersonation(user: Any, admin: Any, action: AdminPermissionEnum) -> bool:
    """Grant every permission, ``IMPERSONATE`` included.

    Args:
        user (Any): The principal.
        admin (Any): The admin asked about.
        action (AdminPermissionEnum): The permission.

    Returns:
        bool: Always ``True``.
    """
    return True


def deny_impersonation(user: Any, admin: Any, action: AdminPermissionEnum) -> bool:
    """Grant everything but ``IMPERSONATE``.

    Args:
        user (Any): The principal.
        admin (Any): The admin asked about.
        action (AdminPermissionEnum): The permission.

    Returns:
        bool: ``False`` only for ``IMPERSONATE``.
    """
    return action is not AdminPermissionEnum.IMPERSONATE


async def _open(
    *,
    policy: Callable[[Any, Any, AdminPermissionEnum], bool] | None,
    handler: Callable[[ImpWorld], AdminImpersonateHandler] | None = None,
) -> AsyncIterator[ImpWorld]:
    """Seed the accounts, mount the admin and sign the operator in.

    Args:
        policy (Callable | None): The ``access_policy``.
        handler (Callable[[ImpWorld], AdminImpersonateHandler] | None):
            Builds the handler from the world; ``None`` records the call and
            returns a fixed URL.

    Yields:
        ImpWorld: The signed-in world.
    """
    db = AsyncDatabaseManager("sqlite+aiosqlite:///:memory:")
    await db.connect()
    await db.create_tables()
    async with db.get_session_context() as session:
        operator = ImpUser(email="op@example.com", hashed_password="", is_admin=True)
        operator.set_password("hunter2")
        other_admin = ImpUser(
            email="boss@example.com", hashed_password="", is_admin=True
        )
        customer = ImpUser(email="cu@example.com", hashed_password="")
        session.add_all([operator, other_admin, customer])
        await session.commit()
        ids = {
            "operator": str(operator.id),
            "admin": str(other_admin.id),
            "customer": str(customer.id),
        }
    world = ImpWorld(client=None, db=db, ids=ids)  # type: ignore[arg-type]

    def record(operator: Any, target: Any) -> str:
        """Record the call and return a fixed URL.

        Args:
            operator (Any): The signed-in admin.
            target (Any): The row to act as.

        Returns:
            str: The redirect URL.
        """
        world.calls.append((str(operator.id), str(target.id)))
        return f"https://app.example/enter?token=SECRET-{target.id}"

    site = AdminSite(title="Imp")
    site.register(
        AdminModel(
            ImpUser,
            search_fields=["email"],
            audit_model=ImpAudit,
            impersonate=handler(world) if handler is not None else record,
        )
    )
    app = FastAPI()
    app.include_router(
        make_admin_router(
            site,
            db=db,
            auth_backend=UserModelAuthBackend(ImpUser),
            secret_key=SECRET,
            cookie_secure=False,
            access_policy=policy,
        )
    )
    async with AsyncClient(
        transport=ASGITransport(app=app), base_url="http://test"
    ) as client:
        await admin_login(client, "op@example.com", "hunter2")
        world.client = client
        yield world
    await db.drop_tables()
    await db.disconnect()


@pytest.fixture
async def granted() -> AsyncIterator[ImpWorld]:
    """World whose policy grants ``IMPERSONATE``.

    Yields:
        ImpWorld: The signed-in world.
    """
    async for world in _open(policy=grant_impersonation):
        yield world


async def _confirm(world: ImpWorld, key: str) -> tuple[str, str]:
    """Open the confirmation page and read its CSRF token and nonce.

    Args:
        world (ImpWorld): The world.
        key (str): Which seeded account.

    Returns:
        tuple[str, str]: ``(csrf, nonce)``.
    """
    page = await world.client.get(f"{USERS}/{world.ids[key]}/impersonate")
    assert page.status_code == 200, page.text
    match = NONCE.search(page.text)
    assert match is not None
    return admin_csrf_token(page.text), match.group(1)


async def _audit_rows(world: ImpWorld) -> list[ImpAudit]:
    """Read every audit row.

    Args:
        world (ImpWorld): The world.

    Returns:
        list[ImpAudit]: The rows.
    """
    async with world.db.get_session_context() as session:
        return list((await session.execute(select(ImpAudit))).scalars().all())


@pytest.mark.asyncio
async def test_no_access_policy_never_grants_impersonation() -> None:
    async for world in _open(policy=None):
        detail = await world.client.get(f"{USERS}/{world.ids['customer']}")
        assert detail.status_code == 200
        assert "/impersonate" not in detail.text
        page = await world.client.get(f"{USERS}/{world.ids['customer']}/impersonate")
        assert page.status_code == 403
        csrf = admin_csrf_token(detail.text)
        post = await world.client.post(
            f"{USERS}/{world.ids['customer']}/impersonate",
            data={"csrf_token": csrf, "submission_nonce": "x"},
        )
        assert post.status_code == 403
        assert world.calls == []


@pytest.mark.asyncio
async def test_policy_without_impersonate_is_refused_even_with_edit() -> None:
    async for world in _open(policy=deny_impersonation):
        detail = await world.client.get(f"{USERS}/{world.ids['customer']}")
        assert "/impersonate" not in detail.text
        assert f"{USERS}/{world.ids['customer']}/edit" in detail.text
        page = await world.client.get(f"{USERS}/{world.ids['customer']}/impersonate")
        assert page.status_code == 403


@pytest.mark.asyncio
async def test_confirmed_impersonation_redirects_and_is_audited(
    granted: ImpWorld, caplog: pytest.LogCaptureFixture
) -> None:
    detail = await granted.client.get(f"{USERS}/{granted.ids['customer']}")
    assert f"{USERS}/{granted.ids['customer']}/impersonate" in detail.text
    csrf, nonce = await _confirm(granted, "customer")
    with caplog.at_level(logging.INFO, logger="tempest_fastapi_sdk.admin.router"):
        resp = await granted.client.post(
            f"{USERS}/{granted.ids['customer']}/impersonate",
            data={"csrf_token": csrf, "submission_nonce": nonce},
        )
    assert resp.status_code == 303
    assert resp.headers["location"].startswith("https://app.example/enter?token=")
    assert resp.headers["referrer-policy"] == "no-referrer"
    assert resp.headers["cache-control"] == "no-store"
    assert granted.calls == [(granted.ids["operator"], granted.ids["customer"])]
    rows = await _audit_rows(granted)
    assert len(rows) == 1
    entry = rows[0]
    assert entry.action == "event"
    assert entry.event == ADMIN_IMPERSONATION_EVENT
    assert entry.entity == "ImpUser"
    assert entry.entity_id == granted.ids["customer"]
    assert entry.actor == granted.ids["operator"]
    logged = " ".join(record.getMessage() for record in caplog.records)
    assert granted.ids["customer"] in logged
    assert "SECRET-" not in logged
    history = await granted.client.get(f"{USERS}/{granted.ids['customer']}")
    assert "logged in as" in history.text


@pytest.mark.asyncio
async def test_reload_of_the_confirmation_does_not_run_it_again(
    granted: ImpWorld,
) -> None:
    csrf, nonce = await _confirm(granted, "customer")
    data = {"csrf_token": csrf, "submission_nonce": nonce}
    first = await granted.client.post(
        f"{USERS}/{granted.ids['customer']}/impersonate", data=data
    )
    assert first.status_code == 303
    again = await granted.client.post(
        f"{USERS}/{granted.ids['customer']}/impersonate", data=data
    )
    assert again.status_code == 303
    assert again.headers["location"].startswith(f"{USERS}/?")
    assert len(granted.calls) == 1


@pytest.mark.asyncio
async def test_csrf_mismatch_is_refused(granted: ImpWorld) -> None:
    _csrf, nonce = await _confirm(granted, "customer")
    resp = await granted.client.post(
        f"{USERS}/{granted.ids['customer']}/impersonate",
        data={"csrf_token": "forged", "submission_nonce": nonce},
    )
    assert resp.status_code == 403
    assert granted.calls == []


@pytest.mark.asyncio
async def test_admin_account_cannot_be_impersonated(granted: ImpWorld) -> None:
    detail = await granted.client.get(f"{USERS}/{granted.ids['admin']}")
    assert "/impersonate" not in detail.text
    page = await granted.client.get(f"{USERS}/{granted.ids['admin']}/impersonate")
    assert page.status_code == 403
    assert "can sign into the admin itself" in page.text
    assert 'name="submission_nonce"' not in page.text
    csrf, nonce = await _confirm(granted, "customer")
    post = await granted.client.post(
        f"{USERS}/{granted.ids['admin']}/impersonate",
        data={"csrf_token": csrf, "submission_nonce": nonce},
    )
    assert post.status_code == 403
    assert granted.calls == []
    assert await _audit_rows(granted) == []


@pytest.mark.asyncio
async def test_admin_detection_follows_the_backend_override() -> None:
    class RoleBackend(UserModelAuthBackend):
        """Backend that authorizes by e-mail domain instead of ``is_admin``."""

        def is_authorized(self, user: BaseUserModel) -> bool:
            """Authorize ``@example.com`` accounts.

            Args:
                user (BaseUserModel): The account.

            Returns:
                bool: Whether the e-mail ends in ``@example.com``.
            """
            return user.email.endswith("@example.com")

    customer = ImpUser(email="cu@example.com", hashed_password="", is_admin=False)
    stranger = ImpUser(email="x@other.test", hashed_password="", is_admin=False)
    backend = RoleBackend(ImpUser)
    assert backend.is_admin_account(customer) is True
    assert backend.is_admin_account(stranger) is False
    assert UserModelAuthBackend(ImpUser).is_admin_account(customer) is False


@pytest.mark.asyncio
async def test_async_handler_and_app_exception() -> None:
    def build(world: ImpWorld) -> AdminImpersonateHandler:
        """Build an async handler that refuses the first call.

        Args:
            world (ImpWorld): The world.

        Returns:
            AdminImpersonateHandler: The handler.
        """

        async def handler(operator: Any, target: Any) -> str:
            """Refuse once, then answer.

            Args:
                operator (Any): The admin.
                target (Any): The row.

            Returns:
                str: The URL.

            Raises:
                ConflictException: On the first call.
            """
            world.calls.append((str(operator.id), str(target.id)))
            if len(world.calls) == 1:
                raise ConflictException(message="Account is locked")
            return "https://app.example/async"

        return handler

    async for world in _open(policy=grant_impersonation, handler=build):
        csrf, nonce = await _confirm(world, "customer")
        refused = await world.client.post(
            f"{USERS}/{world.ids['customer']}/impersonate",
            data={"csrf_token": csrf, "submission_nonce": nonce},
        )
        assert refused.status_code == 400
        assert "Account is locked" in refused.text
        assert await _audit_rows(world) == []
        match = NONCE.search(refused.text)
        assert match is not None
        ok = await world.client.post(
            f"{USERS}/{world.ids['customer']}/impersonate",
            data={"csrf_token": csrf, "submission_nonce": match.group(1)},
        )
        assert ok.status_code == 303
        assert ok.headers["location"] == "https://app.example/async"
        assert len(await _audit_rows(world)) == 1


@pytest.mark.asyncio
async def test_jwt_handler_signs_a_short_access_token_with_the_actor() -> None:
    jwt = JWTUtils(JWT_SECRET)
    handler = make_jwt_impersonation_handler(
        jwt, redirect_url="https://app.example/as#token={token}"
    )
    operator = ImpUser(
        id=uuid.uuid4(), email="op@example.com", hashed_password="", is_admin=True
    )
    target = ImpUser(id=uuid.uuid4(), email="cu@example.com", hashed_password="")
    url = handler(operator, target)
    assert isinstance(url, str)
    assert url.startswith("https://app.example/as#token=")
    claims = jwt.decode(url.split("#token=", 1)[1])
    assert claims["sub"] == str(target.id)
    assert claims["typ"] == ACCESS_TOKEN_TYPE
    assert claims["act"] == {"sub": str(operator.id)}
    assert claims["email"] == "cu@example.com"
    assert claims["exp"] - claims["iat"] == int(ADMIN_IMPERSONATION_TTL.total_seconds())
    assert token_type_allowed(claims, [ACCESS_TOKEN_TYPE], strict=True)


@pytest.mark.asyncio
async def test_jwt_handler_honours_ttl_and_validates_its_arguments() -> None:
    jwt = JWTUtils(JWT_SECRET)
    short = make_jwt_impersonation_handler(
        jwt, redirect_url="/{token}", ttl=timedelta(minutes=2)
    )
    operator = ImpUser(
        id=uuid.uuid4(), email="op@example.com", hashed_password="", is_admin=True
    )
    target = ImpUser(id=uuid.uuid4(), email="cu@example.com", hashed_password="")
    outcome = short(operator, target)
    assert isinstance(outcome, str)
    claims = jwt.decode(outcome.removeprefix("/"))
    assert claims["exp"] - claims["iat"] == 120
    with pytest.raises(ValueError, match="token"):
        make_jwt_impersonation_handler(jwt, redirect_url="https://app.example/")
    with pytest.raises(ValueError, match="positive"):
        make_jwt_impersonation_handler(jwt, redirect_url="/{token}", ttl=timedelta(0))


@pytest.mark.asyncio
async def test_jwt_handler_end_to_end_through_the_admin() -> None:
    jwt = JWTUtils(JWT_SECRET)

    def build(world: ImpWorld) -> AdminImpersonateHandler:
        """Return the shipped JWT handler.

        Args:
            world (ImpWorld): The world (unused).

        Returns:
            AdminImpersonateHandler: The handler.
        """
        return make_jwt_impersonation_handler(
            jwt, redirect_url="https://app.example/as#token={token}"
        )

    async for world in _open(policy=grant_impersonation, handler=build):
        csrf, nonce = await _confirm(world, "customer")
        resp = await world.client.post(
            f"{USERS}/{world.ids['customer']}/impersonate",
            data={"csrf_token": csrf, "submission_nonce": nonce},
        )
        assert resp.status_code == 303
        claims = jwt.decode(resp.headers["location"].split("#token=", 1)[1])
        assert claims["sub"] == world.ids["customer"]
        assert claims["act"] == {"sub": world.ids["operator"]}


class ImpPlainAudit(BaseAuditLogModel):
    """Audit table without :class:`AuditRequestMixin`."""

    __tablename__ = "admin_imp_plain_audit"


def test_audit_entry_without_the_request_mixin_keeps_the_event_in_context() -> None:
    from starlette.requests import Request

    from tempest_fastapi_sdk.admin.router import (
        _audit_event_name,
        _impersonation_audit_entry,
    )

    request = Request({"type": "http", "headers": [], "client": ("10.0.0.1", 1)})
    entry = _impersonation_audit_entry(
        ImpPlainAudit,
        request,
        entity="ImpUser",
        entity_id="abc",
        operator_id="op",
        operator_name="op@example.com",
        trusted_ip_header=None,
    )
    assert entry.action == "event"
    assert entry.context == {
        "event": ADMIN_IMPERSONATION_EVENT,
        "operator": "op@example.com",
    }
    assert _audit_event_name(entry) == ADMIN_IMPERSONATION_EVENT
    with_mixin = _impersonation_audit_entry(
        ImpAudit,
        request,
        entity="ImpUser",
        entity_id="abc",
        operator_id="op",
        operator_name="op@example.com",
        trusted_ip_header=None,
    )
    assert with_mixin.event == ADMIN_IMPERSONATION_EVENT
    assert with_mixin.ip == "10.0.0.1"


@pytest.mark.asyncio
async def test_jwt_handler_token_passes_the_bearer_dependency() -> None:
    from fastapi import Depends

    from tempest_fastapi_sdk import make_bearer_token_dependency

    jwt = JWTUtils(JWT_SECRET)
    handler = make_jwt_impersonation_handler(jwt, redirect_url="{token}")
    operator = ImpUser(
        id=uuid.uuid4(), email="op@example.com", hashed_password="", is_admin=True
    )
    target = ImpUser(id=uuid.uuid4(), email="cu@example.com", hashed_password="")
    token = handler(operator, target)
    assert isinstance(token, str)
    bearer = make_bearer_token_dependency(jwt)
    app = FastAPI()

    @app.get("/me")
    async def me(claims: dict[str, Any] | None = Depends(bearer)) -> dict[str, Any]:
        """Echo the decoded claims.

        Args:
            claims (dict[str, Any] | None): What the dependency decoded.

        Returns:
            dict[str, Any]: The claims.
        """
        assert claims is not None
        return claims

    async with AsyncClient(
        transport=ASGITransport(app=app), base_url="http://test"
    ) as client:
        resp = await client.get("/me", headers={"Authorization": f"Bearer {token}"})
    assert resp.status_code == 200
    assert resp.json()["act"] == {"sub": str(operator.id)}
