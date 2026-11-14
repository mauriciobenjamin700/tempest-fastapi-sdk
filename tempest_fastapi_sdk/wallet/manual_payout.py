"""A payout provider that asks a person to send the Pix.

:class:`ManualPayoutProvider` implements
:class:`~tempest_fastapi_sdk.integrations.payment.PayoutProvider` by
e-mailing the administrators the transfer to make by hand: amount, Pix key
and its type, the wallet owner's id (:attr:`PayoutRequest.payee_id`, which
:meth:`~tempest_fastapi_sdk.wallet.WalletService.withdraw` fills), the
correlation id and the withdrawal's comment. It exists for the
provider whose automated payout is not available to the account — Mercado
Pago's, blocked on the provider's side (#499) — so a service can still let a
user withdraw, with :meth:`~tempest_fastapi_sdk.wallet.WalletService.withdraw`
debiting the wallet exactly as it does for an automated provider.

The result is always :attr:`PayoutStatusEnum.PENDING` with
``provider_status="manual_review"``: the money leaves when a person sends
it, and nothing in the service observes that.
"""

from __future__ import annotations

import logging
from collections.abc import Awaitable, Sequence
from typing import TYPE_CHECKING, Any, Final, Protocol
from uuid import UUID

from tempest_fastapi_sdk.exceptions import (
    EmailDeliveryException,
    IntegrationDisabledException,
    PayoutRejectedException,
)
from tempest_fastapi_sdk.integrations.payment.base import (
    PayoutRequest,
    PayoutResult,
    PayoutStatusEnum,
)
from tempest_fastapi_sdk.utils.regex import PixKeyType
from tempest_fastapi_sdk.wallet.manual_payout_models import ManualPayoutStatusEnum
from tempest_fastapi_sdk.wallet.manual_payout_service import ManualPayoutHooks

if TYPE_CHECKING:
    from tempest_fastapi_sdk.db.repository import BaseRepository

logger: logging.Logger = logging.getLogger(__name__)

MANUAL_PAYOUT_PROVIDER: Final[str] = "manual"
"""Default :attr:`ManualPayoutProvider.provider_name`."""

MANUAL_REVIEW_STATUS: Final[str] = "manual_review"
"""The ``provider_status`` every manual payout answers with."""


class AdminMailer(Protocol):
    """What :class:`ManualPayoutProvider` needs from a mailer.

    :class:`~tempest_fastapi_sdk.EmailUtils` satisfies it as is, and so does
    :class:`~tempest_fastapi_sdk.testing.fakes.FakeEmailUtils`. Parameters
    are positional-only so an implementation may name them anything.
    """

    def send(self, to: list[str], subject: str, body: str, /) -> Awaitable[None]:
        """Send one plain-text message.

        Args:
            to (list[str]): Recipients.
            subject (str): Subject line.
            body (str): Plain-text body.

        Returns:
            Awaitable[None]: Completes once the message is handed over.
        """
        ...


def format_brl(cents: int) -> str:
    """Write an amount of cents the way a Brazilian reads it.

    Args:
        cents (int): The amount, in cents.

    Returns:
        str: ``R$ 1.234,56`` — dot for thousands, comma for decimals, and a
        leading ``-`` for a negative amount.
    """
    sign = "-" if cents < 0 else ""
    reais, centavos = divmod(abs(cents), 100)
    grouped = f"{reais:,}".replace(",", ".")
    return f"{sign}R$ {grouped},{centavos:02d}"


def mask_pix_key(key: str) -> str:
    """Hide most of a Pix key, for a log line.

    Args:
        key (str): The key.

    Returns:
        str: The first two and the last two characters around ``***``; a key
        of six characters or fewer becomes ``***``.
    """
    if len(key) <= 6:
        return "***"
    return f"{key[:2]}***{key[-2:]}"


