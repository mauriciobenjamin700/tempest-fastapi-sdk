"""Mercado Pago's Orders API and its webhook, simulated offline.

Two pieces, usable apart or together:

- :func:`build_mercado_pago_delivery` builds a notification the way Mercado
  Pago delivers one — query, headers, body — signed through
  :func:`~tempest_fastapi_sdk.integrations.payment.mercado_pago.webhooks.sign_manifest`,
  the function the SDK's own verifier recomputes. There is no second copy of
  the manifest rules here, so a delivery this module builds verifies exactly
  when :func:`make_mercado_pago_webhook_dependency` would accept the same
  values from the provider.
- :class:`FakeMercadoPagoOrdersAPI` answers ``/v1/orders`` with state: create
  (idempotent on ``X-Idempotency-Key``), read, cancel, capture, refund. It is
  an ``httpx`` handler, so the **real** :class:`MercadoPagoPixProvider` and
  :class:`MercadoPagoCardProvider` run over it unchanged — the adapters take
  an :class:`~tempest_fastapi_sdk.utils.http_client.HTTPClient`, and an
  ``HTTPClient`` takes a transport. The test then moves the order the way
  only the provider can (:meth:`~FakeMercadoPagoOrdersAPI.pay`,
  :meth:`~FakeMercadoPagoOrdersAPI.expire`, ...) and each move hands back the
  signed delivery announcing it.

What is measured and what is not, because a simulator is only as good as the
answers it copies:

- **Copied from the sandbox** (``vendor/mercadopago-evidence.md`` sections
  9.2 to 9.6, and the redacted fixtures under
  ``tests/integrations/payment/adapters/fixtures/mercado_pago_orders``): the
  order and payment states of every transition, the ``201`` of a create and
  of a refund, the ``200`` of a cancel (whole order) and of a capture (partial
  order), the ``402`` of a declined card with the order under ``data``, the
  idempotency rule (same key and body, same order; another body, ``409
  idempotency_key_already_used``), ``409 cannot_refund_order`` on an unpaid
  order, ``400`` for a create without a payer, and ``404`` for an unknown
  order id.
- **Copied from one production delivery** (section 11): the delivery's shape
  — ``?data.id=…&type=order`` plus ``data.external_reference``, a body with
  ``action``, ``type``, ``api_version``, ``live_mode``, ``application_id``,
  ``user_id`` and a summarized order under ``data``.
- **Assumed, and written as such where they appear**: the error bodies of a
  ``404``, of a cancel or capture in the wrong state, and of a refund above
  the balance; the ``action`` names other than ``order.canceled`` (see
  :func:`order_action`); the identifiers this simulator mints.
"""

from tempest_fastapi_sdk.testing.fakes.mercado_pago.delivery import (
    FAKE_APPLICATION_ID as FAKE_APPLICATION_ID,
)
from tempest_fastapi_sdk.testing.fakes.mercado_pago.delivery import (
    FAKE_USER_ID as FAKE_USER_ID,
)
from tempest_fastapi_sdk.testing.fakes.mercado_pago.delivery import (
    FAKE_WEBHOOK_SECRET as FAKE_WEBHOOK_SECRET,
)
from tempest_fastapi_sdk.testing.fakes.mercado_pago.delivery import (
    MercadoPagoDelivery as MercadoPagoDelivery,
)
from tempest_fastapi_sdk.testing.fakes.mercado_pago.delivery import (
    build_mercado_pago_delivery as build_mercado_pago_delivery,
)
from tempest_fastapi_sdk.testing.fakes.mercado_pago.delivery import (
    order_action as order_action,
)
from tempest_fastapi_sdk.testing.fakes.mercado_pago.orders_api import (
    DEFAULT_PIX_EXPIRATION as DEFAULT_PIX_EXPIRATION,
)
from tempest_fastapi_sdk.testing.fakes.mercado_pago.orders_api import (
    TRANSITIONS as TRANSITIONS,
)
from tempest_fastapi_sdk.testing.fakes.mercado_pago.orders_api import (
    FakeMercadoPagoOrdersAPI as FakeMercadoPagoOrdersAPI,
)
from tempest_fastapi_sdk.testing.fakes.mercado_pago.server import (
    FAKE_CONTROL_PREFIX as FAKE_CONTROL_PREFIX,
)
from tempest_fastapi_sdk.testing.fakes.mercado_pago.server import (
    make_fake_orders_app as make_fake_orders_app,
)

__all__: list[str] = [
    "DEFAULT_PIX_EXPIRATION",
    "FAKE_APPLICATION_ID",
    "FAKE_CONTROL_PREFIX",
    "FAKE_USER_ID",
    "FAKE_WEBHOOK_SECRET",
    "TRANSITIONS",
    "FakeMercadoPagoOrdersAPI",
    "MercadoPagoDelivery",
    "build_mercado_pago_delivery",
    "make_fake_orders_app",
    "order_action",
]
