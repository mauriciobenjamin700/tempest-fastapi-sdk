"""``tempest integrations`` — what the SDK bundles, and whether it answers.

Two questions, one command group. ``list`` answers "which third-party
clients ship here and does this project have the credentials", which is
otherwise a trip through the docs. ``verify`` answers "is this key
still good", which nothing but a real authenticated call can.

The verification call per provider is a table on purpose, kept to the
cheapest authenticated read each API offers — and a guard asserts the
named method still exists on the generated client, so a provider
renaming it fails a test instead of the command.
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass
from typing import Any

import typer

from tempest_fastapi_sdk.cli.project import load_project_settings

integrations_app: typer.Typer = typer.Typer(
    name="integrations",
    help="List the bundled third-party clients and verify their credentials.",
    no_args_is_help=True,
)


@dataclass(frozen=True)
class Integration:
    """One bundled third-party client.

    Attributes:
        name (str): The name this CLI addresses it by.
        import_path (str): Where the client class lives.
        settings_mixin (str): The settings mixin carrying its
            credentials, or an empty string when the SDK ships none.
        kwargs_mapper (str): Name of the settings method producing the
            ``HTTPClient`` kwargs, empty when there is none.
        credential_field (str): The settings field that has to be set
            for the integration to be usable.
        verify_method (str): The cheapest authenticated read on the
            client, empty when the integration cannot be verified this
            way.
    """

    name: str
    import_path: str
    settings_mixin: str
    kwargs_mapper: str
    credential_field: str
    verify_method: str


INTEGRATIONS: tuple[Integration, ...] = (
    Integration(
        "openpix",
        "tempest_fastapi_sdk.integrations.payment.openpix:OpenPixClient",
        "OpenPixSettings",
        "openpix_kwargs",
        "OPENPIX_APP_ID",
        "get_company",
    ),
    Integration(
        "mercado-pago",
        "tempest_fastapi_sdk.integrations.payment.mercado_pago:MercadoPagoClient",
        "MercadoPagoSettings",
        "mercado_pago_kwargs",
        "MERCADOPAGO_ACCESS_TOKEN",
        "get_authenticated_user",
    ),
    Integration(
        "stripe",
        "tempest_fastapi_sdk.integrations.payment.stripe:StripeClient",
        "",
        "",
        "",
        "",
    ),
    Integration(
        "zap",
        "tempest_fastapi_sdk.integrations.messaging.zap:ZapClient",
        "",
        "",
        "",
        "health",
    ),
)
"""The clients the SDK ships, with how each one is configured.

``verify_method`` is the cheapest authenticated read the provider
offers: OpenPix's ``GET /company`` and Mercado Pago's
``GET /users/me`` both take no argument and touch no money. Zap's
``health`` needs no credential at all, so verifying it measures
reachability, not authorization.
"""


def _find(name: str) -> Integration:
    """Look an integration up by the name the CLI addresses it by.

    Args:
        name (str): The value the operator typed.

    Returns:
        Integration: The matching entry.

    Raises:
        typer.Exit: Exit code 2 when nothing matches, listing what does.
    """
    for integration in INTEGRATIONS:
        if integration.name == name:
            return integration
    known = ", ".join(item.name for item in INTEGRATIONS)
    typer.echo(f"error: unknown integration {name!r}. Known: {known}.", err=True)
    raise typer.Exit(2)


def _credential_state(integration: Integration, settings: Any) -> str:
    """Describe whether this project carries the integration's credential.

    Args:
        integration (Integration): The entry to describe.
        settings (Any): The project's settings, or ``None``.

    Returns:
        str: A short phrase for the listing's last column.
    """
    if not integration.settings_mixin:
        return "no settings mixin (build the client yourself)"
    if settings is None:
        return f"needs {integration.settings_mixin}"
    value = getattr(settings, integration.credential_field, None)
    if not value:
        return f"{integration.credential_field} not set"
    return f"{integration.credential_field} set"


@integrations_app.command("list")
def integrations_list() -> None:
    """List the bundled clients and this project's credential state."""
    settings = load_project_settings()
    width = max(len(item.name) for item in INTEGRATIONS)
    for integration in INTEGRATIONS:
        typer.echo(
            f"{integration.name.ljust(width)}  {integration.import_path}\n"
            f"{' ' * width}  {_credential_state(integration, settings)}"
        )


@integrations_app.command("verify")
def integrations_verify(
    name: str = typer.Argument(..., help="Integration name, as 'list' prints it."),
    base_url: str = typer.Option(
        "",
        "--base-url",
        help=(
            "Base URL for an integration the SDK ships no settings mixin for "
            "(zap). Ignored when the credentials come from settings."
        ),
    ),
    timeout: float = typer.Option(
        15.0,
        "--timeout",
        min=0.1,
        help="Seconds to wait for the provider's reply.",
    ),
) -> None:
    """Make one authenticated call and report what the provider answered.

    This spends a request against the provider, so it is a check to run
    when a key changed or a deploy fails — not on every boot.

    Raises:
        typer.Exit: Exit code 2 when the integration cannot be verified
            from settings (no mixin and no ``--base-url``, or the
            credential is unset); code 1 when the provider refuses,
            carrying its own message.
    """
    integration = _find(name)
    if not integration.verify_method:
        typer.echo(
            f"error: {integration.name} exposes no cheap authenticated read, "
            "so there is nothing the CLI can call without spending money or "
            "creating a record. Construct the client in your service.",
            err=True,
        )
        raise typer.Exit(2)

    settings = load_project_settings()
    if integration.kwargs_mapper:
        if settings is None or not hasattr(settings, integration.kwargs_mapper):
            typer.echo(
                f"error: this project composes no {integration.settings_mixin}, "
                f"so there is no {integration.credential_field} to verify.",
                err=True,
            )
            raise typer.Exit(2)
        if not getattr(settings, integration.credential_field, ""):
            typer.echo(
                f"error: {integration.credential_field} is empty.",
                err=True,
            )
            raise typer.Exit(2)
        client_kwargs: dict[str, Any] = getattr(settings, integration.kwargs_mapper)()
    elif base_url:
        client_kwargs = {"base_url": base_url}
    else:
        typer.echo(
            f"error: {integration.name} has no settings mixin, so the CLI does "
            "not know where it lives. Pass --base-url.",
            err=True,
        )
        raise typer.Exit(2)

    module_name, _, class_name = integration.import_path.partition(":")

    async def _call() -> Any:
        import importlib

        from tempest_fastapi_sdk import HTTPClient

        module = importlib.import_module(module_name)
        client_class = getattr(module, class_name)
        async with HTTPClient(**client_kwargs, timeout=timeout) as http:
            client = client_class(http)
            return await getattr(client, integration.verify_method)()

    try:
        answer = asyncio.run(_call())
    except Exception as exc:
        typer.echo(
            f"error: {integration.name} refused the call — {type(exc).__name__}: {exc}",
            err=True,
        )
        raise typer.Exit(1) from exc

    typer.echo(f"{integration.name}: {integration.verify_method} answered")
    typer.echo(f"  {_summarize(answer)}")


def _summarize(answer: Any) -> str:
    """Render a provider's reply as one short line.

    Args:
        answer (Any): Whatever the verification call returned.

    Returns:
        str: A one-line rendering, truncated so a large payload does not
        take over the terminal.
    """
    if hasattr(answer, "model_dump"):
        answer = answer.model_dump()
    text = str(answer)
    return text if len(text) <= 200 else f"{text[:197]}..."


__all__: list[str] = [
    "INTEGRATIONS",
    "Integration",
    "integrations_app",
]
