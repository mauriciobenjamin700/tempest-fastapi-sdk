"""Admin preset for the withdrawals an administrator pays by hand.

:func:`manual_payout_admin` registers a project's concrete
:class:`~tempest_fastapi_sdk.wallet.BaseManualPayoutModel` with the panel
set up for the one job the table has: find the pending payouts, attach the
Pix receipt as a PDF, and mark the row paid.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from typing import TYPE_CHECKING, Any, Final

from sqlalchemy import update

from tempest_fastapi_sdk.admin.actions import (
    ActionHandler,
    AdminActionContext,
    AdminActionResult,
    admin_action,
)
from tempest_fastapi_sdk.admin.config import AdminModel
from tempest_fastapi_sdk.utils.datetime import utcnow
from tempest_fastapi_sdk.wallet.manual_payout_models import (
    BaseManualPayoutModel,
    ManualPayoutStatusEnum,
)

if TYPE_CHECKING:
    from tempest_fastapi_sdk.db.audit import BaseAuditLogModel
    from tempest_fastapi_sdk.utils.storage_backends import UploadStorage

RECEIPT_CONTENT_TYPES: Final[tuple[str, ...]] = ("application/pdf",)
"""What the receipt field takes: a PDF, checked by type and by ``%PDF-``."""

MARK_PAID_ACTION: Final[str] = "mark_manual_payout_paid"
"""Name of the "mark as paid" action (the bulk form's value)."""

_FINANCIAL_FIELDS: Final[tuple[str, ...]] = (
    "user_id",
    "amount_cents",
    "pix_key_masked",
    "pix_key_type",
    "correlation_id",
    "status",
    "completed_at",
    "completed_by",
)
"""Columns the panel shows and never lets an operator type."""

STATUS_LABELS: Final[dict[str, str]] = {
    ManualPayoutStatusEnum.PENDING.value: "Pendente",
    ManualPayoutStatusEnum.COMPLETED.value: "Pago",
    ManualPayoutStatusEnum.CANCELED.value: "Cancelado",
}
"""Portuguese labels for the status filter, keyed by the stored value."""


def make_mark_paid_action(model: type[BaseManualPayoutModel]) -> ActionHandler:
    """Build the "mark as paid" action for one concrete model.

    Each selected row is moved with one conditional ``UPDATE … WHERE status =
    'pending' AND receipt_key IS NOT NULL``, so a row without a receipt is
    left alone, a ``COMPLETED`` or ``CANCELED`` row never changes, and two
    operators clicking at once complete the row once.

    Args:
        model (type[BaseManualPayoutModel]): The project's concrete model.

    Returns:
        ActionHandler: The decorated action.
    """

    @admin_action(label="Marcar como pago", name=MARK_PAID_ACTION)
    async def mark_paid(ctx: AdminActionContext) -> AdminActionResult:
        """Complete every selected pending row that has a receipt.

        Args:
            ctx (AdminActionContext): The selection and the session.

        Returns:
            AdminActionResult: ``success`` when every row was completed;
            ``error`` naming the correlation ids left pending for lack of a
            receipt, or already final.
        """
        rows = await ctx.repository.list(filters={"id": ctx.ids})
        actor = str(getattr(ctx.principal, "id", ctx.principal))
        done: list[str] = []
        missing: list[str] = []
        final: list[str] = []
        for row in rows:
            if row.status != ManualPayoutStatusEnum.PENDING.value:
                final.append(row.correlation_id)
                continue
            if not row.receipt_key:
                missing.append(row.correlation_id)
                continue
            result = await ctx.db_session.execute(
                update(model)
                .where(
                    model.id == row.id,
                    model.status == ManualPayoutStatusEnum.PENDING.value,
                    model.receipt_key.is_not(None),
                )
                .values(
                    status=ManualPayoutStatusEnum.COMPLETED.value,
                    completed_at=utcnow(),
                    completed_by=actor,
                )
            )
            if getattr(result, "rowcount", 0) == 1:
                done.append(row.correlation_id)
            else:
                final.append(row.correlation_id)
        await ctx.db_session.commit()
        problems: list[str] = []
        if missing:
            problems.append(f"sem comprovante anexado: {', '.join(missing)}")
        if final:
            problems.append(f"já finalizados: {', '.join(final)}")
        if problems:
            return AdminActionResult(
                f"{len(done)} marcado(s) como pago; não alterados — "
                + "; ".join(problems),
                category="error",
            )
        return AdminActionResult(f"{len(done)} marcado(s) como pago.")

    return mark_paid


def _receipt_locked(row: Any, form: Mapping[str, Any]) -> Mapping[str, str]:
    """Refuse a new receipt on a row that is no longer pending.

    Args:
        row (Any): The stored row.
        form (Mapping[str, Any]): The posted form.

    Returns:
        Mapping[str, str]: A ``receipt_key`` error when a file was posted
        for a ``COMPLETED`` or ``CANCELED`` row; empty otherwise.
    """
    upload = form.get("receipt_key")
    if not getattr(upload, "filename", None):
        return {}
    if row.status == ManualPayoutStatusEnum.PENDING.value:
        return {}
    return {"receipt_key": "O comprovante de um saque finalizado não pode mudar."}


def manual_payout_admin(
    model: type[BaseManualPayoutModel],
    *,
    upload_storage: UploadStorage,
    audit_model: type[BaseAuditLogModel] | None = None,
    display_timezone: str | None = None,
    verbose_name: str = "Saque manual",
    verbose_name_plural: str = "Saques manuais",
    actions: Sequence[ActionHandler] = (),
) -> AdminModel[Any]:
    """Configure the panel for a manual payout table.

    - list filtered by ``status`` (a select labelled Pendente / Pago /
      Cancelado), searchable by ``correlation_id``;
    - no "Activate" / "Deactivate" bulk actions, and ``is_active``
      read-only: a payout is never switched off;
    - every money column read-only — amount, user, key, correlation id,
      status and who completed it; no create, no delete, no import;
    - ``receipt_key`` an upload that takes only a PDF (declared type
      ``application/pdf`` **and** bytes starting ``%PDF-``), downloadable
      from the row's page, and refused once the row is no longer
      ``PENDING``;
    - the "Marcar como pago" action (:func:`make_mark_paid_action`).

    Args:
        model (type[BaseManualPayoutModel]): The project's concrete model.
        upload_storage (UploadStorage): Where receipts are stored.
        audit_model (type[BaseAuditLogModel] | None): Shows the row's change
            timeline when set.
        display_timezone (str | None): IANA zone for the datetimes.
        verbose_name (str): Singular name in the panel.
        verbose_name_plural (str): Plural name in the panel.
        actions (Sequence[ActionHandler]): More actions, after "mark as
            paid".

    Returns:
        AdminModel[Any]: Ready for ``site.register``.
    """
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
        actions=[make_mark_paid_action(model), *actions],
        upload_fields=["receipt_key"],
        upload_storage=upload_storage,
        upload_accept={"receipt_key": list(RECEIPT_CONTENT_TYPES)},
        validate_edit=_receipt_locked,
        audit_model=audit_model,
        display_timezone=display_timezone,
        verbose_name=verbose_name,
        verbose_name_plural=verbose_name_plural,
    )


__all__: list[str] = [
    "MARK_PAID_ACTION",
    "RECEIPT_CONTENT_TYPES",
    "STATUS_LABELS",
    "make_mark_paid_action",
    "manual_payout_admin",
]
