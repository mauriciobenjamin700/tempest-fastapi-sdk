"""``tempest mercado-pago`` — exercise a Mercado Pago integration by hand, offline.

Two commands for a service running on its own machine:

- ``fake-api`` serves the simulated Orders API
  (:class:`~tempest_fastapi_sdk.testing.fakes.FakeMercadoPagoOrdersAPI`) over
  HTTP. Point the service at it with ``MERCADOPAGO_BASE_URL`` and its
  creates, reads and refunds never reach Mercado Pago.
- ``simulate-webhook`` signs a delivery the way Mercado Pago does and posts
  it to the service's webhook route, then prints what the route answered.

The secret is never accepted as an argument — a value typed on the command
line lands in the shell history and in ``ps`` — and never printed: it is
read from an environment variable or a file.

Everything heavy is imported inside the commands, so ``tempest --help`` does
not import FastAPI.
"""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any

import typer

DEFAULT_FAKE_API_PORT: int = 8090
"""Port ``fake-api`` binds by default."""

DEFAULT_WEBHOOK_URL: str = "http://127.0.0.1:8000/webhooks/mercado-pago"
"""Route ``simulate-webhook`` posts to by default."""

DEFAULT_SECRET_ENV: str = "MERCADOPAGO_WEBHOOK_SECRET"
"""Variable the secret is read from by default — the ``MercadoPagoSettings`` field."""

mercado_pago_app: typer.Typer = typer.Typer(
    name="mercado-pago",
    help="Simulate Mercado Pago (Orders API and webhook) for a local service.",
    no_args_is_help=True,
)


def _fail(message: str) -> typer.Exit:
    """Print an error and build the exit to raise.

    Args:
        message (str): What went wrong. Never carries the secret.

    Returns:
        typer.Exit: Exit code 2.
    """
    typer.echo(f"error: {message}", err=True)
    return typer.Exit(2)


def _read_secret(secret_env: str, secret_file: Path | None) -> str:
    """Read the webhook secret from a file or an environment variable.

    Args:
        secret_env (str): Variable name, used when no file is given.
        secret_file (Path | None): File holding the secret; a trailing
            newline is dropped.

    Returns:
        str: The secret.

    Raises:
        typer.Exit: When the source is unreadable or empty. The message names
            the source, never the value.
    """
    if secret_file is not None:
        try:
            secret = secret_file.read_text(encoding="utf-8").strip()
        except OSError as error:
            raise _fail(f"cannot read --secret-file {secret_file}: {error}") from error
        if not secret:
            raise _fail(f"--secret-file {secret_file} is empty")
        return secret
    secret = os.environ.get(secret_env, "")
    if not secret:
        raise _fail(
            f"environment variable {secret_env} is not set or empty; export the "
            "secret the service verifies with, or pass --secret-file"
        )
    return secret


def _load_order(
    order_id: str,
    order_json: Path | None,
    fake_api: str,
    transition: str,
    amount_cents: int | None,
) -> tuple[dict[str, Any], str]:
    """Find the order the delivery is about.

    Args:
        order_id (str): The order id, when not taken from ``order_json``;
            with ``fake_api``, also its ``external_reference``.
        order_json (Path | None): A file holding the order as JSON.
        fake_api (str): Base URL of a running ``fake-api``.
        transition (str): A transition to apply on ``fake_api`` first.
        amount_cents (int | None): For a partial ``refund`` transition.

    Returns:
        tuple[dict[str, Any], str]: The order and the action the transition
        reported (``""`` without a transition).

    Raises:
        typer.Exit: When the order cannot be found or the transition fails.
    """
    import httpx

    if order_json is not None:
        try:
            loaded = json.loads(order_json.read_text(encoding="utf-8"))
        except (OSError, ValueError) as error:
            raise _fail(f"cannot read --order-json {order_json}: {error}") from error
        if not isinstance(loaded, dict):
            raise _fail("--order-json must hold a JSON object")
        if order_id:
            loaded["id"] = order_id
        return loaded, ""
    if not order_id:
        raise _fail("pass --order-id (or --order-json)")
    if not fake_api:
        if transition:
            raise _fail("--transition needs --fake-api")
        return {"id": order_id}, ""
    from tempest_fastapi_sdk.testing.fakes.mercado_pago import FAKE_CONTROL_PREFIX

    base = fake_api.rstrip("/")
    with httpx.Client(timeout=10.0) as client:
        if transition:
            params = {} if amount_cents is None else {"amount_cents": amount_cents}
            answer = client.post(
                f"{base}{FAKE_CONTROL_PREFIX}/orders/{order_id}/{transition}",
                params=params,
            )
            if answer.status_code != 200:
                raise _fail(f"fake-api refused {transition}: {answer.text}")
            moved: dict[str, Any] = answer.json()
            return moved["order"], str(moved["action"])
        answer = client.get(f"{base}{FAKE_CONTROL_PREFIX}/orders/{order_id}")
        if answer.status_code != 200:
            raise _fail(f"fake-api has no order {order_id}: {answer.status_code}")
        found: dict[str, Any] = answer.json()
        return found, ""


