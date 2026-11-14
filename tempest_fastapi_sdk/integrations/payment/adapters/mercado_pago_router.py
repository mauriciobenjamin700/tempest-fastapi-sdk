"""Opt-in FastAPI router for a service that charges with Mercado Pago.

:func:`make_mercado_pago_router` mounts what every such service writes by
hand around :func:`make_mercado_pago_webhook_delivery_dependency`:

- ``POST {webhook_path}`` — verifies the signature, re-reads the order and
  awaits ``on_delivery`` with the :class:`MercadoPagoOrderDelivery`;
- with ``simulate=True`` (development and test only), the routes a front end
  uses to pay, expire, fail, cancel, capture and refund an order of the
  in-memory simulator — or, with ``simulation_url=``, of a
  ``tempest mercado-pago fake-api`` shared by the API and its workers — each
  delivering the signed webhook **to the route above**, through the whole
  application;
- with ``withdraw=``, ``POST {withdraw.path}``: the user's whole available
  balance is debited and paid out by a
  :class:`~tempest_fastapi_sdk.integrations.payment.PayoutProvider` —
  :class:`~tempest_fastapi_sdk.wallet.ManualPayoutProvider` by default,
  which e-mails the administrators the Pix to send, because Mercado Pago's
  own payout is not available to the account (#499).

A service that already writes the webhook route with the dependency keeps
working: nothing here changes the dependency.
"""

from __future__ import annotations

from collections.abc import AsyncIterator, Awaitable, Callable, Sequence
from dataclasses import dataclass
from enum import Enum
from typing import TYPE_CHECKING, Any, Final
from uuid import UUID

import httpx
from fastapi import APIRouter, Depends
from sqlalchemy.ext.asyncio import AsyncSession

from tempest_fastapi_sdk.api.dependencies._session import session_provider_for
from tempest_fastapi_sdk.api.route_docs import RouteDocs
from tempest_fastapi_sdk.db.repository import BaseRepository
from tempest_fastapi_sdk.integrations.payment.adapters.mercado_pago import (
    MercadoPagoCardProvider,
    MercadoPagoOrderDelivery,
    MercadoPagoPixProvider,
    make_mercado_pago_webhook_delivery_dependency,
)
from tempest_fastapi_sdk.integrations.payment.adapters.mercado_pago_simulation import (
    DEFAULT_SIMULATION_PREFIX,
    RemoteMercadoPagoSimulation,
    add_simulation_routes,
    ambient_settings,
    ensure_simulation_allowed,
    simulation_api_for,
)
from tempest_fastapi_sdk.integrations.payment.base import PayoutProvider
from tempest_fastapi_sdk.integrations.payment.mercado_pago.webhooks import (
    DEFAULT_TOLERANCE_SECONDS,
)
from tempest_fastapi_sdk.schemas import BaseSchema
from tempest_fastapi_sdk.utils.regex import PixKeyType
from tempest_fastapi_sdk.wallet.manual_payout import AdminMailer, ManualPayoutProvider
from tempest_fastapi_sdk.wallet.manual_payout_models import BaseManualPayoutModel
from tempest_fastapi_sdk.wallet.manual_payout_service import ManualPayoutHooks
from tempest_fastapi_sdk.wallet.schemas import PixDestinationSchema, WithdrawalSchema
from tempest_fastapi_sdk.wallet.service import WalletService

if TYPE_CHECKING:
    from tempest_fastapi_sdk.settings.mixins import MercadoPagoSettings
    from tempest_fastapi_sdk.testing.fakes.mercado_pago import (
        FakeMercadoPagoOrdersAPI,
    )

DEFAULT_WEBHOOK_PATH: Final[str] = "/webhooks/mercado-pago"
"""Where the webhook route is mounted by default."""

DEFAULT_WITHDRAW_PATH: Final[str] = "/api/mercado-pago/withdraw"
"""Where the withdrawal route is mounted by default."""

