"""End-to-end tests for admin CSV import."""

from __future__ import annotations

import re
import secrets
import time
from collections.abc import AsyncIterator

import httpx
import itsdangerous
import pytest
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient
from sqlalchemy import Integer, String
from sqlalchemy.orm import Mapped, mapped_column

from tempest_fastapi_sdk import (
    AdminModel,
    AdminSite,
    AsyncDatabaseManager,
    BaseModel,
    BaseRepository,
    BaseUserModel,
    UserModelAuthBackend,
    make_admin_router,
)
from tempest_fastapi_sdk.admin import (
    ADMIN_IMPORT_MAX_BYTES,
    ADMIN_IMPORT_PREVIEW_MAX_AGE_SECONDS,
)
from tempest_fastapi_sdk.admin.csv_import import sign_import_payload
from tempest_fastapi_sdk.db.signals import RepositorySignalEnum, connect, disconnect
from tempest_fastapi_sdk.testing import admin_login


class ImportUser(BaseUserModel):
    __tablename__ = "admin_import_users"


class Gadget(BaseModel):
    __tablename__ = "admin_import_gadget"
    name: Mapped[str] = mapped_column(String(64), nullable=False)
    qty: Mapped[int] = mapped_column(Integer, nullable=False, default=0)


SECRET = "x" * 48
_SLUG = Gadget.__tablename__


@pytest.fixture
async def app_import() -> AsyncIterator[tuple[FastAPI, AsyncDatabaseManager]]:
    db = AsyncDatabaseManager("sqlite+aiosqlite:///:memory:")
    await db.connect()
    await db.create_tables()
    async with db.get_session_context() as session:
        user = ImportUser(email="root@example.com", hashed_password="", is_admin=True)
        user.set_password("hunter2")
        session.add(user)
        await session.commit()

    site = AdminSite(title="Import Admin")
    site.register(AdminModel(model=Gadget, can_import=True))
    app = FastAPI()
    app.include_router(
        make_admin_router(
            site,
            db=db,
            auth_backend=UserModelAuthBackend(ImportUser),
            secret_key=SECRET,
            cookie_secure=False,
        )
    )
    yield app, db
    await db.drop_tables()
    await db.disconnect()


def _client(app: FastAPI) -> AsyncClient:
    return AsyncClient(transport=ASGITransport(app=app), base_url="http://test")


async def _login_csrf(client: AsyncClient) -> str:
    await admin_login(client, "root@example.com", "hunter2")
    page = await client.get(f"/admin/m/{_SLUG}/import")
    match = re.search(r'name="csrf_token" value="([^"]+)"', page.text)
    assert match is not None
    return match.group(1)


@pytest.mark.asyncio
async def test_import_page_renders(
    app_import: tuple[FastAPI, AsyncDatabaseManager],
) -> None:
    app, _db = app_import
    async with _client(app) as client:
        await admin_login(client, "root@example.com", "hunter2")
        page = await client.get(f"/admin/m/{_SLUG}/import")
    assert page.status_code == 200
    assert "Expected header columns" in page.text
    assert "name" in page.text


PAYLOAD = re.compile(r'name="payload" value="([^"]+)"')
NONCE = re.compile(r'name="submission_nonce" value="([^"]+)"')


async def _preview(client: AsyncClient, token: str, body: bytes) -> httpx.Response:
    """Upload a CSV to the preview step.

    Args:
        client (AsyncClient): The signed-in client.
        token (str): The CSRF token.
        body (bytes): The CSV bytes.

    Returns:
        httpx.Response: The preview page.
    """
    return await client.post(
        f"/admin/m/{_SLUG}/import",
        data={"csrf_token": token},
        files={"file": ("gadgets.csv", body, "text/csv")},
    )


def _confirm_fields(page: str) -> dict[str, str]:
    """Read the signed payload and the nonce off a preview.

    Args:
        page (str): The preview HTML.

    Returns:
        dict[str, str]: ``payload`` and ``submission_nonce``.
    """
    payload = PAYLOAD.search(page)
    nonce = NONCE.search(page)
    assert payload is not None and nonce is not None
    return {"payload": payload.group(1), "submission_nonce": nonce.group(1)}


async def _gadgets(db: AsyncDatabaseManager) -> list[Gadget]:
    """Load every stored gadget.

    Args:
        db (AsyncDatabaseManager): The database.

    Returns:
        list[Gadget]: The rows, by name.
    """
    async with db.get_session_context() as session:
        repo: BaseRepository[Gadget] = BaseRepository(session, model=Gadget)
        return await repo.list(order_by=Gadget.name)


