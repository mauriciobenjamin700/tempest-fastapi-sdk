"""ManualPayoutService: mark paid / reject over PENDING, refund and hooks, on SQLite."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any
from uuid import UUID, uuid4

import pytest
from sqlalchemy import String, func, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import Mapped, mapped_column

from tempest_fastapi_sdk import (
    AsyncDatabaseManager,
    BaseModel,
    BaseRepository,
    BaseUserModel,
    PixKeyType,
)
from tempest_fastapi_sdk.exceptions import (
    EmailDeliveryException,
    ManualPayoutNotFoundException,
    ManualPayoutNotPendingException,
    ManualPayoutReceiptMissingException,
    PayoutRejectedException,
    PayoutUncertainException,
)
from tempest_fastapi_sdk.testing.fakes import FakeEmailUtils
from tempest_fastapi_sdk.wallet import (
    DEFAULT_REFUND_DESCRIPTION,
    ManualPayoutHooks,
    ManualPayoutProvider,
    ManualPayoutService,
    ManualPayoutStatusEnum,
    WalletBalanceMixin,
    WalletEntryKindEnum,
    WalletLedger,
    WalletService,
    make_manual_payout_model,
    make_wallet_entry_model,
)

AMOUNT: int = 5_000
"""What every test withdraws."""


class _SvcUser(BaseUserModel, WalletBalanceMixin):
    __tablename__ = "manual_payout_svc_users"


_SvcEntry = make_wallet_entry_model(
    user_table="manual_payout_svc_users",
    tablename="manual_payout_svc_entries",
    class_name="_SvcEntry",
)

_SvcPayout = make_manual_payout_model(
    user_table="manual_payout_svc_users",
    tablename="manual_payout_svc_rows",
    class_name="_SvcPayout",
)


class _SvcHistory(BaseModel):
    """What a consuming service writes from a hook: the user-visible line."""

    __tablename__ = "manual_payout_svc_history"

    label: Mapped[str] = mapped_column(String(64), nullable=False)
    correlation_id: Mapped[str] = mapped_column(String(128), nullable=False)


def _ledger(session: AsyncSession) -> WalletLedger:
    return WalletLedger(
        balances=BaseRepository(session, model=_SvcUser),
        entries=BaseRepository(session, model=_SvcEntry),
    )


@dataclass
class _Recorder:
    """Hooks that write a history line, and optionally fail afterwards."""

    fail: set[str] = field(default_factory=set)
    calls: list[tuple[str, str]] = field(default_factory=list)

    def hook(self, label: str) -> Any:
        """Build one hook.

        Args:
            label (str): The history label and the hook's name.

        Returns:
            Any: The hook.
        """

        async def run(session: AsyncSession, row: Any) -> None:
            self.calls.append((label, row.status))
            session.add(_SvcHistory(label=label, correlation_id=row.correlation_id))
            await session.flush()
            if label in self.fail:
                raise RuntimeError(f"{label} hook failed")

        return run

    def hooks(self) -> ManualPayoutHooks:
        """All four hooks.

        Returns:
            ManualPayoutHooks: The hooks.
        """
        return ManualPayoutHooks(
            on_requested=self.hook("requested"),
            on_canceled=self.hook("canceled"),
            on_paid=self.hook("paid"),
            on_rejected=self.hook("rejected"),
        )


async def _setup(
    db: AsyncDatabaseManager, *, receipt: bool = False
) -> tuple[UUID, UUID]:
    """Fund a user, debit the withdrawal and write its PENDING row.

    Args:
        db (AsyncDatabaseManager): The database.
        receipt (bool): Attach a receipt key to the row.

    Returns:
        tuple[UUID, UUID]: The user id and the row id.
    """
    async with db.get_session_context() as session:
        user = _SvcUser(email=f"{uuid4()}@example.com", hashed_password="x")
        session.add(user)
        await session.commit()
        ledger = _ledger(session)
        await ledger.credit(user.id, AMOUNT, kind="SALE")
        correlation = f"saque-{uuid4().hex[:8]}"
        await ledger.debit_available(
            user.id,
            AMOUNT,
            kind=WalletEntryKindEnum.WITHDRAW.value,
            reference_type="payout",
            reference_id=correlation,
        )
        row = _SvcPayout(
            user_id=user.id,
            amount_cents=AMOUNT,
            pix_key_masked="mo***om",
            pix_key_type="email",
            correlation_id=correlation,
            status=ManualPayoutStatusEnum.PENDING.value,
            receipt_key="r/1.pdf" if receipt else None,
        )
        session.add(row)
        await session.commit()
        return user.id, row.id


async def _state(db: AsyncDatabaseManager, user_id: UUID, row_id: UUID) -> Any:
    """Read the row, the balance, the refund lines and the history labels.

    Returns:
        Any: ``(row, balance, refunds, history)``.
    """
    async with db.get_session_context() as session:
        row = (
            await session.execute(select(_SvcPayout).where(_SvcPayout.id == row_id))
        ).scalar_one()
        balance = (await _ledger(session).balance(user_id)).total_cents
        refunds = (
            await session.execute(
                select(func.count())
                .select_from(_SvcEntry)
                .where(_SvcEntry.kind == WalletEntryKindEnum.WITHDRAW_REFUND.value)
                .where(_SvcEntry.user_id == user_id)
            )
        ).scalar_one()
        history = [
            label
            for (label,) in (
                await session.execute(
                    select(_SvcHistory.label).where(
                        _SvcHistory.correlation_id == row.correlation_id
                    )
                )
            ).all()
        ]
        return row, balance, refunds, history


def _service(session: AsyncSession, hooks: ManualPayoutHooks | None = None) -> Any:
    return ManualPayoutService(
        records=BaseRepository(session, model=_SvcPayout),
        ledger=_ledger(session),
        hooks=hooks,
    )


class TestMarkPaid:
    async def test_completes_once_and_runs_the_hook(
        self, db: AsyncDatabaseManager
    ) -> None:
        user_id, row_id = await _setup(db)
        recorder = _Recorder()
        async with db.get_session_context() as session:
            row = await _service(session, recorder.hooks()).mark_paid(
                row_id, "E123", actor="admin-1"
            )
        assert row.status == ManualPayoutStatusEnum.COMPLETED.value
        stored, balance, refunds, history = await _state(db, user_id, row_id)
        assert stored.status == ManualPayoutStatusEnum.COMPLETED.value
        assert stored.end_to_end_id == "E123"
        assert stored.completed_by == "admin-1"
        assert stored.completed_at is not None
        assert balance == 0
        assert refunds == 0
        assert history == ["paid"]
        assert recorder.calls == [("paid", ManualPayoutStatusEnum.COMPLETED.value)]

    async def test_second_call_is_not_pending_and_changes_nothing(
        self, db: AsyncDatabaseManager
    ) -> None:
        user_id, row_id = await _setup(db)
        async with db.get_session_context() as session:
            await _service(session).mark_paid(row_id, actor="admin-1")
        before = (await _state(db, user_id, row_id))[0]
        async with db.get_session_context() as session:
            with pytest.raises(ManualPayoutNotPendingException):
                await _service(session).mark_paid(row_id, "E999", actor="admin-2")
        after = (await _state(db, user_id, row_id))[0]
        assert after.completed_by == "admin-1"
        assert after.completed_at == before.completed_at
        assert after.end_to_end_id is None

    async def test_require_receipt_refuses_a_row_without_one(
        self, db: AsyncDatabaseManager
    ) -> None:
        user_id, row_id = await _setup(db)
        async with db.get_session_context() as session:
            with pytest.raises(ManualPayoutReceiptMissingException):
                await _service(session).mark_paid(row_id, require_receipt=True)
        stored = (await _state(db, user_id, row_id))[0]
        assert stored.status == ManualPayoutStatusEnum.PENDING.value

    async def test_require_receipt_passes_with_one(
        self, db: AsyncDatabaseManager
    ) -> None:
        user_id, row_id = await _setup(db, receipt=True)
        async with db.get_session_context() as session:
            await _service(session).mark_paid(row_id, require_receipt=True)
        stored = (await _state(db, user_id, row_id))[0]
        assert stored.status == ManualPayoutStatusEnum.COMPLETED.value

    async def test_unknown_id_is_not_found(self, db: AsyncDatabaseManager) -> None:
        async with db.get_session_context() as session:
            with pytest.raises(ManualPayoutNotFoundException):
                await _service(session).mark_paid(uuid4())

    async def test_hook_failure_rolls_the_transition_back(
        self, db: AsyncDatabaseManager
    ) -> None:
        user_id, row_id = await _setup(db)
        recorder = _Recorder(fail={"paid"})
        async with db.get_session_context() as session:
            with pytest.raises(RuntimeError, match="paid hook failed"):
                await _service(session, recorder.hooks()).mark_paid(row_id)
        stored, _, _, history = await _state(db, user_id, row_id)
        assert stored.status == ManualPayoutStatusEnum.PENDING.value
        assert stored.completed_at is None
        assert history == []

    async def test_works_without_a_ledger(self, db: AsyncDatabaseManager) -> None:
        user_id, row_id = await _setup(db)
        async with db.get_session_context() as session:
            service = ManualPayoutService(
                records=BaseRepository(session, model=_SvcPayout)
            )
            await service.mark_paid(row_id)
        stored = (await _state(db, user_id, row_id))[0]
        assert stored.status == ManualPayoutStatusEnum.COMPLETED.value


class TestReject:
    async def test_rejects_refunds_and_runs_the_hook_in_one_commit(
        self, db: AsyncDatabaseManager
    ) -> None:
        user_id, row_id = await _setup(db)
        recorder = _Recorder()
        async with db.get_session_context() as session:
            await _service(session, recorder.hooks()).reject(
                row_id, "  chave Pix inválida  ", actor="admin-1"
            )
        stored, balance, refunds, history = await _state(db, user_id, row_id)
        assert stored.status == ManualPayoutStatusEnum.REJECTED.value
        assert stored.rejection_reason == "chave Pix inválida"
        assert stored.rejected_by == "admin-1"
        assert stored.rejected_at is not None
        assert balance == AMOUNT
        assert refunds == 1
        assert history == ["rejected"]
        async with db.get_session_context() as session:
            line = (
                await session.execute(
                    select(_SvcEntry).where(
                        _SvcEntry.kind == WalletEntryKindEnum.WITHDRAW_REFUND.value
                    )
                )
            ).scalar_one()
        assert line.amount_cents == AMOUNT
        assert line.reference_type == "payout"
        assert line.reference_id == stored.correlation_id
        assert line.description == DEFAULT_REFUND_DESCRIPTION

    async def test_second_reject_is_not_pending_and_refunds_once(
        self, db: AsyncDatabaseManager
    ) -> None:
        user_id, row_id = await _setup(db)
        async with db.get_session_context() as session:
            await _service(session).reject(row_id, "duplicado")
        async with db.get_session_context() as session:
            with pytest.raises(ManualPayoutNotPendingException):
                await _service(session).reject(row_id, "de novo")
        stored, balance, refunds, _ = await _state(db, user_id, row_id)
        assert stored.rejection_reason == "duplicado"
        assert balance == AMOUNT
        assert refunds == 1

    async def test_stale_read_loses_to_the_committed_mark_paid(
        self, db: AsyncDatabaseManager
    ) -> None:
        """Admin A loaded the row as PENDING; admin B paid it; A's reject fails."""
        user_id, row_id = await _setup(db)
        async with db.get_session_context() as session_a:
            seen = (
                await session_a.execute(
                    select(_SvcPayout).where(_SvcPayout.id == row_id)
                )
            ).scalar_one()
            assert seen.status == ManualPayoutStatusEnum.PENDING.value
            await session_a.commit()
            async with db.get_session_context() as session_b:
                await _service(session_b).mark_paid(row_id, actor="admin-b")
            with pytest.raises(ManualPayoutNotPendingException):
                await _service(session_a).reject(row_id, "atrasado", actor="admin-a")
        stored, balance, refunds, _ = await _state(db, user_id, row_id)
        assert stored.status == ManualPayoutStatusEnum.COMPLETED.value
        assert stored.rejection_reason is None
        assert balance == 0
        assert refunds == 0

    async def test_hook_failure_keeps_status_and_balance(
        self, db: AsyncDatabaseManager
    ) -> None:
        user_id, row_id = await _setup(db)
        recorder = _Recorder(fail={"rejected"})
        async with db.get_session_context() as session:
            with pytest.raises(RuntimeError, match="rejected hook failed"):
                await _service(session, recorder.hooks()).reject(row_id, "motivo")
        stored, balance, refunds, history = await _state(db, user_id, row_id)
        assert stored.status == ManualPayoutStatusEnum.PENDING.value
        assert stored.rejection_reason is None
        assert balance == 0
        assert refunds == 0
        assert history == []
        assert recorder.calls == [("rejected", ManualPayoutStatusEnum.REJECTED.value)]

    async def test_hook_failure_inside_a_caller_block_rolls_back_with_it(
        self, db: AsyncDatabaseManager
    ) -> None:
        user_id, row_id = await _setup(db)
        recorder = _Recorder(fail={"rejected"})
        async with db.get_session_context() as session:
            repository = BaseRepository(session, model=_SvcPayout)
            with pytest.raises(RuntimeError):
                async with repository.transaction():
                    await _service(session, recorder.hooks()).reject(row_id, "x")
        _, balance, refunds, _ = await _state(db, user_id, row_id)
        assert balance == 0
        assert refunds == 0

    @pytest.mark.parametrize("reason", ["", "   ", "x" * 513])
    async def test_bad_reason_writes_nothing(
        self, db: AsyncDatabaseManager, reason: str
    ) -> None:
        user_id, row_id = await _setup(db)
        async with db.get_session_context() as session:
            with pytest.raises(ValueError, match="reason"):
                await _service(session).reject(row_id, reason)
        stored = (await _state(db, user_id, row_id))[0]
        assert stored.status == ManualPayoutStatusEnum.PENDING.value

    async def test_without_a_ledger_reject_refuses(
        self, db: AsyncDatabaseManager
    ) -> None:
        _, row_id = await _setup(db)
        async with db.get_session_context() as session:
            service = ManualPayoutService(
                records=BaseRepository(session, model=_SvcPayout)
            )
            with pytest.raises(ValueError, match="needs a ledger"):
                await service.reject(row_id, "motivo")

    async def test_ledger_on_another_session_is_refused(
        self, db: AsyncDatabaseManager
    ) -> None:
        async with (
            db.get_session_context() as one,
            db.get_session_context() as other,
        ):
            with pytest.raises(ValueError, match="share one session"):
                ManualPayoutService(
                    records=BaseRepository(one, model=_SvcPayout),
                    ledger=_ledger(other),
                )


