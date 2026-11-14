"""manual_payout_admin: attach the PDF receipt, mark paid, refuse and refund."""

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
from sqlalchemy.ext.asyncio import AsyncSession

from tempest_fastapi_sdk import (
    AdminSite,
    AsyncDatabaseManager,
    BaseRepository,
    BaseUserModel,
    UserModelAuthBackend,
    make_admin_router,
)
from tempest_fastapi_sdk.admin import (
    REJECT_ACTION,
    STATUS_LABELS,
    AdminModel,
    manual_payout_admin,
)
from tempest_fastapi_sdk.testing import admin_login
from tempest_fastapi_sdk.utils import LocalUploadStorage
from tempest_fastapi_sdk.wallet import (
    ManualPayoutHooks,
    ManualPayoutStatusEnum,
    WalletBalanceMixin,
    WalletEntryKindEnum,
    WalletLedger,
    make_manual_payout_model,
    make_wallet_entry_model,
)

SECRET: str = "x" * 48
"""Admin session secret."""

PDF: bytes = b"%PDF-1.7\n1 0 obj\n<<>>\nendobj\n%%EOF\n"
"""The smallest bytes that sniff as a PDF."""

PNG: bytes = b"\x89PNG\r\n\x1a\n" + b"\x00" * 32
"""A PNG header."""


class _AdminUser(BaseUserModel, WalletBalanceMixin):
    __tablename__ = "manual_payout_admin_users"


_AdminEntry = make_wallet_entry_model(
    user_table="manual_payout_admin_users",
    tablename="manual_payout_admin_entries",
    class_name="_AdminEntry",
)


def _ledger(session: AsyncSession) -> WalletLedger:
    return WalletLedger(
        balances=BaseRepository(session, model=_AdminUser),
        entries=BaseRepository(session, model=_AdminEntry),
    )


REJECTED_HOOK_CALLS: list[str] = []
"""Correlation ids the panel's ``on_rejected`` hook saw, in order."""


async def _on_rejected(session: AsyncSession, row: object) -> None:
    """Record the rejection, and fail for a correlation id ending in ``-boom``.

    Args:
        session (AsyncSession): The transition's session.
        row (object): The row after the transition.

    Raises:
        RuntimeError: For a ``-boom`` correlation id.
    """
    correlation_id = str(getattr(row, "correlation_id", ""))
    REJECTED_HOOK_CALLS.append(correlation_id)
    if correlation_id.endswith("-boom"):
        raise RuntimeError("hook failed")


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
    site.register(
        manual_payout_admin(
            _Payout,
            upload_storage=storage,
            ledger_factory=_ledger,
            hooks=ManualPayoutHooks(on_rejected=_on_rejected),
        )
    )
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


