"""Simulate a Mercado Pago move and deliver the signed webhook it produces.

:class:`MercadoPagoSimulator` is the one piece every simulation goes
through: it moves an order on a
:class:`~tempest_fastapi_sdk.integrations.payment.adapters.MercadoPagoSimulationBackend`
(the in-memory simulator or a ``tempest mercado-pago fake-api``), signs the
notification with the service's webhook secret and posts it to the
service's webhook route. The development routes, the admin panel actions
(:func:`~tempest_fastapi_sdk.admin.mercado_pago_simulation_actions`) and a
script or end-to-end test all call it, so none of them reimplements the
transition, the signature or the delivery.

:func:`make_mercado_pago_simulation_router` mounts only the development
routes, delivering to a webhook route the service wrote itself.
:func:`~tempest_fastapi_sdk.integrations.payment.adapters.make_mercado_pago_router`
with ``simulate=True`` mounts the same routes next to the SDK's own webhook
route.

Every simulation is refused outside ``ENV=development`` and ``ENV=test``.
"""

from __future__ import annotations

from enum import Enum
from typing import TYPE_CHECKING, Any

import httpx
from fastapi import APIRouter, HTTPException, Query, Request
from starlette.routing import NoMatchFound

from tempest_fastapi_sdk.api.route_docs import RouteDocs
from tempest_fastapi_sdk.integrations.payment.adapters.mercado_pago_simulation import (
    DEFAULT_SIMULATION_PREFIX,
    SIMULATION_TAG,
    MercadoPagoSimulatedDeliverySchema,
    MercadoPagoSimulationBackend,
    MercadoPagoSimulationResultSchema,
    MercadoPagoTransition,
    RemoteMercadoPagoSimulation,
    _InMemorySimulation,
    ambient_settings,
    ensure_simulation_allowed,
)

if TYPE_CHECKING:
    from starlette.types import ASGIApp

    from tempest_fastapi_sdk.settings.mixins import MercadoPagoSettings
    from tempest_fastapi_sdk.testing.fakes.mercado_pago import (
        FakeMercadoPagoOrdersAPI,
    )

LOCAL_BASE_URL: str = "http://localhost"
"""Base URL of an in-process delivery whose ``webhook_url`` is a bare path."""


