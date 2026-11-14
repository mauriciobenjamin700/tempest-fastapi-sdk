"""Remote simulation: one ``tempest mercado-pago fake-api`` shared by processes.

The fake-api runs as a **subprocess** started through the real CLI entry
point. The service (this process) charges through the real adapter pointed
at it by ``MERCADOPAGO_BASE_URL`` and mounts
``make_mercado_pago_router(simulate=True, simulation_url=...)``; a "worker"
is another subprocess that builds its own provider from the environment and
re-reads the order. Nothing is mocked between the signed delivery and
``on_delivery``.
"""

from __future__ import annotations

import os
import socket
import subprocess
import sys
import time
import uuid
from collections.abc import Iterator
from pathlib import Path

import httpx
import pytest
from fastapi import FastAPI

from tempest_fastapi_sdk.integrations.payment import PixChargeRequest, PixPayer
from tempest_fastapi_sdk.integrations.payment.adapters import (
    MercadoPagoOrderDelivery,
    MercadoPagoPixProvider,
    RemoteMercadoPagoSimulation,
    make_mercado_pago_router,
    mercado_pago_provider_for,
)
from tempest_fastapi_sdk.settings import EnvironmentSettings, MercadoPagoSettings
from tempest_fastapi_sdk.testing.fakes import FakeMercadoPagoOrdersAPI

SECRET: str = "segredo-do-servico-remoto"
"""The service's webhook secret; the fake-api never learns it."""

PAYER: PixPayer = PixPayer(email="comprador@example.com")
"""A payer the Orders API accepts."""

WORKER_SCRIPT: str = (
    "import asyncio, sys\n"
    "from tempest_fastapi_sdk.integrations.payment.adapters import (\n"
    "    mercado_pago_provider_for)\n"
    "async def main() -> None:\n"
    "    provider = mercado_pago_provider_for()\n"
    "    charge = await provider.get_pix_charge(sys.argv[1])\n"
    "    print(charge.status.value)\n"
    "asyncio.run(main())\n"
)
"""A worker: provider from ``MERCADOPAGO_*`` in its environment, one re-read."""


class _Settings(EnvironmentSettings, MercadoPagoSettings):
    """A service's settings: ``ENV`` plus the Mercado Pago mixin."""


def _free_port() -> int:
    """Pick a free TCP port on the loopback.

    Returns:
        int: The port.
    """
    with socket.socket() as probe:
        probe.bind(("127.0.0.1", 0))
        port: int = probe.getsockname()[1]
        return port


@pytest.fixture(scope="module")
def fake_api(tmp_path_factory: pytest.TempPathFactory) -> Iterator[str]:
    """Run ``tempest mercado-pago fake-api`` in its own process.

    Args:
        tmp_path_factory (pytest.TempPathFactory): A working directory with
            no ``.env``.

    Yields:
        str: The fake-api's base URL.

    Raises:
        RuntimeError: When the server does not answer within 30 seconds.
    """
    port = _free_port()
    url = f"http://127.0.0.1:{port}"
    process = subprocess.Popen(
        [
            sys.executable,
            "-c",
            "from tempest_fastapi_sdk.cli.entrypoint import main; main()",
            "mercado-pago",
            "fake-api",
            "--port",
            str(port),
        ],
        cwd=tmp_path_factory.mktemp("fake-api"),
        stdout=subprocess.DEVNULL,
        stderr=subprocess.PIPE,
    )
    try:
        deadline = time.monotonic() + 30
        while True:
            try:
                if httpx.get(f"{url}/_fake/orders").status_code == 200:
                    break
            except httpx.HTTPError:
                pass
            if process.poll() is not None or time.monotonic() > deadline:
                stderr = process.stderr.read() if process.stderr else b""
                raise RuntimeError(f"fake-api did not start: {stderr!r}")
            time.sleep(0.05)
        yield url
    finally:
        process.terminate()
        process.wait(timeout=10)


def _settings(base_url: str, env: str = "development") -> _Settings:
    """Build the service's settings, pointed at the fake-api.

    Args:
        base_url (str): The fake-api's base URL.
        env (str): The deployment environment.

    Returns:
        _Settings: The settings, ignoring ``.env``.
    """
    return _Settings(  # type: ignore[call-arg]
        _env_file=None,
        ENV=env,
        MERCADOPAGO_WEBHOOK_SECRET=SECRET,
        MERCADOPAGO_BASE_URL=base_url,
    )


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

    @property
    def statuses(self) -> list[str]:
        """The re-read order status of each delivery.

        Returns:
            list[str]: The statuses, in arrival order.
        """
        return [str(d.order["status"]) for d in self.deliveries if d.order]


