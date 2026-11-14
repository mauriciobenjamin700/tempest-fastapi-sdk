"""The simulated Orders API over HTTP, for a service running on its own."""

from __future__ import annotations

from typing import Final

import httpx
from fastapi import FastAPI, Request, Response
from fastapi.responses import JSONResponse

from tempest_fastapi_sdk.testing.fakes.mercado_pago.orders_api import (
    FakeMercadoPagoOrdersAPI,
)

FAKE_CONTROL_PREFIX: Final[str] = "/_fake"
"""Path prefix of the control routes :func:`make_fake_orders_app` adds.

``POST /_fake/orders/{order_id}/{transition}`` applies a transition over
HTTP, for a manual test against a service running on its own; Mercado Pago
has nothing under this prefix.
"""


def make_fake_orders_app(api: FakeMercadoPagoOrdersAPI) -> FastAPI:
    """Serve a simulator over HTTP, for a service running on its own.

    ``tempest mercado-pago fake-api`` runs it under uvicorn. Every
    ``/v1/orders`` route answers as the in-process handler does, and
    :data:`FAKE_CONTROL_PREFIX` adds:

    - ``GET /_fake/orders`` — every order id.
    - ``POST /_fake/orders/{order_id}/{transition}`` — apply
      :meth:`FakeMercadoPagoOrdersAPI.apply` (``?amount_cents=`` for a
      partial refund) and answer
      ``{"order": <order>, "action": <action>}``. The delivery itself is
      not sent: the server does not know the service's secret, so
      ``tempest mercado-pago simulate-webhook`` signs and sends it.

    Args:
        api (FakeMercadoPagoOrdersAPI): The simulator whose state the app
            serves; keep a reference to drive it from the same process.

    Returns:
        FastAPI: The app.
    """
    app = FastAPI(title="Fake Mercado Pago Orders API", docs_url=None, redoc_url=None)

    @app.get(f"{FAKE_CONTROL_PREFIX}/orders", include_in_schema=False)
    async def list_orders() -> JSONResponse:
        """List the orders created so far.

        Returns:
            JSONResponse: ``{"orders": [ids]}``.
        """
        return JSONResponse({"orders": api.order_ids})

    @app.post(
        f"{FAKE_CONTROL_PREFIX}/orders/{{order_id}}/{{transition}}",
        include_in_schema=False,
    )
    async def control(
        order_id: str, transition: str, amount_cents: int | None = None
    ) -> JSONResponse:
        """Apply a transition and report the new state.

        Args:
            order_id (str): The order id.
            transition (str): The transition name.
            amount_cents (int | None): For a partial refund.

        Returns:
            JSONResponse: The order and the action, ``404`` for an
            unknown order, ``409`` for a transition that does not apply.
        """
        try:
            delivery = api.apply(order_id, transition, amount_cents)
        except KeyError:
            return JSONResponse({"error": f"order {order_id} not found"}, 404)
        except ValueError as error:
            return JSONResponse({"error": str(error)}, 409)
        return JSONResponse(
            {"order": api.order(order_id), "action": delivery.payload["action"]}
        )

    @app.api_route("/{path:path}", methods=["GET", "POST"], include_in_schema=False)
    async def orders_api(request: Request) -> Response:
        """Forward a request to the in-process handler.

        Args:
            request (Request): The inbound request.

        Returns:
            Response: The handler's answer, verbatim.
        """
        answer = api(
            httpx.Request(
                request.method,
                str(request.url),
                headers=request.headers.raw,
                content=await request.body(),
            )
        )
        return Response(
            content=answer.content,
            status_code=answer.status_code,
            media_type="application/json",
        )

    return app


__all__: list[str] = ["FAKE_CONTROL_PREFIX", "make_fake_orders_app"]
