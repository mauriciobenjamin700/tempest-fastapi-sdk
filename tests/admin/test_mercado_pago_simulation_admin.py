"""mercado_pago_simulation_actions: simulate a payment from the admin panel (#780).

The service has its own webhook route that confirms the charge on its own
database session, the panel lists the service's charges, and the action
moves the order on the simulator and delivers the signed webhook to that
route. The database is a SQLite **file**, so the panel's read and the
webhook's write are two connections, as in production.
"""

from __future__ import annotations

import html
import re
from collections.abc import AsyncIterator
from dataclasses import dataclass
from pathlib import Path

import pytest
from fastapi import Depends, FastAPI
from httpx import ASGITransport, AsyncClient
from sqlalchemy import String, select, update
from sqlalchemy.orm import Mapped, mapped_column

from tempest_fastapi_sdk import (
    AdminSite,
    AsyncDatabaseManager,
    BaseModel,
    BaseUserModel,
    UserModelAuthBackend,
    make_admin_router,
)
from tempest_fastapi_sdk.admin import (
    SIMULATION_ACTION_PREFIX,
    AdminModel,
    mercado_pago_simulation_actions,
)
from tempest_fastapi_sdk.admin.actions import resolve_admin_action
from tempest_fastapi_sdk.integrations.payment import PixChargeRequest, PixPayer
from tempest_fastapi_sdk.integrations.payment.adapters import (
    MercadoPagoOrderDelivery,
    MercadoPagoPixProvider,
    MercadoPagoSimulator,
    make_mercado_pago_webhook_delivery_dependency,
)
from tempest_fastapi_sdk.settings import EnvironmentSettings, MercadoPagoSettings
from tempest_fastapi_sdk.testing import admin_login
from tempest_fastapi_sdk.testing.fakes import FakeMercadoPagoOrdersAPI

ADMIN_SECRET: str = "y" * 48
"""Admin session secret."""

WEBHOOK_SECRET: str = "segredo-do-servico"
"""The service's Mercado Pago webhook secret."""

WEBHOOK_PATH: str = "/api/webhook/mercadopago"
"""The service's own webhook route."""

SLUG: str = "mp_sim_admin_charges"
"""The charge table's slug in the panel."""


class _SimAdminUser(BaseUserModel):
    __tablename__ = "mp_sim_admin_users"


class _Charge(BaseModel):
    """The service's charge table."""

    __tablename__ = "mp_sim_admin_charges"

    correlation_id: Mapped[str] = mapped_column(String(64), unique=True)
    status: Mapped[str] = mapped_column(String(16), default="pending")


class _Settings(EnvironmentSettings, MercadoPagoSettings):
    """A service's settings: ``ENV`` plus the Mercado Pago mixin."""


@dataclass
class _Panel:
    """The panel, the database and the simulator behind it."""

    client: AsyncClient
    db: AsyncDatabaseManager
    provider: MercadoPagoPixProvider


@pytest.fixture
async def panel(tmp_path: Path) -> AsyncIterator[_Panel]:
    """Open a service with its webhook route and the panel with the actions.

    Args:
        tmp_path (Path): Where the SQLite file lives.

    Yields:
        _Panel: The panel.
    """
    db = AsyncDatabaseManager(f"sqlite+aiosqlite:///{tmp_path / 'svc.db'}")
    await db.connect()
    await db.create_tables()
    async with db.get_session_context() as session:
        user = _SimAdminUser(email="qa@example.com", hashed_password="", is_admin=True)
        user.set_password("hunter2")
        session.add(user)
        await session.commit()
    settings = _Settings(  # type: ignore[call-arg]
        _env_file=None, ENV="development", MERCADOPAGO_WEBHOOK_SECRET=WEBHOOK_SECRET
    )
    api = FakeMercadoPagoOrdersAPI(webhook_secret=WEBHOOK_SECRET)
    provider = MercadoPagoPixProvider(api.http_client())
    verify = make_mercado_pago_webhook_delivery_dependency(WEBHOOK_SECRET, provider)
    app = FastAPI()

    @app.post(WEBHOOK_PATH)
    async def webhook(
        delivery: MercadoPagoOrderDelivery = Depends(verify),
    ) -> dict[str, bool]:
        order = delivery.order or {}
        if order.get("status") == "processed":
            async with db.get_session_context() as session:
                await session.execute(
                    update(_Charge)
                    .where(_Charge.correlation_id == order["external_reference"])
                    .values(status="paid")
                )
                await session.commit()
        return {"ok": True}

    simulator = MercadoPagoSimulator(
        api, settings=settings, app=app, webhook_url=WEBHOOK_PATH
    )
    site = AdminSite(title="Admin")
    site.register(
        AdminModel(_Charge, actions=mercado_pago_simulation_actions(simulator))
    )
    app.include_router(
        make_admin_router(
            site,
            db=db,
            auth_backend=UserModelAuthBackend(_SimAdminUser),
            secret_key=ADMIN_SECRET,
            cookie_secure=False,
        )
    )
    async with AsyncClient(
        transport=ASGITransport(app=app), base_url="http://test"
    ) as client:
        await admin_login(client, "qa@example.com", "hunter2")
        yield _Panel(client=client, db=db, provider=provider)
    await db.drop_tables()
    await db.disconnect()


