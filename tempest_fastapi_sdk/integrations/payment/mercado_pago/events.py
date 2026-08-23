"""Notification topics Mercado Pago sends to a ``notification_url``.

The three members below are the ones the **pinned specification** names, in
its ``Webhook events triggered:`` lines — extracted from it rather than
transcribed from the developer portal:

.. code-block:: bash

    grep -oE "Webhook events triggered:\\*\\* [^\\n]*" vendor/mercadopago-openapi.yaml

Mercado Pago's portal documents more topics than these (subscriptions,
invoices, claims). They are deliberately absent: the specification is what
this package is generated from and what the drift test pins, and adding a
member the vendored artifact does not mention would be transcription, not
measurement. :attr:`MercadoPagoEvent.UNKNOWN` is what a topic outside this
list resolves to, with its original string preserved by the caller.
"""

from tempest_fastapi_sdk.core.enums import BaseStrEnum


class MercadoPagoEvent(BaseStrEnum):
    """A notification topic, as Mercado Pago names it.

    Attributes:
        PAYMENT (str): A payment was created or changed state.
        MERCHANT_ORDER (str): A merchant order changed.
        POINT_INTEGRATION (str): An in-person (Point) terminal event.
        UNKNOWN (str): A topic this SDK version does not name. The
            delivery is still verified and still reaches the caller — only
            the classification is absent.
    """

    PAYMENT = "payment"
    MERCHANT_ORDER = "merchant_order"
    POINT_INTEGRATION = "point_integration_wh"
    UNKNOWN = "unknown"


__all__: list[str] = ["MercadoPagoEvent"]
