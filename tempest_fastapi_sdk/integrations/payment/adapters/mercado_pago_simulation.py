"""Run a service's Mercado Pago flow against the in-memory simulator.

Two pieces that :func:`make_mercado_pago_router` puts together for a front
end testing in development:

- :func:`mercado_pago_provider_for` builds the adapter the service charges
  with. With ``simulate=False`` it is the real adapter over
  ``MercadoPagoSettings.mercado_pago_kwargs()``; with ``simulate=True`` it is
  the **same** adapter over a
  :class:`~tempest_fastapi_sdk.testing.fakes.FakeMercadoPagoOrdersAPI` in
  memory, and the simulator is remembered next to the provider so the router
  finds it with :func:`simulation_api_for`.
- :func:`add_simulation_routes` mounts the routes that move an order the way
  only the provider can (pay, expire, fail, cancel, capture, refund) and
  deliver the signed notification to the service's own webhook route.

Simulation is refused outside ``ENV=development`` and ``ENV=test`` — see
:func:`ensure_simulation_allowed`. The simulator module is imported only when
a simulation is built, so a production process never loads it.
"""

from __future__ import annotations

import os
import weakref
from enum import Enum
from typing import TYPE_CHECKING, Any, Final, Literal, overload

import httpx
from fastapi import APIRouter, HTTPException, Query, Request
from starlette.routing import NoMatchFound

from tempest_fastapi_sdk.api.route_docs import RouteDocs
from tempest_fastapi_sdk.integrations.payment.adapters.mercado_pago import (
    MercadoPagoCardProvider,
    MercadoPagoPixProvider,
)
from tempest_fastapi_sdk.schemas import BaseSchema
from tempest_fastapi_sdk.settings.mixins import MercadoPagoSettings
from tempest_fastapi_sdk.utils.http_client import HTTPClient

if TYPE_CHECKING:
    from tempest_fastapi_sdk.testing.fakes.mercado_pago import (
        FakeMercadoPagoOrdersAPI,
    )

SIMULATION_ENVIRONMENTS: Final[frozenset[str]] = frozenset({"development", "test"})
"""Values of ``ENV`` under which a simulation may be built."""

DEFAULT_SIMULATION_PREFIX: Final[str] = "/dev/mercado-pago"
"""Path prefix of the simulation routes."""

SIMULATION_TAG: Final[str] = "mercado-pago (dev)"
"""OpenAPI tag of the simulation routes, so they stand apart in ``/docs``."""

MercadoPagoTransition = Literal["pay", "expire", "fail", "cancel", "capture", "refund"]
"""The moves a simulation route applies — the simulator's ``TRANSITIONS``."""

_SIMULATIONS: weakref.WeakKeyDictionary[
    MercadoPagoPixProvider | MercadoPagoCardProvider, FakeMercadoPagoOrdersAPI
] = weakref.WeakKeyDictionary()
"""Provider built by :func:`mercado_pago_provider_for` to its simulator."""


class _AmbientMercadoPagoSettings(MercadoPagoSettings):
    """``MercadoPagoSettings`` plus an optional ``ENV``, read from the environment.

    Built only when the caller passed no settings, so a service that keeps
    ``ENV`` and ``MERCADOPAGO_WEBHOOK_SECRET`` in ``.env`` is read the same
    way its own ``Settings`` reads them.

    Attributes:
        ENV (str | None): The deployment environment, when set.
    """

    ENV: str | None = None


def ambient_settings(settings: MercadoPagoSettings | None) -> MercadoPagoSettings:
    """Return the settings given, or read them from the environment.

    Args:
        settings (MercadoPagoSettings | None): What the caller passed.

    Returns:
        MercadoPagoSettings: ``settings``, or a settings object read from the
        process environment and ``.env``, with ``ENV`` when set.
    """
    return settings if settings is not None else _AmbientMercadoPagoSettings()


def ensure_simulation_allowed(settings: MercadoPagoSettings | None, what: str) -> None:
    """Refuse a simulation outside development and test.

    The environment is ``settings.ENV`` when the settings carry one (a
    ``Settings`` composing ``EnvironmentSettings``, or the ambient read of
    :func:`ambient_settings`), else the ``ENV`` environment variable. Unset
    counts as production: a host that forgot ``ENV`` must not expose routes
    that mark orders paid.

    Args:
        settings (MercadoPagoSettings | None): The service's settings.
        what (str): What is being built, for the message.

    Raises:
        RuntimeError: When the environment is not ``development`` or
            ``test``.
    """
    environment = getattr(ambient_settings(settings), "ENV", None) or os.environ.get(
        "ENV"
    )
    if environment not in SIMULATION_ENVIRONMENTS:
        raise RuntimeError(
            f"{what} with simulate=True needs ENV=development or ENV=test "
            f"(got {environment!r}); the simulation routes mark orders paid "
            "and must never reach production."
        )


@overload
def mercado_pago_provider_for(
    settings: MercadoPagoSettings | None = None,
    *,
    simulate: bool = False,
    provider_class: type[MercadoPagoPixProvider] = MercadoPagoPixProvider,
    simulation_api: FakeMercadoPagoOrdersAPI | None = None,
) -> MercadoPagoPixProvider: ...