async def _charge(p: _Panel, reference: str, *, with_order: bool = True) -> str:
    """Insert a pending charge, with its order on the simulator.

    Args:
        p (_Panel): The panel.
        reference (str): The correlation id.
        with_order (bool): Also create the order on the simulator.

    Returns:
        str: The row id.
    """
    if with_order:
        await p.provider.create_pix_charge(
            PixChargeRequest(
                amount_cents=4_000,
                reference=reference,
                payer=PixPayer(email="comprador@example.com"),
            )
        )
    async with p.db.get_session_context() as session:
        row = _Charge(correlation_id=reference)
        session.add(row)
        await session.commit()
        return str(row.id)


async def _status(p: _Panel, reference: str) -> str:
    async with p.db.get_session_context() as session:
        result = await session.execute(
            select(_Charge.status).where(_Charge.correlation_id == reference)
        )
        return str(result.scalar_one())


async def _run(p: _Panel, transition: str, row_ids: list[str]) -> str:
    """Run one simulation action from the list page.

    Returns:
        str: The list page after the redirect, with the flash.
    """
    page = await p.client.get(f"/admin/m/{SLUG}/")
    match = re.search(r'name="csrf_token" value="([^"]+)"', page.text)
    assert match is not None
    response = await p.client.post(
        f"/admin/m/{SLUG}/bulk",
        data={
            "csrf_token": match.group(1),
            "action": f"custom:{SIMULATION_ACTION_PREFIX}{transition}",
            "ids": row_ids,
        },
        follow_redirects=False,
    )
    assert response.status_code == 303
    return (await p.client.get(response.headers["location"])).text


async def test_simulate_payment_confirms_through_the_services_route(
    panel: _Panel,
) -> None:
    first = await _charge(panel, "compra-a")
    second = await _charge(panel, "compra-b")
    page = await _run(panel, "pay", [second, first])
    assert (
        "Simular pagamento: 2 entregue(s) — compra-b (processed), "
        "compra-a (processed)." in page
    )
    assert await _status(panel, "compra-a") == "paid"
    assert await _status(panel, "compra-b") == "paid"


async def test_failures_are_named_in_the_flash(panel: _Panel) -> None:
    unknown = await _charge(panel, "sem-pedido", with_order=False)
    pending = await _charge(panel, "compra-c")
    page = await _run(panel, "refund", [unknown, pending])
    flash = html.unescape(page)
    assert (
        "Simular estorno: 0 entregue(s); falharam — sem-pedido: pedido não "
        "encontrado no simulador; compra-c: 409 cannot_refund_order: the order "
        "cannot be refunded because the current status, 'action_required', "
        "doesn't allow refunds." in flash
    )
    assert await _status(panel, "compra-c") == "pending"


async def test_menu_offers_the_three_moves_with_refund_dangerous(
    panel: _Panel,
) -> None:
    await _charge(panel, "compra-d")
    page = await panel.client.get(f"/admin/m/{SLUG}/")
    for label in ("Simular pagamento", "Simular expiração"):
        assert label in page.text
    assert (
        f'<option value="custom:{SIMULATION_ACTION_PREFIX}refund" data-confirm="Run'
        ' &#34;Simular estorno&#34; on the checked rows? This cannot be undone.">'
        in page.text
    )


def _simulator(**kwargs: object) -> MercadoPagoSimulator:
    settings = _Settings(  # type: ignore[call-arg]
        _env_file=None, ENV="test", MERCADOPAGO_WEBHOOK_SECRET=WEBHOOK_SECRET
    )
    return MercadoPagoSimulator(
        FakeMercadoPagoOrdersAPI(),
        settings=settings,
        **kwargs,  # type: ignore[arg-type]
    )


def test_actions_carry_names_labels_and_danger() -> None:
    actions = [
        resolve_admin_action(handler)
        for handler in mercado_pago_simulation_actions(
            _simulator(app=FastAPI(), webhook_url=WEBHOOK_PATH),
            transitions=("pay", "cancel", "refund"),
            labels={"cancel": "Cancelar no MP"},
        )
    ]
    assert [(a.name, a.label, a.dangerous) for a in actions] == [
        ("simulate_mercado_pago_pay", "Simular pagamento", False),
        ("simulate_mercado_pago_cancel", "Cancelar no MP", False),
        ("simulate_mercado_pago_refund", "Simular estorno", True),
    ]


@pytest.mark.parametrize(
    ("simulator_kwargs", "transitions", "message"),
    [
        ({}, ("pay",), "delivers on its own"),
        ({"app": FastAPI(), "webhook_url": "/h"}, ("pay", "pay"), "repeated"),
        ({"app": FastAPI(), "webhook_url": "/h"}, ("teleport",), "unknown"),
    ],
)
def test_bad_configurations_are_refused(
    simulator_kwargs: dict[str, object], transitions: tuple[str, ...], message: str
) -> None:
    with pytest.raises(ValueError, match=message):
        mercado_pago_simulation_actions(
            _simulator(**simulator_kwargs),
            transitions=transitions,  # type: ignore[arg-type]
        )