@mercado_pago_app.command("simulate-webhook")
def simulate_webhook_command(
    url: str = typer.Option(
        DEFAULT_WEBHOOK_URL, "--url", help="The service's webhook route."
    ),
    secret_env: str = typer.Option(
        DEFAULT_SECRET_ENV,
        "--secret-env",
        help="Environment variable holding the webhook secret.",
    ),
    secret_file: Path | None = typer.Option(
        None,
        "--secret-file",
        help="File holding the webhook secret; wins over --secret-env.",
    ),
    order_id: str = typer.Option(
        "",
        "--order-id",
        help=(
            "The order id (ORD…); with --fake-api, also the external_reference "
            "the service sent on create."
        ),
    ),
    action: str = typer.Option(
        "",
        "--action",
        help=(
            "The body's action. Omitted: order.<status> of the order, or "
            "order.updated when only the id is known."
        ),
    ),
    order_json: Path | None = typer.Option(
        None, "--order-json", help="File holding the order as JSON."
    ),
    fake_api: str = typer.Option(
        "",
        "--fake-api",
        help="Base URL of a running 'tempest mercado-pago fake-api'.",
    ),
    transition: str = typer.Option(
        "",
        "--transition",
        help="Apply pay/expire/fail/cancel/capture/refund on --fake-api first.",
    ),
    amount_cents: int | None = typer.Option(
        None, "--amount-cents", min=1, help="Amount of a partial refund transition."
    ),
    live_mode: bool = typer.Option(
        False, "--live-mode/--no-live-mode", help="The body's live_mode."
    ),
) -> None:
    """Sign a Mercado Pago delivery and post it to a webhook route.

    Raises:
        typer.Exit: Exit code 2 for a usage error, 1 when the route answers
            anything but 2xx or cannot be reached.
    """
    import httpx

    from tempest_fastapi_sdk.testing.fakes.mercado_pago import (
        build_mercado_pago_delivery,
    )

    secret = _read_secret(secret_env, secret_file)
    order, reported = _load_order(
        order_id, order_json, fake_api, transition, amount_cents
    )
    try:
        delivery = build_mercado_pago_delivery(
            order,
            secret=secret,
            action=action or reported or None,
            live_mode=live_mode,
        )
    except ValueError as error:
        raise _fail(str(error)) from error
    typer.echo(f"POST {delivery.url(url)}")
    typer.echo(f"action: {delivery.payload['action']}")
    try:
        with httpx.Client(timeout=10.0) as client:
            response = delivery.send_sync(client, url)
    except httpx.HTTPError as error:
        typer.echo(f"error: could not reach {url}: {error}", err=True)
        raise typer.Exit(1) from error
    typer.echo(f"HTTP {response.status_code}")
    typer.echo(response.text)
    if not response.is_success:
        raise typer.Exit(1)


@mercado_pago_app.command("fake-api")
def fake_api_command(
    host: str = typer.Option("127.0.0.1", "--host", help="Bind interface."),
    port: int = typer.Option(
        DEFAULT_FAKE_API_PORT, "--port", min=1, max=65535, help="TCP port."
    ),
) -> None:
    """Serve the simulated Mercado Pago Orders API over HTTP.

    State lives in memory and is lost when the process stops. Point the
    service at it with ``MERCADOPAGO_BASE_URL=http://127.0.0.1:8090``.

    Raises:
        typer.Exit: Exit code 2 when ``uvicorn`` is not installed — it is
            the service's server, not a dependency of this package.
    """
    try:
        import uvicorn
    except ImportError as error:
        raise _fail("fake-api needs uvicorn: pip install uvicorn") from error

    from tempest_fastapi_sdk.testing.fakes.mercado_pago import (
        FakeMercadoPagoOrdersAPI,
        make_fake_orders_app,
    )

    typer.echo(f"Fake Mercado Pago Orders API on http://{host}:{port}")
    uvicorn.run(make_fake_orders_app(FakeMercadoPagoOrdersAPI()), host=host, port=port)


__all__: list[str] = [
    "DEFAULT_FAKE_API_PORT",
    "DEFAULT_SECRET_ENV",
    "DEFAULT_WEBHOOK_URL",
    "fake_api_command",
    "mercado_pago_app",
    "simulate_webhook_command",
]