async def _row(
    p: _Panel,
    status: str = ManualPayoutStatusEnum.PENDING.value,
    *,
    reason: str | None = None,
    suffix: str = "",
) -> str:
    """Insert one manual payout row.

    Args:
        p (_Panel): The panel.
        status (str): Its status.
        reason (str | None): A rejection reason already typed.
        suffix (str): Appended to the correlation id.

    Returns:
        str: Its id.
    """
    async with p.db.get_session_context() as session:
        row = _Payout(
            user_id=p.admin_id,
            amount_cents=5_000,
            pix_key_masked="mo***om",
            pix_key_type="email",
            correlation_id=f"saque-{uuid4().hex[:8]}{suffix}",
            status=status,
            rejection_reason=reason,
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
    """Run the "mark as paid" action on one row.

    Returns:
        str: The list page after the redirect.
    """
    return await _run(p, "mark_manual_payout_paid", [row_id])


async def _run(p: _Panel, action: str, row_ids: list[str]) -> str:
    """Run a custom action on rows and return the flash text page.

    Returns:
        str: The list page after the redirect.
    """
    token = await _token(p, f"/admin/m/{_SLUG}/")
    response = await p.client.post(
        f"/admin/m/{_SLUG}/bulk",
        data={
            "csrf_token": token,
            "action": f"custom:{action}",
            "ids": row_ids,
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


async def test_list_offers_only_the_payout_action_and_a_status_select(
    panel: _Panel,
) -> None:
    """No activate/deactivate; the status filter is a labelled select."""
    await _row(panel)
    page = await panel.client.get(f"/admin/m/{_SLUG}/")
    assert page.status_code == 200
    assert 'value="activate"' not in page.text
    assert 'value="deactivate"' not in page.text
    assert 'value="custom:mark_manual_payout_paid"' in page.text
    assert '<select name="filter_status">' in page.text
    assert '<input type="text" name="filter_status"' not in page.text
    for value, label in STATUS_LABELS.items():
        assert f'<option value="{value}"' in page.text
        assert label in page.text


async def test_status_select_filters_the_list(panel: _Panel) -> None:
    """Choosing "Pago" lists only completed payouts."""
    await _row(panel)
    done = await _row(panel, ManualPayoutStatusEnum.COMPLETED.value)
    page = await panel.client.get(
        f"/admin/m/{_SLUG}/",
        params={"filter_status": ManualPayoutStatusEnum.COMPLETED.value},
    )
    assert page.status_code == 200
    assert "1 record" in page.text
    assert done in page.text


async def test_forged_deactivate_is_refused(panel: _Panel) -> None:
    """A hand-built ``action=deactivate`` cannot switch a payout off."""
    row_id = await _row(panel)
    token = await _token(panel, f"/admin/m/{_SLUG}/")
    response = await panel.client.post(
        f"/admin/m/{_SLUG}/bulk",
        data={"csrf_token": token, "action": "deactivate", "ids": [row_id]},
        follow_redirects=False,
    )
    assert response.status_code == 400
    row = await _stored(panel, row_id)
    assert row.is_active is True


async def test_edit_form_does_not_offer_is_active(panel: _Panel) -> None:
    """``is_active`` is read-only on a payout."""
    row_id = await _row(panel)
    page = await panel.client.get(f"/admin/m/{_SLUG}/{row_id}/edit")
    assert page.status_code == 200
    assert 'name="is_active"' not in page.text


async def _wallet(p: _Panel) -> tuple[int, int]:
    """Read the administrator's balance and how many refund lines exist.

    Returns:
        tuple[int, int]: Balance in cents and ``WITHDRAW_REFUND`` lines.
    """
    async with p.db.get_session_context() as session:
        balance = (await _ledger(session).balance(p.admin_id)).total_cents
        refunds = (
            await session.execute(
                select(_AdminEntry).where(
                    _AdminEntry.kind == WalletEntryKindEnum.WITHDRAW_REFUND.value
                )
            )
        ).all()
        return balance, len(refunds)


async def _type_reason(p: _Panel, row_id: str, reason: str) -> int:
    """Post a rejection reason through the edit form.

    Returns:
        int: The status code.
    """
    url = f"/admin/m/{_SLUG}/{row_id}/edit"
    token = await _token(p, url)
    response = await p.client.post(
        url,
        data={"csrf_token": token, "rejection_reason": reason},
        follow_redirects=False,
    )
    return response.status_code


async def test_list_offers_the_reject_action(panel: _Panel) -> None:
    await _row(panel)
    page = await panel.client.get(f"/admin/m/{_SLUG}/")
    assert f'value="custom:{REJECT_ACTION}"' in page.text
    assert "Recusar e devolver" in page.text


def test_without_a_ledger_there_is_no_reject_action(tmp_path: Path) -> None:
    config = manual_payout_admin(_Payout, upload_storage=LocalUploadStorage(tmp_path))
    assert [action.name for action in config.custom_actions()] == [
        "mark_manual_payout_paid"
    ]


async def test_reject_with_reason_refunds_and_runs_the_hook(panel: _Panel) -> None:
    REJECTED_HOOK_CALLS.clear()
    row_id = await _row(panel)
    assert await _type_reason(panel, row_id, "chave Pix de outra pessoa") == 303

    page = await _run(panel, REJECT_ACTION, [row_id])

    row = await _stored(panel, row_id)
    assert "1 recusado(s) e devolvido(s) à carteira." in page
    assert row.status == ManualPayoutStatusEnum.REJECTED.value
    assert row.rejection_reason == "chave Pix de outra pessoa"
    assert row.rejected_by == str(panel.admin_id)
    assert row.rejected_at is not None
    assert await _wallet(panel) == (5_000, 1)
    assert [row.correlation_id] == REJECTED_HOOK_CALLS


async def test_reject_without_reason_leaves_the_row(panel: _Panel) -> None:
    row_id = await _row(panel)

    page = await _run(panel, REJECT_ACTION, [row_id])

    assert "sem motivo da recusa" in page
    assert (await _stored(panel, row_id)).status == ManualPayoutStatusEnum.PENDING.value
    assert await _wallet(panel) == (0, 0)


async def test_second_reject_refunds_once(panel: _Panel) -> None:
    row_id = await _row(panel, reason="duplicado")

    await _run(panel, REJECT_ACTION, [row_id])
    second = await _run(panel, REJECT_ACTION, [row_id])

    assert "já finalizados" in second
    assert await _wallet(panel) == (5_000, 1)


async def test_rejected_row_cannot_be_marked_paid(panel: _Panel) -> None:
    row_id = await _row(panel)
    await _upload(panel, row_id, "c.pdf", PDF, "application/pdf")
    await _type_reason(panel, row_id, "duplicado")
    await _run(panel, REJECT_ACTION, [row_id])

    page = await _mark_paid(panel, row_id)

    assert "já finalizados" in page
    row = await _stored(panel, row_id)
    assert row.status == ManualPayoutStatusEnum.REJECTED.value
    assert row.completed_at is None


async def test_paid_row_cannot_be_rejected(panel: _Panel) -> None:
    row_id = await _row(panel, reason="tarde demais")
    await _upload(panel, row_id, "c.pdf", PDF, "application/pdf")
    await _mark_paid(panel, row_id)

    page = await _run(panel, REJECT_ACTION, [row_id])

    assert "já finalizados" in page
    assert (await _stored(panel, row_id)).status == (
        ManualPayoutStatusEnum.COMPLETED.value
    )
    assert await _wallet(panel) == (0, 0)


async def test_hook_failure_rolls_back_only_its_row(panel: _Panel) -> None:
    REJECTED_HOOK_CALLS.clear()
    broken = await _row(panel, reason="motivo", suffix="-boom")
    fine = await _row(panel, reason="motivo")

    page = await _run(panel, REJECT_ACTION, [broken, fine])

    assert "1 recusado(s)" in page
    assert "falharam" in page
    stored_broken = await _stored(panel, broken)
    assert stored_broken.status == ManualPayoutStatusEnum.PENDING.value
    assert stored_broken.rejected_at is None
    stored_fine = await _stored(panel, fine)
    assert stored_fine.status == ManualPayoutStatusEnum.REJECTED.value
    assert await _wallet(panel) == (5_000, 1)


async def test_reason_of_a_final_row_cannot_change(panel: _Panel) -> None:
    row_id = await _row(panel, reason="duplicado")
    await _run(panel, REJECT_ACTION, [row_id])

    assert await _type_reason(panel, row_id, "outro motivo") == 303

    assert (await _stored(panel, row_id)).rejection_reason == "duplicado"


@pytest.mark.parametrize(
    "status",
    [ManualPayoutStatusEnum.COMPLETED.value, ManualPayoutStatusEnum.REJECTED.value],
)
async def test_final_row_edit_form_has_no_reason_input(
    panel: _Panel, status: str
) -> None:
    row_id = await _row(panel, status, reason="motivo")
    page = await panel.client.get(f"/admin/m/{_SLUG}/{row_id}/edit")
    assert page.status_code == 200
    assert 'name="rejection_reason"' not in page.text


async def test_pending_row_edit_form_offers_the_reason(panel: _Panel) -> None:
    row_id = await _row(panel)
    page = await panel.client.get(f"/admin/m/{_SLUG}/{row_id}/edit")
    assert 'name="rejection_reason"' in page.text


async def test_reject_option_confirms_as_dangerous(panel: _Panel) -> None:
    await _row(panel)
    page = await panel.client.get(f"/admin/m/{_SLUG}/")
    assert (
        f'<option value="custom:{REJECT_ACTION}" data-confirm="Run &#34;Recusar e'
        ' devolver&#34; on the checked rows? This cannot be undone.">' in page.text
    )
    assert (
        '<option value="custom:mark_manual_payout_paid" data-confirm="Apply the'
        ' selected action to the checked rows?">' in page.text
    )
