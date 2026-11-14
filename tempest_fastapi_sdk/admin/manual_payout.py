"""Admin preset for the withdrawals an administrator pays by hand.

:func:`manual_payout_admin` registers a project's concrete
:class:`~tempest_fastapi_sdk.wallet.BaseManualPayoutModel` with the panel
set up for the one job the table has: find the pending payouts, attach the
Pix receipt as a PDF and mark the row paid — or, given the wallet, write a
reason and refuse the payout, refunding the amount. Both actions call
:class:`~tempest_fastapi_sdk.wallet.ManualPayoutService`, the same methods a
service exposes through its own route.
"""

from __future__ import annotations

import logging
from collections.abc import Callable, Mapping, Sequence
from typing import TYPE_CHECKING, Any, Final

from tempest_fastapi_sdk.admin.actions import (
    ActionHandler,
    AdminActionContext,
    AdminActionResult,
    admin_action,
)
from tempest_fastapi_sdk.admin.config import AdminModel
from tempest_fastapi_sdk.exceptions.wallet import (
    ManualPayoutNotFoundException,
    ManualPayoutNotPendingException,
    ManualPayoutReceiptMissingException,
)
from tempest_fastapi_sdk.wallet.manual_payout_models import (
    BaseManualPayoutModel,
    ManualPayoutStatusEnum,
)
from tempest_fastapi_sdk.wallet.manual_payout_service import (
    ManualPayoutHooks,
    ManualPayoutService,
)

if TYPE_CHECKING:
    from sqlalchemy.ext.asyncio import AsyncSession

    from tempest_fastapi_sdk.db.audit import BaseAuditLogModel
    from tempest_fastapi_sdk.utils.storage_backends import UploadStorage
    from tempest_fastapi_sdk.wallet.ledger import WalletLedger

logger: logging.Logger = logging.getLogger(__name__)

RECEIPT_CONTENT_TYPES: Final[tuple[str, ...]] = ("application/pdf",)
"""What the receipt field takes: a PDF, checked by type and by ``%PDF-``."""

MARK_PAID_ACTION: Final[str] = "mark_manual_payout_paid"
"""Name of the "mark as paid" action (the bulk form's value)."""

REJECT_ACTION: Final[str] = "reject_manual_payout"
"""Name of the "refuse and refund" action (the bulk form's value)."""

_FINANCIAL_FIELDS: Final[tuple[str, ...]] = (
    "user_id",
    "amount_cents",
    "pix_key_masked",
    "pix_key_type",
    "correlation_id",
    "status",
    "completed_at",
    "completed_by",
    "end_to_end_id",
    "rejected_at",
    "rejected_by",
)
"""Columns the panel shows and never lets an operator type."""

STATUS_LABELS: Final[dict[str, str]] = {
    ManualPayoutStatusEnum.PENDING.value: "Pendente",
    ManualPayoutStatusEnum.COMPLETED.value: "Pago",
    ManualPayoutStatusEnum.CANCELED.value: "Cancelado",
    ManualPayoutStatusEnum.REJECTED.value: "Recusado",
}
"""Portuguese labels for the status filter, keyed by the stored value."""


def _actor(ctx: AdminActionContext) -> str:
    """Name the administrator running an action.

    Args:
        ctx (AdminActionContext): The action's context.

    Returns:
        str: The principal's ``id``, or the principal itself as text.
    """
    return str(getattr(ctx.principal, "id", ctx.principal))


def _summary(
    done: int, verb: str, groups: Sequence[tuple[str, list[str]]]
) -> AdminActionResult:
    """Write the flash of an action over several rows.

    Args:
        done (int): Rows that moved.
        verb (str): What happened to them (``"marcado(s) como pago"``).
        groups (Sequence[tuple[str, list[str]]]): ``(label, correlation
            ids)`` per kind of row left alone; empty groups are skipped.

    Returns:
        AdminActionResult: ``success`` when every group is empty, ``error``
        naming the rows left alone otherwise.
    """
    problems = [f"{label}: {', '.join(ids)}" for label, ids in groups if ids]
    if problems:
        return AdminActionResult(
            f"{done} {verb}; não alterados — " + "; ".join(problems),
            category="error",
        )
    return AdminActionResult(f"{done} {verb}.")