class ManualPayoutProvider:
    """Pay a withdrawal out by e-mailing the administrators the Pix to send.

    Error contract, as :class:`~tempest_fastapi_sdk.integrations.payment.PayoutProvider`
    asks:

    * the mailer raises
      :class:`~tempest_fastapi_sdk.exceptions.EmailDeliveryException` or
      :class:`~tempest_fastapi_sdk.exceptions.IntegrationDisabledException`
      -> :class:`~tempest_fastapi_sdk.exceptions.PayoutRejectedException`.
      No administrator received the order, so no one will send the money,
      and the wallet gets its debit back. ``EmailUtils`` raises the first one
      for every SMTP transport failure, including a timeout; a server that
      accepted the message and then lost the connection is the one case where
      that is wrong, and the correlation id in the next attempt's e-mail is
      what reconciles it;
    * anything else propagates, and the wallet keeps the debit and logs it
      ``CRITICAL`` — the outcome is unknown.

    The e-mail carries the whole Pix key, because a person has to type it.
    The log line names it masked (:func:`mask_pix_key`).

    With ``records``, every payout is also written as a
    :class:`~tempest_fastapi_sdk.wallet.BaseManualPayoutModel` row
    (``PENDING``) **before** the e-mail goes out — the wallet debit has
    already committed by then. A row that cannot be written refuses the
    payout (the debit comes back and no e-mail is sent); an e-mail that
    fails turns the row ``CANCELED`` before the refusal. So a debit the
    wallet keeps always has a row the panel shows, except when the process
    dies between the debit's commit and this write — the ``WITHDRAW`` ledger
    line, whose ``reference_id`` is the correlation id, is what reconciles
    that case.

    ``hooks`` (:class:`~tempest_fastapi_sdk.wallet.ManualPayoutHooks`) runs
    ``on_requested`` in the transaction that writes the row, and
    ``on_canceled`` in the one that cancels it; a hook that raises counts as
    a row that cannot be written. Settling the row afterwards is
    :class:`~tempest_fastapi_sdk.wallet.ManualPayoutService`'s job.

    Attributes:
        provider_name (str): Copied into :attr:`PayoutResult.provider`.
        admin_emails (tuple[str, ...]): Who receives the order.
    """

    def __init__(
        self,
        mailer: AdminMailer,
        *,
        admin_emails: Sequence[str],
        provider_name: str = MANUAL_PAYOUT_PROVIDER,
        subject_prefix: str = "[Saque manual]",
        records: BaseRepository[Any] | None = None,
        record_url: str | None = None,
        hooks: ManualPayoutHooks | None = None,
    ) -> None:
        """Store the mailer and the recipients.

        Args:
            mailer (AdminMailer): Sends the order —
                :class:`~tempest_fastapi_sdk.EmailUtils` built from
                ``EmailSettings``, or a fake in development.
            admin_emails (Sequence[str]): The administrators' addresses.
            provider_name (str): The name the result and the ledger carry.
            subject_prefix (str): Prefixed to the subject line.
            records (BaseRepository[Any] | None): Repository of the
                project's concrete
                :class:`~tempest_fastapi_sdk.wallet.BaseManualPayoutModel`,
                on the request's session. ``None`` sends the e-mail only.
            record_url (str | None): A link to the row in the admin panel,
                with ``{id}`` where the row id goes
                (``"https://api.example.com/admin/m/manual_payouts/{id}"``).
                Written into the e-mail when ``records`` is set.
            hooks (ManualPayoutHooks | None): Transition callbacks; only
                ``on_requested`` and ``on_canceled`` run here, and only with
                ``records``.

        Raises:
            ValueError: If ``admin_emails`` is empty or holds an empty
                address — the order would go nowhere and the debit would sit
                without anyone told to pay it.
        """
        addresses = tuple(admin_emails)
        if not addresses or not all(address.strip() for address in addresses):
            raise ValueError(
                "ManualPayoutProvider needs at least one non-empty admin e-mail."
            )
        self._mailer: AdminMailer = mailer
        self.admin_emails: tuple[str, ...] = addresses
        self.provider_name: str = provider_name
        self._subject_prefix: str = subject_prefix
        self._records: BaseRepository[Any] | None = records
        self._record_url: str | None = record_url
        self._hooks: ManualPayoutHooks = hooks or ManualPayoutHooks()

    def render(
        self, request: PayoutRequest, link: str | None = None
    ) -> tuple[str, str]:
        """Write the subject and body of the order for one payout.

        Args:
            request (PayoutRequest): The transfer to make.
            link (str | None): The row in the admin panel, when known.

        Returns:
            tuple[str, str]: The subject and the plain-text body.
        """
        amount = format_brl(request.amount_cents)
        subject = f"{self._subject_prefix} {amount} para chave Pix"
        body = "\n".join(
            [
                "Um usuário pediu um saque e o valor já foi debitado da carteira.",
                "Faça o Pix manualmente a partir da conta da plataforma:",
                "",
                f"Valor: {amount}",
                f"Chave Pix: {request.pix_key}",
                f"Tipo da chave: {PixKeyType(request.pix_key_type).value}",
                f"Usuário (user_id): {request.payee_id or '-'}",
                f"Identificador (correlation_id): {request.correlation_id}",
                f"Comentário: {request.comment or '-'}",
                "",
                "Use o identificador acima como descrição do Pix, para conciliar.",
                "Se não for possível pagar, estorne o débito na carteira do "
                "usuário com o mesmo identificador.",
            ]
            + (
                ["", f"Depois de pagar, anexe o comprovante e marque como pago: {link}"]
                if link
                else []
            )
        )
        return subject, body

    async def _record(self, request: PayoutRequest) -> Any:
        """Write the ``PENDING`` row, when a repository was given.

        Args:
            request (PayoutRequest): The transfer.

        Returns:
            Any: The row, or ``None`` without ``records``.

        Raises:
            PayoutRejectedException: If the row cannot be written, or
                ``on_requested`` raised — no e-mail is sent and the wallet
                gets its debit back.
        """
        if self._records is None:
            return None
        records = self._records
        try:
            async with records.transaction() as session:
                row = await records.add(
                    records.model(
                        user_id=UUID(str(request.payee_id)),
                        amount_cents=request.amount_cents,
                        pix_key_masked=mask_pix_key(request.pix_key),
                        pix_key_type=PixKeyType(request.pix_key_type).value,
                        correlation_id=request.correlation_id,
                        status=ManualPayoutStatusEnum.PENDING.value,
                    )
                )
                if self._hooks.on_requested is not None:
                    await self._hooks.on_requested(session, row)
            return row
        except Exception as error:
            logger.error(
                "manual payout %s not recorded (%s); refusing it",
                request.correlation_id,
                type(error).__name__,
            )
            raise PayoutRejectedException() from error

    async def transfer_to_pix_key(self, request: PayoutRequest, /) -> PayoutResult:
        """E-mail the administrators the Pix to send.

        Args:
            request (PayoutRequest): The transfer.

        Returns:
            PayoutResult: ``PENDING`` with ``provider_status="manual_review"``.

        Raises:
            PayoutRejectedException: If the row could not be written, or the
                mailer could not deliver the order (see the class docstring).
        """
        row = await self._record(request)
        link = (
            self._record_url.format(id=row.id)
            if row is not None and self._record_url
            else None
        )
        subject, body = self.render(request, link)
        try:
            await self._mailer.send(list(self.admin_emails), subject, body)
        except (EmailDeliveryException, IntegrationDisabledException) as error:
            if row is not None and self._records is not None:
                async with self._records.transaction() as session:
                    row.status = ManualPayoutStatusEnum.CANCELED.value
                    await self._records.update(row)
                    if self._hooks.on_canceled is not None:
                        await self._hooks.on_canceled(session, row)
            logger.error(
                "manual payout %s not sent to the admins (%s); key %s",
                request.correlation_id,
                type(error).__name__,
                mask_pix_key(request.pix_key),
            )
            raise PayoutRejectedException() from error
        logger.info(
            "manual payout %s of %s cents requested to %s key %s",
            request.correlation_id,
            request.amount_cents,
            PixKeyType(request.pix_key_type).value,
            mask_pix_key(request.pix_key),
        )
        return PayoutResult(
            correlation_id=request.correlation_id,
            status=PayoutStatusEnum.PENDING,
            provider=self.provider_name,
            provider_status=MANUAL_REVIEW_STATUS,
        )


__all__: list[str] = [
    "MANUAL_PAYOUT_PROVIDER",
    "MANUAL_REVIEW_STATUS",
    "AdminMailer",
    "ManualPayoutProvider",
    "format_brl",
    "mask_pix_key",
]