def resolve_webhook_secret(
    secret: str | None,
    settings: MercadoPagoSettings | None,
    simulation_api: FakeMercadoPagoOrdersAPI | None,
    what: str,
) -> str:
    """Pick the webhook secret a delivery is signed or verified with.

    Args:
        secret (str | None): The explicit secret.
        settings (MercadoPagoSettings | None): The service's settings.
            ``None`` reads the environment and ``.env``.
        simulation_api (FakeMercadoPagoOrdersAPI | None): The in-memory
            simulator, when there is one.
        what (str): Who is asking, for the message.

    Returns:
        str: ``secret``, else ``MERCADOPAGO_WEBHOOK_SECRET``, else the
        in-memory simulator's secret.

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
            f"{what} needs a non-empty webhook secret: pass `secret=` or set "
            "MERCADOPAGO_WEBHOOK_SECRET. An empty secret rejects every delivery."
        )
    return resolved


class MercadoPagoSimulator:
    """Move an order and deliver the signed webhook announcing it.

    Where the delivery goes:

    - ``app`` and ``webhook_url`` — in process, through the whole
      application (middleware, the signature check, the service's handler)
      over ``httpx.ASGITransport``. ``webhook_url`` is a path
      (``"/api/webhook/mercadopago"``, sent to ``http://localhost``) or an
      absolute URL whose host the app accepts.
    - ``webhook_url`` alone — over the network, to a running service
      (``"http://api:8000/api/webhook/mercadopago"``), with ``client`` or
      a short-lived client per call.
    - neither — only :meth:`apply_to` delivers; the development routes use
      it with the request's own application.

    Attributes:
        backend (MercadoPagoSimulationBackend): Where orders are moved.
        secret (str): The secret the service's webhook route verifies with.
        webhook_url (str | None): Where :meth:`apply` delivers.
        app (ASGIApp | None): The application that receives an in-process
            delivery.
        timeout (float): Seconds per delivery when no client is injected.
    """

    def __init__(
        self,
        backend: FakeMercadoPagoOrdersAPI | MercadoPagoSimulationBackend,
        *,
        secret: str | None = None,
        settings: MercadoPagoSettings | None = None,
        app: ASGIApp | None = None,
        webhook_url: str | None = None,
        client: httpx.AsyncClient | None = None,
        timeout: float = 10.0,
    ) -> None:
        """Build the simulator.

        Args:
            backend (FakeMercadoPagoOrdersAPI | MercadoPagoSimulationBackend):
                The in-memory simulator the provider runs over, or a backend
                such as
                :class:`~tempest_fastapi_sdk.integrations.payment.adapters.RemoteMercadoPagoSimulation`.
            secret (str | None): The secret the service verifies deliveries
                with. ``None`` reads ``MERCADOPAGO_WEBHOOK_SECRET`` from
                ``settings`` (or the environment), then falls back to the
                in-memory simulator's secret.
            settings (MercadoPagoSettings | None): The service's settings,
                for the secret and for ``ENV``.
            app (ASGIApp | None): Deliver in process to this application.
                Needs ``webhook_url``; excludes ``client``.
            webhook_url (str | None): The service's webhook route.
            client (httpx.AsyncClient | None): The client a network delivery
                is sent with; its lifecycle stays with the caller.
            timeout (float): Seconds per delivery when ``client`` is
                ``None``.

        Raises:
            RuntimeError: Outside ``ENV=development`` and ``ENV=test``.
            ValueError: When no webhook secret is found, ``app`` comes
                without ``webhook_url``, ``app`` comes with ``client``, or a
                network ``webhook_url`` is not absolute.
        """
        ensure_simulation_allowed(settings, "MercadoPagoSimulator")
        from tempest_fastapi_sdk.testing.fakes.mercado_pago import (
            FakeMercadoPagoOrdersAPI,
        )

        if app is not None and webhook_url is None:
            raise ValueError("`app` needs `webhook_url`, the webhook route's path")
        if app is not None and client is not None:
            raise ValueError("pass `app` or `client`, not both")
        if (
            app is None
            and webhook_url is not None
            and not webhook_url.startswith(("http://", "https://"))
        ):
            raise ValueError(
                "a `webhook_url` without `app` must be absolute "
                f"(got {webhook_url!r}); pass `app=` to deliver in process"
            )
        in_memory: FakeMercadoPagoOrdersAPI | None = None
        if isinstance(backend, FakeMercadoPagoOrdersAPI):
            in_memory = backend
            self.backend: MercadoPagoSimulationBackend = _InMemorySimulation(backend)
        else:
            self.backend = backend
        self.secret: str = resolve_webhook_secret(
            secret, settings, in_memory, "MercadoPagoSimulator"
        )
        self.app: ASGIApp | None = app
        self.webhook_url: str | None = webhook_url
        self.timeout: float = timeout
        self._client: httpx.AsyncClient | None = client

    async def apply(
        self,
        reference: str,
        transition: MercadoPagoTransition | str,
        *,
        amount_cents: int | None = None,
    ) -> MercadoPagoSimulationResultSchema:
        """Move an order and deliver the webhook to ``webhook_url``.

        Args:
            reference (str): The order id or the ``external_reference`` the
                service sent on create (the most recent order wins when
                several share it).
            transition (MercadoPagoTransition | str): ``pay``, ``expire``,
                ``fail``, ``cancel``, ``capture`` or ``refund``.
            amount_cents (int | None): For ``refund``: a partial amount.

        Returns:
            MercadoPagoSimulationResultSchema: The order after the move and
            what the webhook route answered. A route that refused the
            delivery (``401``) or raised (``500``) is reported here, not
            raised.

        Raises:
            RuntimeError: When the simulator was built without
                ``webhook_url``.
            KeyError: If no order matches ``reference``.
            ValueError: If the move does not apply to the order's state.
            httpx.HTTPError: When a remote backend cannot be reached, or a
                network delivery cannot reach the service.
        """
        if self.webhook_url is None:
            raise RuntimeError(
                "MercadoPagoSimulator.apply needs `webhook_url`; use apply_to "
                "with a client and an endpoint instead."
            )
        if self.app is not None:
            base_url = (
                LOCAL_BASE_URL
                if self.webhook_url.startswith("/")
                else str(httpx.URL(self.webhook_url).copy_with(path="/", query=None))
            )
            transport = httpx.ASGITransport(app=self.app, raise_app_exceptions=False)
            async with httpx.AsyncClient(
                transport=transport, base_url=base_url.rstrip("/")
            ) as client:
                return await self.apply_to(
                    reference,
                    transition,
                    client=client,
                    endpoint=self.webhook_url,
                    amount_cents=amount_cents,
                )
        if self._client is not None:
            return await self.apply_to(
                reference,
                transition,
                client=self._client,
                endpoint=self.webhook_url,
                amount_cents=amount_cents,
            )
        async with httpx.AsyncClient(timeout=self.timeout) as client:
            return await self.apply_to(
                reference,
                transition,
                client=client,
                endpoint=self.webhook_url,
                amount_cents=amount_cents,
            )

    async def apply_to(
        self,
        reference: str,
        transition: MercadoPagoTransition | str,
        *,
        client: httpx.AsyncClient,
        endpoint: str,
        amount_cents: int | None = None,
    ) -> MercadoPagoSimulationResultSchema:
        """Move an order and deliver the webhook with the given client.

        Args:
            reference (str): The order id or its ``external_reference``.
            transition (MercadoPagoTransition | str): The move.
            client (httpx.AsyncClient): The client the delivery is posted
                with — one over ``httpx.ASGITransport`` reaches an app in
                process.
            endpoint (str): The webhook route, relative to the client's base
                URL or absolute.
            amount_cents (int | None): For ``refund``: a partial amount.

        Returns:
            MercadoPagoSimulationResultSchema: The order and the delivery.

        Raises:
            KeyError: If no order matches ``reference``.
            ValueError: If the move does not apply to the order's state.
            httpx.HTTPError: When a remote backend cannot be reached, or the
                delivery cannot reach the service.
        """
        from tempest_fastapi_sdk.testing.fakes.mercado_pago import (
            build_mercado_pago_delivery,
        )

        order, action = await self.backend.apply(reference, transition, amount_cents)
        delivery = build_mercado_pago_delivery(order, secret=self.secret, action=action)
        response = await delivery.send(client, endpoint)
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


def _webhook_path(request: Request, webhook_route: str) -> str:
    """Resolve the service's webhook route on the request's application.

    Args:
        request (Request): The simulation request.
        webhook_route (str): A path starting with ``/``, or a route name.

    Returns:
        str: The route's path.
    """
    if webhook_route.startswith("/"):
        return webhook_route
    try:
        return str(request.app.url_path_for(webhook_route))
    except NoMatchFound:
        return str(request.url_for(webhook_route).path)


def add_simulation_routes(
    router: APIRouter,
    *,
    simulator: MercadoPagoSimulator,
    webhook_route: str,
    prefix: str,
    route_docs: RouteDocs,
    include_in_schema: bool = True,
) -> None:
    """Mount the simulation routes on a router.

    ``{order_id}`` below accepts the order id **or** the
    ``external_reference`` the service sent on create (the most recent order
    wins when several share it), so a developer can simulate by the
    service's own ``correlation_id``.

    - ``GET {prefix}/orders`` — every order, oldest first.
    - ``GET {prefix}/orders/{order_id}`` — one order; ``404`` when unknown.
    - ``POST {prefix}/orders/{order_id}/{transition}`` — apply the move,
      sign the delivery and post it to the webhook route, in process, with
      the host the caller used; ``404`` for an unknown order, ``409`` for a
      move the order's state does not allow (a refund before payment),
      ``422`` for an unknown move, ``502`` when a remote simulator cannot be
      reached or answers something that is not its JSON.

    Args:
        router (APIRouter): Where to mount.
        simulator (MercadoPagoSimulator): Moves, signs and delivers.
        webhook_route (str): The service's webhook route: its name, or its
            path when it starts with ``/``.
        prefix (str): Path prefix.
        route_docs (RouteDocs): The operation text.
        include_in_schema (bool): Whether the routes appear in OpenAPI.
    """
    backend = simulator.backend
    tags: list[str | Enum] = [SIMULATION_TAG]

    def _unreachable(error: httpx.HTTPError) -> HTTPException:
        """Build the answer for a simulator that could not be asked.

        Args:
            error (httpx.HTTPError): What the HTTP client raised.

        Returns:
            HTTPException: ``502`` naming the error.
        """
        return HTTPException(502, f"simulator unavailable: {error!r}")

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

        Raises:
            HTTPException: ``502`` when a remote simulator cannot be reached
                or answers something that is not its JSON.
        """
        try:
            return await backend.list_orders()
        except httpx.HTTPError as error:
            raise _unreachable(error) from None

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
            order_id (str): The order id or its ``external_reference``.

        Returns:
            dict[str, Any]: The order.

        Raises:
            HTTPException: ``404`` for an unknown order, ``502`` when a
                remote simulator cannot be reached.
        """
        try:
            return await backend.find_order(order_id)
        except KeyError:
            raise HTTPException(404, f"order {order_id} not found") from None
        except httpx.HTTPError as error:
            raise _unreachable(error) from None

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
            order_id (str): The order id or its ``external_reference``.
            transition (MercadoPagoTransition): The move.
            amount_cents (int | None): For ``refund``: a partial amount.

        Returns:
            MercadoPagoSimulationResultSchema: The order and the delivery.

        Raises:
            HTTPException: ``404`` for an unknown order, ``409`` for a move
                the order's state does not allow, ``502`` when a remote
                simulator cannot be reached.
        """
        path = _webhook_path(request, webhook_route)
        transport = httpx.ASGITransport(app=request.app, raise_app_exceptions=False)
        base_url = str(request.base_url).rstrip("/")
        try:
            async with httpx.AsyncClient(
                transport=transport, base_url=base_url
            ) as client:
                return await simulator.apply_to(
                    order_id,
                    transition,
                    client=client,
                    endpoint=path,
                    amount_cents=amount_cents,
                )
        except KeyError:
            raise HTTPException(404, f"order {order_id} not found") from None
        except ValueError as error:
            raise HTTPException(409, str(error)) from None
        except httpx.HTTPError as error:
            raise _unreachable(error) from None


def make_mercado_pago_simulation_router(
    *,
    webhook_route: str,
    simulation_url: str | None = None,
    simulation_api: FakeMercadoPagoOrdersAPI | None = None,
    simulation_client: httpx.AsyncClient | None = None,
    secret: str | None = None,
    settings: MercadoPagoSettings | None = None,
    prefix: str = DEFAULT_SIMULATION_PREFIX,
    include_in_schema: bool = True,
    docs_locale: str | None = None,
) -> APIRouter:
    """Build only the development routes, delivering to the service's webhook.

    For a service that wrote its own webhook route (its own controller,
    ``BackgroundTasks``, a worker): the simulation then exercises the same
    path production does. Mount the router on the **same** application as
    that route — the delivery goes through it in process.

    Args:
        webhook_route (str): The service's webhook route: its name
            (``@router.post(..., name="mercado_pago_webhook")``), or its path
            when it starts with ``/`` (``"/api/webhook/mercadopago"``).
        simulation_url (str | None): Base URL of a running
            ``tempest mercado-pago fake-api``, shared with the worker
            through ``MERCADOPAGO_BASE_URL``. Excludes ``simulation_api``.
        simulation_api (FakeMercadoPagoOrdersAPI | None): The in-memory
            simulator the provider runs over (see
            :func:`~tempest_fastapi_sdk.integrations.payment.adapters.simulation_api_for`).
        simulation_client (httpx.AsyncClient | None): The client the remote
            simulation sends with; its lifecycle stays with the caller.
            Needs ``simulation_url``.
        secret (str | None): The secret the service's webhook route verifies
            with. ``None`` reads ``MERCADOPAGO_WEBHOOK_SECRET``.
        settings (MercadoPagoSettings | None): The service's settings, for
            the secret and for ``ENV``.
        prefix (str): Path prefix of the routes.
        include_in_schema (bool): Whether the routes appear in OpenAPI.
        docs_locale (str | None): Language of the OpenAPI text —
            ``"en-US"`` or ``"pt-BR"``; ``None`` reads ``API_DOCS_LOCALE``.

    Returns:
        APIRouter: Ready for ``app.include_router``.

    Raises:
        RuntimeError: Outside ``ENV=development`` and ``ENV=test``.
        ValueError: When neither or both of ``simulation_url`` and
            ``simulation_api`` are given, ``simulation_client`` comes without
            ``simulation_url``, or no webhook secret is found.
    """
    ensure_simulation_allowed(settings, "make_mercado_pago_simulation_router")
    if simulation_client is not None and simulation_url is None:
        raise ValueError("`simulation_client` needs `simulation_url`")
    if (simulation_url is None) == (simulation_api is None):
        raise ValueError("pass exactly one of `simulation_url` and `simulation_api`")
    backend: FakeMercadoPagoOrdersAPI | MercadoPagoSimulationBackend = (
        RemoteMercadoPagoSimulation(simulation_url, client=simulation_client)
        if simulation_url is not None
        else simulation_api  # type: ignore[assignment]
    )
    simulator = MercadoPagoSimulator(backend, secret=secret, settings=settings)
    router = APIRouter()
    add_simulation_routes(
        router,
        simulator=simulator,
        webhook_route=webhook_route,
        prefix=prefix,
        route_docs=RouteDocs(docs_locale),
        include_in_schema=include_in_schema,
    )
    return router


__all__: list[str] = [
    "LOCAL_BASE_URL",
    "MercadoPagoSimulator",
    "add_simulation_routes",
    "make_mercado_pago_simulation_router",
    "resolve_webhook_secret",
]
