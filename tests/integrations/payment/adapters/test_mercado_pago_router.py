"""make_mercado_pago_router: the real webhook path, and the dev simulation over it.

Every delivery goes through the SDK's signature check and the re-read of the
order from :class:`FakeMercadoPagoOrdersAPI`; nothing is mocked between the
signed request and ``on_delivery``.
"""

from __future__ import annotations

import subprocess
import sys

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from tempest_fastapi_sdk.integrations.payment import PixChargeRequest, PixPayer
from tempest_fastapi_sdk.integrations.payment.adapters import (
    SIMULATION_TAG,
    MercadoPagoOrderDelivery,
    MercadoPagoPixProvider,
    make_mercado_pago_router,
    mercado_pago_provider_for,
    simulation_api_for,
)
from tempest_fastapi_sdk.settings import EnvironmentSettings, MercadoPagoSettings
from tempest_fastapi_sdk.testing.fakes import (
    FakeMercadoPagoOrdersAPI,
    build_mercado_pago_delivery,
)

SECRET: str = "segredo-do-servico"
"""The service's webhook secret."""

PAYER: PixPayer = PixPayer(email="comprador@example.com")
"""A payer the Orders API accepts."""


class _Settings(EnvironmentSettings, MercadoPagoSettings):
    """A service's settings: ``ENV`` plus the Mercado Pago mixin."""


def _settings(env: str) -> _Settings:
    """Build settings for one environment, ignoring ``.env``.

    Args:
        env (str): ``development``, ``test`` or ``production``.

    Returns:
        _Settings: The settings.
    """
    return _Settings(_env_file=None, ENV=env, MERCADOPAGO_WEBHOOK_SECRET=SECRET)  # type: ignore[call-arg]


class _Inbox:
    """Collects what ``on_delivery`` received."""

    def __init__(self) -> None:
        """Start empty."""
        self.deliveries: list[MercadoPagoOrderDelivery] = []

    async def __call__(self, delivery: MercadoPagoOrderDelivery) -> None:
        """Record one delivery.

        Args:
            delivery (MercadoPagoOrderDelivery): The delivery.
        """
        self.deliveries.append(delivery)


def _app(
    provider: MercadoPagoPixProvider,
    inbox: _Inbox,
    *,
    simulate: bool,
    settings: _Settings | None = None,
) -> FastAPI:
    """Mount the router the way a service does.

    Args:
        provider (MercadoPagoPixProvider): The adapter.
        inbox (_Inbox): The handler.
        simulate (bool): Mount the simulation routes.
        settings (_Settings | None): Settings; development by default.

    Returns:
        FastAPI: The app.
    """
    app = FastAPI()
    app.include_router(
        make_mercado_pago_router(
            provider,
            on_delivery=inbox,
            settings=settings or _settings("development"),
            simulate=simulate,
        )
    )
    return app


async def _pix(provider: MercadoPagoPixProvider, reference: str = "pedido-1") -> str:
    """Create a Pix charge through the adapter.

    Args:
        provider (MercadoPagoPixProvider): The adapter.
        reference (str): The charge's reference.

    Returns:
        str: The order id.
    """
    charge = await provider.create_pix_charge(
        PixChargeRequest(amount_cents=2_500, reference=reference, payer=PAYER)
    )
    return charge.provider_charge_id