@pytest.mark.asyncio
async def test_preview_writes_nothing_and_confirm_creates_rows(
    app_import: tuple[FastAPI, AsyncDatabaseManager],
) -> None:
    app, db = app_import
    csv_body = b"name,qty\nAlpha,3\nBeta,7\n"
    async with _client(app) as client:
        token = await _login_csrf(client)
        preview = await _preview(client, token, csv_body)
        assert preview.status_code == 200
        assert "2 of 2 row(s) will be imported." in preview.text
        assert "Alpha" in preview.text and "Beta" in preview.text
        assert "Confirm import of 2 records" in preview.text
        assert await _gadgets(db) == []
        confirm = await client.post(
            f"/admin/m/{_SLUG}/import/confirm",
            data={"csrf_token": token, **_confirm_fields(preview.text)},
        )
        assert confirm.status_code == 303
        listing = await client.get(confirm.headers["location"])
    assert "Created 2 record(s)." in listing.text
    rows = await _gadgets(db)
    assert {r.name for r in rows} == {"Alpha", "Beta"}
    assert {r.qty for r in rows} == {3, 7}


@pytest.mark.asyncio
async def test_row_error_blocks_the_confirm(
    app_import: tuple[FastAPI, AsyncDatabaseManager],
) -> None:
    app, db = app_import
    csv_body = b"name,qty\nGood,1\n,2\n"
    async with _client(app) as client:
        token = await _login_csrf(client)
        preview = await _preview(client, token, csv_body)
    assert preview.status_code == 200
    assert "1 of 2 row(s) will be imported." in preview.text
    assert "1 row refused:" in preview.text
    assert "<td>3</td>" in preview.text
    assert "This field is required." in preview.text
    assert PAYLOAD.search(preview.text) is None
    assert "disabled>Confirm import of 1 record</button>" in preview.text
    assert await _gadgets(db) == []


@pytest.mark.asyncio
async def test_confirm_refuses_a_tampered_payload(
    app_import: tuple[FastAPI, AsyncDatabaseManager],
) -> None:
    app, db = app_import
    async with _client(app) as client:
        token = await _login_csrf(client)
        preview = await _preview(client, token, b"name,qty\nAlpha,3\n")
        fields = _confirm_fields(preview.text)
        fields["payload"] = fields["payload"][:-2] + (
            "AA" if not fields["payload"].endswith("AA") else "BB"
        )
        resp = await client.post(
            f"/admin/m/{_SLUG}/import/confirm",
            data={"csrf_token": token, **fields},
        )
    assert resp.status_code == 400
    assert "This preview expired or belongs to another import" in resp.text
    assert await _gadgets(db) == []


@pytest.mark.asyncio
async def test_confirm_refuses_a_payload_signed_for_another_model(
    app_import: tuple[FastAPI, AsyncDatabaseManager],
) -> None:
    app, db = app_import
    forged = sign_import_payload(
        SECRET, slug="another_table", principal_id="whoever", text="name\nX\n"
    )
    async with _client(app) as client:
        token = await _login_csrf(client)
        resp = await client.post(
            f"/admin/m/{_SLUG}/import/confirm",
            data={"csrf_token": token, "payload": forged, "submission_nonce": "n"},
        )
    assert resp.status_code == 400
    assert await _gadgets(db) == []


@pytest.mark.asyncio
async def test_resubmitting_the_confirm_does_not_import_twice(
    app_import: tuple[FastAPI, AsyncDatabaseManager],
) -> None:
    app, db = app_import
    async with _client(app) as client:
        token = await _login_csrf(client)
        preview = await _preview(client, token, b"name,qty\nAlpha,3\n")
        fields = {"csrf_token": token, **_confirm_fields(preview.text)}
        first = await client.post(f"/admin/m/{_SLUG}/import/confirm", data=fields)
        second = await client.post(f"/admin/m/{_SLUG}/import/confirm", data=fields)
    assert first.status_code == 303
    assert second.status_code == 303
    assert "already+submitted" in second.headers["location"]
    assert [r.name for r in await _gadgets(db)] == ["Alpha"]


