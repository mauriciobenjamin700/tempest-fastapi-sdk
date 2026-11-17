"""MercadoPagoSimulator and the simulation-only router (#780).

The service here writes its **own** webhook route, with its own dependency
call and ``BackgroundTasks``, the way a service that never adopted
``make_mercado_pago_router`` does. Every delivery goes through that route's
signature check and re-read; nothing is mocked between the signed request
and the service's handler.
"""

from __future__ import annotations

import httpx
import pytest
from fastapi import BackgroundTasks, Depends, FastAPI

from tempest_fastapi_sdk.integrations.payment import PixChargeRequest, PixPayer
from tempest_fastapi_sdk.integrations.payment.adapters import (
    SIMULATION_TAG,
    MercadoPagoOrderDelivery,
    MercadoPagoPixProvider,
    MercadoPagoSimulator,
    RemoteMercadoPagoSimulation,
    make_mercado_pago_simulation_router,
    make_mercado_pago_webhook_delivery_dependency,
)
from tempest_fastapi_sdk.settings import EnvironmentSettings, MercadoPagoSettings
from tempest_fastapi_sdk.testing.fakes import FakeMercadoPagoOrdersAPI
from tempest_fastapi_sdk.testing.fakes.mercado_pago import make_fake_orders_app

SECRET: str = "segredo-do-servico"
"""The service's webhook secret."""

WEBHOOK_PATH: str = "/api/webhook/mercadopago"
"""Where the service mounted its own webhook route."""

WEBHOOK_NAME: str = "service_mercado_pago_webhook"
"""The name the service gave its webhook route."""

PAYER: PixPayer = PixPayer(email="comprador@example.com")
"""A payer the Orders API accepts."""


class _Settings(EnvironmentSettings, MercadoPagoSettings):
    """A service's settings: ``ENV`` plus the Mercado Pago mixin."""


def _settings(env: str = "development") -> _Settings:
    """Build settings for one environment, ignoring ``.env``.

    Args:
        env (str): ``development``, ``test`` or ``production``.

    Returns:
        _Settings: The settings.
    """
    return _Settings(_env_file=None, ENV=env, MERCADOPAGO_WEBHOOK_SECRET=SECRET)  # type: ignore[call-arg]


def _service_app(provider: MercadoPagoPixProvider, seen: list[str]) -> FastAPI:
    """Build a service with its own webhook route, confirming in the background.

    Args:
        provider (MercadoPagoPixProvider): The adapter re-reading the order.
        seen (list[str]): Receives ``<external_reference>:<status>`` per
            delivery, from a background task.

    Returns:
        FastAPI: The app, without the simulation routes.
    """
    app = FastAPI()
    verify = make_mercado_pago_webhook_delivery_dependency(SECRET, provider)

    def _confirm(reference: str, status: str) -> None:
        seen.append(f"{reference}:{status}")

    @app.post(WEBHOOK_PATH, name=WEBHOOK_NAME)
    async def webhook(
        tasks: BackgroundTasks,
        delivery: MercadoPagoOrderDelivery = Depends(verify),
    ) -> dict[str, bool]:
        order = delivery.order or {}
        tasks.add_task(
            _confirm, str(order.get("external_reference")), str(order.get("status"))
        )
        return {"ok": True}

    return app


async def _pix(provider: MercadoPagoPixProvider, reference: str) -> str:
    """Create a Pix charge through the adapter.

    Args:
        provider (MercadoPagoPixProvider): The adapter.
        reference (str): The service's correlation id.

    Returns:
        str: The order id.
    """
    charge = await provider.create_pix_charge(
        PixChargeRequest(amount_cents=2_500, reference=reference, payer=PAYER)
    )
    return charge.provider_charge_id


def _client(app: FastAPI) -> httpx.AsyncClient:
    """Open an in-process client on the app.

    Args:
        app (FastAPI): The app.

    Returns:
        httpx.AsyncClient: The client.
    """
    return httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    )


