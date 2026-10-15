"""Subscription providers — recurring access sold through someone else's store.

The grouping is by what the third party does, as in ``payment`` and
``messaging``: a service that moves from one subscription platform to another
changes one import segment.

Available:

- **`revenuecat`** — RevenueCat, the layer most Flutter and native apps use
  over App Store and Google Play billing. Webhook authentication, a router
  that records each event once in a
  :class:`~tempest_fastapi_sdk.db.WebhookInbox`, typed event models, a
  normalized subscription state, and ``GET /v1/subscribers`` for
  reconciliation. Hand-written: the OpenAPI document RevenueCat links for v1
  declares no paths.

.. code-block:: python

    from tempest_fastapi_sdk.integrations.subscriptions.revenuecat import (
        RevenueCatEvent,
        normalize,
    )
"""

from tempest_fastapi_sdk.integrations.subscriptions import revenuecat as revenuecat

__all__: list[str] = ["revenuecat"]