class TestWebhook:
    """The webhook route, without simulation."""

    async def test_signed_delivery_reaches_on_delivery_with_the_reread_order(
        self,
    ) -> None:
        api = FakeMercadoPagoOrdersAPI(webhook_secret=SECRET)
        provider = MercadoPagoPixProvider(api.http_client())
        order_id = await _pix(provider)
        api.pay(order_id)
        inbox = _Inbox()
        delivery = build_mercado_pago_delivery({"id": order_id}, secret=SECRET)
        with TestClient(_app(provider, inbox, simulate=False)) as client:
            response = delivery.send_sync(client, "/webhooks/mercado-pago")
        assert response.status_code == 200
        assert response.json() == {
            "ok": True,
            "data_id": order_id,
            "order_status": "processed",
        }
        assert [d.order["status"] for d in inbox.deliveries if d.order] == ["processed"]

    async def test_wrong_secret_is_401_and_on_delivery_is_not_called(self) -> None:
        api = FakeMercadoPagoOrdersAPI(webhook_secret=SECRET)
        provider = MercadoPagoPixProvider(api.http_client())
        order_id = await _pix(provider)
        inbox = _Inbox()
        delivery = build_mercado_pago_delivery({"id": order_id}, secret="outro")
        with TestClient(_app(provider, inbox, simulate=False)) as client:
            response = delivery.send_sync(client, "/webhooks/mercado-pago")
        assert response.status_code == 401
        assert inbox.deliveries == []

    def test_simulation_routes_are_absent_without_simulate(self) -> None:
        api = FakeMercadoPagoOrdersAPI(webhook_secret=SECRET)
        router = make_mercado_pago_router(
            MercadoPagoPixProvider(api.http_client()),
            on_delivery=_Inbox(),
            secret=SECRET,
        )
        paths = {getattr(route, "path", "") for route in router.routes}
        assert paths == {"/webhooks/mercado-pago"}

    def test_empty_secret_is_refused(self) -> None:
        api = FakeMercadoPagoOrdersAPI(webhook_secret=SECRET)
        with pytest.raises(ValueError, match="non-empty webhook secret"):
            make_mercado_pago_router(
                MercadoPagoPixProvider(api.http_client()),
                on_delivery=_Inbox(),
                secret="",
            )


