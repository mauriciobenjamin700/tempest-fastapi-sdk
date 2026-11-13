"""Notification topics Mercado Pago sends to a ``notification_url``.

Three of the members below are the ones the **pinned specification** names, in
its ``Webhook events triggered:`` lines — extracted from it rather than
transcribed from the developer portal:

.. code-block:: bash

    grep -oE "Webhook events triggered:\\*\\* [^\\n]*" vendor/mercadopago-openapi.yaml

Mercado Pago's portal documents more topics than these (subscriptions,
invoices, claims). They are deliberately absent: the specification is what
this package is generated from and what the drift test pins, and adding a
member the vendored artifact does not mention would be transcription, not
measurement. :attr:`MercadoPagoEventEnum.UNKNOWN` is what a topic outside this
list resolves to, with its original string preserved by the caller.

:attr:`MercadoPagoEventEnum.ORDER` is the one member added on evidence other
than those lines. The specification names it indirectly — the
``WebhookNotification`` schema lists ``order.created`` / ``order.updated``
among its ``action`` values — and a live Orders delivery observed in
production on 2026-11-13 carried ``type=order`` in the query and the body
(issue #498). Without the member, every delivery of the Orders API, the API
the bundled adapters use, resolved to ``UNKNOWN``.
"""

from tempest_fastapi_sdk.core.enums import BaseStrEnum


class MercadoPagoEventEnum(BaseStrEnum):
    """A notification topic, as Mercado Pago names it.

    Attributes:
        PAYMENT (str): A payment was created or changed state.
        MERCHANT_ORDER (str): A merchant order changed.
        POINT_INTEGRATION (str): An in-person (Point) terminal event.
        ORDER (str): An order of the Orders API was created or changed
            state. The ``action`` in the body says which
            (``order.updated``, ``order.canceled``, ...).
        UNKNOWN (str): A topic this SDK version does not name. The
            delivery is still verified and still reaches the caller — only
            the classification is absent.
    """

    PAYMENT = "payment"
    MERCHANT_ORDER = "merchant_order"
    POINT_INTEGRATION = "point_integration_wh"
    ORDER = "order"
    UNKNOWN = "unknown"


__all__: list[str] = ["MercadoPagoEventEnum"]