WEBHOOK_ROUTE_NAME: Final[str] = "mercado_pago_webhook"
"""Route name of the webhook, which the simulation resolves its path by."""

MercadoPagoDeliveryHandler = Callable[[MercadoPagoOrderDelivery], Awaitable[None]]
"""An async callable receiving one verified, re-read delivery.

Raise to answer ``500`` and have Mercado Pago deliver again; return to
answer ``200``.
"""


class MercadoPagoWebhookAckSchema(BaseSchema):
    """What the webhook route answers once ``on_delivery`` returned.

    Mercado Pago reads only the status; the body is for a log or a test.

    Attributes:
        ok (bool): Always ``True``.
        data_id (str): The ``data.id`` the signature covered.
        order_status (str | None): The re-read order's ``status``, or
            ``None`` when the delivery is not about a known order.
    """

    ok: bool
    data_id: str
    order_status: str | None = None


@dataclass(frozen=True, slots=True)
class MercadoPagoWithdrawConfig:
    """How the withdrawal route resolves the wallet, the user and the payout.

    Same dependencies as :func:`~tempest_fastapi_sdk.wallet.make_wallet_router`:
    the user and the Pix key come from the service, never from the request.

    Attributes:
        service_factory (Callable[[AsyncSession], WalletService]): Builds the
            request-scoped wallet service from the session.
        session_factory (Callable[..., AsyncIterator[AsyncSession]]): Yields
            the request's session.
        current_user_id (Callable[..., Any]): Dependency resolving the
            authenticated user's :class:`~uuid.UUID`.
        pix_destination (Callable[..., Any]): Dependency resolving the user's
            :class:`~tempest_fastapi_sdk.wallet.PixDestinationSchema` from
            their profile; raise from it when there is none.
        payout (PayoutProvider | None): An automated provider. Leave
            ``None`` to build a
            :class:`~tempest_fastapi_sdk.wallet.ManualPayoutProvider` from
            ``mailer`` and ``admin_emails``.
        mailer (AdminMailer | None): Sends the manual payout order.
        admin_emails (Sequence[str]): Who receives it.
        record_model (type[BaseManualPayoutModel] | None): The project's
            concrete manual payout table; each withdrawal writes a row the
            admin panel completes.
        record_url (str | None): Link to a row in the panel, ``{id}`` for
            the row id, written into the e-mail.
        hooks (ManualPayoutHooks | None): Transition callbacks handed to the
            :class:`~tempest_fastapi_sdk.wallet.ManualPayoutProvider`:
            ``on_requested`` runs in the transaction that writes the row
            (raising refuses the withdrawal and returns the debit), and
            ``on_canceled`` in the one that cancels it when the e-mail fails.
            Needs ``record_model`` — without a row there is no transaction
            for them to join. Pass the same instance to
            :class:`~tempest_fastapi_sdk.wallet.ManualPayoutService` for
            ``on_paid`` / ``on_rejected``.
        path (str): The route's path.
    """

    service_factory: Callable[[AsyncSession], WalletService]
    session_factory: Callable[..., AsyncIterator[AsyncSession]]
    current_user_id: Callable[..., Any]
    pix_destination: Callable[..., Any]
    payout: PayoutProvider | None = None
    mailer: AdminMailer | None = None
    admin_emails: Sequence[str] = ()
    record_model: type[BaseManualPayoutModel] | None = None
    record_url: str | None = None
    hooks: ManualPayoutHooks | None = None
    path: str = DEFAULT_WITHDRAW_PATH

    def __post_init__(self) -> None:
        """Refuse a configuration that cannot pay anyone.

        Raises:
            ValueError: If neither or both of ``payout`` and ``mailer`` are
                given, ``mailer`` comes without ``admin_emails``, or
                ``record_model`` comes with an automated ``payout``, or
                ``hooks`` comes without ``record_model`` (they would never
                run).
        """
        if (self.payout is None) == (self.mailer is None):
            raise ValueError("pass `payout` or `mailer` (with `admin_emails`)")
        if self.mailer is not None and not self.admin_emails:
            raise ValueError("`mailer` needs at least one entry in `admin_emails`")
        if self.payout is not None and self.record_model is not None:
            raise ValueError("`record_model` is written by the manual payout only")
        if self.hooks is not None and self.record_model is None:
            raise ValueError("`hooks` run on the manual payout row: pass record_model")

    def provider(self, session: AsyncSession) -> PayoutProvider:
        """Build the payout provider for one request.

        Args:
            session (AsyncSession): The request's session — the one the
                wallet service debits on.

        Returns:
            PayoutProvider: ``payout``, or a manual provider writing its row
            on ``session``.
        """
        if self.payout is not None:
            return self.payout
        assert self.mailer is not None, "checked in __post_init__"
        records = (
            None
            if self.record_model is None
            else BaseRepository(session, model=self.record_model)
        )
        return ManualPayoutProvider(
            self.mailer,
            admin_emails=self.admin_emails,
            records=records,
            record_url=self.record_url,
            hooks=self.hooks,
        )