class TestProductionGate:
    """``simulate=True`` never builds outside development and test."""

    def test_router_refuses_simulation_in_production(self) -> None:
        provider = mercado_pago_provider_for(_settings("test"), simulate=True)
        with pytest.raises(RuntimeError, match="ENV=development or ENV=test"):
            make_mercado_pago_router(
                provider,
                on_delivery=_Inbox(),
                settings=_settings("production"),
                simulate=True,
            )

    def test_provider_helper_refuses_simulation_in_production(self) -> None:
        with pytest.raises(RuntimeError, match="ENV=development or ENV=test"):
            mercado_pago_provider_for(_settings("production"), simulate=True)

    def test_unset_env_counts_as_production(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.delenv("ENV", raising=False)
        bare = MercadoPagoSettings(_env_file=None, MERCADOPAGO_WEBHOOK_SECRET=SECRET)  # type: ignore[call-arg]
        with pytest.raises(RuntimeError, match="got None"):
            mercado_pago_provider_for(bare, simulate=True)

    def test_simulate_without_a_simulator_is_refused(self) -> None:
        api = FakeMercadoPagoOrdersAPI(webhook_secret=SECRET)
        with pytest.raises(ValueError, match="simulator the provider runs over"):
            make_mercado_pago_router(
                MercadoPagoPixProvider(api.http_client()),
                on_delivery=_Inbox(),
                settings=_settings("development"),
                simulate=True,
            )


class TestSimulation:
    """The front end's flow: create through the service, then simulate."""

    def test_routes_are_mounted_and_tagged(self) -> None:
        provider = mercado_pago_provider_for(_settings("development"), simulate=True)
        schema = _app(provider, _Inbox(), simulate=True).openapi()
        prefix = "/dev/mercado-pago/orders"
        assert set(schema["paths"]) == {
            "/webhooks/mercado-pago",
            prefix,
            f"{prefix}/{{order_id}}",
            f"{prefix}/{{order_id}}/{{transition}}",
        }
        transition = schema["paths"][f"{prefix}/{{order_id}}/{{transition}}"]["post"]
        assert transition["tags"] == [SIMULATION_TAG]

    async def test_pay_delivers_the_processed_order_through_the_webhook(
        self,
    ) -> None:
        provider = mercado_pago_provider_for(_settings("development"), simulate=True)
        order_id = await _pix(provider)
        inbox = _Inbox()
        with TestClient(_app(provider, inbox, simulate=True)) as client:
            response = client.post(f"/dev/mercado-pago/orders/{order_id}/pay")
            listed = client.get("/dev/mercado-pago/orders")
        assert response.status_code == 200
        body = response.json()
        assert body["order"]["status"] == "processed"
        assert body["delivery"]["status_code"] == 200
        assert body["delivery"]["body"]["order_status"] == "processed"
        assert [d.order["status"] for d in inbox.deliveries if d.order] == ["processed"]
        assert [order["id"] for order in listed.json()] == [order_id]

    async def test_partial_refund_then_cancel_of_another_order(self) -> None:
        provider = mercado_pago_provider_for(_settings("development"), simulate=True)
        paid = await _pix(provider, "pedido-pago")
        pending = await _pix(provider, "pedido-pendente")
        inbox = _Inbox()
        with TestClient(_app(provider, inbox, simulate=True)) as client:
            client.post(f"/dev/mercado-pago/orders/{paid}/pay")
            refund = client.post(
                f"/dev/mercado-pago/orders/{paid}/refund", params={"amount_cents": 500}
            )
            cancel = client.post(f"/dev/mercado-pago/orders/{pending}/cancel")
        assert refund.status_code == 200
        payment = refund.json()["order"]["transactions"]["payments"][0]
        assert payment["refunded_amount"] == "5.00"
        assert refund.json()["order"]["status_detail"] == "partially_refunded"
        assert cancel.json()["order"]["status"] == "canceled"
        assert [d.order["status"] for d in inbox.deliveries if d.order] == [
            "processed",
            "processed",
            "canceled",
        ]

    async def test_refund_before_payment_is_409_and_delivers_nothing(self) -> None:
        provider = mercado_pago_provider_for(_settings("development"), simulate=True)
        order_id = await _pix(provider)
        inbox = _Inbox()
        with TestClient(_app(provider, inbox, simulate=True)) as client:
            response = client.post(f"/dev/mercado-pago/orders/{order_id}/refund")
            unknown = client.post("/dev/mercado-pago/orders/ORDNOPE/pay")
            bad = client.post(f"/dev/mercado-pago/orders/{order_id}/teleport")
        assert response.status_code == 409
        assert unknown.status_code == 404
        assert bad.status_code == 422
        assert inbox.deliveries == []

    async def test_routes_accept_the_external_reference(self) -> None:
        provider = mercado_pago_provider_for(_settings("development"), simulate=True)
        order_id = await _pix(provider, "compra-123")
        inbox = _Inbox()
        with TestClient(_app(provider, inbox, simulate=True)) as client:
            found = client.get("/dev/mercado-pago/orders/compra-123")
            paid = client.post("/dev/mercado-pago/orders/compra-123/pay")
            missing = client.get("/dev/mercado-pago/orders/compra-404")
        assert found.json()["id"] == order_id
        assert paid.json()["order"]["id"] == order_id
        assert paid.json()["delivery"]["body"]["data_id"] == order_id
        assert missing.status_code == 404
        assert [d.order["status"] for d in inbox.deliveries if d.order] == ["processed"]

    async def test_signature_is_the_routes_secret_not_the_simulators(self) -> None:
        api = FakeMercadoPagoOrdersAPI(webhook_secret="segredo-do-simulador")
        provider = mercado_pago_provider_for(
            _settings("development"), simulate=True, simulation_api=api
        )
        assert simulation_api_for(provider) is api
        order_id = await _pix(provider)
        inbox = _Inbox()
        with TestClient(_app(provider, inbox, simulate=True)) as client:
            response = client.post(f"/dev/mercado-pago/orders/{order_id}/pay")
        assert response.json()["delivery"]["status_code"] == 200
        assert len(inbox.deliveries) == 1


def test_simulate_false_does_not_import_the_simulator() -> None:
    code = (
        "import sys\n"
        "from tempest_fastapi_sdk.integrations.payment.adapters import (\n"
        "    MercadoPagoPixProvider, make_mercado_pago_router)\n"
        "from tempest_fastapi_sdk.utils import HTTPClient\n"
        "async def handle(delivery: object) -> None: ...\n"
        "make_mercado_pago_router(MercadoPagoPixProvider(HTTPClient()),\n"
        "    on_delivery=handle, secret='s')\n"
        "print('tempest_fastapi_sdk.testing.fakes.mercado_pago' in sys.modules)\n"
    )
    result = subprocess.run(
        [sys.executable, "-c", code], capture_output=True, text=True, check=True
    )
    assert result.stdout.strip() == "False"