def _service(
    settings: _Settings,
    inbox: _Inbox,
    *,
    simulation_url: str,
    client: httpx.AsyncClient | None = None,
) -> tuple[FastAPI, MercadoPagoPixProvider]:
    """Build the API process the way a service does, with the SDK only.

    Args:
        settings (_Settings): The service's settings.
        inbox (_Inbox): The delivery handler.
        simulation_url (str): The fake-api the simulation routes drive.
        client (httpx.AsyncClient | None): Injected into the router.

    Returns:
        tuple[FastAPI, MercadoPagoPixProvider]: The app and its provider.
    """
    provider = mercado_pago_provider_for(settings)
    app = FastAPI()
    app.include_router(
        make_mercado_pago_router(
            provider,
            on_delivery=inbox,
            settings=settings,
            simulate=True,
            simulation_url=simulation_url,
            simulation_client=client,
        )
    )
    return app, provider


def _worker_reads(
    fake_url: str, order_id: str, cwd: Path, env: str = "development"
) -> str:
    """Re-read a charge from a separate worker process.

    Args:
        fake_url (str): The fake-api's base URL.
        order_id (str): The charge id.
        cwd (Path): A directory with no ``.env``.
        env (str): The worker's ``ENV``.

    Returns:
        str: The charge's canonical status, as the worker printed it.
    """
    environment = {
        **os.environ,
        "ENV": env,
        "MERCADOPAGO_BASE_URL": fake_url,
        "MERCADOPAGO_ACCESS_TOKEN": "TEST-worker",
        "MERCADOPAGO_WEBHOOK_SECRET": SECRET,
    }
    result = subprocess.run(
        [sys.executable, "-c", WORKER_SCRIPT, order_id],
        capture_output=True,
        text=True,
        check=True,
        cwd=cwd,
        env=environment,
        timeout=60,
    )
    return result.stdout.strip()


async def _pix(provider: MercadoPagoPixProvider, reference: str) -> str:
    """Create a Pix charge through the adapter.

    Args:
        provider (MercadoPagoPixProvider): The adapter.
        reference (str): The service's ``correlation_id``.

    Returns:
        str: The order id.
    """
    charge = await provider.create_pix_charge(
        PixChargeRequest(amount_cents=4_000, reference=reference, payer=PAYER)
    )
    return charge.provider_charge_id


class TestAcrossProcesses:
    """The acceptance of #767: API and worker in separate processes."""

    async def test_worker_process_sees_the_order_and_its_simulated_moves(
        self, fake_api: str, tmp_path: Path
    ) -> None:
        correlation_id = f"compra-{uuid.uuid4().hex}"
        inbox = _Inbox()
        app, provider = _service(_settings(fake_api), inbox, simulation_url=fake_api)
        order_id = await _pix(provider, correlation_id)
        assert order_id.startswith("ORD")
        assert _worker_reads(fake_api, order_id, tmp_path) == "pending"
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(
            transport=transport, base_url="http://service"
        ) as client:
            paid = await client.post(f"/dev/mercado-pago/orders/{correlation_id}/pay")
            assert _worker_reads(fake_api, order_id, tmp_path) == "paid"
            refunded = await client.post(
                f"/dev/mercado-pago/orders/{correlation_id}/refund"
            )
        assert paid.status_code == 200
        assert paid.json()["order"]["id"] == order_id
        assert paid.json()["delivery"] == {
            "action": "order.processed",
            "status_code": 200,
            "body": {"ok": True, "data_id": order_id, "order_status": "processed"},
        }
        assert refunded.status_code == 200
        assert refunded.json()["delivery"]["status_code"] == 200
        assert inbox.statuses == ["processed", "refunded"]
        assert _worker_reads(fake_api, order_id, tmp_path) == "refunded"

    async def test_expire_by_correlation_id_with_an_injected_client(
        self, fake_api: str, tmp_path: Path
    ) -> None:
        correlation_id = f"compra-{uuid.uuid4().hex}"
        inbox = _Inbox()
        async with httpx.AsyncClient(timeout=10.0) as remote:
            app, provider = _service(
                _settings(fake_api), inbox, simulation_url=fake_api, client=remote
            )
            order_id = await _pix(provider, correlation_id)
            transport = httpx.ASGITransport(app=app)
            async with httpx.AsyncClient(
                transport=transport, base_url="http://service"
            ) as client:
                found = await client.get(f"/dev/mercado-pago/orders/{correlation_id}")
                expired = await client.post(
                    f"/dev/mercado-pago/orders/{correlation_id}/expire"
                )
                listed = await client.get("/dev/mercado-pago/orders")
        assert found.json()["id"] == order_id
        assert expired.json()["order"]["status_detail"] == "expired"
        assert expired.json()["delivery"]["status_code"] == 200
        assert order_id in [order["id"] for order in listed.json()]
        assert inbox.statuses == ["canceled"]
        assert _worker_reads(fake_api, order_id, tmp_path) == "expired"

    async def test_unknown_reference_is_404_and_bad_state_is_409(
        self, fake_api: str
    ) -> None:
        correlation_id = f"compra-{uuid.uuid4().hex}"
        inbox = _Inbox()
        app, provider = _service(_settings(fake_api), inbox, simulation_url=fake_api)
        await _pix(provider, correlation_id)
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(
            transport=transport, base_url="http://service"
        ) as client:
            unknown = await client.post("/dev/mercado-pago/orders/nao-existe/pay")
            missing = await client.get("/dev/mercado-pago/orders/nao-existe")
            early = await client.post(
                f"/dev/mercado-pago/orders/{correlation_id}/refund"
            )
        assert unknown.status_code == 404
        assert missing.status_code == 404
        assert early.status_code == 409
        assert "cannot_refund_order" in early.json()["detail"]
        assert inbox.deliveries == []


