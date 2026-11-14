"""Tests for ``tempest mercado-pago``.

The flow test runs what the manual test runs: the simulated Orders API and a
service, each a real uvicorn server on ``127.0.0.1``, and the command posting
a signed delivery over the network to the service, which re-reads the order
from the simulator.
"""

from __future__ import annotations

import socket
import threading
import time
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import httpx
import pytest
import uvicorn
from fastapi import Depends, FastAPI
from typer.testing import CliRunner

from tempest_fastapi_sdk import HTTPClient
from tempest_fastapi_sdk.cli.main import app
from tempest_fastapi_sdk.integrations.payment.adapters import (
    MercadoPagoOrderDelivery,
    MercadoPagoPixProvider,
    make_mercado_pago_webhook_delivery_dependency,
)
from tempest_fastapi_sdk.testing.fakes.mercado_pago import (
    FakeMercadoPagoOrdersAPI,
    make_fake_orders_app,
)

runner = CliRunner()

SECRET: str = "segredo-cli-teste"
"""The secret the service verifies with."""

SECRET_ENV: str = "TEMPEST_TEST_MP_WEBHOOK_SECRET"
"""Variable the command reads the secret from in these tests."""


def _free_port() -> int:
    """Pick a free TCP port on the loopback.

    Returns:
        int: The port.
    """
    with socket.socket() as probe:
        probe.bind(("127.0.0.1", 0))
        port: int = probe.getsockname()[1]
        return port


class _Server:
    """A uvicorn server in a background thread.

    Attributes:
        url (str): Its base URL.
    """

    def __init__(self, application: FastAPI) -> None:
        """Start serving and wait until it accepts connections.

        Args:
            application (FastAPI): The app.
        """
        port = _free_port()
        self.url: str = f"http://127.0.0.1:{port}"
        self._server = uvicorn.Server(
            uvicorn.Config(application, host="127.0.0.1", port=port, log_level="error")
        )
        self._thread = threading.Thread(target=self._server.run, daemon=True)
        self._thread.start()
        deadline = time.monotonic() + 10
        while not self._server.started:
            if time.monotonic() > deadline:
                raise RuntimeError("server did not start")
            time.sleep(0.02)

    def stop(self) -> None:
        """Stop the server and join its thread."""
        self._server.should_exit = True
        self._thread.join(timeout=10)


def _service(fake_url: str) -> FastAPI:
    """Build a service whose adapter talks to the fake API over HTTP.

    Args:
        fake_url (str): Base URL of the running simulator.

    Returns:
        FastAPI: The service.
    """
    provider = MercadoPagoPixProvider(HTTPClient(base_url=fake_url))
    service = FastAPI()
    dependency = make_mercado_pago_webhook_delivery_dependency(SECRET, provider)

    @service.post("/webhooks/mercado-pago")
    async def webhook(
        delivery: MercadoPagoOrderDelivery = Depends(dependency),
    ) -> dict[str, Any]:
        """Report what the re-read decided.

        Args:
            delivery (MercadoPagoOrderDelivery): The re-read delivery.

        Returns:
            dict[str, Any]: The event type and the re-read status.
        """
        event = provider.parse_webhook(delivery)
        return {
            "type": event.type.value,
            "order_status": delivery.order["status"] if delivery.order else None,
        }

    return service


@pytest.fixture
def running() -> Iterator[tuple[str, str]]:
    """Run the simulator and a service, both on the loopback.

    Yields:
        tuple[str, str]: The simulator's URL and the service's webhook URL.
    """
    fake = _Server(make_fake_orders_app(FakeMercadoPagoOrdersAPI()))
    service = _Server(_service(fake.url))
    try:
        yield fake.url, f"{service.url}/webhooks/mercado-pago"
    finally:
        service.stop()
        fake.stop()


def _create_pix(fake_url: str) -> str:
    """Create a Pix order on the running simulator.

    Args:
        fake_url (str): The simulator's URL.

    Returns:
        str: The order id.
    """
    answer = httpx.post(
        f"{fake_url}/v1/orders",
        json={
            "total_amount": "19.90",
            "external_reference": "pedido-cli",
            "payer": {"email": "comprador@example.com"},
            "transactions": {
                "payments": [
                    {
                        "amount": "19.90",
                        "payment_method": {"id": "pix", "type": "bank_transfer"},
                    }
                ]
            },
        },
    )
    order_id: str = answer.json()["id"]
    return order_id