def make_mark_paid_action(
    model: type[BaseManualPayoutModel],
    *,
    hooks: ManualPayoutHooks | None = None,
) -> ActionHandler:
    """Build the "mark as paid" action for one concrete model.

    Each selected row goes through
    :meth:`~tempest_fastapi_sdk.wallet.ManualPayoutService.mark_paid` with
    ``require_receipt=True``, in its own transaction: a row without a
    receipt is left alone, a final row never changes, and two operators
    clicking at once complete the row once. A row whose ``on_paid`` hook
    raises is rolled back, logged and reported; the other rows still move.

    Args:
        model (type[BaseManualPayoutModel]): The project's concrete model.
            The action reads it from the panel's repository; the parameter
            keeps the factory's signature uniform with
            :func:`make_reject_action`.
        hooks (ManualPayoutHooks | None): Callbacks; ``on_paid`` runs here.

    Returns:
        ActionHandler: The decorated action.
    """

    @admin_action(label="Marcar como pago", name=MARK_PAID_ACTION)
    async def mark_paid(ctx: AdminActionContext) -> AdminActionResult:
        """Complete every selected pending row that has a receipt.

        The ids and correlation ids are copied out of the rows before the
        first transition: a rolled-back row expires every loaded instance.

        Args:
            ctx (AdminActionContext): The selection and the session.

        Returns:
            AdminActionResult: ``success`` when every row was completed;
            ``error`` naming the correlation ids left pending for lack of a
            receipt, already final, or whose hook failed.
        """
        rows = await ctx.repository.list(filters={"id": ctx.ids})
        targets = [(row.id, row.correlation_id) for row in rows]
        service = ManualPayoutService(records=ctx.repository, hooks=hooks)
        actor = _actor(ctx)
        done = 0
        missing: list[str] = []
        final: list[str] = []
        failed: list[str] = []
        for row_id, correlation_id in targets:
            try:
                await service.mark_paid(row_id, actor=actor, require_receipt=True)
            except ManualPayoutReceiptMissingException:
                missing.append(correlation_id)
            except (ManualPayoutNotPendingException, ManualPayoutNotFoundException):
                final.append(correlation_id)
            except Exception:
                logger.exception("manual payout %s not marked paid", correlation_id)
                failed.append(correlation_id)
            else:
                done += 1
        return _summary(
            done,
            "marcado(s) como pago",
            [
                ("sem comprovante anexado", missing),
                ("já finalizados", final),
                ("falharam", failed),
            ],
        )

    return mark_paid


def make_reject_action(
    model: type[BaseManualPayoutModel],
    *,
    ledger_factory: Callable[[AsyncSession], WalletLedger],
    hooks: ManualPayoutHooks | None = None,
) -> ActionHandler:
    """Build the "refuse and refund" action for one concrete model.

    The operator writes the reason in the row's ``rejection_reason`` field
    first; the action then runs
    :meth:`~tempest_fastapi_sdk.wallet.ManualPayoutService.reject` per row,
    each in its own transaction: the row turns ``REJECTED``, the amount goes
    back to the wallet as a ``WITHDRAW_REFUND`` line and ``on_rejected``
    runs, in one commit. A row without a reason is left alone.

    Args:
        model (type[BaseManualPayoutModel]): The project's concrete model.
            The action reads it from the panel's repository.
        ledger_factory (Callable[[AsyncSession], WalletLedger]): Builds the
            wallet ledger on the panel's session — the refund and the status
            change must share it.
        hooks (ManualPayoutHooks | None): Callbacks; ``on_rejected`` runs
            here.

    Returns:
        ActionHandler: The decorated action.
    """

    @admin_action(label="Recusar e devolver", name=REJECT_ACTION, dangerous=True)
    async def reject(ctx: AdminActionContext) -> AdminActionResult:
        """Refuse every selected pending row that has a reason, refunding it.

        Args:
            ctx (AdminActionContext): The selection and the session.

        Returns:
            AdminActionResult: ``success`` when every row was refused;
            ``error`` naming the correlation ids left alone for lack of a
            reason, already final, or whose refund or hook failed.
        """
        rows = await ctx.repository.list(filters={"id": ctx.ids})
        targets = [
            (row.id, row.correlation_id, row.status, row.rejection_reason)
            for row in rows
        ]
        service = ManualPayoutService(
            records=ctx.repository,
            ledger=ledger_factory(ctx.db_session),
            hooks=hooks,
        )
        actor = _actor(ctx)
        done = 0
        missing: list[str] = []
        final: list[str] = []
        failed: list[str] = []
        for row_id, correlation_id, status, reason in targets:
            if status != ManualPayoutStatusEnum.PENDING.value:
                final.append(correlation_id)
                continue
            if not (reason or "").strip():
                missing.append(correlation_id)
                continue
            try:
                await service.reject(row_id, reason, actor=actor)
            except (ManualPayoutNotPendingException, ManualPayoutNotFoundException):
                final.append(correlation_id)
            except Exception:
                logger.exception("manual payout %s not rejected", correlation_id)
                failed.append(correlation_id)
            else:
                done += 1
        return _summary(
            done,
            "recusado(s) e devolvido(s) à carteira",
            [
                ("sem motivo da recusa", missing),
                ("já finalizados", final),
                ("falharam", failed),
            ],
        )

    return reject