class TestRemoteSimulationClient:
    """:class:`RemoteMercadoPagoSimulation` on its own."""

    async def test_unreachable_fake_api_is_502(self) -> None:
        closed = f"http://127.0.0.1:{_free_port()}"
        app, _ = _service(_settings(closed), _Inbox(), simulation_url=closed)
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(
            transport=transport, base_url="http://service"
        ) as client:
            moved = await client.post("/dev/mercado-pago/orders/ORDX/pay")
            listed = await client.get("/dev/mercado-pago/orders")
        assert moved.status_code == 502
        assert listed.status_code == 502
        assert "simulator unavailable" in moved.json()["detail"]

    async def test_most_recent_order_wins_a_shared_reference(
        self, fake_api: str
    ) -> None:
        correlation_id = f"compra-{uuid.uuid4().hex}"
        provider = mercado_pago_provider_for(_settings(fake_api))
        first = await _pix(provider, correlation_id)
        second = await _pix(provider, correlation_id)
        remote = RemoteMercadoPagoSimulation(fake_api)
        assert (await remote.find_order(correlation_id))["id"] == second
        assert (await remote.find_order(first))["id"] == first
        order, action = await remote.apply(correlation_id, "cancel")
        assert (order["id"], action) == (second, "order.canceled")
        with pytest.raises(KeyError):
            await remote.find_order("nao-existe")


HTML_PAGE: str = "<html><body>502 Bad Gateway</body></html>"
"""What a proxy or an unrelated service answers in place of the fake-api."""

WRONG_URL: str = "http://not-the-fake-api:8090"
"""A ``simulation_url`` that reaches something other than the fake-api."""


def _answering(status_code: int, content_type: str, body: str) -> httpx.AsyncClient:
    """Build a client whose every request gets the same canned answer.

    Args:
        status_code (int): The status of the answer.
        content_type (str): Its ``content-type``.
        body (str): Its body.

    Returns:
        httpx.AsyncClient: The client, over an :class:`httpx.MockTransport`.
    """

    def handler(request: httpx.Request) -> httpx.Response:
        """Answer any request with the canned response.

        Args:
            request (httpx.Request): The request, ignored.

        Returns:
            httpx.Response: The canned answer.
        """
        return httpx.Response(
            status_code, headers={"content-type": content_type}, text=body
        )

    return httpx.AsyncClient(transport=httpx.MockTransport(handler))


