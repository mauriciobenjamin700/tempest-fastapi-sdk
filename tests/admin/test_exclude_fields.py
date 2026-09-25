"""``AdminModel(exclude_fields=...)`` keeps a credential off every surface.

Issue #297: ``readonly_fields`` locks the input and still prints the
value in the detail view, so a push subscription's ``endpoint`` /
``p256dh`` / ``auth``, a device ``push_token`` or a ``totp_secret`` was
readable by every admin. Each test below names one surface and asserts
the stored value never appears in it.
"""

from __future__ import annotations

import re
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from dataclasses import dataclass
from typing import ClassVar
from uuid import UUID

import pytest
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient
from sqlalchemy import ForeignKey, String, Uuid, select
from sqlalchemy.orm import Mapped, mapped_column

from tempest_fastapi_sdk import (
    AdminModel,
    AdminSite,
    AsyncDatabaseManager,
    BaseAuditLogModel,
    BaseModel,
    BaseRepository,
    BaseUserModel,
    Inline,
    Lens,
    UserModelAuthBackend,
    make_admin_router,
)
from tempest_fastapi_sdk.admin.forms import fk_label
from tempest_fastapi_sdk.db.audit import AUDIT_REDACTED, snapshot_model


class ExcludeUser(BaseUserModel):
    __tablename__ = "admin_exclude_users"

    totp_secret: Mapped[str | None] = mapped_column(String(64), nullable=True)
    push_token: Mapped[str | None] = mapped_column(String(128), nullable=True)


class PushDevice(BaseModel):
    __tablename__ = "admin_exclude_devices"
    __audit_redact__: ClassVar[frozenset[str]] = frozenset(
        {"name", "endpoint", "p256dh", "auth"}
    )

    name: Mapped[str | None] = mapped_column(String(64), nullable=True)
    platform: Mapped[str] = mapped_column(String(16), nullable=False)
    endpoint: Mapped[str] = mapped_column(String(256), nullable=False)
    p256dh: Mapped[str] = mapped_column(String(128), nullable=False)
    auth: Mapped[str] = mapped_column(String(64), nullable=False)
    user_id: Mapped[UUID] = mapped_column(
        Uuid(), ForeignKey("admin_exclude_users.id"), nullable=False
    )


class ApiKey(BaseModel):
    __tablename__ = "admin_exclude_api_keys"

    name: Mapped[str] = mapped_column(String(64), nullable=False)
    secret: Mapped[str | None] = mapped_column(String(64), nullable=True)


class PushDeviceAuditLog(BaseAuditLogModel):
    __tablename__ = "admin_exclude_devices_log"


TOTP = "TOTPSECRETJBSWY3DPEHPK3PXP"
PUSH_TOKEN = "fcm-token-7f3a9c21"
ENDPOINT = "https://push.example.com/send/endpoint-4b8e"
P256DH = "BNcRdreALRFXTkOOUHK1EtK2wtaz5Ry4YfYCA"
AUTH = "tBHItJI5svbpez7KI4CCXg"
ROTATED_ENDPOINT = "https://push.example.com/send/rotated-9d1f"
SECRETS: tuple[str, ...] = (TOTP, PUSH_TOKEN, ENDPOINT, P256DH, AUTH, ROTATED_ENDPOINT)
SESSION_KEY = "x" * 48
USERS = ExcludeUser.__tablename__
DEVICES = PushDevice.__tablename__
KEYS = ApiKey.__tablename__


def _user_admin() -> AdminModel[ExcludeUser]:
    """Return the user admin, hiding the two credentials.

    Returns:
        AdminModel[ExcludeUser]: The configuration.
    """
    return AdminModel(
        model=ExcludeUser,
        password_fields=[ExcludeUser.hashed_password],
        exclude_fields=[ExcludeUser.totp_secret, ExcludeUser.push_token],
        inlines=[
            Inline(
                PushDevice,
                PushDevice.user_id,
                list_display=[PushDevice.platform, PushDevice.endpoint],
            )
        ],
    )


def _device_admin() -> AdminModel[PushDevice]:
    """Return the device admin, hiding the subscription keys.

    Returns:
        AdminModel[PushDevice]: The configuration.
    """
    return AdminModel(
        model=PushDevice,
        exclude_fields=[
            PushDevice.name,
            PushDevice.endpoint,
            PushDevice.p256dh,
            PushDevice.auth,
        ],
        can_create=False,
        audit_model=PushDeviceAuditLog,
    )


@dataclass
class Panel:
    """What a test needs from the running panel."""

    app: FastAPI
    db: AsyncDatabaseManager
    user_id: str
    device_id: str


