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
- :class:`MercadoPagoSimulationBackend` is where a simulation reads and
  moves orders the way only the provider can (pay, expire, fail, cancel,
  capture, refund); ``MercadoPagoSimulator`` (module
  ``mercado_pago_simulator``) signs and delivers the notification each move
  produces.

The in-memory simulator lives in one process. A service whose worker
re-reads orders runs ``tempest mercado-pago fake-api`` instead, points every
process's ``MERCADOPAGO_BASE_URL`` at it, and mounts the routes over a
:class:`RemoteMercadoPagoSimulation` — both satisfy
:class:`MercadoPagoSimulationBackend`.

Simulation is refused outside ``ENV=development`` and ``ENV=test`` — see
:func:`ensure_simulation_allowed`. The simulator module is imported only when
a simulation is built, so a production process never loads it.
"""

from __future__ import annotations

import os
import weakref
from typing import TYPE_CHECKING, Any, Final, Literal, Protocol, overload
from urllib.parse import quote

import httpx

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
        what (str): What is being built, for the message
            (``"make_mercado_pago_router with simulate=True"``).

    Raises:
        RuntimeError: When the environment is not ``development`` or
            ``test``.
    """
    environment = getattr(ambient_settings(settings), "ENV", None) or os.environ.get(
        "ENV"
    )
    if environment not in SIMULATION_ENVIRONMENTS:
        raise RuntimeError(
            f"{what} needs ENV=development or ENV=test "
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
    ensure_simulation_allowed(settings, "mercado_pago_provider_for with simulate=True")
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


class MercadoPagoSimulationBackend(Protocol):
    """Where the simulation routes read and move orders.

    Two implementations ship: the in-memory
    :class:`~tempest_fastapi_sdk.testing.fakes.FakeMercadoPagoOrdersAPI`
    (wrapped by :class:`MercadoPagoSimulator`) and
    :class:`RemoteMercadoPagoSimulation`, which drives a
    ``tempest mercado-pago fake-api`` over HTTP so an API and a worker in
    separate processes see the same orders.

    ``reference`` is an order id or the ``external_reference`` the service
    sent on create; when several orders share an ``external_reference`` the
    most recent one is meant.
    """

    async def list_orders(self) -> list[dict[str, Any]]:
        """List every order, oldest first.

        Returns:
            list[dict[str, Any]]: The orders, as ``GET /v1/orders/{id}``
            answers each.
        """
        ...

    async def find_order(self, reference: str, /) -> dict[str, Any]:
        """Read one order by id or by ``external_reference``.

        Args:
            reference (str): The order id or its ``external_reference``.

        Returns:
            dict[str, Any]: The order.

        Raises:
            KeyError: If no order matches.
        """
        ...

    async def apply(
        self, reference: str, transition: str, /, amount_cents: int | None = None
    ) -> tuple[dict[str, Any], str]:
        """Apply a transition to an order.

        Args:
            reference (str): The order id or its ``external_reference``.
            transition (str): ``pay``, ``expire``, ``fail``, ``cancel``,
                ``capture`` or ``refund``.
            amount_cents (int | None): For ``refund``: a partial amount.

        Returns:
            tuple[dict[str, Any], str]: The order after the move and the
            delivery ``action`` announcing it.

        Raises:
            KeyError: If no order matches.
            ValueError: If the move does not apply to the order's state.
        """
        ...


class _InMemorySimulation:
    """:class:`MercadoPagoSimulationBackend` over a simulator in this process.

    Attributes:
        api (FakeMercadoPagoOrdersAPI): The simulator.
    """

    def __init__(self, api: FakeMercadoPagoOrdersAPI) -> None:
        """Wrap a simulator.

        Args:
            api (FakeMercadoPagoOrdersAPI): The simulator.
        """
        self.api: FakeMercadoPagoOrdersAPI = api

    async def list_orders(self) -> list[dict[str, Any]]:
        """List every order, oldest first.

        Returns:
            list[dict[str, Any]]: The orders.
        """
        return [self.api.order(order_id) for order_id in self.api.order_ids]

    async def find_order(self, reference: str, /) -> dict[str, Any]:
        """Read one order by id or by ``external_reference``.

        Args:
            reference (str): The order id or its ``external_reference``.

        Returns:
            dict[str, Any]: The order.

        Raises:
            KeyError: If no order matches.
        """
        return self.api.order(self.api.find_order_id(reference))

    async def apply(
        self, reference: str, transition: str, /, amount_cents: int | None = None
    ) -> tuple[dict[str, Any], str]:
        """Apply a transition to an order.

        Args:
            reference (str): The order id or its ``external_reference``.
            transition (str): The transition name.
            amount_cents (int | None): For ``refund``: a partial amount.

        Returns:
            tuple[dict[str, Any], str]: The order and the delivery action.

        Raises:
            KeyError: If no order matches.
            ValueError: If the move does not apply to the order's state.
        """
        order_id = self.api.find_order_id(reference)
        moved = self.api.apply(order_id, transition, amount_cents)
        return self.api.order(order_id), str(moved.payload["action"])


class RemoteMercadoPagoSimulation:
    """Drive a ``tempest mercado-pago fake-api`` running in another process.

    The in-memory simulator lives in one process, so a worker that re-reads
    an order (a sweep of pending charges, a refund cron) cannot see what the
    API process created. Run ``tempest mercado-pago fake-api`` once, point
    every process's ``MERCADOPAGO_BASE_URL`` at it, and give
    ``make_mercado_pago_router(simulate=True, simulation_url=...)`` the same
    URL: the simulation routes then move the order **on the fake-api**,
    through its control prefix
    (:data:`~tempest_fastapi_sdk.testing.fakes.FAKE_CONTROL_PREFIX`), and
    deliver the signed webhook to the service's own route, as the in-memory
    mode does.

    Attributes:
        base_url (str): The fake-api's base URL, without a trailing ``/``.
        timeout (float): Seconds per request when no client is injected.
    """

    def __init__(
        self,
        base_url: str,
        *,
        client: httpx.AsyncClient | None = None,
        timeout: float = 10.0,
    ) -> None:
        """Point at a running fake-api.

        Args:
            base_url (str): Where the fake-api answers, e.g.
                ``"http://mp-fake:8090"``.
            client (httpx.AsyncClient | None): The client to send with; its
                lifecycle stays with the caller. ``None`` opens a short-lived
                client per call.
            timeout (float): Seconds per request when ``client`` is ``None``.
        """
        from tempest_fastapi_sdk.testing.fakes.mercado_pago import (
            FAKE_CONTROL_PREFIX,
        )

        self.base_url: str = base_url.rstrip("/")
        self.timeout: float = timeout
        self._client: httpx.AsyncClient | None = client
        self._control: str = f"{self.base_url}{FAKE_CONTROL_PREFIX}/orders"

    async def _send(
        self, method: str, url: str, params: dict[str, int] | None = None
    ) -> httpx.Response:
        """Send one request to the fake-api.

        Args:
            method (str): ``GET`` or ``POST``.
            url (str): The absolute URL.
            params (dict[str, int] | None): Query parameters.

        Returns:
            httpx.Response: The answer, whatever its status.

        Raises:
            httpx.HTTPError: When the fake-api cannot be reached.
        """
        if self._client is not None:
            return await self._client.request(method, url, params=params)
        async with httpx.AsyncClient(timeout=self.timeout) as client:
            return await client.request(method, url, params=params)

    @staticmethod
    def _error(response: httpx.Response) -> str:
        """Read the fake-api's error message.

        Args:
            response (httpx.Response): An error answer.

        Returns:
            str: Its ``error`` field, or the raw text.
        """
        try:
            body: Any = response.json()
        except ValueError:
            return response.text
        return str(body.get("error", body)) if isinstance(body, dict) else str(body)

    def _checked(self, response: httpx.Response, reference: str) -> Any:
        """Turn an answer into its JSON body, or the protocol's exceptions.

        Args:
            response (httpx.Response): The fake-api's answer.
            reference (str): What was looked up, for the ``KeyError``.

        A ``200`` whose body is not JSON means ``base_url`` reaches
        something other than the fake-api (a proxy, a load balancer's error
        page, another service on that port). The decode error is a
        ``ValueError``, which the simulation routes read as a move the
        order's state does not allow, so it is re-raised as
        :class:`httpx.DecodingError`: an :class:`httpx.HTTPError`, answered
        ``502`` like an unreachable fake-api.

        Returns:
            Any: The decoded body of a ``200``.

        Raises:
            KeyError: On ``404``.
            ValueError: On ``409``.
            httpx.HTTPStatusError: On any other non-``200`` status.
            httpx.DecodingError: On a success whose body is not JSON.
        """
        if response.status_code == 404:
            raise KeyError(reference)
        if response.status_code == 409:
            raise ValueError(self._error(response))
        response.raise_for_status()
        try:
            return response.json()
        except ValueError:
            content_type = response.headers.get("content-type", "no content-type")
            raise httpx.DecodingError(
                f"{self.base_url} answered {response.status_code} "
                f"({content_type}) with a body that is not JSON; "
                "is simulation_url the fake-api?",
                request=response.request,
            ) from None

    def _member(self, response: httpx.Response, body: Any, key: str) -> Any:
        """Read one key of a decoded fake-api answer.

        A ``KeyError`` here would reach the simulation routes as an unknown
        order (``404``), so a body without the key is raised as
        :class:`httpx.DecodingError` instead, answered ``502``.

        Args:
            response (httpx.Response): The answer the body came from.
            body (Any): Its decoded JSON.
            key (str): The key the fake-api always sends.

        Returns:
            Any: The key's value.

        Raises:
            httpx.DecodingError: When the body is not an object with ``key``.
        """
        if not isinstance(body, dict) or key not in body:
            raise httpx.DecodingError(
                f"{self.base_url} answered {response.status_code} without "
                f"{key!r}; is simulation_url the fake-api?",
                request=response.request,
            )
        return body[key]

    async def list_orders(self) -> list[dict[str, Any]]:
        """List every order on the fake-api, oldest first.

        Returns:
            list[dict[str, Any]]: The orders; one request per order after the
            listing of ids.

        Raises:
            httpx.HTTPError: When the fake-api cannot be reached, answers an
                unexpected status, or answers a body that is not its JSON
                (:class:`httpx.DecodingError`).
        """
        response = await self._send("GET", self._control)
        listed = self._member(response, self._checked(response, ""), "orders")
        return [await self.find_order(str(order_id)) for order_id in listed]

    async def find_order(self, reference: str, /) -> dict[str, Any]:
        """Read one order by id or by ``external_reference``.

        Args:
            reference (str): The order id or its ``external_reference``.

        Returns:
            dict[str, Any]: The order.

        Raises:
            KeyError: If no order matches.
            httpx.HTTPError: When the fake-api cannot be reached, answers an
                unexpected status, or answers a body that is not its JSON
                (:class:`httpx.DecodingError`).
        """
        url = f"{self._control}/{quote(reference, safe='')}"
        found: dict[str, Any] = self._checked(await self._send("GET", url), reference)
        return found

    async def apply(
        self, reference: str, transition: str, /, amount_cents: int | None = None
    ) -> tuple[dict[str, Any], str]:
        """Apply a transition on the fake-api.

        Args:
            reference (str): The order id or its ``external_reference``.
            transition (str): The transition name.
            amount_cents (int | None): For ``refund``: a partial amount.

        Returns:
            tuple[dict[str, Any], str]: The order and the delivery action.

        Raises:
            KeyError: If no order matches.
            ValueError: If the move does not apply to the order's state.
            httpx.HTTPError: When the fake-api cannot be reached, answers an
                unexpected status, or answers a body that is not its JSON
                (:class:`httpx.DecodingError`).
        """
        url = f"{self._control}/{quote(reference, safe='')}/{transition}"
        params = None if amount_cents is None else {"amount_cents": amount_cents}
        response = await self._send("POST", url, params)
        moved = self._checked(response, reference)
        order: dict[str, Any] = self._member(response, moved, "order")
        return order, str(self._member(response, moved, "action"))


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


__all__: list[str] = [
    "DEFAULT_SIMULATION_PREFIX",
    "SIMULATION_ENVIRONMENTS",
    "SIMULATION_TAG",
    "MercadoPagoSimulatedDeliverySchema",
    "MercadoPagoSimulationBackend",
    "MercadoPagoSimulationResultSchema",
    "MercadoPagoTransition",
    "RemoteMercadoPagoSimulation",
    "ensure_simulation_allowed",
    "mercado_pago_provider_for",
    "simulation_api_for",
]
