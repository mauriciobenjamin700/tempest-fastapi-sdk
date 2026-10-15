"""RevenueCat: in-app subscriptions from the App Store and Google Play.

.. code-block:: python

    from tempest_fastapi_sdk.integrations.subscriptions.revenuecat import (
        make_revenuecat_webhook_router,
    )

    app.include_router(
        make_revenuecat_webhook_router(
            inbox=inbox, secret=settings.REVENUECAT_WEBHOOK_SECRET
        )
    )

Five pieces, each written from RevenueCat's own documentation (the URL is in
every module docstring; nothing here was measured against a live account):

- **Webhook authentication** — the ``Authorization`` value configured in the
  dashboard and, when enabled, the ``X-RevenueCat-Webhook-Signature`` HMAC.
  Constant-time comparison; an empty secret refuses every delivery.
- **A router that records and answers** — the event goes into a
  :class:`~tempest_fastapi_sdk.db.WebhookInbox` keyed by ``event.id``, so a
  retried delivery is recorded once, and processing happens off the request.
- **Typed models** of the webhook body and of the v1 customer info, with
  ``extra="allow"`` and ``subscriber_attributes`` kept out of ``model_dump``.
- **normalize** — one event in, one :class:`SubscriptionSnapshot` out,
  covering every documented event type and answering ``UNKNOWN`` for a new
  one instead of raising.
- **RevenueCatClient** — ``GET /v1/subscribers/{app_user_id}`` for
  reconciliation.

What a state means for your product — which plan, how long a grace period
lasts, what a refund takes away — stays in your service.
"""