class TestSimulateWebhook:
    """``tempest mercado-pago simulate-webhook``."""

    def test_pay_reaches_the_service_through_the_network(
        self, running: tuple[str, str], monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Transition on the simulator, signed post, re-read: ``charge_paid``.

        Args:
            running (tuple[str, str]): The two servers.
            monkeypatch (pytest.MonkeyPatch): Sets the secret variable.
        """
        fake_url, webhook_url = running
        order_id = _create_pix(fake_url)
        monkeypatch.setenv(SECRET_ENV, SECRET)

        result = runner.invoke(
            app,
            [
                "mercado-pago",
                "simulate-webhook",
                "--url",
                webhook_url,
                "--secret-env",
                SECRET_ENV,
                "--fake-api",
                fake_url,
                "--order-id",
                order_id,
                "--transition",
                "pay",
            ],
        )

        assert result.exit_code == 0, result.output
        assert "action: order.processed" in result.output
        assert "HTTP 200" in result.output
        assert '"type":"charge_paid"' in result.output
        assert '"order_status":"processed"' in result.output
        assert SECRET not in result.output

    def test_order_id_accepts_the_external_reference(
        self, running: tuple[str, str], monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """``--order-id pedido-cli`` resolves on the fake-api, with and without a move.

        Args:
            running (tuple[str, str]): The two servers.
            monkeypatch (pytest.MonkeyPatch): Sets the secret variable.
        """
        fake_url, webhook_url = running
        order_id = _create_pix(fake_url)
        monkeypatch.setenv(SECRET_ENV, SECRET)
        base = [
            "mercado-pago",
            "simulate-webhook",
            "--url",
            webhook_url,
            "--secret-env",
            SECRET_ENV,
            "--fake-api",
            fake_url,
            "--order-id",
            "pedido-cli",
        ]

        read = runner.invoke(app, base)
        moved = runner.invoke(app, [*base, "--transition", "expire"])

        assert read.exit_code == 0, read.output
        assert order_id in read.output
        assert "action: order.action_required" in read.output
        assert moved.exit_code == 0, moved.output
        assert order_id in moved.output
        assert "action: order.canceled" in moved.output
        assert '"order_status":"canceled"' in moved.output

    def test_another_secret_is_401_and_exit_1(
        self, running: tuple[str, str], tmp_path: Path
    ) -> None:
        """A secret the service does not hold: ``401``, exit code ``1``.

        Args:
            running (tuple[str, str]): The two servers.
            tmp_path (Path): Holds the secret file.
        """
        fake_url, webhook_url = running
        order_id = _create_pix(fake_url)
        secret_file = tmp_path / "secret"
        secret_file.write_text("outro-segredo\n", encoding="utf-8")

        result = runner.invoke(
            app,
            [
                "mercado-pago",
                "simulate-webhook",
                "--url",
                webhook_url,
                "--secret-file",
                str(secret_file),
                "--fake-api",
                fake_url,
                "--order-id",
                order_id,
            ],
        )

        assert result.exit_code == 1
        assert "HTTP 401" in result.output
        assert "outro-segredo" not in result.output

    def test_order_json_without_fake_api(
        self, running: tuple[str, str], tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """An order from a file, for an id the simulator does not know: no order.

        Args:
            running (tuple[str, str]): The two servers.
            tmp_path (Path): Holds the order file.
            monkeypatch (pytest.MonkeyPatch): Sets the secret variable.
        """
        _, webhook_url = running
        order_file = tmp_path / "order.json"
        order_file.write_text(
            '{"id": "ORD00000000000000000000000000", "status": "processed"}',
            encoding="utf-8",
        )
        monkeypatch.setenv(SECRET_ENV, SECRET)

        result = runner.invoke(
            app,
            [
                "mercado-pago",
                "simulate-webhook",
                "--url",
                webhook_url,
                "--secret-env",
                SECRET_ENV,
                "--order-json",
                str(order_file),
            ],
        )

        assert result.exit_code == 0, result.output
        assert "action: order.processed" in result.output
        assert '"order_status":null' in result.output

    def test_a_missing_secret_names_the_variable(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Unset variable: exit ``2`` before any request.

        Args:
            monkeypatch (pytest.MonkeyPatch): Clears the variable.
        """
        monkeypatch.delenv(SECRET_ENV, raising=False)

        result = runner.invoke(
            app,
            [
                "mercado-pago",
                "simulate-webhook",
                "--secret-env",
                SECRET_ENV,
                "--order-id",
                "ORD1",
            ],
        )

        assert result.exit_code == 2
        assert SECRET_ENV in result.output

    def test_there_is_no_secret_option(self) -> None:
        """The secret cannot be typed on the command line."""
        result = runner.invoke(
            app, ["mercado-pago", "simulate-webhook", "--secret", "x"]
        )

        assert result.exit_code == 2
        assert "No such option" in result.output

    def test_a_transition_needs_the_fake_api(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """``--transition`` alone has nothing to move: exit ``2``.

        Args:
            monkeypatch (pytest.MonkeyPatch): Sets the secret variable.
        """
        monkeypatch.setenv(SECRET_ENV, SECRET)

        result = runner.invoke(
            app,
            [
                "mercado-pago",
                "simulate-webhook",
                "--secret-env",
                SECRET_ENV,
                "--order-id",
                "ORD1",
                "--transition",
                "pay",
            ],
        )

        assert result.exit_code == 2
        assert "--fake-api" in result.output

    def test_the_default_url_is_the_loopback(self) -> None:
        """The route defaults to ``127.0.0.1``."""
        from tempest_fastapi_sdk.cli.mercado_pago import DEFAULT_WEBHOOK_URL

        assert DEFAULT_WEBHOOK_URL.startswith("http://127.0.0.1:")


def test_fake_api_binds_the_loopback(monkeypatch: pytest.MonkeyPatch) -> None:
    """``fake-api`` hands uvicorn the simulator app on ``127.0.0.1:8090``.

    Args:
        monkeypatch (pytest.MonkeyPatch): Replaces ``uvicorn.run``.
    """
    seen: dict[str, Any] = {}

    def fake_run(application: Any, **kwargs: Any) -> None:
        """Record the call instead of serving.

        Args:
            application (Any): The app.
            **kwargs (Any): uvicorn's options.
        """
        seen["app"] = application
        seen.update(kwargs)

    monkeypatch.setattr(uvicorn, "run", fake_run)

    result = runner.invoke(app, ["mercado-pago", "fake-api"])

    assert result.exit_code == 0, result.output
    assert (seen["host"], seen["port"]) == ("127.0.0.1", 8090)
    assert isinstance(seen["app"], FastAPI)