def _resolve_secret(
    secret: str | None,
    settings: MercadoPagoSettings | None,
    simulation_api: FakeMercadoPagoOrdersAPI | None,
) -> str:
    """Pick the webhook secret the route verifies with.

    Args:
        secret (str | None): The explicit secret.
        settings (MercadoPagoSettings | None): The service's settings.
        simulation_api (FakeMercadoPagoOrdersAPI | None): The simulator, in
            simulation.

    Returns:
        str: ``secret``, else ``MERCADOPAGO_WEBHOOK_SECRET``, else — in
        simulation only — the simulator's secret.

    Raises:
        ValueError: When no non-empty secret is found.
    """
    resolved = (
        secret
        if secret is not None
        else ambient_settings(settings).MERCADOPAGO_WEBHOOK_SECRET
    )
    if not resolved and simulation_api is not None:
        resolved = simulation_api.webhook_secret
    if not resolved:
        raise ValueError(
            "make_mercado_pago_router needs a non-empty webhook secret: pass "
            "`secret=` or set MERCADOPAGO_WEBHOOK_SECRET. An empty secret "
            "rejects every delivery."
        )
    return resolved


def make_mercado_pago_router(
    provider: MercadoPagoPixProvider | MercadoPagoCardProvider,
    *,
    on_delivery: MercadoPagoDeliveryHandler,
    secret: str | None = None,
    settings: MercadoPagoSettings | None = None,
    simulate: bool = False,
    simulation_api: FakeMercadoPagoOrdersAPI | None = None,
    simulation_url: str | None = None,
    simulation_client: httpx.AsyncClient | None = None,
    withdraw: MercadoPagoWithdrawConfig | None = None,
    webhook_path: str = DEFAULT_WEBHOOK_PATH,
    simulation_prefix: str = DEFAULT_SIMULATION_PREFIX,
    tags: list[str] | None = None,
    include_in_schema: bool = True,
    tolerance_seconds: float | None = DEFAULT_TOLERANCE_SECONDS,
    docs_locale: str | None = None,
) -> APIRouter:
    """Build the Mercado Pago routes of a service.

    Args:
        provider (MercadoPagoPixProvider | MercadoPagoCardProvider): The
            adapter the service charges with; re-reads each delivery's order.
        on_delivery (MercadoPagoDeliveryHandler): Awaited with each verified,
            re-read delivery. Raise to answer ``500`` (Mercado Pago retries).
        secret (str | None): The webhook secret. ``None`` reads
            ``settings.MERCADOPAGO_WEBHOOK_SECRET`` (or the environment and
            ``.env`` when ``settings`` is ``None``).
        settings (MercadoPagoSettings | None): The service's settings, for
            the secret and for ``ENV``.
        simulate (bool): Mount the simulation routes. Refused unless ``ENV``
            is ``development`` or ``test`` (see
            :func:`ensure_simulation_allowed`).
        simulation_api (FakeMercadoPagoOrdersAPI | None): The simulator
            ``provider`` runs over. ``None`` takes the one
            :func:`mercado_pago_provider_for` registered for ``provider``.
        simulation_url (str | None): Base URL of a running
            ``tempest mercado-pago fake-api`` (e.g.
            ``"http://mp-fake:8090"``). The simulation routes then move
            orders on it, through :class:`RemoteMercadoPagoSimulation`, so a
            worker in another process — its provider pointed at the same
            fake-api by ``MERCADOPAGO_BASE_URL`` — reads what they moved.
            Needs ``simulate=True``; excludes ``simulation_api``.
        simulation_client (httpx.AsyncClient | None): The client the remote
            simulation sends with; its lifecycle stays with the caller.
            ``None`` opens a short-lived client per call. Needs
            ``simulation_url``.
        withdraw (MercadoPagoWithdrawConfig | None): Mounts the withdrawal
            route. ``None`` mounts none.
        webhook_path (str): Path of the webhook route — the URL registered
            in the Mercado Pago dashboard.
        simulation_prefix (str): Path prefix of the simulation routes.
        tags (list[str] | None): OpenAPI tags of the webhook and withdrawal
            routes. Defaults to ``["mercado-pago"]``; the simulation routes
            always carry ``"mercado-pago (dev)"``.
        include_in_schema (bool): Whether the routes appear in OpenAPI.
        tolerance_seconds (float | None): Signature clock drift allowed.
        docs_locale (str | None): Language of the OpenAPI text —
            ``"en-US"`` or ``"pt-BR"``; ``None`` reads ``API_DOCS_LOCALE``.

    Returns:
        APIRouter: Ready for ``app.include_router``.

    Raises:
        RuntimeError: If ``simulate`` is ``True`` outside development and
            test.
        ValueError: If ``simulate`` is ``True`` and no simulator is found
            for ``provider``, no webhook secret is configured,
            ``simulation_url`` comes without ``simulate=True`` or together
            with ``simulation_api``, or ``simulation_client`` comes without
            ``simulation_url``.
    """
    if simulation_client is not None and simulation_url is None:
        raise ValueError("`simulation_client` needs `simulation_url`")
    if simulation_url is not None and not simulate:
        raise ValueError("`simulation_url` needs simulate=True")
    if simulation_url is not None and simulation_api is not None:
        raise ValueError("pass `simulation_api` or `simulation_url`, not both")
    api: FakeMercadoPagoOrdersAPI | None = None
    remote: RemoteMercadoPagoSimulation | None = None
    if simulate:
        ensure_simulation_allowed(settings, "make_mercado_pago_router")
        if simulation_url is not None:
            remote = RemoteMercadoPagoSimulation(
                simulation_url, client=simulation_client
            )
        else:
            api = simulation_api or simulation_api_for(provider)
            if api is None:
                raise ValueError(
                    "simulate=True needs the simulator the provider runs over: "
                    "build the provider with mercado_pago_provider_for(settings, "
                    "simulate=True), pass simulation_api=, or pass "
                    "simulation_url= for a running fake-api."
                )
    resolved_secret = _resolve_secret(secret, settings, api)
    verify = make_mercado_pago_webhook_delivery_dependency(
        resolved_secret, provider, tolerance_seconds=tolerance_seconds
    )
    route_docs: RouteDocs = RouteDocs(docs_locale)
    route_tags: list[str | Enum] = list(tags or ["mercado-pago"])
    router = APIRouter()

    @router.post(
        webhook_path,
        name=WEBHOOK_ROUTE_NAME,
        tags=route_tags,
        response_model=MercadoPagoWebhookAckSchema,
        include_in_schema=include_in_schema,
        summary=route_docs.summary("mercado_pago.receive_mercado_pago_webhook"),
        description=route_docs.description("mercado_pago.receive_mercado_pago_webhook"),
    )
    async def receive_mercado_pago_webhook(
        delivery: MercadoPagoOrderDelivery = Depends(verify),
    ) -> MercadoPagoWebhookAckSchema:
        """Hand a verified, re-read delivery to ``on_delivery``.

        Args:
            delivery (MercadoPagoOrderDelivery): The delivery.

        Returns:
            MercadoPagoWebhookAckSchema: The acknowledgement.
        """
        await on_delivery(delivery)
        status = None if delivery.order is None else delivery.order.get("status")
        return MercadoPagoWebhookAckSchema(
            ok=True,
            data_id=delivery.notification.data_id,
            order_status=status if isinstance(status, str) else None,
        )

    backend = api if api is not None else remote
    if backend is not None:
        add_simulation_routes(
            router,
            api=backend,
            secret=resolved_secret,
            webhook_route=WEBHOOK_ROUTE_NAME,
            prefix=simulation_prefix,
            route_docs=route_docs,
            include_in_schema=include_in_schema,
        )
    if withdraw is not None:
        _add_withdraw_route(router, withdraw, route_docs, include_in_schema, route_tags)
    return router