@pytest.fixture
async def panel() -> AsyncIterator[Panel]:
    """Yield a panel with one user and one audited device.

    Yields:
        Panel: The app, the database and the two row ids.
    """
    db = AsyncDatabaseManager("sqlite+aiosqlite:///:memory:")
    await db.connect()
    await db.create_tables()
    async with db.get_session_context() as session:
        user = ExcludeUser(
            email="root@example.com",
            hashed_password="",
            is_admin=True,
            totp_secret=TOTP,
            push_token=PUSH_TOKEN,
        )
        user.set_password("hunter2hunter2")
        session.add(user)
        await session.commit()
        await session.refresh(user)
        repository: BaseRepository[PushDevice] = BaseRepository(
            session,
            model=PushDevice,
            audit_model=PushDeviceAuditLog,
        )
        device = await repository.add_audited(
            PushDevice(
                name="Pixel",
                platform="web",
                endpoint=ENDPOINT,
                p256dh=P256DH,
                auth=AUTH,
                user_id=user.id,
            ),
            actor="root@example.com",
        )
        before = snapshot_model(device)
        device.endpoint = ROTATED_ENDPOINT
        await repository.update_audited(device, before, actor="root@example.com")
        user_id, device_id = str(user.id), str(device.id)

    site = AdminSite(title="Exclude Admin")
    site.register(_user_admin())
    site.register(_device_admin())
    site.register(
        AdminModel(model=ApiKey, exclude_fields=[ApiKey.secret], can_import=True)
    )
    app = FastAPI()
    app.include_router(
        make_admin_router(
            site,
            db=db,
            auth_backend=UserModelAuthBackend(ExcludeUser),
            secret_key=SESSION_KEY,
            cookie_secure=False,
        ),
    )
    yield Panel(app=app, db=db, user_id=user_id, device_id=device_id)
    await db.drop_tables()
    await db.disconnect()


@asynccontextmanager
async def _logged_in(app: FastAPI) -> AsyncIterator[AsyncClient]:
    """Yield a client already past the login form.

    Args:
        app (FastAPI): The application.

    Yields:
        AsyncClient: The authenticated client.
    """
    async with AsyncClient(
        transport=ASGITransport(app=app), base_url="http://test"
    ) as client:
        await client.post(
            "/admin/login",
            data={"identifier": "root@example.com", "password": "hunter2hunter2"},
        )
        yield client


def _assert_hidden(text: str) -> None:
    """Assert no stored credential appears in ``text``.

    Args:
        text (str): A rendered page or exported file.
    """
    leaked = [secret for secret in SECRETS if secret in text]
    assert leaked == []


class TestConstruction:
    def test_hidden_field_names(self) -> None:
        assert _user_admin().hidden_field_names() == {
            "hashed_password",
            "totp_secret",
            "push_token",
        }

    def test_unknown_column_raises(self) -> None:
        with pytest.raises(ValueError, match="does not have: nope"):
            AdminModel(model=ExcludeUser, exclude_fields=["nope"])

    @pytest.mark.parametrize(
        ("option", "value"),
        [
            ("list_display", [ExcludeUser.email, ExcludeUser.totp_secret]),
            ("list_filter", [ExcludeUser.totp_secret]),
            ("search_fields", [ExcludeUser.totp_secret]),
            ("readonly_fields", [ExcludeUser.totp_secret]),
            ("ordering", ExcludeUser.totp_secret),
            ("lenses", [Lens("with totp", filters={"totp_secret__ne": None})]),
            ("lenses", [Lens("by totp", order_by="-totp_secret")]),
        ],
    )
    def test_clash_with_another_option_raises(
        self,
        option: str,
        value: object,
    ) -> None:
        options: dict[str, object] = {option: value}
        with pytest.raises(ValueError, match=f"`{option.rstrip('s')}"):
            AdminModel(
                model=ExcludeUser,
                exclude_fields=[ExcludeUser.totp_secret],
                **options,  # type: ignore[arg-type]
            )

    def test_identity_field_clash_raises(self) -> None:
        with pytest.raises(ValueError, match="`identity_field`"):
            AdminModel(model=ExcludeUser, exclude_fields=["id"], can_create=False)

    def test_required_column_with_create_raises(self) -> None:
        with pytest.raises(ValueError, match=r"endpoint.*NOT NULL"):
            AdminModel(model=PushDevice, exclude_fields=[PushDevice.endpoint])

    def test_required_column_without_create_is_accepted(self) -> None:
        admin = AdminModel(
            model=PushDevice,
            exclude_fields=[PushDevice.endpoint],
            can_create=False,
        )

        assert "endpoint" not in admin.editable_field_names()

    def test_default_list_and_form_drop_the_columns(self) -> None:
        admin = _user_admin()

        assert "totp_secret" not in admin.resolved_list_display()
        assert "push_token" not in admin.editable_field_names()
        assert "hashed_password" in admin.editable_field_names()

    def test_audited_model_must_redact_the_column(self) -> None:
        with pytest.raises(ValueError, match=r"ExcludeUser.__audit_redact__"):
            AdminModel(
                model=ExcludeUser,
                exclude_fields=[ExcludeUser.totp_secret],
                audit_model=PushDeviceAuditLog,
            )

    def test_fk_label_skips_a_hidden_display_attribute(self) -> None:
        device = PushDevice(name="Pixel", platform="web")

        label = fk_label(_device_admin(), device)

        assert label != "Pixel"