@pytest.mark.asyncio
async def test_too_large_file_is_refused_before_parsing(
    app_import: tuple[FastAPI, AsyncDatabaseManager],
) -> None:
    app, db = app_import
    body = b"name,qty\n" + b"x,1\n" * (ADMIN_IMPORT_MAX_BYTES // 4 + 1)
    async with _client(app) as client:
        token = await _login_csrf(client)
        resp = await _preview(client, token, body)
    assert resp.status_code == 400
    assert "The file is larger than 512 KiB" in resp.text
    assert await _gadgets(db) == []


@pytest.mark.asyncio
async def test_largest_accepted_file_round_trips_through_the_confirm(
    app_import: tuple[FastAPI, AsyncDatabaseManager],
) -> None:
    app, db = app_import
    header = b"name,qty\n"
    rows: list[bytes] = []
    size = len(header)
    index = 0
    while True:
        line = f"{secrets.token_hex(24)},{index}\n".encode()
        if size + len(line) > ADMIN_IMPORT_MAX_BYTES:
            break
        rows.append(line)
        size += len(line)
        index += 1
    body = header + b"".join(rows)
    async with _client(app) as client:
        token = await _login_csrf(client)
        preview = await _preview(client, token, body)
        assert preview.status_code == 200
        fields = _confirm_fields(preview.text)
        confirm = await client.post(
            f"/admin/m/{_SLUG}/import/confirm",
            data={"csrf_token": token, **fields},
        )
    assert len(body) <= ADMIN_IMPORT_MAX_BYTES
    assert len(fields["payload"]) < 1024 * 1024
    assert confirm.status_code == 303
    assert len(await _gadgets(db)) == len(rows)


@pytest.mark.asyncio
async def test_preview_fires_no_repository_signal(
    app_import: tuple[FastAPI, AsyncDatabaseManager],
) -> None:
    app, _db = app_import
    seen: list[str] = []

    def record(instance: Gadget) -> None:
        """Record which gadget a signal fired for.

        Args:
            instance (Gadget): The saved row.
        """
        seen.append(instance.name)

    connect(Gadget, RepositorySignalEnum.POST_SAVE, record)
    try:
        async with _client(app) as client:
            token = await _login_csrf(client)
            preview = await _preview(client, token, b"name,qty\nAlpha,3\n")
            assert seen == []
            await client.post(
                f"/admin/m/{_SLUG}/import/confirm",
                data={"csrf_token": token, **_confirm_fields(preview.text)},
            )
    finally:
        disconnect(Gadget, RepositorySignalEnum.POST_SAVE, record)
    assert seen == ["Alpha"]


@pytest.mark.asyncio
async def test_import_disabled_404(
    app_import: tuple[FastAPI, AsyncDatabaseManager],
) -> None:
    # A fresh site where the model has no can_import.
    db = AsyncDatabaseManager("sqlite+aiosqlite:///:memory:")
    await db.connect()
    await db.create_tables()
    async with db.get_session_context() as session:
        user = ImportUser(email="a@b.com", hashed_password="", is_admin=True)
        user.set_password("pw")
        session.add(user)
        await session.commit()
    site = AdminSite(title="No Import")
    site.register(AdminModel(model=Gadget))  # can_import defaults False
    app = FastAPI()
    app.include_router(
        make_admin_router(
            site,
            db=db,
            auth_backend=UserModelAuthBackend(ImportUser),
            secret_key=SECRET,
            cookie_secure=False,
        )
    )
    async with _client(app) as client:
        await admin_login(client, "a@b.com", "pw")
        resp = await client.get(f"/admin/m/{_SLUG}/import")
    assert resp.status_code == 404
    await db.drop_tables()
    await db.disconnect()


@pytest.mark.asyncio
async def test_confirm_refuses_an_expired_preview(
    app_import: tuple[FastAPI, AsyncDatabaseManager],
) -> None:
    app, db = app_import

    class Backdated(itsdangerous.TimestampSigner):
        """Signs as if the preview had been rendered just over an hour ago."""

        def get_timestamp(self) -> int:
            """Return a timestamp past the preview's max age.

            Returns:
                int: Seconds since the epoch, one past the limit.
            """
            return int(time.time()) - ADMIN_IMPORT_PREVIEW_MAX_AGE_SECONDS - 1

    async with _client(app) as client:
        token = await _login_csrf(client)
        preview = await _preview(client, token, b"name,qty\nAlpha,3\n")
        fields = _confirm_fields(preview.text)
        fresh = itsdangerous.URLSafeTimedSerializer(
            SECRET, salt="tempest-admin-import"
        ).loads(fields["payload"])
        fields["payload"] = itsdangerous.URLSafeTimedSerializer(
            SECRET, salt="tempest-admin-import", signer=Backdated
        ).dumps(fresh)
        resp = await client.post(
            f"/admin/m/{_SLUG}/import/confirm",
            data={"csrf_token": token, **fields},
        )
    assert resp.status_code == 400
    assert "This preview expired" in resp.text
    assert await _gadgets(db) == []