def _add_withdraw_route(
    router: APIRouter,
    config: MercadoPagoWithdrawConfig,
    route_docs: RouteDocs,
    include_in_schema: bool,
    tags: list[str | Enum],
) -> None:
    """Mount ``POST {config.path}``: withdraw the whole available balance.

    The route takes no body: it always withdraws everything available
    (``amount_cents=None``), leaving the wallet at zero. Errors are the
    wallet's, answered by the SDK's exception handlers:
    ``409 WALLET_INSUFFICIENT_BALANCE`` with nothing available,
    ``502 WALLET_PAYOUT_REJECTED`` when the payout was refused (debit given
    back), ``502 WALLET_PAYOUT_UNCERTAIN`` when its outcome is unknown
    (debit kept), and whatever ``pix_destination`` raises for a user with no
    key.

    Args:
        router (APIRouter): Where to mount.
        config (MercadoPagoWithdrawConfig): The dependencies.
        route_docs (RouteDocs): The operation text.
        include_in_schema (bool): Whether the route appears in OpenAPI.
        tags (list[str | Enum]): The route's OpenAPI tags.
    """
    session_dependency = session_provider_for(config.session_factory)

    @router.post(
        config.path,
        tags=tags,
        response_model=WithdrawalSchema,
        include_in_schema=include_in_schema,
        summary=route_docs.summary("mercado_pago.withdraw_wallet"),
        description=route_docs.description("mercado_pago.withdraw_wallet"),
    )
    async def withdraw_wallet(
        user_id: UUID = Depends(config.current_user_id),
        destination: PixDestinationSchema = Depends(config.pix_destination),
        session: AsyncSession = Depends(session_dependency),
    ) -> WithdrawalSchema:
        """Pay the caller's whole available balance out to their Pix key.

        Args:
            user_id (UUID): The authenticated user.
            destination (PixDestinationSchema): The user's registered key.
            session (AsyncSession): The request's session.

        Returns:
            WithdrawalSchema: The debit and the provider's answer.
        """
        return await config.service_factory(session).withdraw(
            user_id,
            payout=config.provider(session),
            pix_key=destination.pix_key,
            pix_key_type=PixKeyType(destination.pix_key_type),
        )


__all__: list[str] = [
    "DEFAULT_WEBHOOK_PATH",
    "DEFAULT_WITHDRAW_PATH",
    "WEBHOOK_ROUTE_NAME",
    "MercadoPagoDeliveryHandler",
    "MercadoPagoWebhookAckSchema",
    "MercadoPagoWithdrawConfig",
    "make_mercado_pago_router",
]