class TestSurfaces:
    async def test_list(self, panel: Panel) -> None:
        async with _logged_in(panel.app) as client:
            users = await client.get(f"/admin/m/{USERS}/")
            devices = await client.get(f"/admin/m/{DEVICES}/?sort=endpoint")

        assert users.status_code == devices.status_code == 200
        _assert_hidden(users.text + devices.text)

    async def test_detail(self, panel: Panel) -> None:
        async with _logged_in(panel.app) as client:
            page = await client.get(f"/admin/m/{USERS}/{panel.user_id}")

        assert page.status_code == 200
        assert "root@example.com" in page.text
        _assert_hidden(page.text)
        assert "totp_secret" not in page.text

    async def test_inline_table_on_the_parent(self, panel: Panel) -> None:
        async with _logged_in(panel.app) as client:
            page = await client.get(f"/admin/m/{USERS}/{panel.user_id}")

        assert "web" in page.text
        _assert_hidden(page.text)

    async def test_audit_timeline(self, panel: Panel) -> None:
        async with _logged_in(panel.app) as client:
            page = await client.get(f"/admin/m/{DEVICES}/{panel.device_id}")

        assert page.status_code == 200
        assert "platform" in page.text
        _assert_hidden(page.text)

    async def test_edit_form(self, panel: Panel) -> None:
        async with _logged_in(panel.app) as client:
            page = await client.get(f"/admin/m/{USERS}/{panel.user_id}/edit")

        assert page.status_code == 200
        assert 'name="totp_secret"' not in page.text
        _assert_hidden(page.text)

    async def test_edit_submit_cannot_write_the_column(self, panel: Panel) -> None:
        async with _logged_in(panel.app) as client:
            page = await client.get(f"/admin/m/{USERS}/{panel.user_id}/edit")
            match = re.search(r'name="csrf_token" value="([^"]+)"', page.text)
            assert match is not None
            response = await client.post(
                f"/admin/m/{USERS}/{panel.user_id}/edit",
                data={
                    "csrf_token": match.group(1),
                    "email": "root@example.com",
                    "is_admin": "on",
                    "is_active": "on",
                    "totp_secret": "ATTACKER",
                },
            )

        assert response.status_code in (200, 303)
        async with panel.db.get_session_context() as session:
            stored = (
                await session.execute(
                    select(ExcludeUser.totp_secret).where(
                        ExcludeUser.email == "root@example.com"
                    )
                )
            ).scalar_one()
        assert stored == TOTP

    @pytest.mark.parametrize("fmt", ["csv", "json"])
    async def test_export(self, panel: Panel, fmt: str) -> None:
        async with _logged_in(panel.app) as client:
            users = await client.get(f"/admin/m/{USERS}/export.{fmt}")
            devices = await client.get(f"/admin/m/{DEVICES}/export.{fmt}")

        assert users.status_code == devices.status_code == 200
        assert "root@example.com" in users.text
        _assert_hidden(users.text + devices.text)

    async def test_csv_import_ignores_the_column(self, panel: Panel) -> None:
        async with _logged_in(panel.app) as client:
            page = await client.get(f"/admin/m/{KEYS}/import")
            match = re.search(r'name="csrf_token" value="([^"]+)"', page.text)
            assert match is not None
            response = await client.post(
                f"/admin/m/{KEYS}/import",
                data={"csrf_token": match.group(1)},
                files={"file": ("keys.csv", b"name,secret\nci,IMPORTED\n", "text/csv")},
            )

        assert "secret" not in page.text
        assert "Created 1 record(s)." in response.text
        async with panel.db.get_session_context() as session:
            stored = (await session.execute(select(ApiKey.secret))).scalar_one()
        assert stored is None


class TestAuditStorage:
    """The audit table itself, not only the timeline that reads it."""

    async def test_rows_store_the_marker_not_the_value(self, panel: Panel) -> None:
        async with panel.db.get_session_context() as session:
            entries = (
                (
                    await session.execute(
                        select(PushDeviceAuditLog).order_by(
                            PushDeviceAuditLog.created_at
                        )
                    )
                )
                .scalars()
                .all()
            )

        assert [entry.action for entry in entries] == ["create", "update"]
        created, updated = entries
        assert created.changes["after"]["endpoint"] == AUDIT_REDACTED
        assert created.changes["after"]["platform"] == "web"
        assert updated.changes == {
            "endpoint": {"before": AUDIT_REDACTED, "after": AUDIT_REDACTED}
        }
        _assert_hidden(repr([entry.changes for entry in entries]))