def _receipt_locked(row: Any, form: Mapping[str, Any]) -> Mapping[str, str]:
    """Refuse a new receipt on a row that is no longer pending.

    An upload field is not covered by ``readonly_fields_for`` (the form
    handles files apart), so the receipt keeps this check.

    Args:
        row (Any): The stored row.
        form (Mapping[str, Any]): The posted form.

    Returns:
        Mapping[str, str]: A ``receipt_key`` error when a file was posted
        for a row that is not ``PENDING``; empty otherwise.
    """
    upload = form.get("receipt_key")
    if not getattr(upload, "filename", None):
        return {}
    if row.status == ManualPayoutStatusEnum.PENDING.value:
        return {}
    return {"receipt_key": "O comprovante de um saque finalizado não pode mudar."}


def _reason_locked(row: Any) -> Sequence[str]:
    """Lock ``rejection_reason`` once the row is no longer pending.

    Args:
        row (Any): The stored row.

    Returns:
        Sequence[str]: ``["rejection_reason"]`` for a final row; empty for
        a ``PENDING`` one.
    """
    if row.status == ManualPayoutStatusEnum.PENDING.value:
        return []
    return ["rejection_reason"]


def manual_payout_admin(
    model: type[BaseManualPayoutModel],
    *,
    upload_storage: UploadStorage,
    ledger_factory: Callable[[AsyncSession], WalletLedger] | None = None,
    hooks: ManualPayoutHooks | None = None,
    audit_model: type[BaseAuditLogModel] | None = None,
    display_timezone: str | None = None,
    verbose_name: str = "Saque manual",
    verbose_name_plural: str = "Saques manuais",
    actions: Sequence[ActionHandler] = (),
) -> AdminModel[Any]:
    """Configure the panel for a manual payout table.

    - list filtered by ``status`` (a select labelled Pendente / Pago /
      Cancelado / Recusado), searchable by ``correlation_id``;
    - no "Activate" / "Deactivate" bulk actions, and ``is_active``
      read-only: a payout is never switched off;
    - every money column read-only — amount, user, key, correlation id,
      status, end-to-end id and who completed or refused it; no create, no
      delete, no import;
    - ``receipt_key`` an upload that takes only a PDF (declared type
      ``application/pdf`` **and** bytes starting ``%PDF-``), downloadable
      from the row's page, and refused once the row is no longer
      ``PENDING``;
    - ``rejection_reason`` editable while the row is ``PENDING``; on a final
      row it leaves the edit form and a posted value is ignored
      (``readonly_fields_for``);
    - the "Marcar como pago" action (:func:`make_mark_paid_action`), and,
      with ``ledger_factory``, "Recusar e devolver"
      (:func:`make_reject_action`).

    Args:
        model (type[BaseManualPayoutModel]): The project's concrete model.
        upload_storage (UploadStorage): Where receipts are stored.
        ledger_factory (Callable[[AsyncSession], WalletLedger] | None):
            Builds the wallet ledger on the panel's session. ``None`` leaves
            the "refuse and refund" action out.
        hooks (ManualPayoutHooks | None): Callbacks run inside each
            transition — the same instance the provider gets.
        audit_model (type[BaseAuditLogModel] | None): Shows the row's change
            timeline when set.
        display_timezone (str | None): IANA zone for the datetimes.
        verbose_name (str): Singular name in the panel.
        verbose_name_plural (str): Plural name in the panel.
        actions (Sequence[ActionHandler]): More actions, after the preset's.

    Returns:
        AdminModel[Any]: Ready for ``site.register``.
    """
    preset: list[ActionHandler] = [make_mark_paid_action(model, hooks=hooks)]
    if ledger_factory is not None:
        preset.append(
            make_reject_action(model, ledger_factory=ledger_factory, hooks=hooks)
        )
    return AdminModel(
        model,
        list_display=[
            "correlation_id",
            "user_id",
            "amount_cents",
            "pix_key_type",
            "pix_key_masked",
            "status",
            "receipt_key",
            "completed_at",
            "created_at",
        ],
        list_filter=["status"],
        search_fields=["correlation_id"],
        readonly_fields=[*_FINANCIAL_FIELDS, "is_active"],
        choices={"status": list(STATUS_LABELS.items())},
        ordering="-created_at",
        can_create=False,
        can_delete=False,
        can_toggle_active=False,
        actions=[*preset, *actions],
        upload_fields=["receipt_key"],
        upload_storage=upload_storage,
        upload_accept={"receipt_key": list(RECEIPT_CONTENT_TYPES)},
        validate_edit=_receipt_locked,
        readonly_fields_for=_reason_locked,
        audit_model=audit_model,
        display_timezone=display_timezone,
        verbose_name=verbose_name,
        verbose_name_plural=verbose_name_plural,
    )


__all__: list[str] = [
    "MARK_PAID_ACTION",
    "RECEIPT_CONTENT_TYPES",
    "REJECT_ACTION",
    "STATUS_LABELS",
    "make_mark_paid_action",
    "make_reject_action",
    "manual_payout_admin",
]
