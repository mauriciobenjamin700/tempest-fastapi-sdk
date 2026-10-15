"""``normalize`` covers every documented event type, one test per type.

The expected status for each type is the rule the module docstring cites
from RevenueCat's "Event Types and Fields" page. Documented samples are used
where the page has one; the rest are built from the fields the page lists.
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

import pytest

from tempest_fastapi_sdk.integrations.subscriptions.revenuecat import (
    RevenueCatEvent,
    RevenueCatEventTypeEnum,
    RevenueCatWebhook,
    SubscriptionStatusEnum,
    normalize,
)

S = SubscriptionStatusEnum
E = RevenueCatEventTypeEnum

EXPECTED: dict[RevenueCatEventTypeEnum, SubscriptionStatusEnum] = {
    E.TEST: S.NO_CHANGE,
    E.INITIAL_PURCHASE: S.ACTIVE,
    E.RENEWAL: S.ACTIVE,
    E.CANCELLATION: S.CANCELLED,
    E.UNCANCELLATION: S.ACTIVE,
    E.NON_RENEWING_PURCHASE: S.ACTIVE,
    E.SUBSCRIPTION_PAUSED: S.PAUSE_SCHEDULED,
    E.EXPIRATION: S.EXPIRED,
    E.BILLING_ISSUE: S.BILLING_ISSUE,
    E.PRODUCT_CHANGE: S.ACTIVE,
    E.SUBSCRIPTION_EXTENDED: S.ACTIVE,
    E.REFUND_REVERSED: S.ACTIVE,
    E.INVOICE_ISSUANCE: S.NO_CHANGE,
    E.TRANSFER: S.NO_CHANGE,
    E.TEMPORARY_ENTITLEMENT_GRANT: S.TEMPORARY_GRANT,
    E.VIRTUAL_CURRENCY_TRANSACTION: S.NO_CHANGE,
    E.EXPERIMENT_ENROLLMENT: S.NO_CHANGE,
    E.PURCHASE_REDEEMED: S.NO_CHANGE,
    E.SUBSCRIBER_ALIAS: S.NO_CHANGE,
    E.PRICE_INCREASE_CONSENT_REQUIRED: S.NO_CHANGE,
    E.PRICE_INCREASE_CONSENT_APPROVED: S.NO_CHANGE,
}
"""The status each type maps to, with a ``NORMAL`` period and no reason."""


def test_every_documented_type_has_an_expectation() -> None:
    assert set(EXPECTED) == set(RevenueCatEventTypeEnum)


@pytest.mark.parametrize("event_type", list(EXPECTED), ids=str)
def test_each_type_maps_to_its_status(event_type: RevenueCatEventTypeEnum) -> None:
    event = RevenueCatEvent(
        id=f"evt-{event_type}",
        type=event_type.value,
        period_type="NORMAL",
        app_user_id="user-1",
    )

    snapshot = normalize(event)

    assert snapshot.status is EXPECTED[event_type]
    assert snapshot.event_id == f"evt-{event_type}"
    assert snapshot.event_type == event_type.value
    assert snapshot.app_user_id == "user-1"


def test_every_documented_sample_normalizes(samples: list[dict[str, Any]]) -> None:
    statuses = [
        normalize(RevenueCatWebhook.model_validate(body).event).status
        for body in samples
    ]

    assert S.UNKNOWN not in statuses


def test_the_sample_initial_purchase_carries_its_period(
    samples: list[dict[str, Any]],
) -> None:
    body = next(b for b in samples if b["event"]["type"] == "INITIAL_PURCHASE")

    snapshot = normalize(RevenueCatWebhook.model_validate(body).event)

    assert snapshot.status is S.ACTIVE
    assert snapshot.product_id == "com.subscription.weekly"
    assert snapshot.entitlement_ids == ("pro",)
    assert snapshot.period_end == datetime.fromtimestamp(1659331174, tz=UTC)
    assert snapshot.environment == "PRODUCTION"


def test_a_trial_period_is_trial() -> None:
    event = RevenueCatEvent(id="e", type="INITIAL_PURCHASE", period_type="TRIAL")

    assert normalize(event).status is S.TRIAL


@pytest.mark.parametrize(
    ("reason", "status"),
    [
        ("CUSTOMER_SUPPORT", S.REFUNDED),
        ("BILLING_ERROR", S.BILLING_ISSUE),
        ("UNSUBSCRIBE", S.CANCELLED),
        ("DEVELOPER_INITIATED", S.CANCELLED),
    ],
)
def test_cancellation_reasons(reason: str, status: SubscriptionStatusEnum) -> None:
    event = RevenueCatEvent(id="e", type="CANCELLATION", cancel_reason=reason)

    snapshot = normalize(event)

    assert snapshot.status is status
    assert snapshot.reason == reason


def test_an_expiration_by_pause_is_paused() -> None:
    event = RevenueCatEvent(
        id="e", type="EXPIRATION", expiration_reason="SUBSCRIPTION_PAUSED"
    )

    assert normalize(event).status is S.PAUSED


def test_a_billing_issue_carries_the_grace_period() -> None:
    event = RevenueCatEvent(
        id="e", type="BILLING_ISSUE", grace_period_expiration_at_ms=1_700_000_000_000
    )

    snapshot = normalize(event)

    assert snapshot.grace_period_end == datetime.fromtimestamp(1_700_000_000, tz=UTC)


def test_an_unknown_type_does_not_raise() -> None:
    event = RevenueCatEvent(id="e", type="SOMETHING_NEW_IN_2027", app_user_id="u")

    snapshot = normalize(event)

    assert snapshot.status is S.UNKNOWN
    assert snapshot.event_type == "SOMETHING_NEW_IN_2027"
    assert snapshot.app_user_id == "u"