from tempest_fastapi_sdk.integrations.subscriptions.revenuecat.client import (
    REVENUECAT_API_BASE_URL as REVENUECAT_API_BASE_URL,
)
from tempest_fastapi_sdk.integrations.subscriptions.revenuecat.client import (
    RevenueCatClient as RevenueCatClient,
)
from tempest_fastapi_sdk.integrations.subscriptions.revenuecat.client import (
    revenuecat_http_client as revenuecat_http_client,
)
from tempest_fastapi_sdk.integrations.subscriptions.revenuecat.events import (
    RevenueCatEnvironmentEnum as RevenueCatEnvironmentEnum,
)
from tempest_fastapi_sdk.integrations.subscriptions.revenuecat.events import (
    RevenueCatEventTypeEnum as RevenueCatEventTypeEnum,
)
from tempest_fastapi_sdk.integrations.subscriptions.revenuecat.events import (
    RevenueCatPeriodTypeEnum as RevenueCatPeriodTypeEnum,
)
from tempest_fastapi_sdk.integrations.subscriptions.revenuecat.events import (
    RevenueCatReasonEnum as RevenueCatReasonEnum,
)
from tempest_fastapi_sdk.integrations.subscriptions.revenuecat.events import (
    RevenueCatStoreEnum as RevenueCatStoreEnum,
)
from tempest_fastapi_sdk.integrations.subscriptions.revenuecat.schemas import (
    RevenueCatCustomerInfo as RevenueCatCustomerInfo,
)
from tempest_fastapi_sdk.integrations.subscriptions.revenuecat.schemas import (
    RevenueCatEntitlement as RevenueCatEntitlement,
)
from tempest_fastapi_sdk.integrations.subscriptions.revenuecat.schemas import (
    RevenueCatEvent as RevenueCatEvent,
)
from tempest_fastapi_sdk.integrations.subscriptions.revenuecat.schemas import (
    RevenueCatExperiment as RevenueCatExperiment,
)
from tempest_fastapi_sdk.integrations.subscriptions.revenuecat.schemas import (
    RevenueCatSubscriber as RevenueCatSubscriber,
)
from tempest_fastapi_sdk.integrations.subscriptions.revenuecat.schemas import (
    RevenueCatSubscription as RevenueCatSubscription,
)
from tempest_fastapi_sdk.integrations.subscriptions.revenuecat.schemas import (
    RevenueCatWebhook as RevenueCatWebhook,
)
from tempest_fastapi_sdk.integrations.subscriptions.revenuecat.snapshot import (
    SubscriptionSnapshot as SubscriptionSnapshot,
)
from tempest_fastapi_sdk.integrations.subscriptions.revenuecat.snapshot import (
    SubscriptionStatusEnum as SubscriptionStatusEnum,
)
from tempest_fastapi_sdk.integrations.subscriptions.revenuecat.snapshot import (
    normalize as normalize,
)
from tempest_fastapi_sdk.integrations.subscriptions.revenuecat.webhooks import (
    DEFAULT_SIGNATURE_TOLERANCE_SECONDS as DEFAULT_SIGNATURE_TOLERANCE_SECONDS,
)
from tempest_fastapi_sdk.integrations.subscriptions.revenuecat.webhooks import (
    REVENUECAT_AUTHORIZATION_HEADER as REVENUECAT_AUTHORIZATION_HEADER,
)
from tempest_fastapi_sdk.integrations.subscriptions.revenuecat.webhooks import (
    REVENUECAT_PROVIDER as REVENUECAT_PROVIDER,
)
from tempest_fastapi_sdk.integrations.subscriptions.revenuecat.webhooks import (
    REVENUECAT_SIGNATURE_HEADER as REVENUECAT_SIGNATURE_HEADER,
)
from tempest_fastapi_sdk.integrations.subscriptions.revenuecat.webhooks import (
    RevenueCatWebhookAck as RevenueCatWebhookAck,
)
from tempest_fastapi_sdk.integrations.subscriptions.revenuecat.webhooks import (
    RevenueCatWebhookDelivery as RevenueCatWebhookDelivery,
)
from tempest_fastapi_sdk.integrations.subscriptions.revenuecat.webhooks import (
    SecretSource as SecretSource,
)
from tempest_fastapi_sdk.integrations.subscriptions.revenuecat.webhooks import (
    make_revenuecat_webhook_dependency as make_revenuecat_webhook_dependency,
)
from tempest_fastapi_sdk.integrations.subscriptions.revenuecat.webhooks import (
    make_revenuecat_webhook_router as make_revenuecat_webhook_router,
)
from tempest_fastapi_sdk.integrations.subscriptions.revenuecat.webhooks import (
    parse_signature_header as parse_signature_header,
)
from tempest_fastapi_sdk.integrations.subscriptions.revenuecat.webhooks import (
    parse_webhook as parse_webhook,
)
from tempest_fastapi_sdk.integrations.subscriptions.revenuecat.webhooks import (
    sign_payload as sign_payload,
)
from tempest_fastapi_sdk.integrations.subscriptions.revenuecat.webhooks import (
    verify_authorization as verify_authorization,
)
from tempest_fastapi_sdk.integrations.subscriptions.revenuecat.webhooks import (
    verify_signature as verify_signature,
)

__all__: list[str] = [
    "DEFAULT_SIGNATURE_TOLERANCE_SECONDS",
    "REVENUECAT_API_BASE_URL",
    "REVENUECAT_AUTHORIZATION_HEADER",
    "REVENUECAT_PROVIDER",
    "REVENUECAT_SIGNATURE_HEADER",
    "RevenueCatClient",
    "RevenueCatCustomerInfo",
    "RevenueCatEntitlement",
    "RevenueCatEnvironmentEnum",
    "RevenueCatEvent",
    "RevenueCatEventTypeEnum",
    "RevenueCatExperiment",
    "RevenueCatPeriodTypeEnum",
    "RevenueCatReasonEnum",
    "RevenueCatStoreEnum",
    "RevenueCatSubscriber",
    "RevenueCatSubscription",
    "RevenueCatWebhook",
    "RevenueCatWebhookAck",
    "RevenueCatWebhookDelivery",
    "SecretSource",
    "SubscriptionSnapshot",
    "SubscriptionStatusEnum",
    "make_revenuecat_webhook_dependency",
    "make_revenuecat_webhook_router",
    "normalize",
    "parse_signature_header",
    "parse_webhook",
    "revenuecat_http_client",
    "sign_payload",
    "verify_authorization",
    "verify_signature",
]
