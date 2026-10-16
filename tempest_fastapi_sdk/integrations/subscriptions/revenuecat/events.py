"""The closed vocabularies of a RevenueCat webhook, as enums.

Every value here is copied from RevenueCat's "Event Types and Fields" page,
vendored byte for byte in ``vendor/revenuecat-webhook-event-types.md``
(source: https://www.revenuecat.com/docs/integrations/webhooks/event-types-and-fields).
``tests/integrations/subscriptions/revenuecat/test_vendored_docs.py`` parses
that file and fails when a value here is missing from it or the page lists
one this module does not have — so a refresh of the vendored page that adds
an event type is a failing test, not a silent gap.

The page also says, under "Future Proofing" on
https://www.revenuecat.com/docs/integrations/webhooks, that new fields and
event types may be added "without changing the API version". That is why the
webhook models keep these fields as ``str`` and the enums are for comparing:
a :class:`~tempest_fastapi_sdk.core.BaseStrEnum` member compares equal to
its string value, and a value this release does not know still parses.
"""

from __future__ import annotations

from tempest_fastapi_sdk.core.enums import BaseStrEnum


class RevenueCatEventTypeEnum(BaseStrEnum):
    """Every ``event.type`` the RevenueCat documentation lists.

    Grouped as the documentation groups them. ``SUBSCRIBER_ALIAS`` is marked
    deprecated there ("New projects don't receive this webhook") and stays
    here because older projects still do.
    """

    TEST = "TEST"
    INITIAL_PURCHASE = "INITIAL_PURCHASE"
    RENEWAL = "RENEWAL"
    CANCELLATION = "CANCELLATION"
    UNCANCELLATION = "UNCANCELLATION"
    NON_RENEWING_PURCHASE = "NON_RENEWING_PURCHASE"
    SUBSCRIPTION_PAUSED = "SUBSCRIPTION_PAUSED"
    EXPIRATION = "EXPIRATION"
    BILLING_ISSUE = "BILLING_ISSUE"
    PRODUCT_CHANGE = "PRODUCT_CHANGE"
    SUBSCRIPTION_EXTENDED = "SUBSCRIPTION_EXTENDED"
    REFUND_REVERSED = "REFUND_REVERSED"
    INVOICE_ISSUANCE = "INVOICE_ISSUANCE"
    TRANSFER = "TRANSFER"
    TEMPORARY_ENTITLEMENT_GRANT = "TEMPORARY_ENTITLEMENT_GRANT"
    VIRTUAL_CURRENCY_TRANSACTION = "VIRTUAL_CURRENCY_TRANSACTION"
    EXPERIMENT_ENROLLMENT = "EXPERIMENT_ENROLLMENT"
    PURCHASE_REDEEMED = "PURCHASE_REDEEMED"
    SUBSCRIBER_ALIAS = "SUBSCRIBER_ALIAS"
    PRICE_INCREASE_CONSENT_REQUIRED = "PRICE_INCREASE_CONSENT_REQUIRED"
    PRICE_INCREASE_CONSENT_APPROVED = "PRICE_INCREASE_CONSENT_APPROVED"


class RevenueCatPeriodTypeEnum(BaseStrEnum):
    """``period_type`` of a subscription lifecycle event."""

    TRIAL = "TRIAL"
    INTRO = "INTRO"
    NORMAL = "NORMAL"
    PROMOTIONAL = "PROMOTIONAL"
    PREPAID = "PREPAID"


class RevenueCatEnvironmentEnum(BaseStrEnum):
    """``environment`` — decided by the store transaction, not by RevenueCat.

    The webhooks page says RevenueCat "itself does not have sandbox and
    production environments", so the same customer can carry both.
    """

    SANDBOX = "SANDBOX"
    PRODUCTION = "PRODUCTION"


class RevenueCatStoreEnum(BaseStrEnum):
    """``store`` the purchase belongs to."""

    AMAZON = "AMAZON"
    APP_STORE = "APP_STORE"
    MAC_APP_STORE = "MAC_APP_STORE"
    PADDLE = "PADDLE"
    PLAY_STORE = "PLAY_STORE"
    PROMOTIONAL = "PROMOTIONAL"
    RC_BILLING = "RC_BILLING"
    ROKU = "ROKU"
    STRIPE = "STRIPE"
    TEST_STORE = "TEST_STORE"


class RevenueCatReasonEnum(BaseStrEnum):
    """Reason of a ``CANCELLATION`` or of an ``EXPIRATION``.

    One vocabulary for both fields, as the documentation's "Cancellation and
    Expiration Reasons" table has it. ``SUBSCRIPTION_PAUSED`` appears only on
    ``EXPIRATION``.
    """

    UNSUBSCRIBE = "UNSUBSCRIBE"
    BILLING_ERROR = "BILLING_ERROR"
    DEVELOPER_INITIATED = "DEVELOPER_INITIATED"
    PRICE_INCREASE = "PRICE_INCREASE"
    CUSTOMER_SUPPORT = "CUSTOMER_SUPPORT"
    UNKNOWN = "UNKNOWN"
    SUBSCRIPTION_PAUSED = "SUBSCRIPTION_PAUSED"


__all__: list[str] = [
    "RevenueCatEnvironmentEnum",
    "RevenueCatEventTypeEnum",
    "RevenueCatPeriodTypeEnum",
    "RevenueCatReasonEnum",
    "RevenueCatStoreEnum",
]