@overload
def mercado_pago_provider_for(
    settings: MercadoPagoSettings | None = None,
    *,
    simulate: bool = False,
    provider_class: type[MercadoPagoCardProvider],
    simulation_api: FakeMercadoPagoOrdersAPI | None = None,
) -> MercadoPagoCardProvider: ...


def mercado_pago_provider_for(
    settings: MercadoPagoSettings | None = None,
    *,
    simulate: bool = False,
    provider_class: type[MercadoPagoPixProvider]
    | type[MercadoPagoCardProvider] = MercadoPagoPixProvider,
    simulation_api: FakeMercadoPagoOrdersAPI | None = None,
) -> MercadoPagoPixProvider | MercadoPagoCardProvider:
    """Build the Mercado Pago adapter a service charges with.

    Args:
        settings (MercadoPagoSettings | None): The service's settings.
            ``None`` reads ``MERCADOPAGO_*`` and ``ENV`` from the environment
            and ``.env``.
        simulate (bool): ``True`` runs the adapter over a simulator in
            memory instead of the API host. Refused outside development and
            test.
        provider_class (type[MercadoPagoPixProvider] | type[MercadoPagoCardProvider]):
            :class:`MercadoPagoPixProvider` (default) or
            :class:`MercadoPagoCardProvider`.
        simulation_api (FakeMercadoPagoOrdersAPI | None): The simulator to
            run over, to share one between a Pix and a card provider. ``None``
            builds one signing with ``MERCADOPAGO_WEBHOOK_SECRET`` (or the
            simulator's default secret when that is empty). Ignored unless
            ``simulate`` is ``True``.

    Returns:
        MercadoPagoPixProvider | MercadoPagoCardProvider: The adapter, of
        ``provider_class``. In simulation,
        :func:`simulation_api_for` returns its simulator.

    Raises:
        RuntimeError: If ``simulate`` is ``True`` outside development and
            test.
    """
    resolved = ambient_settings(settings)
    if not simulate:
        return provider_class(HTTPClient(**resolved.mercado_pago_kwargs()))
    ensure_simulation_allowed(settings, "mercado_pago_provider_for")
    from tempest_fastapi_sdk.testing.fakes.mercado_pago import (
        FakeMercadoPagoOrdersAPI,
    )

    api = simulation_api
    if api is None:
        secret = resolved.MERCADOPAGO_WEBHOOK_SECRET
        api = (
            FakeMercadoPagoOrdersAPI(webhook_secret=secret)
            if secret
            else FakeMercadoPagoOrdersAPI()
        )
    provider = provider_class(api.http_client())
    _SIMULATIONS[provider] = api
    return provider


def simulation_api_for(
    provider: MercadoPagoPixProvider | MercadoPagoCardProvider,
) -> FakeMercadoPagoOrdersAPI | None:
    """Find the simulator a provider runs over.

    Args:
        provider (MercadoPagoPixProvider | MercadoPagoCardProvider): A
            provider.

    Returns:
        FakeMercadoPagoOrdersAPI | None: The simulator, when the provider came
        from :func:`mercado_pago_provider_for` with ``simulate=True``.
    """
    return _SIMULATIONS.get(provider)


class MercadoPagoSimulatedDeliverySchema(BaseSchema):
    """What the service's webhook route answered to a simulated delivery.

    Attributes:
        action (str): The delivery's ``action``.
        status_code (int): The webhook route's status — ``200`` when
            ``on_delivery`` returned, ``401`` for a signature mismatch,
            ``500`` when it raised.
        body (Any): The webhook route's body, decoded when it is JSON.
    """

    action: str
    status_code: int
    body: Any = None


class MercadoPagoSimulationResultSchema(BaseSchema):
    """The answer of a simulation route.

    Attributes:
        order (dict[str, Any]): The order after the transition, as
            ``GET /v1/orders/{id}`` answers it.
        delivery (MercadoPagoSimulatedDeliverySchema): How the webhook went.
    """

    order: dict[str, Any]
    delivery: MercadoPagoSimulatedDeliverySchema


async def _deliver(
    request: Request, webhook_route: str, delivery: Any
) -> httpx.Response:
    """Post a signed delivery to the service's webhook route, in process.

    Goes through the whole application — middleware, the signature check,
    the re-read and ``on_delivery`` — over ``httpx.ASGITransport``, with the
    host the caller used. An exception in the route becomes a ``500``
    answer rather than escaping into the simulation route.

    Args:
        request (Request): The simulation request.
        webhook_route (str): Name of the webhook route.
        delivery (Any): The ``MercadoPagoDelivery`` to send.

    Returns:
        httpx.Response: What the webhook route answered.
    """
    try:
        path = str(request.app.url_path_for(webhook_route))
    except NoMatchFound:
        path = str(request.url_for(webhook_route).path)
    transport = httpx.ASGITransport(app=request.app, raise_app_exceptions=False)
    base_url = str(request.base_url).rstrip("/")
    async with httpx.AsyncClient(transport=transport, base_url=base_url) as client:
        response: httpx.Response = await delivery.send(client, path)
        return response


