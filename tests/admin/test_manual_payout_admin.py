"""manual_payout_admin: attach the PDF receipt, mark paid, download it back."""

from __future__ import annotations

import re
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from dataclasses import dataclass
from pathlib import Path
from uuid import UUID, uuid4

import pytest
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient
from sqlalchemy import select

from tempest_fastapi_sdk import (
    AdminSite,
    AsyncDatabaseManager,
    BaseUserModel,
    UserModelAuthBackend,
    make_admin_router,
)
from tempest_fastapi_sdk.admin import AdminModel, manual_payout_admin
from tempest_fastapi_sdk.testing import admin_login
from tempest_fastapi_sdk.utils import LocalUploadStorage
from tempest_fastapi_sdk.wallet import ManualPayoutStatusEnum, make_manual_payout_model

SECRET: str = "x" * 48
"""Admin session secret."""

PDF: bytes = b"%PDF-1.7\n1 0 obj\n<<>>\nendobj\n%%EOF\n"
"""The smallest bytes that sniff as a PDF."""

PNG: bytes = b"\x89PNG\r\n\x1a\n" + b"\x00" * 32
"""A PNG header."""


class _AdminUser(BaseUserModel):
    __tablename__ = "manual_payout_admin_users"


_Payout = make_manual_payout_model(
    user_table="manual_payout_admin_users",
    tablename="manual_payout_admin_rows",
    class_name="_Payout",
)
_SLUG: str = "manual_payout_admin_rows"


@dataclass
class _Panel:
    """The panel and what a test inspects."""

    client: AsyncClient
    db: AsyncDatabaseManager
    admin_id: UUID
    storage: Path


@asynccontextmanager
async def _open_panel(storage: LocalUploadStorage, base: Path) -> AsyncIterator[_Panel]:
    """Open an admin panel with the preset and one signed-in administrator.

    Args:
        storage (LocalUploadStorage): Where receipts are stored.
        base (Path): The storage's directory.

    Yields:
        _Panel: The panel.
    """
    db = AsyncDatabaseManager("sqlite+aiosqlite:///:memory:")
    await db.connect()
    await db.create_tables()
    async with db.get_session_context() as session:
        user = _AdminUser(email="root@example.com", hashed_password="", is_admin=True)
        user.set_password("hunter2")
        session.add(user)
        await session.commit()
        admin_id = user.id
    site = AdminSite(title="Admin")
    site.register(manual_payout_admin(_Payout, upload_storage=storage))
    app = FastAPI()
    app.include_router(
        make_admin_router(
            site,
            db=db,
            auth_backend=UserModelAuthBackend(_AdminUser),
            secret_key=SECRET,
            cookie_secure=False,
        )
    )
    async with AsyncClient(
        transport=ASGITransport(app=app), base_url="http://test"
    ) as client:
        await admin_login(client, "root@example.com", "hunter2")
        yield _Panel(client=client, db=db, admin_id=admin_id, storage=base)
    await db.drop_tables()
    await db.disconnect()


@pytest.fixture
async def panel(tmp_path: Path) -> AsyncIterator[_Panel]:
    """The panel over local storage.

    Args:
        tmp_path (Path): Where receipts are stored.

    Yields:
        _Panel: The panel.
    """
    async with _open_panel(LocalUploadStorage(tmp_path), tmp_path) as opened:
        yield opened


async def _row(p: _Panel, status: str = ManualPayoutStatusEnum.PENDING.value) -> str:
    """Insert one manual payout row.

    Args:
        p (_Panel): The panel.
        status (str): Its status.

    Returns:
        str: Its id.
    """
    async with p.db.get_session_context() as session:
        row = _Payout(
            user_id=p.admin_id,
            amount_cents=5_000,
            pix_key_masked="mo***om",
            pix_key_type="email",
            correlation_id=f"saque-{uuid4().hex[:8]}",
            status=status,
        )
        session.add(row)
        await session.commit()
        return str(row.id)


async def _stored(p: _Panel, row_id: str) -> _Payout:  # type: ignore[valid-type]
    async with p.db.get_session_context() as session:
        result = await session.execute(
            select(_Payout).where(_Payout.id == UUID(row_id))
        )
        return result.scalar_one()


async def _token(p: _Panel, url: str) -> str:
    page = await p.client.get(url)
    match = re.search(r'name="csrf_token" value="([^"]+)"', page.text)
    assert match is not None
    return match.group(1)


async def _upload(
    p: _Panel, row_id: str, name: str, content: bytes, content_type: str
) -> int:
    """Post a receipt through the edit form.

    Returns:
        int: The status code.
    """
    url = f"/admin/m/{_SLUG}/{row_id}/edit"
    token = await _token(p, url)
    response = await p.client.post(
        url,
        data={"csrf_token": token},
        files={"receipt_key": (name, content, content_type)},
        follow_redirects=False,
    )
    return response.status_code