class TestNonJsonAnswer:
    """A ``simulation_url`` that answers ``200`` with HTML is a bad gateway."""

    async def _call(
        self, remote: httpx.AsyncClient
    ) -> tuple[httpx.Response, httpx.Response, httpx.Response]:
        """Hit the list, lookup and transition routes through ``remote``.

        Args:
            remote (httpx.AsyncClient): The client the router sends with.

        Returns:
            tuple[httpx.Response, httpx.Response, httpx.Response]: The
            listing, the lookup and the transition answers.
        """
        app, _ = _service(
            _settings(WRONG_URL), _Inbox(), simulation_url=WRONG_URL, client=remote
        )
        transport = httpx.ASGITransport(app=app, raise_app_exceptions=False)
        async with httpx.AsyncClient(
            transport=transport, base_url="http://service"
        ) as client:
            listed = await client.get("/dev/mercado-pago/orders")
            found = await client.get("/dev/mercado-pago/orders/ORDX")
            moved = await client.post("/dev/mercado-pago/orders/ORDX/pay")
        return listed, found, moved

    async def test_html_body_is_502_naming_the_simulation_url(self) -> None:
        async with _answering(200, "text/html", HTML_PAGE) as remote:
            answers = await self._call(remote)
        assert [answer.status_code for answer in answers] == [502, 502, 502]
        for answer in answers:
            assert WRONG_URL in answer.json()["detail"]
            assert "not JSON" in answer.json()["detail"]

    async def test_html_conflict_is_still_409(self) -> None:
        async with _answering(409, "text/html", HTML_PAGE) as remote:
            _, _, moved = await self._call(remote)
        assert moved.status_code == 409
        assert moved.json()["detail"] == HTML_PAGE

    async def test_json_without_the_fake_api_keys_is_502(self) -> None:
        async with _answering(200, "application/json", "{}") as remote:
            listed, _, moved = await self._call(remote)
        assert [listed.status_code, moved.status_code] == [502, 502]
        assert "without 'orders'" in listed.json()["detail"]
        assert "without 'order'" in moved.json()["detail"]
        assert WRONG_URL in moved.json()["detail"]

    async def test_client_raises_decoding_error(self) -> None:
        async with _answering(200, "text/html", HTML_PAGE) as remote:
            simulation = RemoteMercadoPagoSimulation(WRONG_URL, client=remote)
            with pytest.raises(httpx.DecodingError, match="not JSON"):
                await simulation.find_order("ORDX")
            with pytest.raises(httpx.DecodingError, match="not JSON"):
                await simulation.apply("ORDX", "pay")
            with pytest.raises(httpx.DecodingError, match="not JSON"):
                await simulation.list_orders()


class TestRouterArguments:
    """``simulation_url`` and ``simulation_client`` combinations."""

    def test_simulation_url_needs_simulate(self) -> None:
        settings = _settings("http://127.0.0.1:1")
        with pytest.raises(ValueError, match="needs simulate=True"):
            make_mercado_pago_router(
                mercado_pago_provider_for(settings),
                on_delivery=_Inbox(),
                settings=settings,
                simulation_url="http://127.0.0.1:1",
            )

    def test_simulation_url_excludes_simulation_api(self) -> None:
        settings = _settings("http://127.0.0.1:1")
        with pytest.raises(ValueError, match="not both"):
            make_mercado_pago_router(
                mercado_pago_provider_for(settings),
                on_delivery=_Inbox(),
                settings=settings,
                simulate=True,
                simulation_api=FakeMercadoPagoOrdersAPI(),
                simulation_url="http://127.0.0.1:1",
            )

    async def test_simulation_client_needs_simulation_url(self) -> None:
        settings = _settings("http://127.0.0.1:1")
        async with httpx.AsyncClient() as client:
            with pytest.raises(ValueError, match="needs `simulation_url`"):
                make_mercado_pago_router(
                    mercado_pago_provider_for(settings),
                    on_delivery=_Inbox(),
                    settings=settings,
                    simulate=True,
                    simulation_client=client,
                )

    def test_remote_simulation_is_refused_in_production(self) -> None:
        settings = _settings("https://api.mercadopago.com", env="production")
        with pytest.raises(RuntimeError, match="ENV=development or ENV=test"):
            make_mercado_pago_router(
                mercado_pago_provider_for(settings),
                on_delivery=_Inbox(),
                settings=settings,
                simulate=True,
                simulation_url="http://127.0.0.1:1",
            )

    def test_remote_routes_are_mounted(self) -> None:
        settings = _settings("http://127.0.0.1:1")
        router = make_mercado_pago_router(
            mercado_pago_provider_for(settings),
            on_delivery=_Inbox(),
            settings=settings,
            simulate=True,
            simulation_url="http://127.0.0.1:1",
        )
        prefix = "/dev/mercado-pago/orders"
        assert {getattr(route, "path", "") for route in router.routes} == {
            "/webhooks/mercado-pago",
            prefix,
            f"{prefix}/{{order_id}}",
            f"{prefix}/{{order_id}}/{{transition}}",
        }