def add_simulation_routes(
    router: APIRouter,
    *,
    api: FakeMercadoPagoOrdersAPI,
    secret: str,
    webhook_route: str,
    prefix: str,
    route_docs: RouteDocs,
    include_in_schema: bool = True,
) -> None:
    """Mount the simulation routes on a router.

    - ``GET {prefix}/orders`` — every order, oldest first.
    - ``GET {prefix}/orders/{order_id}`` — one order; ``404`` when unknown.
    - ``POST {prefix}/orders/{order_id}/{transition}`` — apply the move,
      sign the delivery with ``secret`` and post it to the webhook route;
      ``404`` for an unknown order, ``409`` for a move the order's state does
      not allow (a refund before payment), ``422`` for an unknown move.

    Args:
        router (APIRouter): Where to mount.
        api (FakeMercadoPagoOrdersAPI): The simulator the provider runs over.
        secret (str): The secret the webhook route verifies with.
        webhook_route (str): Name of the webhook route.
        prefix (str): Path prefix.
        route_docs (RouteDocs): The operation text.
        include_in_schema (bool): Whether the routes appear in OpenAPI.
    """
    from tempest_fastapi_sdk.testing.fakes.mercado_pago import (
        build_mercado_pago_delivery,
    )

    tags: list[str | Enum] = [SIMULATION_TAG]

    def _order(order_id: str) -> dict[str, Any]:
        """Read an order or answer 404.

        Args:
            order_id (str): The order id.

        Returns:
            dict[str, Any]: The order.

        Raises:
            HTTPException: ``404`` for an id the simulator never created.
        """
        try:
            return api.order(order_id)
        except KeyError:
            raise HTTPException(404, f"order {order_id} not found") from None

    @router.get(
        f"{prefix}/orders",
        tags=tags,
        include_in_schema=include_in_schema,
        summary=route_docs.summary("mercado_pago.list_simulated_orders"),
        description=route_docs.description("mercado_pago.list_simulated_orders"),
    )
    async def list_simulated_orders() -> list[dict[str, Any]]:
        """List the simulated orders.

        Returns:
            list[dict[str, Any]]: Every order, oldest first.
        """
        return [api.order(order_id) for order_id in api.order_ids]

    @router.get(
        f"{prefix}/orders/{{order_id}}",
        tags=tags,
        include_in_schema=include_in_schema,
        summary=route_docs.summary("mercado_pago.get_simulated_order"),
        description=route_docs.description("mercado_pago.get_simulated_order"),
    )
    async def get_simulated_order(order_id: str) -> dict[str, Any]:
        """Read one simulated order.

        Args:
            order_id (str): The order id.

        Returns:
            dict[str, Any]: The order.
        """
        return _order(order_id)

    @router.post(
        f"{prefix}/orders/{{order_id}}/{{transition}}",
        tags=tags,
        response_model=MercadoPagoSimulationResultSchema,
        include_in_schema=include_in_schema,
        summary=route_docs.summary("mercado_pago.simulate_order_transition"),
        description=route_docs.description("mercado_pago.simulate_order_transition"),
    )
    async def simulate_order_transition(
        request: Request,
        order_id: str,
        transition: MercadoPagoTransition,
        amount_cents: int | None = Query(default=None, gt=0),
    ) -> MercadoPagoSimulationResultSchema:
        """Move an order and deliver the webhook announcing it.

        Args:
            request (Request): The request, whose app receives the delivery.
            order_id (str): The order id.
            transition (MercadoPagoTransition): The move.
            amount_cents (int | None): For ``refund``: a partial amount.

        Returns:
            MercadoPagoSimulationResultSchema: The order and the delivery.

        Raises:
            HTTPException: ``404`` for an unknown order, ``409`` for a move
                the order's state does not allow.
        """
        _order(order_id)
        try:
            moved = api.apply(order_id, transition, amount_cents)
        except ValueError as error:
            raise HTTPException(409, str(error)) from None
        order = api.order(order_id)
        action = str(moved.payload["action"])
        delivery = build_mercado_pago_delivery(order, secret=secret, action=action)
        response = await _deliver(request, webhook_route, delivery)
        try:
            body: Any = response.json()
        except ValueError:
            body = response.text
        return MercadoPagoSimulationResultSchema(
            order=order,
            delivery=MercadoPagoSimulatedDeliverySchema(
                action=action, status_code=response.status_code, body=body
            ),
        )


__all__: list[str] = [
    "DEFAULT_SIMULATION_PREFIX",
    "SIMULATION_ENVIRONMENTS",
    "SIMULATION_TAG",
    "MercadoPagoSimulatedDeliverySchema",
    "MercadoPagoSimulationResultSchema",
    "MercadoPagoTransition",
    "add_simulation_routes",
    "ensure_simulation_allowed",
    "mercado_pago_provider_for",
    "simulation_api_for",
]