async def _mark_paid(p: _Panel, row_id: str) -> str:
    """Run the action on one row and return the flash text page.

    Returns:
        str: The list page after the redirect.
    """
    token = await _token(p, f"/admin/m/{_SLUG}/")
    response = await p.client.post(
        f"/admin/m/{_SLUG}/bulk",
        data={
            "csrf_token": token,
            "action": "custom:mark_manual_payout_paid",
            "ids": [row_id],
        },
        follow_redirects=False,
    )
    assert response.status_code == 303
    return (await p.client.get(response.headers["location"])).text


def test_preset_rejects_accept_on_a_non_upload_field(tmp_path: Path) -> None:
    with pytest.raises(ValueError, match="not `upload_fields`"):
        AdminModel(
            _Payout,
            upload_fields=["receipt_key"],
            upload_storage=LocalUploadStorage(tmp_path),
            upload_accept={"correlation_id": ["application/pdf"]},
        )


async def test_form_renders_accept_and_locks_the_money(panel: _Panel) -> None:
    row_id = await _row(panel)
    page = await panel.client.get(f"/admin/m/{_SLUG}/{row_id}/edit")
    assert page.status_code == 200
    assert 'name="receipt_key" accept="application/pdf"' in page.text
    assert 'name="amount_cents"' not in page.text


async def test_valid_pdf_is_stored_and_downloadable(panel: _Panel) -> None:
    row_id = await _row(panel)

    assert (
        await _upload(panel, row_id, "comprovante.pdf", PDF, "application/pdf") == 303
    )

    row = await _stored(panel, row_id)
    assert row.receipt_key is not None
    assert (panel.storage / row.receipt_key).read_bytes() == PDF
    detail = await panel.client.get(f"/admin/m/{_SLUG}/{row_id}")
    link = f"/admin/m/{_SLUG}/{row_id}/upload/receipt_key"
    assert f'href="{link}"' in detail.text
    download = await panel.client.get(link)
    assert download.status_code == 200
    assert download.content == PDF
    assert download.headers["x-content-type-options"] == "nosniff"


@pytest.mark.parametrize(
    ("name", "content", "content_type"),
    [
        ("comprovante.pdf", PNG, "application/pdf"),
        ("comprovante.png", PNG, "image/png"),
        ("comprovante.pdf", PDF, "text/plain"),
    ],
)
async def test_non_pdf_is_refused_and_nothing_is_stored(
    panel: _Panel, name: str, content: bytes, content_type: str
) -> None:
    row_id = await _row(panel)

    assert await _upload(panel, row_id, name, content, content_type) == 400

    assert (await _stored(panel, row_id)).receipt_key is None
    assert list(panel.storage.rglob("*")) == []


async def test_mark_paid_without_receipt_fails_and_keeps_pending(
    panel: _Panel,
) -> None:
    row_id = await _row(panel)

    page = await _mark_paid(panel, row_id)

    row = await _stored(panel, row_id)
    assert "sem comprovante anexado" in page
    assert row.status == ManualPayoutStatusEnum.PENDING.value
    assert row.completed_at is None


async def test_mark_paid_with_receipt_completes_once(panel: _Panel) -> None:
    row_id = await _row(panel)
    await _upload(panel, row_id, "c.pdf", PDF, "application/pdf")

    first = await _mark_paid(panel, row_id)
    done = await _stored(panel, row_id)
    second = await _mark_paid(panel, row_id)
    again = await _stored(panel, row_id)

    assert "1 marcado(s) como pago." in first
    assert done.status == ManualPayoutStatusEnum.COMPLETED.value
    assert done.completed_by == str(panel.admin_id)
    assert done.completed_at is not None
    assert "já finalizados" in second
    assert again.completed_at == done.completed_at


async def test_receipt_of_a_completed_row_cannot_change(panel: _Panel) -> None:
    row_id = await _row(panel)
    await _upload(panel, row_id, "c.pdf", PDF, "application/pdf")
    await _mark_paid(panel, row_id)
    before = (await _stored(panel, row_id)).receipt_key

    assert await _upload(panel, row_id, "outro.pdf", PDF, "application/pdf") == 400

    assert (await _stored(panel, row_id)).receipt_key == before
    assert len(list(panel.storage.rglob("*.pdf"))) == 1


class _PresignedStorage(LocalUploadStorage):
    """Local storage that mints a URL, as MinIO does."""

    async def presigned_url(self, key: str, *, expires: object = None) -> str:
        """Mint a fake temporary URL.

        Args:
            key (str): The key.
            expires (object): Ignored.

        Returns:
            str: The URL.
        """
        return f"https://storage.example.com/{key}?sig=1"


async def test_download_redirects_when_the_storage_mints_a_url(
    tmp_path: Path,
) -> None:
    async with _open_panel(_PresignedStorage(tmp_path), tmp_path) as p:
        row_id = await _row(p)
        await _upload(p, row_id, "c.pdf", PDF, "application/pdf")
        key = (await _stored(p, row_id)).receipt_key
        response = await p.client.get(
            f"/admin/m/{_SLUG}/{row_id}/upload/receipt_key", follow_redirects=False
        )
    assert response.status_code == 303
    assert response.headers["location"] == f"https://storage.example.com/{key}?sig=1"