class TestProviderHooks:
    """``on_requested`` / ``on_canceled`` run where the provider writes the row."""

    async def _withdraw(
        self, db: AsyncDatabaseManager, recorder: _Recorder, mailer: FakeEmailUtils
    ) -> tuple[UUID, Exception | None]:
        async with db.get_session_context() as session:
            user = _SvcUser(email=f"{uuid4()}@example.com", hashed_password="x")
            session.add(user)
            await session.commit()
            user_id = user.id
            wallet = WalletService(ledger=_ledger(session))
            await wallet.credit(user_id, AMOUNT, kind="SALE")
            provider = ManualPayoutProvider(
                mailer,
                admin_emails=["financeiro@example.com"],
                records=BaseRepository(session, model=_SvcPayout),
                hooks=recorder.hooks(),
            )
            try:
                await wallet.withdraw(
                    user_id,
                    payout=provider,
                    pix_key="motorista@example.com",
                    pix_key_type=PixKeyType.EMAIL,
                    correlation_id="saque-hook",
                )
            except Exception as error:
                return user_id, error
            return user_id, None

    async def _rows(self, db: AsyncDatabaseManager) -> tuple[list[Any], list[str]]:
        async with db.get_session_context() as session:
            rows = list((await session.execute(select(_SvcPayout))).scalars().all())
            labels = list(
                (await session.execute(select(_SvcHistory.label))).scalars().all()
            )
        return rows, labels

    async def test_on_requested_commits_with_the_row(
        self, db: AsyncDatabaseManager
    ) -> None:
        recorder = _Recorder()
        _, error = await self._withdraw(db, recorder, FakeEmailUtils())
        assert error is None
        [row], labels = await self._rows(db)
        assert row.status == ManualPayoutStatusEnum.PENDING.value
        assert labels == ["requested"]

    async def test_on_requested_failure_refuses_and_refunds(
        self, db: AsyncDatabaseManager
    ) -> None:
        recorder = _Recorder(fail={"requested"})
        mailer = FakeEmailUtils()
        user_id, error = await self._withdraw(db, recorder, mailer)
        assert isinstance(error, PayoutRejectedException)
        rows, labels = await self._rows(db)
        assert rows == []
        assert labels == []
        assert mailer.outbox == []
        async with db.get_session_context() as session:
            assert (await _ledger(session).balance(user_id)).total_cents == AMOUNT

    async def test_on_canceled_runs_when_the_mail_fails(
        self, db: AsyncDatabaseManager
    ) -> None:
        recorder = _Recorder()
        mailer = FakeEmailUtils()
        mailer.fail_next(EmailDeliveryException())
        _, error = await self._withdraw(db, recorder, mailer)
        assert isinstance(error, PayoutRejectedException)
        [row], labels = await self._rows(db)
        assert row.status == ManualPayoutStatusEnum.CANCELED.value
        assert sorted(labels) == ["canceled", "requested"]

    async def test_on_canceled_failure_keeps_pending_and_the_debit(
        self, db: AsyncDatabaseManager
    ) -> None:
        recorder = _Recorder(fail={"canceled"})
        mailer = FakeEmailUtils()
        mailer.fail_next(EmailDeliveryException())
        user_id, error = await self._withdraw(db, recorder, mailer)
        assert isinstance(error, PayoutUncertainException)
        [row], labels = await self._rows(db)
        assert row.status == ManualPayoutStatusEnum.PENDING.value
        assert labels == ["requested"]
        async with db.get_session_context() as session:
            assert (await _ledger(session).balance(user_id)).total_cents == 0
