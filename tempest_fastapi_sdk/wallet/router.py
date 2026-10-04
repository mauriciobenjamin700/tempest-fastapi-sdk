"""Opt-in FastAPI router for the wallet module.

:func:`make_wallet_router` puts :class:`WalletService` behind three
endpoints scoped to the authenticated user. Same factory shape as the
other SDK routers: the caller says how a request-scoped service, the
current user, the payout provider and the user's Pix key resolve; the
router owns the HTTP surface.

There is no endpoint that takes a Pix key or a user id from the request
body: the key comes from ``pix_destination`` (the user's profile) and the
user from ``current_user_id``. A withdrawal endpoint that accepted either
would let one user pay themselves out of another's wallet, or to a key the
owner never registered.
"""

from __future__ import annotations

from collections.abc import AsyncIterator, Callable
from typing import Any
from uuid import UUID

from fastapi import APIRouter, Depends
from sqlalchemy.ext.asyncio import AsyncSession

from tempest_fastapi_sdk.integrations.payment.base import (
    PayoutProvider,
    PixKeyType,
)
from tempest_fastapi_sdk.schemas.pagination import BasePaginationSchema
from tempest_fastapi_sdk.wallet.schemas import (
    PixDestinationSchema,
    WalletBalanceSchema,
    WalletEntrySchema,
    WithdrawalSchema,
    WithdrawRequestSchema,
)
from tempest_fastapi_sdk.wallet.service import WalletService


def make_wallet_router(
    *,
    service_factory: Callable[[AsyncSession], WalletService],
    session_factory: Callable[[], AsyncIterator[AsyncSession]],
    current_user_id: Callable[..., Any],
    payout_provider: Callable[..., Any],
    pix_destination: Callable[..., Any],
    prefix: str = "/api/wallet",
    tags: list[str] | None = None,
) -> APIRouter:
    """Build the wallet router.

    Endpoints, all for the authenticated user:

    * ``GET {prefix}/balance`` -> :class:`WalletBalanceSchema`.
    * ``GET {prefix}/statement`` -> a page of ledger lines, newest first.
    * ``POST {prefix}/withdraw`` -> pays the available balance (or
      ``amount_cents`` of it) to the user's registered Pix key.

    Args:
        service_factory (Callable[[AsyncSession], WalletService]): Builds a
            request-scoped :class:`WalletService` from the session.
        session_factory (Callable[[], AsyncIterator[AsyncSession]]): Yields
            a request-scoped DB session.
        current_user_id (Callable[..., Any]): FastAPI dependency resolving
            the authenticated user's :class:`~uuid.UUID`.
        payout_provider (Callable[..., Any]): FastAPI dependency resolving
            the :class:`~tempest_fastapi_sdk.integrations.payment.PayoutProvider`.
        pix_destination (Callable[..., Any]): FastAPI dependency resolving
            the current user's :class:`PixDestinationSchema` from their
            profile. Raise from it when the user has no key registered.
        prefix (str): URL prefix. Defaults to ``"/api/wallet"``.
        tags (list[str] | None): OpenAPI tags. Defaults to ``["wallet"]``.

    Returns:
        APIRouter: Ready to mount with ``app.include_router``.
    """
    router = APIRouter(prefix=prefix, tags=list(tags or ["wallet"]))

    async def _session() -> AsyncIterator[AsyncSession]:
        async for session in session_factory():
            yield session

    def _service(session: AsyncSession = Depends(_session)) -> WalletService:
        return service_factory(session)

    @router.get("/balance", response_model=WalletBalanceSchema)
    async def get_balance(
        user_id: UUID = Depends(current_user_id),
        service: WalletService = Depends(_service),
    ) -> WalletBalanceSchema:
        """Return the caller's balance split into held and available.

        Args:
            user_id (UUID): The authenticated user.
            service (WalletService): Request-scoped service.

        Returns:
            WalletBalanceSchema: The balance.
        """
        return await service.balance(user_id)

    @router.get(
        "/statement",
        response_model=BasePaginationSchema[WalletEntrySchema],
    )
    async def get_statement(
        page: int = 1,
        page_size: int = 20,
        user_id: UUID = Depends(current_user_id),
        service: WalletService = Depends(_service),
    ) -> dict[str, Any]:
        """Page the caller's ledger, newest first.

        Args:
            page (int): 1-indexed page number.
            page_size (int): Lines per page.
            user_id (UUID): The authenticated user.
            service (WalletService): Request-scoped service.

        Returns:
            dict[str, Any]: The paginated ledger.
        """
        return await service.statement(user_id, page=page, page_size=page_size)

    @router.post("/withdraw", response_model=WithdrawalSchema)
    async def withdraw(
        body: WithdrawRequestSchema,
        user_id: UUID = Depends(current_user_id),
        payout: PayoutProvider = Depends(payout_provider),
        destination: PixDestinationSchema = Depends(pix_destination),
        service: WalletService = Depends(_service),
    ) -> WithdrawalSchema:
        """Pay the caller's wallet out to their registered Pix key.

        Args:
            body (WithdrawRequestSchema): Optional amount.
            user_id (UUID): The authenticated user.
            payout (PayoutProvider): Who sends the Pix.
            destination (PixDestinationSchema): The user's registered key.
            service (WalletService): Request-scoped service.

        Returns:
            WithdrawalSchema: The debit and the provider's answer.
        """
        return await service.withdraw(
            user_id,
            payout=payout,
            pix_key=destination.pix_key,
            pix_key_type=PixKeyType(destination.pix_key_type),
            amount_cents=body.amount_cents,
        )

    return router


__all__: list[str] = [
    "make_wallet_router",
]