class TestSimulationOnlyRouter:
    """The development routes over a webhook route the service wrote."""

    @pytest.mark.parametrize("webhook_route", [WEBHOOK_NAME, WEBHOOK_PATH])
    async def test_pay_reaches_the_services_own_route(self, webhook_route: str) -> None:
        api = FakeMercadoPagoOrdersAPI(webhook_secret=SECRET)
        provider = MercadoPagoPixProvider(api.http_client())
        await _pix(provider, "compra-1")
        seen: list[str] = []
        app = _service_app(provider, seen)
        app.include_router(
            make_mercado_pago_simulation_router(
                webhook_route=webhook_route,
                simulation_api=api,
                settings=_settings(),
            )
        )
        async with _client(app) as client:
            response = await client.post("/dev/mercado-pago/orders/compra-1/pay")
        assert response.status_code == 200
        body = response.json()
        assert body["order"]["status"] == "processed"
        assert body["delivery"]["status_code"] == 200
        assert body["delivery"]["body"] == {"ok": True}
        assert seen == ["compra-1:processed"]

    async def test_remote_fake_api_is_moved_and_the_service_route_answers(
        self,
    ) -> None:
        api = FakeMercadoPagoOrdersAPI(webhook_secret=SECRET)
        provider = MercadoPagoPixProvider(api.http_client())
        await _pix(provider, "compra-2")
        seen: list[str] = []
        app = _service_app(provider, seen)
        fake = httpx.AsyncClient(
            transport=httpx.ASGITransport(app=make_fake_orders_app(api)),
            base_url="http://mp-fake",
        )
        async with fake:
            app.include_router(
                make_mercado_pago_simulation_router(
                    webhook_route=WEBHOOK_NAME,
                    simulation_url="http://mp-fake",
                    simulation_client=fake,
                    settings=_settings(),
                )
            )
            async with _client(app) as client:
                expired = await client.post("/dev/mercado-pago/orders/compra-2/expire")
                missing = await client.post("/dev/mercado-pago/orders/nada/pay")
                refused = await client.post("/dev/mercado-pago/orders/compra-2/refund")
        assert expired.status_code == 200
        assert expired.json()["order"]["status"] == "canceled"
        assert seen == ["compra-2:canceled"]
        assert missing.status_code == 404
        assert refused.status_code == 409

    def test_routes_are_tagged_and_no_webhook_route_is_mounted(self) -> None:
        router = make_mercado_pago_simulation_router(
            webhook_route=WEBHOOK_NAME,
            simulation_api=FakeMercadoPagoOrdersAPI(webhook_secret=SECRET),
            settings=_settings(),
        )
        paths = {getattr(route, "path", "") for route in router.routes}
        assert paths == {
            "/dev/mercado-pago/orders",
            "/dev/mercado-pago/orders/{order_id}",
            "/dev/mercado-pago/orders/{order_id}/{transition}",
        }
        assert all(
            getattr(route, "tags", None) == [SIMULATION_TAG] for route in router.routes
        )

    def test_refused_in_production(self) -> None:
        with pytest.raises(RuntimeError, match="ENV=development or ENV=test"):
            make_mercado_pago_simulation_router(
                webhook_route=WEBHOOK_NAME,
                simulation_api=FakeMercadoPagoOrdersAPI(webhook_secret=SECRET),
                settings=_settings("production"),
            )

    def test_production_is_refused_before_the_arguments(self) -> None:
        with pytest.raises(RuntimeError, match="make_mercado_pago_simulation_router"):
            make_mercado_pago_simulation_router(
                webhook_route=WEBHOOK_NAME, settings=_settings("production")
            )

    def test_unset_env_counts_as_production(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.delenv("ENV", raising=False)
        monkeypatch.setenv("MERCADOPAGO_WEBHOOK_SECRET", SECRET)
        with pytest.raises(RuntimeError, match="got None"):
            make_mercado_pago_simulation_router(
                webhook_route=WEBHOOK_NAME,
                simulation_api=FakeMercadoPagoOrdersAPI(webhook_secret=SECRET),
            )

    @pytest.mark.parametrize(
        ("kwargs", "message"),
        [
            ({}, "exactly one"),
            (
                {
                    "simulation_url": "http://mp-fake",
                    "simulation_api": FakeMercadoPagoOrdersAPI(),
                },
                "exactly one",
            ),
        ],
    )
    def test_needs_exactly_one_backend(
        self, kwargs: dict[str, object], message: str
    ) -> None:
        with pytest.raises(ValueError, match=message):
            make_mercado_pago_simulation_router(
                webhook_route=WEBHOOK_NAME,
                settings=_settings(),
                **kwargs,  # type: ignore[arg-type]
            )

    async def test_client_needs_url(self) -> None:
        async with httpx.AsyncClient() as client:
            with pytest.raises(ValueError, match="needs `simulation_url`"):
                make_mercado_pago_simulation_router(
                    webhook_route=WEBHOOK_NAME,
                    simulation_api=FakeMercadoPagoOrdersAPI(),
                    simulation_client=client,
                    settings=_settings(),
                )


class TestSimulator:
    """MercadoPagoSimulator outside any route."""

    async def test_apply_in_process_by_path(self) -> None:
        api = FakeMercadoPagoOrdersAPI(webhook_secret=SECRET)
        provider = MercadoPagoPixProvider(api.http_client())
        order_id = await _pix(provider, "compra-3")
        seen: list[str] = []
        app = _service_app(provider, seen)
        simulator = MercadoPagoSimulator(
            api, settings=_settings(), app=app, webhook_url=WEBHOOK_PATH
        )
        result = await simulator.apply("compra-3", "pay")
        assert result.order["id"] == order_id
        assert result.order["status"] == "processed"
        assert result.delivery.status_code == 200
        assert seen == ["compra-3:processed"]

    async def test_apply_over_an_injected_client_to_an_absolute_url(self) -> None:
        api = FakeMercadoPagoOrdersAPI(webhook_secret=SECRET)
        provider = MercadoPagoPixProvider(api.http_client())
        await _pix(provider, "compra-4")
        seen: list[str] = []
        app = _service_app(provider, seen)
        async with _client(app) as client:
            simulator = MercadoPagoSimulator(
                RemoteMercadoPagoSimulation(
                    "http://mp-fake",
                    client=httpx.AsyncClient(
                        transport=httpx.ASGITransport(app=make_fake_orders_app(api))
                    ),
                ),
                secret=SECRET,
                settings=_settings(),
                webhook_url=f"http://test{WEBHOOK_PATH}",
                client=client,
            )
            await simulator.apply("compra-4", "pay")
            result = await simulator.apply("compra-4", "refund", amount_cents=1_000)
        assert result.order["status"] == "processed"
        assert result.delivery.status_code == 200
        assert seen[0] == "compra-4:processed"
        assert len(seen) == 2

    async def test_wrong_secret_is_reported_not_raised(self) -> None:
        api = FakeMercadoPagoOrdersAPI(webhook_secret=SECRET)
        provider = MercadoPagoPixProvider(api.http_client())
        await _pix(provider, "compra-5")
        seen: list[str] = []
        simulator = MercadoPagoSimulator(
            api,
            secret="outro-segredo",
            settings=_settings(),
            app=_service_app(provider, seen),
            webhook_url=WEBHOOK_PATH,
        )
        result = await simulator.apply("compra-5", "pay")
        assert result.delivery.status_code == 401
        assert seen == []

    async def test_unknown_reference_and_bad_state_raise(self) -> None:
        api = FakeMercadoPagoOrdersAPI(webhook_secret=SECRET)
        provider = MercadoPagoPixProvider(api.http_client())
        await _pix(provider, "compra-6")
        simulator = MercadoPagoSimulator(
            api,
            settings=_settings(),
            app=_service_app(provider, []),
            webhook_url=WEBHOOK_PATH,
        )
        with pytest.raises(KeyError):
            await simulator.apply("nada", "pay")
        with pytest.raises(ValueError):
            await simulator.apply("compra-6", "refund")

    async def test_apply_without_a_target_is_refused(self) -> None:
        simulator = MercadoPagoSimulator(
            FakeMercadoPagoOrdersAPI(webhook_secret=SECRET), settings=_settings()
        )
        with pytest.raises(RuntimeError, match="apply_to"):
            await simulator.apply("x", "pay")

    def test_refused_in_production(self) -> None:
        with pytest.raises(RuntimeError, match="MercadoPagoSimulator"):
            MercadoPagoSimulator(
                FakeMercadoPagoOrdersAPI(), settings=_settings("production")
            )

    @pytest.mark.parametrize(
        ("kwargs", "message"),
        [
            ({"app": FastAPI()}, "needs `webhook_url`"),
            ({"webhook_url": "/hook"}, "must be absolute"),
            (
                {
                    "app": FastAPI(),
                    "webhook_url": "/hook",
                    "client": httpx.AsyncClient(),
                },
                "not both",
            ),
        ],
    )
    def test_target_arguments_are_checked(
        self, kwargs: dict[str, object], message: str
    ) -> None:
        with pytest.raises(ValueError, match=message):
            MercadoPagoSimulator(
                FakeMercadoPagoOrdersAPI(),
                settings=_settings(),
                **kwargs,  # type: ignore[arg-type]
            )

    def test_secret_falls_back_to_the_in_memory_simulator(self) -> None:
        settings = _Settings(_env_file=None, ENV="test")  # type: ignore[call-arg]
        api = FakeMercadoPagoOrdersAPI(webhook_secret="do-simulador")
        assert MercadoPagoSimulator(api, settings=settings).secret == "do-simulador"

    def test_remote_backend_without_a_secret_is_refused(self) -> None:
        settings = _Settings(_env_file=None, ENV="test")  # type: ignore[call-arg]
        with pytest.raises(ValueError, match="non-empty webhook secret"):
            MercadoPagoSimulator(
                RemoteMercadoPagoSimulation("http://mp-fake"), settings=settings
            )
